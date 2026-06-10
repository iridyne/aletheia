"""Operator control-plane routes."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.agent_shell.operator_store import (
    append_message,
    get_latest_heartbeat,
    get_session,
    get_task,
    list_artifacts,
    list_commands,
    list_messages,
    list_sessions,
    list_tasks,
    record_heartbeat,
    update_task,
    create_task,
)
from aletheia.config.loader import load_config, save_config

router = APIRouter(prefix="/api/operator", tags=["operator"])


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _operator_status_defaults() -> dict[str, Any]:
    cfg = load_config()
    return {
        "agent_id": "operator",
        "status": "offline",
        "mode": "Paused" if cfg.runtime.paused else "Observing",
        "permission_mode": cfg.operator.permission_mode,
        "is_busy": False,
        "current_task_id": None,
        "current_intent": None,
        "queue_size": 0,
        "last_seen_at": None,
        "last_success_at": None,
        "last_error": None,
        "model_provider": None,
        "session_id": cfg.operator.main_session_id,
        "stale": False,
        "stale_after_sec": cfg.operator.heartbeat_stale_after_sec,
    }


def _apply_stale_status(heartbeat: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    seen_at = _parse_ts(heartbeat.get("last_seen_at"))
    if seen_at is None:
        heartbeat["stale"] = False
        heartbeat["stale_after_sec"] = cfg.operator.heartbeat_stale_after_sec
        return heartbeat

    age = (datetime.now(timezone.utc) - seen_at).total_seconds()
    stale = age > cfg.operator.heartbeat_stale_after_sec
    if stale and heartbeat.get("status") == "online":
        heartbeat = dict(heartbeat)
        heartbeat["status"] = "stale"
    heartbeat["stale"] = stale
    heartbeat["stale_after_sec"] = cfg.operator.heartbeat_stale_after_sec
    heartbeat["heartbeat_age_sec"] = max(0, int(age))
    return heartbeat


def _set_paused(paused: bool, *, event_type: str, message: str) -> dict[str, Any]:
    cfg = load_config()
    cfg.runtime.paused = paused
    save_config(cfg)
    try:
        if paused:
            from aletheia.agent_runtime.scheduler import pause_scheduler

            pause_scheduler()
        else:
            from aletheia.agent_runtime.scheduler import resume_scheduler

            resume_scheduler()
    except Exception:
        pass

    from aletheia.files.jsonl import write_audit

    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": event_type,
            "entity_type": "operator",
            "entity_id": "operator",
            "message": message,
        }
    )
    return {"paused": paused}


class OperatorHeartbeatIn(BaseModel):
    agent_id: str = "operator"
    status: str
    mode: str
    permission_mode: str
    is_busy: bool = False
    current_task_id: str | None = None
    current_intent: str | None = None
    queue_size: int = 0
    last_seen_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    model_provider: str | None = None
    session_id: str | None = None


class OperatorTaskCreate(BaseModel):
    task_type: str
    status: str
    priority: str = "normal"
    created_by: str
    assigned_to: str = "operator"
    input_data: dict[str, Any] | list[Any] | None = None
    output_data: dict[str, Any] | list[Any] | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class OperatorTaskUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    assigned_to: str | None = None
    input_data: dict[str, Any] | list[Any] | None = None
    output_data: dict[str, Any] | list[Any] | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class OperatorMessageCreate(BaseModel):
    session_id: str
    role: str
    content: Any
    message_type: str = "message"
    related_event_id: str | None = None


class OperatorCommandRequest(BaseModel):
    input: str
    context: dict[str, Any] | None = None


@router.get("/status")
async def operator_status():
    heartbeat = get_latest_heartbeat()
    if heartbeat is None:
        return _operator_status_defaults()
    return _apply_stale_status(heartbeat)


@router.get("/sessions")
async def operator_sessions(limit: int = 50):
    return list_sessions(limit=limit)


@router.get("/sessions/{session_id}")
async def operator_session_detail(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return {
        "session": session,
        "messages": list_messages(session_id),
        "commands": list_commands(session_id),
        "artifacts": list_artifacts(session_id=session_id),
    }


@router.get("/sessions/{session_id}/messages")
async def operator_session_messages(session_id: str, limit: int = 200):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return list_messages(session_id, limit=limit)


@router.get("/tasks")
async def operator_tasks(limit: int = 50, status: str | None = None):
    return list_tasks(limit=limit, status=status)


@router.get("/tasks/{task_id}")
async def operator_task_detail(task_id: str):
    task = get_task(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


@router.post("/pause")
async def operator_pause():
    return _set_paused(True, event_type="operator_paused", message="Operator paused via API")


@router.post("/resume")
async def operator_resume():
    return _set_paused(False, event_type="operator_resumed", message="Operator resumed via API")


@router.post("/wake")
async def operator_wake():
    return _set_paused(False, event_type="operator_woken", message="Operator wake requested via API")


@router.post("/command")
async def operator_command(req: OperatorCommandRequest):
    from aletheia.agent_shell.runtime import AgentShellRuntime

    runtime = AgentShellRuntime()
    return await runtime.handle(req.input)


@router.post("/internal/heartbeat")
async def operator_heartbeat(payload: OperatorHeartbeatIn):
    record_heartbeat(**payload.model_dump())
    heartbeat = get_latest_heartbeat(payload.agent_id)
    if heartbeat is None:
        raise HTTPException(500, "Heartbeat write failed")
    return _apply_stale_status(heartbeat)


@router.post("/internal/tasks")
async def operator_create_task(payload: OperatorTaskCreate):
    task_id = create_task(**payload.model_dump())
    task = get_task(task_id)
    if task is None:
        raise HTTPException(500, "Task write failed")
    return task


@router.patch("/internal/tasks/{task_id}")
async def operator_update_task(task_id: str, payload: OperatorTaskUpdate):
    if get_task(task_id) is None:
        raise HTTPException(404, "Task not found")
    updates = payload.model_dump(exclude_none=True)
    update_task(task_id, **updates)
    task = get_task(task_id)
    if task is None:
        raise HTTPException(500, "Task update failed")
    return task


@router.post("/internal/messages")
async def operator_create_message(payload: OperatorMessageCreate):
    message_id = append_message(**payload.model_dump())
    messages = list_messages(payload.session_id, limit=200)
    message = next((item for item in reversed(messages) if item["id"] == message_id), None)
    if message is None:
        raise HTTPException(500, "Message write failed")
    return message
