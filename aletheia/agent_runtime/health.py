"""Health monitoring + honest alerting (Component 5).

Computes health from REAL backend signals (recent agent-run failure ratio, kill
switch, LLM availability) and alerts only on transition INTO an unhealthy state
(de-duped via a persisted flag). Never fabricates a positive "healthy" push.
Alerts go through the existing Telegram bot when configured.
"""

import asyncio
import logging
import os

from aletheia.config.loader import load_config
from aletheia.files.jsonl import write_audit
from aletheia.storage.repositories import list_agent_runs

logger = logging.getLogger("aletheia.health")

_FAILED_STATUSES = {"failed", "error", "completed_with_errors"}

# In-process de-dupe of consecutive unhealthy alerts.
_last_alerted_unhealthy = False

# --- Deterministic self-heal state (see HealthConfig.self_heal_*) ---
# Indirection over os._exit so tests can patch it without killing the test
# process. The real restart is exit(42) → the supervisor respawns fresh code,
# mirroring POST /api/gateway/restart.
_os_exit = os._exit
# De-dupe consecutive "self-heal wanted to act but was suppressed" alerts.
# In-process is fine here: a suppressed alert does NOT restart, so the flag
# survives across health cycles within one process; it only needs to reset when
# health recovers (done in run_health_check).
_self_heal_suppressed_alerted = False
# Rate limiting reads from the PERSISTED audit log (self_heal_restart events),
# NOT in-process memory — because the restart action (os._exit) wipes any
# in-memory counter, so a memory-based limit can never see "I just restarted"
# and would loop every health cycle on any restart-resistant issue. See
# _recent_self_heal_restarts().

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
        from datetime import datetime, timezone
        from aletheia.storage.repositories import list_market_snapshots
        rows = list_market_snapshots(limit=1)
        if not rows:
            return None  # never collected yet — "not started", not "stale"
        snapshot_at = rows[0].get("snapshot_at")
        if not snapshot_at:
            return None
        ts = datetime.fromisoformat(snapshot_at)
        # Snapshots are stored as tz-aware UTC isoformat; older rows may be naive.
        # Normalize both sides to aware-UTC before subtracting.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
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


def _recent_self_heal_restarts() -> list[float]:
    """Ages (seconds-ago) of self_heal_restart events in the last hour, oldest→newest.

    Reads from the PERSISTED audit log, so the count survives the os._exit(42)
    that a restart performs. A memory counter cannot: the restart wipes it, so
    the next process would see zero history and restart again every cycle. Audit
    timestamps are naive-UTC isoformat (write_audit uses datetime.utcnow()); we
    compare against utcnow() the same way. Best-effort: returns [] on any error
    (fail-open is acceptable — the supervisor 5/600 budget is the last backstop)."""
    from datetime import datetime
    from aletheia.files.jsonl import read_jsonl
    ages: list[float] = []
    now = datetime.utcnow()
    try:
        for e in read_jsonl("audit.jsonl", limit=200):
            if e.get("event_type") != "self_heal_restart":
                continue
            ts = e.get("timestamp")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)  # normalise to naive-UTC for subtraction
            age = (now - dt).total_seconds()
            if 0 <= age <= 3600:
                ages.append(age)
    except Exception as e:
        logger.warning("self-heal rate-limit audit read failed: %s", e)
        return []
    return sorted(ages, reverse=True)  # oldest (largest age) first


def _trigger_restart(reason: str) -> None:
    """Audit FIRST (synchronous append completes before exit), then exit(42) so
    the supervisor respawns with fresh code. Mirrors POST /api/gateway/restart.

    Only call after _can_self_restart() has confirmed a supervisor/autostart will
    bring the process back — otherwise this would permanently kill the gateway.
    The audit write is also what the NEXT process's rate limit reads back."""
    write_audit({
        "actor": "system", "source": "self_heal", "event_type": "self_heal_restart",
        "entity_type": "gateway", "entity_id": "gateway",
        "message": f"Self-heal restart: {reason}",
    })
    _send_alert(f"self-heal restart ({reason}) — respawning")
    _os_exit(42)


def _can_self_restart() -> tuple[bool, str]:
    """Gate a self-heal restart: a supervisor/autostart must exist to respawn us,
    and rate limits (read from the persisted audit log) must not be exceeded.
    Returns (allowed, reason_if_blocked)."""
    from aletheia.gateway.pidfile import supervisor_owns_pid
    if not supervisor_owns_pid():
        # No supervisor owns the pidfile — check the OS-level autostart watchdog.
        try:
            from aletheia.gateway import autostart
            if not autostart.status().get("enabled"):
                return (False, "no supervisor/autostart — restart would be fatal")
        except Exception:
            return (False, "no supervisor/autostart — restart would be fatal")

    recent = _recent_self_heal_restarts()  # ages in seconds, last hour, oldest→newest
    cfg = load_config()
    if recent and recent[-1] < cfg.health.self_heal_min_interval_sec:
        # recent[-1] = smallest age = most recent restart
        return (False, "cooldown — too soon since last self-heal restart")
    if len(recent) >= cfg.health.self_heal_max_per_hour:
        return (False, "hourly self-heal restart ceiling reached")
    return (True, "")


def _alert_suppressed(scenario: str, why: str) -> None:
    """Self-heal wanted to act but couldn't (no supervisor / rate-limited). Never
    silently do nothing — alert + audit once, deduped until health recovers."""
    global _self_heal_suppressed_alerted
    if _self_heal_suppressed_alerted:
        return
    msg = f"self-heal suppressed ({scenario}): {why}"
    _send_alert(f"⚠️ {msg}")
    write_audit({
        "actor": "system", "source": "self_heal", "event_type": "self_heal_suppressed",
        "entity_type": "gateway", "entity_id": "gateway", "message": msg,
    })
    _self_heal_suppressed_alerted = True


def _heal_port_cleanup() -> bool:
    """Kill orphan/port-holder gateway PIDs that aren't us or our supervisor.

    Opportunistic (only called when already unhealthy). find_gateway_orphans only
    matches OUR markers (never a foreign process); find_port_holders excludes our
    own pid. We additionally exclude the SUPERVISOR pid (read_pid) — find_port_holders
    does NOT, and in normal operation the supervisor also holds the port, so without
    this guard we would kill our own supervisor and create a new zombie. Returns
    True if anything was cleaned."""
    from aletheia.gateway.pidfile import (
        find_gateway_orphans, find_port_holders, kill_pid_tree, read_pid,
    )
    cfg = load_config()
    keep = {os.getpid()}
    sup = read_pid()
    if sup:
        keep.add(sup)
    targets = [p for p in find_gateway_orphans() if p not in keep]
    targets += [p for p in find_port_holders(cfg.app.dashboard_port)
                if p not in keep and p not in targets]
    if not targets:
        return False
    write_audit({
        "actor": "system", "source": "self_heal", "event_type": "self_heal_port_cleanup",
        "entity_type": "gateway", "entity_id": "gateway",
        "message": f"Self-heal cleaned orphan/port-holder PIDs: {targets}",
    })
    for p in targets:
        kill_pid_tree(p)
    _send_alert(f"self-heal cleaned orphans {targets}")
    return True


def run_health_check() -> dict:
    """Evaluate health; alert on transition into unhealthy, then self-heal.

    Self-heal is deterministic (no LLM): it acts on the issue strings
    compute_health() already produces. Restart-fixable signals (code drift, stale
    feed) trigger a guarded, rate-limited exit(42) respawn; orphan PIDs get cleaned;
    everything else (LLM down, kill switch, bad strategy runs) is alert-only."""
    global _last_alerted_unhealthy, _self_heal_suppressed_alerted
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
        # Healthy again — reset both dedupe flags so the next downturn re-alerts.
        _last_alerted_unhealthy = False
        _self_heal_suppressed_alerted = False
        return h

    cfg = load_config()
    if not cfg.health.self_heal_enabled:
        return h  # legacy alert-only behavior

    issues = h["issues"]
    # Priority: code drift first — safest (self-terminating: boot==HEAD after
    # respawn) and fresh code may also restore a stalled feed.
    if cfg.health.self_heal_code_drift and any(
        i.startswith("code_updated_pending_restart") for i in issues
    ):
        ok, why = _can_self_restart()
        if ok:
            _trigger_restart("code_drift")  # does not return
        _alert_suppressed("code_drift", why)
        return h

    if cfg.health.self_heal_market_data and any(
        i.startswith("market_data_stale") for i in issues
    ):
        ok, why = _can_self_restart()
        if ok:
            _trigger_restart("market_data_stale")  # does not return
        _alert_suppressed("market_data_stale", why)
        return h

    if cfg.health.self_heal_port_cleanup:
        _heal_port_cleanup()  # opportunistic; audits + alerts itself if it acts

    # llm_unavailable / kill_switch_active / agent_failure_ratio → alert-only
    # (already alerted above on the transition).
    return h
