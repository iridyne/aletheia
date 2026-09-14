"""Read-only reporting of the archived Paper Internal trailing holdout."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import mean
from typing import Any


from aletheia.storage import repositories as repo


_TRAILING_QUALIFICATION_VERSION = "paper-dynamic-profit-protection/v2"
_TRAILING_TREATMENT_ID = "atr1.5-balance1.5-completed1m/v1"
_TRAILING_TREATMENT_BPS = 9_000
_TRAILING_RECOVERY_WINDOW_BARS = 30
_TRAILING_RECOVERY_THRESHOLD_PCT = 1.0


def _expected_archived_assignment(position_id: str) -> dict:
    """Validate the original archived assignment identity without enrolling a Position."""
    digest = hashlib.sha256(
        f"{_TRAILING_QUALIFICATION_VERSION}:{position_id}".encode("utf-8")
    ).hexdigest()
    bucket = int(digest[:8], 16) % 10_000
    return {
        "policy_version": _TRAILING_QUALIFICATION_VERSION,
        "treatment_id": _TRAILING_TREATMENT_ID,
        "assignment_unit": position_id,
        "assignment_hash": digest,
        "arm": "treatment" if bucket < _TRAILING_TREATMENT_BPS else "control",
        "treatment_allocation_bps": _TRAILING_TREATMENT_BPS,
        "control_holdout_bps": 10_000 - _TRAILING_TREATMENT_BPS,
        "recovery_window_bars": _TRAILING_RECOVERY_WINDOW_BARS,
        "recovery_threshold_pct": _TRAILING_RECOVERY_THRESHOLD_PCT,
    }


_QUALIFICATION_SELECT = (
    "SELECT position.*, first_plan.plan_json AS qualification_plan_json, "
    "protection.venue AS protection_venue, "
    "protection.symbol AS protection_symbol, "
    "protection.market_type AS protection_market_type, "
    "protection.recovery_observation_json, "
    "protection.recovery_observation_hash, "
    "EXISTS(SELECT 1 FROM position_exit_plan_versions AS changed "
    "WHERE changed.position_id=position.id AND changed.version>1 "
    "AND changed.provenance='deterministic_trailing_v2') AS trailing_changed, "
    "EXISTS(SELECT 1 FROM position_exit_plan_versions AS intervened "
    "WHERE intervened.position_id=position.id AND intervened.version>1 "
    "AND intervened.provenance!='deterministic_trailing_v2') AS plan_intervened "
    "FROM positions AS position JOIN position_exit_plan_versions AS first_plan "
    "ON first_plan.position_id=position.id AND first_plan.version=1 "
    "LEFT JOIN position_protection_state AS protection "
    "ON protection.position_id=position.id "
)


def parse_utc_strict(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite(
    value: object,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not math.isfinite(number)
        or (positive and number <= 0)
        or (nonnegative and number < 0)
    ):
        return None
    return number


def _arm_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    available = [item for item in items if item.get("net_pnl") is not None]
    recovery_available = [
        item for item in items if item.get("recovery_status") == "available"
    ]
    net_pnl = [float(item["net_pnl"]) for item in available]
    net_r = [float(item["net_r"]) for item in available]
    reasons = Counter(
        str(item["unavailable_reason"])
        for item in items
        if item.get("unavailable_reason")
    )
    recovery_reasons = Counter(
        str(item["recovery_reason"])
        for item in items
        if item.get("recovery_status") == "unavailable"
        and item.get("recovery_reason")
    )
    recovery_truncation_count = sum(
        1 for item in recovery_available if item.get("recovery_truncated") is True
    )
    return {
        "assignment_count": len(items),
        "open_count": sum(1 for item in items if item["status"] == "open"),
        "closed_count": sum(1 for item in items if item["status"] == "closed"),
        "available_count": len(available),
        "unavailable_count": sum(reasons.values()),
        "unavailable_reasons": dict(sorted(reasons.items())),
        "changed_count": sum(1 for item in available if item["changed"]),
        "net_pnl_total": sum(net_pnl) if net_pnl else None,
        "net_pnl_mean": mean(net_pnl) if net_pnl else None,
        "net_r_mean": mean(net_r) if net_r else None,
        "loss_count": sum(1 for value in net_r if value < 0),
        "loss_rate": (
            sum(1 for value in net_r if value < 0) / len(net_r)
            if net_r else None
        ),
        "worst_net_r": min(net_r) if net_r else None,
        "recovery_available_count": len(recovery_available),
        "recovery_pending_count": sum(
            1 for item in items if item.get("recovery_status") == "pending"
        ),
        "recovery_unavailable_count": sum(recovery_reasons.values()),
        "recovery_unavailable_reasons": dict(sorted(recovery_reasons.items())),
        "recovery_truncation_count": recovery_truncation_count,
        "recovery_truncation_rate": (
            recovery_truncation_count / len(recovery_available)
            if recovery_available else None
        ),
    }


def _stored_recovery_observation(
    row: dict[str, Any],
    assignment: dict[str, Any],
) -> dict[str, Any] | None:
    raw = row.get("recovery_observation_json")
    digest = row.get("recovery_observation_hash")
    if raw is None and digest is None:
        return None
    invalid = {
        "status": "unavailable",
        "reason": "stored_recovery_observation_invalid",
        "mfe_pct": None,
    }
    if not isinstance(raw, str) or not isinstance(digest, str):
        return invalid
    try:
        observation = json.loads(raw)
    except json.JSONDecodeError:
        return invalid
    if not isinstance(observation, dict):
        return invalid
    try:
        canonical = json.dumps(
            observation,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return invalid
    exit_price = _finite(row.get("current_price"), positive=True)
    observed_exit = _finite(observation.get("exit_price"), positive=True)
    identity_matches = (
        hashlib.sha256(canonical.encode("utf-8")).hexdigest() == digest
        and observation.get("contract_version") == "post-exit-recovery/v1"
        and observation.get("policy_version") == _TRAILING_QUALIFICATION_VERSION
        and observation.get("position_id") == row.get("id")
        and observation.get("venue") == row.get("protection_venue")
        and observation.get("symbol") == row.get("protection_symbol")
        and observation.get("market_type") == row.get("protection_market_type")
        and observation.get("timeframe") == "1m"
        and observation.get("direction") == row.get("direction")
        and observation.get("closed_at") == row.get("closed_at")
        and observation.get("bars_requested") == assignment.get("recovery_window_bars")
        and observation.get("recovery_threshold_pct")
        == assignment.get("recovery_threshold_pct")
        and exit_price is not None
        and observed_exit is not None
        and math.isclose(exit_price, observed_exit, rel_tol=1e-12, abs_tol=1e-12)
    )
    return observation if identity_matches else invalid


def _recovery_observation(
    row: dict[str, Any],
    assignment: dict[str, Any],
) -> dict[str, Any]:
    return _stored_recovery_observation(row, assignment) or {
        "status": "unavailable",
        "reason": "archived_recovery_not_recorded",
        "mfe_pct": None,
    }


def _qualified_item(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        plan = json.loads(row["qualification_plan_json"])
        trailing = plan["trailing"]
        assignment = trailing["qualification"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None, None
    if not all(isinstance(value, dict) for value in (plan, trailing, assignment)):
        return None, "assignment_contract_invalid"
    if assignment.get("policy_version") != _TRAILING_QUALIFICATION_VERSION:
        return None, None
    expected = _expected_archived_assignment(str(row["id"]))
    if assignment != expected:
        return None, "assignment_identity_invalid"
    arm = str(assignment["arm"])
    exit_fee_rate = _finite(trailing.get("exit_fee_rate"), nonnegative=True)
    slippage_bps = _finite(trailing.get("slippage_bps"), nonnegative=True)
    if (
        trailing.get("cost_model_version") != "paper-net-balance-v1"
        or exit_fee_rate is None
        or exit_fee_rate >= 1
        or slippage_bps is None
        or slippage_bps >= 10_000
        or (arm == "treatment" and (trailing.get("mode") != "atr" or trailing.get("enabled") is not True))
        or (arm == "control" and (trailing.get("mode") != "off" or trailing.get("enabled") is not False))
    ):
        return None, "assignment_contract_invalid"

    item: dict[str, Any] = {
        "position_id": row["id"],
        "arm": arm,
        "status": row["status"],
        "changed": False,
        "net_pnl": None,
        "net_r": None,
        "unavailable_reason": None,
        "recovery_status": "not_applicable",
        "recovery_reason": "treatment_stop_unchanged",
        "recovery_mfe_pct": None,
        "recovery_truncated": None,
        "_recovery_observation": None,
    }
    if row["status"] != "closed":
        return item, None
    if bool(row["plan_intervened"]):
        item["unavailable_reason"] = "exit_plan_intervention"
        return item, None
    if arm == "control" and bool(row["trailing_changed"]):
        item["unavailable_reason"] = "control_behavior_changed"
        return item, None
    if not bool(row["outcome_eligible"]):
        item["unavailable_reason"] = str(
            row.get("outcome_exclusion_reason") or "outcome_economics_unavailable"
        )
        return item, None
    if (
        row.get("producer_commit") is None
        or row.get("closed_producer_commit") != row.get("producer_commit")
        or row.get("behavior_fingerprint") is None
        or row.get("closed_behavior_fingerprint") != row.get("behavior_fingerprint")
    ):
        item["unavailable_reason"] = "outcome_identity_unavailable"
        return item, None
    net_pnl = _finite(row.get("realized_pnl"))
    entry = _finite(row.get("entry_price"), positive=True)
    quantity = _finite(row.get("quantity"), positive=True)
    stop = _finite(plan.get("stop_price"), positive=True)
    if net_pnl is None or entry is None or quantity is None or stop is None:
        item["unavailable_reason"] = "net_economics_or_initial_risk_unavailable"
        return item, None
    risk = abs(entry - stop) * quantity
    if not math.isfinite(risk) or risk <= 0:
        item["unavailable_reason"] = "initial_risk_unavailable"
        return item, None
    item.update({
        "changed": arm == "treatment" and bool(row["trailing_changed"]),
        "net_pnl": net_pnl,
        "net_r": net_pnl / risk,
    })
    if item["changed"]:
        recovery = _recovery_observation(row, assignment)
        item["_recovery_observation"] = recovery
        recovery_mfe = _finite(recovery.get("mfe_pct"), nonnegative=True)
        recovery_status = str(recovery.get("status") or "unavailable")
        item.update({
            "recovery_status": recovery_status,
            "recovery_reason": recovery.get("reason"),
            "recovery_mfe_pct": recovery_mfe,
            "recovery_truncated": (
                recovery_mfe >= float(assignment["recovery_threshold_pct"])
                if recovery_status == "available" and recovery_mfe is not None
                else None
            ),
        })
    return item, None


def build_exit_trailing_qualification_report(
    limit: int = 10_000,
    *,
    opened_after: str | None = None,
) -> dict[str, Any]:
    """Report exact-build treatment/control economics without granting authority."""

    if limit < 1 or limit > 10_000:
        raise ValueError("limit must be between 1 and 10000")
    cutoff = parse_utc_strict(opened_after) if opened_after is not None else None
    if opened_after is not None and cutoff is None:
        raise ValueError("opened_after must be an ISO-8601 timestamp with timezone")
    rows = repo.get_db().execute(
        _QUALIFICATION_SELECT
        +
        "WHERE position.execution_mode='paper_internal' "
        "ORDER BY datetime(position.opened_at), position.id LIMIT ?",
        (limit,),
    ).fetchall()

    cohorts: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    invalid_reasons: Counter[str] = Counter()
    for raw in rows:
        row = dict(raw)
        opened = parse_utc_strict(row.get("opened_at"))
        if cutoff is not None and (opened is None or opened < cutoff):
            continue
        item, invalid = _qualified_item(row)
        if invalid:
            invalid_reasons[invalid] += 1
        if item is None:
            continue
        key = (row.get("producer_commit"), row.get("behavior_fingerprint"))
        cohorts.setdefault(key, []).append(item)

    cohort_reports = []
    for (code_commit, behavior_fingerprint), items in sorted(
        cohorts.items(), key=lambda entry: str(entry[0])
    ):
        treatment = _arm_summary([item for item in items if item["arm"] == "treatment"])
        control = _arm_summary([item for item in items if item["arm"] == "control"])
        pnl_delta = (
            treatment["net_pnl_mean"] - control["net_pnl_mean"]
            if treatment["net_pnl_mean"] is not None and control["net_pnl_mean"] is not None
            else None
        )
        r_delta = (
            treatment["net_r_mean"] - control["net_r_mean"]
            if treatment["net_r_mean"] is not None and control["net_r_mean"] is not None
            else None
        )
        tail_delta = (
            treatment["worst_net_r"] - control["worst_net_r"]
            if treatment["worst_net_r"] is not None and control["worst_net_r"] is not None
            else None
        )
        cohort_reports.append({
            "code_commit": code_commit,
            "behavior_fingerprint": behavior_fingerprint,
            "treatment": treatment,
            "control": control,
            "incremental_net_pnl_mean": pnl_delta,
            "incremental_net_r_mean": r_delta,
            "adverse_tail_net_r_delta": tail_delta,
            "promotion_eligible": False,
        })

    all_items = [item for items in cohorts.values() for item in items]
    unavailable_count = sum(
        1 for item in all_items if item.get("unavailable_reason")
    )
    recovery_available_count = sum(
        1 for item in all_items if item.get("recovery_status") == "available"
    )
    recovery_truncation_count = sum(
        1 for item in all_items if item.get("recovery_truncated") is True
    )
    return {
        "contract_version": "exit-trailing-qualification/v2",
        "policy_version": _TRAILING_QUALIFICATION_VERSION,
        "enrollment_enabled": False,
        "enrollment_block_reason": "archived",
        "assignment_unit": "position_id",
        "treatment_allocation_bps": _TRAILING_TREATMENT_BPS,
        "control_holdout_bps": 10_000 - _TRAILING_TREATMENT_BPS,
        "assignment_count": len(all_items),
        "treatment_assignment_count": sum(
            1 for item in all_items if item["arm"] == "treatment"
        ),
        "control_assignment_count": sum(
            1 for item in all_items if item["arm"] == "control"
        ),
        "closed_count": sum(1 for item in all_items if item["status"] == "closed"),
        "available_count": sum(1 for item in all_items if item.get("net_pnl") is not None),
        "unavailable_count": unavailable_count,
        "changed_count": sum(1 for item in all_items if item["changed"]),
        "invalid_assignment_count": sum(invalid_reasons.values()),
        "invalid_assignment_reasons": dict(sorted(invalid_reasons.items())),
        "cohort_count": len(cohort_reports),
        "cohorts": cohort_reports,
        "cross_cohort_pooling": False,
        "recovery_contract": {
            "contract_version": "post-exit-recovery/v1",
            "observation": "complete_post_exit_1m_bars",
            "window_bars": _TRAILING_RECOVERY_WINDOW_BARS,
            "threshold_pct": _TRAILING_RECOVERY_THRESHOLD_PCT,
            "authority": "diagnostic_only",
        },
        "recovery_available_count": recovery_available_count,
        "recovery_pending_count": sum(
            1 for item in all_items if item.get("recovery_status") == "pending"
        ),
        "recovery_unavailable_count": sum(
            1 for item in all_items if item.get("recovery_status") == "unavailable"
        ),
        "recovery_truncation_count": recovery_truncation_count,
        "recovery_truncation_rate": None,
        "recovery_truncation_reason": (
            "cohort_scoped_only"
            if recovery_available_count
            else "no_mature_treatment_changing_close"
        ),
        "promotion_eligible": False,
    }
