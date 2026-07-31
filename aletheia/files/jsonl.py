"""JSONL event stream writer/reader."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time

from aletheia.config.loader import get_data_dir

logger = logging.getLogger("aletheia.files.jsonl")

_GENESIS_HASH = "0" * 64
_audit_hash_lock = threading.Lock()
_last_audit_hash: str | None = None

_max_bytes_cache: "tuple[float, int] | None" = None
_MAX_BYTES_TTL = 60.0


def _log_dir() -> Path:
    d = get_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _max_bytes() -> int:
    """JSONL rotation threshold in bytes (from config retention.jsonl_max_mb).

    Result is cached for 60 s so repeated write_audit() calls do not trigger
    an extra load_config() disk read on every invocation.
    """
    global _max_bytes_cache
    if _max_bytes_cache is not None:
        ts, val = _max_bytes_cache
        if time.monotonic() - ts < _MAX_BYTES_TTL:
            return val
    try:
        from aletheia.config.loader import load_config
        val = int(load_config().retention.jsonl_max_mb * 1024 * 1024)
    except Exception:
        val = 50 * 1024 * 1024
    _max_bytes_cache = (time.monotonic(), val)
    return val


def append_jsonl(filename: str, record: dict) -> None:
    """Append a JSON record to the runtime home's logs/, rotating at a size cap.

    When the active file exceeds the cap it is rotated to the next numeric
    generation (`{filename}.1`, `{filename}.2`, ...). Rotation is best-effort and
    preserves older generations instead of overwriting them.

    .. note::
        Writing to ``audit.jsonl`` via this function is forbidden.  All audit
        records must go through :func:`write_audit` which maintains the
        SHA-256 hash chain.  Direct writes would silently break chain
        integrity.  See ``docs/design/contracts/audit_chain_contract.md §8``.
    """
    if Path(filename).name == "audit.jsonl":
        raise ValueError(
            "Use write_audit() for audit.jsonl — direct append_jsonl bypasses "
            "the hash chain. See docs/design/contracts/audit_chain_contract.md §8."
        )
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


def _audit_record_ids(path: Path, dedup_ids: set[str]) -> set[str]:
    """Return target ids already present in the audit chain."""
    found: set[str] = set()
    for candidate in _jsonl_paths_chronological(path):
        with candidate.open("rb") as stream:
            for line in stream:
                try:
                    event_id = json.loads(line.decode("utf-8")).get("id")
                    if isinstance(event_id, str) and event_id in dedup_ids:
                        found.add(event_id)
                        if found == dedup_ids:
                            return found
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    continue
    return found


def _repair_incomplete_final_line(path: Path, events: list[dict]) -> None:
    """Finish a provable short write using append-only operations."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("rb") as stream:
        stream.seek(-1, os.SEEK_END)
        if stream.read(1) == b"\n":
            return
        stream.seek(0)
        data = stream.read()
    last_newline = data.rfind(b"\n")
    tail = data[last_newline + 1:]
    try:
        if not isinstance(json.loads(tail), dict):
            raise ValueError("audit record must be an object")
        suffix = b"\n"
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        previous_line = data[:last_newline].splitlines()[-1:] if last_newline >= 0 else []
        previous_text = (
            previous_line[0].decode("utf-8")
            if previous_line
            else _last_non_empty_line(_rotated_paths(path))
        )
        prev_hash = _hash_record(previous_text) if previous_text else _GENESIS_HASH
        target_ids = {
            str(event.get("id")) for event in events if event.get("id")
        }
        existing_ids = _audit_record_ids(path, target_ids) if target_ids else set()
        matches: list[bytes] = []
        for event in events:
            if "timestamp" not in event or event.get("id") in existing_ids:
                continue
            candidate = {**event, "_prev_hash": prev_hash}
            line = json.dumps(candidate, default=str, ensure_ascii=False).encode("utf-8")
            if line.startswith(tail):
                matches.append(line[len(tail):] + b"\n")
        if len(matches) != 1:
            raise OSError("audit chain ends with an unrepairable partial record")
        suffix = matches[0]
    with path.open("ab") as stream:
        stream.write(suffix)


def write_audits_deduplicated(events: list[dict]) -> set[str]:
    """Append a durable-outbox batch once per stable event id.

    The chain is scanned once and the entire batch is appended under one
    cross-process lock. The returned ids are already present or were appended
    successfully; a failed suffix remains absent for a later retry.
    """
    global _last_audit_hash
    if not events:
        return set()
    event_ids = [str(event.get("id") or "") for event in events]
    if any(not event_id for event_id in event_ids) or len(set(event_ids)) != len(event_ids):
        raise ValueError("deduplicated audit events require unique non-empty ids")

    audit_path = _log_dir() / "audit.jsonl"
    completed: set[str] = set()
    try:
        with _audit_hash_lock:
            with _exclusive_file_lock(_log_dir() / ".audit.lock"):
                _repair_incomplete_final_line(audit_path, events)
                completed = _audit_record_ids(audit_path, set(event_ids))
                prev_hash = _get_last_audit_hash(use_cache=False)
                for event, event_id in zip(events, event_ids, strict=True):
                    if event_id in completed:
                        continue
                    if "timestamp" not in event:
                        event["timestamp"] = datetime.now(timezone.utc).isoformat()
                    event["_prev_hash"] = prev_hash
                    line = json.dumps(event, default=str, ensure_ascii=False)
                    _append_jsonl_line(audit_path, line)
                    prev_hash = _hash_record(line)
                    _last_audit_hash = prev_hash
                    completed.add(event_id)
    except OSError as e:
        logger.warning("audit jsonl batch write failed: %s", e)
    return completed


def write_audit(
    event: dict,
    *,
    mirror_db: bool = True,
    dedup_id: str | None = None,
) -> bool:
    """Write an audit event to audit.jsonl with hash chain.

    Each record includes `_prev_hash`: the SHA-256 of the previous line on disk.
    Verification: re-hash each line and confirm it matches the next line's
    `_prev_hash`. By default the event is also mirrored to ``audit_events``.
    Callers that already committed the same authoritative ``AuditEvent`` inside
    their state transaction may pass ``mirror_db=False`` to avoid duplicating
    that row; the append-only JSONL write and hash chain are unchanged.
    Durable outbox callers may also pass a stable ``dedup_id`` matching
    ``event['id']``; retries then check the chain under the same file lock and
    return ``True`` without appending a duplicate. Deduplicated writes require
    ``mirror_db=False`` because the caller already owns structured persistence.
    """
    global _last_audit_hash
    if dedup_id is not None:
        if mirror_db:
            raise ValueError("dedup_id requires mirror_db=False")
        if event.get("id") != dedup_id:
            raise ValueError("dedup_id must match event['id']")
        return dedup_id in write_audits_deduplicated([event])
    if "timestamp" not in event:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()

    jsonl_written = False
    try:
        with _audit_hash_lock:
            with _exclusive_file_lock(_log_dir() / ".audit.lock"):
                audit_path = _log_dir() / "audit.jsonl"
                # An ordinary event has no durable identity tying it to an
                # earlier short write, so it may only finish a complete JSON
                # record's missing newline. Invalid tails must fail closed.
                _repair_incomplete_final_line(audit_path, [])
                # This lock is cross-process, but _last_audit_hash is only
                # process-local. Re-read the chain tail while holding the file
                # lock so frontdoor/backend writes cannot continue from a stale
                # cached hash after the other process appended an audit row.
                prev_hash = _get_last_audit_hash(use_cache=False)
                event["_prev_hash"] = prev_hash
                line = json.dumps(event, default=str, ensure_ascii=False)
                _append_jsonl_line(audit_path, line)
                _last_audit_hash = _hash_record(line)
                jsonl_written = True
    except OSError as e:
        logger.warning("audit jsonl write failed: %s (event_type=%s)",
                       e, event.get("event_type", "?"))

    # Also persist into sqlite.audit_events for structured querying when DB is
    # available (backward-compatible with pure JSONL deployments). Transactional
    # callers can explicitly suppress only this mirror after inserting the same
    # authoritative row inside their state transition.
    if mirror_db:
        _write_audit_row(event)
    return jsonl_written


def _write_audit_row(event: dict) -> None:
    try:
        from aletheia.storage.models import AuditEvent
        from aletheia.storage.repositories import insert_audit_event
    except Exception as e:
        # Pure-JSONL deployments have no storage layer — the JSONL chain above is
        # the source of truth, so skipping the sqlite mirror is expected. Log at
        # debug so an *unexpected* missing-storage condition is still observable
        # (previously this swallowed silently with zero trace).
        logger.debug("audit sqlite mirror skipped (storage layer unavailable): %s", e)
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
             "error_timestamps": list[str], "reanchors": int}.

    error_timestamps parallels errors: each entry is the ISO-8601 created_at
    of the offending record, or "" if unavailable (e.g. invalid JSON).

    Known benign breaks:
    - File rotation boundaries: the first record in a new file may reference a
      hash from before the rotation event.  Re-anchor on boundary mismatch.
    - Historical concurrency race: before the file lock was added, two records
      could share the same _prev_hash.  Tolerate consecutive duplicates.
    """
    path = _log_dir() / "audit.jsonl"
    paths = _jsonl_paths_chronological(path)
    if not paths:
        return {"valid": True, "records": 0, "errors": [], "error_timestamps": [], "reanchors": 0}

    errors: list[str] = []
    error_timestamps: list[str] = []
    prev_hash = _GENESIS_HASH
    prev_record_prev_hash: str | None = None  # the _prev_hash field of the prior record
    count = 0
    reanchors = 0

    # #43 fix: "first record in file" was previously treated as an unconditional
    # benign reanchor. That masked HEAD TRUNCATION of the oldest file — an
    # attacker (or accidental `head -c`) could lop off the beginning of the
    # first audit file and the verifier would silently reanchor from the new
    # first line as if it were normal rotation. Only allow the reanchor when
    # there IS a prior file in the chronological list, i.e. when this really is
    # a rotation boundary. The oldest file's first record must chain from
    # _GENESIS_HASH; anything else is a real head-truncation error.
    for path_index, current_path in enumerate(paths):
        try:
            line_iter = current_path.open("r", encoding="utf-8")
        except OSError:
            continue
        is_first_in_file = True
        is_oldest_file = path_index == 0
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
                    error_timestamps.append("")
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
                    # Pattern 1: first record AFTER a real file rotation
                    # boundary. Only benign when there's a prior file — the
                    # oldest file's first line must chain from _GENESIS_HASH
                    # or it means the head was truncated (#43).
                    if is_first_in_file and not is_oldest_file:
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
                        rec_ts = record.get("created_at") or record.get("timestamp") or ""
                        error_timestamps.append(str(rec_ts))

                prev_record_prev_hash = record_prev
                prev_hash = _hash_record(raw_line)
                is_first_in_file = False

    return {"valid": len(errors) == 0, "records": count, "errors": errors,
            "error_timestamps": error_timestamps, "reanchors": reanchors}


def write_error(record: dict) -> None:
    """Write an error to errors.jsonl."""
    if "timestamp" not in record:
        record["timestamp"] = datetime.now(timezone.utc).isoformat()
    append_jsonl("errors.jsonl", record)
