"""Coverage and CLI honesty tests for persisted market-data diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


NOW = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)


def test_current_orderbook_coverage_requires_exact_venue_and_market(monkeypatch):
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data.data_coverage import get_data_coverage_report
    from aletheia.storage.repositories import insert_orderbook_snapshot

    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "binance"
    cfg.market_data.observed_instruments = ["binance:BTC/USDT:perp"]
    monkeypatch.setattr(loader, "load_config", lambda **kwargs: cfg)
    for exchange, market in [("bybit", "perp"), ("binance", "spot")]:
        insert_orderbook_snapshot({
            "symbol": "BTC/USDT", "exchange": exchange, "market_type": market,
            "bid_price": 100.0, "ask_price": 101.0, "spread_bps": 100.0,
            "observed_at": (NOW - timedelta(minutes=2)).isoformat(),
        })
    report = get_data_coverage_report(now=NOW)
    domains = {row["domain"]: row for row in report["domains"]}
    assert domains["orderbook"]["state"] == "empty"
    assert domains["orderbook"]["missing_target_count"] == 1
    assert domains["orderbook"]["inventory"]["state"] == "fresh"
    assert domains["liquidations"]["state"] == "idle"
    insert_orderbook_snapshot({
        "symbol": "BTC/USDT", "exchange": "binance", "market_type": "perp",
        "bid_price": 100.0, "ask_price": 101.0, "spread_bps": 100.0,
        "observed_at": (NOW - timedelta(minutes=20)).isoformat(),
    })
    report = get_data_coverage_report(now=NOW)
    orderbook = next(row for row in report["domains"] if row["domain"] == "orderbook")
    assert orderbook["state"] == "stale"
    assert orderbook["inventory"]["state"] == "fresh"



@pytest.mark.parametrize(
    ("source_time_status", "source_observed_at", "acquired_at", "expected_state"),
    (
        (
            "provided",
            (NOW - timedelta(minutes=2)).isoformat(),
            (NOW - timedelta(minutes=16)).isoformat(),
            "stale",
        ),
        (
            "provided",
            (NOW - timedelta(minutes=2)).isoformat(),
            (NOW - timedelta(minutes=2)).isoformat(),
            "fresh",
        ),
        ("missing", None, (NOW - timedelta(minutes=2)).isoformat(), "fresh"),
        ("invalid", None, (NOW - timedelta(minutes=2)).isoformat(), "invalid"),
        (
            "future",
            (NOW + timedelta(minutes=6)).isoformat(),
            (NOW - timedelta(minutes=2)).isoformat(),
            "invalid",
        ),
    ),
)
def test_primary_derivative_coverage_uses_canonical_two_clock_freshness(
    monkeypatch,
    source_time_status,
    source_observed_at,
    acquired_at,
    expected_state,
):
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data import data_coverage
    from aletheia.storage.repositories import insert_funding_rate_snapshot

    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "okx"
    cfg.market_data.observed_instruments = ["okx:BTC/USDT:perp"]
    monkeypatch.setattr(loader, "load_config", lambda **kwargs: cfg)
    insert_funding_rate_snapshot({
        "symbol": "BTC/USDT", "exchange": "okx", "funding_rate": 0.0001,
        "funding_rate_annualized": 0.1095,
        "observed_at": source_observed_at or acquired_at,
        "source_observed_at": source_observed_at,
        "acquired_at": acquired_at, "source_time_status": source_time_status,
    })

    report = data_coverage.get_data_coverage_report(now=NOW)
    funding = next(
        row for row in report["domains"] if row["domain"] == "primary_funding"
    )

    assert funding["state"] == expected_state
    assert funding["fresh_row_count"] == (1 if expected_state == "fresh" else 0)
    assert funding["stale_row_count"] == (1 if expected_state == "stale" else 0)
    assert funding["unavailable_row_count"] == (
        1 if expected_state == "invalid" else 0
    )


def test_primary_derivative_coverage_uses_latest_acquisition_from_sqlite(monkeypatch):
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data.data_coverage import get_data_coverage_report
    from aletheia.storage.repositories import insert_funding_rate_snapshot

    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "okx"
    cfg.market_data.observed_instruments = ["okx:BTC/USDT:perp"]
    monkeypatch.setattr(loader, "load_config", lambda **kwargs: cfg)

    financial = {
        "symbol": "BTC/USDT",
        "exchange": "okx",
        "funding_rate": 0.0001,
        "funding_rate_annualized": 0.1095,
        "next_funding_time": None,
        "mark_price": 50_000.0,
    }
    insert_funding_rate_snapshot({
        **financial,
        "source_observed_at": (NOW - timedelta(minutes=3)).isoformat(),
        "acquired_at": (NOW - timedelta(minutes=3)).isoformat(),
        "source_time_status": "provided",
        "observed_at": (NOW - timedelta(minutes=3)).isoformat(),
    })
    insert_funding_rate_snapshot({
        **financial,
        "source_observed_at": None,
        "acquired_at": (NOW - timedelta(minutes=1)).isoformat(),
        "source_time_status": "missing",
        "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
    })

    report = get_data_coverage_report(now=NOW)
    funding = next(
        row for row in report["domains"] if row["domain"] == "primary_funding"
    )

    assert funding["state"] == "fresh"
    assert funding["fresh_row_count"] == 1
    assert funding["unavailable_row_count"] == 0
    assert funding["freshness_state_counts"] == {"fresh": 1}


def test_current_coverage_keeps_retired_bad_rows_in_inventory_only(monkeypatch):
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data.data_coverage import get_data_coverage_report
    from aletheia.storage.repositories import insert_funding_rate_snapshot

    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "binance"
    cfg.market_data.observed_instruments = ["binance:BTC/USDT:perp"]
    monkeypatch.setattr(loader, "load_config", lambda **kwargs: cfg)
    recent = (NOW - timedelta(minutes=2)).isoformat()
    for symbol, acquired in [("BTC/USDT", recent), ("OLD/USDT", None)]:
        insert_funding_rate_snapshot({
            "symbol": symbol, "exchange": "binance", "funding_rate": 0.0001,
            "funding_rate_annualized": 0.1095, "observed_at": recent,
            "source_time_status": "provided" if acquired else None,
            "source_observed_at": acquired, "acquired_at": acquired,
        })

    report = get_data_coverage_report(now=NOW)
    funding = next(row for row in report["domains"] if row["domain"] == "primary_funding")
    assert funding["state"] == "fresh"
    assert funding["target_count"] == 1
    assert funding["missing_target_count"] == 0
    assert funding["inventory"]["state"] == "invalid"
    assert funding["inventory"]["unavailable_row_count"] == 1


def test_current_coverage_detects_missing_target_even_with_fresh_peer(monkeypatch):
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data.data_coverage import get_data_coverage_report
    from aletheia.storage.repositories import insert_funding_rate_snapshot

    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "binance"
    cfg.market_data.observed_instruments = ["binance:BTC/USDT:perp", "binance:ETH/USDT:perp"]
    monkeypatch.setattr(loader, "load_config", lambda **kwargs: cfg)
    recent = (NOW - timedelta(minutes=2)).isoformat()
    insert_funding_rate_snapshot({
        "symbol": "BTC/USDT", "exchange": "binance", "funding_rate": 0.0001,
        "funding_rate_annualized": 0.1095, "observed_at": recent,
        "source_time_status": "provided", "source_observed_at": recent, "acquired_at": recent,
    })
    report = get_data_coverage_report(now=NOW)
    funding = next(row for row in report["domains"] if row["domain"] == "primary_funding")
    assert funding["state"] == "invalid"
    assert funding["target_count"] == 2
    assert funding["missing_target_count"] == 1
    assert funding["fresh_row_count"] == 1
    assert funding["inventory"]["state"] == "fresh"


def test_inspect_microstructure_uses_exact_venue_and_real_imbalance(monkeypatch, capsys):
    from aletheia.cli.inspect_cmd import microstructure
    from aletheia.storage import repositories as repo

    calls: list[tuple[str, str | None]] = []
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(repo, "get_active_universe_members", lambda limit=20: [{
        "asset_id": "asset-1", "symbol": "BTC/USDT", "market_type": "perp",
    }])
    monkeypatch.setattr(repo, "get_universe_asset", lambda asset_id: {"venue": "okx"})

    def funding(symbol, exchange=None):
        calls.append(("funding", exchange))
        return {"exchange": "okx", "funding_rate": 0.001, "observed_at": now}

    def oi(symbol, exchange=None):
        calls.append(("oi", exchange))
        return {
            "exchange": "okx", "open_interest_usd": 10_000,
            "oi_change_1h_pct": 2.0, "observed_at": now,
        }

    monkeypatch.setattr(repo, "latest_funding_rate", funding)
    monkeypatch.setattr(repo, "latest_open_interest", oi)
    monkeypatch.setattr(repo, "latest_orderbook", lambda *args, **kwargs: {
        "exchange": "okx", "spread_bps": 1.0, "total_depth_10bps_usd": 50_000,
        "imbalance_ratio": 1.25, "observed_at": now,
    })

    microstructure(symbol=None, limit=10, json_output=True)
    result = json.loads(capsys.readouterr().out)

    assert calls == [("funding", "okx"), ("oi", "okx")]
    assert result[0]["imbalance"] == 1.25
    assert result[0]["orderbook_state"] == "fresh"
