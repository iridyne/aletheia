"""Bounded, durable health state for scheduled market-data collector domains."""

from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.data.collector_health")

from datetime import datetime, timezone
import re
import json
from typing import Any

from aletheia.data.freshness import MAX_FUTURE_SKEW
from aletheia.infra.events import emit_infra_event
from aletheia.storage import repositories as repo


COLLECTOR_CADENCE_SEC: dict[str, int] = {
    "microstructure": 120,
    "derivatives": 120,
    "derivatives_history": 600,
    "derivatives_comparison": 600,
    "volume_scanner": 600,
    "coingecko_top": 600,
    "macro": 600,
    "coinalyze": 600,
    "hyperliquid": 600,
    "defillama": 600,
    "options_structure": 600,
}

_PROBLEM_STATUSES = {"failed", "timed_out"}
_ATTENTION_STATUSES = _PROBLEM_STATUSES | {"partial", "empty"}
_COMPLETED_STATUSES = {"success", "partial", "empty"}
_ATTEMPTED_STATUSES = _COMPLETED_STATUSES | {"failed", "timed_out", "not_configured"}
_VALID_STATUSES = _ATTEMPTED_STATUSES | {"skipped_budget", "disabled"}
_ALERT_THRESHOLD = 3
_RECOVERY_GRACE_SEC = max(COLLECTOR_CADENCE_SEC.values()) * 2
_TIMESTAMP_ERRORS = {
    "collector_health_timestamp_missing",
    "collector_health_timestamp_invalid",
    "collector_health_timestamp_future",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error_code(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value))
    return cleaned[:120] or None


def classify_collector_result(result: Any) -> str:
    """Classify returned data without treating an honest empty result as failure."""
    if not isinstance(result, dict):
        return "success" if result is not None else "empty"

    errors = list(result.get("errors") or [])
    if errors == ["no_key"]:
        return "not_configured"

    ignored = {"errors", "observed_at", "movers_summary", "metric_outcomes", "coverage"}
    has_payload = any(
        bool(value) for key, value in result.items() if key not in ignored
    )
    if errors:
        return "partial" if has_payload else "failed"
    return "success" if has_payload else "empty"


def record_collector_status(
    domain: str,
    status: str,
    *,
    tick: int | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    occurred_at: str | None = None,
    derivatives_coverage: dict | None = None,
) -> dict:
    """Update one domain row and alert only after repeated operational problems."""
    if domain not in COLLECTOR_CADENCE_SEC:
        raise ValueError(f"unknown collector domain: {domain}")
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid collector status: {status}")

    now = _now_iso() if occurred_at is None else occurred_at
    _, timestamp_error = _collector_timestamp_age(now, now=datetime.now(timezone.utc))
    if timestamp_error:
        raise ValueError(timestamp_error)
    previous = repo.get_collector_health_state(domain) or {}
    problem_count = (
        int(previous.get("consecutive_problems") or 0) + 1
        if status in _PROBLEM_STATUSES
        else 0
    )
    attempted = status in _ATTEMPTED_STATUSES
    completed = status in _COMPLETED_STATUSES
    state = {
        "domain": domain,
        "cadence_sec": COLLECTOR_CADENCE_SEC[domain],
        "last_status": status,
        "last_tick": tick,
        "last_scheduled_at": now,
        "last_attempt_at": now if attempted else previous.get("last_attempt_at"),
        "last_finished_at": now if attempted else previous.get("last_finished_at"),
        "last_success_at": now if completed else previous.get("last_success_at"),
        "duration_ms": max(0, int(duration_ms)) if duration_ms is not None else None,
        "consecutive_problems": problem_count,
        "attempts": int(previous.get("attempts") or 0) + int(attempted),
        "successes": int(previous.get("successes") or 0) + int(status == "success"),
        "partial_results": int(previous.get("partial_results") or 0) + int(status == "partial"),
        "empty_results": int(previous.get("empty_results") or 0) + int(status == "empty"),
        "timeouts": int(previous.get("timeouts") or 0) + int(status == "timed_out"),
        "budget_skips": int(previous.get("budget_skips") or 0) + int(status == "skipped_budget"),
        "failures": int(previous.get("failures") or 0) + int(status == "failed"),
        "last_error_code": _safe_error_code(error_code) if status in _PROBLEM_STATUSES else None,
        "derivatives_coverage_json": json.dumps(derivatives_coverage, sort_keys=True, allow_nan=False)
        if derivatives_coverage is not None else previous.get('derivatives_coverage_json'),
    }
    repo.upsert_collector_health_state(state)

    # #3089: `last_scheduled_at` advances on every turn, so a domain that is only
    # ever `skipped_budget` looks freshly scheduled forever and never goes overdue.
    # Warn once per starvation window when a skip lands on a domain that is starved
    # under the shared 3-cadence health convention. This is scheduler starvation,
    # not a source failure, so it must not increment `consecutive_problems` or be
    # reported as a collector problem.
    if status == "skipped_budget":
        cadence_sec = COLLECTOR_CADENCE_SEC[domain]
        last_attempt = _parse_time(previous.get("last_attempt_at"))
        now_dt = _parse_time(now)
        attempt_age_sec = (
            (now_dt - last_attempt).total_seconds()
            if last_attempt is not None and now_dt is not None
            else None
        )
        # `_bounded` records `skipped_budget` when the lane budget is already spent,
        # so a domain can be skipped on every tick without ever attempting. That
        # case has no attempt timestamp to age, so it is bounded by the same
        # convention in the only unit available: three cadences' worth of skips.
        starving = bool(
            (attempt_age_sec is not None and attempt_age_sec > cadence_sec * 3)
            or (int(state["attempts"]) == 0 and int(state["budget_skips"]) >= 3)
        )
        if starving:
            reason = (
                f"no real attempt for {int(attempt_age_sec)}s"
                if attempt_age_sec is not None
                else f"no real attempt recorded; {int(state['budget_skips'])} turns skipped"
            )
            emit_infra_event(
                severity="warning",
                source="data_collectors",
                code="collector_budget_starvation",
                entity_id=domain,
                message=(
                    f"{domain} has had {reason} because every turn was "
                    f"skipped_budget (cadence {cadence_sec}s)."
                ),
                action=(
                    "Review the shared collector budget and scheduler load. "
                    "The source is not failing; it is never being attempted."
                ),
                dedupe_sec=cadence_sec * 3,
            )

    if problem_count >= _ALERT_THRESHOLD:
        connectivity_state = "unknown"
        try:
            from aletheia.agent_runtime.connectivity import get_status

            connectivity_state = str(
                (get_status().get("external_connectivity") or {}).get("state")
                or "unknown"
            )
        except Exception:
            pass
        dependent = connectivity_state in {"unavailable", "recovering"}
        emit_infra_event(
            severity="info" if dependent else "warning",
            source="data_collectors",
            code=(
                "collector_problem_dependent_connectivity"
                if dependent
                else "collector_consecutive_problem"
            ),
            entity_id=domain,
            message=(
                f"{domain} is affected by the external connectivity incident; "
                f"latest={status}"
                if dependent
                else f"{domain} had {problem_count} consecutive collector problems; latest={status}"
            ),
            action=(
                "Resolve the connectivity incident first; this collector retries automatically."
                if dependent
                else "Inspect collector health and gateway errors; do not increase the shared budget blindly."
            ),
            metadata={
                "status": status,
                "consecutive_problems": problem_count,
                "dependent_on": "external_connectivity" if dependent else None,
            },
            dedupe_sec=1800,
        )
    return state


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _collector_timestamp_age(
    value: object,
    *,
    now: datetime,
) -> tuple[int | None, str | None]:
    """Return bounded age or the stable reason invalidating collector evidence."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "collector_health_timestamp_missing"
    observed_at = _parse_time(value)
    if observed_at is None:
        return None, "collector_health_timestamp_invalid"
    current = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    age_sec = (current - observed_at.astimezone(timezone.utc)).total_seconds()
    if age_sec < -MAX_FUTURE_SKEW.total_seconds():
        return None, "collector_health_timestamp_future"
    return max(0, int(age_sec)), None


def _epoch_iso(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _as_epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    parsed = _parse_time(str(value)) if value else None
    return parsed.timestamp() if parsed else None


def _infrastructure_incident(
    domains: list[dict[str, Any]],
    connectivity_status: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any] | None:
    connectivity_state = str(connectivity_status.get("state") or "unknown")
    if connectivity_state not in {"unavailable", "recovering"}:
        return None
    observed_at = connectivity_status.get("observed_at")
    observed_epoch = _as_epoch(observed_at)
    stale_after_sec = max(1, int(connectivity_status.get("stale_after_sec") or 180))

    outage_started_at = connectivity_status.get("outage_started_at")
    try:
        outage_epoch = float(outage_started_at)
    except (TypeError, ValueError):
        outage_epoch = None

    problem_rows = [
        row for row in domains
        if row["last_status"] in _PROBLEM_STATUSES
        and row.get("last_error_summary") not in _TIMESTAMP_ERRORS
    ]
    affected: list[dict[str, Any]]
    code: str
    status: str
    severity: str
    message: str
    action: str
    detected_at: str | None
    if connectivity_state == "unavailable":
        affected = [
            row
            for row in problem_rows
            if outage_epoch is None
            or (
                (attempted := _parse_time(row.get("last_attempt_at"))) is not None
                and attempted.timestamp() >= outage_epoch - stale_after_sec
            )
        ]
        code = "external_connectivity_unavailable"
        status = "active"
        severity = "warning"
        message = (
            "External network or the configured proxy is unavailable. "
            "Collector failures are grouped under this infrastructure incident."
        )
        action = "Check the network and proxy process; collectors retry automatically."
        detected_at = _epoch_iso(connectivity_status.get("outage_started_at") or observed_epoch)
        display_status = "blocked_by_connectivity"
    else:
        recovery_at = connectivity_status.get("last_recovery_at")
        try:
            recovery_epoch = float(recovery_at)
        except (TypeError, ValueError):
            return None
        if now.timestamp() - recovery_epoch > _RECOVERY_GRACE_SEC:
            return None
        affected = [
            row
            for row in problem_rows
            if (
                (attempted := _parse_time(row.get("last_attempt_at"))) is not None
                and (outage_epoch is None or attempted.timestamp() >= outage_epoch - stale_after_sec)
                and attempted.timestamp() <= recovery_epoch
            )
        ]
        if not affected:
            return None
        code = "external_connectivity_recovering"
        status = "recovering"
        severity = "info"
        message = (
            "External connectivity is restored. Affected collectors are waiting "
            "for their next scheduled retry."
        )
        action = "No manual restart is required; collector status will clear after a successful run."
        detected_at = _epoch_iso(recovery_epoch)
        display_status = "waiting_for_retry"

    affected_domains = [row["domain"] for row in affected]
    for row in affected:
        row["display_status"] = display_status
        row["attributed_cause"] = code
        row["display_severity"] = "info"
    return {
        "code": code,
        "status": status,
        "severity": severity,
        "priority": int(connectivity_status.get("priority") or (1 if status == "active" else 2)),
        "message": message,
        "action": action,
        "detected_at": detected_at,
        "observed_at": (
            str(observed_at)
            if isinstance(observed_at, str)
            else _epoch_iso(observed_epoch)
        ),
        "affected_domains": affected_domains,
    }


def get_collector_health_report(
    *,
    now: datetime | None = None,
    connectivity_status: dict[str, Any] | None = None,
) -> dict:
    """Build an operator-facing summary from bounded durable state."""
    now = now or datetime.now(timezone.utc)
    stored = {row["domain"]: row for row in repo.list_collector_health_states()}
    domains: list[dict[str, Any]] = []
    degraded = 0
    transient = 0
    attention = 0
    never_run = 0
    completed = 0

    for domain, cadence_sec in COLLECTOR_CADENCE_SEC.items():
        stored_row = stored.get(domain)
        row = dict(stored_row or {})
        age_sec, timestamp_error = (
            _collector_timestamp_age(row.get("last_scheduled_at"), now=now)
            if stored_row is not None
            else (None, None)
        )
        status = str(row.get("last_status") or "never_run")
        never_run += int(status == "never_run")
        overdue = bool(
            age_sec is not None
            and status not in {"disabled", "not_configured"}
            and age_sec > cadence_sec * 3
        )
        # #3089: `last_scheduled_at` advances even when a turn is `skipped_budget`,
        # so overdue alone can report a domain that is never allowed to attempt as
        # healthy forever. Liveness must come from real attempts. A domain that has
        # not attempted for more than the same 3-cadence window is starved by the
        # scheduler, not failing at the source: the raw status and counters stay
        # untouched and only this derived projection changes.
        attempt_age_sec, _ = (
            _collector_timestamp_age(row.get("last_attempt_at"), now=now)
            if stored_row is not None and row.get("last_attempt_at")
            else (None, None)
        )
        starved = bool(
            status not in {"disabled", "not_configured", "never_run"}
            and (
                (attempt_age_sec is not None and attempt_age_sec > cadence_sec * 3)
                # Never attempted at all: there is no attempt timestamp to age, so
                # bound it by the same convention in the only unit available. A
                # domain skipped before its turn every tick reaches this.
                or (
                    row.get("last_attempt_at") is None
                    and int(row.get("attempts") or 0) == 0
                    and int(row.get("budget_skips") or 0) >= 3
                )
            )
        )
        consecutive = int(row.get("consecutive_problems") or 0)
        is_degraded = (
            timestamp_error is not None
            or overdue
            or starved
            or consecutive >= _ALERT_THRESHOLD
        )
        if is_degraded:
            degraded += 1
        elif status in _PROBLEM_STATUSES:
            transient += 1
        elif status in _ATTENTION_STATUSES:
            attention += 1
        if status in _COMPLETED_STATUSES:
            completed += 1
        # Error codes are already constrained to a safe identifier. When a
        # collector returns a failed result without raising, say so explicitly
        # rather than inventing an exception message.
        error_code = row.get("last_error_code")
        error_summary = timestamp_error or ((
            str(error_code) if error_code else "collector_returned_failed_result"
        ) if status in _PROBLEM_STATUSES else None)
        if starved and error_summary is None:
            # Starvation is not a source failure: name the scheduler, not the
            # collector, so the operator does not go looking for a broken feed.
            error_summary = (
                "collector_budget_starvation: no real attempt for "
                f"{int(attempt_age_sec)}s (cadence {cadence_sec}s)"
                if attempt_age_sec is not None
                else (
                    "collector_budget_starvation: no real attempt recorded; "
                    f"{int(row.get('budget_skips') or 0)} turns skipped "
                    f"(cadence {cadence_sec}s)"
                )
            )
        row.update({
            "domain": domain,
            "cadence_sec": cadence_sec,
            "last_status": status,
            "display_status": "degraded" if (timestamp_error or starved) else status,
            "attributed_cause": None,
            "last_error_summary": error_summary,
            "display_severity": (
                "warning"
                if is_degraded or status in _PROBLEM_STATUSES or status == "partial"
                else "info" if status in {"skipped_budget", "empty"}
                else "muted" if status in {"disabled", "not_configured", "never_run"}
                else "ok"
            ),
            "age_sec": age_sec,
            "attempt_age_sec": attempt_age_sec,
            "overdue": overdue,
            "starved": starved,
            "degraded": is_degraded,
        })
        domains.append(row)

    recorded = len(stored)
    overall = (
        "degraded" if degraded
        else "attention" if transient or attention
        else "initializing" if never_run
        else "healthy"
    )
    if connectivity_status is None:
        try:
            from aletheia.agent_runtime.connectivity import get_status

            connectivity_status = get_status().get("external_connectivity") or {}
        except Exception as exc:
            logger.warning("collector_health: connectivity status unavailable (%s)", type(exc).__name__)
            connectivity_status = {}
    infrastructure_incident = _infrastructure_incident(
        domains,
        connectivity_status,
        now=now,
    )
    return {
        "status": overall,
        "domain_count": len(COLLECTOR_CADENCE_SEC),
        "recorded_count": recorded,
        "completed_count": completed,
        "degraded_count": degraded,
        "transient_problem_count": transient,
        "infrastructure_incident": infrastructure_incident,
        "domains": domains,
    }
