"""Health monitoring + honest alerting (Component 5).

Computes health from REAL backend signals (recent agent-run failure ratio, kill
switch, LLM availability) and alerts only on transition INTO an unhealthy state
(de-duped via a persisted flag). Never fabricates a positive "healthy" push.
Alerts go through the local operator notification sink (Windows toast and Telegram when configured).
"""

import logging
import os

from aletheia.config.loader import load_config
from aletheia.files.jsonl import write_audit
from aletheia.storage.repositories import list_agent_runs

logger = logging.getLogger("aletheia.health")

_FAILED_STATUSES = {"failed", "error", "completed_with_errors", "timed_out"}

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


def _mo_ran_in_current_session() -> bool:
    """Return True if market_observer completed at least one run since this session started.

    Warmup guard: after any restart (manual, git-hook, self-heal) the scheduler
    needs up to one MO interval (~15 min) before the first run fires.  Using
    session uptime would reset on every restart and permanently suppress the
    alert during high-churn periods.  Checking agent_runs avoids that — once
    MO has actually executed, we know warmup is done regardless of how many
    restarts occurred.

    Returns True on any DB error so the caller defaults to alerting (safer
    than silently suppressing a real stall).
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        session_row = db.execute(
            "SELECT started_at FROM runtime_sessions WHERE ended_at IS NULL"
            " ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not session_row or not session_row[0]:
            return True
        run_row = db.execute(
            "SELECT id FROM agent_runs WHERE agent_name='market_observer'"
            " AND started_at >= ? LIMIT 1",
            (session_row[0],),
        ).fetchone()
        return run_row is not None
    except Exception:
        return True


def _observer_stale_minutes() -> float | None:
    """Minutes since the last opportunity was created. None if no data."""
    try:
        from datetime import datetime, timezone
        from aletheia.storage.sqlite import get_db
        row = get_db().execute("SELECT MAX(created_at) as t FROM opportunities").fetchone()
        if not row or not row["t"]:
            return None
        dt = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return None


def _exchange_reachable(cfg=None) -> bool:
    """Return the last cached exchange probe result.

    Health/status endpoints are dashboard hot paths. They must not run an
    external exchange request on every frontend poll; the scheduler/startup
    connectivity monitor owns that probe and keeps the last result in memory.
    """
    try:
        from aletheia.agent_runtime.connectivity import get_status
        result = (get_status().get("last_probe") or {})
        if not result:
            return True
        return result.get("reachable", False)
    except Exception:
        return False


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
    except Exception:
        pass

    stale_min = _market_data_stale_minutes(cfg)
    if stale_min is not None and stale_min > cfg.health.market_data_max_staleness_min:
        issues.append(f"market_data_stale:{stale_min:.0f}min")

    if _llm_consecutive_failures >= cfg.health.llm_unavailable_alert_after:
        issues.append(f"llm_unavailable:{_llm_consecutive_failures}")

    # Observer stale: Market Observer should produce opportunities every ~15 min.
    # Only check after MO has completed at least one run in the current session —
    # before that, the scheduler is still warming up after a restart (manual,
    # git-hook, or self-heal).  Keying on agent_runs rather than session uptime
    # means high-churn restart periods don't permanently suppress the alert.
    if not cfg.runtime.paused and cfg.runtime.scheduler_enabled:
        if _mo_ran_in_current_session():
            obs_age = _observer_stale_minutes()
            if obs_age is not None and obs_age > 45:
                issues.append(f"observer_stale:{obs_age:.0f}min")

    # Exchange connectivity: can't trade if the exchange API is unreachable.
    if not cfg.runtime.paused:
        if not _exchange_reachable(cfg):
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
    except Exception:
        pass

    return {"healthy": len(issues) == 0, "issues": issues}


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
        pass
    return False


def _trigger_restart(reason: str) -> None:
    """Audit FIRST (synchronous append completes before exit), then restart.

    Under --reload mode: touches a sentinel file so uvicorn's file-watcher
    triggers a clean reload (os._exit would kill the worker but the reloader
    wouldn't respawn it — it only acts on file changes).

    Without --reload: exit(42) so the supervisor respawns with fresh code.

    Only call after _can_self_restart() has confirmed a supervisor/autostart will
    bring the process back — otherwise this would permanently kill the gateway.
    The audit write is also what the NEXT process's rate limit reads back."""
    write_audit({
        "actor": "system", "source": "self_heal", "event_type": "self_heal_restart",
        "entity_type": "gateway", "entity_id": "gateway",
        "reason": reason,
        "message": f"Self-heal restart: {reason}",
    })
    _send_alert(f"self-heal restart ({reason}) — respawning")

    if os.environ.get("ALETHEIA_DEV_RELOAD") == "1":
        from aletheia.server.dev_reload import touch_reload_sentinel
        touch_reload_sentinel(f"self_heal_{reason}")
        return

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


# --- Ranker admission-starvation detector ---
# Fires when the scheduler is producing opportunities but Ranker is consistently
# returning 0 admitted candidates across a sustained window. "0 proposal" is
# indistinguishable from "system down" on the outside (see issue #329); these
# constants define when that silence becomes signal-worthy.
_RANKER_STARVATION_WINDOW_HOURS = 3
_RANKER_STARVATION_MIN_RUNS = 6       # ≥ ~30 min of actual Ranker activity
_RANKER_STARVATION_MIN_OPPS = 20      # enough eligible supply to make 0 admissions meaningful
_RANKER_ONLINE_COVERAGE_MIN = 0.60    # skip if machine was offline most of the window


def _ranker_session_coverage(window_hours: float) -> float:
    """Fraction of the past `window_hours` covered by an active runtime_session.

    Returns 0.0 on any error (fail-closed: if we can't tell we're online, don't
    suppress the starvation check).
    """
    try:
        from datetime import datetime, timezone, timedelta
        from aletheia.storage.sqlite import get_db
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=window_hours)).isoformat()
        rows = get_db().execute(
            "SELECT started_at, ended_at FROM runtime_sessions"
            " WHERE COALESCE(ended_at, datetime('now')) >= ? ORDER BY started_at",
            (cutoff,),
        ).fetchall()
        if not rows:
            return 0.0
        window_sec = window_hours * 3600.0
        covered = 0.0
        for row in rows:
            s_raw = row[0] if isinstance(row, (list, tuple)) else row["started_at"]
            e_raw = row[1] if isinstance(row, (list, tuple)) else row["ended_at"]
            try:
                s = datetime.fromisoformat(str(s_raw).replace("Z", "+00:00"))
                if s.tzinfo is None:
                    s = s.replace(tzinfo=timezone.utc)
                s = max(s, now - timedelta(hours=window_hours))
                e = (
                    datetime.fromisoformat(str(e_raw).replace("Z", "+00:00"))
                    if e_raw
                    else now
                )
                if e.tzinfo is None:
                    e = e.replace(tzinfo=timezone.utc)
                e = min(e, now)
                if e > s:
                    covered += (e - s).total_seconds()
            except (ValueError, TypeError):
                continue
        return min(covered / window_sec, 1.0)
    except Exception as exc:
        logger.debug("_ranker_session_coverage failed: %s", exc)
        return 0.0


def _check_ranker_admission_starvation() -> None:
    """Emit an infra_event when Ranker has been running but admitting 0 candidates.

    Guards (all must pass before emitting):
    1. Machine was online ≥ 60 % of the window — avoids nightly-shutdown false positives.
    2. Ranker completed ≥ MIN_RUNS in the window.
    3. Zero of those runs emitted a 'signals_pending:' output (0 admissions).
    4. ≥ MIN_OPPS execution-eligible opportunities existed in the window.

    Purely read-only. Never modifies state.
    """
    try:
        from datetime import datetime, timezone, timedelta
        import json as _json
        from aletheia.storage.sqlite import get_db
        from aletheia.infra.events import emit_infra_event

        coverage = _ranker_session_coverage(_RANKER_STARVATION_WINDOW_HOURS)
        if coverage < _RANKER_ONLINE_COVERAGE_MIN:
            return

        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=_RANKER_STARVATION_WINDOW_HOURS)).isoformat()
        db = get_db()

        runs = db.execute(
            "SELECT outputs_created FROM agent_runs"
            " WHERE agent_name = 'paper_trader'"
            "   AND status IN ('completed', 'completed_with_errors')"
            "   AND started_at >= ?",
            (cutoff,),
        ).fetchall()
        if len(runs) < _RANKER_STARVATION_MIN_RUNS:
            return

        admitted = 0
        for row in runs:
            raw = row[0] if isinstance(row, (list, tuple)) else row["outputs_created"]
            try:
                outputs = _json.loads(raw) if raw else []
            except (ValueError, TypeError):
                outputs = []
            if any(str(o).startswith("signals_pending:") for o in outputs):
                admitted += 1

        if admitted > 0:
            return

        opp_count = db.execute(
            "SELECT COUNT(*) FROM opportunities"
            " WHERE execution_eligible = 1 AND created_at >= ?",
            (cutoff,),
        ).fetchone()
        eligible = (opp_count[0] if isinstance(opp_count, (list, tuple)) else opp_count[0]) if opp_count else 0
        if eligible < _RANKER_STARVATION_MIN_OPPS:
            return

        emit_infra_event(
            severity="warning",
            source="ranker",
            code="ranker_admission_starvation",
            message=(
                f"Ranker completed {len(runs)} run(s) in the past "
                f"{_RANKER_STARVATION_WINDOW_HOURS}h but admitted 0 candidates "
                f"from {eligible} eligible opportunities — pipeline may be "
                "stalled upstream of signal_pending"
            ),
            action="Check filter decisions, admission slot state, and opportunity valid_until",
            dedupe_sec=3600,
        )
    except Exception as exc:
        logger.debug("_check_ranker_admission_starvation failed: %s", exc)


def run_health_check() -> dict:
    """Evaluate health; alert on transition into unhealthy, then self-heal.

    Self-heal is deterministic (no LLM): it acts on the issue strings
    compute_health() already produces. Restart-fixable signals (code drift, stale
    feed) trigger a guarded, rate-limited exit(42) respawn; orphan PIDs get cleaned;
    everything else (LLM down, kill switch, bad strategy runs) is alert-only."""
    global _last_alerted_unhealthy, _self_heal_suppressed_alerted
    cfg = load_config()
    if not cfg.runtime.paused and cfg.runtime.scheduler_enabled:
        _check_ranker_admission_starvation()
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
        if not _exchange_reachable(cfg):
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
