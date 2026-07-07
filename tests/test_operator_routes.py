import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from aletheia.server.app import app
from aletheia.agent_shell.context import sections_for_profile
from aletheia.agent_shell.intent import CommandIntent


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
async def test_operator_public_branch_create_generates_child_session_and_selects_it(client):
    created = await client.post(
        "/api/operator/branches",
        json={
            "title": "Investigate stale fills",
            "kind": "diagnostic_session",
            "user_input": "investigate stale fills on BTC/USDT",
            "selected_by": "command-center",
        },
    )

    assert created.status_code == 200
    session = created.json()
    assert session["kind"] == "diagnostic_session"
    assert session["parent_session_id"] == "operator-main"
    assert session["title"] == "Investigate stale fills"
    assert session["id"].startswith("diagnostic-session-")

    detail = await client.get(f"/api/operator/sessions/{session['id']}")
    assert detail.status_code == 200
    assert detail.json()["session"]["user_input"] == "investigate stale fills on BTC/USDT"
    messages = detail.json()["messages"]
    assert len(messages) == 2
    assert messages[0]["message_type"] == "context"
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"]["profile"] == "repair_context"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == {"text": "investigate stale fills on BTC/USDT"}

    status = await client.get("/api/operator/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["active_branch_id"] == session["id"]
    assert payload["active_branch_kind"] == "diagnostic_session"
    assert payload["active_branch_title"] == "Investigate stale fills"

    events = await client.get(f"/api/operator/events?session_id={session['id']}")
    assert events.status_code == 200
    event_types = {event["event_type"] for event in events.json()}
    assert "branch_created" in event_types
    assert "active_branch_selected" in event_types


@pytest.mark.anyio
async def test_operator_public_branch_create_supports_nested_parent_session(client):
    parent = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "strategy-session-parent",
            "user_input": "review strategy root",
            "kind": "strategy_session",
            "parent_session_id": "operator-main",
            "title": "Momentum Root Review",
        },
    )
    assert parent.status_code == 200

    created = await client.post(
        "/api/operator/branches",
        json={
            "title": "Review nested drawdown",
            "kind": "review_session",
            "user_input": "review nested drawdown on momentum branch",
            "parent_session_id": "strategy-session-parent",
            "selected_by": "command-center",
        },
    )

    assert created.status_code == 200
    session = created.json()
    assert session["kind"] == "review_session"
    assert session["parent_session_id"] == "strategy-session-parent"
    assert session["title"] == "Review nested drawdown"

    status = await client.get("/api/operator/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["active_branch_id"] == session["id"]
    assert payload["active_branch_kind"] == "review_session"
    assert payload["active_branch_title"] == "Review nested drawdown"


@pytest.mark.anyio
async def test_operator_active_branch_selection_accepts_nested_branch_session(client):
    created = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "strategy-session-parent-2",
            "user_input": "review strategy parent",
            "kind": "strategy_session",
            "parent_session_id": "operator-main",
            "title": "Momentum Parent Review",
        },
    )
    assert created.status_code == 200
    nested = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "review-session-child-2",
            "user_input": "review nested branch",
            "kind": "review_session",
            "parent_session_id": "strategy-session-parent-2",
            "title": "Nested Trade Review",
        },
    )
    assert nested.status_code == 200

    selected = await client.post(
        "/api/operator/active-branch",
        json={"session_id": "review-session-child-2", "selected_by": "command-center"},
    )
    assert selected.status_code == 200
    payload = selected.json()
    assert payload["active_branch_id"] == "review-session-child-2"
    assert payload["active_branch_kind"] == "review_session"
    assert payload["active_branch_title"] == "Nested Trade Review"


@pytest.mark.anyio
async def test_operator_public_branch_create_rejects_unknown_kind(client):
    created = await client.post(
        "/api/operator/branches",
        json={
            "title": "Bad branch",
            "kind": "unknown_session",
        },
    )

    assert created.status_code == 400
    assert "Unknown operator session kind" in created.text


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
async def test_patch_evicted_task_is_idempotent(client):
    """PATCH completed/failed on a non-existent task returns 200 (no-op).
    This prevents the operator loop from entering Degraded mode when it tries
    to close a task that was evicted after a gateway restart."""
    ghost_id = "00000000-0000-0000-0000-000000000000"

    for terminal_status in ("completed", "failed"):
        r = await client.patch(
            f"/api/operator/internal/tasks/{ghost_id}",
            json={"status": terminal_status},
        )
        assert r.status_code == 200, f"Expected 200 for {terminal_status}, got {r.status_code}"
        body = r.json()
        assert body["id"] == ghost_id
        assert body["status"] == terminal_status
        assert body.get("evicted") is True

    # Non-terminal status on a missing task still returns 404
    r = await client.patch(
        f"/api/operator/internal/tasks/{ghost_id}",
        json={"status": "running"},
    )
    assert r.status_code == 404


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
async def test_operator_pause_resume_and_wake(client, monkeypatch):
    from aletheia.agent_runtime import resume_gate

    async def fake_resume(*, actor: str, source: str, cause: str = "manual_resume"):
        return {"allowed": True, "reason": None, "checked": 1, "results": []}

    monkeypatch.setattr(resume_gate, "resume_if_llm_available", fake_resume)

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
async def test_operator_resume_and_wake_block_when_llm_unavailable(client, monkeypatch):
    from aletheia.agent_runtime import resume_gate
    from aletheia.config.loader import load_config

    async def fake_resume(*, actor: str, source: str, cause: str = "manual_resume"):
        return {
            "allowed": False,
            "reason": "llm_unavailable",
            "checked": 1,
            "results": [{"provider": "anthropic_compatible", "usable": False, "error": "429"}],
        }

    monkeypatch.setattr(resume_gate, "resume_if_llm_available", fake_resume)

    pause = await client.post("/api/operator/pause")
    assert pause.status_code == 200

    wake = await client.post("/api/operator/wake")
    assert wake.status_code == 409
    assert wake.json()["detail"]["reason"] == "llm_unavailable"
    assert load_config().runtime.paused is True

    resume = await client.post("/api/operator/resume")
    assert resume.status_code == 409
    assert resume.json()["detail"]["reason"] == "llm_unavailable"
    assert load_config().runtime.paused is True


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
async def test_operator_command_preview_returns_authoritative_typed_command_truth(client):
    response = await client.post(
        "/api/operator/command-preview",
        json={"input": "show the operator heartbeat"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "operator-main"
    assert payload["classification"] == "typed_command"
    assert payload["intent"] == "get_operator_status"
    assert payload["allowed"] is True
    assert payload["permission_scope"] == "operator"
    assert payload["permission_mode"] == "ReadOnly"
    assert payload["command"]["name"] == "get_operator_status"
    assert payload["command"]["risk_class"] == "read"
    assert payload["command"]["operator_permission_required"] == "ReadOnly"


@pytest.mark.anyio
async def test_operator_command_preview_surfaces_permission_denial_before_execution(client):
    response = await client.post(
        "/api/operator/command-preview",
        json={"input": "pause all agents"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == "typed_command"
    assert payload["intent"] == "pause_agents"
    assert payload["allowed"] is False
    assert "PaperAuto" in payload["denial_reason"]
    assert payload["command"]["name"] == "pause_agents"
    assert payload["command"]["risk_class"] == "controlled_operational"
    assert payload["command"]["requires_confirmation"] is True


@pytest.mark.anyio
async def test_operator_command_preview_accounts_for_worker_global_safety(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "PaperAuto"
    save_config(cfg)

    response = await client.post(
        "/api/operator/command-preview",
        json={"input": "resume agents please"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == "typed_command"
    assert payload["intent"] == "resume_agents"
    assert payload["allowed"] is False
    assert payload["worker_allowed"] is False
    assert payload["worker_permission_mode"] == "Observe"
    assert payload["worker_denial_reason"].startswith("Command 'resume_agents' requires worker permission mode 'Paper'")
    assert payload["denial_reason"].startswith("This command is currently blocked by worker/global safety.")
    assert payload["command"]["worker_permission_required"] == "Paper"
    assert payload["command"]["operator_permission_required"] == "PaperAuto"


@pytest.mark.anyio
async def test_operator_command_preview_uses_selected_session_kind_context_profile(client):
    created = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "diagnostic-preview-session",
            "user_input": "Investigate stale market data.",
            "kind": "diagnostic_session",
            "parent_session_id": "operator-main",
            "title": "Diagnostic Preview Session",
        },
    )
    assert created.status_code == 200

    captured_sections: list[list[str]] = []

    def fake_build_app_context(sections=None):
        captured_sections.append(list(sections or []))
        return {"system": {"healthy": False, "issues": ["market_data_stale:45m"]}}

    with patch(
        "aletheia.server.routes.operator.build_app_context",
        side_effect=fake_build_app_context,
    ), patch(
        "aletheia.server.routes.operator.context_summary_text",
        return_value="repair summary",
    ), patch(
        "aletheia.agent_shell.intent.classify_intent",
        new_callable=AsyncMock,
        return_value=CommandIntent(command="query", parameters={}, confidence=0.9),
    ):
        response = await client.post(
            "/api/operator/command-preview",
            json={
                "input": "what should this diagnostic branch do next?",
                "session_id": "diagnostic-preview-session",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == "query"
    assert payload["intent"] == "query"
    assert captured_sections == [sections_for_profile("repair_context")]


@pytest.mark.anyio
async def test_operator_command_preview_includes_recent_session_context_and_trace(client):
    created = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "strategy-preview-session",
            "user_input": "Review promotion blockers for Momentum A.",
            "kind": "strategy_session",
            "parent_session_id": "operator-main",
            "title": "Momentum A Review",
        },
    )
    assert created.status_code == 200

    await client.post(
        "/api/operator/internal/messages",
        json={
            "session_id": "strategy-preview-session",
            "role": "assistant",
            "message_type": "context",
            "content": {
                "text": "Strategy branch context: Momentum A is under review until promotion blockers clear."
            },
        },
    )
    await client.post(
        "/api/operator/internal/messages",
        json={
            "session_id": "strategy-preview-session",
            "role": "assistant",
            "message_type": "response",
            "content": {
                "text": "Previous strategy result: do not widen live-risk permissions for Momentum A yet."
            },
        },
    )

    captured_summary: dict[str, str] = {}

    async def fake_classify_intent(text, summary):
        captured_summary["value"] = summary
        return CommandIntent(command="query", parameters={}, confidence=0.9)

    response = None
    with patch(
        "aletheia.agent_shell.intent.classify_intent",
        side_effect=fake_classify_intent,
    ):
        response = await client.post(
            "/api/operator/command-preview",
            json={
                "input": "what is this strategy branch still waiting on?",
                "session_id": "strategy-preview-session",
            },
        )

    assert response is not None
    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == "query"
    assert payload["intent"] == "query"
    assert "Strategy branch context: Momentum A is under review until promotion blockers clear." in captured_summary["value"]
    assert "Previous strategy result: do not widen live-risk permissions for Momentum A yet." in captured_summary["value"]


@pytest.mark.anyio
async def test_operator_command_preview_includes_parent_branch_trace_for_nested_session(client):
    created_parent = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "strategy-parent-preview",
            "user_input": "Investigate Momentum root.",
            "kind": "strategy_session",
            "parent_session_id": "operator-main",
            "title": "Momentum Root",
        },
    )
    assert created_parent.status_code == 200
    created_child = await client.post(
        "/api/operator/internal/sessions",
        json={
            "id": "review-child-preview",
            "user_input": "Review nested drawdown branch.",
            "kind": "review_session",
            "parent_session_id": "strategy-parent-preview",
            "title": "Nested Drawdown Review",
        },
    )
    assert created_child.status_code == 200

    await client.post(
        "/api/operator/internal/messages",
        json={
            "session_id": "strategy-parent-preview",
            "role": "assistant",
            "message_type": "context",
            "content": {
                "text": "Parent preview context: Momentum root still controls promotion sequencing."
            },
        },
    )
    await client.post(
        "/api/operator/internal/messages",
        json={
            "session_id": "strategy-parent-preview",
            "role": "assistant",
            "message_type": "response",
            "content": {
                "text": "Parent preview result: clear the promotion backlog before asking for live-candidate escalation."
            },
        },
    )

    captured_summary: dict[str, str] = {}

    async def fake_classify_intent(text, summary):
        captured_summary["value"] = summary
        return CommandIntent(command="query", parameters={}, confidence=0.9)

    response = None
    with patch(
        "aletheia.agent_shell.intent.classify_intent",
        side_effect=fake_classify_intent,
    ):
        response = await client.post(
            "/api/operator/command-preview",
            json={
                "input": "what is this nested review branch blocked on?",
                "session_id": "review-child-preview",
            },
        )

    assert response is not None
    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == "query"
    assert payload["intent"] == "query"
    assert "Parent preview context: Momentum root still controls promotion sequencing." in captured_summary["value"]
    assert "Parent preview result: clear the promotion backlog before asking for live-candidate escalation." in captured_summary["value"]


@pytest.mark.anyio
async def test_operator_command_persists_structured_response_metadata_for_result_cards(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "PaperAuto"
    save_config(cfg)
    baseline = await client.get("/api/operator/sessions/operator-main")
    baseline_ids = (
        {message["id"] for message in baseline.json()["messages"]}
        if baseline.status_code == 200
        else set()
    )

    response = await client.post(
        "/api/operator/command",
        json={"input": "resume agents please", "session_id": "operator-main"},
    )
    assert response.status_code == 200
    assert response.json()["intent"] == "resume_agents"

    detail = await client.get("/api/operator/sessions/operator-main")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assistant_response = next(
        message
        for message in messages
        if message["id"] not in baseline_ids
        and message["message_type"] == "response"
        and message.get("content", {}).get("intent") == "resume_agents"
    )
    assert assistant_response["message_type"] == "response"
    assert assistant_response["content"]["intent"] == "resume_agents"
    assert assistant_response["content"]["command"] == "resume_agents"
    assert assistant_response["content"]["risk_level"] == "operational"
    assert assistant_response["content"]["needs_confirmation"] is True
    # #67 fix: the first call is permitted-but-non-executing when confirmation is
    # required. `permitted` reflects that the AUTHORIZATION check passed; the
    # absence of `action` reflects that no state changed (previously this would
    # have set paused=False under the LLM's nose). A follow-up call with
    # confirm=true is required to actually resume — see B7-trust regression.
    assert assistant_response["content"]["permitted"] is True
    assert assistant_response["content"].get("action") in (None, {})


@pytest.mark.anyio
async def test_operator_command_persists_blocked_result_metadata_for_result_cards(client):
    baseline = await client.get("/api/operator/sessions/operator-main")
    baseline_ids = (
        {message["id"] for message in baseline.json()["messages"]}
        if baseline.status_code == 200
        else set()
    )

    response = await client.post(
        "/api/operator/command",
        json={"input": "pause all agents", "session_id": "operator-main"},
    )
    assert response.status_code == 200
    assert response.json()["intent"] == "pause_agents"

    detail = await client.get("/api/operator/sessions/operator-main")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assistant_response = next(
        message
        for message in messages
        if message["id"] not in baseline_ids
        and message["message_type"] == "response"
        and message.get("content", {}).get("intent") == "pause_agents"
    )
    assert assistant_response["message_type"] == "response"
    assert assistant_response["content"]["intent"] == "pause_agents"
    assert assistant_response["content"]["command"] == "pause_agents"
    assert assistant_response["content"]["permitted"] is False
    assert assistant_response["content"]["risk_level"] == "operational"
    assert assistant_response["content"]["needs_confirmation"] is True
    assert assistant_response["content"]["action"] is None


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
    from aletheia.storage.sqlite import get_db

    assert get_db().in_transaction is False
