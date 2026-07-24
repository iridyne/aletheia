"""Regression tests for GET /api/analytics/learning-contribution.

Issue #322: The route queried a non-existent `classification` column on
`learning_contribution_verdicts` and silently converted the SQLite error
into total=0 via `except Exception: pass`.

Assertions:
- Persisted verdict counts are returned by stored `verdict` value.
- A schema/read failure cannot produce total=0 and exposes only a stable code.
- GET causes no Learning table mutation.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from types import SimpleNamespace


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import sqlite3
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _insert_verdict(verdict_value: str, verdict_id: str | None = None) -> None:
    """Insert a verdict row directly, bypassing FK chain (SQLite doesn't enforce by default)."""
    from aletheia.storage.sqlite import get_db
    import uuid

    db = get_db()
    vid = verdict_id or str(uuid.uuid4())
    db.execute(
        "INSERT INTO learning_contribution_verdicts "
        "(verdict_id, experiment_id, hypothesis_id, window_start, window_end, "
        " unique_treatment_n, unique_control_n, verdict, reason, created_at) "
        "VALUES (?, 'exp-test', 'hyp-test', '2026-01-01', '2026-01-31', 5, 5, ?, 'test', datetime('now'))",
        (vid, verdict_value),
    )
    db.commit()


@pytest.mark.anyio
async def test_verdicts_by_verdict_returned(tmp_path, monkeypatch):
    """Real verdict rows must appear in by_verdict keyed by the stored verdict value."""
    _setup_env(tmp_path, monkeypatch)
    _insert_verdict("invalid", "exp-invalid")
    _insert_verdict("insufficient_data", "exp-insufficient")

    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/analytics/learning-contribution")

    assert resp.status_code == 200
    body = resp.json()
    verdicts = body["verdicts"]
    assert verdicts["total"] == 2, f"expected 2 verdicts, got {verdicts['total']}"
    by_verdict = {row["verdict"]: row["n"] for row in verdicts["by_verdict"]}
    assert by_verdict.get("invalid") == 1
    assert by_verdict.get("insufficient_data") == 1
    assert "by_classification" not in verdicts, "stale key must not appear"


@pytest.mark.anyio
async def test_db_error_is_not_silently_zero(tmp_path, monkeypatch):
    """A schema/read failure on the verdicts query must surface in the response, not return total=0."""
    _setup_env(tmp_path, monkeypatch)

    import aletheia.storage.sqlite as sq
    real_get_db = sq.get_db

    class _FailingConn:
        def execute(self, sql, *args, **kwargs):
            if "learning_contribution_verdicts" in sql:
                raise Exception("simulated schema error")
            return real_get_db().execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_get_db(), name)

    monkeypatch.setattr(sq, "get_db", lambda: _FailingConn())

    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app

    # The route must not turn DB errors into zero. It exposes a stable code rather
    # than returning raw exception text or local paths.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/analytics/learning-contribution")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdicts"]["total"] is None
    assert body["verdicts"]["query_error"] == "read_failed"
    assert "simulated schema error" not in str(body)


@pytest.mark.anyio
async def test_legacy_job_health_does_not_leak_raw_errors(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import aletheia.learning.control as control
    import aletheia.learning.read_models as read_models

    raw = "/private/local/path: sqlite OperationalError"
    monkeypatch.setattr(
        control,
        "get_learning_job_health",
        lambda _job_id: SimpleNamespace(model_dump=lambda: {
            "status": "degraded",
            "last_error": raw,
            "last_failure_reason": raw,
        }),
    )
    monkeypatch.setattr(
        read_models,
        "build_learning_contribution_projection",
        lambda: read_models.unavailable_learning_contribution_projection(
            "projection_read_failed"
        ),
    )

    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/analytics/learning-contribution")

    assert response.status_code == 200
    body = response.json()
    assert raw not in str(body)
    assert all(
        "last_error" not in health and "last_failure_reason" not in health
        for health in body["job_health"].values()
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"
