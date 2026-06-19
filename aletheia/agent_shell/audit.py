"""Command audit — record every shell command execution."""

import json
import uuid

from aletheia.agent_shell.file_artifacts import append_session_log
from aletheia.storage.sqlite import get_db


def record_command(session_id: str, command: str, parameters: dict,
                   permitted: bool, result_summary: str) -> None:
    """Write audit record to SQLite + JSONL."""
    cmd_id = str(uuid.uuid4())

    db = get_db()
    db.execute(
        "INSERT INTO agent_shell_commands (id, session_id, command, parameters, "
        "permitted, result_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (cmd_id, session_id, command, json.dumps(parameters), int(permitted),
         result_summary[:2000]),
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
        })
    except Exception:
        pass
