"""Operator Agent CLI commands."""

import json
from datetime import datetime, timezone

import typer

from aletheia.agent_shell.operator_store import (
    get_latest_heartbeat,
    get_session,
    list_artifacts,
    list_commands,
    list_messages,
    list_sessions,
)
from aletheia.config.loader import load_config, save_config

agent_app = typer.Typer(no_args_is_help=True)
session_app = typer.Typer(no_args_is_help=True)


def _status_payload() -> dict:
    cfg = load_config()
    heartbeat = get_latest_heartbeat()
    if heartbeat is None:
        return {
            "status": "offline",
            "mode": "Paused" if cfg.runtime.paused else "Observing",
            "permission_mode": cfg.operator.permission_mode,
            "session_id": cfg.operator.main_session_id,
            "current_intent": None,
            "is_busy": False,
            "active_branch_id": None,
            "active_branch_kind": None,
            "active_branch_title": None,
        }
    return heartbeat


def _set_paused(paused: bool) -> None:
    cfg = load_config()
    cfg.runtime.paused = paused
    save_config(cfg)


def _resume_or_exit(cause: str) -> None:
    from aletheia.agent_runtime import resume_gate
    from aletheia.gateway.lifecycle import (
        GatewayResumeApiError,
        resume_running_gateway_via_api_if_running,
    )

    try:
        if resume_running_gateway_via_api_if_running():
            return
    except GatewayResumeApiError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    result = resume_gate.resume_if_llm_available_sync(
        actor="human",
        source="cli",
        cause=cause,
    )
    if not result["allowed"]:
        typer.echo(f"Operator resume blocked: {resume_gate.summarize_resume_gate(result)}", err=True)
        raise typer.Exit(1)


@agent_app.command("status")
def status():
    """Show operator status."""
    data = _status_payload()
    typer.echo(f"Operator: {data['status']}")
    typer.echo(f"Mode: {data.get('mode')}")
    typer.echo(f"Permission: {data.get('permission_mode')}")
    typer.echo(f"Session: {data.get('session_id')}")
    if data.get("active_branch_id"):
        branch_label = data.get("active_branch_title") or data["active_branch_id"]
        typer.echo(f"Active branch: {branch_label}")
    if data.get("current_intent"):
        typer.echo(f"Intent: {data['current_intent']}")


@agent_app.command("pause")
def pause():
    """Pause operator-managed work."""
    _set_paused(True)
    typer.echo("Operator paused.")


@agent_app.command("resume")
def resume():
    """Resume operator-managed work."""
    _resume_or_exit("agent_resume_cli")
    typer.echo("Operator resumed.")


@agent_app.command("wake")
def wake():
    """Wake the operator loop."""
    _resume_or_exit("agent_wake_cli")
    typer.echo("Operator wake requested.")


@agent_app.command("logs")
def logs(
    lines: int = typer.Option(20, "-n", "--lines", help="Number of messages to show."),
    session_id: str | None = typer.Option(None, "--session-id", help="Session to inspect."),
):
    """Show recent operator session messages."""
    target_session_id = session_id
    if target_session_id is None:
        sessions = list_sessions(limit=1)
        if not sessions:
            typer.echo("No operator sessions yet.")
            raise typer.Exit(0)
        target_session_id = sessions[0]["id"]

    messages = list_messages(target_session_id, limit=lines)
    if not messages:
        typer.echo(f"No messages for session {target_session_id}.")
        raise typer.Exit(0)

    typer.echo(f"Session: {target_session_id}")
    for item in messages:
        body = item["content"]
        rendered = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        typer.echo(f"[{item['created_at']}] {item['role']}: {rendered}")


@agent_app.command("sessions")
def sessions(limit: int = typer.Option(20, "--limit", min=1, max=200)):
    """List operator sessions."""
    rows = list_sessions(limit=limit)
    if not rows:
        typer.echo("No operator sessions yet.")
        raise typer.Exit(0)
    for row in rows:
        typer.echo(
            f"{row['id']}  messages={row['message_count']}  commands={row['command_count']}  created_at={row['created_at']}"
        )


@session_app.command("show")
def session_show(session_id: str):
    """Show one operator session."""
    session = get_session(session_id)
    if session is None:
        typer.echo(f"Session not found: {session_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Session: {session['id']}")
    typer.echo(f"Created: {session['created_at']}")
    typer.echo(f"Messages: {session['message_count']}")
    typer.echo(f"Commands: {session['command_count']}")
    if session.get("intent"):
        typer.echo(f"Intent: {session['intent']}")
    if session.get("response"):
        typer.echo(f"Response: {session['response']}")

    messages = list_messages(session_id)
    commands = list_commands(session_id)
    artifacts = list_artifacts(session_id=session_id)

    if messages:
        typer.echo("")
        typer.echo("Messages:")
        for item in messages:
            body = item["content"]
            rendered = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            typer.echo(f"- {item['role']}: {rendered}")

    if commands:
        typer.echo("")
        typer.echo("Commands:")
        for item in commands:
            typer.echo(f"- {item['command']} permitted={item['permitted']}")

    if artifacts:
        typer.echo("")
        typer.echo("Artifacts:")
        for item in artifacts:
            typer.echo(f"- {item['artifact_type']}: {item['title']}")


@agent_app.command("compact")
def compact(date: str | None = typer.Option(None, "--date", help="Target UTC date in YYYY-MM-DD format.")):
    """Compact operator session history into a daily markdown summary."""
    from aletheia.agent_shell.compaction import build_operator_daily_summary

    target_date = date or datetime.now(timezone.utc).date().isoformat()
    result = build_operator_daily_summary(date_str=target_date)
    typer.echo(f"Wrote {result['path']}")


agent_app.add_typer(session_app, name="session", help="Inspect a single operator session.")
