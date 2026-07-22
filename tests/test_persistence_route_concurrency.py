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
