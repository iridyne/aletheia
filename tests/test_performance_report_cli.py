import json

import pytest


def test_days_is_forwarded_and_reported(monkeypatch, capsys):
    import aletheia.analytics.trade_stats as trade_stats
    from scripts.analysis import performance_report as report

    monkeypatch.delenv("REPORT_DAYS", raising=False)
    monkeypatch.setenv("REPORT_EXECUTION_MODE", "bitget_demo")
    seen: dict[str, int] = {}
    monkeypatch.setattr(
        trade_stats,
        "compute_rolling_trade_stats",
        lambda *, days, **_: seen.setdefault("rolling", days) or {},
    )
    monkeypatch.setattr(trade_stats, "build_trade_stats", lambda **_: {})
    monkeypatch.setattr(
        trade_stats,
        "compute_order_stats",
        lambda *, days, **_: seen.setdefault("orders", days) or {},
    )
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **_: [],
    )

    assert report.main(["--days", "30", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert seen == {"rolling": 30, "orders": 30}
    assert payload["window"] == {"days": 30, "scope": "rolling_and_order_stats"}


def test_legacy_hours_fallback_is_converted_and_marked(monkeypatch):
    import aletheia.analytics.trade_stats as trade_stats
    from scripts.analysis import performance_report as report

    monkeypatch.delenv("REPORT_DAYS", raising=False)
    monkeypatch.setenv("SNAPSHOT_WINDOW_HOURS", "36")
    seen: dict[str, int] = {}
    monkeypatch.setattr(
        trade_stats,
        "compute_rolling_trade_stats",
        lambda *, days, **_: seen.setdefault("rolling", days) or {},
    )
    monkeypatch.setattr(trade_stats, "build_trade_stats", lambda **_: {})
    monkeypatch.setattr(
        trade_stats,
        "compute_order_stats",
        lambda *, days, **_: seen.setdefault("orders", days) or {},
    )
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **_: [],
    )

    report._collect(execution_mode="bitget_demo")

    assert seen == {"rolling": 2, "orders": 2}


def test_execution_mode_is_trimmed_before_resolution(monkeypatch):
    from scripts.analysis import performance_report as report

    monkeypatch.delenv("REPORT_EXECUTION_MODE", raising=False)
    monkeypatch.setattr(
        "aletheia.analytics.execution_mode.resolve_selected_execution_mode",
        lambda: None,
    )

    result = report._collect(days=1, execution_mode="   ")

    assert result["execution_mode_status"] == "unavailable"


@pytest.mark.parametrize(
    "argv",
    [["--days", "0"], ["--days", "-1"], ["--days", "not-a-number"], ["--unknown"]],
)
def test_invalid_report_arguments_fail_visibly(argv):
    from scripts.analysis import performance_report as report

    with pytest.raises(SystemExit) as exc_info:
        report.main(argv)
    assert exc_info.value.code == 2
