"""Operator reporting keeps raw account events distinct from verified outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from typer.testing import CliRunner


def _insert_mixed_cohort() -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    common = {
        "symbol": "BTC/USDT", "market_type": "spot", "direction": "long",
        "quantity": 1.0, "entry_price": 100.0, "execution_mode": "paper_internal",
        "status": "closed", "closed_at": datetime.now(timezone.utc).isoformat(),
    }
    repo.insert_position(Position(
        **common, id="verified", realized_pnl=5.0, close_reason="verified_exit",
        close_category="strategy", exit_price_source="local_fill",
        exit_fill_verified=True, outcome_eligible=True,
        outcome_exclusion_reason="eligible", outcome_semantics_version="oi-v1",
    ))
    repo.insert_position(Position(
        **common, id="administrative", realized_pnl=0.0,
        close_reason="reconciled_gone", close_category="administrative",
        exit_price_source="unavailable", exit_fill_verified=False,
        outcome_eligible=False, outcome_exclusion_reason="administrative_close",
        outcome_semantics_version="oi-v1",
    ))
    repo.update_position("administrative", realized_pnl=None)
    repo.insert_position(Position(
        **common, id="legacy", realized_pnl=-999.0, close_reason="manual",
    ))


def test_cohort_summary_counts_raw_verified_and_exclusion_reasons():
    from aletheia.analytics.outcome_integrity import build_outcome_cohort_summary

    _insert_mixed_cohort()
    summary = build_outcome_cohort_summary(execution_mode="paper_internal")
    assert summary == {
        "raw_event_count": 3,
        "eligible_outcome_count": 1,
        "excluded_outcome_count": 2,
        "outcome_exclusion_reasons": {
            "administrative_close": 1,
            "legacy_unverified_close": 1,
        },
        "outcome_semantics_version": "oi-v1",
        "cohort_state": "available",
        "raw_recorded_realized_pnl": -994.0,
        "raw_economics_unavailable_count": 1,
        "eligible_realized_pnl": 5.0,
        "clean_window_anchor_id": "outcome-integrity-v1-2026-07-12",
        "clean_window_anchor_iso": "2026-07-12T07:10:00+00:00",
    }


def test_trade_stats_and_equity_curve_expose_matching_dual_cohorts(monkeypatch):
    from aletheia.server.routes.analytics import (
        get_confidence_calibration,
        get_cost_summary,
        get_equity_curve,
        get_strategy_kpis,
        get_strategy_performance,
        get_trade_insights,
        get_trade_stats,
    )
    from aletheia.server.routes.data import get_family_scorecard

    _insert_mixed_cohort()
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: type(
        "Config", (), {"app": type("App", (), {"equity_curve_mode": "full_cumulative"})()}
    )())

    stats = get_trade_stats()
    curve = get_equity_curve(execution_mode="paper_internal")
    payloads = (
        stats,
        curve,
        get_cost_summary(),
        get_strategy_kpis(),
        get_strategy_performance(),
        get_trade_insights(),
        get_confidence_calibration(),
        get_family_scorecard(),
    )
    for payload in payloads:
        assert payload["raw_event_count"] == 3
        assert payload["eligible_outcome_count"] == 1
        assert payload["excluded_outcome_count"] == 2
        assert payload["outcome_semantics_version"] == "oi-v1"
    assert stats["totals"]["trade_count"] == 1
    assert stats["totals"]["total_pnl"] == 5.0
    assert curve["default_curve"] == "genuine_verified"
    assert len(curve["genuine_points"]) == 1
    assert len(curve["raw_recorded_points"]) == 2
    assert curve["raw_economics_unavailable_count"] == 1
    assert curve["total_realized"] == 5.0


def test_inspect_pnl_json_uses_same_counts_and_handles_empty_metrics():
    from aletheia.main import app
    from aletheia.server.routes.analytics import get_cost_summary

    runner = CliRunner()
    empty_costs = get_cost_summary()
    assert empty_costs["cohort_state"] == "insufficient"
    assert empty_costs["total_costs"] is None
    empty = runner.invoke(app, ["inspect", "pnl"])
    assert empty.exit_code == 0
    assert "insufficient verified outcomes" in empty.output.lower()

    _insert_mixed_cohort()
    result = runner.invoke(app, ["inspect", "pnl", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["cohort"]["raw_event_count"] == 3
    assert payload["cohort"]["eligible_outcome_count"] == 1
    assert payload["cohort"]["excluded_outcome_count"] == 2
    assert payload["rolling"]["total_pnl"] == 5.0


def test_frontend_keeps_dual_curve_and_cohort_labels_visible():
    root = Path(__file__).resolve().parents[1]
    analytics = (root / "frontend/src/charts/TradeAnalyticsPanel.tsx").read_text()
    curve = (root / "frontend/src/charts/PnLChart.tsx").read_text()
    for token in ("Raw close events", "Verified strategy outcomes", "Excluded"):
        assert token in analytics
    for token in ("Raw recorded account events", "Verified strategy curve"):
        assert token in curve
    assert "display: 'none'" not in analytics
    assert "display: 'none'" not in curve
