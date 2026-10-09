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
    # True only on the confirmation gate: the command was not run and waits for
    # the human operator. False once anything ran, failed or was refused.
    needs_confirmation: bool = False
    # True only when the handler ran and reported success. ``permitted`` answers
    # "may this caller run it"; it is not proof that anything happened.
    executed: bool = False
    session_id: str


CONFIRMATION_REQUIRED_RESPONSE = (
    "'{command}' requires operator confirmation and was not executed. "
    "Only the operator can confirm it from the Command Center."
)


def handler_succeeded(result: dict[str, Any]) -> bool:
    """Registry handlers report failure with a top-level ``error`` key."""
    return "error" not in result


def build_action(command: str) -> dict[str, Any] | None:
    """Frontend action hint for the two commands that flip durable agent state.

    Shared by the typed executor and AgentShellRuntime so one command cannot
    produce two different hints. The command result is deliberately not an input:
    the hint describes the requested state change, not the outcome.
    """
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
    inference_call_id: str | None = None,
) -> TypedCommandExecutionResult:
    """Run one registry command for a machine caller (operator-shell, llm-session).

    Every caller here is an agent or model, so nothing it sends can confirm a
    confirmation-gated command; only the human /operator/command route can.
    """
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
    started_monotonic = time.monotonic()

    def record(permitted: bool, response: str) -> None:
        record_command(
            session_id,
            command_name,
            params,
            permitted,
            response,
            inference_call_id,
            duration_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
        )

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
        record(False, response)
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
        record(False, response)
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
            session_id=session_id,
        )

    if cmd_def.requires_confirmation:
        response = CONFIRMATION_REQUIRED_RESPONSE.format(command=command_name)
        record(False, response)
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
            needs_confirmation=True,
            session_id=session_id,
        )

    handler = HANDLERS.get(command_name)
    if not handler:
        response = f"Command '{command_name}' has no handler implemented."
        record(False, response)
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
            session_id=session_id,
        )

    result = await handler(params)
    response = _format_fallback(result)
    executed = handler_succeeded(result)
    record(executed, response)
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
        action=build_action(command_name) if executed else None,
        executed=executed,
        session_id=session_id,
    )
