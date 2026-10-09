"""Spec: Analytics contracts (written 2026-06-28).

AN-S01 (shadow-trade resolution health) was retired with the shadow_trades write
and resolve path (#3704 / #3708); the table has no writer or resolver left.
"""

from __future__ import annotations

import pathlib
import sqlite3

from tests.specs.spec_meta import SpecClaim, spec_claim


def _project_root() -> str:
    return str(pathlib.Path(__file__).resolve().parents[2])


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
              "contribution is eligible for an actionable bottleneck; treatment-specific "
              "Universe Twin and Exit paired evidence remain non-authoritative outside "
              "those five verdicts; dynamic-trailing qualification preserves an exact-build "
              "control holdout, forbids cross-cohort pooling, and cannot self-promote; "
              "and the retired local-simulated paired Risk/Execution evidence blocks "
              "stay absent from the report.",
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
    twin = report["universe_twin_evidence"]
    assert twin["contract_version"] == "universe-twin-evidence/v1"
    assert twin["authority"] == "shadow_only"
    assert twin["behavior_authority"] is False
    assert twin["promotion_eligible"] is False
    assert twin["contribution_verdict"] == "insufficient_data"
    paired = report["exit_paired_evidence"]
    assert paired["contract_version"] == "exit-paired-evidence/v2"
    assert paired["outcome_coverage"] is None
    assert paired["incremental_net_pnl_mean_ci_95"] is None
    assert paired["unsettled_count"] == 0
    assert paired["behavior_authority"] is False
    assert paired["promotion_eligible"] is False
    assert paired["contribution_verdict"] == "insufficient_data"
    trailing = report["exit_trailing_qualification"]
    assert trailing["contract_version"] == "exit-trailing-qualification/v2"
    assert trailing["recovery_contract"]["authority"] == "diagnostic_only"
    assert trailing["cross_cohort_pooling"] is False
    assert trailing["promotion_eligible"] is False
    assert "risk_policy_evidence" not in report, (
        "the paired Risk-policy-value evidence was retired with the local simulated "
        "account (owner-approved D2 #3022); a reappearing key would be a second "
        "current authority for a retired capability"
    )
    assert "execution_cost_evidence" not in report, (
        "the paired Execution-cost evidence was retired with the local simulated "
        "account (owner-approved D2 #3022)"
    )
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
        "suspected_modules": ["core/aletheia/universe/allocator.py"],
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
def test_scorecard_excludes_synthetic_closes(monkeypatch):
    """Behavioral proof on the autouse in-memory DB.

    Insert 3 genuine closes (WR 66.7%, PF 4.0) + 10 synthetic reconciled_gone
    LOSSES. If the scorecard counted the synthetic rows, WR would collapse to 15%
    and PF to ~0 and the trade count would read 13. Post-fix it must read the
    genuine cohort only.
    """
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.analytics import go_live_scorecard as sc
    from aletheia.analytics import execution_mode
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    monkeypatch.setattr(execution_mode, "resolve_selected_execution_mode", lambda: "bitget_demo")

    def _closed(symbol, direction, pnl, reason):
        position_id = f"scorecard-{symbol.replace('/', '-')}-{reason}"
        if reason != "reconciled_gone":
            _insert_closed_position_via_producer(
                position_id=position_id,
                symbol=symbol,
                market_type="usdt_futures",
                direction=direction,
                quantity=1.0,
                entry_price=100.0,
                realized_pnl=pnl,
                execution_mode="bitget_demo",
                close_reason=reason,
                closed_at="2026-09-23T17:00:00+00:00",
            )
            return
        repo.insert_position(Position(
            id=position_id,
            symbol=symbol, market_type="usdt_futures", direction=direction,
            quantity=1.0, entry_price=100.0, execution_mode="bitget_demo",
            status="closed", realized_pnl=pnl, close_reason=reason,
            closed_at="2026-09-23T17:00:00+00:00",
            current_price=100.0 - pnl,
            close_category="administrative",
            outcome_eligible=False,
            outcome_exclusion_reason="administrative_close",
            outcome_semantics_version="oi-v2",
        ))

    # Genuine cohort: 2 wins (+10 each), 1 loss (-5) → WR 66.7%, PF 20/5 = 4.0
    _closed("BTC/USDT", "long", 10.0, "take_profit")
    _closed("ETH/USDT", "long", 10.0, "take_profit")
    _closed("SOL/USDT", "long", -5.0, "stop_loss")
    # Synthetic thrash: 10 reconciler sweeps, all large losses — must be excluded
    for i in range(10):
        _closed(f"JUNK{i}/USDT", "short", -1000.0, "reconciled_gone")

    pnls = sc._genuine_closed_pnls(sc._scorecard_cohort())
    vol = sc._check_trade_volume(pnls)
    assert "3 trades" in vol["detail"], (
        f"trade volume must count only genuine closes (3), got: {vol['detail']!r} "
        "— synthetic reconciled_gone sweeps are being counted"
    )

    wr = sc._check_win_rate(pnls)
    assert "66.7%" in wr["detail"], (
        f"win rate must be computed on genuine closes (66.7%), got: {wr['detail']!r}"
    )
    assert wr["score"] >= 100.0, "genuine WR 66.7% ≥ 45% target → full score"

    pf = sc._check_profit_factor(pnls)
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
        root / "core/aletheia/server/routes/capital.py"
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
