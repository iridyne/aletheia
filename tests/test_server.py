"""Integration tests for the FastAPI server."""

import logging

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
    # /api/health reports tri-state health: "healthy", "degraded", or "initializing".
    assert data["status"] in ("healthy", "degraded", "initializing")
    assert "version" in data


@pytest.mark.anyio
async def test_status(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["live_state"] == "live_disabled"
    assert data["permission_mode"] == "Paper"
    assert data["exchange_mode"] == "paper"


@pytest.mark.anyio
async def test_http_timing_header_and_opt_in_log_exclude_query_string(
    client, monkeypatch, caplog,
):
    monkeypatch.setenv("ALETHEIA_HTTP_TIMING", "1")

    with caplog.at_level(logging.INFO, logger="aletheia.http"):
        resp = await client.get("/api/health?diagnostic_token=must-not-be-logged")

    assert resp.status_code == 200
    assert "app;dur=" in resp.headers["server-timing"]
    messages = [record.getMessage() for record in caplog.records]
    assert any("http_request method=GET path=/api/health" in message for message in messages)
    assert all("diagnostic_token" not in message for message in messages)

    caplog.clear()
    monkeypatch.delenv("ALETHEIA_HTTP_TIMING")
    monkeypatch.setenv("ALETHEIA_SLOW_REQUEST_MS", "0")
    with caplog.at_level(logging.WARNING, logger="aletheia.http"):
        await client.get("/api/health")
    assert any("http_request_slow method=GET path=/api/health" in record.getMessage()
               for record in caplog.records)


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
async def test_tunnel_guard_protects_lesson_management_actions(client):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    from aletheia.learning.lessons import LessonStore

    store = LessonStore()
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    set_secret("tunnel_api_token", "LESSONOWNER42")

    for action in ("accept", "reject", "archive"):
        protected = store.create(
            summary=f"Tunnel-protected {action} lesson",
            source_type="trade_case_study",
            source_id=f"case-tunnel-auth-{action}",
        )
        for token in (None, "wrong"):
            headers = {} if token is None else {"X-Aletheia-Token": token}
            response = await client.post(
                f"/api/persistence/lessons/{protected.lesson_id}/{action}",
                headers=headers,
            )
            assert response.status_code == 401
            assert store.get(protected.lesson_id).status == "proposed"

    lesson = store.create(
        summary="Tunnel-authorized lesson",
        source_type="trade_case_study",
        source_id="case-tunnel-authorized",
    )
    assert (await client.get("/api/persistence/lessons")).status_code == 200
    assert (
        await client.get(f"/api/persistence/lessons/{lesson.lesson_id}")
    ).status_code == 200

    headers = {"X-Aletheia-Token": "LESSONOWNER42"}
    for action, expected_status in (
        ("accept", "accepted"),
        ("reject", "rejected"),
        ("archive", "archived"),
    ):
        response = await client.post(
            f"/api/persistence/lessons/{lesson.lesson_id}/{action}",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == expected_status


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
async def test_guard_reads_fail_with_stable_unavailable_response(client, monkeypatch):
    import aletheia.knowledge.guards as guards
    import aletheia.analytics.guard_effectiveness as guard_effectiveness

    monkeypatch.setattr(
        guards,
        "load_trading_guards",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private/path must not leak")
        ),
    )

    for path in ("/api/knowledge/guards", "/api/knowledge/guard-candidates"):
        response = await client.get(path)
        assert response.status_code == 503
        assert response.json() == {"detail": "guard_read_failed"}
        assert "private/path" not in response.text

    monkeypatch.setattr(
        guard_effectiveness,
        "compute_guard_stats",
        lambda: (_ for _ in ()).throw(RuntimeError("private/path must not leak")),
    )
    response = await client.get("/api/knowledge/guards/stats")
    assert response.status_code == 503
    assert response.json() == {"detail": "guard_stats_read_failed"}
    assert "private/path" not in response.text


@pytest.mark.anyio
async def test_learning_funnel_reports_contradictions(client):
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item

    insert_knowledge_item(KnowledgeItem(
        item_type="trading_memo",
        title="BTC/USDT long opportunity",
        content="",
        confidence="high_confidence",
    ))
    insert_knowledge_item(KnowledgeItem(
        item_type="guard_candidate",
        title="Avoid BTC/USDT long",
        content="",
        confidence="medium_confidence",
    ))

    from aletheia.server.routes.analytics import get_learning_funnel

    body = get_learning_funnel()
    assert body["contradictions"]["checked"] is True
    assert body["contradictions"]["count"] >= 1
    first = body["contradictions"]["items"][0]
    assert first["symbol"] == "BTC/USDT"
    assert first["direction"] == "long"


@pytest.mark.anyio
async def test_learning_funnel_optional_read_failures_are_not_zero(client, monkeypatch):
    import aletheia.storage.sqlite as sqlite_module

    real_db = sqlite_module.get_db()

    class FailingOptionalReads:
        def execute(self, sql, *args, **kwargs):
            if any(fragment in sql for fragment in (
                "ORDER BY rowid DESC LIMIT 30",
                "ORDER BY rowid DESC LIMIT 20",
                "FROM ps_lessons",
                "FROM serenity_opportunities",
                "WHERE status='active'",
            )):
                raise RuntimeError("private optional read failure")
            return real_db.execute(sql, *args, **kwargs)

    monkeypatch.setattr(sqlite_module, "get_db", lambda: FailingOptionalReads())

    response = await client.get("/api/analytics/learning-funnel")

    assert response.status_code == 200
    body = response.json()
    assert body["ps_lessons"] == {
        "availability": "unavailable",
        "total": None,
        "by_status": None,
        "latest_created_at": None,
    }
    assert body["serenity_opportunities_active"] is None
    assert body["contradictions"]["checked"] is False
    assert body["contradictions"]["count"] is None
    assert body["query_errors"] == {
        "contradictions": "read_failed",
        "pi_guards_window": "read_failed",
        "pi_lessons_window": "read_failed",
        "ps_lessons": "read_failed",
        "serenity_opportunities": "read_failed",
    }
    assert "private optional read failure" not in response.text


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
