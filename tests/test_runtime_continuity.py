import sqlite3
import inspect

import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner


def test_runtime_sync_io_handlers_are_threadpool_eligible():
    from aletheia.server.routes import runtime

    handlers = (
        runtime.runtime_status,
        runtime.runtime_candidate,
        runtime.runtime_shadow,
        runtime.runtime_promote,
        runtime.runtime_drain,
        runtime.runtime_rollback,
        runtime.runtime_events,
    )
    assert all(not inspect.iscoroutinefunction(handler) for handler in handlers)


def test_current_runtime_record_is_read_only_and_does_not_migrate(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.runtime_continuity import processes
    from aletheia.storage import sqlite as sqlite_store

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    sqlite_store.reset_db()
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE runtime_registry ("
            "runtime_id TEXT PRIMARY KEY, state TEXT NOT NULL, "
            "promoted_at TEXT, created_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO runtime_registry(runtime_id, state, promoted_at, created_at) "
            "VALUES ('runtime-current', 'current', '2026-08-06T00:00:00Z', '2026-08-06T00:00:00Z')"
        )

    real_connect = sqlite3.connect
    observed = {}

    def tracking_connect(database_uri, **kwargs):
        observed["database_uri"] = database_uri
        observed.update(kwargs)
        return real_connect(database_uri, **kwargs)

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", tracking_connect)

    assert processes.current_runtime_record()["runtime_id"] == "runtime-current"
    assert observed["uri"] is True
    assert observed["database_uri"].endswith("?mode=ro")
    with sqlite3.connect(database) as db:
        assert {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } == {"runtime_registry"}


def test_current_runtime_record_tolerates_uninitialized_database(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.runtime_continuity.processes import current_runtime_record
    from aletheia.storage import sqlite as sqlite_store

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    sqlite_store.reset_db()
    assert current_runtime_record() is None

    with sqlite3.connect(tmp_path / "app.db") as db:
        db.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    assert current_runtime_record() is None


def setup_env(tmp_path, monkeypatch, *, runtime_continuity_enabled: bool = True):
    """Runtime-continuity test scaffold.

    #82 fix: the service + API now honour cfg.runtime.runtime_continuity_enabled and
    reject writes when it is off. Existing tests here exercise the feature end-to-end
    so they need the flag ON — default True keeps them working. Set the kwarg to
    False for a negative test that asserts the kill switch actually blocks writes.
    """
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    # Write a minimal config with the runtime_continuity flag set so load_config()
    # from route handlers picks it up. Everything else falls back to defaults.
    if runtime_continuity_enabled:
        (tmp_path / "config.yaml").write_text(
            "runtime:\n  runtime_continuity_enabled: true\n",
            encoding="utf-8",
        )
        loader._config_cache = None  # bust the ttl cache so the write is visible

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

    svc = RuntimeContinuityService(lock_ttl_sec=30, enabled=True, shadow_gateway_launcher=fake_launcher)
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


def _make_ready_candidate(svc, *, suffix="abc123"):
    """Helper: prepare → shadow → validate a candidate, return the validated record."""
    candidate = svc.prepare_candidate(
        actor="test",
        cause="setup",
        code_version="v2",
        config_version="cfg2",
        schema_version="schema1",
        build_git_sha=suffix,
        release_bundle_hash=suffix,
    )
    shadow = svc.start_shadow(candidate["runtime_id"], actor="test", cause="setup")
    return svc.validate_shadow(shadow["runtime_id"], actor="test", cause="setup")


def _fail_write(**_kw):
    raise OSError("disk full")


def test_promote_route_write_failure_leaves_no_split_brain(tmp_path, monkeypatch):
    """#1105: promote() must not durably commit CURRENT when Frontdoor write fails."""
    import aletheia.storage.sqlite as sq
    setup_env(tmp_path, monkeypatch)

    import aletheia.runtime_continuity.service as svc_mod
    from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

    def fake_launcher(runtime):
        return {"attempted": True, "started": True, "pid": 9001, "host": "127.0.0.1", "port": 8900, "base_url": "http://127.0.0.1:8900"}

    svc = RuntimeContinuityService(lock_ttl_sec=30, enabled=True, shadow_gateway_launcher=fake_launcher)
    ready = _make_ready_candidate(svc, suffix="split1")

    monkeypatch.setattr(svc_mod, "write_frontdoor_route", _fail_write)

    with pytest.raises(RuntimeTransitionError, match="frontdoor_route_switch_failed"):
        svc.promote(ready["runtime_id"], actor="test", cause="fail-test")

    # DB must not have committed the candidate as CURRENT
    row = sq._db_conn.execute(
        "SELECT state FROM runtime_registry WHERE runtime_id = ?", (ready["runtime_id"],)
    ).fetchone()
    assert row["state"] == "ready_to_promote", "candidate must not be marked CURRENT after failed activation"

    # Old runtime must still be CURRENT, not DRAINING
    current_row = sq._db_conn.execute(
        "SELECT state FROM runtime_registry WHERE runtime_id = 'runtime-local-current'"
    ).fetchone()
    assert current_row["state"] == "current", "old runtime must remain CURRENT after failed activation"

    # Checkpoint must not have been created
    cp = sq._db_conn.execute(
        "SELECT * FROM runtime_checkpoints WHERE checkpoint_type = 'pre_promote'"
    ).fetchone()
    assert cp is None, "pre_promote checkpoint must not be persisted after failed activation"


def test_rollback_route_write_failure_preserves_checkpoint(tmp_path, monkeypatch):
    """#1105: rollback() must not consume the checkpoint or mark target CURRENT when Frontdoor write fails.

    Requires two promotions so the rollback target (runtime A) has a gateway_process,
    making the rollback activation use frontdoor_route_switch strategy.
    """
    import aletheia.storage.sqlite as sq
    setup_env(tmp_path, monkeypatch)

    import aletheia.runtime_continuity.service as svc_mod
    from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

    ports = iter([8951, 8952])

    def fake_launcher(runtime):
        port = next(ports)
        return {"attempted": True, "started": True, "pid": 9000 + port, "host": "127.0.0.1", "port": port, "base_url": f"http://127.0.0.1:{port}"}

    svc = RuntimeContinuityService(lock_ttl_sec=30, enabled=True, shadow_gateway_launcher=fake_launcher)

    # Promote runtime A → A has gateway_process.base_url
    ready_a = _make_ready_candidate(svc, suffix="rba01")
    promoted_a = svc.promote(ready_a["runtime_id"], actor="test", cause="setup-a")
    assert promoted_a["current"]["runtime_id"] == ready_a["runtime_id"]

    # Promote runtime B → checkpoint now points back to A
    ready_b = _make_ready_candidate(svc, suffix="rba02")
    promoted_b = svc.promote(ready_b["runtime_id"], actor="test", cause="setup-b")
    assert promoted_b["current"]["runtime_id"] == ready_b["runtime_id"]

    # Rollback target = A, which has gateway_process → frontdoor_route_switch strategy
    # Break the Frontdoor write so rollback activation fails
    monkeypatch.setattr(svc_mod, "write_frontdoor_route", _fail_write)

    with pytest.raises(RuntimeTransitionError, match="frontdoor_route_switch_failed"):
        svc.rollback(actor="test", cause="fail-rollback")

    # B must still be CURRENT
    b_row = sq._db_conn.execute(
        "SELECT state FROM runtime_registry WHERE runtime_id = ?", (ready_b["runtime_id"],)
    ).fetchone()
    assert b_row["state"] == "current", "runtime B must still be CURRENT after failed rollback"

    # A must remain DRAINING (not restored to CURRENT)
    a_row = sq._db_conn.execute(
        "SELECT state FROM runtime_registry WHERE runtime_id = ?", (ready_a["runtime_id"],)
    ).fetchone()
    assert a_row["state"] == "draining", "runtime A must remain DRAINING — not restored as CURRENT"

    # Checkpoint must still be available (not consumed)
    cp = sq._db_conn.execute(
        "SELECT status FROM runtime_checkpoints WHERE checkpoint_type = 'pre_promote' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert cp["status"] == "available", "checkpoint must remain available after failed rollback"


def test_promote_activation_failure_raises_transition_error(tmp_path, monkeypatch):
    """#1105: promote() raises RuntimeTransitionError (→ API 409) when Frontdoor write fails."""
    setup_env(tmp_path, monkeypatch)

    import aletheia.runtime_continuity.service as svc_mod
    from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

    def fake_launcher(runtime):
        return {"attempted": True, "started": True, "pid": 9003, "host": "127.0.0.1", "port": 8902, "base_url": "http://127.0.0.1:8902"}

    svc = RuntimeContinuityService(lock_ttl_sec=30, enabled=True, shadow_gateway_launcher=fake_launcher)
    ready = _make_ready_candidate(svc, suffix="api409")

    monkeypatch.setattr(svc_mod, "write_frontdoor_route", _fail_write)

    with pytest.raises(RuntimeTransitionError, match="frontdoor_route_switch_failed"):
        svc.promote(ready["runtime_id"], actor="test", cause="api-test")


def test_successful_promote_route_readback_matches_current_runtime_id(tmp_path, monkeypatch):
    """#1105: after successful promote, read_frontdoor_route().runtime_id == current_runtime_id()."""
    setup_env(tmp_path, monkeypatch)

    from aletheia.gateway.frontdoor_route import read_frontdoor_route
    from aletheia.runtime_continuity.service import RuntimeContinuityService

    def fake_launcher(runtime):
        return {"attempted": True, "started": True, "pid": 9004, "host": "127.0.0.1", "port": 8903, "base_url": "http://127.0.0.1:8903"}

    svc = RuntimeContinuityService(lock_ttl_sec=30, enabled=True, shadow_gateway_launcher=fake_launcher)
    ready = _make_ready_candidate(svc, suffix="readback")
    svc.promote(ready["runtime_id"], actor="test", cause="readback-test")

    route = read_frontdoor_route()
    assert route is not None
    assert route["runtime_id"] == svc.current_runtime_id()


def test_runtime_service_rejects_lock_conflicts(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

    svc = RuntimeContinuityService(lock_ttl_sec=300, enabled=True)
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

    svc = RuntimeContinuityService(enabled=True)
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

    svc = RuntimeContinuityService(enabled=True)
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


def _mock_shadow_gateway(monkeypatch) -> None:
    """Stub the shadow-gateway spawn so API tests don't need a real subprocess.

    The API's `_service()` builds a real `spawn_shadow_gateway` closure when
    runtime_continuity_enabled=True (which the tests below need for #82 to pass);
    without this stub start_shadow tries to actually fork a gateway and fails.

    We patch the NAME as imported into `server.routes.runtime` (the module that
    holds the closure), NOT the original in `runtime_continuity.processes` —
    Python resolves `spawn_shadow_gateway` inside the lambda against the enclosing
    module's namespace at call time, so patching processes.spawn_shadow_gateway
    alone doesn't reach the route's closure.
    """
    def fake_spawn(cfg, runtime):
        return {
            "attempted": True,
            "started": True,
            "ready": True,
            "pid": 9999,
            "host": "127.0.0.1",
            "port": 8877,
            "base_url": "http://127.0.0.1:8877",
        }

    from aletheia.server.routes import runtime as _routes_runtime
    monkeypatch.setattr(_routes_runtime, "spawn_shadow_gateway", fake_spawn)

    # The shadow route also imports spawn_shadow_operator_shell inline via
    # `from aletheia.gateway.operator_shell import spawn_shadow_operator_shell`.
    # Patch it at the source module — the inline `from … import …` will resolve
    # the current attribute of the module at import time, which is inside the
    # request handler, so this reaches it.
    from aletheia.gateway import operator_shell as _os_mod

    def fake_spawn_operator_shell(cfg, runtime_id):
        return None

    monkeypatch.setattr(_os_mod, "spawn_shadow_operator_shell", fake_spawn_operator_shell)


@pytest.mark.anyio
async def test_runtime_api_and_operator_heartbeat_surface_runtime_metadata(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _mock_shadow_gateway(monkeypatch)

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
    _mock_shadow_gateway(monkeypatch)

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
        # #83 fix: promote is state-changing; server-side confirm required.
        promoted = await client.post(
            "/api/operator/runtime/promote",
            json={"runtime_id": runtime_id, "confirm": True},
        )
        assert promoted.status_code == 200
        assert promoted.json()["draining"]["runtime_id"] == "runtime-local-current"

        status = await client.get("/api/operator/runtime")
        assert status.status_code == 200
        assert status.json()["can_drain"] is True
        assert status.json()["draining"]["runtime_id"] == "runtime-local-current"

        # #83 fix: force-drain moves current runtime to legacy_position_manager; confirm required.
        drained = await client.post(
            "/api/operator/runtime/drain",
            json={"runtime_id": "runtime-local-current", "force": True, "confirm": True, "cause": "api-drain-test"},
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

    svc = RuntimeContinuityService(enabled=True)
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
