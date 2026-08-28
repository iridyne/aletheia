import json

import pytest


def test_days_is_forwarded_and_reported(monkeypatch, capsys):
    import aletheia.analytics.trade_stats as trade_stats
    import performance_report as report

    monkeypatch.delenv("REPORT_DAYS", raising=False)
    monkeypatch.setenv("REPORT_EXECUTION_MODE", "paper_internal")
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
    assert payload["window"] == {"days": 30}


@pytest.mark.parametrize(
    "argv",
    [["--days", "0"], ["--days", "-1"], ["--days", "not-a-number"], ["--unknown"]],
)
def test_invalid_report_arguments_fail_visibly(argv):
    import performance_report as report

    with pytest.raises(SystemExit) as exc_info:
        report.main(argv)
    assert exc_info.value.code == 2
