"""Gateway log management.

The gateway's stderr (uvicorn + tracebacks) streams to data/logs/errors.log via a
raw subprocess redirect, so it is not a logging.Handler and a RotatingFileHandler
cannot manage it. Instead we size-cap it explicitly: before each spawn the
supervisor calls rotate_if_needed(), which rolls the file to numeric backups.
tail_logs() reads only the file's tail so a large log can never OOM the CLI.
"""

import os
import re
from pathlib import Path

from aletheia.config.loader import get_data_dir

# Round 2 diagnostic default: keep detailed local logs, split by size.
_MAX_LOG_BYTES = 50 * 1024 * 1024  # 50 MiB
_MAX_LOG_GENERATIONS = 100
# Bytes to read from the end when tailing (enough for a few hundred lines).
_TAIL_READ_BYTES = 256 * 1024

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^'\"\s,}]+)"),
    re.compile(r"(?i)(api[_-]?key|api[_-]?secret|passphrase|token|authorization|x-api-key)(['\"\s:=]+)([^'\"\s,}]+)"),
    re.compile(r"(?i)(bearer\s+)([a-z0-9._~+/=-]{12,})"),
    re.compile(r"(?i)(access[_-]?token|refresh[_-]?token)(['\"\s:=]+)([^'\"\s,}]+)"),
]


def log_path() -> Path:
    return get_data_dir() / "logs" / "gateway.log"


def errors_log_path() -> Path:
    return get_data_dir() / "logs" / "errors.log"


def _rotation_index(path: Path, active_name: str) -> int | None:
    prefix = f"{active_name}."
    if not path.name.startswith(prefix):
        return None
    suffix = path.name[len(prefix):]
    return int(suffix) if suffix.isdigit() else None


def _rotated_paths(path: Path) -> list[Path]:
    try:
        indexed = [
            (idx, candidate)
            for candidate in path.parent.glob(f"{path.name}.*")
            if (idx := _rotation_index(candidate, path.name)) is not None
        ]
    except OSError:
        return []
    return [candidate for _, candidate in sorted(indexed, key=lambda item: item[0])]


def _next_rotation_path(path: Path) -> Path:
    indexes = [
        idx
        for candidate in _rotated_paths(path)
        if (idx := _rotation_index(candidate, path.name)) is not None
    ]
    return path.with_name(f"{path.name}.{(max(indexes) + 1) if indexes else 1}")


def _prune_generations(path: Path, keep_generations: int) -> None:
    if keep_generations < 1:
        return
    rotated = _rotated_paths(path)
    excess = len(rotated) - keep_generations
    if excess <= 0:
        return
    for old in rotated[:excess]:
        try:
            old.unlink()
        except OSError:
            pass


def rotate_if_needed(
    path: Path | None = None,
    max_bytes: int = _MAX_LOG_BYTES,
    keep_generations: int = _MAX_LOG_GENERATIONS,
) -> bool:
    """Roll `path` to the next numeric backup if it exceeds max_bytes.

    Best-effort and never raises: log rotation must not be able to crash the
    supervisor's spawn path. On Windows, a child process holding the file open
    prevents rename — fall back to truncation to bound growth.
    """
    p = path or errors_log_path()
    try:
        if not p.exists() or p.stat().st_size <= max_bytes:
            return False
        try:
            os.replace(p, _next_rotation_path(p))
            _prune_generations(p, keep_generations)
            return True
        except PermissionError:
            # Windows: another process holds the file open (inherited handle).
            # Truncate in place to prevent unbounded growth.
            p.open("w").close()
            return True
    except OSError:
        return False


def _tail_file(p: Path, lines: int) -> list[str]:
    """Return the last `lines` lines of p, reading only the tail of the file."""
    if not p.exists():
        return []
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > _TAIL_READ_BYTES:
                f.seek(-_TAIL_READ_BYTES, os.SEEK_END)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        return text.splitlines()[-lines:]
    except OSError:
        return []


def redact_log_text(text: str) -> str:
    """Redact common secret-bearing fragments before showing log tails in doctor."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        lowered = pattern.pattern.lower()
        if lowered.startswith("(?i)(authorization"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        elif lowered.startswith("(?i)(bearer"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
    return redacted


def tail_file_redacted(p: Path, lines: int = 50) -> list[str]:
    return [redact_log_text(line) for line in _tail_file(p, lines)]


def tail_logs(lines: int = 50) -> list[str]:
    """Return the last N lines of the gateway log (tail-only read, never loads all)."""
    return _tail_file(log_path(), lines)


def log_inventory() -> list[dict]:
    """Return size/rotation facts for known log files and rotated siblings."""
    log_dir = get_data_dir() / "logs"
    known = [
        log_path(),
        errors_log_path(),
        log_dir / "frontdoor-errors.log",
        log_dir / "operator-shell-errors.log",
        log_dir / "audit.jsonl",
        log_dir / "errors.jsonl",
        log_dir / "agent_runs.jsonl",
    ]
    seen: set[Path] = set()
    rows: list[dict] = []
    for base in known:
        for path in [base, *_rotated_paths(base)]:
            if path in seen:
                continue
            seen.add(path)
            try:
                exists = path.exists()
                rows.append({
                    "path": str(path),
                    "name": path.name,
                    "exists": exists,
                    "bytes": path.stat().st_size if exists else 0,
                    "rotated": path != base,
                })
            except OSError:
                rows.append({
                    "path": str(path),
                    "name": path.name,
                    "exists": False,
                    "bytes": 0,
                    "rotated": path != base,
                    "error": "stat failed",
                })
    return rows
