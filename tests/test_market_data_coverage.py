"""Coverage and CLI honesty tests for persisted market-data diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


NOW = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)


def test_data_coverage_distinguishes_fresh_stale_empty_and_idle(monkeypatch):
    from aletheia.data import data_coverage

    rows = {
        "orderbook": {"row_count": 4, "symbol_count": 2, "newest": (NOW - timedelta(minutes=2)).isoformat()},
        "primary_funding": {"row_count": 3, "symbol_count": 2, "newest": (NOW - timedelta(minutes=20)).isoformat()},
        "primary_open_interest": {"row_count": 0, "symbol_count": 0, "newest": None},
        "long_short_ratio": {"row_count": 1, "symbol_count": 1, "newest": (NOW - timedelta(minutes=2)).isoformat()},
        "liquidations": {"row_count": 10, "symbol_count": 2, "newest": (NOW - timedelta(hours=3)).isoformat()},
        "macro": {"row_count": 2, "symbol_count": 0, "newest": (NOW - timedelta(minutes=5)).isoformat()},
        "onchain": {"row_count": 2, "symbol_count": 0, "newest": (NOW - timedelta(minutes=5)).isoformat()},
        "ohlcv": {"row_count": 200, "symbol_count": 2, "newest": int((NOW - timedelta(minutes=40)).timestamp() * 1000)},
    }
    monkeypatch.setattr(data_coverage, "_query_domain", lambda definition: {
        **rows[definition.domain], "source_counts": {"okx": rows[definition.domain]["row_count"]},
    })
    data_coverage.clear_data_coverage_cache()

    report = data_coverage.get_data_coverage_report(now=NOW)
    states = {row["domain"]: row["state"] for row in report["domains"]}

    assert states["orderbook"] == "fresh"
    assert states["primary_funding"] == "stale"
    assert states["primary_open_interest"] == "empty"
    assert states["liquidations"] == "idle"
    assert states["ohlcv"] == "fresh"
    assert report["status"] == "attention"
    assert report["critical_problem_count"] == 2


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
    from aletheia.data import data_coverage

    def query(definition):
        values = {
            "row_count": 1,
            "symbol_count": 1,
            "newest": source_observed_at or acquired_at,
            "source_counts": {"okx": 1},
        }
        if definition.domain == "primary_funding":
            values["freshness_rows"] = [{
                "observed_at": source_observed_at or acquired_at,
                "source_observed_at": source_observed_at,
                "acquired_at": acquired_at,
                "source_time_status": source_time_status,
            }]
        return values

    monkeypatch.setattr(data_coverage, "_query_domain", query)

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


def test_primary_derivative_coverage_uses_latest_acquisition_from_sqlite():
    from aletheia.data.data_coverage import get_data_coverage_report
    from aletheia.storage.repositories import insert_funding_rate_snapshot

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
