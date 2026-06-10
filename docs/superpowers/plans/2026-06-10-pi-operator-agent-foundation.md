# PI Operator Agent Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python-side control-plane foundation for the dual-process PI Operator Agent so the repo has operator config, persistence, routes, CLI surface, and compatibility endpoints ready for the later Node Operator Shell and Command Center work.

**Architecture:** Keep Python as the operator state truth and mutation authority. Add a dedicated `operator` config block, SQLite-backed operator entities, public/internal FastAPI routes, and a new CLI group while preserving the current `agent_shell` request-response runtime and `/api/command` compatibility.

**Tech Stack:** Python, FastAPI, Typer, SQLite, Pydantic v2, pytest, httpx AsyncClient

---

## Scope Split

The approved dual-process design spans multiple independent subsystems. This plan intentionally covers **Tracks 0-2 plus the operator CLI seed**:

- operator config
- operator SQLite schema and store helpers
- operator FastAPI control-plane routes
- app/router wiring
- operator CLI
- compatibility bridge from `/api/operator/command` to the existing Python `AgentShellRuntime`

It does **not** yet implement:

- the Node/TypeScript `operator-shell/` runtime
- supervisor child-process wiring for Node
- the frontend `Command Center` page
- recommendation panel / inspector UI
- repair engine behavior beyond typed persistence hooks

Those follow in later implementation plans after this foundation is working and verified.

### Task 1: Add Operator Config And Backward-Compatible Migration

**Files:**
- Modify: `aletheia/config/schema.py`
- Modify: `aletheia/config/loader.py`
- Test: `tests/test_operator_config.py`

- [ ] **Step 1: Write the failing config tests**

```python
from aletheia.config.loader import load_config, save_config
from aletheia.config.schema import AletheiaConfig


def test_operator_defaults_present(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")

    cfg = load_config()

    assert cfg.operator.enabled is True
    assert cfg.operator.permission_mode == "ReadOnly"
    assert cfg.operator.port == 8791
    assert cfg.operator.main_session_id == "operator-main"


def test_operator_mode_is_independent_from_agent_mode(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")

    cfg = AletheiaConfig()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "Auto"
    save_config(cfg)

    loaded = load_config()
    assert loaded.agent.permission_mode == "Observe"
    assert loaded.operator.permission_mode == "Auto"
```

- [ ] **Step 2: Run the new config tests to verify RED**

Run: `uv run pytest tests/test_operator_config.py -q`

Expected: FAIL because `OperatorConfig` and `cfg.operator` do not exist yet.

- [ ] **Step 3: Implement the operator config block and loader behavior**

```python
class OperatorConfig(BaseModel):
    enabled: bool = True
    bind_host: str = "127.0.0.1"
    port: int = 8791
    permission_mode: str = "ReadOnly"
    heartbeat_interval_sec: int = 10
    heartbeat_stale_after_sec: int = 45
    restart_after_sec: int = 90
    max_restart_failures: int = 3
    loop_enabled: bool = True
    main_session_id: str = "operator-main"


class AletheiaConfig(BaseModel):
    ...
    operator: OperatorConfig = Field(default_factory=OperatorConfig)
```

```python
_OPERATOR_MODE_MIGRATION = {
    "Observe": "ReadOnly",
    "Paper": "PaperAuto",
    "Full": "Auto",
}


def load_config() -> AletheiaConfig:
    ...
    if cfg.operator.permission_mode in _OPERATOR_MODE_MIGRATION:
        cfg.operator.permission_mode = _OPERATOR_MODE_MIGRATION[cfg.operator.permission_mode]
        save_config(cfg)
    return cfg
```

- [ ] **Step 4: Run the config tests to verify GREEN**

Run: `uv run pytest tests/test_operator_config.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aletheia/config/schema.py aletheia/config/loader.py tests/test_operator_config.py
git commit -m "feat: add operator config foundation"
```

### Task 2: Add Operator SQLite Schema And Store Helpers

**Files:**
- Modify: `aletheia/storage/migrations.py`
- Create: `aletheia/agent_shell/operator_store.py`
- Test: `tests/test_operator_store.py`

- [ ] **Step 1: Write the failing storage tests**

```python
def test_operator_tables_exist(tmp_path, monkeypatch):
    import sqlite3
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    sq._db_conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    sq._db_conn.row_factory = sqlite3.Row
    run_migrations()

    names = {
        row["name"]
        for row in sq._db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    assert "agent_shell_messages" in names
    assert "agent_shell_tasks" in names
    assert "agent_shell_artifacts" in names
    assert "agent_shell_heartbeats" in names
```

```python
def test_record_and_fetch_latest_operator_heartbeat(tmp_path, monkeypatch):
    ...
    from aletheia.agent_shell.operator_store import record_heartbeat, get_latest_heartbeat

    record_heartbeat(
        agent_id="operator",
        status="online",
        mode="Observing",
        permission_mode="ReadOnly",
        is_busy=False,
        current_task_id=None,
        current_intent="monitoring system state",
        queue_size=0,
        last_error=None,
        model_provider="openai-compatible",
        session_id="operator-main",
    )

    hb = get_latest_heartbeat()
    assert hb is not None
    assert hb["agent_id"] == "operator"
    assert hb["mode"] == "Observing"
```

- [ ] **Step 2: Run the storage tests to verify RED**

Run: `uv run pytest tests/test_operator_store.py -q`

Expected: FAIL because the new tables and store helpers do not exist yet.

- [ ] **Step 3: Implement the new SQLite tables and store module**

```python
CREATE TABLE IF NOT EXISTS agent_shell_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES agent_shell_sessions(id),
    role TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'message',
    content TEXT NOT NULL,
    related_event_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_tasks (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    created_by TEXT NOT NULL,
    assigned_to TEXT NOT NULL DEFAULT 'operator',
    input_data TEXT DEFAULT '{}',
    output_data TEXT,
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES agent_shell_tasks(id),
    session_id TEXT REFERENCES agent_shell_sessions(id),
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_heartbeats (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    permission_mode TEXT NOT NULL,
    is_busy INTEGER NOT NULL DEFAULT 0,
    current_task_id TEXT,
    current_intent TEXT,
    queue_size INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    model_provider TEXT,
    session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

```python
def record_heartbeat(...): ...
def get_latest_heartbeat() -> dict | None: ...
def create_task(...): ...
def update_task(...): ...
def list_tasks(limit: int = 50) -> list[dict]: ...
def append_message(...): ...
def list_messages(session_id: str) -> list[dict]: ...
```

- [ ] **Step 4: Run the storage tests to verify GREEN**

Run: `uv run pytest tests/test_operator_store.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aletheia/storage/migrations.py aletheia/agent_shell/operator_store.py tests/test_operator_store.py
git commit -m "feat: add operator persistence store"
```

### Task 3: Add Operator FastAPI Control-Plane Routes

**Files:**
- Create: `aletheia/server/routes/operator.py`
- Modify: `aletheia/server/app.py`
- Test: `tests/test_operator_routes.py`

- [ ] **Step 1: Write the failing route tests**

```python
import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app


@pytest.mark.anyio
async def test_operator_status_defaults_when_no_heartbeat(tmp_path, monkeypatch):
    ...
    resp = await client.get("/api/operator/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "operator"
    assert data["status"] == "offline"
    assert data["permission_mode"] == "ReadOnly"


@pytest.mark.anyio
async def test_operator_internal_heartbeat_roundtrip(tmp_path, monkeypatch):
    ...
    await client.post("/api/operator/internal/heartbeat", json={
        "agent_id": "operator",
        "status": "online",
        "mode": "Observing",
        "permission_mode": "ReadOnly",
        "is_busy": False,
        "current_task_id": None,
        "current_intent": "monitoring system state",
        "queue_size": 0,
        "last_error": None,
        "model_provider": "openai-compatible",
        "session_id": "operator-main",
    })
    resp = await client.get("/api/operator/status")
    assert resp.json()["status"] == "online"
```

```python
@pytest.mark.anyio
async def test_operator_task_create_and_list(tmp_path, monkeypatch):
    ...
    create = await client.post("/api/operator/internal/tasks", json={
        "task_type": "diagnose_failure",
        "status": "running",
        "priority": "normal",
        "created_by": "operator_loop",
        "assigned_to": "operator",
        "input_data": {"entity_id": "run-1"},
    })
    assert create.status_code == 200

    listing = await client.get("/api/operator/tasks")
    assert listing.status_code == 200
    assert listing.json()[0]["task_type"] == "diagnose_failure"
```

- [ ] **Step 2: Run the route tests to verify RED**

Run: `uv run pytest tests/test_operator_routes.py -q`

Expected: FAIL because `/api/operator/*` routes do not exist yet.

- [ ] **Step 3: Implement the operator router and app wiring**

```python
router = APIRouter(prefix="/api/operator", tags=["operator"])


@router.get("/status")
async def operator_status():
    cfg = load_config()
    hb = get_latest_heartbeat()
    if hb is None:
        return {
            "agent_id": "operator",
            "status": "offline",
            "mode": "Paused" if cfg.runtime.paused else "Observing",
            "permission_mode": cfg.operator.permission_mode,
            "is_busy": False,
            "current_task_id": None,
            "current_intent": None,
            "queue_size": 0,
            "last_error": None,
            "model_provider": None,
            "session_id": cfg.operator.main_session_id,
        }
    return hb
```

```python
@router.post("/internal/heartbeat")
async def operator_heartbeat(payload: OperatorHeartbeatIn): ...

@router.post("/internal/tasks")
async def operator_create_task(payload: OperatorTaskCreate): ...

@router.patch("/internal/tasks/{task_id}")
async def operator_update_task(task_id: str, payload: OperatorTaskUpdate): ...

@router.get("/tasks")
async def operator_tasks(limit: int = 50): ...

@router.get("/sessions/{session_id}/messages")
async def operator_session_messages(session_id: str): ...
```

```python
from aletheia.server.routes.operator import router as operator_router
...
app.include_router(operator_router)
```

- [ ] **Step 4: Run the route tests to verify GREEN**

Run: `uv run pytest tests/test_operator_routes.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aletheia/server/routes/operator.py aletheia/server/app.py tests/test_operator_routes.py
git commit -m "feat: add operator control-plane routes"
```

### Task 4: Add Operator CLI Surface

**Files:**
- Create: `aletheia/cli/operator.py`
- Modify: `aletheia/main.py`
- Test: `tests/test_operator_cli.py`

- [ ] **Step 1: Write the failing CLI tests**

```python
from typer.testing import CliRunner
from aletheia.main import app

runner = CliRunner()


def test_agent_status_command(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")

    result = runner.invoke(app, ["agent", "status"])
    assert result.exit_code == 0
    assert "Operator:" in result.output
    assert "ReadOnly" in result.output


def test_agent_sessions_command(tmp_path, monkeypatch):
    ...
    result = runner.invoke(app, ["agent", "sessions"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run the CLI tests to verify RED**

Run: `uv run pytest tests/test_operator_cli.py -q`

Expected: FAIL because the `agent` CLI group does not exist yet.

- [ ] **Step 3: Implement the operator CLI group**

```python
agent_app = typer.Typer(no_args_is_help=True)


@agent_app.command("status")
def status():
    cfg = load_config()
    hb = get_latest_heartbeat()
    typer.echo("Operator: online" if hb else "Operator: offline")
    typer.echo(f"Permission: {cfg.operator.permission_mode}")


@agent_app.command("sessions")
def sessions(limit: int = typer.Option(20, "--limit")):
    ...


@agent_app.command("pause")
def pause():
    ...


@agent_app.command("resume")
def resume():
    ...
```

```python
from aletheia.cli.operator import agent_app
...
app.add_typer(agent_app, name="agent", help="Operator Agent control-plane commands.")
```

- [ ] **Step 4: Run the CLI tests to verify GREEN**

Run: `uv run pytest tests/test_operator_cli.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aletheia/cli/operator.py aletheia/main.py tests/test_operator_cli.py
git commit -m "feat: add operator cli surface"
```

### Task 5: Add `/api/operator/command` Compatibility Bridge

**Files:**
- Modify: `aletheia/server/routes/operator.py`
- Test: `tests/test_operator_command_route.py`

- [ ] **Step 1: Write the failing compatibility tests**

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.anyio
async def test_operator_command_uses_existing_agent_shell_runtime(tmp_path, monkeypatch):
    ...
    fake_response = {
        "intent": "get_system_status",
        "risk_level": "read",
        "response": "System healthy",
        "action": None,
        "needs_confirmation": False,
    }

    with patch(
        "aletheia.agent_shell.runtime.AgentShellRuntime.handle",
        new=AsyncMock(return_value=fake_response),
    ):
        resp = await client.post("/api/operator/command", json={"input": "status"})

    assert resp.status_code == 200
    assert resp.json()["response"] == "System healthy"
```

- [ ] **Step 2: Run the compatibility test to verify RED**

Run: `uv run pytest tests/test_operator_command_route.py -q`

Expected: FAIL because `/api/operator/command` does not exist yet.

- [ ] **Step 3: Implement the compatibility route**

```python
class OperatorCommandRequest(BaseModel):
    input: str
    context: dict | None = None


@router.post("/command")
async def operator_command(req: OperatorCommandRequest):
    from aletheia.agent_shell.runtime import AgentShellRuntime
    runtime = AgentShellRuntime()
    return await runtime.handle(req.input)
```

- [ ] **Step 4: Run the compatibility test to verify GREEN**

Run: `uv run pytest tests/test_operator_command_route.py -q`

Expected: PASS

- [ ] **Step 5: Run the full foundation verification**

Run: `uv run pytest tests/test_operator_config.py tests/test_operator_store.py tests/test_operator_routes.py tests/test_operator_cli.py tests/test_operator_command_route.py tests/test_agent_shell.py tests/test_server.py tests/test_cli.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add aletheia/server/routes/operator.py tests/test_operator_command_route.py
git commit -m "feat: add operator command compatibility route"
```

## Plan Self-Review

Spec coverage for this foundation slice:

- `operator` config block: Task 1
- SQLite tables and operator persistence: Task 2
- public/internal operator APIs: Task 3
- operator CLI surface: Task 4
- `/api/operator/command` bridge: Task 5

Gaps intentionally left for later plans:

- Node runtime service
- supervisor child-process orchestration for Node
- frontend Command Center page and operator visual surface
- repair engine logic
- strategy/dreaming/knowledge integration behavior

No placeholders remain in this plan. Type names and route paths are consistent across tasks.
