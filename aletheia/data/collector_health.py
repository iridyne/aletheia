"""Bounded, durable health state for scheduled market-data collector domains."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from aletheia.infra.events import emit_infra_event
from aletheia.storage import repositories as repo


COLLECTOR_CADENCE_SEC: dict[str, int] = {
    "microstructure": 120,
    "derivatives": 120,
    "volume_scanner": 600,
    "coingecko_top": 600,
    "macro": 600,
    "coinalyze": 600,
    "hyperliquid": 600,
    "defillama": 600,
}

_PROBLEM_STATUSES = {"failed", "timed_out"}
_COMPLETED_STATUSES = {"success", "partial", "empty"}
_ATTEMPTED_STATUSES = _COMPLETED_STATUSES | {"failed", "timed_out", "not_configured"}
_VALID_STATUSES = _ATTEMPTED_STATUSES | {"skipped_budget", "disabled"}
_ALERT_THRESHOLD = 3
_RECOVERY_GRACE_SEC = max(COLLECTOR_CADENCE_SEC.values()) * 2


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

    ignored = {"errors", "observed_at", "movers_summary"}
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
) -> dict:
    """Update one domain row and alert only after repeated operational problems."""
    if domain not in COLLECTOR_CADENCE_SEC:
        raise ValueError(f"unknown collector domain: {domain}")
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid collector status: {status}")

    now = occurred_at or _now_iso()
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
    }
    repo.upsert_collector_health_state(state)

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


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


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

    problem_rows = [row for row in domains if row["last_status"] in _PROBLEM_STATUSES]
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
    completed = 0

    for domain, cadence_sec in COLLECTOR_CADENCE_SEC.items():
        row = dict(stored.get(domain) or {})
        last_scheduled = _parse_time(row.get("last_scheduled_at"))
        age_sec = max(0, int((now - last_scheduled).total_seconds())) if last_scheduled else None
        status = str(row.get("last_status") or "never_run")
        overdue = bool(
            age_sec is not None
            and status not in {"disabled", "not_configured"}
            and age_sec > cadence_sec * 3
        )
        consecutive = int(row.get("consecutive_problems") or 0)
        is_degraded = overdue or consecutive >= _ALERT_THRESHOLD
        if is_degraded:
            degraded += 1
        elif status in _PROBLEM_STATUSES:
            transient += 1
        if status in _COMPLETED_STATUSES:
            completed += 1
        # Error codes are already constrained to a safe identifier. When a
        # collector returns a failed result without raising, say so explicitly
        # rather than inventing an exception message.
        error_code = row.get("last_error_code")
        error_summary = (
            str(error_code) if error_code else "collector_returned_failed_result"
        ) if status in _PROBLEM_STATUSES else None
        row.update({
            "domain": domain,
            "cadence_sec": cadence_sec,
            "last_status": status,
            "display_status": status,
            "attributed_cause": None,
            "last_error_summary": error_summary,
            "display_severity": (
                "warning"
                if is_degraded or status in _PROBLEM_STATUSES
                else "info" if status == "skipped_budget"
                else "muted" if status in {"disabled", "not_configured", "never_run"}
                else "ok"
            ),
            "age_sec": age_sec,
            "overdue": overdue,
            "degraded": is_degraded,
        })
        domains.append(row)

    recorded = len(stored)
    overall = (
        "degraded" if degraded
        else "attention" if transient
        else "healthy" if recorded
        else "initializing"
    )
    if connectivity_status is None:
        try:
            from aletheia.agent_runtime.connectivity import get_status

            connectivity_status = get_status().get("external_connectivity") or {}
        except Exception:
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
