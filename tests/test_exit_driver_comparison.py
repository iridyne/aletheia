"""Tests for exit-driver realized-outcome comparison."""

from aletheia.analytics.exit_driver_comparison import (
    compare_exit_outcomes,
    exit_driver,
    format_comparison,
    shadow_report,
)


def test_exit_driver_classification():
    assert exit_driver("operator_close") == "operator"
    assert exit_driver("operator_limit_exit") == "operator"
    assert exit_driver("stop_loss") == "rule"
    assert exit_driver("take_profit") == "rule"
    assert exit_driver("thesis_invalidated") == "rule"
    assert exit_driver("catastrophic_stop") == "safety"
    assert exit_driver("liquidation_proximity") == "safety"
    assert exit_driver("reconciled_gone") == "other"
    assert exit_driver(None) == "other"


def _row(reason, pnl, opened=None, closed=None):
    r = {"close_reason": reason, "realized_pnl": pnl}
    if opened:
        r["opened_at"] = opened
    if closed:
        r["closed_at"] = closed
    return r


def test_compare_groups_and_stats():
    rows = [
        _row("operator_close", 10.0),
        _row("operator_limit_exit", 5.0),
        _row("stop_loss", -8.0),
        _row("take_profit", 12.0),
        _row("catastrophic_stop", -30.0),
    ]
    stats = compare_exit_outcomes(rows)

    op = stats["operator"]
    assert op.count == 2 and op.wins == 2 and op.total_pnl == 15.0
    assert op.avg_pnl == 7.5 and op.win_rate == 1.0

    rule = stats["rule"]
    assert rule.count == 2 and rule.wins == 1 and rule.total_pnl == 4.0
    assert rule.win_rate == 0.5

    safety = stats["safety"]
    assert safety.count == 1 and safety.wins == 0 and safety.total_pnl == -30.0


def test_avg_hold_hours():
    rows = [
        _row("operator_close", 1.0, "2026-07-10T00:00:00+00:00", "2026-07-10T02:00:00+00:00"),
        _row("operator_close", 1.0, "2026-07-10T00:00:00+00:00", "2026-07-10T04:00:00+00:00"),
    ]
    op = compare_exit_outcomes(rows)["operator"]
    assert op.avg_hold_hours == 3.0  # (2 + 4) / 2


def test_empty():
    assert compare_exit_outcomes([]) == {}
    assert "no eligible outcomes" in format_comparison({})


def test_unavailable_or_non_finite_economics_are_not_zero_return_samples():
    stats = compare_exit_outcomes([
        _row("stop_loss", None),
        _row("take_profit", float("nan")),
        _row("take_profit", 5.0),
    ])

    assert stats["rule"].count == 1
    assert stats["rule"].total_pnl == 5.0
    assert stats["rule"].win_rate == 1.0


def test_format_smoke():
    stats = compare_exit_outcomes([_row("operator_close", 10.0), _row("stop_loss", -5.0)])
    out = format_comparison(stats)
    assert "operator" in out and "rule" in out and "win%" in out
    assert "eligible" in out and "oi-v1" in out


def test_shadow_report_uses_central_eligible_cohort(monkeypatch):
    import aletheia.analytics.outcome_integrity as outcome_integrity

    seen = {}
    raw_rows = [
        {"close_reason": "operator_close", "realized_pnl": 100.0, "outcome_eligible": False},
        {"close_reason": "stop_loss", "realized_pnl": -1.0, "outcome_eligible": True},
    ]

    def eligible_rows(**filters):
        seen.update(filters)
        return [row for row in raw_rows if row["outcome_eligible"]]

    monkeypatch.setattr(outcome_integrity, "eligible_strategy_outcomes", eligible_rows)

    stats = shadow_report(days=30, execution_mode="paper_internal", limit=50)

    assert "operator" not in stats
    assert stats["rule"].count == 1
    assert stats["rule"].total_pnl == -1.0
    assert seen["execution_mode"] == "paper_internal"
    assert seen["limit"] == 50
    assert seen["closed_after"] is not None
