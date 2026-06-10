import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.server.app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    reset_db()


@pytest.mark.anyio
async def test_operator_status_defaults_when_no_heartbeat(client):
    resp = await client.get("/api/operator/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "operator"
    assert data["status"] == "offline"
    assert data["mode"] == "Observing"
    assert data["permission_mode"] == "ReadOnly"
    assert data["session_id"] == "operator-main"


@pytest.mark.anyio
async def test_operator_internal_heartbeat_roundtrip(client):
    resp = await client.post(
        "/api/operator/internal/heartbeat",
        json={
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
        },
    )

    assert resp.status_code == 200

    status = await client.get("/api/operator/status")
    data = status.json()
    assert data["status"] == "online"
    assert data["current_intent"] == "monitoring system state"
    assert data["last_seen_at"] is not None


@pytest.mark.anyio
async def test_operator_task_create_update_and_list(client):
    create = await client.post(
        "/api/operator/internal/tasks",
        json={
            "task_type": "diagnose_failure",
            "status": "running",
            "priority": "normal",
            "created_by": "operator_loop",
            "assigned_to": "operator",
            "input_data": {"entity_id": "run-1"},
        },
    )

    assert create.status_code == 200
    task_id = create.json()["id"]

    listing = await client.get("/api/operator/tasks")
    assert listing.status_code == 200
    assert listing.json()[0]["task_type"] == "diagnose_failure"

    detail = await client.get(f"/api/operator/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "running"

    update = await client.patch(
        f"/api/operator/internal/tasks/{task_id}",
        json={"status": "completed", "output_data": {"summary": "ok"}},
    )
    assert update.status_code == 200
    assert update.json()["status"] == "completed"
    assert update.json()["output_data"] == {"summary": "ok"}


@pytest.mark.anyio
async def test_operator_session_messages_and_session_detail(client):
    post = await client.post(
        "/api/operator/internal/messages",
        json={
            "session_id": "session-1",
            "role": "assistant",
            "content": {"text": "hello"},
            "message_type": "message",
            "related_event_id": None,
        },
    )

    assert post.status_code == 200

    messages = await client.get("/api/operator/sessions/session-1/messages")
    assert messages.status_code == 200
    assert messages.json()[0]["content"] == {"text": "hello"}

    sessions = await client.get("/api/operator/sessions")
    assert sessions.status_code == 200
    assert sessions.json()[0]["id"] == "session-1"

    detail = await client.get("/api/operator/sessions/session-1")
    assert detail.status_code == 200
    data = detail.json()
    assert data["session"]["id"] == "session-1"
    assert data["messages"][0]["role"] == "assistant"


@pytest.mark.anyio
async def test_operator_pause_resume_and_wake(client):
    pause = await client.post("/api/operator/pause")
    assert pause.status_code == 200
    assert pause.json()["paused"] is True

    status = await client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["paused"] is True

    wake = await client.post("/api/operator/wake")
    assert wake.status_code == 200
    assert wake.json()["paused"] is False

    resume = await client.post("/api/operator/resume")
    assert resume.status_code == 200
    assert resume.json()["paused"] is False
