from unittest.mock import AsyncMock, patch
import json

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.server.app import app
from aletheia.agent_shell.runtime import CommandResponse


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

    fake_response = CommandResponse(
        intent="get_system_status",
        risk_level="read",
        response="System healthy",
        action=None,
        needs_confirmation=False,
        permitted=True,
        executed=True,
    )

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
        confirmed_command=None,
    )


@pytest.mark.anyio
async def test_operator_command_persists_main_session_messages(operator_env):
    fake_response = CommandResponse(
        intent="get_system_status",
        risk_level="read",
        response="System healthy",
        action=None,
        needs_confirmation=False,
        permitted=True,
        executed=True,
    )

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

    fake_response = CommandResponse(
        intent="get_failed_tasks",
        risk_level="read",
        response="Strategy session continued",
        action=None,
        needs_confirmation=False,
        permitted=True,
        executed=True,
        session_id="strategy-session-1",
    )

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
        confirmed_command=None,
    )
    messages = list_messages("strategy-session-1")
    assert len(messages) == 2
    assert messages[0]["content"] == {"text": "continue this review"}
    assert messages[1]["content"]["text"] == "Strategy session continued"
    assert messages[1]["content"]["intent"] == "get_failed_tasks"
    assert messages[1]["content"]["command"] == "get_failed_tasks"
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
            "/api/operator/internal/commands/execute",
            json={
                "command_name": "create_diagnostic_todo",
                "session_id": "diagnostic-session-1",
                "user_input": "Create a diagnostic todo for stale market data.",
                "parameters": {
                    "title": "Market data stale",
                    "content": "Inspect market_data_stale:45m before trusting automation. Check provider health and websocket reconnect logic.",
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
            "/api/operator/internal/commands/execute",
            json={
                "command_name": "create_diagnostic_todo",
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


def _event_types(session_id: str) -> list[str]:
    from aletheia.agent_shell.operator_store import list_events

    return [event["event_type"] for event in list_events(limit=50, session_id=session_id)]


@pytest.mark.anyio
async def test_unconfirmed_pause_is_recorded_as_pending_not_executed(operator_env, monkeypatch):
    """#1607: the gate answer is 200 + not run; it must not be logged as command_executed."""
    import aletheia.agent_shell.intent as intent_mod
    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import list_commands

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "PaperAuto"
    cfg.runtime.paused = False
    loader.save_config(cfg)

    async def fake_classify(text, summary):
        return intent_mod.CommandIntent(command="pause_agents", parameters={}, confidence=0.9)

    monkeypatch.setattr(intent_mod, "classify_intent", fake_classify)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pending = await client.post(
            "/api/operator/command", json={"input": "pause agents", "session_id": "s-pending"}
        )
        assert pending.status_code == 200
        assert pending.json()["executed"] is False
        assert pending.json()["needs_confirmation"] is True
        assert loader.load_config().runtime.paused is False
        assert _event_types("s-pending").count("command_executed") == 0
        assert "command_confirmation_required" in _event_types("s-pending")
        assert [c["permitted"] for c in list_commands("s-pending")] == [False]

        confirmed = await client.post(
            "/api/operator/command",
            json={"input": "pause agents", "session_id": "s-pending", "confirmed_command": "pause_agents"},
        )
    body = confirmed.json()
    assert body["executed"] is True
    assert body["needs_confirmation"] is False
    assert loader.load_config().runtime.paused is True
    assert _event_types("s-pending").count("command_executed") == 1


@pytest.mark.anyio
async def test_machine_caller_gated_command_is_pending_not_executed(operator_env):
    """#1974: a machine caller's gated command is permitted but never run."""
    import aletheia.config.loader as loader

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "PaperAuto"
    loader.save_config(cfg)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/operator/internal/commands/execute",
            json={"command_name": "rerun_failed_agent_task", "session_id": "s-rerun", "parameters": {"agent_name": "post_trade_reviewer"}},
        )
    data = resp.json()
    assert data["permitted"] is True
    assert data["executed"] is False
    assert data["needs_confirmation"] is True
    assert "command_executed" not in _event_types("s-rerun")
    assert "command_confirmation_required" in _event_types("s-rerun")


@pytest.mark.anyio
async def test_handler_error_is_recorded_as_failed_not_executed(operator_env):
    """#1974: a handler that ran and returned a top-level error did not succeed."""
    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import list_commands

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/operator/internal/commands/execute",
            json={"command_name": "explain_agent_run", "session_id": "s-missing-run", "parameters": {"run_id": "run-does-not-exist"}},
        )
    data = resp.json()
    assert data["permitted"] is True
    assert data["executed"] is False
    assert data["result"] == {"error": "Agent run 'run-does-not-exist' not found."}
    assert "command_executed" not in _event_types("s-missing-run")
    assert "command_failed" in _event_types("s-missing-run")
    assert [c["permitted"] for c in list_commands("s-missing-run")] == [False]


@pytest.mark.anyio
async def test_reconcile_handler_reports_suspended_run_as_error(monkeypatch):
    """#1974: detector-unavailable reconcile is suspended with errors, not 'complete'."""
    import aletheia.portfolio.position_reconciler as reconciler
    from aletheia.agent_shell.commands import reconcile_positions
    from aletheia.agent_shell.executor import handler_succeeded

    summary = {
        "account_mode": "bitget_demo",
        "sync_status": "degraded",
        "counts": {"managed": 2, "external": 0, "orphaned": 0, "degraded": 0},
        "adopted": [],
        "closed_orphans": [],
        "errors": ["reconciliation integrity detector unavailable"],
        "suspended": True,
    }
    monkeypatch.setattr(reconciler, "run_reconcile", lambda: summary)
    result = await reconcile_positions({})
    assert not handler_succeeded(result)
    assert "incomplete" in result["error"]
    assert result["summary"] is summary
