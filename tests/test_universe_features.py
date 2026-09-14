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

        async def get_taker_buy_ratio(self, symbol):
            return 1.25

        async def close(self):
            self.closed = True

    async def fetch_single(exchange, asset, *_args, **_kwargs):
        reader = _kwargs["taker_reader"]
        if asset["market_type"] == "perp":
            assert reader.__self__.exchange is exchange
        else:
            assert reader is None
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
@pytest.mark.parametrize("venue", ["okx", "bitget"])
async def test_live_funding_override_requires_finite_rate(
    monkeypatch, live_rate, fallback_rate, source_age, expected, expected_oi, venue,
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
        id = venue
        markets = {'BTC/USDT:USDT': dict(id='BTC-USDT-SWAP',symbol='BTC/USDT:USDT',swap=True,base='BTC',quote='USDT',settle='USDT',linear=True)}

        async def fetch_ohlcv(self, *args, **kwargs):
            return [[int((now - timedelta(days=1)).timestamp() * 1000), 100, 101, 99, 100, 1000]]

        async def fetch_funding_rate(self, *args, **kwargs):
            return {
                "fundingRate": live_rate,
                "timestamp": int(
                    (now - timedelta(minutes=source_age)).timestamp() * 1000
                ) if venue != "bitget" else None,
            }

        async def fetch_open_interest(self, *args, **kwargs):
            return {
                "openInterestValue": 123.0,
                "info": {"oiUsd":123},
                "timestamp": int(
                    (now - timedelta(minutes=source_age)).timestamp() * 1000
                ),
            }

    feature = await _fetch_single_feature(
        Exchange(),
        {
            "id": "asset-btc",
            "symbol": "BTC/USDT",
            "venue": venue,
            "market_type": "perp",
        },
        {
            "quoteVolume": 1_000_000, "bid": 100, "ask": 101,
            "timestamp": int((now - timedelta(minutes=source_age)).timestamp() * 1000),
            "info": {"fundingRate": live_rate},
        },
        now,
        lambda symbol: f"{symbol}:USDT",
    )

    assert feature is not None
    assert feature["funding_rate"] == expected
    assert feature["open_interest"] == (expected_oi if venue == "okx" else None)


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
    receipt_times = iter([scan_started_at, funding_received_at, oi_received_at, assembled_at])

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
        markets = {"BTC/USDT:USDT":dict(id="BTC-USDT-SWAP",symbol="BTC/USDT:USDT",swap=True,base="BTC",quote="USDT",settle="USDT",linear=True)}

        async def fetch_ohlcv(self, *args, **kwargs):
            return [[int((scan_started_at - timedelta(days=1)).timestamp() * 1000), 100, 101, 99, 100, 1000]]

        async def fetch_funding_rate(self, *args, **kwargs):
            return {
                "fundingRate": -0.0002,
                "timestamp": int(funding_received_at.timestamp() * 1000),
            }

        async def fetch_open_interest(self, *args, **kwargs):
            return {
                "openInterestValue": 123.0,
                "info": {"oiUsd":123},
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


@pytest.mark.asyncio
async def test_discovery_books_are_exact_durable_and_reused(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env
    from aletheia.universe import features
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    setup_env(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)

    class Exchange:
        id = "okx"
        calls = []
        def market(self, symbol):
            perp = ":" in symbol
            return {"symbol": symbol, "contract": perp, "spot": not perp, "contractSize": 2}
        async def fetch_order_book(self, symbol, limit):
            self.calls.append(symbol)
            return {"symbol": symbol, "bids": [[99.99, 100]], "asks": [[100.01, 100]]}
        async def fetch_ohlcv(self, symbol, timeframe, limit):
            interval = {"15m": 900000, "1h": 3600000, "4h": 14400000}[timeframe]
            end = int(now.timestamp() * 1000) // interval * interval
            return [[end - i * interval, 100, 101, 99, 100, 1000] for i in range(limit - 1, -1, -1)]

    exchange = Exchange()
    for market in ("spot", "perp"):
        asset = {"id": market, "symbol": "ETH/USDT", "venue": "okx", "market_type": market}
        get_db().execute("INSERT INTO universe_registry (id, symbol, venue, market_type) VALUES (?, ?, ?, ?)",
                         (market, "ETH/USDT", "okx", market))
        get_db().commit()
        assert features._load_fresh_enrichment_rows(asset, now=now)[0] is None
        feature = await features._fetch_single_feature(exchange, asset, {"quoteVolume": 1000000}, now, lambda s: f"{s}:USDT")
        assert feature["depth_10bps_usd"] == (40000 if market == "perp" else 20000)
        row = repo.latest_orderbook("ETH/USDT", exchange="okx", market_type=market)
        assert row["total_depth_10bps_usd"] == feature["depth_10bps_usd"]
        from aletheia.trading.entry_analysis import entry_causal_windows
        cutoff = datetime.now(timezone.utc)
        windows = {tf: repo.get_ohlcv_candles("ETH/USDT", tf, n, "okx", market, include_provenance=True)
                   for tf, n in (("15m", 96), ("1h", 48), ("4h", 30))}
        admitted, *_ = entry_causal_windows("okx", "ETH/USDT", market, "long", windows, cutoff)
        assert all(window["availability"] == "available" for window in admitted.values())
        repo.execute_write("UPDATE market_ohlcv SET first_persisted_at=NULL, last_mutated_at=NULL WHERE exchange=? AND symbol=? AND market_type=?",
                           ("okx", "ETH/USDT", market))
        repeated = await features._fetch_single_feature(exchange, asset, {"quoteVolume": 2000000}, now, lambda s: f"{s}:USDT")
        assert repeated["feature_snapshot_id"] == feature["feature_snapshot_id"]
        recovered = repo.get_ohlcv_candles("ETH/USDT", "15m", 96, "okx", market, include_provenance=True)
        admitted, *_ = entry_causal_windows("okx", "ETH/USDT", market, "long", {"15m": recovered}, datetime.now(timezone.utc))
        assert admitted["15m"]["availability"] == "available"
        assert all(datetime.fromisoformat(bar["first_persisted_at"]) >= cutoff for bar in recovered)
        current_id = features.persist_feature_snapshot_for_emission(repeated)
        persisted = get_db().execute("SELECT * FROM universe_feature_snapshots WHERE id=?", (current_id,)).fetchone()
        assert current_id != feature["feature_snapshot_id"]
        assert persisted["quote_volume_24h"] == repeated["quote_volume_24h"] == 2000000
        assert persisted["ts"] == repeated["feature_ts"] == repeated["feature_snapshot_ts"]
    assert exchange.calls == ["ETH/USDT", "ETH/USDT:USDT"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ticker", "ohlcv", "low_volume"])
async def test_collection_failure_is_not_a_successful_empty_population(monkeypatch, failure):
    from aletheia.universe import features
    from aletheia.data import ccxt_provider
    from aletheia.config.schema import AletheiaConfig
    cfg = AletheiaConfig()
    cfg.universe.min_volume_24h_usdt = 1000
    class Exchange:
        id = cfg.market_data.ccxt_exchange
        options = {}
        markets = {"ETH/USDT": {"symbol": "ETH/USDT", "spot": True}}
        async def load_markets(self):
            return {}
        async def fetch_tickers(self, symbols):
            if failure == "ticker":
                raise OSError("provider down")
            return {s: {"quoteVolume": 1 if failure == "low_volume" else 1000000} for s in symbols}
        async def fetch_ticker(self, symbol):
            raise OSError("provider down")
        async def fetch_ohlcv(self, *args, **kwargs):
            raise OSError("history down")
    closed = []
    class Provider:
        def __init__(self, **kwargs):
            pass
        def _ensure_primary(self, *args):
            return Exchange()
        async def close(self):
            closed.append(True)
    monkeypatch.setattr(ccxt_provider, "CCXTProvider", Provider)
    diagnostics = {}
    call = features.collect_features([{
        "id": "asset", "symbol": "ETH/USDT", "market_type": "spot", "venue": cfg.market_data.ccxt_exchange,
    }], cfg, diagnostics_out=diagnostics)
    if failure == "low_volume":
        assert await call == []
        assert diagnostics["ticker_observed"] == 1
        assert diagnostics["prefilter_passed"] == 0
    else:
        with pytest.raises(RuntimeError, match="universe_feature_collection_unavailable"):
            await call
        assert diagnostics["reason_counts"]["ticker_unavailable" if failure == "ticker" else "ohlcv_unavailable"] == 1
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["venue", "market", "crossed", "nan", "empty", "persist", "cancel"])
async def test_discovery_rejects_unavailable_books_without_persisting(tmp_path, monkeypatch, failure):
    from tests.test_entry_logic import setup_env
    from aletheia.universe import features
    from aletheia.storage import repositories as repo
    setup_env(tmp_path, monkeypatch)
    class Exchange:
        id = "binance" if failure == "venue" else "okx"
        def market(self, symbol):
            return {"symbol": symbol, "spot": failure != "market", "contract": failure == "market", "contractSize": 1}
        async def fetch_order_book(self, symbol, limit):
            if failure == "cancel":
                raise asyncio.CancelledError
            return {"symbol": symbol, "bids": [] if failure == "empty" else [[101 if failure == "crossed" else 99.99, float("nan") if failure == "nan" else 1]], "asks": [[100.01, 1]]}
    if failure == "persist":
        def unavailable(data):
            raise RuntimeError("writer unavailable")
        monkeypatch.setattr(repo, "insert_orderbook_snapshot", unavailable)
    call = features._fetch_feature_orderbook(Exchange(), {"venue": "okx", "symbol": "ETH/USDT", "market_type": "spot"}, "ETH/USDT")
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        assert await call is None
    assert repo.latest_orderbook("ETH/USDT", exchange="okx", market_type="spot") is None
