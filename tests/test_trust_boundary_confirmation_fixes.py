"""Persistence path boundaries and real command confirmation remain enforced."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    """Isolate config and database to tmp_path."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "backups").mkdir(parents=True, exist_ok=True)
    (tmp_path / "exports").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


# ---------------------------------------------------------------------------
# #66 — Persistence import/verify path traversal
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_persistence_verify_rejects_path_outside_managed_roots(tmp_path, monkeypatch):
    """#66: /backups/verify used to Path(caller).exists() then read whatever the caller
    named. Now the resolve() must land inside `data_dir/backups` or `data_dir/exports`,
    so the /etc/passwd-style probe returns 400 rather than 200/500."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.server.app import app

    hostile = tmp_path.parent / "hostile.tar.gz"
    hostile.write_bytes(b"x" * 32)  # exists on disk, but outside managed roots

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/api/persistence/backups/verify?path={hostile}")
    assert r.status_code == 400
    assert r.json()["detail"] == "archive_path_outside_managed_roots"


@pytest.mark.anyio
async def test_persistence_import_rejects_dotdot_traversal(tmp_path, monkeypatch):
    """#66: '..' segments in the caller path are rejected before any filesystem I/O."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/persistence/import",
            json={"path": "backups/../../etc/passwd", "dry_run": True},
        )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_archive_path"


@pytest.mark.anyio
async def test_persistence_verify_accepts_archive_inside_backups_root(tmp_path, monkeypatch):
    """#66: legitimate archives inside data_dir/backups are still accepted (400 would be
    over-blocking). Whether verification passes is out of scope — we're asserting the
    guard doesn't reject a well-placed archive."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.server.app import app

    archive = tmp_path / "backups" / "backup_test.tar.gz"
    archive.write_bytes(b"not-a-real-archive")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/api/persistence/backups/verify?path={archive}")
    # 200 or 500 is fine (verify_backup itself will complain about the format); the
    # guard-under-test is that we don't return 400 out-of-root.
    assert r.status_code != 400 or r.json().get("detail") != "archive_path_outside_managed_roots"


# ---------------------------------------------------------------------------
# #67 — Executor confirmation gate is real, not metadata
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_execute_typed_command_gates_pause_agents_without_confirm(tmp_path, monkeypatch):
    """#67: pause_agents used to mutate runtime.paused on the first call while the
    response merely labelled itself needs_confirmation=True. Now the first call must
    be non-executing — the paused flag stays False."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.executor import execute_typed_command
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "PaperAuto"
    cfg.runtime.paused = False
    save_config(cfg)

    result = await execute_typed_command(
        "pause_agents",
        parameters={},  # no confirm
        permission_scope="operator",
        session_id="test-session",
        user_input="pause agents",
    )
    assert result.permitted is True
    assert result.needs_confirmation is True
    # Handler must NOT have run — runtime.paused stays False.
    assert load_config().runtime.paused is False
    assert "confirm=true" in result.response.lower() or "confirmation" in result.response.lower()


@pytest.mark.anyio
async def test_execute_typed_command_runs_pause_agents_with_confirm_true(tmp_path, monkeypatch):
    """#67: the two-call flow works — confirm=True executes the handler."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.executor import execute_typed_command
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "PaperAuto"
    cfg.runtime.paused = False
    save_config(cfg)

    result = await execute_typed_command(
        "pause_agents",
        parameters={"confirm": True},
        permission_scope="operator",
        session_id="test-session-confirm",
        user_input="pause agents",
    )
    assert result.permitted is True
    # With confirm=true, the handler ran and the state actually changed.
    assert load_config().runtime.paused is True
