"""Operator control-plane routes."""

import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from aletheia.agent_shell.context import (
    ALL_SECTIONS,
    CONTEXT_PROFILES,
    DEFAULT_CONTEXT_PROFILE_BY_SESSION_KIND,
    build_app_context,
    context_summary_text,
    extend_summary_with_session_trace,
    profile_for_session_kind,
    sections_for_profile,
    sections_for_session_kind,
)
from aletheia.agent_shell.operator_recommendations import build_recommendations
from aletheia.agent_shell.operator_store import (
    append_message,
    create_session_record,
    create_artifact,
    create_event,
    get_latest_heartbeat,
    get_extension,
    get_extension_run,
    get_session,
    get_task,
    list_artifacts,
    list_commands,
    list_events,
    list_extensions,
    list_extension_runs,
    list_messages,
    list_sessions,
    list_tasks,
    record_heartbeat,
    update_task,
    update_extension,
    create_task,
)
from aletheia.config.loader import load_config, save_config

router = APIRouter(prefix="/api/operator", tags=["operator"])

OPERATOR_SESSION_KINDS = tuple(DEFAULT_CONTEXT_PROFILE_BY_SESSION_KIND.keys())
_recommendations_cache_lock = threading.Lock()
_recommendations_cache: dict[str, Any] = {"value": None}


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
        "runtime_id": "runtime-local-current",
        "runtime_mode": "current",
        "runtime_state": "current",
        "active_branch_id": None,
        "active_branch_kind": None,
        "active_branch_title": None,
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


def _task_event_type(status: str) -> str | None:
    if status == "running":
        return "task_started"
    if status == "completed":
        return "task_completed"
    if status == "failed":
        return "task_failed"
    return None


def _looks_like_command_rejection(intent: str, response: str | None) -> bool:
    if intent in {"forbidden", "error"}:
        return True
    text = (response or "").lower()
    markers = [
        "requires worker permission mode",
        "requires operator permission mode",
        "permission denied",
        "unknown command",
        "permanently forbidden",
    ]
    return any(marker in text for marker in markers)


def _sse_block(event_name: str, data: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _set_paused(paused: bool, *, event_type: str, message: str) -> dict[str, Any]:
    cfg = load_config()
    if paused:
        cfg.runtime.paused = True
        save_config(cfg)
        try:
            from aletheia.agent_runtime.scheduler import pause_scheduler

            pause_scheduler()
        except Exception:
            pass
    else:
        from aletheia.agent_runtime import resume_gate

        result = await resume_gate.resume_if_llm_available(
            actor="human",
            source="dashboard",
            cause=event_type,
        )
        if not result["allowed"]:
            raise HTTPException(status_code=409, detail=result)

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
    runtime_id: str | None = None
    runtime_mode: str = "current"
    runtime_state: str = "current"
    is_busy: bool = False
    current_task_id: str | None = None
    current_intent: str | None = None
    queue_size: int = 0
    last_seen_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    model_provider: str | None = None
    session_id: str | None = None
    active_branch_id: str | None = None
    active_branch_kind: str | None = None
    active_branch_title: str | None = None


class OperatorTaskCreate(BaseModel):
    task_type: str
    status: str
    priority: str = "normal"
    created_by: str
    assigned_to: str = "operator"
    runtime_id: str | None = None
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


class OperatorSessionCreate(BaseModel):
    id: str
    user_input: str = "[operator session]"
    kind: str = "command_session"
    parent_session_id: str | None = None
    title: str | None = None


class OperatorCommandRequest(BaseModel):
    input: str
    context: dict[str, Any] | None = None
    session_id: str | None = None


class OperatorCommandPreviewRequest(BaseModel):
    input: str
    session_id: str | None = None


class OperatorBranchCreateRequest(BaseModel):
    title: str | None = None
    kind: str = "command_session"
    user_input: str | None = None
    parent_session_id: str | None = None
    activate: bool = True
    selected_by: str = "command-center"


class OperatorActiveBranchSelectRequest(BaseModel):
    session_id: str
    selected_by: str = "human"


class OperatorEventCreate(BaseModel):
    event_type: str
    severity: str = "info"
    source: str = "operator"
    runtime_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    related_entity_type: str | None = None
    related_entity_id: str | None = None
    payload: dict[str, Any] | list[Any] | None = None


class OperatorArtifactCreate(BaseModel):
    task_id: str | None = None
    session_id: str | None = None
    artifact_type: str
    title: str
    content: Any


class OperatorExtensionEnableRequest(BaseModel):
    approved_by: str = "human"
    session_id: str | None = None


class OperatorDecisionCreate(BaseModel):
    task_id: str | None = None
    session_id: str | None = None
    title: str
    content: Any


class OperatorCommandIntentCreate(BaseModel):
    task_id: str | None = None
    session_id: str | None = None
    command_name: str
    user_input: str | None = None
    classification: str | None = None
    summary: str | None = None
    parameters: dict[str, Any] | list[Any] | None = None


class OperatorInternalCommandRequest(BaseModel):
    session_id: str | None = None
    user_input: str | None = None
    parameters: dict[str, Any] | list[Any] | None = None


class OperatorExecuteCommandRequest(OperatorInternalCommandRequest):
    command_name: str


class OperatorContextQueryRequest(BaseModel):
    session_id: str | None = None
    profile: str | None = None
    sections: list[str] | None = None


class LlmSessionRequest(BaseModel):
    goal: str
    session_id: str | None = None
    context_sections: list[str] | None = None
    allowed_commands: list[str] | None = None
    max_turns: int = 8


def _summary_artifact_for_date(session_id: str, target_date: str) -> dict[str, Any] | None:
    for artifact in list_artifacts(session_id=session_id, limit=500):
        if artifact.get("artifact_type") != "operator_daily_summary":
            continue
        content = artifact.get("content") or {}
        if not isinstance(content, dict):
            continue
        if content.get("date") == target_date:
            return artifact
    return None


def _normalize_session_kind(kind: str) -> str:
    normalized = kind.strip()
    if normalized not in OPERATOR_SESSION_KINDS:
        raise HTTPException(400, f"Unknown operator session kind: {kind}")
    return normalized


def _generated_branch_session_id(kind: str) -> str:
    prefixes = {
        "command_session": "command-session",
        "diagnostic_session": "diagnostic-session",
        "dreaming_session": "dreaming-session",
        "strategy_session": "strategy-session",
        "review_session": "review-session",
    }
    prefix = prefixes.get(kind, "command-session")
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _session_belongs_to_operator_tree(session_id: str, main_session_id: str) -> bool:
    current_session_id = session_id.strip()
    visited: set[str] = set()
    while current_session_id and current_session_id not in visited:
        if current_session_id == main_session_id:
            return True
        visited.add(current_session_id)
        session = get_session(current_session_id)
        if session is None:
            return False
        current_session_id = str(session.get("parent_session_id") or "").strip()
    return False


def _write_active_branch_selection(
    *,
    target_session: dict[str, Any] | None,
    selected_by: str,
) -> dict[str, Any]:
    cfg = load_config()
    main_session_id = cfg.operator.main_session_id
    latest = get_latest_heartbeat("operator") or _operator_status_defaults()

    active_branch_id = None
    active_branch_kind = None
    active_branch_title = None
    event_type = "active_branch_cleared"
    event_session_id = main_session_id
    event_entity_id = main_session_id

    if target_session is not None and target_session.get("id") != main_session_id:
        active_branch_id = str(target_session["id"])
        active_branch_kind = str(target_session.get("kind") or "command_session")
        active_branch_title = str(target_session.get("title") or target_session["id"])
        event_type = "active_branch_selected"
        event_session_id = active_branch_id
        event_entity_id = active_branch_id

    record_heartbeat(
        agent_id=str(latest.get("agent_id") or "operator"),
        status=str(latest.get("status") or "offline"),
        mode=str(latest.get("mode") or "Observing"),
        permission_mode=str(latest.get("permission_mode") or cfg.operator.permission_mode),
        is_busy=bool(latest.get("is_busy")),
        current_task_id=latest.get("current_task_id"),
        current_intent=latest.get("current_intent"),
        queue_size=int(latest.get("queue_size") or 0),
        last_error=latest.get("last_error"),
        model_provider=latest.get("model_provider"),
        session_id=str(latest.get("session_id") or main_session_id),
        runtime_id=latest.get("runtime_id"),
        runtime_mode=str(latest.get("runtime_mode") or "current"),
        runtime_state=str(latest.get("runtime_state") or "current"),
        active_branch_id=active_branch_id,
        active_branch_kind=active_branch_kind,
        active_branch_title=active_branch_title,
        last_seen_at=latest.get("last_seen_at"),
        last_success_at=latest.get("last_success_at"),
    )
    create_event(
        event_type=event_type,
        source="operator_branch_route",
        session_id=event_session_id,
        related_entity_type="session",
        related_entity_id=event_entity_id,
        payload={
            "selected_by": selected_by,
            "active_branch_id": active_branch_id,
            "active_branch_kind": active_branch_kind,
            "active_branch_title": active_branch_title,
        },
    )
    heartbeat = get_latest_heartbeat("operator")
    if heartbeat is None:
        raise HTTPException(500, "Active branch write failed")
    return _apply_stale_status(heartbeat)


def _create_typed_artifact(
    *,
    artifact_type: str,
    title: str,
    content: Any,
    task_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    artifact_id = create_artifact(
        task_id=task_id,
        session_id=session_id,
        artifact_type=artifact_type,
        title=title,
        content=content,
    )
    artifacts = list_artifacts(
        task_id=task_id,
        session_id=session_id,
        limit=200,
    )
    artifact = next((item for item in artifacts if item["id"] == artifact_id), None)
    if artifact is None:
        raise HTTPException(500, "Artifact write failed")
    return artifact


def _resolve_context_query(
    *,
    session_id: str | None,
    profile: str | None,
    sections: list[str] | None,
) -> dict[str, Any]:
    cfg = load_config()
    target_session_id = session_id or cfg.operator.main_session_id
    session = get_session(target_session_id)
    if session is None and target_session_id != cfg.operator.main_session_id:
        raise HTTPException(404, "Session not found")

    session_kind = (
        str(session.get("kind"))
        if session and session.get("kind")
        else "command_session"
    )
    effective_profile = profile or profile_for_session_kind(session_kind)
    if effective_profile not in CONTEXT_PROFILES:
        raise HTTPException(400, f"Unknown context profile: {effective_profile}")

    effective_sections = list(sections or sections_for_profile(effective_profile))
    unknown_sections = sorted(set(effective_sections) - set(ALL_SECTIONS))
    if unknown_sections:
        raise HTTPException(400, f"Unknown context sections: {', '.join(unknown_sections)}")

    context = build_app_context(effective_sections)
    return {
        "profile": effective_profile,
        "session_id": target_session_id,
        "session_kind": session_kind,
        "sections": effective_sections,
        "summary": context_summary_text(context),
        "context": context,
    }


def _seed_branch_workspace(session_id: str, opening_text: str) -> None:
    snapshot = _resolve_context_query(
        session_id=session_id,
        profile=None,
        sections=None,
    )
    append_message(
        session_id=session_id,
        role="assistant",
        content={
            "text": snapshot["summary"],
            "profile": snapshot["profile"],
            "session_kind": snapshot["session_kind"],
            "sections": snapshot["sections"],
            "context": snapshot["context"],
        },
        message_type="context",
        related_event_id=None,
    )
    append_message(
        session_id=session_id,
        role="user",
        content={"text": opening_text},
        message_type="message",
        related_event_id=None,
    )


def _command_def_payload(command_name: str) -> dict[str, Any] | None:
    from aletheia.agent_shell.registry import COMMANDS

    cmd = COMMANDS.get(command_name)
    if cmd is None:
        return None
    return {
        "name": cmd.name,
        "risk_class": cmd.risk_class,
        "risk_level": cmd.risk_level,
        "description": cmd.description,
        "worker_permission_required": cmd.worker_permission_required,
        "operator_permission_required": cmd.operator_permission_required,
        "requires_confirmation": cmd.requires_confirmation,
        "requires_proposal": cmd.requires_proposal,
        "audit_event_type": cmd.audit_event_type,
        "backend_handler": cmd.backend_handler,
    }


async def _preview_operator_command(req: OperatorCommandPreviewRequest) -> dict[str, Any]:
    from aletheia.agent_shell.intent import classify_intent
    from aletheia.agent_shell.operator_store import get_extension_by_slash_command
    from aletheia.agent_shell.permissions import check_permission
    from aletheia.agent_shell.runtime import AgentShellRuntime

    cfg = load_config()
    session_id = req.session_id or cfg.operator.main_session_id
    permission_mode = cfg.operator.permission_mode
    text = req.input.strip()
    payload: dict[str, Any] = {
        "session_id": session_id,
        "input": req.input,
        "permission_scope": "operator",
        "permission_mode": permission_mode,
        "worker_permission_mode": cfg.agent.permission_mode,
        "classification": "empty",
        "intent": "empty",
        "confidence": None,
        "parameters": {},
        "allowed": False,
        "denial_reason": "Please type a question or command.",
        "worker_allowed": None,
        "worker_denial_reason": None,
        "command": None,
        "extension": None,
    }
    if not text:
        return payload

    extension_invocation = AgentShellRuntime._split_extension_invocation(text)
    if extension_invocation is not None:
        slash_command, raw_tail = extension_invocation
        extension = get_extension_by_slash_command(slash_command)
        payload["extension"] = {
            "slash_command": slash_command,
            "status": None if extension is None else extension.get("status"),
            "target_command": None if extension is None else extension.get("target_command"),
            "extension_id": None if extension is None else extension.get("id"),
            "description": None if extension is None else extension.get("description"),
        }
        if extension is None:
            payload.update(
                classification="extension_unknown",
                intent="extension_unknown",
                denial_reason=f"Unknown slash command: {slash_command}",
            )
            return payload

        parameters, parameter_error = AgentShellRuntime._extension_parameters(extension, raw_tail)
        payload["parameters"] = parameters
        payload["command"] = _command_def_payload(str(extension.get("target_command") or ""))
        if parameter_error:
            payload.update(
                classification="extension_invalid_arguments",
                intent="extension_invalid_arguments",
                denial_reason=parameter_error,
            )
            return payload
        if extension.get("status") != "enabled":
            payload.update(
                classification="extension_not_enabled",
                intent="extension_not_enabled",
                denial_reason=(
                    f"Extension {slash_command} exists but is not enabled yet "
                    f"(current status: {extension.get('status')})."
                ),
            )
            return payload

        command_name = str(extension.get("target_command") or "").strip()
        payload["intent"] = command_name or "extension_unknown"
        payload["classification"] = "typed_command"
    else:
        session_kind = "command_session"
        if payload["session_id"]:
            session = get_session(payload["session_id"])
            if session and session.get("kind"):
                session_kind = str(session.get("kind"))
        effective_sections = (
            sections_for_session_kind(session_kind)
            if session_kind != "command_session"
            else ["system", "market", "agents", "trading"]
        )
        ctx = build_app_context(effective_sections)
        summary = extend_summary_with_session_trace(
            context_summary_text(ctx),
            payload["session_id"],
            pending_user_input=text,
        )
        intent = await classify_intent(text, summary)
        payload["intent"] = intent.command
        payload["confidence"] = intent.confidence
        payload["parameters"] = intent.parameters if isinstance(intent.parameters, dict) else {}
        if intent.command == "query":
            payload.update(
                classification="query",
                allowed=True,
                denial_reason=None,
            )
            return payload
        if intent.command == "forbidden":
            payload.update(
                classification="forbidden",
                denial_reason=(
                    "That action is permanently forbidden. The Agent Shell cannot "
                    "access live trading, credentials, or hard risk limits."
                ),
            )
            return payload
        command_name = intent.command
        payload["classification"] = "typed_command"

    payload["command"] = _command_def_payload(command_name)
    if payload["command"] is None:
        payload.update(
            classification="unknown_command",
            allowed=False,
            denial_reason=f"Unknown command: {command_name}",
        )
        return payload

    allowed, denial_reason = check_permission(
        command_name,
        permission_mode,
        permission_scope="operator",
    )
    worker_allowed: bool | None = None
    worker_denial_reason: str | None = None
    if allowed:
        worker_allowed, worker_denial_reason = check_permission(
            command_name,
            cfg.agent.permission_mode,
            permission_scope="worker",
        )
        if not worker_allowed:
            allowed = False
            denial_reason = (
                "This command is currently blocked by worker/global safety. "
                f"{worker_denial_reason or 'Permission denied.'}"
            )
    payload["allowed"] = allowed
    payload["denial_reason"] = denial_reason
    payload["worker_allowed"] = worker_allowed
    payload["worker_denial_reason"] = worker_denial_reason
    return payload


@router.get("/status")
async def operator_status():
    heartbeat = get_latest_heartbeat()
    if heartbeat is None:
        return _operator_status_defaults()
    return _apply_stale_status(heartbeat)


@router.post("/branches")
async def operator_create_branch(payload: OperatorBranchCreateRequest):
    kind = _normalize_session_kind(payload.kind)
    title = (payload.title or payload.user_input or "").strip()
    if not title:
        raise HTTPException(400, "Branch title or user_input is required")

    user_input = (payload.user_input or title).strip()
    if not user_input:
        raise HTTPException(400, "Branch user_input cannot be empty")

    main_session_id = load_config().operator.main_session_id
    parent_session_id = (payload.parent_session_id or main_session_id).strip()
    if not parent_session_id:
        parent_session_id = main_session_id
    if parent_session_id != main_session_id and not _session_belongs_to_operator_tree(
        parent_session_id,
        main_session_id,
    ):
        raise HTTPException(400, "Branch parent must belong to the operator session tree")
    session_id = _generated_branch_session_id(kind)
    create_session_record(
        session_id=session_id,
        user_input=user_input,
        kind=kind,
        parent_session_id=parent_session_id,
        title=title,
    )
    session = get_session(session_id)
    if session is None:
        raise HTTPException(500, "Branch create failed")
    _seed_branch_workspace(session_id, user_input)

    create_event(
        event_type="branch_created",
        source="operator_branch_route",
        session_id=session_id,
        related_entity_type="session",
        related_entity_id=session_id,
        payload={
            "kind": kind,
            "title": title,
            "parent_session_id": parent_session_id,
            "selected_by": payload.selected_by,
            "activate": payload.activate,
        },
    )
    if payload.activate:
        _write_active_branch_selection(
            target_session=session,
            selected_by=payload.selected_by,
        )
    updated = get_session(session_id)
    if updated is None:
        raise HTTPException(500, "Branch create failed")
    return updated


@router.post("/active-branch")
async def operator_select_active_branch(payload: OperatorActiveBranchSelectRequest):
    requested_session_id = payload.session_id.strip()
    if not requested_session_id:
        raise HTTPException(400, "Session id is required")

    main_session_id = load_config().operator.main_session_id
    if requested_session_id == main_session_id:
        return _write_active_branch_selection(
            target_session=None,
            selected_by=payload.selected_by,
        )

    session = get_session(requested_session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    if not _session_belongs_to_operator_tree(requested_session_id, main_session_id):
        raise HTTPException(400, "Active branch must belong to the operator session tree")

    return _write_active_branch_selection(
        target_session=session,
        selected_by=payload.selected_by,
    )


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


@router.get("/commands")
async def operator_commands(limit: int = 100, session_id: str | None = None, query: str | None = None):
    return list_commands(session_id=session_id, limit=limit, query=query)


@router.get("/extensions")
async def operator_extensions(limit: int = 100, status: str | None = None):
    return list_extensions(status=status, limit=limit)


@router.get("/extensions/{extension_id}")
async def operator_extension_detail(extension_id: str):
    extension = get_extension(extension_id)
    if extension is None:
        raise HTTPException(404, "Extension not found")
    return {
        "extension": extension,
        "runs": list_extension_runs(extension_id=extension_id, limit=100),
    }


@router.post("/extensions/{extension_id}/enable")
async def operator_enable_extension(extension_id: str, payload: OperatorExtensionEnableRequest):
    extension = get_extension(extension_id)
    if extension is None:
        raise HTTPException(404, "Extension not found")
    if extension.get("status") != "proposed":
        raise HTTPException(409, "Extension is not waiting for human approval")

    latest_run = None
    latest_run_id = extension.get("latest_run_id")
    if latest_run_id:
        latest_run = get_extension_run(str(latest_run_id))
    if latest_run is None:
        recent_runs = list_extension_runs(extension_id=extension_id, limit=1)
        latest_run = recent_runs[0] if recent_runs else None
    if latest_run is None or latest_run.get("status") != "passed":
        raise HTTPException(409, "Extension has no passing sandbox test")
    if not extension.get("proposal_artifact_id"):
        raise HTTPException(409, "Extension has no enablement proposal artifact")

    enabled_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    session_id = payload.session_id or str(extension.get("session_id") or load_config().operator.main_session_id)
    update_extension(
        extension_id,
        status="enabled",
        enabled_at=enabled_at,
        enabled_by=payload.approved_by,
        session_id=session_id,
    )
    create_event(
        event_type="extension_enabled",
        source="operator_extension_route",
        session_id=session_id,
        related_entity_type="extension",
        related_entity_id=extension_id,
        payload={
            "slash_command": extension.get("slash_command"),
            "approved_by": payload.approved_by,
            "proposal_artifact_id": extension.get("proposal_artifact_id"),
        },
    )
    updated = get_extension(extension_id)
    if updated is None:
        raise HTTPException(500, "Extension enable failed")
    return updated


@router.get("/recommendations")
async def operator_recommendations(limit: int = 10):
    limit = max(1, min(limit, 50))
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return build_recommendations(limit=limit)

    now = time.monotonic()
    with _recommendations_cache_lock:
        cache_value = _recommendations_cache["value"]
        if cache_value is not None:
            ts, cached_limit, cached = cache_value
            if cached_limit == limit and now - ts < 15.0:
                return [dict(item) for item in cached]

    recommendations = build_recommendations(limit=limit)
    with _recommendations_cache_lock:
        _recommendations_cache["value"] = (now, limit, [dict(item) for item in recommendations])
    return recommendations


@router.get("/loop/latest")
async def operator_loop_latest(limit: int = 5):
    """Get the most recent operator loop assessments."""
    from aletheia.storage.repositories import list_knowledge_items
    items = list_knowledge_items(item_type="operator_assessment", limit=limit)
    results = []
    for item in items:
        try:
            tags_data = json.loads(item.get("tags") or "{}") if isinstance(item.get("tags"), str) else {}
        except (json.JSONDecodeError, TypeError):
            tags_data = {}
        results.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "created_at": item.get("created_at"),
            "summary": tags_data,
        })
    return results


@router.post("/loop/trigger")
async def operator_loop_trigger():
    """Manually trigger an operator loop run."""
    import asyncio
    from aletheia.agent_shell.operator_loop import run_operator_loop
    summary = await asyncio.to_thread(run_operator_loop, trigger="manual")
    return summary


@router.get("/events")
async def operator_events(
    limit: int = 100,
    session_id: str | None = None,
    task_id: str | None = None,
):
    return list_events(limit=limit, session_id=session_id, task_id=task_id)


@router.get("/events/stream")
async def operator_events_stream(limit: int = 50, once: bool = False):
    async def _stream():
        seen_ids: set[str] = set()
        backlog = list_events(limit=limit)
        for event in reversed(backlog):
            seen_ids.add(event["id"])
            yield _sse_block("operator_event", event)
        if once:
            return

        while True:
            await asyncio.sleep(1.0)
            latest = list_events(limit=limit)
            fresh = [event for event in reversed(latest) if event["id"] not in seen_ids]
            for event in fresh:
                seen_ids.add(event["id"])
                yield _sse_block("operator_event", event)
            if not fresh:
                yield ": keep-alive\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


def _require_operator_permission(command_name: str) -> None:
    """#40 fix: /pause, /resume, /wake used to mutate runtime.paused without any
    permission check — the same operations are gated by `pause_agents` /
    `resume_agents` command permissions elsewhere. Reuse that permission model
    here so the two paths agree: an operator running in ReadOnly cannot silently
    pause the whole system from the REST route while the same command through
    the command channel would be denied.
    """
    from aletheia.agent_shell.permissions import check_permission

    cfg = load_config()
    allowed, denial = check_permission(
        command_name,
        cfg.operator.permission_mode,
        permission_scope="operator",
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=denial or "Permission denied")


@router.post("/pause")
async def operator_pause():
    _require_operator_permission("pause_agents")
    return await _set_paused(True, event_type="operator_paused", message="Operator paused via API")


@router.post("/resume")
async def operator_resume():
    _require_operator_permission("resume_agents")
    return await _set_paused(False, event_type="operator_resumed", message="Operator resumed via API")


@router.post("/wake")
async def operator_wake():
    _require_operator_permission("resume_agents")
    return await _set_paused(False, event_type="operator_woken", message="Operator wake requested via API")


@router.post("/command-preview")
async def operator_command_preview(req: OperatorCommandPreviewRequest):
    return await _preview_operator_command(req)


@router.post("/command")
async def operator_command(req: OperatorCommandRequest):
    from aletheia.agent_shell.runtime import AgentShellRuntime

    cfg = load_config()
    target_session_id = req.session_id or cfg.operator.main_session_id
    append_message(
        session_id=target_session_id,
        role="user",
        content={"text": req.input},
        message_type="command",
        related_event_id=None,
    )
    create_event(
        event_type="command_received",
        source="operator_command_route",
        session_id=target_session_id,
        related_entity_type="command",
        related_entity_id=target_session_id,
        payload={"input": req.input},
    )
    runtime = AgentShellRuntime()
    result = await runtime.handle(
        req.input,
        permission_mode=cfg.operator.permission_mode,
        permission_scope="operator",
        session_id=target_session_id,
    )
    payload = result.model_dump() if hasattr(result, "model_dump") else result
    command_name = None
    intent_name = payload.get("intent")
    if isinstance(intent_name, str) and _command_def_payload(intent_name) is not None:
        command_name = intent_name
    command_meta = _command_def_payload(command_name) if command_name else None
    permitted = not _looks_like_command_rejection(payload.get("intent", ""), payload.get("response"))
    append_message(
        session_id=target_session_id,
        role="assistant",
        content={
            "text": payload.get("response"),
            "intent": payload.get("intent"),
            "command": command_name,
            "command_meta": command_meta,
            "risk_level": payload.get("risk_level"),
            "needs_confirmation": (
                bool(command_meta.get("requires_confirmation"))
                if isinstance(command_meta, dict)
                else bool(payload.get("needs_confirmation"))
            ),
            "permitted": permitted,
            "action": payload.get("action"),
        },
        message_type="response",
        related_event_id=None,
    )
    create_event(
        event_type=(
            "command_rejected"
            if not permitted
            else "command_executed"
        ),
        source="operator_command_route",
        session_id=target_session_id,
        related_entity_type="command",
        related_entity_id=target_session_id,
        payload={
            "input": req.input,
            "intent": payload.get("intent"),
            "response": payload.get("response"),
        },
    )
    return result


async def _execute_internal_command(command_name: str, payload: OperatorInternalCommandRequest):
    from aletheia.agent_shell.executor import execute_typed_command

    cfg = load_config()
    target_session_id = payload.session_id or cfg.operator.main_session_id
    command_text = payload.user_input or command_name
    append_message(
        session_id=target_session_id,
        role="assistant",
        content={
            "text": command_text,
            "command": command_name,
            "parameters": payload.parameters or {},
        },
        message_type="command",
        related_event_id=None,
    )
    create_event(
        event_type="command_received",
        source="operator_internal_command_route",
        session_id=target_session_id,
        related_entity_type="command",
        related_entity_id=command_name,
        payload={
            "input": command_text,
            "command": command_name,
            "parameters": payload.parameters or {},
        },
    )
    result = await execute_typed_command(
        command_name,
        payload.parameters if isinstance(payload.parameters, dict) else {},
        permission_mode=cfg.operator.permission_mode,
        permission_scope="operator",
        session_id=target_session_id,
        user_input=command_text,
    )
    append_message(
        session_id=target_session_id,
        role="assistant",
        content={
            "text": result.response,
            "command": command_name,
            "permitted": result.permitted,
            "result": result.result,
        },
        message_type="response",
        related_event_id=None,
    )
    create_event(
        event_type="command_executed" if result.permitted else "command_rejected",
        source="operator_internal_command_route",
        session_id=target_session_id,
        related_entity_type="command",
        related_entity_id=command_name,
        payload={
            "input": command_text,
            "command": command_name,
            "permitted": result.permitted,
            "response": result.response,
        },
    )
    return result.model_dump()


@router.post("/internal/decisions")
async def operator_create_decision(payload: OperatorDecisionCreate):
    return _create_typed_artifact(
        artifact_type="operator_decision",
        title=payload.title,
        content=payload.content,
        task_id=payload.task_id,
        session_id=payload.session_id,
    )


@router.post("/internal/repair-decisions")
async def operator_create_repair_decision(payload: OperatorDecisionCreate):
    return _create_typed_artifact(
        artifact_type="repair_decision",
        title=payload.title,
        content=payload.content,
        task_id=payload.task_id,
        session_id=payload.session_id,
    )


@router.post("/internal/command-intents")
async def operator_create_command_intent(payload: OperatorCommandIntentCreate):
    return _create_typed_artifact(
        artifact_type="command_intent",
        title=payload.summary or f"Command intent: {payload.command_name}",
        content={
            "command_name": payload.command_name,
            "user_input": payload.user_input,
            "classification": payload.classification,
            "summary": payload.summary,
            "parameters": payload.parameters or {},
        },
        task_id=payload.task_id,
        session_id=payload.session_id,
    )


@router.post("/internal/commands/execute")
async def operator_execute_internal_command(payload: OperatorExecuteCommandRequest):
    return await _execute_internal_command(
        payload.command_name,
        OperatorInternalCommandRequest(
            session_id=payload.session_id,
            user_input=payload.user_input,
            parameters=payload.parameters,
        ),
    )


@router.post("/internal/commands/{command_name}")
async def operator_internal_command(command_name: str, payload: OperatorInternalCommandRequest):
    return await _execute_internal_command(command_name, payload)


@router.post("/internal/context/query")
async def operator_context_query(payload: OperatorContextQueryRequest):
    return _resolve_context_query(
        session_id=payload.session_id,
        profile=payload.profile,
        sections=payload.sections,
    )


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
    event_type = _task_event_type(task["status"])
    if event_type:
        create_event(
            event_type=event_type,
            source="operator_task_route",
            task_id=task_id,
            related_entity_type="task",
            related_entity_id=task_id,
            payload={"task_type": task["task_type"], "status": task["status"]},
        )
    return task


@router.patch("/internal/tasks/{task_id}")
async def operator_update_task(task_id: str, payload: OperatorTaskUpdate):
    if get_task(task_id) is None:
        # Idempotent: client marking a terminal state on an already-evicted task
        # (e.g. after gateway restart) gets a 200 no-op instead of 404, so the
        # operator loop does not enter Degraded mode for a stale task reference.
        if payload.status in ("completed", "failed"):
            return {"id": task_id, "status": payload.status, "evicted": True}
        raise HTTPException(404, "Task not found")
    updates = payload.model_dump(exclude_none=True)
    update_task(task_id, **updates)
    task = get_task(task_id)
    if task is None:
        raise HTTPException(500, "Task update failed")
    event_type = _task_event_type(task["status"])
    if event_type:
        create_event(
            event_type=event_type,
            source="operator_task_route",
            task_id=task_id,
            related_entity_type="task",
            related_entity_id=task_id,
            payload={"task_type": task["task_type"], "status": task["status"]},
        )
    return task


@router.post("/internal/messages")
async def operator_create_message(payload: OperatorMessageCreate):
    message_id = append_message(**payload.model_dump())
    messages = list_messages(payload.session_id, limit=200)
    message = next((item for item in reversed(messages) if item["id"] == message_id), None)
    if message is None:
        raise HTTPException(500, "Message write failed")
    return message


@router.post("/internal/sessions")
async def operator_create_session(payload: OperatorSessionCreate):
    create_session_record(
        session_id=payload.id,
        user_input=payload.user_input,
        kind=payload.kind,
        parent_session_id=payload.parent_session_id,
        title=payload.title,
    )
    session = get_session(payload.id)
    if session is None:
        raise HTTPException(500, "Session write failed")
    return session


@router.post("/internal/events")
async def operator_create_event(payload: OperatorEventCreate):
    event_id = create_event(**payload.model_dump())
    events = list_events(limit=200)
    event = next((item for item in events if item["id"] == event_id), None)
    if event is None:
        raise HTTPException(500, "Event write failed")
    return event


@router.post("/internal/artifacts")
async def operator_create_artifact(payload: OperatorArtifactCreate):
    artifact_id = create_artifact(**payload.model_dump())
    artifacts = list_artifacts(
        task_id=payload.task_id,
        session_id=payload.session_id,
        limit=200,
    )
    artifact = next((item for item in artifacts if item["id"] == artifact_id), None)
    if artifact is None:
        raise HTTPException(500, "Artifact write failed")
    return artifact


@router.post("/internal/compact")
async def operator_compact(date: str | None = None):
    from aletheia.agent_shell.compaction import build_operator_daily_summary

    cfg = load_config()
    target_date = date or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    existing = _summary_artifact_for_date(cfg.operator.main_session_id, target_date)
    if existing is not None:
        content = existing.get("content") or {}
        path = content.get("path") if isinstance(content, dict) else None
        return {
            "date": target_date,
            "path": str(path or ""),
            "artifact_id": existing["id"],
            "created": False,
        }

    result = build_operator_daily_summary(date_str=target_date)
    artifact_id = create_artifact(
        task_id=None,
        session_id=cfg.operator.main_session_id,
        artifact_type="operator_daily_summary",
        title=f"Operator Daily Summary {target_date}",
        content={
            "date": target_date,
            "path": str(result["path"]),
            "summary_excerpt": result.get("summary_excerpt", ""),
            "sections": result.get("sections", {}),
        },
    )
    create_event(
        event_type="session_compacted",
        source="operator_compaction_route",
        session_id=cfg.operator.main_session_id,
        related_entity_type="operator_memory",
        related_entity_id=artifact_id,
        payload={"date": target_date, "path": str(result["path"])},
    )
    return {
        "date": target_date,
        "path": str(result["path"]),
        "artifact_id": artifact_id,
        "created": True,
    }


@router.post("/internal/llm-session")
async def operator_llm_session(payload: LlmSessionRequest):
    """Multi-turn LLM session for PI-style operator reasoning.

    Runs a goal-directed loop: build context → LLM → tool_call → execute → repeat.
    Returns when the LLM produces a final answer or max_turns is reached.
    """
    from aletheia.agents.helpers import llm_complete
    from aletheia.agent_shell.executor import execute_typed_command
    from aletheia.agent_shell.registry import COMMANDS
    from aletheia.agent_shell.permissions import check_permission

    cfg = load_config()
    session_id = payload.session_id or cfg.operator.main_session_id
    max_turns = min(payload.max_turns, 10)
    permission_mode = cfg.operator.permission_mode

    allowed = set(payload.allowed_commands or [])
    if not allowed:
        allowed = {
            name for name, cmd in COMMANDS.items()
            if cmd.risk_class == "read"
        }

    tool_descriptions = []
    for name in sorted(allowed):
        cmd = COMMANDS.get(name)
        if cmd:
            tool_descriptions.append(f"- {name}: {cmd.description}")
    tools_block = "\n".join(tool_descriptions)

    sections = payload.context_sections or ["system", "market", "agents", "trading"]
    ctx = build_app_context(sections)
    context_text = context_summary_text(ctx)

    system_prompt = (
        "You are the Aletheia operator reasoning engine. You accomplish goals by "
        "reading system state and calling commands. Your available commands:\n"
        f"{tools_block}\n\n"
        "To call a command, respond with EXACTLY this format on its own line:\n"
        "TOOL_CALL: command_name {\"param\": \"value\"}\n\n"
        "When you have enough information to answer the goal, respond with:\n"
        "FINAL_ANSWER: <your conclusion>\n\n"
        "Rules:\n"
        "- Only call commands from the list above\n"
        "- One TOOL_CALL per response\n"
        "- Be concise in your reasoning\n"
        "- If a command fails or is not permitted, adjust your approach"
    )

    messages: list[dict[str, str]] = [
        {"role": "user", "content": f"System context:\n{context_text}\n\nGoal: {payload.goal}"},
    ]
    commands_executed: list[dict[str, Any]] = []
    turns_used = 0

    for _ in range(max_turns):
        turns_used += 1
        content, error = await llm_complete(system_prompt, _messages_to_prompt(messages), max_tokens=800)
        if error:
            return {
                "status": "llm_unavailable",
                "error": error,
                "turns_used": turns_used,
                "commands_executed": commands_executed,
                "final_answer": None,
            }

        if content and "FINAL_ANSWER:" in content:
            answer = content.split("FINAL_ANSWER:", 1)[1].strip()
            return {
                "status": "completed",
                "turns_used": turns_used,
                "commands_executed": commands_executed,
                "final_answer": answer,
            }

        if content and "TOOL_CALL:" in content:
            call_line = content.split("TOOL_CALL:", 1)[1].strip()
            parts = call_line.split(" ", 1)
            cmd_name = parts[0].strip()
            params: dict[str, Any] = {}
            if len(parts) > 1:
                try:
                    params = json.loads(parts[1].strip())
                except (json.JSONDecodeError, ValueError):
                    pass

            if cmd_name not in allowed:
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": f"Error: command '{cmd_name}' not in allowed set."})
                continue

            ok, reason = check_permission(cmd_name, permission_mode, permission_scope="operator")
            if not ok:
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": f"Permission denied: {reason}"})
                commands_executed.append({"command": cmd_name, "permitted": False, "reason": reason})
                continue

            result = await execute_typed_command(
                cmd_name, params,
                permission_mode=permission_mode,
                permission_scope="operator",
                session_id=session_id,
                user_input=cmd_name,
            )
            commands_executed.append({
                "command": cmd_name,
                "permitted": result.permitted,
                "response": result.response[:500],
            })
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"Result of {cmd_name}: {result.response[:800]}"})
            continue

        return {
            "status": "completed",
            "turns_used": turns_used,
            "commands_executed": commands_executed,
            "final_answer": content,
        }

    return {
        "status": "max_turns_reached",
        "turns_used": turns_used,
        "commands_executed": commands_executed,
        "final_answer": None,
    }


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    """Flatten multi-turn messages into a single user prompt for llm_complete."""
    parts = []
    for msg in messages:
        role = msg["role"].upper()
        parts.append(f"[{role}]: {msg['content']}")
    return "\n\n".join(parts)
