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
    append_jsonl("audit.jsonl", event)


def write_agent_run_log(record: dict) -> None:
    """Write an agent run event to agent_runs.jsonl."""
    append_jsonl("agent_runs.jsonl", record)


def write_error(record: dict) -> None:
    """Write an error to errors.jsonl."""
    if "timestamp" not in record:
        record["timestamp"] = datetime.utcnow().isoformat()
    append_jsonl("errors.jsonl", record)
