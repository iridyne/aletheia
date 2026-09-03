"""Focused regressions for Universe feature collection."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


class _Exchange:
    def __init__(self, markets):
        self.markets = markets


@pytest.mark.asyncio
async def test_mixed_market_collection_binds_each_fetch_to_physical_market(
    monkeypatch,
):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data import ccxt_provider
    from aletheia.universe import features

    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.market_data.ccxt_fallback = "okx"
    cfg.universe.min_volume_24h_usdt = 0
    assets = [
        {
            "id": "spot-eth",
            "symbol": "ETH/USDT",
            "venue": "bitget",
            "market_type": "spot",
        },
        {
            "id": "perp-sol",
            "symbol": "SOL/USDT",
            "venue": "bitget",
            "market_type": "perp",
        },
    ]
    calls = []
    barrier = asyncio.Event()
    arrivals = 0

    class Exchange:
        id = "bitget"

        def __init__(self):
            self.options = {}
            self.markets = {
                "BTC/USDT": {},
                "ETH/USDT": {},
                "SOL/USDT:USDT": {"contract": True, "contractSize": 1},
            }

        async def load_markets(self):
            return self.markets

        async def fetch_tickers(self, symbols):
            calls.append(("tickers", tuple(symbols), self.options.get("defaultType")))
            return {
                symbol: {"last": 100.0, "quoteVolume": 1_000_000.0}
                for symbol in symbols
            }

        async def fetch_ohlcv(self, symbol, timeframe, limit):
            nonlocal arrivals
            if timeframe == "feature":
                arrivals += 1
                if arrivals == 2:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), timeout=1)
                await asyncio.sleep(0)
            mode = self.options.get("defaultType")
            calls.append(("ohlcv", symbol, timeframe, mode))
            if timeframe == "1h":
                return [[index, 1, 1, 1, 100 + index, 1] for index in range(limit)]
            return [{"physical_market_type": mode}]

    class Provider:
        instances = []

        def __init__(self, *, exchange, fallback_exchange):
            assert (exchange, fallback_exchange) == ("bitget", "okx")
            self.exchange = Exchange()
            self.closed = False
            self.instances.append(self)

        def _ensure_primary(self, _market_type=None):
            return self.exchange

        async def close(self):
            self.closed = True

    async def fetch_single(exchange, asset, *_args, **_kwargs):
        symbol = (
            f"{asset['symbol']}:USDT"
            if asset["market_type"] == "perp"
            else asset["symbol"]
        )
        physical = await exchange.fetch_ohlcv(symbol, "feature", 1)
        return {
            "asset_id": asset["id"],
            "market_type": asset["market_type"],
            "physical_market_type": physical[0]["physical_market_type"],
        }

    monkeypatch.setattr(ccxt_provider, "CCXTProvider", Provider)
    monkeypatch.setattr(features, "_fetch_single_feature", fetch_single)
    monkeypatch.setattr(
        features,
        "instrument_background_from_metadata",
        lambda _value: {"asset_class": "crypto", "product_kind": "crypto_spot"},
    )

    collected = await features.collect_features(assets, cfg)

    assert len(Provider.instances) == 2
    assert all(provider.closed for provider in Provider.instances)
    assert {row["market_type"]: row["physical_market_type"] for row in collected} == {
        "spot": "spot",
        "perp": "swap",
    }
    assert ("ohlcv", "BTC/USDT", "1h", "spot") in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_market", ["spot", "perp"])
async def test_market_bootstrap_failure_does_not_abort_other_market_features(
    monkeypatch,
    failed_market,
):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data import ccxt_provider
    from aletheia.universe import features

    cfg = AletheiaConfig()
    cfg.universe.min_volume_24h_usdt = 0
    assets = [
        {"id": "spot-eth", "symbol": "ETH/USDT", "market_type": "spot"},
        {"id": "perp-sol", "symbol": "SOL/USDT", "market_type": "perp"},
    ]
    fetched_assets = []

    class Exchange:
        def __init__(self, *, fail_load=False):
            self.fail_load = fail_load
            self.options = {}
            self.markets = {"ETH/USDT": {}, "SOL/USDT:USDT": {"contract": True}}

        async def load_markets(self):
            if self.fail_load:
                raise RuntimeError("market metadata unavailable")
            return self.markets

        async def fetch_tickers(self, symbols):
            return {
                symbol: {"last": 100.0, "quoteVolume": 1_000_000.0}
                for symbol in symbols
            }

        async def fetch_ohlcv(self, _symbol, _timeframe, limit):
            return [[index, 1, 1, 1, 100 + index, 1] for index in range(limit)]

    class Provider:
        instances = []

        def __init__(self, **_kwargs):
            market = "spot" if not self.instances else "perp"
            self.exchange = Exchange(fail_load=market == failed_market)
            self.closed = False
            self.instances.append(self)

        def _ensure_primary(self, _market_type=None):
            return self.exchange

        async def close(self):
            self.closed = True

    async def fetch_single(_exchange, asset, *_args, **_kwargs):
        fetched_assets.append(asset["id"])
        return {"asset_id": asset["id"], "market_type": asset["market_type"]}

    monkeypatch.setattr(ccxt_provider, "CCXTProvider", Provider)
    monkeypatch.setattr(features, "_fetch_single_feature", fetch_single)
    monkeypatch.setattr(
        features,
        "instrument_background_from_metadata",
        lambda _value: {"asset_class": "crypto", "product_kind": "crypto_spot"},
    )

    collected = await features.collect_features(assets, cfg)

    expected_asset = "perp-sol" if failed_market == "spot" else "spot-eth"
    assert fetched_assets == [expected_asset]
    assert [row["asset_id"] for row in collected] == [expected_asset]
    assert len(Provider.instances) == 2
    assert all(provider.closed for provider in Provider.instances)


def test_quote_volume_prefers_exchange_quote_volume():
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({"BTC/USDT": {"spot": True}})
    ticker = {"quoteVolume": 123_456.0, "baseVolume": 999.0, "last": 100.0}

    assert _quote_volume_24h(exchange, "BTC/USDT", ticker) == 123_456.0


def test_quote_volume_converts_okx_contract_volume_to_quote_currency():
    """OKX swaps expose contracts in baseVolume and leave quoteVolume empty."""
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({
        "BTC/USDT:USDT": {
            "contract": True,
            "contractSize": 0.01,
        },
    })
    ticker = {
        "quoteVolume": None,
        "baseVolume": 9_000_000.0,
        "last": 60_000.0,
    }

    assert _quote_volume_24h(exchange, "BTC/USDT:USDT", ticker) == 5_400_000_000.0


def test_quote_volume_converts_spot_base_volume_when_quote_is_missing():
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({"ETH/USDT": {"spot": True}})
    ticker = {"quoteVolume": None, "baseVolume": 2_000.0, "last": 3_000.0}

    assert _quote_volume_24h(exchange, "ETH/USDT", ticker) == 6_000_000.0


def test_quote_volume_returns_zero_when_it_cannot_be_derived():
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({"BAD/USDT:USDT": {"contract": True}})
    ticker = {"quoteVolume": None, "baseVolume": 100.0, "last": None}

    assert _quote_volume_24h(exchange, "BAD/USDT:USDT", ticker) == 0.0


def test_quote_volume_rejects_nonfinite_derived_total():
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({"BTC/USDT": {"spot": True}})
    ticker = {"quoteVolume": None, "baseVolume": 1e308, "last": 1e308}

    assert _quote_volume_24h(exchange, "BTC/USDT", ticker) is None


@pytest.mark.parametrize("invalid_volume", [float("nan"), float("inf"), -1.0, "", True])
def test_quote_volume_rejects_explicit_invalid_direct_volume(invalid_volume):
    from aletheia.universe.features import _quote_volume_24h

    exchange = _Exchange({"BTC/USDT": {"spot": True}})
    ticker = {
        "quoteVolume": invalid_volume,
        "baseVolume": 100.0,
        "last": 100.0,
    }

    assert _quote_volume_24h(exchange, "BTC/USDT", ticker) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": float("nan")},
        {"price": float("inf")},
        {"price": 0.0},
        {"price": True},
        {"volume_24h": float("nan")},
        {"volume_24h": False},
        {"high_24h": 90.0, "low_24h": 100.0},
    ],
)
def test_market_snapshot_rejects_invalid_numeric_facts(overrides):
    from pydantic import ValidationError
    from aletheia.storage.models import MarketSnapshot

    with pytest.raises(ValidationError):
        payload = {"symbol": "BTC/USDT", "market_type": "spot", "price": 100.0}
        payload.update(overrides)
        MarketSnapshot(**payload)


def test_provider_observed_at_requires_source_timestamp():
    from aletheia.universe.features import _provider_observed_at

    source = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    received = datetime(2026, 8, 26, 12, 15, tzinfo=timezone.utc)

    assert _provider_observed_at(
        {"timestamp": int(source.timestamp() * 1000)},
        received_at=received,
    ) == source.isoformat()
    assert _provider_observed_at(
        {"datetime": "2026-08-26T12:00:00Z"},
        received_at=received,
    ) == "2026-08-26T12:00:00+00:00"
    assert _provider_observed_at({}, received_at=received) is None


def test_ticker_prefilters_keep_complete_sequential_candidate_facts():
    from aletheia.universe.features import _evaluate_ticker_prefilters

    assets = [
        {"id": "btc", "symbol": "BTC/USDT", "venue": "okx", "market_type": "spot"},
        {"id": "eth", "symbol": "ETH/USDT", "venue": "okx", "market_type": "perp"},
        {"id": "sol", "symbol": "SOL/USDT", "venue": "okx", "market_type": "spot"},
        {"id": "low", "symbol": "LOW/USDT", "venue": "okx", "market_type": "spot"},
    ]
    exchange = _Exchange({})
    tickers = {
        "BTC/USDT": {"quoteVolume": 20_000_000.0},
        "ETH/USDT:USDT": {"quoteVolume": 15_000_000.0},
        "LOW/USDT": {"quoteVolume": 100.0},
    }

    candidates, ticker_facts, volume_facts = _evaluate_ticker_prefilters(
        assets,
        tickers,
        exchange,
        lambda symbol: f"{symbol}:USDT",
        min_volume=1_000_000.0,
    )

    assert [asset["id"] for asset, _ticker in candidates] == ["btc", "eth"]
    assert [fact["decision"] for fact in ticker_facts] == [
        "passed", "passed", "rejected", "passed",
    ]
    assert [fact["decision"] for fact in volume_facts] == [
        "passed", "passed", "not_applicable", "rejected",
    ]
    assert volume_facts[2]["reason_code"] == "ticker_prerequisite_failed"


def test_pending_candidate_bypasses_only_volume_prefilter():
    from aletheia.universe.features import _evaluate_ticker_prefilters

    asset = {
        "id": "pending-low-volume",
        "symbol": "LOW/USDT",
        "venue": "okx",
        "market_type": "spot",
    }
    candidates, ticker_facts, volume_facts = _evaluate_ticker_prefilters(
        [asset],
        {"LOW/USDT": {"quoteVolume": 100.0}},
        _Exchange({}),
        lambda symbol: symbol,
        min_volume=1_000_000.0,
        priority_asset_ids={"pending-low-volume"},
    )

    assert [row[0]["id"] for row in candidates] == ["pending-low-volume"]
    assert ticker_facts[0]["decision"] == "passed"
    assert volume_facts[0]["reason_code"] == (
        "pending_candidate_volume_prefilter_bypassed"
    )


def test_spot_feature_enrichment_does_not_read_derivatives(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.universe.features import _load_fresh_enrichment_rows

    monkeypatch.setattr(repo, "latest_orderbook", lambda *args, **kwargs: None)

    def unexpected(*args, **kwargs):
        raise AssertionError("spot enrichment must not query derivatives")

    monkeypatch.setattr(repo, "latest_funding_rate", unexpected)
    monkeypatch.setattr(repo, "latest_open_interest", unexpected)

    ob, funding, oi = _load_fresh_enrichment_rows(
        {"symbol": "BTC/USDT", "venue": "okx", "market_type": "spot"},
        now=datetime.now(timezone.utc),
    )

    assert ob is None
    assert funding is None
    assert oi is None


def test_only_explicit_derivative_market_types_read_derivative_enrichment(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.universe.features import _load_fresh_enrichment_rows

    calls = []
    monkeypatch.setattr(repo, "latest_orderbook", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        repo, "latest_funding_rate", lambda *args, **kwargs: calls.append("funding")
    )
    monkeypatch.setattr(
        repo, "latest_open_interest", lambda *args, **kwargs: calls.append("oi")
    )

    for market_type in ("", "margin", "option", "unknown"):
        _load_fresh_enrichment_rows(
            {"symbol": "BTC/USDT", "venue": "okx", "market_type": market_type},
            now=datetime.now(timezone.utc),
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("live_rate", "fallback_rate", "source_age", "expected", "expected_oi"),
    [
        ("NaN", 0.0001, 0, 0.0001, 123.0),
        ("Infinity", None, 0, None, 123.0),
        ("-Infinity", -0.0001, 0, -0.0001, 123.0),
        (True, 0.0001, 0, 0.0001, 123.0),
        (False, None, 0, None, 123.0),
        (-0.0002, 0.0001, 0, -0.0002, 123.0),
        (-0.0002, 0.0001, 16, 0.0001, None),
    ],
)
async def test_live_funding_override_requires_finite_rate(
    monkeypatch, live_rate, fallback_rate, source_age, expected, expected_oi,
):
    from aletheia.storage import repositories as repo
    from aletheia.universe.features import _fetch_single_feature

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(repo, "latest_orderbook", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        repo,
        "latest_funding_rate",
        lambda *args, **kwargs: (
            {
                "funding_rate": fallback_rate,
                "observed_at": now.isoformat(),
            }
            if fallback_rate is not None
            else None
        ),
    )
    monkeypatch.setattr(repo, "latest_open_interest", lambda *args, **kwargs: None)
    monkeypatch.setattr(repo, "get_latest_feature_snapshot", lambda *args: None)

    class Exchange:
        id = "okx"

        async def fetch_ohlcv(self, *args, **kwargs):
            return []

        async def fetch_funding_rate(self, *args, **kwargs):
            return {
                "fundingRate": live_rate,
                "timestamp": int(
                    (now - timedelta(minutes=source_age)).timestamp() * 1000
                ),
            }

        async def fetch_open_interest(self, *args, **kwargs):
            return {
                "openInterestValue": 123.0,
                "timestamp": int(
                    (now - timedelta(minutes=source_age)).timestamp() * 1000
                ),
            }

    feature = await _fetch_single_feature(
        Exchange(),
        {
            "id": "asset-btc",
            "symbol": "BTC/USDT",
            "venue": "okx",
            "market_type": "perp",
        },
        {"quoteVolume": 1_000_000, "bid": 100, "ask": 101},
        now,
        lambda symbol: f"{symbol}:USDT",
    )

    assert feature is not None
    assert feature["funding_rate"] == expected
    assert feature["open_interest"] == expected_oi


@pytest.mark.asyncio
@pytest.mark.parametrize("response_delay_minutes", [4, 6])
async def test_live_derivatives_use_response_receipt_and_snapshot_assembly_time(
    monkeypatch, response_delay_minutes,
):
    from aletheia.storage import repositories as repo
    from aletheia.universe import features

    scan_started_at = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
    funding_received_at = scan_started_at + timedelta(
        minutes=response_delay_minutes,
    )
    oi_received_at = funding_received_at + timedelta(minutes=1)
    assembled_at = oi_received_at + timedelta(minutes=1)
    receipt_times = iter([funding_received_at, oi_received_at, assembled_at])

    class ResponseClock(datetime):
        @classmethod
        def now(cls, tz=None):
            current = next(receipt_times)
            return current if tz is not None else current.replace(tzinfo=None)

    monkeypatch.setattr(features, "datetime", ResponseClock)
    monkeypatch.setattr(repo, "latest_orderbook", lambda *args, **kwargs: None)
    monkeypatch.setattr(repo, "latest_funding_rate", lambda *args, **kwargs: None)
    monkeypatch.setattr(repo, "latest_open_interest", lambda *args, **kwargs: None)
    monkeypatch.setattr(repo, "get_latest_feature_snapshot", lambda *args: None)

    class Exchange:
        id = "okx"

        async def fetch_ohlcv(self, *args, **kwargs):
            return []

        async def fetch_funding_rate(self, *args, **kwargs):
            return {
                "fundingRate": -0.0002,
                "timestamp": int(funding_received_at.timestamp() * 1000),
            }

        async def fetch_open_interest(self, *args, **kwargs):
            return {
                "openInterestValue": 123.0,
                "timestamp": int(oi_received_at.timestamp() * 1000),
            }

    snapshots = []
    feature = await features._fetch_single_feature(
        Exchange(),
        {
            "id": "asset-btc",
            "symbol": "BTC/USDT",
            "venue": "okx",
            "market_type": "perp",
        },
        {"quoteVolume": 1_000_000, "bid": 100, "ask": 101},
        scan_started_at,
        lambda symbol: f"{symbol}:USDT",
        snapshot_buffer=snapshots,
    )

    assert feature is not None
    assert feature["funding_rate"] == -0.0002
    assert feature["funding_observed_at"] == funding_received_at.isoformat()
    assert feature["open_interest"] == 123.0
    assert feature["feature_snapshot_ts"] == assembled_at.isoformat()
    assert [snapshot.ts for snapshot in snapshots] == [assembled_at.isoformat()]
