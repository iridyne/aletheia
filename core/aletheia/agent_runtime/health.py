"""Health monitoring + honest alerting (Component 5).

Computes health from REAL backend signals (recent agent-run failure ratio, kill
switch, LLM availability) and alerts only on transition INTO an unhealthy state
(de-duped via a persisted flag). Never fabricates a positive "healthy" push.
Alerts go through the local operator notification sink (Windows toast and Telegram when configured).
"""

import logging
import math
import os
import time
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.data.freshness import MAX_FUTURE_SKEW
from aletheia.files.jsonl import write_audit
from aletheia.storage.repositories import list_agent_runs

logger = logging.getLogger("aletheia.health")

_SUCCESSFUL_STATUSES = {"completed"}
_FAILED_STATUSES = {
    "failed",
    "error",
    "completed_with_errors",
    "timed_out",
    "stale",
    "rejected_queue_full",
    "cancelled",
    "cancel_requested",
}
_IN_PROGRESS_STATUSES = {"queued", "running"}

# In-process de-dupe by issue kind. Durable warnings must not suppress alerts
# when an independent incident appears before health returns to fully healthy.
_last_alerted_issue_keys: frozenset[str] = frozenset()

# --- Deterministic self-heal state (see HealthConfig.self_heal_*) ---
# Indirection so tests can patch it without signalling the test process. The
# real restart is a controlled restart (#1454): graceful lifespan shutdown, then
# exit 42 → the supervisor respawns fresh code, mirroring POST /api/gateway/restart.
def _request_restart() -> None:
    from aletheia.server.controlled_restart import request_controlled_restart

    request_controlled_restart()
# De-dupe consecutive "self-heal wanted to act but was suppressed" alerts.
# In-process is fine here: a suppressed alert does NOT restart, so the flag
# survives across health cycles within one process; it only needs to reset when
# health recovers (done in run_health_check).
_self_heal_suppressed_alerted = False
# Rate limiting reads from the PERSISTED audit log (self_heal_restart events),
# NOT in-process memory — because the restart (a new process) wipes any
# in-memory counter, so a memory-based limit can never see "I just restarted"
# and would loop every health cycle on any restart-resistant issue. See
# _recent_self_heal_restarts().

# In-process count of consecutive LLM-provider failures (reset on any success).
# Lets compute_health() detect "alive but the model backend is down" — a zombie
# state the process-liveness watchdog cannot see. Resets to 0 on restart, which
# is fine: a restart re-probes the provider on the next agent run anyway.
_llm_consecutive_failures = 0

# Process start time for warmup-window calculations (monotonic — not affected by
# clock adjustments or manual time changes between restarts).
_process_start_time: float = time.monotonic()


# Atomically replaced by the background health job; requests never query these ledgers.
_candidate_path_progress: dict | None = None
_CANDIDATE_PATH_ISSUES = frozenset({
    "candidate_refresh_failed", "candidate_refresh_stalled",
    "candidate_planning_stalled", "candidate_outcome_backlog",
})


def get_candidate_path_progress_snapshot() -> dict:
    from copy import deepcopy

    snapshot = {
        **dict.fromkeys(("generated_at", "last_attempt_at", "last_success_at", "last_snapshot_id",
            "selection_policy", "policy_epoch", "selected_count", "planning_supported_count",
            "observe_only_count", "ready_planning_count", "oldest_ready_at", "expired_claim_count",
            "due_outcome_count", "oldest_due_at", "shadow_capture_status", "shadow_capture_reason")),
        "selection_reason_counts": {}, "blocking_reason_counts": {},
        "availability": {"available": False, "reason": "not_sampled"},
        "issues": [], "ttl_seconds": 1200,
        **(deepcopy(_candidate_path_progress) if _candidate_path_progress else {}),
    }
    try:
        age = _timestamp_age_minutes(snapshot.get("generated_at"))
    except (TypeError, ValueError):
        age = None
    snapshot["age_seconds"] = age * 60 if age is not None else None
    if snapshot.get("generated_at") and (age is None or age * 60 > snapshot["ttl_seconds"]):
        snapshot["availability"] = {"available": False, "reason": "cache_expired"}
    elif age is None and snapshot["availability"]["available"]:
        snapshot["availability"] = {"available": False, "reason": "cache_invalid"}
    return snapshot


def _refresh_candidate_path_progress() -> None:
    global _candidate_path_progress
    from aletheia.storage.repositories import get_candidate_path_progress

    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        cfg = load_config()
        snapshot = get_candidate_path_progress()
        snapshot["ttl_seconds"] = 1200
        if not cfg.universe.enabled:
            snapshot["availability"] = {"available": True, "reason": "disabled"}
            snapshot["issues"] = []
        elif cfg.runtime.paused or not cfg.runtime.scheduler_enabled:
            snapshot["availability"] = {"available": True, "reason": "paused"}
            snapshot["issues"] = []
        else:
            from aletheia.agent_runtime.scheduler import _current_scheduler, scheduled_job_status
            state = scheduled_job_status("health_check")
            scheduler = _current_scheduler()
            if not state["registered"] or scheduler is None:
                snapshot["availability"] = {"available": False, "reason": "not_configured"}
                snapshot["issues"] = []
                _candidate_path_progress = snapshot
                return
            elif state["paused"]:
                snapshot["availability"] = {"available": False, "reason": "health_job_paused"}
                snapshot["issues"] = []
                _candidate_path_progress = snapshot
                return
            else:
                interval = scheduler.get_job("health_check").trigger.interval.total_seconds()
                if not math.isfinite(interval) or interval <= 0:
                    raise ValueError("health_interval_unavailable")
                snapshot["ttl_seconds"] = 2 * interval
            success_age = _timestamp_age_minutes(snapshot.get("last_success_at"))
            stalled_after = 2 * cfg.universe.refresh_interval_min * 60 + 600
            if success_age is not None and success_age * 60 > stalled_after:
                snapshot["issues"].append("candidate_refresh_stalled")
            elif success_age is None:
                if time.monotonic() - _process_start_time > stalled_after:
                    snapshot["issues"].append("candidate_refresh_stalled")
                elif snapshot["availability"]["available"]:
                    snapshot["availability"] = {"available": False, "reason": "initializing"}
        _candidate_path_progress = snapshot
    except Exception as exc:
        logger.warning("candidate progress probe failed: %s", type(exc).__name__)
        _candidate_path_progress = {
            "generated_at": generated_at, "availability": {"available": False, "reason": "probe_failed"},
            "issues": [], "ttl_seconds": 1200,
        }


class _FutureTimestampError(ValueError):
    """A health timestamp is too far ahead of the host clock."""


def _timestamp_age_minutes(value: object) -> float | None:
    """Return signed age in minutes, rejecting impossible future observations."""
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError(f"invalid health timestamp: {value!r}") from None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - timestamp).total_seconds() / 60.0
    if age_minutes < -MAX_FUTURE_SKEW.total_seconds() / 60.0:
        raise _FutureTimestampError(str(value))
    return age_minutes


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
    """Minutes since the newest market snapshot, None if not applicable.

    Returns None when paused or scheduler disabled (non-applicable).
    Returns float('inf') when the scheduler is active but no snapshot has ever
    been collected — the caller decides whether that is initializing or degraded.
    Exceptions propagate so the caller can distinguish probe errors from the
    not-applicable / never-collected cases.
    """
    if cfg.runtime.paused or not cfg.runtime.scheduler_enabled:
        return None
    from aletheia.storage.repositories import latest_market_snapshot_groups
    from aletheia.trading.market_data import market_snapshot_target_groups

    rows = latest_market_snapshot_groups()
    targets = market_snapshot_target_groups(cfg)
    if not targets:
        return None
    if not rows:
        return float("inf")  # scheduler active but snapshot never collected
    by_group = {
        (str(row.get("exchange") or ""), str(row.get("market_type") or "")): row.get("snapshot_at")
        for row in rows
    }
    ages = [
        _timestamp_age_minutes(by_group[group]) if by_group.get(group) else float("inf")
        for group in targets
    ]
    return max(ages, default=float("inf"))


def _market_data_stale_sources(cfg) -> list[tuple[str, str, float]]:
    """Return stale target groups so health can attribute partial-source gaps."""
    if cfg.runtime.paused or not cfg.runtime.scheduler_enabled:
        return []
    from aletheia.storage.repositories import latest_market_snapshot_groups
    from aletheia.trading.market_data import market_snapshot_target_groups

    rows = latest_market_snapshot_groups()
    targets = market_snapshot_target_groups(cfg)
    if not targets:
        return []
    latest = {
        (str(row.get("exchange") or ""), str(row.get("market_type") or "")): row.get("snapshot_at")
        for row in rows
    }
    stale = []
    for venue, market_type in sorted(targets):
        timestamp = latest.get((venue, market_type))
        age = _timestamp_age_minutes(timestamp) if timestamp else float("inf")
        if age > cfg.health.market_data_max_staleness_min:
            stale.append((venue, market_type, age))
    return stale


def _exchange_reachable(cfg=None) -> bool | None:
    """Return the last cached exchange probe result.

    Health/status endpoints are dashboard hot paths. They must not run an
    external exchange request on every frontend poll; the scheduler/startup
    connectivity monitor owns that probe and keeps the last result in memory.

    Returns:
        True   — a cached probe confirmed reachability.
        False  — a cached probe confirmed failure.
        None   — no probe has ever completed (unknown state), or the
                 connectivity module raised; callers must not treat this as
                 reachable.
    """
    try:
        from aletheia.agent_runtime.connectivity import get_status
        status = get_status()
        result = status.get("last_probe") or {}
        if not result:
            return None  # no probe has run yet — unknown, not reachable
        stale_after_sec = max(
            30,
            int(cfg.health.exchange_probe_interval_sec) * 3 if cfg is not None else 180,
        )
        observed_at = float(result.get("ts") or 0)
        age_sec = time.time() - observed_at
        if not math.isfinite(observed_at) or age_sec < 0 or age_sec > stale_after_sec:
            return None
        return result.get("reachable", False)
    except Exception:
        return None  # module error → unknown, not False


def compute_health() -> dict:
    """Compute current health from real backend data.

    Returns a dict with:
      - healthy (bool): True only when status is "healthy".
      - status (str): "healthy" | "degraded" | "initializing".
          "healthy"      — all critical signals have recent positive observations.
          "degraded"     — at least one signal confirmed a negative state.
          "initializing" — no degraded signals, but one or more critical signals
                           lack sufficient evidence (fresh boot, warmup, probe
                           error). Health is not yet established; do not treat
                           as healthy.
      - issues (list[str]): all signal strings, prefixed with "initializing_"
                            for initializing-state entries.
    """
    cfg = load_config()
    issues: list[str] = []
    initializing: list[str] = []
    progress = get_candidate_path_progress_snapshot()
    if cfg.universe.enabled and cfg.runtime.scheduler_enabled and not cfg.runtime.paused:
        issues.extend(issue for issue in progress.get("issues", []) if issue in _CANDIDATE_PATH_ISSUES)
        if not progress["availability"]["available"]:
            initializing.append("candidate_progress_unavailable")

    runs = list_agent_runs(limit=20)
    if runs:
        failed = sum(1 for r in runs if (r.get("status") or "") in _FAILED_STATUSES)
        ratio = failed / len(runs)
        if ratio > cfg.health.max_agent_failure_ratio:
            issues.append(f"agent_failure_ratio:{ratio:.2f}>{cfg.health.max_agent_failure_ratio}")

    if cfg.runtime.kill_switch_active:
        issues.append("kill_switch_active")

    # Paused-but-enabled is a degraded state, not healthy: the trading loop is
    # not running while it is configured to. Surfacing it here is what keeps
    # /api/health HONEST when the LLM startup gate pauses the loop — otherwise a
    # transient record_llm_success() (a manual agent run, a Telegram-triggered
    # completion) could zero the llm_unavailable counter and flip health green
    # while the loop is still paused and dead (the "looks alive, is dead" zombie
    # the gate exists to prevent). A deliberate operator pause is also genuinely
    # "not trading" — health should say so. self-heal treats this as alert-only
    # (no restart), so flagging it cannot trigger a respawn loop.
    if cfg.runtime.paused and cfg.runtime.scheduler_enabled:
        issues.append("trading_paused")

    # Startup Behavior Gate: when the gate holds the trading loop, surface the
    # SPECIFIC blockers (db integrity, exchange sync, reconciliation incident, …)
    # so /api/health is honest about *why* — not just that it is paused.
    try:
        from aletheia.agent_runtime.startup_behavior_gate import read_gate_state
        gate_state = read_gate_state()
        if gate_state and gate_state.get("paused_by_gate"):
            names = ",".join(gate_state.get("blockers") or []) or "unknown"
            issues.append(f"startup_gate_blocked:{names}")
    except Exception as exc:
        logger.warning("startup behavior gate health probe failed: %s", type(exc).__name__)
        initializing.append("startup_gate_probe_error")

    # Market data freshness: distinguish paused/disabled (non-applicable),
    # never collected (initializing or degraded depending on uptime), and stale.
    try:
        stale_min = _market_data_stale_minutes(cfg)
    except _FutureTimestampError:
        issues.append("market_data_timestamp_future")
        stale_min = None
    except Exception as e:
        logger.warning("market-data staleness probe error: %s", e)
        stale_min = None
        if not cfg.runtime.paused and cfg.runtime.scheduler_enabled:
            initializing.append("market_data_probe_error")
    else:
        if stale_min is None:
            pass  # paused or scheduler disabled — non-applicable
        elif math.isinf(stale_min):
            # Scheduler active but snapshot never collected: initializing within
            # the expected first-run window, degraded once that window is past.
            _uptime = time.monotonic() - _process_start_time
            if _uptime > cfg.health.market_data_max_staleness_min * 60:
                issues.append("market_data_never_collected")
            else:
                initializing.append("market_data_initializing")
        elif stale_min > cfg.health.market_data_max_staleness_min:
            stale_sources = _market_data_stale_sources(cfg)
            if stale_sources:
                for venue, market_type, age in stale_sources:
                    age_label = "missing" if math.isinf(age) else f"{age:.0f}min"
                    issues.append(
                        f"market_data_stale:{age_label}:source={venue}/{market_type}"
                    )
            else:
                issues.append(f"market_data_stale:{stale_min:.0f}min")

    if _llm_consecutive_failures >= cfg.health.llm_unavailable_alert_after:
        issues.append(f"llm_unavailable:{_llm_consecutive_failures}")

    if not cfg.runtime.paused:
        reachable = _exchange_reachable(cfg)
        if reachable is None:
            initializing.append("exchange_connectivity_unknown")
        elif not reachable:
            issues.append("exchange_unreachable")

    # Stale-process guard: disk source committed past what this process booted on.
    from aletheia.agent_runtime.build_info import code_drift_issue
    drift = code_drift_issue()
    if drift:
        issues.append(drift)

    # Provenance durability: an active session that was never persisted means every
    # child row carries a NULL runtime_session_id (safe) but observers cannot trace
    # them to a parent lifecycle — surface the degraded state explicitly.
    try:
        from aletheia.agent_runtime.runtime_session import current_session, current_session_durable
        sess = current_session()
        if sess is not None and not current_session_durable():
            issues.append("runtime_session_provenance_degraded")
    except Exception as exc:
        logger.warning("runtime session provenance health probe failed: %s", type(exc).__name__)
        initializing.append("runtime_session_provenance_unknown")

    try:
        from aletheia.portfolio.funding_audit import funding_audit_backlog

        audit_backlog = funding_audit_backlog()
        if audit_backlog["count"]:
            issues.append(
                f"funding_audit_mirror_backlog:{audit_backlog['count']}"
            )
    except Exception as exc:
        logger.warning(
            "funding audit mirror health probe failed: %s", type(exc).__name__,
        )
        initializing.append("funding_audit_mirror_unknown")

    try:
        from aletheia.portfolio.broker_funding_settlement import (
            funding_recovery_state_counts,
        )

        funding_state = funding_recovery_state_counts()
        if funding_state["terminal"]:
            issues.append(
                f"broker_funding_history_expired:{funding_state['terminal']}"
            )
        if funding_state["retry_pending"]:
            issues.append(
                f"broker_funding_recovery_pending:{funding_state['retry_pending']}"
            )
    except Exception as exc:
        logger.warning(
            "broker funding recovery health probe failed: %s",
            type(exc).__name__,
        )
        initializing.append("broker_funding_recovery_unknown")

    try:
        from aletheia.execution.liveness import (
            identity_blocked_modes, overdue_orders, retry_storm_proposals,
        )

        if overdue := overdue_orders():
            issues.append(f"execution_orders_overdue:{len(overdue)}")
        if blocked := identity_blocked_modes():
            issues.append(f"execution_identity_gate_blocked:{','.join(blocked)}")
        if storm := retry_storm_proposals():
            issues.append(f"execution_retry_storm:{len(storm)}")
    except Exception as exc:
        logger.warning("execution liveness health probe failed: %s", type(exc).__name__)
        initializing.append("execution_liveness_unknown")

    if not issues and not initializing:
        status = "healthy"
    elif not issues:
        status = "initializing"
    else:
        status = "degraded"

    return {
        "healthy": status == "healthy",
        "status": status,
        "issues": issues + initializing,
    }


def _send_alert(message: str) -> None:
    """Best-effort operator alert. Never raises."""
    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            "Aletheia health degraded",
            message,
            source="health",
            event_type=None,  # caller writes the specific audit event.
            audit=False,
            error_log=False,
        )
    except Exception as e:
        logger.warning("health alert send failed: %s", e)


def _recent_self_heal_restarts() -> list[float]:
    """Ages (seconds-ago) of self_heal_restart events in the last hour, oldest→newest.

    Reads from the PERSISTED audit log, so the count survives the process replacement
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


def _code_drift_already_tried() -> bool:
    """True if a code_drift restart was attempted in the last hour but drift persists.

    code_drift is supposed to be self-terminating (boot==HEAD after respawn).
    If it persists after a restart, HEAD keeps moving (ongoing dev) — stop
    consuming restart budget and let other signals (market_data_stale) through.
    """
    from datetime import datetime
    from aletheia.files.jsonl import read_jsonl
    now = datetime.utcnow()
    try:
        for e in read_jsonl("audit.jsonl", limit=200):
            if e.get("event_type") != "self_heal_restart":
                continue
            if "code_drift" not in (e.get("reason") or e.get("message", "")):
                continue
            ts = e.get("timestamp")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            age = (now - dt).total_seconds()
            if 0 <= age <= 3600:
                return True
    except Exception:
        logger.warning("health: code-drift remediation history read failed; treated as never remediated", exc_info=True)
    return False


def _trigger_restart(reason: str) -> None:
    """Audit FIRST (synchronous append completes before exit), then restart.

    Under --reload mode: touches a sentinel file so uvicorn's file-watcher
    triggers a clean reload (os._exit would kill the worker but the reloader
    wouldn't respawn it — it only acts on file changes).

    Without --reload: controlled restart (graceful shutdown, then exit 42) so
    the supervisor respawns with fresh code outside its crash budget.

    Only call after _can_self_restart() has confirmed a supervisor/autostart will
    bring the process back — otherwise this would permanently kill the gateway.
    The audit write is also what the NEXT process's rate limit reads back."""
    reload_mode = os.environ.get("ALETHEIA_DEV_RELOAD") == "1"
    if reload_mode:
        from aletheia.server.dev_reload import is_dev_reload_enabled

        # Same gate as every dev-reload path (env AND live_disabled, read now).
        # Under --reload a non-reload restart would not be respawned, so blocked = no restart.
        if not is_dev_reload_enabled():
            write_audit({
                "actor": "system", "source": "self_heal", "event_type": "self_heal_restart_blocked",
                "entity_type": "gateway", "entity_id": "gateway",
                "reason": reason,
                "message": f"Self-heal restart blocked ({reason}): dev reload not permitted while live is enabled",
            })
            return
    write_audit({
        "actor": "system", "source": "self_heal", "event_type": "self_heal_restart",
        "entity_type": "gateway", "entity_id": "gateway",
        "reason": reason,
        "message": f"Self-heal restart: {reason}",
    })
    _send_alert(f"self-heal restart ({reason}) — respawning")

    if reload_mode:
        from aletheia.server.dev_reload import touch_reload_sentinel
        touch_reload_sentinel(f"self_heal_{reason}")
        return

    _request_restart()


def _can_self_restart() -> tuple[bool, str]:
    """Gate a self-heal restart: a supervisor/autostart must exist to respawn us,
    and rate limits (read from the persisted audit log) must not be exceeded.
    Returns (allowed, reason_if_blocked)."""
    from aletheia.gateway.pidfile import supervisor_owns_pid
    if not supervisor_owns_pid():
        # Login/boot activation is not proof of immediate crash recovery.  Only
        # the live supervisor (or the SCM outside this worker) can safely respawn.
        return (False, "no live supervisor — restart would be fatal")

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
    """Kill verified orphan gateway PIDs that aren't us or our supervisor.

    Opportunistic (only called when already unhealthy). ``find_gateway_orphans``
    only matches Aletheia command-line markers. Never tree-kill a process merely
    because it listens on or connects to the dashboard port: the current frontdoor
    is a legitimate listener in the supervisor's process group, and clients are
    unrelated processes. Generic port cleanup remains an explicit lifecycle action,
    where the owner requested start/stop/restart. Returns True if anything was
    cleaned."""
    from aletheia.gateway.pidfile import (
        find_gateway_orphans, kill_pid_tree, read_pid,
    )
    keep = {os.getpid()}
    sup = read_pid()
    if sup:
        keep.add(sup)
    targets = [p for p in find_gateway_orphans() if p not in keep]
    if not targets:
        return False
    write_audit({
        "actor": "system", "source": "self_heal", "event_type": "self_heal_port_cleanup",
        "entity_type": "gateway", "entity_id": "gateway",
        "message": f"Self-heal cleaned verified gateway orphan PIDs: {targets}",
    })
    for p in targets:
        kill_pid_tree(p)
    _send_alert(f"self-heal cleaned verified gateway orphans {targets}")
    return True




def run_health_check() -> dict:
    """Evaluate health; alert on new issue kinds, then self-heal.

    Self-heal is deterministic (no LLM): it acts on the issue strings
    compute_health() already produces. Restart-fixable signals (code drift, stale
    feed) trigger a guarded, rate-limited exit(42) respawn; orphan PIDs get cleaned;
    everything else (LLM down, kill switch, bad strategy runs) is alert-only."""
    global _last_alerted_issue_keys, _self_heal_suppressed_alerted
    cfg = load_config()
    _refresh_candidate_path_progress()
    h = compute_health()
    current_issue_keys = frozenset(
        str(issue).partition(":")[0] for issue in h["issues"]
    )
    new_issue_keys = current_issue_keys - _last_alerted_issue_keys
    if not h["healthy"]:
        if new_issue_keys:
            msg = "; ".join(h["issues"])
            _send_alert(msg)
            write_audit({
                "actor": "system", "source": "scheduler", "event_type": "health_alert",
                "entity_type": "system_health", "message": f"Unhealthy: {msg}",
            })
        _last_alerted_issue_keys = current_issue_keys
    else:
        # Healthy again — reset both dedupe states so the next downturn re-alerts.
        _last_alerted_issue_keys = frozenset()
        _self_heal_suppressed_alerted = False
        return h
    if not cfg.health.self_heal_enabled:
        return h  # legacy alert-only behavior

    issues = h["issues"]
    # Priority: code drift first — safest (self-terminating: boot==HEAD after
    # respawn) and fresh code may also restore a stalled feed. BUT: only try
    # once per hour — if the restart didn't fix the drift (HEAD keeps moving),
    # stop consuming budget and let other signals (market_data_stale) through.
    if cfg.health.self_heal_code_drift and any(
        i.startswith("code_updated_pending_restart") for i in issues
    ):
        if not _code_drift_already_tried():
            ok, why = _can_self_restart()
            if ok:
                _trigger_restart("code_drift")
                return h
            _alert_suppressed("code_drift", why)
            return h
        else:
            logger.info("code_drift restart already attempted within the hour; "
                        "demoting to alert-only, letting other signals through")

    if cfg.health.self_heal_market_data and any(
        i.startswith("market_data_stale") for i in issues
    ):
        # If exchange is unreachable, restart won't help — let connectivity
        # monitor handle pause/resume. Only restart when network is fine but
        # data is still stale (implies a process/code bug).
        if _exchange_reachable(cfg) is False:
            logger.info("market_data_stale but exchange unreachable — "
                        "deferring to connectivity_monitor (no restart)")
        else:
            ok, why = _can_self_restart()
            if ok:
                _trigger_restart("market_data_stale")
                return h
            _alert_suppressed("market_data_stale", why)
        return h

    if cfg.health.self_heal_port_cleanup:
        _heal_port_cleanup()  # opportunistic; audits + alerts itself if it acts

    # llm_unavailable / kill_switch_active / agent_failure_ratio → alert-only
    # (already alerted above on the transition).
    return h
