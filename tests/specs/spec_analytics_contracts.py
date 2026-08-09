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
    from aletheia.storage.sqlite import db_path

    db = db_path()
    if not db.exists():
        pytest.skip("no production app.db on this machine")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shadow_trades'"
        ).fetchone()
        if not exists:
            pytest.skip("production app.db has no shadow_trades table to evaluate")
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
    from aletheia.counterfactual.trades import resolve_stale_shadows

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


# ---------------------------------------------------------------------------
# AN-S04 — alpha attribution audit is read-only and honest about thin data
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("AN-S04", "analytics_contract", "§alpha attribution audit", "L2b",
              "Alpha Attribution Audit must return exactly the five judging-layer "
              "verdicts (Universe, Ranker, System Operator, Learning, Serenity), "
              "include baseline/sample/delta/confidence/causal-grade fields, use "
              "insufficient_data instead of fake conclusions when samples are thin, "
              "and preserve directional evidence as diagnostic while only measured "
              "contribution is eligible for an actionable bottleneck.",
              status="implemented"),
)
def test_alpha_attribution_contract_shape_on_empty_db():
    from aletheia.analytics.alpha_attribution import (
        _main_bottleneck,
        build_alpha_attribution_audit,
    )

    report = build_alpha_attribution_audit(days=30)
    assert set(report["verdicts"]) == {
        "universe", "ranker", "system_operator", "learning", "serenity",
    }
    for item in report["verdicts"].values():
        assert item["verdict"] == "insufficient_data"
        for key in ("sample_size", "baseline", "observed", "delta", "confidence_label",
                    "causal_grade", "evidence_type", "contribution_verdict",
                    "measurement_status", "suspected_modules", "reason"):
            assert key in item
    assert report["main_bottleneck"]["layer"] == "insufficient_data"

    weak_negative = {
        "name": "Ranker Alpha",
        "verdict": "negative",
        "delta": -10.0,
        "contribution_verdict": "insufficient_data",
        "measurement_status": "directional_evidence_only",
        "suspected_modules": ["aletheia/agents/paper_trader_ranker.py"],
        "reason": "directional only",
    }
    measured_negative = {
        "name": "Universe Alpha",
        "verdict": "negative",
        "delta": -1.0,
        "contribution_verdict": "negative",
        "measurement_status": "measured_contribution",
        "suspected_modules": ["aletheia/universe/allocator.py"],
        "reason": "prospective control",
    }
    assert _main_bottleneck({"ranker": weak_negative})["layer"] == "insufficient_data"
    assert _main_bottleneck({
        "ranker": weak_negative,
        "universe": measured_negative,
    })["layer"] == "Universe Alpha"


# ---------------------------------------------------------------------------
# AN-S03 — go-live Scorecard excludes synthetic/administrative closes
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("AN-S03", "analytics_contract", "§scorecard excludes synthetic closes", "L2b",
              "go_live_scorecard trade metrics (volume / profit_factor / win_rate / "
              "max_drawdown) must EXCLUDE synthetic closes (reconciled_gone, "
              "demo_validation_cleanup, units_bug_cleanup) — reconciler orphan sweeps "
              "are not real trade outcomes and were polluting the go-live readiness "
              "signal (Win-line A). See ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md.",
              status="implemented"),
)
def test_scorecard_excludes_synthetic_closes():
    """Behavioral proof on the autouse in-memory DB.

    Insert 3 genuine closes (WR 66.7%, PF 4.0) + 10 synthetic reconciled_gone
    LOSSES. If the scorecard counted the synthetic rows, WR would collapse to 15%
    and PF to ~0 and the trade count would read 13. Post-fix it must read the
    genuine cohort only.
    """
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.analytics import go_live_scorecard as sc
    from tests.test_outcome_closure import _insert_entry_chain

    def _closed(symbol, direction, pnl, reason):
        synthetic = reason == "reconciled_gone"
        position_id = f"scorecard-{symbol.replace('/', '-')}-{reason}"
        causal_ids = (None, None, None)
        if not synthetic:
            causal_ids = _insert_entry_chain(
                position_id=position_id,
                symbol=symbol,
                market_type="usdt_futures",
                direction=direction,
                quantity=1.0,
                price=100.0,
                execution_mode="okx_demo",
            )
        opportunity_id, proposal_id, order_id = causal_ids
        repo.insert_position(Position(
            id=position_id,
            symbol=symbol, market_type="usdt_futures", direction=direction,
            quantity=1.0, entry_price=100.0, execution_mode="okx_demo",
            status="closed", realized_pnl=pnl, close_reason=reason,
            closed_at="2026-07-12T08:00:00+00:00",
            current_price=(100.0 + pnl if direction == "long" else 100.0 - pnl),
            entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="broker_ledger",
            entry_price_source="unavailable" if synthetic else "broker_fill",
            entry_fill_verified=not synthetic,
            close_category="administrative" if synthetic else "strategy",
            exit_price_source="unavailable" if synthetic else "broker_fill",
            exit_fill_verified=not synthetic,
            outcome_eligible=not synthetic,
            outcome_exclusion_reason="administrative_close" if synthetic else "eligible",
            outcome_semantics_version="oi-v1",
            opportunity_id=opportunity_id,
            proposal_id=proposal_id,
            order_id=order_id,
        ))

    # Genuine cohort: 2 wins (+10 each), 1 loss (-5) → WR 66.7%, PF 20/5 = 4.0
    _closed("BTC/USDT", "long", 10.0, "take_profit")
    _closed("ETH/USDT", "long", 10.0, "take_profit")
    _closed("SOL/USDT", "long", -5.0, "stop_loss")
    # Synthetic thrash: 10 reconciler sweeps, all large losses — must be excluded
    for i in range(10):
        _closed(f"JUNK{i}/USDT", "short", -1000.0, "reconciled_gone")

    vol = sc._check_trade_volume()
    assert "3 trades" in vol["detail"], (
        f"trade volume must count only genuine closes (3), got: {vol['detail']!r} "
        "— synthetic reconciled_gone sweeps are being counted"
    )

    wr = sc._check_win_rate()
    assert "66.7%" in wr["detail"], (
        f"win rate must be computed on genuine closes (66.7%), got: {wr['detail']!r}"
    )
    assert wr["score"] >= 100.0, "genuine WR 66.7% ≥ 45% target → full score"

    pf = sc._check_profit_factor()
    assert "PF 4.00" in pf["detail"] or "PF 4.0" in pf["detail"], (
        f"profit factor must exclude synthetic losses (expected PF 4.0), got: {pf['detail']!r}"
    )


# ---------------------------------------------------------------------------
# AN-S05 — dreaming summary lifecycle separation
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("AN-S05", "analytics_contract", "§dreaming summary lifecycle", "L2b",
              "GET /dreaming/summary must separate active and archived/deprecated knowledge: "
              "recent_* collections contain only active items; deprecated_memories contains only "
              "archived or deprecated items. An item recently archived must not appear in any "
              "recent_* collection.",
              status="implemented"),
)
def test_dreaming_summary_excludes_archived_from_recent(tmp_path, monkeypatch):
    """Archived/deprecated items created in the last 24h must not appear in
    recent_* collections.  Active items must not appear in deprecated_memories.
    """
    import sqlite3
    from datetime import datetime, timezone

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO knowledge_items (id, item_type, status, created_at, updated_at, title, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("active-research", "research_note", "active", now, now, "Active note", ""),
    )
    conn.execute(
        "INSERT INTO knowledge_items (id, item_type, status, created_at, updated_at, title, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("archived-research", "research_note", "archived", now, now, "Archived note", ""),
    )
    conn.execute(
        "INSERT INTO knowledge_items (id, item_type, status, created_at, updated_at, title, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("depr-strategy", "strategy_memory", "deprecated", now, now, "Deprecated hypothesis", ""),
    )
    conn.execute(
        "INSERT INTO knowledge_items (id, item_type, status, created_at, updated_at, title, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("active-strategy", "strategy_memory", "active", now, now, "Active hypothesis", ""),
    )
    conn.commit()

    from aletheia.server.routes.data import get_dreaming_summary

    result = get_dreaming_summary()

    recent_research_ids = {row["id"] for row in result["recent_research_notes"]}
    recent_hypothesis_ids = {row["id"] for row in result["recent_strategy_hypotheses"]}
    deprecated_ids = {row["id"] for row in result["deprecated_memories"]}

    # Active items appear in their respective recent collections
    assert "active-research" in recent_research_ids
    assert "active-strategy" in recent_hypothesis_ids

    # Archived/deprecated items do NOT appear in recent collections
    assert "archived-research" not in recent_research_ids, (
        "archived item appeared in recent_research_notes — active filter is missing"
    )
    assert "depr-strategy" not in recent_hypothesis_ids, (
        "deprecated item appeared in recent_strategy_hypotheses — active filter is missing"
    )

    # Archived/deprecated items appear in deprecated_memories
    assert "archived-research" in deprecated_ids
    assert "depr-strategy" in deprecated_ids

    # Active items do NOT appear in deprecated_memories
    assert "active-research" not in deprecated_ids
    assert "active-strategy" not in deprecated_ids


# ---------------------------------------------------------------------------
# AN-S06 / AN-C06 — capital-event analytics honesty
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "AN-S06",
        "analytics_contract",
        "§capital-event curve semantics",
        "L2a",
        "Capital Events is presented as audited cash-flow history; raw Account "
        "Equity, cash-flow-adjusted equity, and verified trading PnL remain "
        "explicitly separate.",
        status="implemented",
    )
)
def test_capital_events_ui_distinguishes_raw_adjusted_and_trading_series():
    root = pathlib.Path(_project_root())
    settings = (
        root / "frontend/src/features/settings/tabs/ExecutionTab.tsx"
    ).read_text(encoding="utf-8")
    capital_route = (
        root / "aletheia/server/routes/capital.py"
    ).read_text(encoding="utf-8")

    assert "renders a separate cash-flow-adjusted series" in settings
    assert "Cumulative PnL remains verified trading PnL" in settings
    assert "Account-equity history consumes this event" in capital_route


@spec_claim(
    SpecClaim(
        "AN-C06",
        "analytics_contract",
        "§capital-adjusted performance consumer",
        "L2c",
        "A public cash-flow-adjusted performance projection consumes scoped Capital "
        "Events while leaving raw Account Equity and verified trading PnL distinct.",
        status="implemented",
    )
)
def test_cash_flow_adjusted_performance_has_capital_event_consumer():
    import inspect

    from aletheia.server.routes.analytics import get_equity_curve
    from aletheia.server.routes.data import get_account_equity_history

    consumer_source = "\n".join(
        (
            inspect.getsource(get_equity_curve),
            inspect.getsource(get_account_equity_history),
        )
    )
    expected_consumer_markers = ("list_capital_events", "adjusted_points")
    assert any(marker in consumer_source for marker in expected_consumer_markers)
    chart_source = (
        pathlib.Path(_project_root())
        / "frontend/src/charts/AccountEquityChart.tsx"
    ).read_text(encoding="utf-8")
    assert "capital_flow_markers" in chart_source
    assert "setMarkers" in chart_source
