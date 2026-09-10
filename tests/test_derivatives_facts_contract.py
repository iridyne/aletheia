"""Minimal canonical #2890 contract and durable provenance boundary."""

from datetime import datetime, timezone
import importlib
import pytest

NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
INSTRUMENT = dict(
    canonical_symbol="BTC/USDT",
    source_instrument_id="BTCUSDT",
    market_type="perp",
    base_asset="BTC",
    quote_asset="USDT",
    settle_asset="USDT",
    linear=True,
)


def facts():
    return importlib.import_module("aletheia.data.derivatives_facts")


@pytest.mark.parametrize("interval", [None, 3600, 28800])
def test_funding_preserves_observed_rate_without_inventing_interval(interval):
    fact = facts().normalize_funding(
        {"fundingRate": 0.001, "timestamp": int(NOW.timestamp() * 1000)},
        instrument=INSTRUMENT,
        provider="ccxt",
        venue="bitget",
        acquired_at=NOW,
        interval_seconds=interval,
        value_kind="observed",
    )
    assert fact["value"] == 0.001
    assert fact["interval_seconds"] == interval
    assert fact["value_kind"] == "observed"
    assert fact["source_scope"] == "venue_native"
    assert fact["venue"] == "bitget"


def test_ambiguous_oi_amount_is_unavailable_even_with_mark():
    fact = facts().normalize_open_interest(
        {"openInterestAmount": 100},
        instrument=INSTRUMENT,
        provider="ccxt",
        venue="okx",
        acquired_at=NOW,
        amount_unit=None,
        mark_price=60000,
    )
    assert fact["availability"] == "unavailable"
    assert fact["value"] is None
    assert fact["unavailable_reason"] == "oi_unit_unproven"


def test_missing_source_clock_remains_missing_and_forecast_stays_forecast():
    fact = facts().normalize_funding(
        {"fundingRate": 0.001},
        instrument={**INSTRUMENT, "source_instrument_id": "BTC", "quote_asset": "USDC", "settle_asset": "USDC"},
        provider="hyperliquid_public",
        venue="hyperliquid",
        acquired_at=NOW,
        interval_seconds=3600,
        value_kind="forecast",
    )
    assert fact["source_observed_at"] is None
    assert fact["acquired_at"] == NOW.isoformat()
    assert fact["value_kind"] == "forecast"


def test_nonfinite_funding_cannot_be_available():
    fact = facts().normalize_funding(
        {"fundingRate": float("nan")},
        instrument=INSTRUMENT,
        provider="ccxt",
        venue="bitget",
        acquired_at=NOW,
        interval_seconds=None,
        value_kind="observed",
    )
    assert fact["availability"] == "invalid"
    assert fact["value"] is None


def test_aggregate_liquidation_does_not_gain_exact_venue_or_zero_price():
    fact = facts().project_liquidation(
        {
            "symbol": "BTC/USDT",
            "exchange": "coinalyze",
            "quantity_usd": 250000,
            "price": 0.0,
            "side": "long",
            "event_time": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
        },
        source_scope="market_aggregate",
        instrument=INSTRUMENT,
        now=NOW,
    )
    assert fact["venue"] is None
    assert fact["source_scope"] == "market_aggregate"
    assert fact["event_price"] is None
    assert fact["unit"] == "USD"


def test_new_provenance_survives_dedup_without_rewriting_legacy():
    from aletheia.storage import repositories as repo
    from aletheia.data.collectors import derivatives
    import json

    clock = datetime.now(timezone.utc)
    raw = {"fundingRate": 0.001, "interval": "1h", "timestamp": int(clock.timestamp() * 1000)}
    snapshot = derivatives._parse_funding("BTC/USDT", "okx", raw).model_dump()
    repo.insert_funding_rate_snapshot(snapshot)
    legacy = repo.latest_funding_rate("BTC/USDT", exchange="okx")
    assert (
        facts().project_snapshot(legacy, "funding", now=clock)[0]["unavailable_reason"] == "legacy_provenance_unknown"
    )
    market = dict(id="BTC-USDT-SWAP", swap=True, base="BTC", quote="USDT", settle="USDT", linear=True)
    snapshot.pop("id")
    snapshot["derivatives_metadata_json"] = facts().ccxt_metadata("BTC/USDT", "okx", market, raw, family="funding")
    repo.insert_funding_rate_snapshot(snapshot)
    current = repo._select_all("funding_rate_snapshots", order="rowid DESC", limit=1)[0]
    assert current["id"] != legacy["id"]
    assert current["funding_rate_annualized"] == legacy["funding_rate_annualized"]
    projected = facts().read_derivatives_facts("BTC/USDT", venue="okx", now=clock)[0]
    assert projected["availability"] == "available"
    assert projected["interval_seconds"] == 3600
    assert projected["value"] == 0.001
    rows = repo._select_all("funding_rate_snapshots")
    assert next(r for r in rows if r["id"] == legacy["id"])["derivatives_metadata_json"] is None
    # Identical new provenance still deduplicates; this is not a new row-per-read mechanism.
    snapshot.pop("id")
    repo.insert_funding_rate_snapshot(snapshot)
    assert len(repo._select_all("funding_rate_snapshots")) == 2
    assert json.loads(current["derivatives_metadata_json"])["instrument"]["source_instrument_id"] == "BTC-USDT-SWAP"


def test_ccxt_usd_proof_and_binance_population_are_independent_of_primary_venue():
    market = dict(id="BTC-USDT-SWAP", swap=True, base="BTC", quote="USDT", settle="USDT", linear=True)
    raw = dict(
        openInterestAmount=100,
        openInterestValue=1_000_000,
        info={"oiUsd": "1000000"},
        timestamp=int(NOW.timestamp() * 1000),
    )
    row = dict(
        symbol="BTC/USDT",
        exchange="okx",
        open_interest_usd=1_000_000,
        long_short_ratio=1.8,
        top_trader_position_ratio=0.7,
        acquired_at=NOW.isoformat(),
        source_observed_at=NOW.isoformat(),
        source_time_status="provided",
    )
    provenance = dict(
        instrument=INSTRUMENT, source_observed_at=None, source_time_status="missing", acquired_at=NOW.isoformat()
    )
    row["derivatives_metadata_json"] = facts().ccxt_metadata(
        "BTC/USDT",
        "okx",
        market,
        raw,
        family="open_interest",
        ratios=dict(long_short_ratio=provenance, top_trader_position_ratio=provenance),
    )
    projected = facts().project_snapshot(row, "open_interest", now=NOW)
    assert projected[0]["value"] == 1_000_000 and projected[0]["venue"] == "okx"
    assert [(f["venue"], f["population"], f["value"]) for f in projected[1:]] == [
        ("binance", "global_accounts", 1.8),
        ("binance", "top_trader_positions", 0.7),
    ]
    raw["info"] = {}
    row["derivatives_metadata_json"] = facts().ccxt_metadata("BTC/USDT", "okx", market, raw, family="open_interest")
    assert facts().project_snapshot(row, "open_interest", now=NOW)[0]["unavailable_reason"] == "oi_unit_unproven"
