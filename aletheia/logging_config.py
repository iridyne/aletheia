"""Centralized logging configuration for Aletheia.

- Structured stderr handler (human-readable) for interactive use.
- errors.jsonl mirror for critical/error records (machine-queryable).
- Full tracebacks on exception records.

Import once at process start::

    from aletheia.logging_config import configure_logging
    configure_logging()

After configure_logging(), use the standard pattern everywhere::

    logger = logging.getLogger("aletheia.<module>")
"""

import logging
import os
import sys
import traceback
from datetime import datetime, timezone

_jsonl_handler = None
_configured = False


class _JsonlErrorWriter:
    """Lazily import write_error to avoid circular imports at module load."""
    _fn = None

    @classmethod
    def write(cls, record):
        if cls._fn is None:
            try:
                from aletheia.files.jsonl import write_error
                cls._fn = write_error
            except Exception:
                return
        try:
            cls._fn(record)
        except Exception:
            pass


class JsonlErrorMirror(logging.Handler):
    """Mirror ERROR+ records into data/logs/errors.jsonl (structured)."""

    def emit(self, record):
        try:
            exc_text = None
            if record.exc_info and record.exc_info[1]:
                exc_text = "".join(traceback.format_exception(*record.exc_info)).strip()
            title = f"{record.name}: {record.getMessage()[:80]}"
            payload = {
                "source": "logger",
                "logger": record.name,
                "level": record.levelname.lower(),
                "title": title[:120],
                "message": record.getMessage()[:1000],
                "timestamp": datetime.fromtimestamp(
                    record.created, tz=timezone.utc
                ).isoformat(),
            }
            if exc_text:
                payload["traceback"] = exc_text[:4000]
            _JsonlErrorWriter.write(payload)
        except Exception:
            pass


def configure_logging(level=None):
    """Configure root aletheia logger with stderr + JSONL mirror handlers.

    Idempotent: subsequent calls are a no-op.
    """
    global _configured, _jsonl_handler
    if _configured:
        return
    root = logging.getLogger("aletheia")
    if level is None:
        if os.environ.get("ALETHEIA_VERBOSE", "").lower() in ("1", "true", "yes"):
            level = "DEBUG"
        else:
            level = "INFO"
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    if not any(isinstance(h, _StderrHandler) for h in root.handlers):
        stderr_handler = _StderrHandler()
        stderr_handler.setFormatter(_READABLE_FMT)
        root.addHandler(stderr_handler)

    if _jsonl_handler is None:
        _jsonl_handler = JsonlErrorMirror()
        _jsonl_handler.setLevel(logging.ERROR)
    if not any(isinstance(h, JsonlErrorMirror) for h in root.handlers):
        root.addHandler(_jsonl_handler)

    for name in ("httpx", "httpcore", "urllib3", "apscheduler"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True


_READABLE_FMT = logging.Formatter(
    fmt="%(asctime)s %(levelname)-8s %(name)s:%(lineno)d  %(message)s",
    datefmt="%H:%M:%S",
)


class _StderrHandler(logging.StreamHandler):
    def __init__(self):
        super().__init__(sys.stderr)
