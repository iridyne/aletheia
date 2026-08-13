"""Focused contracts for the Markets Opportunity projections."""

import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.server.app import app
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Opportunity
from aletheia.storage.sqlite import reset_db


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(sq, "db_path", lambda: test_db)
    conn = sqlite3.connect(str(test_db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value

    reset_db()


def _seed_opportunity(*, oid: str, symbol: str, market: str, status: str, direction: str, created_at: str):
    repo.insert_opportunity(Opportunity(
        id=oid,
        symbol=symbol,
        market=market,
        status=status,
        direction_bias=direction,
        created_at=created_at,
    ))


@pytest.mark.anyio
async def test_market_opportunity_projections_keep_exact_identity_and_state(client):
    _seed_opportunity(
        oid="btc-spot-old", symbol="BTC/USDT", market="spot", status="reviewed",
        direction="short", created_at="2026-01-01T00:00:00+00:00",
    )
    _seed_opportunity(
        oid="btc-spot-new", symbol="BTC/USDT", market="spot", status="open",
        direction="long", created_at="2026-01-02T00:00:00+00:00",
    )
    _seed_opportunity(
        oid="btc-perp", symbol="BTC/USDT", market="perp", status="expired",
        direction="short", created_at="2026-01-03T00:00:00+00:00",
    )
    _seed_opportunity(
        oid="eth-perp", symbol="ETH/USDT", market="perp", status="open",
        direction="long", created_at="2026-01-04T00:00:00+00:00",
    )

    before = {row["id"]: row["status"] for row in repo.list_opportunities(limit=20)}

    instruments_response = await client.get("/api/market/opportunity-instruments")
    assert instruments_response.status_code == 200
    instruments = instruments_response.json()
    assert [(row["symbol"], row["market"]) for row in instruments] == [
        ("ETH/USDT", "perp"),
        ("BTC/USDT", "spot"),
        ("BTC/USDT", "perp"),
    ]
    btc_spot = next(row for row in instruments if row["symbol"] == "BTC/USDT" and row["market"] == "spot")
    assert btc_spot == {
        "symbol": "BTC/USDT",
        "market": "spot",
        "total_count": 2,
        "open_count": 1,
        "latest_created_at": "2026-01-02T00:00:00+00:00",
        "latest_status": "open",
        "latest_direction_bias": "long",
    }

    exact_response = await client.get(
        "/api/market/opportunities",
        params={"symbol": "BTC/USDT", "market_type": "spot", "limit": 1},
    )
    assert exact_response.status_code == 200
    assert [row["id"] for row in exact_response.json()] == ["btc-spot-new"]

    all_exact_response = await client.get(
        "/api/market/opportunities",
        params={"symbol": "BTC/USDT", "market_type": "spot"},
    )
    assert [row["id"] for row in all_exact_response.json()] == ["btc-spot-new", "btc-spot-old"]
    assert (await client.get("/api/market/opportunities?market_type=spot")).status_code == 422
    assert (await client.get("/api/market/opportunities?symbol=BTC/USDT")).status_code == 422
    assert (
        await client.get(
            "/api/market/opportunities?symbol=BTC/USDT&market_type=spot&limit=501"
        )
    ).status_code == 422

    after = {row["id"]: row["status"] for row in repo.list_opportunities(limit=20)}
    assert after == before
