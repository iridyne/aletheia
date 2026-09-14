"""Unit tests for MacroCollector + _fetch_fng (Step 2 Fear & Greed).

Covers MS-C02 invariant: F&G failure MUST NOT regress other macro fields.

All tests are offline — httpx_client is monkeypatched to return fake responses;
CoinGecko/FRED/yfinance paths are stubbed out so we only exercise the F&G code path
and the collector's error-isolation contract.

Async functions are driven via asyncio.run() (no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import extensions.macro.collector as macro_mod
from extensions.macro.collector import MacroCollector, _fetch_fng


def test_missing_fred_adapter_preserves_other_macro_facts_without_raw_key_access(monkeypatch):
    from types import SimpleNamespace

    def forbidden(*args, **kwargs):
        raise AssertionError("Core must not read a raw FRED credential")

    async def coingecko():
        return 50.0, 1e12, 1.5

    monkeypatch.setattr("aletheia.config.secrets.get_secret", forbidden)
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: SimpleNamespace(
        market_data=SimpleNamespace(enable_fear_greed=False)))
    monkeypatch.setattr(macro_mod, "_fred_macro_fetch", None)
    monkeypatch.setattr(macro_mod, "_fred_research_fetch", None)
    monkeypatch.setattr(macro_mod, "_fred_is_configured", None)
    monkeypatch.setattr(macro_mod, "_fetch_coingecko", coingecko)
    monkeypatch.setattr(macro_mod, "_fetch_one_tradfi_ticker", lambda _: None)
    result = asyncio.run(MacroCollector().collect([]))
    assert result["snapshot"].btc_dominance == 50.0
    assert result["snapshot"].vix_close is None
    assert result["fred_source_batches"] == []
    assert "fred_research:failed" in result["errors"]


# ---------------------------------------------------------------------------
# Test scaffolding: fake httpx client
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        self.calls.append((url, params))
        return _FakeResponse(self._payload)


def _make_fake_httpx(payload: dict[str, Any]):
    """Build a drop-in replacement for httpx_client that yields _FakeClient(payload)."""

    @asynccontextmanager
    async def fake_httpx_client(timeout: float = 30, **kwargs):
        yield _FakeClient(payload)

    return fake_httpx_client


# ---------------------------------------------------------------------------
# 1. _fetch_fng — happy path
# ---------------------------------------------------------------------------

def test_fetch_fng_parses_value_and_classification(monkeypatch):
    payload = {
        "data": [
            {"value": "15", "value_classification": "Extreme Fear", "timestamp": "1234"}
        ]
    }
    monkeypatch.setattr(macro_mod, "httpx_client", _make_fake_httpx(payload))

    value, classification = asyncio.run(_fetch_fng())

    assert value == 15
    assert classification == "Extreme Fear"


# ---------------------------------------------------------------------------
# 2. _fetch_fng — empty data
# ---------------------------------------------------------------------------

def test_fetch_fng_handles_empty_data(monkeypatch):
    monkeypatch.setattr(macro_mod, "httpx_client", _make_fake_httpx({"data": []}))

    value, classification = asyncio.run(_fetch_fng())

    assert value is None
    assert classification is None


# ---------------------------------------------------------------------------
# 3. _fetch_fng — non-int value never raises
# ---------------------------------------------------------------------------

def test_fetch_fng_handles_non_int_value(monkeypatch):
    payload = {"data": [{"value": "not_a_number"}]}
    monkeypatch.setattr(macro_mod, "httpx_client", _make_fake_httpx(payload))

    # Must NOT raise — must return (None, None).
    value, classification = asyncio.run(_fetch_fng())

    assert value is None
    assert classification is None


# ---------------------------------------------------------------------------
# 4. MacroCollector — F&G disabled in config → _fetch_fng NOT called
# ---------------------------------------------------------------------------

def test_macro_collect_with_fng_disabled(monkeypatch):
    # Stub the other paths so collect() doesn't try network.
    async def _fake_coingecko():
        return (50.0, 1e12, 1.5)

    monkeypatch.setattr(macro_mod, "_fetch_coingecko", _fake_coingecko)
    monkeypatch.setattr(macro_mod, "_fetch_one_tradfi_ticker", lambda _ticker: None)
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    # Spy on _fetch_fng: track whether it gets called.
    called = {"flag": False}

    async def _spy_fng():
        called["flag"] = True
        return (10, "Extreme Fear")

    monkeypatch.setattr(macro_mod, "_fetch_fng", _spy_fng)

    # Patch load_config to return a config with enable_fear_greed=False.
    class _FakeMarketData:
        enable_fear_greed = False

    class _FakeCfg:
        market_data = _FakeMarketData()

    import aletheia.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "load_config", lambda: _FakeCfg())

    result = asyncio.run(MacroCollector().collect([]))
    snapshot = result["snapshot"]

    assert snapshot.fng_value is None
    assert snapshot.fng_classification is None
    assert called["flag"] is False, "_fetch_fng must NOT be called when disabled"


def test_macro_collect_zero_usable_inputs_returns_no_snapshot(monkeypatch):
    async def _empty_coingecko():
        return (None, None, None)

    async def _empty_fng():
        return (None, None)

    monkeypatch.setattr(macro_mod, "_fetch_coingecko", _empty_coingecko)
    monkeypatch.setattr(macro_mod, "_fetch_one_tradfi_ticker", lambda _ticker: None)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(macro_mod, "_fetch_fng", _empty_fng)

    class _FakeMarketData:
        enable_fear_greed = True

    class _FakeCfg:
        market_data = _FakeMarketData()

    import aletheia.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "load_config", lambda: _FakeCfg())

    result = asyncio.run(MacroCollector().collect([]))

    assert result["snapshot"] is None
    assert "macro:no_usable_inputs" in result["errors"]
    assert "coingecko:empty" in result["errors"]
    assert "fng:empty" in result["errors"]
    assert "yfinance:empty" in result["errors"]


def test_macro_collect_fng_only_does_not_emit_neutral_regime(monkeypatch):
    async def _empty_coingecko():
        return (None, None, None)

    async def _valid_fng():
        return (15, "Extreme Fear")

    monkeypatch.setattr(macro_mod, "_fetch_coingecko", _empty_coingecko)
    monkeypatch.setattr(macro_mod, "_fetch_one_tradfi_ticker", lambda _ticker: None)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(macro_mod, "_fetch_fng", _valid_fng)

    class _FakeMarketData:
        enable_fear_greed = True

    class _FakeCfg:
        market_data = _FakeMarketData()

    import aletheia.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "load_config", lambda: _FakeCfg())

    result = asyncio.run(MacroCollector().collect([]))
    snapshot = result["snapshot"]

    assert snapshot is not None
    assert snapshot.fng_value == 15
    assert snapshot.fng_classification == "Extreme Fear"
    assert snapshot.risk_regime == "unavailable"


# ---------------------------------------------------------------------------
# 5. MS-C02 invariant: F&G failure does not regress other fields
# ---------------------------------------------------------------------------

def test_macro_collect_fng_failure_does_not_regress_other_fields(monkeypatch):
    # CoinGecko returns valid data.
    async def _fake_coingecko():
        return (50.0, 1e12, 1.5)

    monkeypatch.setattr(macro_mod, "_fetch_coingecko", _fake_coingecko)
    monkeypatch.setattr(macro_mod, "_fetch_one_tradfi_ticker", lambda _ticker: None)
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    # F&G raises — must be caught, must not poison other fields.
    async def _exploding_fng():
        raise RuntimeError("alt.me down")

    monkeypatch.setattr(macro_mod, "_fetch_fng", _exploding_fng)

    # Config: F&G enabled (so the call is attempted).
    class _FakeMarketData:
        enable_fear_greed = True

    class _FakeCfg:
        market_data = _FakeMarketData()

    import aletheia.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "load_config", lambda: _FakeCfg())

    result = asyncio.run(MacroCollector().collect([]))
    snapshot = result["snapshot"]
    errors = result["errors"]

    # Other fields preserved.
    assert snapshot.btc_dominance == 50.0
    assert snapshot.total_market_cap_usd == 1e12
    assert snapshot.total_market_cap_change_24h_pct == 1.5

    # F&G fields None.
    assert snapshot.fng_value is None
    assert snapshot.fng_classification is None

    # Error recorded with fng: prefix.
    assert any(e.startswith("fng:") for e in errors), f"expected fng: error in {errors}"
