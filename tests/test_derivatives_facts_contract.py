from tests.market_data import parse_funding
from extensions.market_data import decode_derivatives_metadata
import pytest


@pytest.mark.parametrize("venue,field,expected", [
    ("okx", "oiUsd", "USD"), ("okx", "sumOpenInterestValue", None),
    ("binance", "sumOpenInterestValue", "USD"), ("binance", "sumOpenInterestUsd", "USD"),
    ("binance", "oiUsd", None), ("bitget", "oiUsd", None),
])
def test_metadata_proof_is_adapter_owned_and_generic_default_is_unknown(venue, field, expected):
    import json
    from aletheia.data import derivatives_facts
    from extensions.exchanges.ccxt.derivatives_metadata import ccxt_metadata

    raw = {"openInterestValue": 123, "info": {field: "123"}}
    configured = json.loads(decode_derivatives_metadata("BTC/USDT", venue, {}, raw, family="open_interest"))
    assert configured["oi_unit"] == expected
    generic = json.loads(ccxt_metadata("BTC/USDT", venue, {}, raw, family="open_interest"))
    assert generic["oi_unit"] is None
    assert not hasattr(derivatives_facts, "ccxt_metadata")


@pytest.mark.parametrize("venue", ["okx", "binance"])
def test_missing_native_metadata_rules_do_not_fall_back(monkeypatch, venue):
    import builtins

    original = builtins.__import__

    def without_rules(name, *args, **kwargs):
        if name == f"extensions.exchanges.{venue}.derivatives_metadata":
            raise ModuleNotFoundError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_rules)
    with pytest.raises(ModuleNotFoundError):
        decode_derivatives_metadata("BTC/USDT", venue, {}, {}, family="open_interest")


@pytest.mark.parametrize("interval,expected", [
    ("8h", 28800), ("0.5h", 1800), (None, None),
    ("0h", None), ("nanh", None), (True, None),
])
def test_ccxt_shape_helpers_preserve_unknowns_outside_core(interval, expected):
    from aletheia.data import derivatives_facts
    from extensions.exchanges.ccxt.derivatives_metadata import (
        funding_interval_seconds, instrument_from_market,
    )

    assert funding_interval_seconds({"interval": interval}) == expected
    instrument = instrument_from_market("BTC/USDT", {"swap": True, "contractSize": "NaN"})
    assert instrument == dict(
        canonical_symbol="BTC/USDT", source_instrument_id=None, market_type="perp",
        base_asset=None, quote_asset=None, settle_asset=None, linear=None, contract_size=None,
    )
    assert not hasattr(derivatives_facts, "funding_interval_seconds")
    assert not hasattr(derivatives_facts, "instrument_from_market")


"""Minimal canonical #2890 contract and durable provenance boundary."""

from datetime import datetime, timedelta, timezone
import importlib


def test_fact_normalization_does_not_import_network_collector():
    import subprocess
    import sys

    code = '''
import importlib.abc
import sys
from datetime import datetime, timezone

class NoCollector(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.data.collectors.derivatives" or fullname.startswith("extensions"):
            raise AssertionError("fact normalization must not import collectors or Extensions")

sys.meta_path.insert(0, NoCollector())
from aletheia.data.derivatives_facts import normalize_funding, normalize_open_interest
now = datetime.now(timezone.utc)
instrument = dict(canonical_symbol="BTC/USDT", source_instrument_id="BTCUSDT", market_type="perp",
    base_asset="BTC", quote_asset="USDT", settle_asset="USDT", linear=True, contract_size=None)
args = dict(instrument=instrument, provider="ccxt", venue="okx", acquired_at=now)
source = {"timestamp": int(now.timestamp() * 1000)}
assert normalize_funding(.001, source_observed_at=now.isoformat(), source_time_status="provided", **args)["value"] == .001
assert normalize_open_interest(reported_value=123, source_observed_at=now.isoformat(), source_time_status="provided", value_unit="USD", **args)["value"] == 123
assert "aletheia.data.collectors.derivatives" not in sys.modules
sys.meta_path.pop(0)
from aletheia.data.collectors import derivatives
assert not hasattr(derivatives, "provider_source_time")
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

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


@pytest.mark.parametrize("values,expected", [
    ({"reported_value": 0, "value_unit": "USD"}, (0, None)),
    ({"reported_value": -1, "value_unit": "USD"}, (None, "negative_open_interest")),
    ({"reported_value": float("inf"), "value_unit": "USD"}, (None, "nonfinite_value")),
    ({"amount": 100, "amount_unit": "contracts", "mark_price": 2}, (2, None)),
    ({"amount": 100, "amount_unit": "base", "mark_price": 2}, (200, None)),
    ({"amount": 100, "mark_price": 2}, (None, "oi_unit_unproven")),
])
def test_core_oi_conversion_uses_normalized_scalars_without_wire_parser(values, expected):
    import inspect
    assert facts().open_interest_usd(instrument={**INSTRUMENT, "contract_size": .01}, **values) == expected
    for function in (facts().open_interest_usd, facts().normalize_open_interest):
        assert "raw" not in inspect.signature(function).parameters
        assert "openInterest" not in inspect.getsource(function)
    assert not hasattr(facts(), "provider_source_time")


@pytest.mark.parametrize("interval", [None, 3600, 28800])
def test_funding_preserves_observed_rate_without_inventing_interval(interval):
    import inspect
    assert "raw" not in inspect.signature(facts().normalize_funding).parameters
    fact = facts().normalize_funding(
        0.001, source_observed_at=NOW.isoformat(), source_time_status="provided",
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
        amount=100,
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
        0.001,
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
        float("nan"),
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
    import json

    clock = datetime.now(timezone.utc)
    raw = {"fundingRate": 0.001, "interval": "1h", "timestamp": int(clock.timestamp() * 1000)}
    snapshot = parse_funding("BTC/USDT", "okx", raw).model_dump()
    snapshot["derivatives_metadata_json"] = None
    repo.insert_funding_rate_snapshot(snapshot)
    legacy = repo.latest_funding_rate("BTC/USDT", exchange="okx")
    assert (
        facts().project_snapshot(legacy, "funding", now=clock)[0]["unavailable_reason"] == "legacy_provenance_unknown"
    )
    market = dict(id="BTC-USDT-SWAP", swap=True, base="BTC", quote="USDT", settle="USDT", linear=True)
    snapshot.pop("id")
    snapshot["derivatives_metadata_json"] = decode_derivatives_metadata("BTC/USDT", "okx", market, raw, family="funding")
    repo.insert_funding_rate_snapshot(snapshot)
    current = repo._select_all("funding_rate_snapshots", order="rowid DESC", limit=1)[0]
    assert current["id"] != legacy["id"]
    assert legacy["funding_rate_annualized"] is None
    assert current["funding_rate_annualized"] == pytest.approx(8.76)
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


@pytest.mark.parametrize("identity", [{}, {"provider": "fixture_public", "venue": "fixture"},
                                      {"provider": "fixture_public"}, {"provider": None, "venue": None}])
def test_ccxt_usd_proof_and_binance_population_are_independent_of_primary_venue(identity):
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
    provenance.update(identity)
    row["derivatives_metadata_json"] = decode_derivatives_metadata(
        "BTC/USDT",
        "okx",
        market,
        raw,
        family="open_interest",
        ratios=dict(long_short_ratio=provenance, top_trader_position_ratio=provenance),
    )
    before = dict(row)
    projected = facts().project_snapshot(row, "open_interest", now=NOW)
    assert row == before
    assert projected[0]["value"] == 1_000_000 and projected[0]["venue"] == "okx"
    expected_venue = identity.get("venue") if identity else "binance"
    valid = not identity or bool(identity.get("provider") and identity.get("venue"))
    assert [(f["venue"], f["population"], f["value"]) for f in projected[1:]] == [
        (expected_venue, "global_accounts", 1.8 if valid else None),
        (expected_venue, "top_trader_positions", 0.7 if valid else None),
    ]
    assert {f["provider"] for f in projected[1:]} == {identity.get("provider") if identity else "binance_public"}
    raw["info"] = {}
    row["derivatives_metadata_json"] = decode_derivatives_metadata("BTC/USDT", "okx", market, raw, family="open_interest")
    assert facts().project_snapshot(row, "open_interest", now=NOW)[0]["unavailable_reason"] == "oi_unit_unproven"


def test_native_retry_keeps_legacy_heartbeat_but_new_interval_is_persisted():
    from extensions.hyperliquid.collector import _parse_funding, hyperliquid_metadata
    from aletheia.storage import repositories as repo
    now = datetime.now(timezone.utc)
    first = _parse_funding('BTC/USDT', {'funding':'.001', 'markPx':'60000'}, now.isoformat()).model_dump()
    first['derivatives_metadata_json'] = hyperliquid_metadata('BTC/USDT', now.isoformat())
    repo.insert_funding_rate_snapshot(first)
    later = (now + timedelta(seconds=1)).isoformat()
    retry = {**first, 'observed_at': later,
        'derivatives_metadata_json': hyperliquid_metadata('BTC/USDT', later)}
    retry.pop('id')
    repo.insert_funding_rate_snapshot(retry)
    assert len(repo._select_all('funding_rate_snapshots')) == 1
    retry.pop('id')
    retry['derivatives_metadata_json'] = hyperliquid_metadata('BTC/USDT', later, interval_seconds=7200)
    repo.insert_funding_rate_snapshot(retry)
    assert len(repo._select_all('funding_rate_snapshots')) == 2
