from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.server.app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def operator_env(tmp_path, monkeypatch):
    import sqlite3
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
    return tmp_path


@pytest.mark.anyio
async def test_operator_command_uses_existing_agent_shell_runtime(operator_env):
    fake_response = {
        "intent": "get_system_status",
        "risk_level": "read",
        "response": "System healthy",
        "action": None,
        "needs_confirmation": False,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "aletheia.agent_shell.runtime.AgentShellRuntime.handle",
            new=AsyncMock(return_value=fake_response),
        ) as mocked:
            resp = await client.post("/api/operator/command", json={"input": "status"})

    assert resp.status_code == 200
    assert resp.json()["response"] == "System healthy"
    mocked.assert_awaited_once_with("status")


@pytest.mark.anyio
async def test_operator_command_persists_main_session_messages(operator_env):
    fake_response = {
        "intent": "get_system_status",
        "risk_level": "read",
        "response": "System healthy",
        "action": None,
        "needs_confirmation": False,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "aletheia.agent_shell.runtime.AgentShellRuntime.handle",
            new=AsyncMock(return_value=fake_response),
        ):
            resp = await client.post("/api/operator/command", json={"input": "status"})

    assert resp.status_code == 200

    from aletheia.agent_shell.operator_store import list_messages

    messages = list_messages("operator-main")
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == {"text": "status"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == {"text": "System healthy", "intent": "get_system_status"}
