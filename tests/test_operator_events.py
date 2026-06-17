import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

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
async def test_operator_internal_event_roundtrip_and_stream(client):
    create = await client.post(
        "/api/operator/internal/events",
        json={
            "event_type": "operator_degraded",
            "severity": "warning",
            "source": "operator_monitor",
            "session_id": "operator-main",
            "task_id": None,
            "related_entity_type": "operator",
            "related_entity_id": "operator",
            "payload": {"message": "degraded"},
        },
    )

    assert create.status_code == 200
    assert create.json()["event_type"] == "operator_degraded"

    listing = await client.get("/api/operator/events")
    assert listing.status_code == 200
    assert listing.json()[0]["event_type"] == "operator_degraded"

    async with client.stream("GET", "/api/operator/events/stream?limit=5&once=true") as response:
        body = ""
        async for chunk in response.aiter_text():
            body += chunk
            if "operator_degraded" in body:
                break

    assert "operator_degraded" in body


@pytest.mark.anyio
async def test_operator_task_routes_emit_lifecycle_events(client):
    created = await client.post(
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
    assert created.status_code == 200

    task_id = created.json()["id"]
    updated = await client.patch(
        f"/api/operator/internal/tasks/{task_id}",
        json={"status": "completed", "output_data": {"summary": "ok"}},
    )
    assert updated.status_code == 200

    listing = await client.get("/api/operator/events")
    event_types = [item["event_type"] for item in listing.json()]

    assert "task_started" in event_types
    assert "task_completed" in event_types


@pytest.mark.anyio
async def test_operator_events_can_filter_by_session_and_task(client):
    task_a = await client.post(
        "/api/operator/internal/tasks",
        json={
            "task_type": "diagnose_failure",
            "status": "running",
            "priority": "normal",
            "created_by": "operator_loop",
            "assigned_to": "operator",
            "input_data": {"entity_id": "run-a"},
        },
    )
    assert task_a.status_code == 200
    task_a_id = task_a.json()["id"]

    task_b = await client.post(
        "/api/operator/internal/tasks",
        json={
            "task_type": "generate_review",
            "status": "running",
            "priority": "normal",
            "created_by": "operator_loop",
            "assigned_to": "post_trade_reviewer",
            "input_data": {"entity_id": "run-b"},
        },
    )
    assert task_b.status_code == 200
    task_b_id = task_b.json()["id"]

    event_a = await client.post(
        "/api/operator/internal/events",
        json={
            "event_type": "dreaming_started",
            "severity": "info",
            "source": "operator_loop",
            "session_id": "dreaming-session-1",
            "task_id": task_a_id,
            "related_entity_type": "agent",
            "related_entity_id": "dreaming_agent",
            "payload": {"agent_name": "dreaming_agent"},
        },
    )
    assert event_a.status_code == 200

    event_b = await client.post(
        "/api/operator/internal/events",
        json={
            "event_type": "review_started",
            "severity": "info",
            "source": "operator_loop",
            "session_id": "review-session-1",
            "task_id": task_b_id,
            "related_entity_type": "agent",
            "related_entity_id": "post_trade_reviewer",
            "payload": {"agent_name": "post_trade_reviewer"},
        },
    )
    assert event_b.status_code == 200

    by_session = await client.get("/api/operator/events?session_id=dreaming-session-1")
    assert by_session.status_code == 200
    assert [item["event_type"] for item in by_session.json()] == ["dreaming_started"]

    by_task = await client.get(f"/api/operator/events?task_id={task_b_id}")
    assert by_task.status_code == 200
    assert {item["event_type"] for item in by_task.json()} == {"task_started", "review_started"}


@pytest.mark.anyio
async def test_operator_recommendations_reflect_real_backend_state(client):
    import aletheia.config.loader as loader

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)

    failed = await client.post(
        "/api/operator/internal/tasks",
        json={
            "task_type": "diagnose_failure",
            "status": "failed",
            "priority": "normal",
            "created_by": "operator_loop",
            "assigned_to": "operator",
            "input_data": {"entity_id": "run-1"},
            "error": "boom",
        },
    )
    assert failed.status_code == 200

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    kinds = {item["kind"] for item in recommendations.json()}
    assert "operator_offline" in kinds
    assert "failed_tasks_present" in kinds
    assert "operator_paused" in kinds


@pytest.mark.anyio
async def test_operator_recommendations_surface_stuck_running_tasks(client):
    from datetime import datetime, timedelta, timezone

    started_at = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()

    created = await client.post(
        "/api/operator/internal/tasks",
        json={
            "task_type": "run_dreaming",
            "status": "running",
            "priority": "normal",
            "created_by": "operator_loop",
            "assigned_to": "dreaming_agent",
            "input_data": {"reason": "refresh memory"},
            "started_at": started_at,
        },
    )
    assert created.status_code == 200

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    kinds = {item["kind"] for item in recommendations.json()}
    assert "stuck_tasks_present" in kinds


@pytest.mark.anyio
async def test_operator_recommendations_surface_queued_task_backlog(client):
    created = await client.post(
        "/api/operator/internal/tasks",
        json={
            "task_type": "generate_review",
            "status": "queued",
            "priority": "normal",
            "created_by": "operator_loop",
            "assigned_to": "post_trade_reviewer",
            "input_data": {"reason": "review backlog"},
        },
    )
    assert created.status_code == 200

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    kinds = {item["kind"] for item in recommendations.json()}
    assert "queued_tasks_present" in kinds


@pytest.mark.anyio
async def test_operator_recommendations_escalate_queue_review_when_backlog_accumulates(client):
    for index in range(3):
        created = await client.post(
            "/api/operator/internal/tasks",
            json={
                "task_type": "generate_review",
                "status": "queued",
                "priority": "normal",
                "created_by": "operator_loop",
                "assigned_to": "post_trade_reviewer",
                "input_data": {"reason": f"review backlog {index}"},
            },
        )
        assert created.status_code == 200

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    kinds = {item["kind"] for item in payload}
    assert "queued_tasks_present" in kinds
    assert "queue_review_needed" in kinds

    queue_review = next(item for item in payload if item["kind"] == "queue_review_needed")
    assert "queue review" in queue_review["summary"].lower()
    assert queue_review["queued_count"] == 3
    assert queue_review["review_stage"] == "attention"
    assert queue_review["suggested_review_cadence_sec"] == 300


@pytest.mark.anyio
async def test_operator_recommendations_escalate_queue_review_to_urgent_for_deeper_backlog(client):
    for index in range(5):
        created = await client.post(
            "/api/operator/internal/tasks",
            json={
                "task_type": "generate_review",
                "status": "queued",
                "priority": "high" if index == 0 else "normal",
                "created_by": "operator_loop",
                "assigned_to": "post_trade_reviewer",
                "input_data": {"reason": f"review backlog {index}"},
            },
        )
        assert created.status_code == 200

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    queue_review = next(
        item for item in recommendations.json() if item["kind"] == "queue_review_needed"
    )
    assert queue_review["queued_count"] == 5
    assert queue_review["severity"] == "high"
    assert queue_review["review_stage"] == "urgent"
    assert queue_review["suggested_review_cadence_sec"] == 60


@pytest.mark.anyio
async def test_operator_recommendations_include_shell_specialist_work_from_backend_truth(client):
    from datetime import datetime, timezone

    from aletheia.agent_shell.operator_store import create_artifact
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

    review_strategy = create_strategy("Review Candidate", "needs operator review")
    for target in ("research_note", "strategy_hypothesis", "paper_candidate", "paper_running"):
        result = promote_strategy(review_strategy.id, target)
        assert result["success"] is True
    repo.update_strategy(
        review_strategy.id,
        under_review=1,
        review_notes="needs evaluation",
        review_updated_at=datetime.now(timezone.utc).isoformat(),
    )

    promotion_strategy = create_strategy("Promotion Candidate", "promotion proposal due")
    for target in ("research_note", "strategy_hypothesis", "paper_candidate", "paper_running"):
        result = promote_strategy(promotion_strategy.id, target)
        assert result["success"] is True

    repo.insert_position(
        Position(
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.1,
            entry_price=60000,
            execution_mode="paper_internal",
            strategy_id="review-strat-1",
            status="closed",
            opened_at=datetime.now(timezone.utc).isoformat(),
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    create_artifact(
        task_id=None,
        session_id="strategy-eval-session",
        artifact_type="strategy_evaluation",
        title="Promotion-ready evaluation",
        content={
            "strategy_id": promotion_strategy.id,
            "strategy_name": "Promotion Candidate",
            "summary": "Promotion gates passed for paper_running strategy.",
            "evaluation": {
                "strategy": {
                    "id": promotion_strategy.id,
                    "name": "Promotion Candidate",
                    "state": "paper_running",
                },
                "promotion_gates": {
                    "passed": True,
                    "failures": [],
                },
            },
        },
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    kinds = {item["kind"] for item in recommendations.json()}
    assert "dreaming_due" in kinds
    assert "review_due" in kinds
    assert "strategy_review_due" in kinds
    assert "strategy_promotion_proposal_due" in kinds


@pytest.mark.anyio
async def test_operator_recommendations_include_pause_paper_strategy_due_for_under_review_active_paper_strategy(
    client,
):
    from datetime import datetime, timezone

    from aletheia.storage.models import StrategyRecord
    from aletheia.storage.repositories import insert_strategy

    strategy = StrategyRecord(
        name="Pause Candidate",
        state="paper_running",
        under_review=True,
        review_notes="paper drawdown breached review threshold",
        review_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    insert_strategy(strategy)

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "pause_paper_strategy_due"
    )
    assert due["metadata"]["strategy_id"] == strategy.id
    assert due["metadata"]["task_type"] == "pause_paper_strategy"
    assert "under review" in due["summary"].lower()


@pytest.mark.anyio
async def test_operator_recommendations_include_mark_strategy_under_review_due_for_failed_promotion_gates(
    client,
):
    from aletheia.agent_shell.operator_store import create_artifact
    from aletheia.storage.models import StrategyRecord
    from aletheia.storage.repositories import insert_strategy

    strategy = StrategyRecord(
        name="Review Candidate",
        state="paper_running",
    )
    insert_strategy(strategy)

    create_artifact(
        task_id=None,
        session_id="strategy-eval-session",
        artifact_type="strategy_evaluation",
        title="Failed promotion-gate evaluation",
        content={
            "strategy_id": strategy.id,
            "strategy_name": "Review Candidate",
            "summary": "Promotion gates failed for paper strategy.",
            "evaluation": {
                "strategy": {
                    "id": strategy.id,
                    "name": "Review Candidate",
                    "state": "paper_running",
                },
                "promotion_gates": {
                    "passed": False,
                    "failures": ["drawdown_too_high", "win_rate_below_threshold"],
                },
            },
        },
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "mark_strategy_under_review_due"
    )
    assert due["taskType"] == "create_repair_proposal"
    assert due["metadata"]["strategy_id"] == strategy.id
    assert due["metadata"]["failed_gates"] == [
        "drawdown_too_high",
        "win_rate_below_threshold",
    ]
    assert due["metadata"]["repair_command"] == "mark_strategy_under_review"


@pytest.mark.anyio
async def test_operator_recommendations_include_prompt_change_proposal_due_for_repeated_agent_failures(client):
    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    insert_agent_run(
        AgentRun(
            agent_name="market_observer",
            status="failed",
            errors='["structured output parse failed"]',
        )
    )
    insert_agent_run(
        AgentRun(
            agent_name="market_observer",
            status="failed",
            errors='["structured output parse failed"]',
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    kinds = {item["kind"] for item in recommendations.json()}
    assert "agent_prompt_change_proposal_due" in kinds


@pytest.mark.anyio
async def test_operator_recommendations_include_live_candidate_promotion_due_for_paper_passed_strategy(client):
    from aletheia.agent_shell.operator_store import create_artifact
    from aletheia.storage.models import StrategyRecord
    from aletheia.storage.repositories import insert_strategy

    strategy = StrategyRecord(
        name="Live Candidate Candidate",
        state="paper_passed",
    )
    insert_strategy(strategy)

    create_artifact(
        task_id=None,
        session_id="strategy-eval-session",
        artifact_type="strategy_evaluation",
        title="Live-candidate-ready evaluation",
        content={
            "strategy_id": strategy.id,
            "strategy_name": "Live Candidate Candidate",
            "summary": "Promotion gates passed for paper_passed strategy.",
            "evaluation": {
                "strategy": {
                    "id": strategy.id,
                    "name": "Live Candidate Candidate",
                    "state": "paper_passed",
                },
                "promotion_gates": {
                    "passed": True,
                    "failures": [],
                },
            },
        },
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    proposal_due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "strategy_promotion_proposal_due"
    )
    assert proposal_due["metadata"]["target_state"] == "live_candidate"
    assert proposal_due["metadata"]["proposal_command"] == "propose_strategy_promotion"


@pytest.mark.anyio
async def test_operator_recommendations_include_live_limited_enablement_due_for_live_candidate_strategy(
    client,
):
    from aletheia.agent_shell.operator_store import create_artifact
    from aletheia.storage.models import StrategyRecord
    from aletheia.storage.repositories import insert_strategy

    strategy = StrategyRecord(
        name="Live Limited Candidate",
        state="live_candidate",
    )
    insert_strategy(strategy)

    create_artifact(
        task_id=None,
        session_id="strategy-eval-session",
        artifact_type="strategy_evaluation",
        title="Live-limited-ready evaluation",
        content={
            "strategy_id": strategy.id,
            "strategy_name": "Live Limited Candidate",
            "summary": "Live-limited gates passed for live_candidate strategy.",
            "evaluation": {
                "strategy": {
                    "id": strategy.id,
                    "name": "Live Limited Candidate",
                    "state": "live_candidate",
                },
                "live_limited_gates": {
                    "passed": True,
                    "failures": [],
                },
            },
        },
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    proposal_due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "live_limited_enablement_proposal_due"
    )
    assert proposal_due["metadata"]["proposal_command"] == "propose_live_limited_enablement"
    assert proposal_due["metadata"]["strategy_id"] == strategy.id


@pytest.mark.anyio
async def test_operator_recommendations_include_schedule_change_proposal_due_for_repeated_manual_runs(client):
    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    insert_agent_run(
        AgentRun(
            agent_name="market_observer",
            trigger_type="manual",
            status="completed",
        )
    )
    insert_agent_run(
        AgentRun(
            agent_name="market_observer",
            trigger_type="manual",
            status="completed",
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    kinds = {item["kind"] for item in recommendations.json()}
    assert "agent_schedule_change_proposal_due" in kinds


@pytest.mark.anyio
async def test_operator_recommendations_include_market_scan_refresh_due_for_stale_market_observer_activity(client):
    from datetime import datetime, timedelta, timezone

    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    stale_started_at = (datetime.now(timezone.utc) - timedelta(minutes=50)).isoformat()
    insert_agent_run(
        AgentRun(
            agent_name="market_observer",
            trigger_type="scheduled",
            status="completed",
            started_at=stale_started_at,
            ended_at=stale_started_at,
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "market_scan_refresh_due"]
    assert len(due) == 1
    assert due[0]["metadata"]["task_type"] == "run_market_scan"
    assert due[0]["metadata"]["stale_minutes"] >= 45


@pytest.mark.anyio
async def test_operator_recommendations_include_failed_agent_rerun_due_for_unrecovered_recent_failure(client):
    from datetime import datetime, timedelta, timezone

    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    fresh_started_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    failed_started_at = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    insert_agent_run(
        AgentRun(
            agent_name="market_observer",
            trigger_type="scheduled",
            status="completed",
            started_at=fresh_started_at,
            ended_at=fresh_started_at,
        )
    )
    failed_run = AgentRun(
        agent_name="post_trade_reviewer",
        trigger_type="scheduled",
        status="failed",
        started_at=failed_started_at,
        ended_at=failed_started_at,
    )
    insert_agent_run(failed_run)

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "failed_agent_rerun_due"]
    assert len(due) == 1
    assert due[0]["metadata"]["task_type"] == "rerun_failed_agent_task"
    assert due[0]["metadata"]["failed_run_id"] == failed_run.id
    assert due[0]["metadata"]["agent_name"] == "post_trade_reviewer"
    assert due[0]["metadata"]["stale_minutes"] >= 15


@pytest.mark.anyio
async def test_operator_recommendations_include_daily_review_refresh_due_when_today_has_activity_but_no_review(client):
    from datetime import datetime, timedelta, timezone

    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    fresh_started_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    insert_agent_run(
        AgentRun(
            agent_name="market_observer",
            trigger_type="scheduled",
            status="completed",
            started_at=fresh_started_at,
            ended_at=fresh_started_at,
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "daily_review_refresh_due"]
    assert len(due) == 1
    assert due[0]["metadata"]["task_type"] == "create_daily_review"
    assert due[0]["metadata"]["target_date"] == datetime.now(timezone.utc).date().isoformat()


@pytest.mark.anyio
async def test_operator_recommendations_include_system_health_report_refresh_due_when_health_is_degraded(client):
    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": False, "issues": ["market_data_stale:45m"]},
    ):
        recommendations = await client.get("/api/operator/recommendations")

    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "system_health_report_refresh_due"]
    assert len(due) == 1
    assert due[0]["taskType"] == "create_diagnostic_report"
    assert due[0]["metadata"]["report_type"] == "system_health"
    assert due[0]["metadata"]["refresh_window_minutes"] == 60


@pytest.mark.anyio
async def test_operator_recommendations_include_worker_health_review_due_when_agent_failure_ratio_is_degraded(client):
    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": False, "issues": ["agent_failure_ratio:0.40>0.25"]},
    ):
        recommendations = await client.get("/api/operator/recommendations")

    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "worker_health_review_due"]
    assert len(due) == 1
    assert due[0]["taskType"] == "create_diagnostic_report"
    assert due[0]["metadata"]["report_type"] == "worker_health"
    assert due[0]["metadata"]["refresh_window_minutes"] == 60


@pytest.mark.anyio
async def test_operator_recommendations_include_model_runtime_health_review_due_when_llm_health_is_degraded(client):
    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": False, "issues": ["llm_unavailable:3"]},
    ):
        recommendations = await client.get("/api/operator/recommendations")

    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "model_runtime_health_review_due"]
    assert len(due) == 1
    assert due[0]["taskType"] == "create_diagnostic_report"
    assert due[0]["metadata"]["report_type"] == "model_runtime_health"
    assert due[0]["metadata"]["refresh_window_minutes"] == 60


@pytest.mark.anyio
async def test_operator_recommendations_include_data_source_health_review_due_when_market_data_is_stale(client):
    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": False, "issues": ["market_data_stale:45m"]},
    ):
        recommendations = await client.get("/api/operator/recommendations")

    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "data_source_health_review_due"]
    assert len(due) == 1
    assert due[0]["taskType"] == "create_diagnostic_report"
    assert due[0]["metadata"]["report_type"] == "data_source_health"
    assert due[0]["metadata"]["refresh_window_minutes"] == 60


@pytest.mark.anyio
async def test_operator_recommendations_include_trade_review_report_refresh_due_when_closed_positions_exist(client):
    from datetime import datetime, timezone

    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    insert_position(
        Position(
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.1,
            entry_price=60000,
            execution_mode="paper_internal",
            strategy_id="review-strategy-1",
            status="closed",
            opened_at=datetime.now(timezone.utc).isoformat(),
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "trade_review_report_refresh_due"]
    assert len(due) == 1
    assert due[0]["taskType"] == "create_review_report"
    assert due[0]["metadata"]["report_type"] == "trade_review"
    assert due[0]["metadata"]["closed_position_count"] == 1


@pytest.mark.anyio
async def test_operator_recommendations_include_agent_performance_report_refresh_due_when_recent_failures_exist(client):
    from datetime import datetime, timedelta, timezone

    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    failed_started_at = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    insert_agent_run(
        AgentRun(
            agent_name="paper_trader",
            trigger_type="scheduled",
            status="failed",
            started_at=failed_started_at,
            ended_at=failed_started_at,
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "agent_performance_report_refresh_due"]
    assert len(due) == 1
    assert due[0]["taskType"] == "create_review_report"
    assert due[0]["metadata"]["report_type"] == "agent_performance"
    assert due[0]["metadata"]["failed_run_count"] == 1


@pytest.mark.anyio
async def test_operator_recommendations_include_strategy_design_due_when_recent_strategy_inputs_exist_without_active_paper_strategy(
    client,
):
    from aletheia.knowledge.manager import create_research_note, create_strategy_memory
    from aletheia.trading.strategy_lifecycle import create_strategy

    create_research_note("Breakout Research", "Recent market structure favors breakout continuation with BTC breaking resistance and volume confirming the move.")
    strategy = create_strategy("Draft Strategy Anchor", "anchor strategy for memory path")
    create_strategy_memory(strategy.id, "Momentum Hypothesis", "Probe ETH continuation on 1h.")

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "strategy_design_due"
    )
    assert due["taskType"] == "run_strategy_design"
    assert due["metadata"]["recent_research_note_count"] == 1
    assert due["metadata"]["recent_strategy_hypothesis_count"] == 1


@pytest.mark.anyio
async def test_operator_recommendations_include_risk_critique_due_for_recent_rejections_without_recent_risk_critic_run(
    client,
):
    from aletheia.storage.models import RiskDecision, TradeProposal
    from aletheia.storage.repositories import insert_risk_decision, insert_trade_proposal

    proposal = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.005,
        price=60000,
    )
    insert_trade_proposal(proposal)
    insert_risk_decision(
        RiskDecision(
            proposal_id=proposal.id,
            approved=False,
            violations='["exceeds_demo_position_notional:300.0>200.0"]',
            message="Rejected",
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "risk_critique_due"
    )
    assert due["taskType"] == "run_risk_critique"
    assert due["metadata"]["rejected_decision_count"] == 1


@pytest.mark.anyio
async def test_operator_recommendations_include_portfolio_snapshot_refresh_due_when_open_positions_lack_fresh_snapshot(
    client,
):
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    insert_position(
        Position(
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=60000,
            current_price=60500,
            unrealized_pnl=5.0,
            execution_mode="paper_internal",
            status="open",
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "portfolio_snapshot_refresh_due"
    )
    assert due["taskType"] == "run_portfolio_snapshot"
    assert due["metadata"]["open_positions_count"] == 1
    assert due["metadata"]["stale_minutes"] is None


@pytest.mark.anyio
async def test_operator_recommendations_use_latest_portfolio_snapshot_not_oldest_window(client):
    from aletheia.storage.models import PortfolioSnapshot, Position
    from aletheia.storage.repositories import insert_portfolio_snapshot, insert_position

    insert_position(
        Position(
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=60000,
            current_price=60500,
            unrealized_pnl=5.0,
            execution_mode="paper_internal",
            status="open",
        )
    )
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(60):
        insert_portfolio_snapshot(
            PortfolioSnapshot(
                total_value_usdt=1000 + i,
                positions_count=1,
                snapshot_at=(base + timedelta(minutes=i)).isoformat(),
            )
        )
    insert_portfolio_snapshot(
        PortfolioSnapshot(
            total_value_usdt=2000,
            positions_count=1,
            snapshot_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    recommendations = await client.get("/api/operator/recommendations")

    assert recommendations.status_code == 200
    assert not [
        item
        for item in recommendations.json()
        if item["kind"] == "portfolio_snapshot_refresh_due"
    ]


@pytest.mark.anyio
async def test_operator_recommendations_include_risk_boundary_change_proposal_due_for_repeated_adjustable_rejections(client):
    from aletheia.storage.models import RiskDecision, TradeProposal
    from aletheia.storage.repositories import insert_risk_decision, insert_trade_proposal

    first = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.005,
        price=60000,
    )
    second = TradeProposal(
        symbol="ETHUSDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.1,
        price=3000,
    )
    insert_trade_proposal(first)
    insert_trade_proposal(second)
    insert_risk_decision(
        RiskDecision(
            proposal_id=first.id,
            approved=False,
            violations='["exceeds_demo_position_notional:300.0>200.0"]',
            message="Rejected",
        )
    )
    insert_risk_decision(
        RiskDecision(
            proposal_id=second.id,
            approved=False,
            violations='["exceeds_demo_position_notional:300.0>200.0"]',
            message="Rejected",
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    risk_proposals = [item for item in payload if item["kind"] == "risk_boundary_change_proposal_due"]
    assert len(risk_proposals) == 1
    assert risk_proposals[0]["metadata"]["proposal_command"] == "propose_risk_boundary_change"
    assert risk_proposals[0]["metadata"]["changes"]["max_demo_position_notional_usdt"] == 300.0


@pytest.mark.anyio
async def test_operator_recommendations_include_risk_review_report_refresh_due_when_recent_rejections_exist(client):
    from aletheia.storage.models import RiskDecision, TradeProposal
    from aletheia.storage.repositories import insert_risk_decision, insert_trade_proposal

    proposal = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.005,
        price=60000,
    )
    insert_trade_proposal(proposal)
    insert_risk_decision(
        RiskDecision(
            proposal_id=proposal.id,
            approved=False,
            violations='["exceeds_demo_position_notional:300.0>200.0"]',
            message="Rejected",
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    due = [item for item in payload if item["kind"] == "risk_review_report_refresh_due"]
    assert len(due) == 1
    assert due[0]["taskType"] == "create_review_report"
    assert due[0]["metadata"]["report_type"] == "risk_review"
    assert due[0]["metadata"]["rejected_decision_count"] == 1


@pytest.mark.anyio
async def test_operator_recommendations_include_symbol_observe_only_due_for_repeated_rejected_symbol(
    client, monkeypatch
):
    import aletheia.config.loader as loader
    from aletheia.storage.models import RiskDecision, TradeProposal
    from aletheia.storage.repositories import insert_risk_decision, insert_trade_proposal

    cfg = loader.load_config()
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.risk.symbol_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    cfg.exchange.symbol_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    loader.save_config(cfg)

    first = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.005,
        price=60000,
    )
    second = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="short",
        execution_mode="paper_internal",
        quantity=0.006,
        price=61000,
    )
    insert_trade_proposal(first)
    insert_trade_proposal(second)
    insert_risk_decision(
        RiskDecision(
            proposal_id=first.id,
            approved=False,
            violations='["symbol_not_allowed:BTCUSDT"]',
            message="Rejected",
        )
    )
    insert_risk_decision(
        RiskDecision(
            proposal_id=second.id,
            approved=False,
            violations='["symbol_not_allowed:BTCUSDT"]',
            message="Rejected again",
        )
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "symbol_observe_only_due"
    )
    assert due["taskType"] == "set_symbol_observe_only"
    assert due["metadata"]["symbol"] == "BTC/USDT"


@pytest.mark.anyio
async def test_operator_recommendations_include_command_center_behavior_change_proposal_due_for_repeated_denials(client):
    from aletheia.agent_shell.audit import record_command
    from aletheia.agent_shell.operator_store import create_session_record

    create_session_record(
        session_id="session-command-1",
        user_input="pause everything",
        kind="command_session",
        parent_session_id="operator-main",
        title="Pause attempt",
    )
    create_session_record(
        session_id="session-command-2",
        user_input="pause agents now",
        kind="command_session",
        parent_session_id="operator-main",
        title="Pause retry",
    )
    record_command(
        "session-command-1",
        "pause_agents",
        {},
        False,
        "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
    )
    record_command(
        "session-command-2",
        "pause_agents",
        {},
        False,
        "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    payload = recommendations.json()
    cc_proposals = [
        item
        for item in payload
        if item["kind"] == "command_center_behavior_change_proposal_due"
    ]
    assert len(cc_proposals) == 1
    assert (
        cc_proposals[0]["metadata"]["proposal_command"]
        == "propose_command_center_behavior_change"
    )
    assert cc_proposals[0]["metadata"]["target_command"] == "pause_agents"


@pytest.mark.anyio
async def test_operator_recommendations_suppress_command_center_behavior_change_when_recent_proposal_exists(client):
    from aletheia.agent_shell.audit import record_command
    from aletheia.agent_shell.operator_store import create_artifact, create_session_record

    create_session_record(
        session_id="session-command-1",
        user_input="pause everything",
        kind="command_session",
        parent_session_id="operator-main",
        title="Pause attempt",
    )
    create_session_record(
        session_id="session-command-2",
        user_input="pause agents now",
        kind="command_session",
        parent_session_id="operator-main",
        title="Pause retry",
    )
    record_command(
        "session-command-1",
        "pause_agents",
        {},
        False,
        "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
    )
    record_command(
        "session-command-2",
        "pause_agents",
        {},
        False,
        "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
    )
    create_artifact(
        task_id=None,
        session_id="operator-main",
        artifact_type="command_center_behavior_proposal",
        title="Command Center behavior proposal: pause_agents",
        content={
            "target_surface": "command_center",
            "target_command": "pause_agents",
            "requested_behavior": "Add an inline permission gate.",
            "requires_human_approval": True,
        },
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200
    assert not any(
        item["kind"] == "command_center_behavior_change_proposal_due"
        for item in recommendations.json()
    )


@pytest.mark.anyio
async def test_operator_recommendations_include_extension_enablement_proposal_due_for_passed_sandbox_extension(
    client,
):
    from aletheia.agent_shell.commands import (
        draft_extension_manifest,
        test_extension_sandbox,
    )

    draft = await draft_extension_manifest(
        {
            "extension_name": "Explain Proposal",
            "slash_command": "/explain-proposal",
            "description": "Open a structured explanation flow for the latest proposal.",
            "target_command": "explain_proposal",
            "capabilities": ["operator_command_proxy"],
        }
    )
    sandbox = await test_extension_sandbox({"extension_id": draft["extension_id"]})
    assert sandbox["passed"] is True

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    due = next(
        item
        for item in recommendations.json()
        if item["kind"] == "extension_enablement_proposal_due"
    )
    assert due["taskType"] == "create_repair_proposal"
    assert due["metadata"]["proposal_command"] == "propose_extension_enablement"
    assert due["metadata"]["extension_id"] == draft["extension_id"]
    assert due["metadata"]["slash_command"] == "/explain-proposal"


@pytest.mark.anyio
async def test_operator_recommendations_hide_archived_low_value_notifications(client):
    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_artifact

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)

    create_artifact(
        task_id=None,
        session_id="operator-main",
        artifact_type="notification_archive",
        title="Archive gateway paused recommendation",
        content={"recommendation_id": "gateway-paused"},
    )

    recommendations = await client.get("/api/operator/recommendations")
    assert recommendations.status_code == 200

    kinds = {item["kind"] for item in recommendations.json()}
    assert "operator_offline" in kinds
    assert "operator_paused" not in kinds


@pytest.mark.anyio
async def test_operator_command_route_emits_command_events(client):
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

    listing = await client.get("/api/operator/events")
    event_types = [item["event_type"] for item in listing.json()]

    assert "command_received" in event_types
    assert "command_executed" in event_types
