"""Phase 5 tests: market/chart data routes."""

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
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
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
async def test_ohlcv_route_returns_candles_or_error(client, monkeypatch):
    from aletheia.exchanges.bitget.client import Candle

    async def fake_ohlcv(self, symbol, timeframe="1h", limit=100, market_type="spot"):
        return [Candle(timestamp=1700000000000, open=1, high=2, low=0.5, close=1.5, volume=10)]

    monkeypatch.setattr("aletheia.exchanges.bitget.client.BitgetClient.get_ohlcv", fake_ohlcv)
    resp = await client.get("/api/market/ohlcv?symbol=BTCUSDT&timeframe=1h")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["time"] == 1700000000  # ms -> s
    assert data[0]["close"] == 1.5
