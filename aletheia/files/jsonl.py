"""JSONL event stream writer/reader."""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import threading

from aletheia.config.loader import get_data_dir

logger = logging.getLogger("aletheia.files.jsonl")

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

    When the active file exceeds the cap it is rotated to the next numeric
    generation (`{filename}.1`, `{filename}.2`, ...). Rotation is best-effort and
    preserves older generations instead of overwriting them.
    """
    path = _log_dir() / filename
    line = json.dumps(record, default=str, ensure_ascii=False)
    _append_jsonl_line(path, line)


def _append_jsonl_line(path: Path, line: str) -> None:
    """Append a pre-serialized line after best-effort rotation.

    Audit logging uses this helper so the bytes used for the hash are exactly
    the bytes written to disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _rotate_jsonl_if_needed(path)
    except OSError:
        pass  # rotation is best-effort; never block a write
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _rotation_index(path: Path, active_name: str) -> int | None:
    prefix = f"{active_name}."
    if not path.name.startswith(prefix):
        return None
    suffix = path.name[len(prefix):]
    if not suffix.isdigit():
        return None
    return int(suffix)


def _rotated_paths(path: Path) -> list[Path]:
    paths: list[tuple[int, Path]] = []
    try:
        for candidate in path.parent.glob(f"{path.name}.*"):
            index = _rotation_index(candidate, path.name)
            if index is not None:
                paths.append((index, candidate))
    except OSError:
        return []
    return [candidate for _, candidate in sorted(paths, key=lambda item: item[0])]


def _jsonl_paths_chronological(path: Path) -> list[Path]:
    paths = _rotated_paths(path)
    if path.exists():
        paths.append(path)
    return paths


def _next_rotation_path(path: Path) -> Path:
    indexes = [
        index
        for candidate in _rotated_paths(path)
        if (index := _rotation_index(candidate, path.name)) is not None
    ]
    next_index = (max(indexes) + 1) if indexes else 1
    return path.with_name(f"{path.name}.{next_index}")


def _rotate_jsonl_if_needed(path: Path) -> None:
    if path.exists() and path.stat().st_size >= _max_bytes():
        path.rename(_next_rotation_path(path))


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


def _last_non_empty_line(paths: list[Path]) -> str:
    last_line = ""
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last_line = line.strip()
        except OSError:
            continue
    return last_line


def _get_last_audit_hash(*, use_cache: bool = True) -> str:
    """Return the hash of the last audit record across active + rotated files."""
    global _last_audit_hash
    if use_cache and _last_audit_hash is not None:
        return _last_audit_hash
    path = _log_dir() / "audit.jsonl"
    last_line = _last_non_empty_line(_jsonl_paths_chronological(path))
    if not last_line:
        _last_audit_hash = _GENESIS_HASH
        return _last_audit_hash
    _last_audit_hash = _hash_record(last_line)
    return _last_audit_hash


@contextmanager
def _exclusive_file_lock(lock_path: Path):
    """Cross-process exclusive lock used to serialize audit hash-chain writes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_audit(event: dict) -> None:
    """Write an audit event to audit.jsonl with hash chain.

    Each record includes `_prev_hash`: the SHA-256 of the previous line on disk.
    Verification: re-hash each line and confirm it matches the next line's
    `_prev_hash`.
    """
    global _last_audit_hash
    if "timestamp" not in event:
        event["timestamp"] = datetime.utcnow().isoformat()

    try:
        with _audit_hash_lock:
            with _exclusive_file_lock(_log_dir() / ".audit.lock"):
                prev_hash = _get_last_audit_hash(use_cache=False)
                event["_prev_hash"] = prev_hash
                line = json.dumps(event, default=str, ensure_ascii=False)
                _append_jsonl_line(_log_dir() / "audit.jsonl", line)
                _last_audit_hash = _hash_record(line)
    except OSError as e:
        logger.warning("audit jsonl write failed: %s (event_type=%s)",
                       e, event.get("event_type", "?"))

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
    except Exception as e:
        logger.warning("audit sqlite write failed: %s (event_type=%s)",
                       e, payload.get("event_type", "?"))


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
    """Verify the integrity of the audit JSONL hash chain.

    Returns {"valid": bool, "records": int, "errors": list[str],
             "reanchors": int}.

    Known benign breaks:
    - File rotation boundaries: the first record in a new file may reference a
      hash from before the rotation event.  Re-anchor on boundary mismatch.
    - Historical concurrency race: before the file lock was added, two records
      could share the same _prev_hash.  Tolerate consecutive duplicates.
    """
    path = _log_dir() / "audit.jsonl"
    paths = _jsonl_paths_chronological(path)
    if not paths:
        return {"valid": True, "records": 0, "errors": [], "reanchors": 0}

    errors: list[str] = []
    prev_hash = _GENESIS_HASH
    prev_record_prev_hash: str | None = None  # the _prev_hash field of the prior record
    count = 0
    reanchors = 0

    for current_path in paths:
        try:
            line_iter = current_path.open("r", encoding="utf-8")
        except OSError:
            continue
        is_first_in_file = True
        with line_iter as f:
            for i, raw_line in enumerate(f, 1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                count += 1
                label = f"{current_path.name}:{i}"
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    errors.append(f"{label}: invalid JSON")
                    prev_hash = _GENESIS_HASH
                    prev_record_prev_hash = None
                    is_first_in_file = False
                    continue

                record_prev = record.get("_prev_hash")
                if record_prev is None:
                    prev_hash = _hash_record(raw_line)
                    prev_record_prev_hash = None
                    is_first_in_file = False
                    continue

                if record_prev != prev_hash:
                    benign = False
                    # Pattern 1: first record after file rotation boundary
                    if is_first_in_file:
                        benign = True
                    # Pattern 2: concurrent-writer race — this record shares
                    # the same _prev_hash as the previous record
                    elif prev_record_prev_hash and record_prev == prev_record_prev_hash:
                        benign = True

                    if benign:
                        reanchors += 1
                    else:
                        errors.append(
                            f"{label}: _prev_hash mismatch "
                            f"(expected {prev_hash[:16]}..., got {record_prev[:16]}...)"
                        )

                prev_record_prev_hash = record_prev
                prev_hash = _hash_record(raw_line)
                is_first_in_file = False

    return {"valid": len(errors) == 0, "records": count, "errors": errors,
            "reanchors": reanchors}


def write_error(record: dict) -> None:
    """Write an error to errors.jsonl."""
    if "timestamp" not in record:
        record["timestamp"] = datetime.utcnow().isoformat()
    append_jsonl("errors.jsonl", record)
