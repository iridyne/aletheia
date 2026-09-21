"""Funding/OI acquisition belongs to Data; Core keeps admission semantics."""

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest


def test_features_do_not_call_derivative_endpoints():
    tree = ast.parse(
        (Path(__file__).resolve().parents[1] / "aletheia/universe/features.py").read_text()
    )
    native = {"fetch_funding_rate", "fetch_open_interest"}
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in native
        for node in ast.walk(tree)
    )


@pytest.mark.asyncio
async def test_provider_returns_normalized_funding_and_oi_without_fallback(monkeypatch):
    from extensions.data.ccxt_provider import CCXTProvider

    calls = []
    source_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

    class Exchange:
        id = "okx"
        markets = {
            "BTC/USDT:USDT": {
                "id": "BTC-USDT-SWAP",
                "symbol": "BTC/USDT:USDT",
                "swap": True,
                "base": "BTC",
                "quote": "USDT",
                "settle": "USDT",
                "linear": True,
            }
        }

        async def fetch_funding_rate(self, symbol):
            calls.append(("funding", symbol))
            return {
                "fundingRate": "0.001",
                "interval": "1h",
                "timestamp": source_timestamp,
            }

        async def fetch_open_interest(self, symbol):
            calls.append(("oi", symbol))
            return {
                "symbol": symbol,
                "openInterestValue": 123.0,
                "info": {"oiUsd": "123"},
                "timestamp": source_timestamp,
            }

    provider = CCXTProvider("okx", "bitget")
    monkeypatch.setattr(provider, "_ensure_primary", lambda market_type: Exchange())
    monkeypatch.setattr(
        provider,
        "_ensure_fallback",
        lambda *args: pytest.fail("derivative observations must not use fallback"),
    )

    funding = await provider.get_funding_observation("BTC/USDT")
    oi = await provider.get_open_interest_observation("BTC/USDT")

    assert calls == [("funding", "BTC/USDT:USDT"), ("oi", "BTC/USDT:USDT")]
    assert funding["funding_rate"] == 0.001
    assert funding["evidence"]["interval_seconds"] == 3600.0
    assert "fundingRate" not in funding
    assert oi["fact"]["availability"] == "available"
    assert oi["fact"]["value"] == 123.0
    assert "openInterestValue" not in oi


@pytest.mark.asyncio
async def test_bitget_funding_uses_existing_ticker_and_contract_interval(monkeypatch):
    import json

    from extensions.data.ccxt_provider import CCXTProvider
    from aletheia.universe.candidate_funding import (
        PERP_FUNDING_EVIDENCE_VERSION,
        proved_funding_interval,
    )

    class Exchange:
        id = "bitget"
        options = {}
        markets = {
            "BTC/USDT:USDT": {
                "id": "BTCUSDT_UMCBL",
                "symbol": "BTC/USDT:USDT",
                "swap": True,
                "base": "BTC",
                "quote": "USDT",
                "settle": "USDT",
                "linear": True,
                "info": {"fundInterval": "1"},
            }
        }

        async def load_markets(self):
            return self.markets

        async def fetch_funding_rate(self, _symbol):
            pytest.fail("Bitget funding uses the already acquired ticker")

    provider = CCXTProvider("bitget", "okx")
    exchange = Exchange()
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type: exchange)
    before = datetime.now(timezone.utc)
    assert len(await provider.get_market_instruments("perp")) == 1
    after = datetime.now(timezone.utc)
    observation = await provider.get_funding_observation(
        "BTC/USDT",
        ticker={
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "info": {"fundingRate": "0.001", "markPrice": "60000"},
        },
    )

    assert observation["funding_rate"] == 0.001
    assert observation["interval_source"] == "bitget.contracts.fundInterval"
    assert observation["interval_raw"] == "1h"
    acquired = datetime.fromisoformat(observation["interval_acquired_at"])
    assert before <= acquired <= after
    proof = observation["evidence"] | {
        "version": PERP_FUNDING_EVIDENCE_VERSION,
        "interval_source": observation["interval_source"],
        "interval_acquired_at": observation["interval_acquired_at"],
    }
    assert proved_funding_interval({
        "venue": "bitget", "symbol": "BTC/USDT",
        "entry_ts": datetime.now(timezone.utc).isoformat(),
        "funding_rate_at_entry": observation["funding_rate"],
        "funding_evidence_json": json.dumps(proof),
    }) == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", [None, "2026-09-20T00:00:00+00:00"])
async def test_failed_market_load_does_not_create_or_refresh_interval_proof(monkeypatch, previous):
    from extensions.data.ccxt_provider import CCXTProvider

    class Exchange:
        options = {}

        async def load_markets(self):
            raise RuntimeError("metadata unavailable")

    exchange = Exchange()
    if previous is not None:
        exchange._candidate_markets_acquired_at = previous
    provider = CCXTProvider("bitget", "okx")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type: exchange)
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await provider.get_market_instruments("perp")
    assert getattr(exchange, "_candidate_markets_acquired_at", None) == previous
