"""Read-only paired policy value for authoritative Paper Risk decisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from statistics import fmean
from typing import Any


CONTRACT_VERSION = "risk-policy-value/v1"
BASELINE_POLICY = "pass-all-candidates/v1"
HORIZON = "24h"
ALPHA_THRESHOLD_PCT = 0.10


def empty_risk_policy_contribution(reason: str) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "baseline_policy": BASELINE_POLICY,
        "horizon": HORIZON,
        "statistical_unit": "proposal_id",
        "economic_unit": "net_return_pct",
        "behavior_authority": False,
        "promotion_eligible": False,
        "verdict": "insufficient_data",
        "contribution_verdict": "insufficient_data",
        "measurement_status": "not_measured",
        "causal_grade": "insufficient",
        "evidence_type": "prospective_paired_policy_value",
        "sample_size": 0,
        "raw_authoritative_count": 0,
        "eligible_count": 0,
        "excluded_count": 0,
        "approved_count": 0,
        "rejected_count": 0,
        "actual_intervention_count": 0,
        "baseline": None,
        "observed": None,
        "delta": None,
        "confidence_label": "insufficient",
        "policy_identity": None,
        "exclusion_counts": {},
        "reason": reason,
        "details": {},
    }


def build_risk_policy_contribution(
    db,
    *,
    cutoff: str,
    min_sample_size: int = 5,
    min_interventions: int = 5,
) -> dict[str, Any]:
    """Estimate current Risk policy value versus a shadow pass-all policy.

    A rejected proposal is the actual intervention. Its observed Risk-policy
    value is zero exposure; the paired pass-all control is the proposal's exact
    settled 24h cost-aware candidate return. Approved proposals have zero paired
    delta because both policies pass them.
    """

    cutoff_at = _parse_time(cutoff)
    if cutoff_at is None:
        return empty_risk_policy_contribution("invalid_audit_cutoff")
    rows = db.execute(
        "SELECT rd.id AS risk_decision_id, rd.proposal_id, rd.approved, "
        "rd.evaluated_quantity, rd.proposal_commit_hash, rd.proposal_execution_mode, "
        "rd.risk_mode, rd.policy_id, rd.policy_revision, rd.policy_hash, "
        "rd.runtime_session_id, rd.producer_commit, rd.behavior_fingerprint, "
        "rd.created_at AS risk_created_at, p.symbol AS proposal_symbol, "
        "p.market_type AS proposal_market_type, p.direction AS proposal_direction, "
        "p.quantity AS proposal_quantity, p.price AS proposal_price, "
        "p.execution_mode, p.source AS proposal_source, p.commit_hash, "
        "p.runtime_session_id AS proposal_runtime_session_id, "
        "p.producer_commit AS proposal_producer_commit, "
        "p.behavior_fingerprint AS proposal_behavior_fingerprint, "
        "fd.id AS filter_decision_id, fd.proposal_id AS filter_proposal_id, "
        "fd.venue AS filter_venue, fd.symbol AS filter_symbol, "
        "fd.market_type AS filter_market_type, fd.direction AS filter_direction, "
        "fd.decision AS filter_decision, fd.reason_code, fd.input_snapshot, "
        "fd.parameter_snapshot, fd.code_commit AS filter_code_commit, "
        "fd.outcome_candidate_key AS filter_outcome_key, fd.evaluated_at, "
        "fo.venue AS outcome_venue, fo.symbol AS outcome_symbol, "
        "fo.market_type AS outcome_market_type, fo.direction AS outcome_direction, "
        "fo.entry_price_usd, fo.entry_ts, fo.due_at, fo.settled_at, "
        "fo.exit_price_usd, fo.directional_return_pct, fo.estimated_fee_pct, "
        "fo.estimated_spread_slippage_pct, fo.estimated_funding_pct, "
        "fo.net_return_pct, fo.settlement_price_source, fo.cost_model_version, "
        "fo.status AS outcome_status "
        "FROM risk_decisions rd "
        "JOIN trade_proposals p ON p.id=rd.proposal_id "
        "LEFT JOIN filter_decisions fd ON fd.proposal_id=rd.proposal_id "
        "AND fd.filter_name='risk_engine' AND fd.stage='risk' "
        "AND json_extract(CASE WHEN json_valid(fd.input_snapshot) "
        "THEN fd.input_snapshot ELSE '{}' END, '$.risk_decision_id')=rd.id "
        "LEFT JOIN filter_candidate_outcomes fo "
        "ON fo.outcome_candidate_key=fd.outcome_candidate_key AND fo.horizon='24h' "
        "WHERE rd.decision_role='authoritative' "
        "AND datetime(rd.created_at)>=datetime(?) "
        "ORDER BY datetime(rd.created_at), rd.id, fd.id",
        (cutoff,),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["risk_decision_id"])].append(dict(row))

    exclusions: Counter[str] = Counter()
    eligible: list[dict[str, Any]] = []
    for candidates in grouped.values():
        if len(candidates) != 1:
            exclusions["ambiguous_risk_filter_evidence"] += 1
            continue
        row = candidates[0]
        reason = _exclusion_reason(row, cutoff_at)
        if reason is not None:
            exclusions[reason] += 1
            continue
        eligible.append(row)

    base = empty_risk_policy_contribution("insufficient_risk_policy_evidence")
    base.update({
        "raw_authoritative_count": len(grouped),
        "eligible_count": len(eligible),
        "excluded_count": sum(exclusions.values()),
        "exclusion_counts": dict(sorted(exclusions.items())),
    })
    if not eligible:
        return base

    identities = {
        (
            str(row["risk_mode"]),
            str(row["policy_id"]),
            int(row["policy_revision"]),
            str(row["policy_hash"]),
            str(row["producer_commit"]),
            str(row["behavior_fingerprint"]),
            str(row["cost_model_version"]),
        )
        for row in eligible
    }
    approved = sum(int(row["approved"]) for row in eligible)
    rejected = len(eligible) - approved
    base.update({
        "sample_size": len(eligible),
        "approved_count": approved,
        "rejected_count": rejected,
        "actual_intervention_count": rejected,
    })
    if len(identities) != 1:
        base["reason"] = "mixed_risk_policy_identity"
        base["details"] = {"identity_count": len(identities)}
        return base

    mode, policy_id, revision, policy_hash, commit, behavior, cost_model = identities.pop()
    base["policy_identity"] = {
        "risk_mode": mode,
        "policy_id": policy_id,
        "policy_revision": revision,
        "policy_hash": policy_hash,
        "producer_commit": commit,
        "behavior_fingerprint": behavior,
        "cost_model_version": cost_model,
    }
    if len(eligible) < max(1, int(min_sample_size)):
        base["reason"] = "insufficient_paired_risk_samples"
        return base
    if rejected < max(1, int(min_interventions)):
        base["reason"] = "insufficient_actual_risk_interventions"
        return base

    baseline_values = [float(row["net_return_pct"]) for row in eligible]
    observed_values = [
        float(row["net_return_pct"]) if row["approved"] == 1 else 0.0
        for row in eligible
    ]
    baseline = fmean(baseline_values)
    observed = fmean(observed_values)
    delta = observed - baseline
    verdict = (
        "positive"
        if delta > ALPHA_THRESHOLD_PCT
        else "negative"
        if delta < -ALPHA_THRESHOLD_PCT
        else "neutral"
    )
    base.update({
        "verdict": verdict,
        "contribution_verdict": verdict,
        "measurement_status": "measured_contribution",
        "causal_grade": "strong_prospective_paired_policy",
        "baseline": round(baseline, 4),
        "observed": round(observed, 4),
        "delta": round(delta, 4),
        "confidence_label": _confidence_label(len(eligible)),
        "reason": "measured_against_pass_all_candidate_control",
        "details": {
            "estimand": "mean_candidate_net_return_delta",
            "treatment_value": "approved_return_else_zero",
            "control_value": "all_candidate_net_return",
            "scope": "native_paper_internal_authoritative_risk_gate",
            "portfolio_interference_modeled": False,
        },
    })
    return base


def _exclusion_reason(row: dict[str, Any], cutoff_at: datetime) -> str | None:
    if row.get("approved") not in (0, 1):
        return "invalid_risk_decision_value"
    if row.get("proposal_source") != "native":
        return "non_native_proposal"
    if row.get("execution_mode") != "paper_internal" or row.get("proposal_execution_mode") != "paper_internal":
        return "non_local_paper_proposal"
    if row.get("risk_mode") != "demo":
        return "non_demo_risk_policy"
    if any(
        row.get(field) in {None, ""}
        for field in (
            "policy_id", "policy_revision", "policy_hash", "runtime_session_id",
            "producer_commit", "behavior_fingerprint", "proposal_commit_hash",
            "proposal_runtime_session_id", "proposal_producer_commit",
            "proposal_behavior_fingerprint",
        )
    ):
        return "incomplete_risk_policy_identity"
    if int(row["policy_revision"]) < 1:
        return "invalid_risk_policy_revision"
    if row.get("proposal_commit_hash") != row.get("commit_hash"):
        return "proposal_commit_mismatch"
    if any(
        row.get(risk_field) != row.get(proposal_field)
        for risk_field, proposal_field in (
            ("runtime_session_id", "proposal_runtime_session_id"),
            ("producer_commit", "proposal_producer_commit"),
            ("behavior_fingerprint", "proposal_behavior_fingerprint"),
        )
    ):
        return "proposal_risk_provenance_mismatch"
    quantity = _finite(row.get("evaluated_quantity"), positive=True)
    proposal_quantity = _finite(row.get("proposal_quantity"), positive=True)
    if quantity is None or proposal_quantity is None or not math.isclose(quantity, proposal_quantity):
        return "evaluated_quantity_mismatch"
    if row.get("filter_decision_id") is None:
        return "missing_risk_filter_evidence"
    expected_decision = "passed" if row["approved"] == 1 else "rejected"
    expected_reason = "risk_approved" if row["approved"] == 1 else "risk_rejected"
    if row.get("filter_decision") != expected_decision or row.get("reason_code") != expected_reason:
        return "risk_decision_trace_mismatch"
    parameters = _parse_json(row.get("parameter_snapshot"))
    if parameters != {"authority": "RiskDecision", "auto_tunable": False}:
        return "risk_filter_contract_mismatch"
    inputs = _parse_json(row.get("input_snapshot"))
    if inputs is None or inputs.get("execution_mode") != "paper_internal":
        return "risk_filter_input_mismatch"
    if row.get("filter_code_commit") != row.get("producer_commit"):
        return "risk_filter_commit_mismatch"
    proposal_id = str(row["proposal_id"])
    if row.get("filter_proposal_id") != proposal_id or row.get("filter_outcome_key") != f"proposal:{proposal_id}":
        return "risk_filter_candidate_identity_mismatch"
    if (
        str(row.get("filter_symbol") or "").lower()
        != str(row.get("proposal_symbol") or "").lower()
        or _canonical_market_type(row.get("filter_market_type"))
        != _canonical_market_type(row.get("proposal_market_type"))
        or str(row.get("filter_direction") or "").lower()
        != str(row.get("proposal_direction") or "").lower()
    ):
        return "risk_filter_instrument_mismatch"
    if row.get("outcome_status") != "settled" or row.get("settled_at") in {None, ""}:
        return "risk_outcome_unsettled"
    if (
        row.get("filter_venue") != row.get("outcome_venue")
        or str(row.get("outcome_symbol") or "").lower()
        != str(row.get("proposal_symbol") or "").lower()
        or _canonical_market_type(row.get("outcome_market_type"))
        != _canonical_market_type(row.get("proposal_market_type"))
        or str(row.get("outcome_direction") or "").lower()
        != str(row.get("proposal_direction") or "").lower()
        or not row.get("outcome_venue")
    ):
        return "risk_outcome_instrument_mismatch"
    entry_at = _parse_time(row.get("entry_ts"))
    due_at = _parse_time(row.get("due_at"))
    settled_at = _parse_time(row.get("settled_at"))
    if entry_at is None or entry_at < cutoff_at:
        return "risk_outcome_outside_window"
    if (
        due_at is None
        or settled_at is None
        or not math.isclose((due_at - entry_at).total_seconds(), 24 * 3600, abs_tol=1.0)
        or settled_at < due_at
    ):
        return "risk_outcome_horizon_mismatch"
    entry = _finite(row.get("entry_price_usd"), positive=True)
    proposal_price = _finite(row.get("proposal_price"), positive=True)
    if entry is None or proposal_price is None or not math.isclose(entry, proposal_price):
        return "risk_outcome_entry_mismatch"
    economics = [
        _finite(row.get(field))
        for field in (
            "directional_return_pct", "estimated_fee_pct",
            "estimated_spread_slippage_pct", "estimated_funding_pct",
            "net_return_pct",
        )
    ]
    if any(value is None for value in economics) or not row.get("cost_model_version"):
        return "incomplete_risk_outcome_economics"
    directional, fee, spread, funding, net = economics
    if fee < 0 or spread < 0:
        return "risk_outcome_economics_mismatch"
    exit_price = _finite(row.get("exit_price_usd"), positive=True)
    direction = str(row.get("outcome_direction") or "").lower()
    if exit_price is None:
        return "incomplete_risk_outcome_settlement"
    expected_directional = (exit_price - entry) / entry * 100.0
    if direction == "short":
        expected_directional = -expected_directional
    if not math.isclose(directional, expected_directional, abs_tol=1e-8):
        return "risk_outcome_economics_mismatch"
    if not math.isclose(net, directional - fee - spread - funding, abs_tol=1e-8):
        return "risk_outcome_economics_mismatch"
    if not row.get("settlement_price_source"):
        return "incomplete_risk_outcome_settlement"
    return None


def _parse_json(raw: Any) -> Any:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    return value


def _canonical_market_type(raw: Any) -> str:
    value = str(raw or "").lower()
    return "perp" if value in {"perp", "futures", "swap", "usdt_futures"} else value


def _parse_time(raw: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: Any, *, positive: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _confidence_label(sample_size: int) -> str:
    if sample_size >= 30:
        return "high"
    if sample_size >= 10:
        return "medium"
    return "low"
