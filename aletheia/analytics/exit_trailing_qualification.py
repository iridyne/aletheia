"""Read-only qualification report for the Paper dynamic-trailing holdout."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from statistics import mean
from typing import Any

from aletheia.portfolio.protective_barrier import (
    _TRAILING_QUALIFICATION_VERSION,
    _TRAILING_TREATMENT_BPS,
    _trailing_qualification_assignment,
)
from aletheia.storage import repositories as repo


def _parse_utc(value: object) -> datetime | None:
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
    net_pnl = [float(item["net_pnl"]) for item in available]
    net_r = [float(item["net_r"]) for item in available]
    reasons = Counter(
        str(item["unavailable_reason"])
        for item in items
        if item.get("unavailable_reason")
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
    expected = _trailing_qualification_assignment(str(row["id"]))
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
    return item, None


def build_exit_trailing_qualification_report(
    limit: int = 10_000,
    *,
    opened_after: str | None = None,
) -> dict[str, Any]:
    """Report exact-build treatment/control economics without granting authority."""
    if limit < 1 or limit > 10_000:
        raise ValueError("limit must be between 1 and 10000")
    cutoff = _parse_utc(opened_after) if opened_after is not None else None
    if opened_after is not None and cutoff is None:
        raise ValueError("opened_after must be an ISO-8601 timestamp with timezone")
    rows = repo.get_db().execute(
        "SELECT position.*, first_plan.plan_json AS qualification_plan_json, "
        "EXISTS(SELECT 1 FROM position_exit_plan_versions AS changed "
        "WHERE changed.position_id=position.id AND changed.version>1 "
        "AND changed.provenance='deterministic_trailing_v2') AS trailing_changed, "
        "EXISTS(SELECT 1 FROM position_exit_plan_versions AS intervened "
        "WHERE intervened.position_id=position.id AND intervened.version>1 "
        "AND intervened.provenance!='deterministic_trailing_v2') AS plan_intervened "
        "FROM positions AS position JOIN position_exit_plan_versions AS first_plan "
        "ON first_plan.position_id=position.id AND first_plan.version=1 "
        "WHERE position.execution_mode='paper_internal' "
        "ORDER BY datetime(position.opened_at), position.id LIMIT ?",
        (limit,),
    ).fetchall()

    cohorts: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    invalid_reasons: Counter[str] = Counter()
    for raw in rows:
        row = dict(raw)
        opened = _parse_utc(row.get("opened_at"))
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
    return {
        "contract_version": "exit-trailing-qualification/v1",
        "policy_version": _TRAILING_QUALIFICATION_VERSION,
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
        "recovery_truncation_count": None,
        "recovery_truncation_reason": "post_exit_recovery_contract_not_registered",
        "promotion_eligible": False,
    }
