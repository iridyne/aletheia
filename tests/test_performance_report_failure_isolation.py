import pytest


def _outcomes():
    return [
        {
            "symbol": "BTC",
            "strategy_id": "s1",
            "close_reason": "take_profit",
            "realized_pnl": 4.0,
            "closed_at": "2026-08-26T00:00:00+00:00",
        },
        {
            "symbol": "ETH",
            "strategy_id": "s2",
            "close_reason": "stop_loss",
            "realized_pnl": -1.0,
            "closed_at": "2026-08-26T01:00:00+00:00",
        },
    ]


def _patch_common(monkeypatch, outcomes=None):
    monkeypatch.setenv("REPORT_EXECUTION_MODE", "bitget_demo")
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **_: list(_outcomes() if outcomes is None else outcomes),
    )


@pytest.mark.parametrize(
    ("failing", "error_key", "healthy"),
    [
        ("rolling", "rolling_error", {"trade_stats", "order_stats"}),
        ("trade_stats", "trade_stats_error", {"rolling", "order_stats"}),
        ("order_stats", "order_stats_error", {"rolling", "trade_stats"}),
    ],
)
def test_report_isolates_trade_section_failures(monkeypatch, failing, error_key, healthy):
    import aletheia.analytics.trade_stats as trade_stats
    import performance_report as pr

    _patch_common(monkeypatch)
    calls = []

    def maybe(name, value):
        def producer(**_kwargs):
            calls.append(name)
            if name == failing:
                raise RuntimeError(f"{name} broke")
            return value

        return producer

    monkeypatch.setattr(
        trade_stats,
        "compute_rolling_trade_stats",
        maybe("rolling", {"total_trades": 2, "win_rate": 50.0, "total_pnl": 3.0}),
    )
    monkeypatch.setattr(
        trade_stats,
        "build_trade_stats",
        maybe("trade_stats", {"totals": {"trade_count": 2}}),
    )
    monkeypatch.setattr(
        trade_stats,
        "compute_order_stats",
        maybe("order_stats", {"fill_rate_pct": 100.0, "filled": 2, "errors": 0}),
    )

    rep = pr._collect()

    assert calls == ["rolling", "trade_stats", "order_stats"]
    assert error_key in rep
    assert rep["completeness"] == "partial"
    assert rep["complete"] is False
    assert failing not in rep
    assert failing not in rep["sections"]
    assert healthy.issubset(rep["sections"])
    assert all(name in rep for name in healthy)


@pytest.mark.parametrize(
    ("target", "error_key", "healthy"),
    [
        ("summarize", "equity_error", {"attribution", "streaks", "temporal"}),
        ("attribute", "attribution_error", {"equity", "streaks", "temporal"}),
        ("streaks", "streaks_error", {"equity", "attribution", "temporal"}),
        ("temporal", "temporal_error", {"equity", "attribution", "streaks"}),
    ],
)
def test_report_isolates_outcome_transform_failures(monkeypatch, target, error_key, healthy):
    import aletheia.analytics.attribution as attribution
    import aletheia.analytics.performance_summary as summary
    import aletheia.analytics.streaks as streaks
    import aletheia.analytics.temporal as temporal
    import aletheia.analytics.trade_stats as trade_stats
    import performance_report as pr

    outcome_reads = 0

    def eligible(**_kwargs):
        nonlocal outcome_reads
        outcome_reads += 1
        return list(_outcomes())

    monkeypatch.setenv("REPORT_EXECUTION_MODE", "bitget_demo")
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        eligible,
    )
    monkeypatch.setattr(trade_stats, "compute_rolling_trade_stats", lambda **_: {})
    monkeypatch.setattr(trade_stats, "build_trade_stats", lambda **_: {})
    monkeypatch.setattr(trade_stats, "compute_order_stats", lambda **_: {})

    if target == "summarize":
        monkeypatch.setattr(
            summary,
            "summarize",
            lambda _pnls: (_ for _ in ()).throw(RuntimeError("summary broke")),
        )
    if target == "attribute":
        monkeypatch.setattr(
            attribution,
            "attribute",
            lambda _outcomes: (_ for _ in ()).throw(RuntimeError("attribution broke")),
        )
    if target == "streaks":
        monkeypatch.setattr(
            streaks,
            "summarize_streaks",
            lambda _pnls: (_ for _ in ()).throw(RuntimeError("streaks broke")),
        )
    if target == "temporal":
        monkeypatch.setattr(
            temporal,
            "temporal_summary",
            lambda _outcomes: (_ for _ in ()).throw(RuntimeError("temporal broke")),
        )

    rep = pr._collect()

    assert outcome_reads == 1
    assert error_key in rep
    assert rep["completeness"] == "partial"
    assert rep["complete"] is False
    failed_section = {
        "summarize": "equity",
        "attribute": "attribution",
        "streaks": "streaks",
        "temporal": "temporal",
    }[target]
    assert failed_section not in rep["sections"]
    assert healthy.issubset(rep["sections"])
    assert all(name in rep for name in healthy)
    assert "equity_error" not in rep or target == "summarize"
    assert "attribution_error" not in rep or target == "attribute"


def test_report_failure_manifest_matches_human_and_json_shape(monkeypatch):
    import aletheia.analytics.trade_stats as trade_stats
    import performance_report as pr

    _patch_common(monkeypatch)
    monkeypatch.setattr(
        trade_stats,
        "compute_rolling_trade_stats",
        lambda **_: {"total_trades": 2, "win_rate": 50.0, "total_pnl": 3.0},
    )
    monkeypatch.setattr(
        trade_stats,
        "build_trade_stats",
        lambda **_: (_ for _ in ()).throw(RuntimeError("trade stats broke")),
    )
    monkeypatch.setattr(
        trade_stats,
        "compute_order_stats",
        lambda **_: {"fill_rate_pct": 100.0, "filled": 2, "errors": 0},
    )

    rep = pr._collect()
    human = pr._render_human(rep)

    assert rep["completeness"] == "partial"
    assert rep["complete"] is False
    assert "trade_stats_error" in rep
    assert "trade_stats" not in rep
    assert "trade_stats" not in rep["sections"]
    assert "rolling" in rep and "rolling" in rep["sections"]
    assert "order_stats" in rep and "order_stats" in rep["sections"]
    assert "完整性: partial" in human
    assert "trade_stats: RuntimeError: trade stats broke" in human


def test_report_no_eligible_outcomes_remains_complete_note(monkeypatch):
    import aletheia.analytics.trade_stats as trade_stats
    import performance_report as pr

    _patch_common(monkeypatch, outcomes=[])
    monkeypatch.setattr(trade_stats, "compute_rolling_trade_stats", lambda **_: {})
    monkeypatch.setattr(trade_stats, "build_trade_stats", lambda **_: {})
    monkeypatch.setattr(trade_stats, "compute_order_stats", lambda **_: {})

    rep = pr._collect()

    assert rep["equity_note"] == "no eligible closed outcomes yet"
    assert rep["completeness"] == "complete"
    assert rep["complete"] is True
