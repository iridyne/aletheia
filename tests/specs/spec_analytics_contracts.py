"""Spec: Analytics contracts (written 2026-06-28).

AN-S01 — Shadow-trade resolution health. Shadow trades are counterfactual
"what-if" rows persisted per opportunity batch; their PnL is resolved by
comparing entry_price to a later price. The `content.shadow_trades` doctor probe
found 7560/8039 unresolved >24h because their parent positions (opened before the
b9ad957 opportunity_id fix) carry NULL opportunity_id, so the event-driven
resolver returned 0 every time.

This is a DATA-level (L2b) tripwire on the production DB: after the historical
backfill + the periodic catch-up resolver, the share of long-stale unresolved
shadows must stay bounded. It skips on machines without a populated app.db
(CI / fresh clone / worktree). It does not assert zero — symbols that left the
universe legitimately have no OHLCV to resolve against.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _project_root() -> str:
    return str(pathlib.Path(__file__).resolve().parents[2])


# ---------------------------------------------------------------------------
# AN-S01 — unresolved shadow trades must not pile up unbounded
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("AN-S01", "analytics_contract", "§shadow resolution health", "L2b",
              "Shadow trades older than 7 days must be predominantly resolved "
              "(resolved_at set). A large unresolved backlog means counterfactual "
              "PnL is never computed and the learning loop is blind to it.",
              status="implemented"),
)
def test_shadow_trades_resolution_bounded():
    """GREEN tripwire on the production DB (mirrors LP-S01/S07 data reads).

    After the one-time backfill (scripts/backfill_shadow_trades.py) and the
    periodic shadow_resolver job, shadows older than 7 days should be mostly
    resolved. We allow a generous unresolved share because delisted / out-of-
    universe symbols genuinely have no OHLCV bar to resolve against — the point
    is to catch a *systemic* resolver failure (the original 94% backlog), not to
    demand perfection. Skips on machines without a populated app.db.
    """
    db = pathlib.Path(_project_root()) / "data" / "app.db"
    if not db.exists():
        pytest.skip("no production app.db on this machine")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        row = conn.execute(
            "SELECT "
            "  SUM(CASE WHEN created_at < datetime('now','-7 days') THEN 1 ELSE 0 END) AS old_total, "
            "  SUM(CASE WHEN created_at < datetime('now','-7 days') "
            "           AND resolved_at IS NULL THEN 1 ELSE 0 END) AS old_unresolved "
            "FROM shadow_trades"
        ).fetchone()
    finally:
        conn.close()

    old_total = row[0] or 0
    old_unresolved = row[1] or 0
    if old_total == 0:
        pytest.skip("no shadow trades older than 7 days to evaluate")

    unresolved_pct = 100.0 * old_unresolved / old_total
    assert unresolved_pct < 50.0, (
        f"{unresolved_pct:.1f}% of {old_total} shadow trades older than 7 days are "
        f"still unresolved ({old_unresolved}/{old_total}) — the counterfactual PnL "
        "resolver has systemically failed. Run scripts/backfill_shadow_trades.py and "
        "check the shadow_resolver scheduler job. See FIX 3 (AN-S01)."
    )


# ---------------------------------------------------------------------------
# AN-S02 — resolve_stale_shadows resolves by historical horizon (behavioral)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("AN-S02", "analytics_contract", "§historical shadow resolution", "L2b",
              "resolve_stale_shadows resolves an unresolved shadow using the OHLCV "
              "close at entry_at + horizon (NOT current price), computes the correct "
              "directional pnl, tolerates naive UTC timestamps, and skips symbols "
              "with no OHLCV. Fixed 2026-06-28 (content-probe fixes).",
              status="implemented"),
)
def test_resolve_stale_shadows_uses_historical_bar():
    """Behavioral proof on the autouse in-memory DB (no network, no mocks)."""
    from datetime import datetime, timezone

    from aletheia.storage.sqlite import get_db
    from aletheia.storage.models import new_id
    from aletheia.analytics.shadow_trades import resolve_stale_shadows

    db = get_db()
    entry_dt = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    target_ms = int((entry_dt.timestamp() + 24 * 3600) * 1000)

    # OHLCV close at entry+24h: 110 vs entry 100 → long +10%, short -10%.
    db.execute(
        "INSERT INTO market_ohlcv (symbol, timeframe, ts, open, high, low, close, volume, provider) "
        "VALUES ('BTC/USDT','1h',?,109,111,108,110,1,'test')",
        (target_ms,),
    )

    def _mk_shadow(direction: str, created_at: str, symbol: str = "BTC/USDT", entry: float = 100.0) -> str:
        sid = new_id()
        db.execute(
            "INSERT INTO shadow_trades (id, opportunity_id, symbol, direction, entry_price, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (sid, "", symbol, direction, entry, created_at),
        )
        return sid

    long_id = _mk_shadow("long", "2026-06-01T00:00:00+00:00")
    short_id = _mk_shadow("short", "2026-06-01T00:00:00+00:00")
    naive_id = _mk_shadow("long", "2026-06-01 00:00:00")           # SQLite datetime('now') form
    orphan_id = _mk_shadow("long", "2026-06-01T00:00:00+00:00", symbol="DELISTED/USDT")
    db.commit()

    counts = resolve_stale_shadows(min_age_hours=1, horizon_hours=24)

    assert counts["resolved"] == 3, f"expected 3 resolved, got {counts}"
    assert counts["skipped_no_ohlcv"] == 1, f"expected 1 no-ohlcv skip, got {counts}"

    def _row(sid):
        return db.execute(
            "SELECT exit_price, pnl_pct, resolved_at FROM shadow_trades WHERE id=?", (sid,)
        ).fetchone()

    rl = _row(long_id)
    assert rl["exit_price"] == 110 and rl["pnl_pct"] == 10.0 and rl["resolved_at"]
    rs = _row(short_id)
    assert rs["pnl_pct"] == -10.0, f"short pnl wrong: {rs['pnl_pct']}"
    rn = _row(naive_id)
    assert rn["pnl_pct"] == 10.0 and rn["resolved_at"], "naive UTC timestamp not handled"
    ro = _row(orphan_id)
    assert ro["resolved_at"] is None, "symbol with no OHLCV must stay unresolved"
