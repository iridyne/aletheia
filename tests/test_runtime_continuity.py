import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def test_runtime_continuity_migration_creates_baseline_and_backfills_operator_rows(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)

    runtime_tables = {
        "runtime_registry",
        "runtime_checkpoints",
        "runtime_events",
        "runtime_exclusive_lock",
        "runtime_ownership",
    }
    existing_tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert runtime_tables.issubset(existing_tables)

    current = conn.execute(
        "SELECT runtime_id, state FROM runtime_registry WHERE state = 'current'"
    ).fetchone()
    assert current is not None
    assert current["runtime_id"] == "runtime-local-current"

    for table in (
        "agent_shell_sessions",
        "agent_shell_tasks",
        "agent_shell_messages",
        "agent_shell_events",
        "agent_shell_heartbeats",
    ):
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "runtime_id" in cols

    conn.execute(
        "INSERT INTO agent_shell_sessions (id, user_input, kind, runtime_id) "
        "VALUES ('s1', 'legacy', 'command_session', NULL)"
    )
    from aletheia.storage.migrations import run_migrations

    run_migrations()
    row = conn.execute("SELECT runtime_id FROM agent_shell_sessions WHERE id = 's1'").fetchone()
    assert row["runtime_id"] == "runtime-local-current"


def test_runtime_service_candidate_shadow_promote_and_rollback(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.runtime_continuity.service import RuntimeContinuityService

    launches = []

    def fake_launcher(runtime):
        launches.append(runtime["runtime_id"])
        return {
            "attempted": True,
            "started": True,
            "pid": 4321,
            "host": "127.0.0.1",
            "port": 8877,
            "base_url": "http://127.0.0.1:8877",
        }

    svc = RuntimeContinuityService(lock_ttl_sec=30, shadow_gateway_launcher=fake_launcher)
    candidate = svc.prepare_candidate(
        actor="human",
        cause="test-candidate",
        code_version="v2",
        config_version="cfg2",
        schema_version="schema1",
        build_git_sha="abc123",
        release_bundle_hash="bundle123",
    )
    assert candidate["state"] == "candidate"

    shadow = svc.start_shadow(candidate["runtime_id"], actor="human", cause="test-shadow")
    assert shadow["state"] == "shadowing"
    assert launches == [candidate["runtime_id"]]
    assert shadow["metadata"]["gateway_process"]["pid"] == 4321

    validation = svc.validate_shadow(shadow["runtime_id"], actor="human", cause="test-validate")
    assert validation["state"] == "ready_to_promote"
    assert validation["can_promote"] is True

    promoted = svc.promote(validation["runtime_id"], actor="human", cause="test-promote")
    assert promoted["current"]["runtime_id"] == validation["runtime_id"]
    assert promoted["previous_current"]["state"] == "draining"
    assert promoted["draining"]["runtime_id"] == "runtime-local-current"
    assert promoted["activation"]["restart_required"] is False
    assert promoted["activation"]["strategy"] == "frontdoor_route_switch"
    assert promoted["activation"]["backend_base_url"] == "http://127.0.0.1:8877"
    assert promoted["current"]["metadata"]["activation"]["action"] == "promote"
    from aletheia.gateway.frontdoor_route import read_frontdoor_route

    route = read_frontdoor_route()
    assert route is not None
    assert route["runtime_id"] == validation["runtime_id"]
    assert route["backend_base_url"] == "http://127.0.0.1:8877"

    rolled = svc.rollback(actor="human", cause="test-rollback")
    assert rolled["current"]["runtime_id"] == "runtime-local-current"
    assert rolled["rolled_back"]["state"] == "rolled_back"
    assert rolled["activation"]["restart_required"] is True
    assert rolled["activation"]["action"] == "rollback"

    events = svc.list_events(limit=20)
    event_types = [event["event_type"] for event in events]
    assert "candidate_prepared" in event_types
    assert "shadow_started" in event_types
    assert "shadow_validated" in event_types
    assert "runtime_promoted" in event_types
    assert "runtime_rolled_back" in event_types


def test_runtime_service_rejects_lock_conflicts(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

    svc = RuntimeContinuityService(lock_ttl_sec=300)
    token = svc.acquire_lock("promote", runtime_id="runtime-local-current", owner="operator-a")
    assert token

    with pytest.raises(RuntimeTransitionError, match="runtime_lock_conflict"):
        svc.acquire_lock("promote", runtime_id="runtime-local-current", owner="operator-b")

    takeover = svc.acquire_lock(
        "promote",
        runtime_id="runtime-local-current",
        owner="operator-b",
        force=True,
    )
    assert takeover != token


def test_runtime_status_exposes_metrics_guidance_and_dto_types(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.runtime_continuity import (
        RuntimeCheckpoint,
        RuntimeContinuityService,
        RuntimeEvent,
        RuntimeOwnership,
        RuntimeRecord,
    )

    svc = RuntimeContinuityService()
    status = svc.status()

    assert RuntimeRecord(runtime_id="runtime-x", state="candidate").runtime_id == "runtime-x"
    assert RuntimeCheckpoint(id="checkpoint-x", runtime_id="runtime-x", checkpoint_type="pre_promote").status == "available"
    assert RuntimeEvent(id="event-x", event_type="candidate_prepared", phase="candidate").severity == "info"
    assert RuntimeOwnership(id="own-x", entity_type="task", entity_id="task-x", runtime_id="runtime-x").status == "active"
    assert status["metrics"]["state_counts"]["current"] == 1
    assert status["metrics"]["active_lock_count"] == 0
    assert status["metrics"]["recent_event_count"] >= 0
    assert status["guidance"]["action"] in {"prepare_candidate", "start_shadow", "promote", "drain", "rollback", "activate_restart", "observe"}
    assert "reason" in status["guidance"]


def test_runtime_shadow_validation_rejects_unready_gateway_process(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

    svc = RuntimeContinuityService()
    candidate = svc.prepare_candidate(
        actor="test",
        cause="validation-gateway-test",
        code_version="v2",
        config_version="cfg2",
        schema_version="schema1",
        build_git_sha="notready123",
        release_bundle_hash="notready123",
    )
    shadow = svc.start_shadow(candidate["runtime_id"], actor="test", cause="validation-gateway-test")
    conn = svc._db()
    conn.execute(
        "UPDATE runtime_registry SET metadata = ? WHERE runtime_id = ?",
        ('{"gateway_process":{"attempted":true,"started":true,"ready":false}}', shadow["runtime_id"]),
    )
    conn.commit()

    with pytest.raises(RuntimeTransitionError, match="shadow_validation_failed"):
        svc.validate_shadow(shadow["runtime_id"], actor="test", cause="validation-gateway-test")

    failed = conn.execute(
        "SELECT state, last_error FROM runtime_registry WHERE runtime_id = ?",
        (shadow["runtime_id"],),
    ).fetchone()
    assert failed["state"] == "failed"
    assert failed["last_error"] == "shadow_validation_failed"


@pytest.mark.anyio
async def test_runtime_api_and_operator_heartbeat_surface_runtime_metadata(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        status = await client.get("/api/operator/runtime")
        assert status.status_code == 200
        assert status.json()["current"]["runtime_id"] == "runtime-local-current"

        candidate = await client.post(
            "/api/operator/runtime/candidate",
            json={
                "code_version": "v2",
                "config_version": "cfg2",
                "schema_version": "schema1",
                "build_git_sha": "abc123",
                "release_bundle_hash": "bundle123",
                "cause": "api-test",
            },
        )
        assert candidate.status_code == 200
        candidate_body = candidate.json()
        runtime_id = candidate_body["runtime_id"]
        assert candidate_body["phase"] == "candidate"
        assert candidate_body["actor"] == "human"
        assert candidate_body["cause"] == "api-test"
        assert candidate_body["can_promote"] is False
        assert candidate_body["can_rollback"] is False

        shadow = await client.post(
            "/api/operator/runtime/shadow",
            json={"runtime_id": runtime_id, "cause": "api-test"},
        )
        assert shadow.status_code == 200
        shadow_body = shadow.json()
        assert shadow_body["state"] == "ready_to_promote"
        assert shadow_body["phase"] == "ready_to_promote"
        assert shadow_body["can_promote"] is True

        heartbeat = await client.post(
            "/api/operator/internal/heartbeat",
            json={
                "agent_id": "operator",
                "status": "online",
                "mode": "Observing",
                "permission_mode": "ReadOnly",
                "runtime_id": runtime_id,
                "runtime_mode": "shadow",
                "runtime_state": "ready_to_promote",
                "is_busy": False,
                "current_task_id": None,
                "current_intent": "shadow validation",
                "queue_size": 0,
                "last_error": None,
                "model_provider": "pi-shell",
                "session_id": "operator-main",
            },
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["runtime_id"] == runtime_id
        assert heartbeat.json()["runtime_mode"] == "shadow"
        assert heartbeat.json()["runtime_state"] == "ready_to_promote"

        events = await client.get("/api/operator/runtime/events")
        assert events.status_code == 200
        assert any(item["event_type"] == "shadow_validated" for item in events.json())


@pytest.mark.anyio
async def test_runtime_api_exposes_and_drains_previous_runtime(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        candidate = await client.post(
            "/api/operator/runtime/candidate",
            json={
                "code_version": "v2",
                "config_version": "cfg2",
                "schema_version": "schema1",
                "build_git_sha": "drain123",
                "release_bundle_hash": "drain123",
                "cause": "api-drain-test",
            },
        )
        runtime_id = candidate.json()["runtime_id"]
        shadow = await client.post("/api/operator/runtime/shadow", json={"runtime_id": runtime_id})
        assert shadow.status_code == 200
        promoted = await client.post("/api/operator/runtime/promote", json={"runtime_id": runtime_id})
        assert promoted.status_code == 200
        assert promoted.json()["draining"]["runtime_id"] == "runtime-local-current"

        status = await client.get("/api/operator/runtime")
        assert status.status_code == 200
        assert status.json()["can_drain"] is True
        assert status.json()["draining"]["runtime_id"] == "runtime-local-current"

        drained = await client.post(
            "/api/operator/runtime/drain",
            json={"runtime_id": "runtime-local-current", "force": True, "cause": "api-drain-test"},
        )
        assert drained.status_code == 200
        assert drained.json()["state"] == "legacy_position_manager"

        events = await client.get("/api/operator/runtime/events")
        assert any(item["event_type"] == "runtime_drained" for item in events.json())


@pytest.mark.anyio
async def test_update_continuity_materializes_candidate_worktree(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.runtime_continuity import bundles
    from aletheia.server.app import app
    from aletheia.update import applier

    monkeypatch.setattr(
        applier,
        "check_for_update",
        lambda: {
            "ok": True,
            "has_update": True,
            "behind_count": 1,
            "current_commit": "current123",
            "remote_commit": "remote123",
            "branch": "master",
        },
    )
    materialized = []

    def fake_materialize_candidate_worktree(*, runtime_id, commit, branch):
        target = tmp_path / "runtime_bundles" / runtime_id
        target.mkdir(parents=True)
        materialized.append((runtime_id, commit, branch, target))
        return target

    monkeypatch.setattr(bundles, "materialize_candidate_worktree", fake_materialize_candidate_worktree)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/update/continuity", json={"confirm": True})

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    candidate = body["candidate"]
    assert candidate["runtime_id"] == "runtime-remote123"
    assert materialized == [
        ("runtime-remote123", "remote123", "master", tmp_path / "runtime_bundles" / "runtime-remote123")
    ]
    assert candidate["artifact_path"] == str(tmp_path / "runtime_bundles" / "runtime-remote123")


def test_runtime_cli_status_smoke(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.cli.runtime_cmd import runtime_app

    result = CliRunner().invoke(runtime_app, ["status"])
    assert result.exit_code == 0
    assert "runtime-local-current" in result.output
    assert "Guidance:" in result.output
    assert "Metrics:" in result.output


def test_runtime_cli_drain_defaults_to_latest_draining_runtime(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.cli.runtime_cmd import runtime_app
    from aletheia.runtime_continuity.service import RuntimeContinuityService

    svc = RuntimeContinuityService()
    candidate = svc.prepare_candidate(
        actor="test",
        cause="cli-drain-test",
        code_version="v2",
        config_version="cfg2",
        schema_version="schema1",
        build_git_sha="clidrain123",
        release_bundle_hash="clidrain123",
    )
    shadow = svc.start_shadow(candidate["runtime_id"], actor="test", cause="cli-drain-test")
    ready = svc.validate_shadow(shadow["runtime_id"], actor="test", cause="cli-drain-test")
    svc.promote(ready["runtime_id"], actor="test", cause="cli-drain-test")

    result = CliRunner().invoke(runtime_app, ["drain", "--force"])
    assert result.exit_code == 0
    assert "runtime-local-current" in result.output
    assert "legacy_position_manager" in result.output


def test_candidate_worktree_materialization_uses_detached_git_worktree(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.runtime_continuity import bundles

    calls = []

    def fake_git(args, *, cwd=None):
        calls.append((args, cwd))
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(bundles, "_git", fake_git)
    target = bundles.materialize_candidate_worktree(
        runtime_id="runtime-abc123",
        commit="abc123",
        branch="master",
    )

    assert target.name == "runtime-abc123"
    assert calls[0][0] == ["fetch", "origin", "master"]
    assert calls[1][0][:4] == ["worktree", "add", "--detach", str(target)]
    assert calls[1][0][4] == "abc123"


def test_shadow_gateway_process_uses_runtime_env_port_and_artifact_path(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config
    from aletheia.runtime_continuity import processes

    artifact = tmp_path / "candidate"
    artifact.mkdir()
    captured = {}

    class FakeProc:
        pid = 2468

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(processes.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(processes, "_resolve_real_python", lambda: ("python-real", {"PYTHON_PATCH": "1"}))
    monkeypatch.setattr(processes, "wait_http_ready", lambda *a, **k: True)

    cfg = load_config()
    result = processes.spawn_shadow_gateway(
        cfg,
        {
            "runtime_id": "runtime-shadow",
            "artifact_path": str(artifact),
            "state": "shadowing",
        },
    )

    assert result["started"] is True
    assert result["pid"] == 2468
    assert result["port"] == cfg.app.dashboard_port + cfg.runtime.runtime_continuity_shadow_port_offset
    assert captured["cmd"][:4] == ["python-real", "-m", "uvicorn", "aletheia.server.app:app"]
    assert captured["kwargs"]["cwd"] == str(artifact)
    env = captured["kwargs"]["env"]
    assert env["ALETHEIA_RUNTIME_ID"] == "runtime-shadow"
    assert env["ALETHEIA_RUNTIME_MODE"] == "shadow"
    assert env["ALETHEIA_RUNTIME_STATE"] == "shadowing"
    assert env["ALETHEIA_RUNTIME_PORT"] == str(result["port"])
    assert str(artifact) in env["PYTHONPATH"]


def test_gateway_runner_inner_server_uses_current_runtime_artifact_path(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)

    artifact = tmp_path / "current-runtime"
    artifact.mkdir()
    conn.execute(
        "UPDATE runtime_registry SET artifact_path = ?, metadata = ? WHERE runtime_id = 'runtime-local-current'",
        (str(artifact), '{"test":"artifact"}'),
    )
    conn.commit()

    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    captured = {}

    class FakeProc:
        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner, "_resolve_real_python", lambda: ("python-real", {"PYTHON_PATCH": "1"}))

    code = runner._spawn_server(load_config())

    assert code == 0
    assert captured["kwargs"]["cwd"] == str(artifact)
    env = captured["kwargs"]["env"]
    assert env["ALETHEIA_RUNTIME_ID"] == "runtime-local-current"
    assert env["ALETHEIA_RUNTIME_MODE"] == "current"
    assert str(artifact) in env["PYTHONPATH"]


def test_shadow_runtime_context_skips_scheduler_and_pidfile(monkeypatch):
    monkeypatch.setenv("ALETHEIA_RUNTIME_MODE", "shadow")
    monkeypatch.setenv("ALETHEIA_RUNTIME_ID", "runtime-shadow")

    from aletheia.server import app as server_app

    context = server_app.runtime_process_context()

    assert context["runtime_id"] == "runtime-shadow"
    assert context["runtime_mode"] == "shadow"
    assert server_app.should_start_background_services(context) is False
    assert server_app.should_write_gateway_pidfile(context) is False
