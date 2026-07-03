"""Integration tests for the FastAPI server."""

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_db()


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    # /api/health now reports real health: "healthy" or "degraded".
    assert data["status"] in ("healthy", "degraded")
    assert "version" in data


@pytest.mark.anyio
async def test_status(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["live_state"] == "live_disabled"
    assert data["permission_mode"] == "Paper"
    assert data["exchange_mode"] == "demo"


@pytest.mark.anyio
async def test_knowledge_route_accepts_learning_item_types(client):
    for item_type in ("trade_case_study", "guard_candidate", "trading_guard", "trading_memo"):
        resp = await client.get(f"/api/knowledge?type={item_type}")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_knowledge_route_rejects_unknown_item_type(client):
    resp = await client.get("/api/knowledge?type=made_up_type")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_lesson_management_actions(client):
    from aletheia.learning.lessons import LessonStore

    store = LessonStore()
    lesson = store.create(
        summary="BTC long momentum lesson",
        source_type="trade_case_study",
        source_id="case-1",
    )

    accepted = await client.post(f"/api/persistence/lessons/{lesson.lesson_id}/accept")
    assert accepted.status_code == 200
    accepted_body = accepted.json()
    assert accepted_body["status"] == "accepted"
    assert accepted_body["activated_at"]
    assert accepted_body["dry_run_until"]

    rejected = await client.post(f"/api/persistence/lessons/{lesson.lesson_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    archived = await client.post(f"/api/persistence/lessons/{lesson.lesson_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    from aletheia.storage.event_store import get_event_store
    events = get_event_store().list_by_aggregate("lesson", lesson.lesson_id)
    assert [e.event_type for e in events] == [
        "LESSON_CREATED",
        "LESSON_ACCEPTED",
        "LESSON_REJECTED",
        "LESSON_ARCHIVED",
    ]


@pytest.mark.anyio
async def test_lesson_management_action_rejects_unknown_lesson(client):
    resp = await client.post("/api/persistence/lessons/missing-lesson/accept")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_guard_candidate_can_be_listed_and_manually_promoted(client):
    from aletheia.knowledge.manager import create_guard_candidate
    from aletheia.knowledge.guards import load_trading_guards
    from aletheia.storage import repositories as repo
    from aletheia.storage.event_store import get_event_store

    candidate = create_guard_candidate(
        title="Avoid weak BTC longs",
        content="Repeated weak BTC long entries lost money.",
        metadata={
            "guard_id": "manual-btc-long-guard",
            "title": "Avoid weak BTC longs",
            "status": "candidate",
            "scope": "paper_entry",
            "scanner_family": "momentum",
            "direction": "long",
            "severity": "warning",
        },
    )

    listed = await client.get("/api/knowledge/guard-candidates")
    assert listed.status_code == 200
    assert any(g["guard_id"] == "manual-btc-long-guard" for g in listed.json())

    promoted = await client.post("/api/knowledge/guard-candidates/manual-btc-long-guard/promote")
    assert promoted.status_code == 200
    promoted_body = promoted.json()
    assert promoted_body["status"] == "active"
    assert promoted_body["enforcement"] == "blocking"
    assert promoted_body["block_candidate"] is True
    assert promoted_body["score_penalty"] == 10.0

    assert not any(g.guard_id == "manual-btc-long-guard" for g in load_trading_guards(status="candidate"))
    assert any(g.guard_id == "manual-btc-long-guard" for g in load_trading_guards(status="active"))
    row = repo._select_all("knowledge_items", where="id = ?", params=(candidate.id,), limit=1)[0]
    assert row["status"] == "deprecated"
    assert row["confidence"] == "deprecated"

    events = get_event_store().list_by_aggregate("trading_guard", "manual-btc-long-guard")
    assert [e.event_type for e in events] == ["TRADING_GUARD_PROMOTED"]
    assert events[0].actor_type == "operator"

    second_promote = await client.post("/api/knowledge/guard-candidates/manual-btc-long-guard/promote")
    assert second_promote.status_code == 404


@pytest.mark.anyio
async def test_guard_candidate_promote_rejects_missing_guard(client):
    resp = await client.post("/api/knowledge/guard-candidates/missing-guard/promote")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_pause_resume(client, monkeypatch):
    from aletheia.agent_runtime import resume_gate

    async def fake_resume(*, actor: str, source: str, cause: str = "manual_resume", max_retries=None):
        return {"allowed": True, "reason": None, "checked": 1, "results": []}

    monkeypatch.setattr(resume_gate, "resume_if_llm_available", fake_resume)

    resp = await client.post("/api/gateway/pause")
    assert resp.status_code == 200
    assert resp.json()["paused"] is True

    resp = await client.get("/api/status")
    assert resp.json()["paused"] is True

    resp = await client.post("/api/gateway/resume")
    assert resp.status_code == 200
    assert resp.json()["paused"] is False


@pytest.mark.anyio
async def test_gateway_resume_blocks_when_llm_unavailable(client, monkeypatch):
    from aletheia.agent_runtime import resume_gate
    from aletheia.config.loader import load_config

    async def fake_resume(*, actor: str, source: str, cause: str = "manual_resume", max_retries=None):
        return {
            "allowed": False,
            "reason": "llm_unavailable",
            "checked": 1,
            "results": [{"provider": "anthropic_compatible", "usable": False, "error": "429"}],
        }

    monkeypatch.setattr(resume_gate, "resume_if_llm_available", fake_resume)

    pause = await client.post("/api/gateway/pause")
    assert pause.status_code == 200

    resp = await client.post("/api/gateway/resume")

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "llm_unavailable"
    assert load_config().runtime.paused is True


@pytest.mark.anyio
async def test_permission_mode_change(client):
    resp = await client.post("/api/settings/permission-mode", json={"mode": "Paper"})
    assert resp.status_code == 200
    assert resp.json()["new"] == "Paper"

    resp = await client.get("/api/status")
    assert resp.json()["permission_mode"] == "Paper"


@pytest.mark.anyio
async def test_permission_mode_invalid(client):
    resp = await client.post("/api/settings/permission-mode", json={"mode": "invalid"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_exchange_mode_change(client):
    resp = await client.post("/api/settings/exchange-mode", json={"mode": "live"})
    assert resp.status_code == 200
    assert resp.json()["new"] == "live"


@pytest.mark.anyio
async def test_settings_get(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"]["dashboard_port"] == 8787
    assert data["risk"]["max_daily_loss_pct"] == 1.0
