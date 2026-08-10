"""Command audit — record every shell command execution."""

import json
import uuid

from aletheia.agent_shell.file_artifacts import append_session_log
from aletheia.storage.sqlite import get_db


def record_command(session_id: str, command: str, parameters: dict,
                   permitted: bool, result_summary: str,
                   inference_call_id: str | None = None,
                   duration_ms: int | None = None) -> None:
    """Write audit record to SQLite + JSONL."""
    cmd_id = str(uuid.uuid4())
    stored_duration_ms = max(0, int(duration_ms)) if duration_ms is not None else None

    db = get_db()
    db.execute(
        "INSERT INTO agent_shell_commands (id, session_id, command, parameters, "
        "permitted, result_summary, inference_call_id, duration_ms, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (cmd_id, session_id, command, json.dumps(parameters), int(permitted),
         result_summary[:2000], inference_call_id, stored_duration_ms),
    )
    db.commit()
    append_session_log(
        session_id,
        {
            "type": "command",
            "command_id": cmd_id,
            "command": command,
            "parameters": parameters,
            "permitted": permitted,
            "result_summary": result_summary[:2000],
            "inference_call_id": inference_call_id,
            "duration_ms": stored_duration_ms,
        },
    )

    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "agent_shell",
            "source": "command_center",
            "event_type": "agent_shell_command",
            "entity_type": "command",
            "entity_id": cmd_id,
            "message": f"{'ALLOWED' if permitted else 'DENIED'}: {command}",
            "inference_call_id": inference_call_id,
            "duration_ms": stored_duration_ms,
        })
    except Exception:
        pass
