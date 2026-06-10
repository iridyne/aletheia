"""Command audit — record every shell command execution."""

import json
import uuid

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
         result_summary[:500]),
    )
    db.commit()

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
