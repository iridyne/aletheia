"""Focused regressions for B7-trust batch (#66, #67, #82, #83).

Each test locks in a trust-boundary invariant that the batch established. The
production impact is spelled out in the ledger:

- #66: persistence /backups/verify + /import used to accept any local path.
- #67: agent_shell/executor.py + runtime.py `requires_confirmation` was metadata only —
       the handler ran, the confirmation was cosmetic.
- #82: runtime_continuity service write methods ignored `self.enabled` — an owner
       who kept the switch off in Settings could still be mutated via the API.
- #83: runtime routes accepted caller-supplied `actor` and required no `confirm` on
       promote / rollback / forced drain.

These tests exist so a regression of any of the four flips the suite from green
to red — do not weaken them without re-establishing the invariant.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


def _setup_env(tmp_path: Path, monkeypatch, *, runtime_continuity_enabled: bool = False) -> None:
    """Isolate config/db to tmp_path. Mirrors test_runtime_continuity.setup_env's shape."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "backups").mkdir(parents=True, exist_ok=True)
    (tmp_path / "exports").mkdir(parents=True, exist_ok=True)
    if runtime_continuity_enabled:
        (tmp_path / "config.yaml").write_text(
            "runtime:\n  runtime_continuity_enabled: true\n",
            encoding="utf-8",
        )
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


# ---------------------------------------------------------------------------
# #82 — runtime_continuity_enabled kill switch honoured on write paths
# ---------------------------------------------------------------------------


def test_runtime_continuity_write_methods_reject_when_disabled(tmp_path, monkeypatch):
    """#82: prepare_candidate/start_shadow/promote/drain/rollback/acquire_lock all raise
    RuntimeTransitionError('runtime_continuity_disabled:*') when enabled=False."""
    _setup_env(tmp_path, monkeypatch, runtime_continuity_enabled=False)
    from aletheia.runtime_continuity.service import (
        RuntimeContinuityService,
        RuntimeTransitionError,
    )

    svc = RuntimeContinuityService(enabled=False)

    with pytest.raises(RuntimeTransitionError, match="runtime_continuity_disabled:prepare_candidate"):
        svc.prepare_candidate(actor="test", cause="off-test", build_git_sha="deadbeef")

    with pytest.raises(RuntimeTransitionError, match="runtime_continuity_disabled:start_shadow"):
        svc.start_shadow("runtime-x", actor="test", cause="off-test")

    with pytest.raises(RuntimeTransitionError, match="runtime_continuity_disabled:promote"):
        svc.promote("runtime-x", actor="test", cause="off-test")

    with pytest.raises(RuntimeTransitionError, match="runtime_continuity_disabled:drain"):
        svc.drain("runtime-x", actor="test", cause="off-test")

    with pytest.raises(RuntimeTransitionError, match="runtime_continuity_disabled:rollback"):
        svc.rollback(actor="test", cause="off-test")

    with pytest.raises(RuntimeTransitionError, match="runtime_continuity_disabled:acquire_lock"):
        svc.acquire_lock("promote", runtime_id="runtime-x", owner="test")

    # Read paths remain available so the dashboard can render recorded state.
    status = svc.status()
    assert status["enabled"] is False


@pytest.mark.anyio
async def test_runtime_candidate_route_returns_409_when_disabled(tmp_path, monkeypatch):
    """#82: the operator route surfaces the kill switch as HTTP 409, not silent success."""
    _setup_env(tmp_path, monkeypatch, runtime_continuity_enabled=False)
    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/operator/runtime/candidate",
            json={"build_git_sha": "abc", "release_bundle_hash": "abc", "cause": "off-test"},
        )
    assert r.status_code == 409
    assert "runtime_continuity_disabled" in r.json()["detail"]


# ---------------------------------------------------------------------------
# #83 — Runtime routes: server-derived actor + confirm gate on state pivots
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_runtime_promote_requires_confirm(tmp_path, monkeypatch):
    """#83: promote is a current-runtime pivot; without confirm=true the route must 400
    before touching state. Mirrors update.update_continuity's confirm contract."""
    _setup_env(tmp_path, monkeypatch, runtime_continuity_enabled=True)
    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/operator/runtime/promote",
            json={"runtime_id": "runtime-x", "cause": "promote-without-confirm"},
        )
    assert r.status_code == 400
    assert r.json()["detail"] == "runtime_promote_requires_confirm"


@pytest.mark.anyio
async def test_runtime_rollback_requires_confirm(tmp_path, monkeypatch):
    """#83: rollback is a current-runtime pivot; same confirm contract as promote."""
    _setup_env(tmp_path, monkeypatch, runtime_continuity_enabled=True)
    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/operator/runtime/rollback",
            json={"cause": "rollback-without-confirm"},
        )
    assert r.status_code == 400
    assert r.json()["detail"] == "runtime_rollback_requires_confirm"


@pytest.mark.anyio
async def test_runtime_candidate_actor_is_server_derived_not_caller_supplied(tmp_path, monkeypatch):
    """#83: caller can no longer set `actor` on runtime routes; audit records
    server-derived 'human'. Enforced at the pydantic layer — Extra field is dropped
    silently; the recorded actor stays 'human' regardless."""
    _setup_env(tmp_path, monkeypatch, runtime_continuity_enabled=True)
    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/operator/runtime/candidate",
            json={
                "actor": "hostile-caller",  # attempted spoof — must be ignored
                "build_git_sha": "actorspoof123",
                "release_bundle_hash": "actorspoof123",
                "cause": "actor-spoof-test",
            },
        )
        assert r.status_code == 200
        assert r.json()["actor"] == "human"

        # Cross-check via the events endpoint: the recorded actor on the
        # candidate_prepared event is also 'human', not the caller's spoof.
        events = await client.get("/api/operator/runtime/events")
    prepared = [
        e for e in events.json()
        if e["event_type"] == "candidate_prepared" and e["cause"] == "actor-spoof-test"
    ]
    assert prepared, "candidate_prepared event should exist"
    assert prepared[0]["actor"] == "human"
