"""JSONL event stream writer/reader."""

import hashlib
import json
import threading
from pathlib import Path
from datetime import datetime

from aletheia.config.loader import get_data_dir

_GENESIS_HASH = "0" * 64
_audit_hash_lock = threading.Lock()
_last_audit_hash: str | None = None


def _log_dir() -> Path:
    d = get_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _max_bytes() -> int:
    """JSONL rotation threshold in bytes (from config retention.jsonl_max_mb)."""
    try:
        from aletheia.config.loader import load_config
        return int(load_config().retention.jsonl_max_mb * 1024 * 1024)
    except Exception:
        return 50 * 1024 * 1024


def append_jsonl(filename: str, record: dict) -> None:
    """Append a JSON record to a JSONL file in data/logs/, rotating at a size cap.

    When the file exceeds the cap it is rotated to `{filename}.1` (single cold
    generation, overwriting any prior .1) — content is never deleted.
    """
    path = _log_dir() / filename
    try:
        if path.exists() and path.stat().st_size >= _max_bytes():
            rotated = path.with_name(filename + ".1")
            if rotated.exists():
                rotated.unlink()
            path.rename(rotated)
    except OSError:
        pass  # rotation is best-effort; never block a write
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


def _hash_record(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _get_last_audit_hash() -> str:
    """Return the hash of the last audit record (reads from file on first call)."""
    global _last_audit_hash
    if _last_audit_hash is not None:
        return _last_audit_hash
    path = _log_dir() / "audit.jsonl"
    if not path.exists():
        _last_audit_hash = _GENESIS_HASH
        return _last_audit_hash
    last_line = ""
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line.strip()
    except OSError:
        _last_audit_hash = _GENESIS_HASH
        return _last_audit_hash
    if not last_line:
        _last_audit_hash = _GENESIS_HASH
        return _last_audit_hash
    _last_audit_hash = _hash_record(last_line)
    return _last_audit_hash


def write_audit(event: dict) -> None:
    """Write an audit event to audit.jsonl with hash chain.

    Each record includes `_prev_hash` — the SHA-256 of the previous line on disk.
    Verification: re-hash each line and confirm it matches the next line's _prev_hash.
    """
    global _last_audit_hash
    if "timestamp" not in event:
        event["timestamp"] = datetime.utcnow().isoformat()

    with _audit_hash_lock:
        prev_hash = _get_last_audit_hash()
        event["_prev_hash"] = prev_hash
        line = json.dumps(event, default=str, ensure_ascii=False)
        _last_audit_hash = _hash_record(line)

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


def verify_audit_chain() -> dict:
    """Verify the integrity of audit.jsonl hash chain.

    Returns {"valid": bool, "records": int, "errors": list[str]}.
    """
    path = _log_dir() / "audit.jsonl"
    if not path.exists():
        return {"valid": True, "records": 0, "errors": []}

    errors: list[str] = []
    prev_hash = _GENESIS_HASH
    count = 0

    with path.open("r", encoding="utf-8") as f:
        for i, raw_line in enumerate(f, 1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            count += 1
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                errors.append(f"line {i}: invalid JSON")
                prev_hash = _GENESIS_HASH
                continue

            record_prev = record.get("_prev_hash")
            if record_prev is None:
                # Legacy record before hash chain was added — reset chain
                prev_hash = _hash_record(raw_line)
                continue

            if record_prev != prev_hash:
                errors.append(
                    f"line {i}: _prev_hash mismatch (expected {prev_hash[:16]}…, got {record_prev[:16]}…)"
                )
            prev_hash = _hash_record(raw_line)

    return {"valid": len(errors) == 0, "records": count, "errors": errors}


def write_error(record: dict) -> None:
    """Write an error to errors.jsonl."""
    if "timestamp" not in record:
        record["timestamp"] = datetime.utcnow().isoformat()
    append_jsonl("errors.jsonl", record)
