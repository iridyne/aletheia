"""Operator reporting keeps raw account events distinct from verified outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from typer.testing import CliRunner


def _insert_mixed_cohort() -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from tests.test_outcome_closure import _insert_entry_chain

    common = {
        "symbol": "BTC/USDT", "market_type": "spot", "direction": "long",
        "quantity": 1.0, "entry_price": 100.0, "execution_mode": "paper_internal",
        "status": "closed", "closed_at": datetime.now(timezone.utc).isoformat(),
    }
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id="verified",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )
    repo.insert_position(Position(
        **common, id="verified", realized_pnl=5.0, close_reason="verified_exit",
        current_price=105.0, entry_fee=0.0, exit_fee=0.0,
        funding_cost=0.0, funding_source="not_applicable",
        entry_price_source="local_fill", entry_fill_verified=True,
        close_category="strategy", exit_price_source="local_fill",
        exit_fill_verified=True, outcome_eligible=True,
        outcome_exclusion_reason="eligible", outcome_semantics_version="oi-v1",
        opportunity_id=opportunity_id, proposal_id=proposal_id, order_id=order_id,
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


def test_cost_summary_keeps_signed_funding_and_marks_incomplete_raw_costs(monkeypatch):
    from aletheia.analytics import outcome_integrity
    from aletheia.server.routes.analytics import get_cost_summary

    verified = [
        {
            "entry_fee": 1.0,
            "exit_fee": 1.0,
            "funding_cost": 10.0,
            "slippage_cost": 0.5,
        },
        {
            "entry_fee": 1.0,
            "exit_fee": 1.0,
            "funding_cost": -3.0,
            "slippage_cost": 0.25,
        },
    ]
    raw = [
        *verified,
        {
            "entry_fee": 1.0,
            "exit_fee": 1.0,
            "funding_cost": None,
            "slippage_cost": 0.1,
        },
    ]
    monkeypatch.setattr(outcome_integrity, "eligible_strategy_outcomes", lambda **_: verified)
    monkeypatch.setattr(outcome_integrity, "raw_account_events", lambda **_: raw)
    monkeypatch.setattr(
        outcome_integrity,
        "build_outcome_cohort_summary",
        lambda **_: {"outcome_status": "available"},
    )

    payload = get_cost_summary(execution_mode="paper_internal")

    assert payload["total_funding_cost"] == 7.0
    assert payload["total_costs"] == 11.0
    assert payload["total_economic_costs"] == 11.0
    assert payload["total_execution_drag"] == 4.75
    assert payload["raw_recorded_costs"]["total_funding_cost"] is None
    assert payload["raw_recorded_costs"]["total_costs"] is None
    assert payload["raw_recorded_costs"]["missing_cost_counts"]["funding_cost"] == 1


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
    # #1939: the resolver reads config.exchange.paper_execution_mode too, so
    # the stub must supply it or every endpoint fails closed.
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: type(
        "Config", (), {
            "app": type("App", (), {"equity_curve_mode": "full_cumulative"})(),
            "exchange": type("Exchange", (), {"paper_execution_mode": "paper_internal"})(),
        }
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


def test_cost_summary_preserves_unknown_fee_legs(monkeypatch):
    from aletheia.analytics import outcome_integrity
    from aletheia.server.routes.analytics import get_cost_summary

    complete = {
        "entry_fee": 1.25,
        "exit_fee": 0.75,
        "funding_cost": 0.0,
        "slippage_cost": 0.1,
    }
    unknown_exit = {
        "entry_fee": 0.5,
        "exit_fee": None,
        "funding_cost": 0.0,
        "slippage_cost": 0.2,
    }
    monkeypatch.setattr(
        outcome_integrity, "eligible_strategy_outcomes", lambda **_: [complete]
    )
    monkeypatch.setattr(
        outcome_integrity, "raw_account_events", lambda **_: [complete, unknown_exit]
    )
    monkeypatch.setattr(
        outcome_integrity, "build_outcome_cohort_summary", lambda **_: {}
    )

    costs = get_cost_summary(execution_mode="paper_internal")

    assert costs["total_entry_fees"] == 1.25
    assert costs["total_exit_fees"] == 0.75
    assert costs["total_fees"] == 2.0
    assert costs["total_slippage"] == 0.1
    assert costs["total_costs"] == 2.1
    assert costs["total_execution_drag"] == 2.1
    assert costs["raw_recorded_costs"] == {
        "total_entry_fees": 1.75,
        "total_exit_fees": None,
        "total_funding_cost": 0.0,
        "total_fees": None,
        "total_slippage": 0.3,
        "total_costs": None,
        "total_economic_costs": None,
        "total_execution_drag": None,
        "missing_cost_counts": {
            "entry_fee": 0,
            "exit_fee": 1,
            "funding_cost": 0,
            "slippage_cost": 0,
        },
    }


def test_cost_summary_keeps_explicit_zero_fees_numeric(monkeypatch):
    from aletheia.analytics import outcome_integrity
    from aletheia.server.routes.analytics import get_cost_summary

    zero = {
        "entry_fee": 0.0,
        "exit_fee": 0.0,
        "funding_cost": 0.0,
        "slippage_cost": 0.0,
    }
    monkeypatch.setattr(
        outcome_integrity, "eligible_strategy_outcomes", lambda **_: [zero]
    )
    monkeypatch.setattr(
        outcome_integrity, "raw_account_events", lambda **_: [zero]
    )
    monkeypatch.setattr(
        outcome_integrity, "build_outcome_cohort_summary", lambda **_: {}
    )

    costs = get_cost_summary(execution_mode="paper_internal")

    assert costs["total_fees"] == 0.0
    assert costs["total_costs"] == 0.0
    assert costs["raw_recorded_costs"]["total_fees"] == 0.0


def test_frontend_keeps_dual_curve_and_cohort_labels_visible():
    root = Path(__file__).resolve().parents[1]
    analytics = (root / "frontend/src/charts/TradeAnalyticsPanel.tsx").read_text()
    curve = (root / "frontend/src/charts/PnLChart.tsx").read_text()
    for token in ("Raw close events", "Verified strategy outcomes", "Excluded"):
        assert token in analytics
    # PnLChart shows a single verified baseline (green/red); raw and live-mark
    # series were removed. Check that the verified data source and empty-state
    # copy are still present.
    for token in (
        "genuine_points",
        "addBaselineSeries",
        "No verified strategy outcomes yet",
    ):
        assert token in curve
    assert "display: 'none'" not in analytics
    assert "display: 'none'" not in curve
