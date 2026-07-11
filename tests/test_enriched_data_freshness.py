"""Regression tests for decision-context freshness of enriched market data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


NOW = datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)


def _row(age: timedelta, **values) -> dict:
    return {"observed_at": (NOW - age).isoformat(), **values}


def test_freshness_policy_fails_closed_on_bad_or_future_timestamps():
    from aletheia.data.freshness import assess_freshness, fresh_row

    assert fresh_row(_row(timedelta(minutes=14)), "orderbook", now=NOW) is not None
    assert fresh_row(_row(timedelta(minutes=16)), "orderbook", now=NOW) is None
    assert fresh_row({"observed_at": "not-a-time"}, "orderbook", now=NOW) is None
    assert fresh_row({}, "orderbook", now=NOW) is None

    verdict = assess_freshness(
        {"observed_at": (NOW + timedelta(minutes=6)).isoformat()},
        "orderbook",
        now=NOW,
    )
    assert verdict.state == "future"
    assert verdict.usable is False


def test_context_builder_gates_whole_rows_and_preserves_fresh_lsr(monkeypatch):
    from aletheia.data.context_builder import (
        build_market_context_from_db,
        render_enriched_context_md,
    )
    from aletheia.data.models import LeverageState
    from aletheia.storage import repositories as repo

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        repo,
        "latest_orderbook",
        lambda symbol, exchange="", market_type="": _row(
            timedelta(minutes=16), exchange=exchange, spread_bps=1.0,
            total_depth_10bps_usd=1_000_000,
        ),
    )

    def funding(symbol, exchange=None):
        calls.append(("funding", exchange))
        return _row(
            timedelta(minutes=16), funding_rate=0.01,
            funding_rate_annualized=10.95,
        )

    def oi(symbol, exchange=None):
        calls.append(("oi", exchange))
        return _row(
            timedelta(minutes=2), open_interest_usd=12_000_000,
            oi_change_1h_pct=4.5, long_short_ratio=2.09,
        )

    monkeypatch.setattr(repo, "latest_funding_rate", funding)
    monkeypatch.setattr(repo, "latest_open_interest", oi)
    monkeypatch.setattr(repo, "recent_liquidations", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        repo,
        "latest_macro_snapshot",
        lambda: _row(timedelta(minutes=31), risk_regime="risk_on"),
    )

    snapshot = build_market_context_from_db(
        ["BTC/USDT"], exchange="okx", market_type="perp", now=NOW,
    )

    assert snapshot.tradability == {}
    assert snapshot.macro == {}
    assert calls == [("funding", "okx"), ("oi", "okx")]
    leverage = LeverageState(**snapshot.leverage["BTC/USDT"])
    assert leverage.funding_rate is None
    assert leverage.funding_annualized is None
    assert leverage.open_interest_usd == 12_000_000
    assert leverage.oi_delta_1h_pct == 4.5
    assert leverage.long_short_ratio == 2.09
    assert "2.09" in render_enriched_context_md(
        ["BTC/USDT"], {}, {"BTC/USDT": leverage}, None,
    )


def test_context_builder_omits_empty_stale_derivatives(monkeypatch):
    from aletheia.data.context_builder import build_market_context_from_db
    from aletheia.storage import repositories as repo

    stale = _row(timedelta(hours=1))
    monkeypatch.setattr(repo, "latest_orderbook", lambda *args, **kwargs: None)
    monkeypatch.setattr(repo, "latest_funding_rate", lambda *args, **kwargs: stale)
    monkeypatch.setattr(repo, "latest_open_interest", lambda *args, **kwargs: stale)
    monkeypatch.setattr(repo, "recent_liquidations", lambda *args, **kwargs: [])
    monkeypatch.setattr(repo, "latest_macro_snapshot", lambda: None)

    snapshot = build_market_context_from_db(["BTC/USDT"], now=NOW)
    assert snapshot.leverage == {}
    assert "derivatives" in snapshot.stale_domains


def test_universe_db_fallback_uses_only_fresh_exact_venue_rows(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.universe.features import _load_fresh_enrichment_rows

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        repo,
        "latest_orderbook",
        lambda symbol, exchange="", market_type="": _row(
            timedelta(minutes=2), total_depth_10bps_usd=50_000, spread_bps=2.0,
        ),
    )

    def funding(symbol, exchange=None):
        calls.append(("funding", exchange))
        return _row(timedelta(minutes=16), funding_rate=0.003)

    def oi(symbol, exchange=None):
        calls.append(("oi", exchange))
        return _row(
            timedelta(minutes=3), open_interest_usd=99_000,
            oi_change_1h_pct=1.2,
        )

    monkeypatch.setattr(repo, "latest_funding_rate", funding)
    monkeypatch.setattr(repo, "latest_open_interest", oi)

    ob, fr, oi_row = _load_fresh_enrichment_rows(
        {"symbol": "BTC/USDT", "venue": "okx", "market_type": "perp"},
        now=NOW,
    )
    assert ob is not None
    assert fr is None
    assert oi_row is not None
    assert calls == [("funding", "okx"), ("oi", "okx")]


def test_aggregated_derivatives_are_not_primary_venue_rows():
    from aletheia.storage.repositories import _latest_deriv_where

    where, excluded = _latest_deriv_where(None)
    assert "NOT IN" in where
    assert "coinalyze" in excluded
