"""Health monitoring + honest alerting (Component 5).

Computes health from REAL backend signals (recent agent-run failure ratio, kill
switch, LLM availability) and alerts only on transition INTO an unhealthy state
(de-duped via a persisted flag). Never fabricates a positive "healthy" push.
Alerts go through the existing Telegram bot when configured.
"""

import asyncio
import logging

from aletheia.config.loader import load_config
from aletheia.files.jsonl import write_audit
from aletheia.storage.repositories import list_agent_runs

logger = logging.getLogger("aletheia.health")

_FAILED_STATUSES = {"failed", "error", "completed_with_errors"}

# In-process de-dupe of consecutive unhealthy alerts.
_last_alerted_unhealthy = False

# In-process count of consecutive LLM-provider failures (reset on any success).
# Lets compute_health() detect "alive but the model backend is down" — a zombie
# state the process-liveness watchdog cannot see. Resets to 0 on restart, which
# is fine: a restart re-probes the provider on the next agent run anyway.
_llm_consecutive_failures = 0


def record_llm_failure() -> None:
    global _llm_consecutive_failures
    _llm_consecutive_failures += 1


def record_llm_success() -> None:
    global _llm_consecutive_failures
    _llm_consecutive_failures = 0


def reset_llm_failures() -> None:
    """Reset the counter (used by tests and at known-good boundaries)."""
    global _llm_consecutive_failures
    _llm_consecutive_failures = 0


def get_llm_consecutive_failures() -> int:
    return _llm_consecutive_failures


def _market_data_stale_minutes(cfg) -> float | None:
    """Minutes since the newest market snapshot, or None if not applicable.

    Returns None (i.e. "do not flag") when paused, when the scheduler is
    disabled, when no snapshot exists yet, or when the timestamp can't be parsed
    — staleness only means something when the feed is supposed to be updating.
    """
    if cfg.runtime.paused or not cfg.runtime.scheduler_enabled:
        return None
    try:
        from datetime import datetime
        from aletheia.storage.repositories import list_market_snapshots
        rows = list_market_snapshots(limit=1)
        if not rows:
            return None  # never collected yet — "not started", not "stale"
        snapshot_at = rows[0].get("snapshot_at")
        if not snapshot_at:
            return None
        ts = datetime.fromisoformat(snapshot_at)  # naive UTC isoformat
        return (datetime.utcnow() - ts).total_seconds() / 60.0
    except Exception as e:
        logger.warning("market-data staleness check failed: %s", e)
        return None


def compute_health() -> dict:
    """Compute current health from real backend data. Returns {healthy, issues}."""
    cfg = load_config()
    issues: list[str] = []

    runs = list_agent_runs(limit=20)
    if runs:
        failed = sum(1 for r in runs if (r.get("status") or "") in _FAILED_STATUSES)
        ratio = failed / len(runs)
        if ratio > cfg.health.max_agent_failure_ratio:
            issues.append(f"agent_failure_ratio:{ratio:.2f}>{cfg.health.max_agent_failure_ratio}")

    if cfg.runtime.kill_switch_active:
        issues.append("kill_switch_active")

    stale_min = _market_data_stale_minutes(cfg)
    if stale_min is not None and stale_min > cfg.health.market_data_max_staleness_min:
        issues.append(f"market_data_stale:{stale_min:.0f}min")

    if _llm_consecutive_failures >= cfg.health.llm_unavailable_alert_after:
        issues.append(f"llm_unavailable:{_llm_consecutive_failures}")

    # Stale-process guard: disk source committed past what this process booted on.
    from aletheia.agent_runtime.build_info import code_drift_issue
    drift = code_drift_issue()
    if drift:
        issues.append(drift)

    return {"healthy": len(issues) == 0, "issues": issues}


def _send_alert(message: str) -> None:
    """Best-effort Telegram alert to all allowed chats. Never raises."""
    cfg = load_config()
    if not cfg.health.alert_via_telegram or not cfg.telegram.enabled:
        return
    try:
        from aletheia.telegram.bot import TelegramBot
        bot = TelegramBot()
        if not bot.configure():
            return

        async def _send_all():
            for chat_id in bot.allowed_chat_ids:
                try:
                    await bot.send_message(chat_id, f"⚠️ Aletheia health: {message}")
                except Exception:
                    pass

        # run_health_check() is sync and may be called from either a plain thread
        # (scheduler) or inside a running event loop (FastAPI route / tests). A bare
        # asyncio.run() raises inside a running loop, and loop.create_task() would
        # fire-and-forget a task this sync function never awaits (it can be GC'd and
        # the alert silently lost). Run the coroutine to completion in a worker thread
        # so it is safe and actually delivered in both contexts.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_send_all())  # no loop in this thread
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, _send_all()).result()
    except Exception as e:
        logger.warning("health alert send failed: %s", e)


def run_health_check() -> dict:
    """Evaluate health; alert + audit only on transition into unhealthy."""
    global _last_alerted_unhealthy
    h = compute_health()
    if not h["healthy"]:
        if not _last_alerted_unhealthy:
            msg = "; ".join(h["issues"])
            _send_alert(msg)
            write_audit({
                "actor": "system", "source": "scheduler", "event_type": "health_alert",
                "entity_type": "system_health", "message": f"Unhealthy: {msg}",
            })
            _last_alerted_unhealthy = True
    else:
        _last_alerted_unhealthy = False  # reset so the next downturn alerts again
    return h
