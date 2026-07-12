"""Structured error recording -- single call writes to BOTH stderr and errors.jsonl.

Usage::

    from aletheia.infra.errors import record_error
    record_error("market_observer", "LLM call failed", exc=e, level="error",
                 extra={"symbol": "BTC/USDT", "run_id": run_id})

This ensures every critical failure has a queryable, structured record in
errors.jsonl (same schema as doctor reads) while still being visible in the
text log for operators tailing stderr.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.infra.errors")


def record_error(
    source: str,
    message: str,
    *,
    exc: BaseException | None = None,
    level: str = "error",
    extra: dict[str, Any] | None = None,
    severity: str | None = None,
) -> dict:
    """Write a structured error to errors.jsonl and the text log (stderr).

    Returns the record written (useful for tests / chaining into notify).

    Args:
        source: component name (e.g. "serenity", "market_observer").
        message: short human-readable description.
        exc: optional exception -- traceback is captured.
        level: "debug" | "info" | "warning" | "error" | "critical".
        extra: additional structured fields (symbol, run_id, stage, ...).
        severity: optional override for the "level" field in errors.jsonl
            (defaults to *level*).
    """
    now = datetime.now(timezone.utc).isoformat()
    tb = (
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        if exc
        else None
    )

    record: dict[str, Any] = {
        "source": source,
        "level": severity or level,
        "title": f"{source}: {message}"[:120],
        "message": str(message)[:1000],
        "timestamp": now,
    }
    if tb:
        record["traceback"] = tb[:4000]
    if extra:
        for k, v in extra.items():
            if k not in record:
                record[k] = v

    # Mirror to errors.jsonl (structured, queryable)
    try:
        from aletheia.files.jsonl import write_error
        write_error(record)
    except Exception:
        pass

    # Mirror to text log (human-visible)
    log = getattr(logger, level, logger.error)
    detail = f"[{source}] {message}"
    if extra:
        detail += " | " + ", ".join(f"{k}={v}" for k, v in extra.items())
    if exc:
        log(detail, exc_info=exc)
    else:
        log(detail)

    return record
