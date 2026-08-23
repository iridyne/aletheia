"""Concurrency regressions for persistence archive API routes (#265)."""

from __future__ import annotations

import asyncio
import inspect
import threading

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.parametrize(
    "handler_name",
    [
        "list_backups",
        "create_backup",
        "verify_backup_endpoint",
        "create_export",
        "import_bundle",
    ],
)
def test_archive_handlers_use_fastapi_threadpool(handler_name: str) -> None:
    """Blocking archive operations must remain synchronous FastAPI endpoints."""
    from aletheia.server.routes import persistence

    handler = getattr(persistence, handler_name)
    assert not inspect.iscoroutinefunction(handler)


@pytest.mark.anyio
async def test_backup_request_does_not_starve_event_loop(monkeypatch, tmp_path) -> None:
    """A slow backup must not freeze unrelated work on the API event loop."""
    from aletheia.backup.service import BackupService
    from aletheia.server.app import app

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_backup(self, label=None):
        started.set()
        released = release.wait(timeout=5.0)
        finished.set()
        if not released:
            raise AssertionError("test did not release the blocking backup")
        return tmp_path / "backup_test.tar.gz"

    monkeypatch.setattr(BackupService, "create_backup", blocking_backup)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        request = asyncio.create_task(
            client.post("/api/persistence/backups", json={"label": "test"})
        )
        try:
            observed_start = await asyncio.wait_for(
                asyncio.to_thread(started.wait, 2.0), timeout=2.5
            )
            assert observed_start
            assert not finished.is_set(), (
                "backup work blocked the event loop until completion"
            )
        finally:
            release.set()
            response = await asyncio.wait_for(request, timeout=2.0)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.anyio
async def test_slow_risk_route_does_not_starve_readyz(monkeypatch) -> None:
    """A slow config read on /api/risk/boundaries must not freeze /readyz (event-loop isolation)."""
    from aletheia.server.app import app
    import aletheia.server.routes.risk as risk_mod
    import aletheia.config.loader as loader_mod

    started = threading.Event()
    release = threading.Event()

    real_load = loader_mod.load_config

    def slow_load_config(*args, **kwargs):
        started.set()
        release.wait(timeout=5.0)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(risk_mod, "load_config", slow_load_config)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        slow_request = asyncio.create_task(client.get("/api/risk/boundaries"))
        try:
            # Wait until the blocking thread has started, then confirm readyz is still responsive.
            await asyncio.wait_for(asyncio.to_thread(started.wait, 2.0), timeout=2.5)
            readyz = await asyncio.wait_for(client.get("/_infra/readyz"), timeout=1.0)
            assert readyz.status_code in (200, 503), f"unexpected readyz status {readyz.status_code}"
        finally:
            release.set()
            await asyncio.wait_for(slow_request, timeout=2.0)
