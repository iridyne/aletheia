"""JSONL event stream writer/reader."""

import json
from pathlib import Path
from datetime import datetime

from aletheia.config.loader import get_data_dir


def _log_dir() -> Path:
    d = get_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_jsonl(filename: str, record: dict) -> None:
    """Append a JSON record to a JSONL file in data/logs/."""
    path = _log_dir() / filename
    line = json.dumps(record, default=str, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_jsonl(filename: str, limit: int = 100) -> list[dict]:
    """Read the last N records from a JSONL file."""
    path = _log_dir() / filename
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    result = []
    for line in lines[-limit:]:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def write_audit(event: dict) -> None:
    """Write an audit event to audit.jsonl."""
    if "timestamp" not in event:
        event["timestamp"] = datetime.utcnow().isoformat()
    # Keep the JSONL audit stream as the canonical external log format.
    append_jsonl("audit.jsonl", event)

    # Also persist into sqlite.audit_events for structured querying when DB is
    # available (backward-compatible with pure JSONL deployments).
    _write_audit_row(event)


def _write_audit_row(event: dict) -> None:
    try:
        from aletheia.storage.models import AuditEvent
        from aletheia.storage.repositories import insert_audit_event
    except Exception:
        return

    payload = dict(event)
    # Support both historical keys (before/after) and current keys
    # (before_state/after_state).
    if "before_state" not in payload and "before" in payload:
        payload["before_state"] = _serialize_state(payload.pop("before"))
    elif "before_state" in payload and isinstance(payload["before_state"], (dict, list)):
        payload["before_state"] = _serialize_state(payload["before_state"])
    if "after_state" not in payload and "after" in payload:
        payload["after_state"] = _serialize_state(payload.pop("after"))
    elif "after_state" in payload and isinstance(payload["after_state"], (dict, list)):
        payload["after_state"] = _serialize_state(payload["after_state"])

    try:
        insert_audit_event(AuditEvent(**payload))
    except Exception:
        # Audit table may not exist before migrations run in some tests.
        return


def _serialize_state(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def write_agent_run_log(record: dict) -> None:
    """Write an agent run event to agent_runs.jsonl."""
    append_jsonl("agent_runs.jsonl", record)


def write_error(record: dict) -> None:
    """Write an error to errors.jsonl."""
    if "timestamp" not in record:
        record["timestamp"] = datetime.utcnow().isoformat()
    append_jsonl("errors.jsonl", record)
