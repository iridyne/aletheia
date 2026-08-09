"""Per-provider LLM completion counters for observability.

This is a *pure instrumentation* module. It records how many real completion
requests are issued to each provider/model pair, how many succeeded, and how
many failed -- and periodically logs a one-line summary so operators can see the
real per-provider call volume directly in the logs.

The intended (and only) call site is
``aletheia.model_provider.broker.brokered_complete``, which is the lowest
common point that *every* brokered LLM completion passes through:

* agent completions go ``complete_with_fallback`` -> ``brokered_complete``
* the model server routes (``server/routes/model.py``) call ``brokered_complete``
  directly
* the actual ``provider.complete()`` HTTP call is executed inside the broker's
  worker, so one ``brokered_complete`` invocation == one real completion request.

Provider id
-----------
The counter key is the ``"provider_name/model"`` string -- e.g.
``"sensenova/sensenova-6.7-flash-lite"`` or ``"anthropic_compatible/LongCat-2.0"``.
This matches the ``provider_name/model`` identifiers already used elsewhere in
Aletheia's logs, so the counters can be cross-referenced with the existing
``aletheia.model_provider.fallback`` / ``aletheia.agents`` log lines.

This module intentionally introduces no third-party dependencies and changes
no existing behaviour: it only exposes record / snapshot /
maybe_log_summary and is invoked as a side effect.

Thread safety
-------------
All counters live in module-level dicts guarded by _COUNTS_LOCK. The broker
executes completions on a dedicated asyncio loop, but callers submit from many
threads (Uvicorn loop, APScheduler workers, CLI), so a lock is required to
avoid lost updates and "dict changed size during iteration" races.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

# At most one summary line is emitted every _LOG_INTERVAL_SEC seconds.
_LOG_INTERVAL_SEC: float = 600.0

# provider_id -> {"calls": int, "success": int, "fail": int}.
# Always CUMULATIVE since process start; never reset when a summary is logged.
_COUNTS: dict[str, dict[str, int]] = {}

# Cumulative calls per provider_id captured at the previous summary, used to
# derive the per-window delta printed in the log line. Never affects the
# cumulative _COUNTS totals.
_LAST_PRINTED_CALLS: dict[str, int] = {}

_COUNTS_LOCK = threading.Lock()
# Initialised so the first completion triggers an immediate baseline summary
# (useful to confirm the instrumentation is live after a deploy).
_LAST_LOG_TS = time.monotonic() - _LOG_INTERVAL_SEC

_LOGGER = logging.getLogger("aletheia.observability.llm_metrics")
def record(provider_id: str, ok: bool) -> None:
    """Record one real completion attempt for provider_id.

    Call this exactly once per brokered_complete invocation: on success with
    ok=True and on any raised error with ok=False.

    Args:
        provider_id: "provider_name/model" string, e.g.
            "sensenova/sensenova-6.7-flash-lite".
        ok: True if the completion returned a usable result, False if this
            attempt raised (provider error, broker timeout, queue-full, ...).
    """
    if not provider_id:
        return
    with _COUNTS_LOCK:
        metrics = _COUNTS.get(provider_id)
        if metrics is None:
            metrics = {"calls": 0, "success": 0, "fail": 0}
            _COUNTS[provider_id] = metrics
        metrics["calls"] += 1
        if ok:
            metrics["success"] += 1
        else:
            metrics["fail"] += 1
    # Throttled logging is performed without holding the lock (logging may block
    # on a slow handler and must not stall concurrent completions).
    maybe_log_summary(_LOGGER)


def snapshot() -> dict[str, Any]:
    """Return a copy of the cumulative per-provider counters.

    Returns:
        A dict with:
            "providers": mapping of provider_id ->
                {"calls", "success", "fail"} (cumulative since start).
            "total": aggregate {"calls", "success", "fail"}.
    """
    with _COUNTS_LOCK:
        per_provider = {pid: dict(metrics) for pid, metrics in _COUNTS.items()}
    totals = {"calls": 0, "success": 0, "fail": 0}
    for metrics in per_provider.values():
        totals["calls"] += metrics["calls"]
        totals["success"] += metrics["success"]
        totals["fail"] += metrics["fail"]
    return {"providers": per_provider, "total": totals}
def maybe_log_summary(logger: logging.Logger) -> None:
    """Log a one-line per-provider summary at most once per _LOG_INTERVAL_SEC.

    The cumulative counters are not reset when a summary is emitted -- only
    the print frequency is throttled. Safe to call on every record.

    The per-provider number printed is the *window* delta (calls since the
    previous summary); the trailing cumulative since start totals are the
    never-reset grand totals.

    Args:
        logger: Logger used for the INFO summary line.
    """
    global _LAST_LOG_TS
    now = time.monotonic()
    with _COUNTS_LOCK:
        if now - _LAST_LOG_TS < _LOG_INTERVAL_SEC:
            return
        window_parts: list[str] = []
        total_calls = total_ok = total_fail = 0
        for pid in sorted(_COUNTS):
            metrics = _COUNTS[pid]
            prev = _LAST_PRINTED_CALLS.get(pid, 0)
            window = metrics["calls"] - prev
            if window > 0:
                window_parts.append(f"{pid}={window}")
            total_calls += metrics["calls"]
            total_ok += metrics["success"]
            total_fail += metrics["fail"]
        # Advance the checkpoint for the next window delta.
        _LAST_PRINTED_CALLS.clear()
        _LAST_PRINTED_CALLS.update(
            {pid: metrics["calls"] for pid, metrics in _COUNTS.items()}
        )
        _LAST_LOG_TS = now

    if window_parts:
        detail = ", ".join(window_parts)
    else:
        detail = "(no calls in last window)"
    logger.info(
        "LLM call counts (last window): %s; cumulative since start: "
        "calls=%d, ok=%d, fail=%d",
        detail,
        total_calls,
        total_ok,
        total_fail,
    )
