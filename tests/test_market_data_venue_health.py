import pytest

from extensions.data.ccxt_provider import CCXTProvider
from extensions.data.sources.binance_public import BinancePublicProvider
from extensions.data.sources.chain import FallbackChainProvider
from aletheia.data.venue_health import (
    get_provider_capability_snapshot,
    get_venue_health_snapshot,
    record_venue_failure,
    reset_venue_health,
)

_GET_TICKER = CCXTProvider.get_ticker
_GET_OHLCV = CCXTProvider.get_ohlcv


@pytest.fixture(autouse=True)
def _reset_venue_health():
    reset_venue_health()
    yield
    reset_venue_health()


def test_venue_refusal_opens_process_wide_cooldown(monkeypatch):
    import aletheia.data.venue_health as health

    events = []
    monkeypatch.setattr(health, "emit_infra_event", lambda **event: events.append(event))
    record_venue_failure("Binance", RuntimeError('418 -1003 "IP banned"'))

    snapshot = get_venue_health_snapshot()
    assert snapshot[0]["venue"] == "binance"
    assert snapshot[0]["cooling_down"] is True
    assert events[0]["code"] == "market_data_venue_cooldown"


def test_provider_capability_snapshot_keeps_policy_and_health_separate(monkeypatch):
    import aletheia.data.venue_health as health

    monkeypatch.setattr(health, "emit_infra_event", lambda **_kwargs: None)
    record_venue_failure("binance", RuntimeError("429 rate limit"))

    rows = get_provider_capability_snapshot(provider="ccxt", primary="binance", fallback="bybit")

    assert rows[0]["venue"] == "binance"
    assert rows[0]["configured"] is True
    assert rows[0]["entitled"] is True
    assert rows[0]["healthy"] is False
    assert rows[0]["permitted"] is True
    assert rows[1]["healthy"] is None


def test_provider_capability_snapshot_keeps_same_venue_primary():
    rows = get_provider_capability_snapshot(
        provider="ccxt", primary="binance", fallback="binance"
    )

    assert [(row["venue"], row["role"]) for row in rows] == [("binance", "primary")]


@pytest.mark.asyncio
async def test_ccxt_skips_a_venue_after_rate_limit_refusal(monkeypatch):
    import aletheia.data.venue_health as health

    monkeypatch.setattr(health, "emit_infra_event", lambda **_kwargs: None)
    calls = {"binance": 0, "bybit": 0}

    class BannedExchange:
        async def fetch_ticker(self, _symbol):
            calls["binance"] += 1
            raise RuntimeError("429 Too Many Requests")

    class HealthyExchange:
        async def fetch_ticker(self, _symbol):
            calls["bybit"] += 1
            return {"last": 1, "quoteVolume": 0.0, "percentage": 0.0}

    provider = CCXTProvider("binance", "bybit")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: BannedExchange())
    monkeypatch.setattr(provider, "_ensure_fallback", lambda _market_type=None: HealthyExchange())

    assert (await _GET_TICKER(provider, "BTC/USDT")).provider == "bybit"
    assert (await _GET_TICKER(provider, "ETH/USDT")).provider == "bybit"
    assert calls == {"binance": 1, "bybit": 2}


@pytest.mark.asyncio
async def test_ccxt_rejects_invalid_ticker_before_recording_success(monkeypatch):
    import extensions.data.ccxt_provider as ccxt_module

    successes = []
    monkeypatch.setattr(ccxt_module, "record_venue_success", successes.append)

    class InvalidExchange:
        async def fetch_ticker(self, _symbol):
            return {
                "last": float("nan"),
                "quoteVolume": 100.0,
                "percentage": 0.0,
            }

    class HealthyExchange:
        async def fetch_ticker(self, _symbol):
            return {"last": 2.0, "quoteVolume": 0.0, "percentage": 0.0}

    provider = CCXTProvider("binance", "bybit")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: InvalidExchange())
    monkeypatch.setattr(provider, "_ensure_fallback", lambda _market_type=None: HealthyExchange())

    ticker = await _GET_TICKER(provider, "BTC/USDT")

    assert ticker.provider == "bybit"
    assert successes == ["bybit"]


@pytest.mark.asyncio
async def test_ccxt_all_invalid_tickers_are_unavailable(monkeypatch):
    class InvalidExchange:
        async def fetch_ticker(self, _symbol):
            return {"last": float("inf"), "quoteVolume": 1.0, "percentage": 0.0}

    provider = CCXTProvider("binance", "bybit")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: InvalidExchange())
    monkeypatch.setattr(provider, "_ensure_fallback", lambda _market_type=None: InvalidExchange())

    with pytest.raises(ValueError, match="ticker last_price"):
        await _GET_TICKER(provider, "BTC/USDT")


@pytest.mark.asyncio
async def test_ccxt_all_invalid_ohlcv_keeps_venue_cooling_until_valid_data(monkeypatch):
    import extensions.data.ccxt_provider as ccxt_module
    import aletheia.data.venue_health as health

    monkeypatch.setattr(health, "emit_infra_event", lambda **_kwargs: None)
    record_venue_failure("binance", RuntimeError("429 rate limit"))
    monkeypatch.setattr(ccxt_module, "is_venue_available", lambda _name: True)

    calls: list[str] = []

    class PrimaryExchange:
        rows = [[1, "invalid", 11, 9, 10.5, 100]]

        async def fetch_ohlcv(self, _symbol, **_kwargs):
            calls.append("binance")
            return self.rows

    class FallbackExchange:
        async def fetch_ohlcv(self, _symbol, **_kwargs):
            calls.append("bybit")
            return [[1, 10, 11, 9, 10.5, 100]]

    primary = PrimaryExchange()
    provider = CCXTProvider("binance", "bybit")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: primary)
    monkeypatch.setattr(provider, "_ensure_fallback", lambda _market_type=None: FallbackExchange())
    monkeypatch.setattr(provider, "_persist", lambda *_args, **_kwargs: None)

    result = await _GET_OHLCV(provider, "BTC/USDT", limit=1, since=0)

    assert result[0].exchange == "bybit"
    assert calls == ["binance", "bybit"]
    binance = next(
        row for row in get_venue_health_snapshot() if row["venue"] == "binance"
    )
    assert binance["cooling_down"] is True
    assert binance["last_success_at"] is None

    primary.rows = [[1, 10, 11, 9, 10.5, 100]]
    result = await _GET_OHLCV(provider, "BTC/USDT", limit=1, since=0)

    assert result[0].exchange == "binance"
    binance = next(
        row for row in get_venue_health_snapshot() if row["venue"] == "binance"
    )
    assert binance["cooling_down"] is False
    assert binance["last_success_at"] is not None


@pytest.mark.asyncio
async def test_spot_chain_skips_binance_public_during_binance_cooldown(monkeypatch):
    import aletheia.data.venue_health as health

    monkeypatch.setattr(health, "emit_infra_event", lambda **_kwargs: None)
    record_venue_failure("binance", RuntimeError("418 IP banned"))

    class FailingProvider:
        async def get_ticker(self, _symbol):
            raise RuntimeError("bybit unavailable")

        async def close(self):
            pass

    binance_public = BinancePublicProvider()
    called = False

    async def should_not_run(_symbol):
        nonlocal called
        called = True
        raise AssertionError("cooling Binance REST provider must be skipped")

    monkeypatch.setattr(binance_public, "get_ticker", should_not_run)
    chain = FallbackChainProvider([FailingProvider(), binance_public])

    with pytest.raises(RuntimeError, match="bybit unavailable"):
        await chain.get_ticker("BTC/USDT")
    assert called is False


def test_perpetual_chain_never_reintroduces_spot_fallback_during_cooldown(monkeypatch):
    import aletheia.data.venue_health as health
    from aletheia.data.symbols import InstrumentId, MARKET_PERP

    monkeypatch.setattr(health, "emit_infra_event", lambda **_kwargs: None)
    record_venue_failure("binance", RuntimeError("418 IP banned"))
    ccxt_provider = object()
    chain = FallbackChainProvider([ccxt_provider, BinancePublicProvider()])

    instrument = InstrumentId.from_parts("bybit", "BTC/USDT", MARKET_PERP)

    assert chain._eligible_providers(instrument) == [ccxt_provider]


def test_doctor_reports_active_market_data_venue_cooldown(monkeypatch):
    import aletheia.data.venue_health as health
    from aletheia.diagnostics import doctor

    monkeypatch.setattr(health, "emit_infra_event", lambda **_kwargs: None)
    record_venue_failure("binance", RuntimeError("418 IP banned"))
    report = doctor.Report()

    doctor._probe_market_data_venue_health(report)

    assert report.checks[0].name == "data.market_venue_health"
    assert report.checks[0].severity == "warn"
