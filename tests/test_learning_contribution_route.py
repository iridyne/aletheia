"""Current contribution cannot resurrect retired verdicts or hide unavailable reads."""

from __future__ import annotations

import pytest
from pathlib import Path


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
async def test_retired_verdicts_are_not_current_lab_counts(tmp_path, monkeypatch):
    """Historical verdict rows are not counted as current canonical Lab evidence."""
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
    assert verdicts["total"] is None
    assert verdicts["by_verdict"] == []
    assert body["availability"] == "unavailable"


@pytest.mark.anyio
async def test_unavailable_projection_preserves_configured_freshness_threshold(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.observation_lab.db import get_lab_db, reset_lab_db
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.learning.evaluation_interval_minutes = 180
    save_config(cfg)
    reset_lab_db()
    get_db().execute("UPDATE observation_authority_state SET authority_source='lab'")
    lab = get_lab_db()
    lab.execute("DROP TABLE observation_windows")
    lab.commit()

    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/api/analytics/learning-contribution")

    body = response.json()
    assert response.status_code == 200
    assert body["availability"] == "unavailable"
    assert body["data_freshness"]["stale_after_sec"] == 21_600
    assert body["verdicts"]["total"] is None
    assert "observation_windows" not in response.text


@pytest.fixture
def anyio_backend():
    return "asyncio"
