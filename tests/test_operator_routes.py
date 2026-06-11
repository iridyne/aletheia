import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
    assert data["active_branch_id"] is None
    assert data["active_branch_kind"] is None
    assert data["active_branch_title"] is None


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
            "active_branch_id": "strategy-session-1",
            "active_branch_kind": "strategy_session",
            "active_branch_title": "Momentum A Review",
        },
    )

    assert resp.status_code == 200

    status = await client.get("/api/operator/status")
    data = status.json()
    assert data["status"] == "online"
    assert data["current_intent"] == "monitoring system state"
    assert data["last_seen_at"] is not None
    assert data["active_branch_id"] == "strategy-session-1"
    assert data["active_branch_kind"] == "strategy_session"
    assert data["active_branch_title"] == "Momentum A Review"


@pytest.mark.anyio
async def test_operator_active_branch_selection_updates_operator_status_truth(client):
    created = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "strategy-session-2",
            "user_input": "review strategy B",
            "kind": "strategy_session",
            "parent_session_id": "operator-main",
            "title": "Momentum B Review",
        },
    )
    assert created.status_code == 200

    heartbeat = await client.post(
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
    assert heartbeat.status_code == 200

    selected = await client.post(
        "/api/operator/active-branch",
        json={"session_id": "strategy-session-2", "selected_by": "command-center"},
    )
    assert selected.status_code == 200
    payload = selected.json()
    assert payload["active_branch_id"] == "strategy-session-2"
    assert payload["active_branch_kind"] == "strategy_session"
    assert payload["active_branch_title"] == "Momentum B Review"

    status = await client.get("/api/operator/status")
    assert status.status_code == 200
    data = status.json()
    assert data["active_branch_id"] == "strategy-session-2"
    assert data["active_branch_kind"] == "strategy_session"
    assert data["active_branch_title"] == "Momentum B Review"


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
async def test_operator_internal_session_create_preserves_kind_and_parent(client):
    created = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "dreaming-session-1",
            "user_input": "run dreaming for stale memory",
            "kind": "dreaming_session",
            "parent_session_id": "operator-main",
            "title": "Dreaming delegation",
        },
    )

    assert created.status_code == 200
    session = created.json()
    assert session["id"] == "dreaming-session-1"
    assert session["kind"] == "dreaming_session"
    assert session["parent_session_id"] == "operator-main"
    assert session["title"] == "Dreaming delegation"

    detail = await client.get("/api/operator/sessions/dreaming-session-1")
    assert detail.status_code == 200
    assert detail.json()["session"]["kind"] == "dreaming_session"


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


@pytest.mark.anyio
async def test_operator_internal_compact_creates_idempotent_daily_summary_artifact(client):
    target_date = datetime.now(timezone.utc).date().isoformat()

    message = await client.post(
        "/api/operator/internal/messages",
        json={
            "session_id": "operator-main",
            "role": "user",
            "content": {"text": "compact this day"},
            "message_type": "command",
            "related_event_id": None,
        },
    )
    assert message.status_code == 200

    first = await client.post(f"/api/operator/internal/compact?date={target_date}")
    assert first.status_code == 200
    first_data = first.json()
    assert first_data["date"] == target_date
    assert Path(first_data["path"]).exists()
    assert first_data["created"] is True

    detail = await client.get("/api/operator/sessions/operator-main")
    assert detail.status_code == 200
    artifacts = detail.json()["artifacts"]
    summary_artifacts = [a for a in artifacts if a["artifact_type"] == "operator_daily_summary"]
    assert len(summary_artifacts) == 1
    assert summary_artifacts[0]["content"]["date"] == target_date
    assert "summary_excerpt" in summary_artifacts[0]["content"]
    assert "sections" in summary_artifacts[0]["content"]
    assert "compact this day" in summary_artifacts[0]["content"]["summary_excerpt"]

    second = await client.post(f"/api/operator/internal/compact?date={target_date}")
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["artifact_id"] == first_data["artifact_id"]
    assert second_data["created"] is False

    events = await client.get("/api/operator/events")
    assert events.status_code == 200
    compact_events = [e for e in events.json() if e["event_type"] == "session_compacted"]
    assert len(compact_events) == 1


@pytest.mark.anyio
async def test_operator_internal_artifact_create_roundtrip(client):
    created = await client.post(
        "/api/operator/internal/artifacts",
        json={
            "task_id": None,
            "session_id": "operator-main",
            "artifact_type": "repair_decision",
            "title": "Repair decision for stale market data",
            "content": {
                "decision_id": "repair-health-market-data-stale",
                "classification": "auto_execute",
                "command": "create_diagnostic_todo",
            },
        },
    )

    assert created.status_code == 200
    artifact = created.json()
    assert artifact["artifact_type"] == "repair_decision"
    assert artifact["title"] == "Repair decision for stale market data"
    assert artifact["content"]["command"] == "create_diagnostic_todo"

    detail = await client.get("/api/operator/sessions/operator-main")
    assert detail.status_code == 200
    artifacts = detail.json()["artifacts"]
    assert any(item["id"] == artifact["id"] for item in artifacts)


@pytest.mark.anyio
async def test_operator_internal_commands_execute_roundtrip(client):
    executed = await client.post(
        "/api/operator/internal/commands/execute",
        json={
            "command_name": "get_operator_status",
            "session_id": "operator-main",
            "user_input": "show operator status",
            "parameters": {},
        },
    )

    assert executed.status_code == 200
    payload = executed.json()
    assert payload["command"] == "get_operator_status"
    assert payload["permitted"] is True
    assert payload["session_id"] == "operator-main"


@pytest.mark.anyio
async def test_operator_command_history_route_searches_across_sessions(client):
    from aletheia.agent_shell.audit import record_command
    from aletheia.agent_shell.operator_store import create_session_record

    create_session_record(
        session_id="operator-main",
        user_input="[operator session]",
        kind="command_session",
        parent_session_id=None,
        title="Operator Main",
    )
    create_session_record(
        session_id="strategy-session-1",
        user_input="Review strategy promotion",
        kind="strategy_session",
        parent_session_id="operator-main",
        title="Strategy Promotion Review",
    )

    record_command(
        "operator-main",
        "get_operator_status",
        {},
        True,
        "Operator is healthy and observing.",
    )
    record_command(
        "strategy-session-1",
        "propose_strategy_promotion",
        {"strategy_id": "strat-1", "target_state": "paper_passed"},
        True,
        "Created strategy promotion proposal for strat-1.",
    )

    listing = await client.get("/api/operator/commands?query=promotion")
    assert listing.status_code == 200
    payload = listing.json()
    assert len(payload) == 1
    assert payload[0]["command"] == "propose_strategy_promotion"
    assert payload[0]["session_id"] == "strategy-session-1"

    scoped = await client.get("/api/operator/commands?session_id=operator-main&query=healthy")
    assert scoped.status_code == 200
    scoped_payload = scoped.json()
    assert len(scoped_payload) == 1
    assert scoped_payload[0]["command"] == "get_operator_status"


@pytest.mark.anyio
async def test_operator_internal_repair_decision_route_persists_typed_artifact(client):
    created = await client.post(
        "/api/operator/internal/repair-decisions",
        json={
            "task_id": None,
            "session_id": "operator-main",
            "title": "Repair decision",
            "content": {
                "decision_id": "repair-health-1",
                "classification": "auto_execute",
                "command": "create_diagnostic_todo",
            },
        },
    )

    assert created.status_code == 200
    artifact = created.json()
    assert artifact["artifact_type"] == "repair_decision"
    assert artifact["content"]["command"] == "create_diagnostic_todo"


@pytest.mark.anyio
async def test_operator_internal_decision_and_command_intent_routes_persist_structured_artifacts(client):
    decision = await client.post(
        "/api/operator/internal/decisions",
        json={
            "task_id": None,
            "session_id": "operator-main",
            "title": "Operator decision",
            "content": {
                "decision_type": "promotion_review",
                "summary": "human review is required",
            },
        },
    )
    assert decision.status_code == 200
    assert decision.json()["artifact_type"] == "operator_decision"

    intent = await client.post(
        "/api/operator/internal/command-intents",
        json={
            "task_id": None,
            "session_id": "operator-main",
            "command_name": "propose_strategy_promotion",
            "user_input": "prepare strategy promotion proposal",
            "classification": "proposal_only",
            "parameters": {
                "strategy_id": "strat-1",
                "target_state": "paper_passed",
            },
        },
    )

    assert intent.status_code == 200
    artifact = intent.json()
    assert artifact["artifact_type"] == "command_intent"
    assert artifact["content"]["command_name"] == "propose_strategy_promotion"
    assert artifact["content"]["classification"] == "proposal_only"


@pytest.mark.anyio
async def test_operator_internal_context_query_defaults_to_session_kind_profile(client):
    created = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "strategy-session-1",
            "user_input": "evaluate paper strategy drift",
            "kind": "strategy_session",
            "parent_session_id": "operator-main",
            "title": "Strategy Evaluation",
        },
    )
    assert created.status_code == 200

    response = await client.post(
        "/api/operator/internal/context/query",
        json={
            "session_id": "strategy-session-1",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "strategy-session-1"
    assert data["session_kind"] == "strategy_session"
    assert data["profile"] == "trading_context"
    assert "trading" in data["sections"]
    assert "risk" in data["sections"]
    assert "strategies" in data["sections"]
    assert "operator_state" in data["context"]
    assert data["summary"]


@pytest.mark.anyio
async def test_operator_extension_routes_roundtrip_and_enable_gate(client):
    from aletheia.agent_shell.operator_store import (
        create_extension_record,
        create_extension_run,
        update_extension,
    )

    extension_id = create_extension_record(
        session_id="operator-main",
        extension_name="Explain Proposal",
        slash_command="/explain-proposal",
        description="Review the latest proposal explanation through a dedicated extension.",
        target_command="explain_proposal",
        manifest={
            "slash_command": "/explain-proposal",
            "target_command": "explain_proposal",
            "description": "Review the latest proposal explanation through a dedicated extension.",
            "sandbox_only": True,
            "requires_human_approval": True,
            "capabilities": ["operator_command_proxy"],
        },
        created_by="operator",
    )
    run_id = create_extension_run(
        extension_id=extension_id,
        session_id="operator-main",
        run_type="sandbox_test",
        status="passed",
        summary="Manifest checks passed.",
        input_data={"check_count": 3},
        output_data={"checks": ["slash_command", "target_command", "capabilities"]},
        scratch_path="data/agent_shell/scratch/explain-proposal.md",
    )
    update_extension(
        extension_id,
        status="proposed",
        latest_run_id=run_id,
        proposal_artifact_id="artifact-extension-1",
    )

    listing = await client.get("/api/operator/extensions")
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 1
    assert items[0]["id"] == extension_id
    assert items[0]["status"] == "proposed"

    detail = await client.get(f"/api/operator/extensions/{extension_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["extension"]["slash_command"] == "/explain-proposal"
    assert detail_payload["runs"][0]["status"] == "passed"

    enable = await client.post(
        f"/api/operator/extensions/{extension_id}/enable",
        json={"approved_by": "human-review"},
    )
    assert enable.status_code == 200
    enabled = enable.json()
    assert enabled["status"] == "enabled"
    assert enabled["enabled_by"] == "human-review"
