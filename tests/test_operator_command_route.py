from unittest.mock import AsyncMock, patch
import json

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
    import aletheia.config.loader as loader

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "Research"
    loader.save_config(cfg)

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
    mocked.assert_awaited_once_with(
        "status",
        permission_mode="Research",
        permission_scope="operator",
        session_id="operator-main",
    )


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
    assert messages[1]["content"]["text"] == "System healthy"
    assert messages[1]["content"]["intent"] == "get_system_status"
    assert messages[1]["content"]["command"] == "get_system_status"
    assert messages[1]["content"]["risk_level"] == "read"
    assert messages[1]["content"]["needs_confirmation"] is False
    assert messages[1]["content"]["permitted"] is True
    assert messages[1]["content"]["action"] is None


@pytest.mark.anyio
async def test_operator_command_can_continue_selected_child_session(operator_env):
    from aletheia.agent_shell.operator_store import create_session_record, list_messages

    create_session_record(
        session_id="strategy-session-1",
        user_input="review strategy",
        kind="strategy_session",
        parent_session_id="operator-main",
        title="Strategy Review",
    )

    fake_response = {
        "intent": "get_strategy_lifecycle",
        "risk_level": "read",
        "response": "Strategy session continued",
        "action": None,
        "needs_confirmation": False,
        "session_id": "strategy-session-1",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "aletheia.agent_shell.runtime.AgentShellRuntime.handle",
            new=AsyncMock(return_value=fake_response),
        ) as mocked:
            resp = await client.post(
                "/api/operator/command",
                json={"input": "continue this review", "session_id": "strategy-session-1"},
            )

    assert resp.status_code == 200
    mocked.assert_awaited_once_with(
        "continue this review",
        permission_mode="ReadOnly",
        permission_scope="operator",
        session_id="strategy-session-1",
    )
    messages = list_messages("strategy-session-1")
    assert len(messages) == 2
    assert messages[0]["content"] == {"text": "continue this review"}
    assert messages[1]["content"]["text"] == "Strategy session continued"
    assert messages[1]["content"]["intent"] == "get_strategy_lifecycle"
    assert messages[1]["content"]["command"] == "get_strategy_lifecycle"
    assert messages[1]["content"]["risk_level"] == "read"
    assert messages[1]["content"]["needs_confirmation"] is False
    assert messages[1]["content"]["permitted"] is True
    assert messages[1]["content"]["action"] is None


@pytest.mark.anyio
async def test_operator_internal_typed_command_executes_with_existing_registry_and_operator_scope(
    operator_env,
):
    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import list_commands, list_events, list_messages
    from aletheia.storage.repositories import list_knowledge_items

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "Research"
    loader.save_config(cfg)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/operator/internal/commands/create_diagnostic_todo",
            json={
                "session_id": "diagnostic-session-1",
                "user_input": "Create a diagnostic todo for stale market data.",
                "parameters": {
                    "title": "Market data stale",
                    "content": "Inspect market_data_stale:45m before trusting automation.",
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["command"] == "create_diagnostic_todo"
    assert data["permitted"] is True
    assert data["result"]["created"] is True
    assert data["result"]["title"] == "Market data stale"
    todo_path = operator_env / "agent_shell" / "todos"
    todo_files = list(todo_path.glob("*.md"))
    assert len(todo_files) == 1
    todo_body = todo_files[0].read_text(encoding="utf-8")
    assert "Market data stale" in todo_body
    assert "Inspect market_data_stale:45m before trusting automation." in todo_body

    commands = list_commands("diagnostic-session-1")
    assert len(commands) == 1
    assert commands[0]["command"] == "create_diagnostic_todo"
    assert commands[0]["permitted"] is True

    messages = list_messages("diagnostic-session-1")
    assert len(messages) == 2
    assert messages[0]["message_type"] == "command"
    assert messages[1]["message_type"] == "response"

    event_types = [event["event_type"] for event in list_events(limit=20, session_id="diagnostic-session-1")]
    assert "command_received" in event_types
    assert "command_executed" in event_types

    notes = list_knowledge_items(item_type="research_note", limit=10)
    assert any(item["title"] == "[DIAG] Market data stale" for item in notes)
    session_log = operator_env / "agent_shell" / "sessions" / "diagnostic-session-1.jsonl"
    assert session_log.exists()
    entries = [json.loads(line) for line in session_log.read_text(encoding="utf-8").splitlines()]
    assert any(entry.get("type") == "command" for entry in entries)


@pytest.mark.anyio
async def test_operator_internal_typed_command_reports_permission_denial_without_mutation(
    operator_env,
):
    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import list_commands, list_events
    from aletheia.storage.repositories import list_knowledge_items

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "Research"
    loader.save_config(cfg)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/operator/internal/commands/create_diagnostic_todo",
            json={
                "session_id": "diagnostic-session-denied",
                "user_input": "Create a diagnostic todo for stale market data.",
                "parameters": {
                    "title": "Market data stale",
                    "content": "Inspect market_data_stale:45m before trusting automation.",
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["command"] == "create_diagnostic_todo"
    assert data["permitted"] is False
    assert "worker/global safety" in data["response"]
    assert data["result"] is None

    commands = list_commands("diagnostic-session-denied")
    assert len(commands) == 1
    assert commands[0]["command"] == "create_diagnostic_todo"
    assert commands[0]["permitted"] is False

    event_types = [event["event_type"] for event in list_events(limit=20, session_id="diagnostic-session-denied")]
    assert "command_received" in event_types
    assert "command_rejected" in event_types

    notes = list_knowledge_items(item_type="research_note", limit=10)
    assert not any(item["title"] == "[DIAG] Market data stale" for item in notes)


@pytest.mark.anyio
async def test_operator_command_route_executes_enabled_extension_from_command_center(operator_env):
    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_extension_record, list_events, list_messages
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=65000))
    create_extension_record(
        session_id="operator-main",
        extension_name="Summarize Market",
        slash_command="/summarize-market",
        description="Summarize the latest market snapshot via slash command.",
        target_command="get_market_snapshot",
        manifest={
            "slash_command": "/summarize-market",
            "target_command": "get_market_snapshot",
            "description": "Summarize the latest market snapshot via slash command.",
            "default_parameters": {"symbol": "BTC/USDT"},
            "capabilities": ["operator_command_proxy"],
            "sandbox_only": True,
            "requires_human_approval": True,
        },
        status="enabled",
        created_by="operator",
        enabled_by="human-review",
    )

    with patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/operator/command", json={"input": "/summarize-market"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "get_market_snapshot"
    assert "BTC/USDT" in data["response"]

    messages = list_messages("operator-main")
    assert messages[-2]["content"] == {"text": "/summarize-market"}
    assert messages[-1]["content"]["intent"] == "get_market_snapshot"

    event_types = [event["event_type"] for event in list_events(limit=20)]
    assert "extension_invoked" in event_types
