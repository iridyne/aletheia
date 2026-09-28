"""HTTP page limits must be finite and positive at the route boundary.

A negative value reaches SQLite as `LIMIT -1` (no limit) on the routes that bind
the parameter directly, and zero silently becomes one row on the routes whose
repository applies `LIMIT max(1, limit)`; neither is what the route advertises.
"""

import sqlite3

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from aletheia.server.routes import data, persistence

PAGED_PATHS = [
    "/api/persistence/gateway/sessions",
    "/api/persistence/gateway/reconciliation-reports",
    "/api/persistence/events",
    "/api/persistence/orders",
    "/api/persistence/lessons",
    "/api/persistence/skills",
    "/api/trades/export",
    "/api/serenity/claims",
    "/api/serenity/themes/example/claims",
]


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [-1, 0])
@pytest.mark.parametrize("path", PAGED_PATHS)
async def test_non_positive_page_limit_is_rejected(path, limit):
    app = FastAPI()
    app.include_router(persistence.router)
    app.include_router(data.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path, params={"limit": limit})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "limit"]


@pytest.mark.anyio
async def test_order_page_honors_the_declared_bound(monkeypatch):
    app = FastAPI()
    app.include_router(persistence.router)
    with sqlite3.connect(":memory:", check_same_thread=False) as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE ps_orders (id TEXT, mode TEXT, created_at TEXT)")
        db.executemany("INSERT INTO ps_orders VALUES (?, 'paper', ?)", [
            (str(i), f"2026-09-28T00:{i // 60:02}:{i % 60:02}Z") for i in range(201)
        ])
        monkeypatch.setattr("aletheia.storage.sqlite.get_db", lambda: db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            page = await client.get("/api/persistence/orders?limit=200")
            oversized = await client.get("/api/persistence/orders?limit=201")
        assert [row["id"] for row in page.json()] == [str(i) for i in range(200, 0, -1)]
        assert oversized.status_code == 422
    db.close()
