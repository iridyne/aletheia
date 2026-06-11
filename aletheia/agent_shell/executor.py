"""Deterministic typed-command execution for the operator control plane."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel


class TypedCommandExecutionResult(BaseModel):
    command: str
    permitted: bool
    risk_level: str
    response: str
    result: dict[str, Any] | None = None
    action: dict[str, Any] | None = None
    needs_confirmation: bool = False
    session_id: str


def _build_action(command: str, result: dict[str, Any]) -> dict[str, Any] | None:
    if command == "pause_agents":
        return {"type": "state_change", "key": "paused", "value": True}
    if command == "resume_agents":
        return {"type": "state_change", "key": "paused", "value": False}
    return None


async def execute_typed_command(
    command_name: str,
    parameters: dict[str, Any] | None = None,
    *,
    permission_mode: str | None = None,
    permission_scope: str = "worker",
    session_id: str,
    user_input: str | None = None,
) -> TypedCommandExecutionResult:
    from aletheia.agent_shell.audit import record_command
    from aletheia.agent_shell.commands import HANDLERS
    from aletheia.agent_shell.operator_store import create_session_record, get_session
    from aletheia.agent_shell.permissions import check_permission
    from aletheia.agent_shell.registry import COMMANDS
    from aletheia.agent_shell.synthesis import _format_fallback
    from aletheia.agent_shell.sessions import close_session
    from aletheia.config.loader import load_config

    params = parameters or {}
    text = (user_input or command_name).strip() or command_name
    start_time = time.time()
    if get_session(session_id) is None:
        create_session_record(session_id=session_id, user_input=text, kind="command_session")

    cfg = load_config()
    if permission_mode is not None:
        current_mode = permission_mode
    elif permission_scope == "operator":
        current_mode = cfg.operator.permission_mode
    else:
        current_mode = cfg.agent.permission_mode

    cmd_def = COMMANDS.get(command_name)
    if not cmd_def:
        response = f"Unknown command: {command_name}"
        record_command(session_id, command_name, params, False, response)
        close_session(
            session_id,
            intent=command_name,
            command=None,
            response=response,
            permitted=False,
            start_time=start_time,
        )
        return TypedCommandExecutionResult(
            command=command_name,
            permitted=False,
            risk_level="read",
            response=response,
            session_id=session_id,
        )

    allowed, denial = check_permission(
        command_name,
        current_mode,
        permission_scope=permission_scope,
    )
    if allowed and permission_scope == "operator":
        worker_allowed, worker_denial = check_permission(
            command_name,
            cfg.agent.permission_mode,
            permission_scope="worker",
        )
        if not worker_allowed:
            allowed = False
            denial = (
                "This command is currently blocked by worker/global safety. "
                f"{worker_denial or 'Permission denied.'}"
            )

    if not allowed:
        response = denial or "Permission denied."
        record_command(session_id, command_name, params, False, response)
        close_session(
            session_id,
            intent=command_name,
            command=command_name,
            response=response,
            permitted=False,
            start_time=start_time,
        )
        return TypedCommandExecutionResult(
            command=command_name,
            permitted=False,
            risk_level=cmd_def.risk_level,
            response=response,
            needs_confirmation=cmd_def.requires_confirmation,
            session_id=session_id,
        )

    handler = HANDLERS.get(command_name)
    if not handler:
        response = f"Command '{command_name}' has no handler implemented."
        record_command(session_id, command_name, params, False, response)
        close_session(
            session_id,
            intent=command_name,
            command=command_name,
            response=response,
            permitted=False,
            start_time=start_time,
        )
        return TypedCommandExecutionResult(
            command=command_name,
            permitted=False,
            risk_level=cmd_def.risk_level,
            response=response,
            needs_confirmation=cmd_def.requires_confirmation,
            session_id=session_id,
        )

    result = await handler(params)
    response = _format_fallback(result)
    record_command(session_id, command_name, params, True, response)
    close_session(
        session_id,
        intent=command_name,
        command=command_name,
        response=response,
        permitted=True,
        start_time=start_time,
    )
    return TypedCommandExecutionResult(
        command=command_name,
        permitted=True,
        risk_level=cmd_def.risk_level,
        response=response,
        result=result,
        action=_build_action(command_name, result),
        needs_confirmation=cmd_def.requires_confirmation,
        session_id=session_id,
    )
