from __future__ import annotations


def _row(
    *,
    id: str,
    opened_at: str,
    closed_at: str,
    direction: str = "long",
    realized_pnl: float = 10.0,
) -> dict:
    return {
        "id": id,
        "position_id": id,
        "strategy_id": "strategy-1",
        "proposal_id": "proposal-1",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": direction,
        "realized_pnl": realized_pnl,
        "entry_price": 100.0,
        "quantity": 1.0,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "close_reason": "take_profit",
    }


def _mixed_hold_rows() -> list[dict]:
    return [
        _row(
            id="valid-10h",
            opened_at="2026-08-01T00:00:00+00:00",
            closed_at="2026-08-01T10:00:00+00:00",
        ),
        _row(
            id="unparseable-open",
            opened_at="legacy-open",
            closed_at="2026-08-01T10:00:00+00:00",
        ),
        _row(
            id="reversed",
            opened_at="2026-08-01T11:00:00+00:00",
            closed_at="2026-08-01T10:00:00+00:00",
        ),
    ]


def test_trade_stats_hold_averages_use_measurable_denominator(monkeypatch):
    import aletheia.analytics.family_scorecard as family_scorecard
    import aletheia.analytics.trade_stats as trade_stats
    import aletheia.storage.repositories as repo

    rows = _mixed_hold_rows()
    monkeypatch.setattr(
        trade_stats.outcome_integrity,
        "eligible_strategy_outcomes",
        lambda **_kwargs: rows,
    )
    monkeypatch.setattr(
        trade_stats.outcome_integrity,
        "build_outcome_cohort_summary",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(trade_stats, "execution_mode_identity", lambda _mode: {})
    monkeypatch.setattr(trade_stats, "outcome_window_metadata", lambda: {})
    monkeypatch.setattr(
        repo,
        "list_strategies",
        lambda limit=10_000: [{"id": "strategy-1", "parameters": "{}"}],
    )
    monkeypatch.setattr(
        family_scorecard,
        "compute_family_scorecard",
        lambda **_kwargs: [],
    )

    result = trade_stats.build_trade_stats()

    opportunity = next(r for r in result["by_source"] if r["source_type"] == "opportunity")
    assert opportunity["trade_count"] == 3
    assert opportunity["avg_hold_hours"] == 10.0
    assert opportunity["hold_time_available_count"] == 1
    assert opportunity["hold_time_unavailable_count"] == 2
    assert result["totals"]["avg_hold_hours"] == 10.0
    assert result["totals"]["hold_time_available_count"] == 1
    assert result["totals"]["hold_time_unavailable_count"] == 2
    assert sum(b["count"] for b in result["hold_distribution"]) == 1


def test_trade_stats_even_hold_samples_use_conventional_median(monkeypatch):
    import aletheia.analytics.family_scorecard as family_scorecard
    import aletheia.analytics.trade_stats as trade_stats
    import aletheia.storage.repositories as repo

    rows = [
        _row(
            id="one-hour",
            opened_at="2026-08-01T00:00:00+00:00",
            closed_at="2026-08-01T01:00:00+00:00",
        ),
        _row(
            id="one-hundred-one-hours",
            opened_at="2026-08-01T00:00:00+00:00",
            closed_at="2026-08-05T05:00:00+00:00",
        ),
    ]
    monkeypatch.setattr(
        trade_stats.outcome_integrity,
        "eligible_strategy_outcomes",
        lambda **_kwargs: rows,
    )
    monkeypatch.setattr(trade_stats, "execution_mode_identity", lambda _mode: {})
    monkeypatch.setattr(trade_stats, "outcome_window_metadata", lambda: {})
    monkeypatch.setattr(
        repo,
        "list_strategies",
        lambda limit=10_000: [{"id": "strategy-1", "parameters": "{}"}],
    )
    monkeypatch.setattr(
        family_scorecard,
        "compute_family_scorecard",
        lambda **_kwargs: [],
    )

    result = trade_stats.build_trade_stats()

    assert result["totals"]["median_hold_hours"] == 51.0


def test_trade_insights_direction_hold_averages_use_measurable_denominator():
    from aletheia.analytics.trade_insights import _by_direction, _hold_duration_buckets

    rows = [
        *_mixed_hold_rows(),
        _row(
            id="all-unavailable",
            opened_at="bad",
            closed_at="2026-08-01T10:00:00+00:00",
            direction="short",
        ),
    ]

    by_direction = _by_direction(rows)

    assert by_direction["long"]["trade_count"] == 3
    assert by_direction["long"]["avg_hold_hours"] == 10.0
    assert by_direction["long"]["hold_time_available_count"] == 1
    assert by_direction["long"]["hold_time_unavailable_count"] == 2
    assert by_direction["short"]["avg_hold_hours"] is None
    assert by_direction["short"]["hold_time_available_count"] == 0
    assert by_direction["short"]["hold_time_unavailable_count"] == 1
    assert sum(b["trade_count"] for b in _hold_duration_buckets(rows)) == 1


def test_family_scorecard_hold_averages_use_measurable_denominator(monkeypatch):
    import aletheia.analytics.family_scorecard as family_scorecard
    import aletheia.counterfactual.trades as shadow_trades
    import aletheia.storage.sqlite as sqlite

    class DummyDb:
        def execute(self, *_args, **_kwargs):
            return self

        def fetchone(self):
            return None

    monkeypatch.setattr(
        family_scorecard.outcome_integrity,
        "eligible_strategy_outcomes",
        lambda **_kwargs: _mixed_hold_rows(),
    )
    monkeypatch.setattr(family_scorecard, "execution_mode_identity", lambda _mode: {})
    monkeypatch.setattr(family_scorecard, "outcome_window_metadata", lambda: {})
    monkeypatch.setattr(sqlite, "get_db", lambda: DummyDb())
    monkeypatch.setattr(shadow_trades, "_detect_window_epochs", lambda days=90: (None, "single"))
    monkeypatch.setattr(shadow_trades, "list_deduped_shadows", lambda **_kwargs: [])

    result = family_scorecard.compute_family_scorecard()

    assert result == [{
        "family": "unknown",
        "trade_count": 3,
        "win_rate": 100.0,
        "expectancy_pct": 10.0,
        "avg_hold_hours": 10.0,
        "hold_time_available_count": 1,
        "hold_time_unavailable_count": 2,
        "shadow_count": 0,
        "alpha_vs_btc_pct": None,
        "outcome_status": "available",
    }]


def test_frontend_hold_time_coverage_contract_is_rendered():
    from pathlib import Path

    analytics = Path("frontend/src/charts/TradeAnalyticsPanel.tsx").read_text()
    insights = Path("frontend/src/charts/TradeInsightsPanel.tsx").read_text()
    client = Path("frontend/src/api/client.ts").read_text()
    generated = Path("frontend/src/api/generated/api.d.ts").read_text()

    assert "fmtHoldCoverage" in analytics
    assert "timed" in analytics
    for schema in ("DirectionStats", "TradeStatsSource", "TradeStatsFamily"):
        assert f'export type {schema} = components["schemas"]["{schema}"];' in client
        fields = generated.split(f"\n        {schema}: {{", 1)[1].split("\n        };", 1)[0]
        assert "hold_time_available_count: number" in fields
        assert "hold_time_unavailable_count: number" in fields
        assert "avg_hold_hours: number | null" in fields
    assert "avg_hold_hours === null ? '—'" in insights
    assert "timed" in insights
