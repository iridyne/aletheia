"""Phase 5 tests: market/chart data routes."""

from datetime import datetime, timezone, timedelta

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import MarketSnapshot, PortfolioSnapshot
from aletheia.storage import repositories as repo


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(sq, "db_path", lambda: test_db)
    conn = sqlite3.connect(str(test_db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    from aletheia.data.cache import get_cache
    from aletheia.data.data_coverage import clear_data_coverage_cache
    get_cache().invalidate()
    clear_data_coverage_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    get_cache().invalidate()
    clear_data_coverage_cache()
    reset_db()


@pytest.mark.anyio
async def test_market_snapshots_route(client):
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000))
    resp = await client.get("/api/market/snapshots?symbol=BTCUSDT")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["price"] == 60000


@pytest.mark.anyio
async def test_portfolio_history_route(client):
    repo.insert_portfolio_snapshot(PortfolioSnapshot(total_value_usdt=1000, unrealized_pnl=50))
    resp = await client.get("/api/market/portfolio-history")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.anyio
async def test_portfolio_history_route_returns_latest_window_in_ascending_order(client):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(3):
        repo.insert_portfolio_snapshot(
            PortfolioSnapshot(
                total_value_usdt=1000 + i,
                snapshot_at=(base + timedelta(minutes=i)).isoformat(),
            )
        )

    resp = await client.get("/api/market/portfolio-history?limit=2")

    assert resp.status_code == 200
    data = resp.json()
    assert [row["total_value_usdt"] for row in data] == [1001, 1002]
    assert [row["snapshot_at"] for row in data] == sorted(row["snapshot_at"] for row in data)


@pytest.mark.anyio
async def test_collector_health_route_returns_durable_domain_state(client):
    from aletheia.data.collector_health import record_collector_status

    record_collector_status("microstructure", "success", tick=1, duration_ms=7)
    resp = await client.get("/api/market/collector-health")

    assert resp.status_code == 200
    body = resp.json()
    row = next(item for item in body["domains"] if item["domain"] == "microstructure")
    assert body["recorded_count"] == 1
    assert row["last_status"] == "success"


@pytest.mark.anyio
async def test_data_coverage_route_separates_persisted_data_from_job_health(client):
    resp = await client.get("/api/market/data-coverage")

    assert resp.status_code == 200
    body = resp.json()
    assert body["domain_count"] == 8
    assert {row["domain"] for row in body["domains"]} >= {
        "orderbook", "primary_funding", "primary_open_interest", "ohlcv",
    }
    liquidation = next(row for row in body["domains"] if row["domain"] == "liquidations")
    assert liquidation["state"] == "idle"


@pytest.mark.anyio
async def test_ohlcv_route_returns_candles_or_error(client, monkeypatch):
    from aletheia.data.providers import Candle
    from aletheia.data import ccxt_provider

    class _FakeCCXT:
        # signature mirrors the real CCXTProvider (since added for history paging)
        async def get_ohlcv(self, symbol, timeframe, limit, since=None):
            return [Candle(timestamp=1700000000000, open=1, high=2, low=0.5, close=1.5, volume=10)]
        async def get_ticker(self, symbol):
            from aletheia.data.providers import Ticker
            return Ticker(symbol=symbol, last_price=1.5, volume_24h=0, change_pct_24h=0)
        async def close(self):
            pass

    monkeypatch.setattr(ccxt_provider, "CCXTProvider", lambda **kw: _FakeCCXT())
    resp = await client.get("/api/market/ohlcv?symbol=BTC/USDT&timeframe=1h")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["time"] == 1700000000  # ms -> s
    assert data[0]["close"] == 1.5
    assert resp.headers["x-aletheia-data-source"] == "provider_fetch"


@pytest.mark.anyio
async def test_ohlcv_route_marks_memory_cache_source(client, monkeypatch):
    from aletheia.data.cache import get_cache
    from aletheia.data.providers import Candle

    get_cache().set(
        "BTC/USDT", "1h", 100,
        [Candle(timestamp=1700000000000, open=1, high=2, low=0.5, close=1.5, volume=10)],
        exchange="binance", market_type="spot",
    )
    resp = await client.get("/api/market/ohlcv?symbol=BTC/USDT&timeframe=1h")

    assert resp.status_code == 200
    assert resp.headers["x-aletheia-data-source"] == "memory_cache"
    assert resp.json()[0]["close"] == 1.5


@pytest.mark.anyio
async def test_ohlcv_route_falls_back_to_exact_database_history(client, monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.data.providers import Candle

    class _UnavailableCCXT:
        async def get_ohlcv(self, *args, **kwargs):
            raise ConnectionError("offline")

        async def close(self):
            pass

    repo.upsert_ohlcv_candles(
        symbol="BTC/USDT", timeframe="1h", exchange="binance", market_type="spot",
        candles=[Candle(
            timestamp=1700000000000, open=1, high=2, low=0.5, close=1.4, volume=10,
        )],
    )
    monkeypatch.setattr(ccxt_provider, "CCXTProvider", lambda **kw: _UnavailableCCXT())

    resp = await client.get(
        "/api/market/ohlcv?symbol=BTC/USDT&timeframe=1h&limit=99",
    )

    assert resp.status_code == 200
    assert resp.headers["x-aletheia-data-source"] == "database_fallback"
    assert resp.json() == [{
        "time": 1700000000, "open": 1.0, "high": 2.0, "low": 0.5,
        "close": 1.4, "volume": 10.0,
    }]


@pytest.mark.anyio
async def test_ohlcv_route_before_filters_to_history(client, monkeypatch):
    """`before` (unix seconds) must drop any candle at/after the cutoff so a
    scroll-back page never overlaps the chart's left-most bar."""
    from aletheia.data.providers import Candle
    from aletheia.data import ccxt_provider

    cutoff = 1700000000  # seconds
    captured = {}

    class _FakeCCXT:
        async def get_ohlcv(self, symbol, timeframe, limit, since=None):
            captured["since"] = since
            return [
                Candle(timestamp=(cutoff - 3600) * 1000, open=1, high=2, low=0.5, close=1.4, volume=10),  # older — kept
                Candle(timestamp=cutoff * 1000, open=1, high=2, low=0.5, close=1.5, volume=10),            # == cutoff — dropped
                Candle(timestamp=(cutoff + 3600) * 1000, open=1, high=2, low=0.5, close=1.6, volume=10),   # newer — dropped
            ]
        async def close(self):
            pass

    monkeypatch.setattr(ccxt_provider, "CCXTProvider", lambda **kw: _FakeCCXT())
    resp = await client.get(f"/api/market/ohlcv?symbol=BTC/USDT&timeframe=1h&limit=300&before={cutoff}")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # only the strictly-older candle survives the cutoff filter
    assert [c["time"] for c in data] == [cutoff - 3600]
    # the route asks the exchange for a window ending at the cutoff
    assert captured["since"] == cutoff * 1000 - 300 * 3_600_000


@pytest.mark.anyio
async def test_ohlcv_route_unsupported_timeframe_with_before(client):
    """A timeframe with no known duration can't form a `before` window — clean error."""
    resp = await client.get("/api/market/ohlcv?symbol=BTC/USDT&timeframe=3m&limit=10&before=1700000000")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("error", "").startswith("unsupported_timeframe")
