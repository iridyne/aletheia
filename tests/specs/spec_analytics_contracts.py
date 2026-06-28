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
