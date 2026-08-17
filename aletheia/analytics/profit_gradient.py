"""Seven-layer profit attribution with explicit comparison-method gates.

This module intentionally accepts structured cohort samples only.  It does not
import or consume Position Lifecycle Story text; narrative rendering remains a
human-review surface, not a Learning/statistical input.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

from aletheia.analytics.profit_gradient_estimators import (
    estimate_deterministic_decomposition,
    estimate_paired_same_unit,
    estimate_prospective_randomized,
)


PROFIT_GRADIENT_CONTRACT_VERSION = "profit_gradient/v3"
RECONCILIATION_CONTRACT_VERSION = "profit_gradient/reconciliation/v1"
RECONCILIATION_TOLERANCE_QUOTE = 1e-8
LAYERS = ("Selection", "Decision", "Risk", "Execution", "Holding", "Exit", "Costs")
COMPARISON_METHODS = (
    "prospective_randomized",
    "paired_same_unit",
    "deterministic_decomposition",
    "directional_observational",
)
_CANDIDATE_LAYERS = frozenset({"Selection", "Decision", "Risk"})
_POSITION_LAYERS = frozenset(LAYERS) - _CANDIDATE_LAYERS
_LAYER_COMPONENTS = {
    "Selection": "candidate_selection",
    "Decision": "operator_decision",
    "Risk": "risk_policy",
    "Execution": "execution_fill",
    "Holding": "holding_authorization",
    "Exit": "exit_geometry",
    "Costs": "avoidable_extra_cost",
}
_COST_BASELINE_KINDS = frozenset(
    {"realizable_limit", "spot_vs_perpetual", "turnover", "venue"}
)
_COMPARISON_PROFILES = {
    "prospective_randomized": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": 20,
        "requires_two_arms": True,
        "allows_unique_winner": True,
        "evidence_role": "prospective_confirmatory",
        "estimand": "treatment_minus_control_cluster_mean_bp",
    },
    "paired_same_unit": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": True,
        "evidence_role": "paired_confirmatory",
        "estimand": "paired_same_unit_mean_bp",
    },
    "deterministic_decomposition": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": False,
        "evidence_role": "measured_only",
        "estimand": "deterministic_decomposition_bp",
    },
    "directional_observational": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": False,
        "evidence_role": "directional_only",
        "estimand": "directional_observational_bp",
    },
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_integer(value: Any) -> int | None:
    number = _finite(value)
    if number is None or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _timestamp(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _expected_units(layer: str) -> tuple[str, str]:
    if layer in _CANDIDATE_LAYERS:
        return "candidate_fact", "fixed_horizon_net_return"
    return "position_fact", "verified_settled_net_economics"


_RANDOMIZED_COHORT_FIELDS = (
    ("assignment_salts", "assignment_salt"),
    ("horizons", "horizon"),
    ("outcome_contract_versions", "outcome_contract_version"),
    ("metric_versions", "metric_version"),
    ("statistical_unit_versions", "statistical_unit_version"),
    ("stages", "stage"),
)


def _randomized_cohort_identity(
    context: Mapping[str, Any], method: str | None
) -> tuple[dict[str, str] | None, list[str]]:
    if method == "prospective_randomized":
        required_fields = _RANDOMIZED_COHORT_FIELDS
    elif method in {
        "paired_same_unit",
        "deterministic_decomposition",
        "directional_observational",
    }:
        required_fields = tuple(
            item for item in _RANDOMIZED_COHORT_FIELDS if item[0] != "assignment_salts"
        )
    else:
        return None, []
    identity: dict[str, str] = {}
    reasons = []
    for field, output_field in required_fields:
        values = context.get(field)
        valid_strings = (
            isinstance(values, list)
            and bool(values)
            and all(isinstance(value, str) and value.strip() for value in values)
        )
        normalized = [value.strip() for value in values] if valid_strings else []
        if not valid_strings or len(set(normalized)) != 1:
            reasons.append(f"mixed_or_missing_{field}")
            continue
        identity[output_field] = normalized[0]
    return (identity if not reasons else None), reasons


def _comparison_method(context: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    value = context.get("comparison_method")
    if not isinstance(value, str) or not value:
        return None, ["missing_comparison_method"]
    if value not in COMPARISON_METHODS:
        return value, ["unsupported_comparison_method"]
    return value, []


def _thresholds_for(method: str | None) -> dict[str, Any]:
    profile = _COMPARISON_PROFILES.get(method or "")
    if profile is None:
        return {}
    return {
        "minimum_independent_units": profile["minimum_independent_units"],
        "minimum_smaller_arm": profile["minimum_smaller_arm"],
        "requires_two_arms": profile["requires_two_arms"],
        "estimand": profile["estimand"],
        "minimum_risk_rejects": 20,
        "minimum_absolute_effect_bp": 5.0,
        "cluster_interval": "95%_cluster_bootstrap_excluding_zero",
        "temporal_stability": "chronological_halves_same_sign",
        "top1_absolute_contribution_share_max": 0.25,
        "top5_absolute_contribution_share_max": 0.50,
    }


def _valid_evidence_refs(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and item.strip() for item in value
    )


def _valid_stage_window(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    start = _timestamp(value.get("start_at"))
    end = _timestamp(value.get("end_at"))
    return start is not None and end is not None and end >= start


def _ownership_reason(layer: str, raw: Mapping[str, Any]) -> str | None:
    ownership = raw.get("ownership")
    if not isinstance(ownership, Mapping):
        return "ownership_evidence_missing"
    if str(ownership.get("stage") or "").lower() != layer.lower():
        return "ownership_stage_mismatch"
    if layer in _CANDIDATE_LAYERS:
        return None
    if layer == "Holding":
        if ownership.get("state") not in {"hypothesis_invalidated", "horizon_expired"}:
            return "holding_invalidity_not_proven"
        if ownership.get("authorization") != "none":
            return "holding_authorization_present"
        if ownership.get("loss_component") != "post_boundary_hold":
            return "holding_loss_component_invalid"
    elif layer == "Exit":
        if ownership.get("thesis") != "valid":
            return "exit_thesis_not_valid"
        if ownership.get("continued_hold") != "reasonable":
            return "continued_hold_not_reasonable"
        if ownership.get("failure") != "protection_geometry":
            return "exit_protection_failure_not_proven"
        if ownership.get("loss_component") != "protection_or_target_geometry":
            return "exit_loss_component_invalid"
    elif layer == "Execution":
        if ownership.get("decision") != "exit_authorized":
            return "execution_exit_decision_missing"
        if ownership.get("loss_component") != "fill_or_execution":
            return "execution_loss_component_invalid"
    return None


def _execution_reason(raw: Mapping[str, Any]) -> str | None:
    evidence = raw.get("execution_evidence")
    if not isinstance(evidence, Mapping):
        return "execution_component_not_isolated"
    if evidence.get("fees_excluded") is not True:
        return "execution_fees_not_excluded"
    if evidence.get("funding_excluded") is not True:
        return "execution_funding_not_excluded"
    if evidence.get("cost_event_id"):
        return "execution_cost_event_overlap"
    if not str(evidence.get("baseline_id") or "").strip():
        return "execution_baseline_identity_missing"
    impact = _finite(evidence.get("incremental_fill_loss_quote"))
    if impact is None or impact < 0:
        return "execution_fill_loss_invalid"
    return None


def _cost_reason(raw: Mapping[str, Any], paired_delta_quote: float, *, strict: bool = True) -> str | None:
    evidence = raw.get("cost_evidence")
    if not isinstance(evidence, Mapping):
        return "cost_baseline_unavailable"
    if not str(evidence.get("cost_event_id") or "").strip():
        return "cost_event_identity_missing"
    if evidence.get("avoidable") is not True:
        return "cost_not_avoidable"
    if evidence.get("baseline_kind") not in _COST_BASELINE_KINDS:
        return "cost_baseline_not_realizable"
    if not str(evidence.get("baseline_id") or "").strip():
        return "cost_baseline_identity_missing"
    if not str(evidence.get("cost_model_id") or "").strip():
        return "cost_model_identity_missing"
    extra = _finite(evidence.get("avoidable_extra_cost_quote"))
    if extra is None or extra < 0:
        return "avoidable_cost_amount_invalid"
    if strict and not math.isclose(extra, abs(paired_delta_quote), rel_tol=1e-9, abs_tol=RECONCILIATION_TOLERANCE_QUOTE):
        return "avoidable_cost_amount_mismatch"
    return None


def _normalize_reconciliation(raw: Mapping[str, Any], delta_quote: float, delta_bp: float) -> tuple[dict[str, Any] | None, str | None]:
    value = raw.get("reconciliation")
    if not isinstance(value, Mapping):
        return None, "reconciliation_mapping_missing"
    mode = str(value.get("mode") or "").strip().lower()
    group_id = str(
        value.get("group_id") or value.get("reconciliation_id") or value.get("residual_id") or ""
    ).strip()
    declared = _finite(
        value.get("declared_total_quote")
        if value.get("declared_total_quote") is not None
        else value.get("declared_net_result_quote")
        if value.get("declared_net_result_quote") is not None
        else value.get("declared_net_delta_quote")
    )
    explicit = declared is not None and all(key in value for key in ("component_quote", "component_bp"))
    component_quote = _finite(value.get("component_quote"))
    component_bp = _finite(value.get("component_bp"))
    if mode not in {"exclusive", "residual"}:
        return None, "reconciliation_mode_invalid"
    if not group_id:
        return None, "reconciliation_identity_missing"
    if declared is None or component_quote is None or component_bp is None:
        return None, "reconciliation_identity_missing"
    if not math.isclose(component_quote, delta_quote, rel_tol=1e-9, abs_tol=RECONCILIATION_TOLERANCE_QUOTE):
        return None, "reconciliation_component_quote_mismatch"
    if not math.isclose(component_bp, delta_bp, rel_tol=1e-9, abs_tol=1e-8):
        return None, "reconciliation_component_bp_mismatch"
    return {
        "mode": mode,
        "group_id": group_id,
        "declared_total_quote": declared,
        "component_quote": component_quote,
        "component_bp": component_bp,
        "explicit": explicit,
    }, None


def _normalized_samples(
    layer: str, raw_samples: Iterable[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], int]:
    fact_unit, economic_unit = _expected_units(layer)
    raw_items = list(raw_samples)
    raw_items.sort(
        key=lambda raw: (
            str(raw.get("unit_id") or "") if isinstance(raw, Mapping) else "",
            str(raw.get("harm_event_id") or "") if isinstance(raw, Mapping) else "",
            str(raw.get("attribution_event_id") or "") if isinstance(raw, Mapping) else "",
            str(raw.get("occurred_at") or "") if isinstance(raw, Mapping) else repr(raw),
            repr(raw),
        )
    )
    samples = []
    exclusions = []
    raw_count = 0
    seen_units: set[str] = set()
    seen_harm_events: set[str] = set()
    seen_attribution_events: set[str] = set()
    seen_cost_events: set[str] = set()
    expected_component = _LAYER_COMPONENTS[layer]
    for raw in raw_items:
        raw_count += 1
        if not isinstance(raw, Mapping):
            exclusions.append("sample_not_object")
            continue
        if raw.get("fact_unit") != fact_unit or raw.get("economic_unit") != economic_unit:
            exclusions.append("denominator_or_economic_unit_mismatch")
            continue
        if raw.get("component") != expected_component:
            exclusions.append("layer_component_mismatch")
            continue
        ownership_reason = _ownership_reason(layer, raw)
        if ownership_reason is not None:
            exclusions.append(ownership_reason)
            continue
        if not _valid_evidence_refs(raw.get("evidence_refs")):
            exclusions.append("evidence_reference_missing")
            continue
        if not _valid_stage_window(raw.get("stage_window")):
            exclusions.append("stage_window_invalid")
            continue
        unit_id = str(raw.get("unit_id") or "")
        cluster_id = str(raw.get("cluster_id") or "")
        harm_event_id = str(raw.get("harm_event_id") or "")
        attribution_event_id = str(raw.get("attribution_event_id") or "")
        delta_bp = _finite(raw.get("paired_net_delta_bp"))
        delta_quote = _finite(raw.get("paired_net_delta_quote"))
        notional_quote = _finite(raw.get("notional_quote"),)
        occurred_at = _timestamp(raw.get("occurred_at"))
        arm_counts = raw.get("arm_counts")
        normalized_arm_counts = None
        if arm_counts is not None:
            if not isinstance(arm_counts, Mapping):
                exclusions.append("incomplete_paired_sample")
                continue
            try:
                normalized_arm_counts = {
                    str(arm): int(float(count))
                    for arm, count in arm_counts.items()
                    if int(float(count)) > 0
                }
            except (TypeError, ValueError, OverflowError):
                exclusions.append("incomplete_paired_sample")
                continue
            if not {"treatment", "control"} <= set(normalized_arm_counts):
                exclusions.append("incomplete_paired_sample")
                continue
        if not unit_id or not cluster_id or not harm_event_id or not attribution_event_id:
            exclusions.append("attribution_identity_missing")
            continue
        if delta_bp is None or delta_quote is None or occurred_at is None:
            exclusions.append("incomplete_paired_sample")
            continue
        if notional_quote is None or notional_quote <= 0:
            exclusions.append("notional_basis_invalid")
            continue
        expected_delta_bp = delta_quote / notional_quote * 10_000.0
        if layer in _POSITION_LAYERS and not str(raw.get("position_id") or "").strip():
            exclusions.append("position_identity_missing")
            continue
        if layer != "Costs" and isinstance(raw.get("cost_evidence"), Mapping):
            exclusions.append("cost_evidence_non_cost_layer")
            continue
        if layer == "Execution":
            execution_reason = _execution_reason(raw)
            if execution_reason is not None:
                exclusions.append(execution_reason)
                continue
        reconciliation, reconciliation_reason = _normalize_reconciliation(raw, delta_quote, delta_bp)
        if reconciliation_reason is not None:
            exclusions.append(reconciliation_reason)
            continue
        if reconciliation.get("explicit") and not math.isclose(delta_bp, expected_delta_bp, rel_tol=1e-9, abs_tol=1e-8):
            exclusions.append("paired_delta_units_mismatch")
            continue
        if layer == "Costs":
            cost_reason = _cost_reason(raw, delta_quote, strict=bool(reconciliation.get("explicit")))
            if cost_reason is not None:
                exclusions.append(cost_reason)
                continue
        if (delta_bp == 0.0) != (delta_quote == 0.0):
            exclusions.append("paired_delta_zero_mismatch")
            continue
        if delta_bp * delta_quote < 0.0:
            exclusions.append("paired_delta_sign_mismatch")
            continue
        if unit_id in seen_units:
            exclusions.append("duplicate_assignment_unit")
            continue
        if harm_event_id in seen_harm_events:
            exclusions.append("duplicate_harm_event")
            continue
        if attribution_event_id in seen_attribution_events:
            exclusions.append("duplicate_attribution_event")
            continue
        cost_evidence = raw.get("cost_evidence")
        cost_event_id = (
            str(cost_evidence.get("cost_event_id") or "")
            if isinstance(cost_evidence, Mapping)
            else ""
        )
        if layer == "Costs" and cost_event_id in seen_cost_events:
            exclusions.append("duplicate_cost_event")
            continue
        seen_units.add(unit_id)
        seen_harm_events.add(harm_event_id)
        seen_attribution_events.add(attribution_event_id)
        if cost_event_id:
            seen_cost_events.add(cost_event_id)
        samples.append(
            {
                "unit_id": unit_id,
                "cluster_id": cluster_id,
                "harm_event_id": harm_event_id,
                "attribution_event_id": attribution_event_id,
                "position_id": str(raw.get("position_id") or "").strip(),
                "paired_net_delta_bp": delta_bp,
                "paired_net_delta_quote": delta_quote,
                "notional_quote": notional_quote,
                "occurred_at": occurred_at,
                "arm": str(raw.get("arm") or ""),
                "arm_counts": normalized_arm_counts,
                "component": expected_component,
                "ownership": dict(raw.get("ownership") or {}),
                "stage_window": dict(raw.get("stage_window") or {}),
                "evidence_refs": list(raw.get("evidence_refs") or []),
                "reconciliation": reconciliation,
                "cost_event_id": cost_event_id or None,
            }
        )
    return samples, exclusions, raw_count


def _epoch_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    for field in (
        "behavior_fingerprints",
        "policy_epochs",
        "market_data_epochs",
        "cost_model_epochs",
    ):
        values = context.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            reasons.append(f"mixed_or_missing_{field}")
            continue
        normalized = [value.strip() for value in values]
        if len(set(normalized)) != 1:
            reasons.append(f"mixed_or_missing_{field}")
    return not reasons, reasons


def _contract_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    if context.get("gradient_contract_version") != PROFIT_GRADIENT_CONTRACT_VERSION:
        reasons.append("gradient_contract_version_mismatch")
    if context.get("reconciliation_contract_version") != RECONCILIATION_CONTRACT_VERSION:
        reasons.append("reconciliation_contract_version_mismatch")
    return not reasons, reasons


def _evidence_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    if context.get("evidence_integrity") != "verified":
        return False, ["evidence_integrity_unverified"]
    return True, []


def _global_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    thresholds = (
        ("candidate_outcome_coverage", 0.80),
        ("position_causal_link_completeness", 0.95),
        ("verified_settled_economics_coverage", 0.95),
        ("ordered_path_coverage", 0.80),
    )
    for field, threshold in thresholds:
        value = _finite(context.get(field))
        if value is None or value < threshold:
            reasons.append(f"{field}_below_{threshold:.2f}")
    candidate_count = _finite(context.get("candidate_count"))
    if candidate_count is None or candidate_count <= 0:
        reasons.append("candidate_count_not_positive")
    elif not candidate_count.is_integer():
        reasons.append("candidate_count_not_integral")
    capital_days = _finite(context.get("capital_days"))
    if capital_days is None or capital_days <= 0:
        reasons.append("capital_days_invalid")
    for field, threshold in (
        ("candidate_outcome_coverage", 1.0),
        ("position_causal_link_completeness", 1.0),
        ("verified_settled_economics_coverage", 1.0),
        ("ordered_path_coverage", 1.0),
    ):
        value = _finite(context.get(field))
        if value is not None and value > threshold:
            reasons.append(f"{field}_above_1.00")
    epoch_ok, epoch_reasons = _epoch_gate(context)
    reasons.extend(epoch_reasons)
    return bool(epoch_ok and not reasons), reasons


def _coerce_raw_samples(value: Any) -> tuple[list[Any], bool]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return [], True
    try:
        return list(value), False
    except TypeError:
        return [], True


def _reconciliation_groups(
    prepared: Mapping[str, tuple[list[dict[str, Any]], list[str], int]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for layer, (samples, _exclusions, _raw_count) in prepared.items():
        for sample in samples:
            reconciliation = sample.get("reconciliation")
            if isinstance(reconciliation, Mapping):
                groups[str(reconciliation["group_id"])].append((layer, sample))
    reports: dict[str, dict[str, Any]] = {}
    invalid: set[str] = set()
    for group_id, entries in sorted(groups.items()):
        first = entries[0][1]["reconciliation"]
        mode = first["mode"]
        declared = first["declared_total_quote"]
        layers = {layer for layer, _sample in entries}
        notionals = [sample.get("notional_quote") for _layer, sample in entries]
        unit_scopes = {
            str(sample.get("position_id") or sample.get("unit_id") or "").strip()
            for _layer, sample in entries
        }
        valid = True
        reason = None
        component_total: float | None = None
        component_bp: float | None = None
        if any(sample["reconciliation"]["mode"] != mode for _layer, sample in entries):
            valid, reason = False, "reconciliation_mode_mismatch"
        elif any(
            not math.isclose(sample["reconciliation"]["declared_total_quote"], declared, rel_tol=1e-9, abs_tol=RECONCILIATION_TOLERANCE_QUOTE)
            for _layer, sample in entries
        ):
            valid, reason = False, "reconciliation_declared_total_mismatch"
        elif any(value is None or not math.isclose(float(value), float(notionals[0]), rel_tol=1e-9, abs_tol=RECONCILIATION_TOLERANCE_QUOTE) for value in notionals):
            valid, reason = False, "reconciliation_notional_mismatch"
        elif mode == "residual" and len(unit_scopes) != 1:
            valid, reason = False, "reconciliation_unit_scope_mismatch"
        elif mode == "exclusive" and len(entries) != 1:
            valid, reason = False, "exclusive_reconciliation_multiple_components"
        else:
            component_total = _finite(sum(float(sample["reconciliation"]["component_quote"]) for _layer, sample in entries))
            component_bp = _finite(sum(float(sample["reconciliation"]["component_bp"]) for _layer, sample in entries))
            expected_bp = declared / float(notionals[0]) * 10_000.0
            if component_total is None or component_bp is None:
                valid, reason = False, "reconciliation_non_finite"
            elif not math.isclose(component_total, declared, rel_tol=1e-9, abs_tol=RECONCILIATION_TOLERANCE_QUOTE):
                valid, reason = False, "reconciliation_component_sum_mismatch"
            elif all(sample["reconciliation"].get("explicit") for _layer, sample in entries) and not math.isclose(component_bp, expected_bp, rel_tol=1e-9, abs_tol=1e-8):
                valid, reason = False, "reconciliation_bp_sum_mismatch"
        if not valid:
            invalid.add(group_id)
        reports[group_id] = {
            "group_id": group_id,
            "mode": mode,
            "status": "reconciled" if valid else "unavailable",
            "layers": sorted(layers),
            "unit_scopes": sorted(unit_scopes),
            "component_count": len(entries),
            "declared_total_quote": declared,
            "component_total_quote": component_total,
            "residual_quote": component_total - declared if component_total is not None else None,
            "reason": reason,
        }
    return reports, invalid


def _prepare_layers(
    layer_samples: Mapping[str, Iterable[Mapping[str, Any]]],
) -> tuple[
    dict[str, tuple[list[dict[str, Any]], list[str], int]],
    set[str],
    set[str],
    set[str],
    dict[str, dict[str, Any]],
]:
    prepared: dict[str, tuple[list[dict[str, Any]], list[str], int]] = {}
    harm_layers: dict[str, set[str]] = defaultdict(set)
    attribution_layers: dict[str, set[str]] = defaultdict(set)
    cost_layers: dict[str, set[str]] = defaultdict(set)
    raw_reconciliation_errors: dict[str, str] = {}
    for layer in LAYERS:
        raw_items, malformed = _coerce_raw_samples(layer_samples.get(layer, ()))
        if malformed:
            prepared[layer] = ([], ["layer_samples_malformed"], 0)
            continue
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            for key, target in (("harm_event_id", harm_layers), ("attribution_event_id", attribution_layers)):
                value = str(raw.get(key) or "").strip()
                if value:
                    target[value].add(layer)
            cost_evidence = raw.get("cost_evidence")
            if isinstance(cost_evidence, Mapping):
                value = str(cost_evidence.get("cost_event_id") or "").strip()
                if value:
                    cost_layers[value].add(layer)
            execution_evidence = raw.get("execution_evidence")
            if isinstance(execution_evidence, Mapping):
                value = str(execution_evidence.get("cost_event_id") or "").strip()
                if value:
                    cost_layers[value].add(layer)
            reconciliation = raw.get("reconciliation")
            if isinstance(reconciliation, Mapping):
                group_id = str(
                    reconciliation.get("group_id")
                    or reconciliation.get("reconciliation_id")
                    or reconciliation.get("residual_id")
                    or ""
                ).strip()
                if group_id:
                    raw_quote = _finite(raw.get("paired_net_delta_quote"))
                    raw_bp = _finite(raw.get("paired_net_delta_bp"))
                    if raw_quote is not None and raw_bp is not None:
                        _normalized_rec, rec_reason = _normalize_reconciliation(raw, raw_quote, raw_bp)
                        if rec_reason is not None:
                            raw_reconciliation_errors[group_id] = rec_reason
        prepared[layer] = _normalized_samples(layer, raw_items)

    harm_conflicts = {value for value, layers in harm_layers.items() if len(layers) > 1}
    attribution_conflicts = {value for value, layers in attribution_layers.items() if len(layers) > 1}
    cost_conflicts = {value for value, layers in cost_layers.items() if len(layers) > 1}
    reports, invalid_reconciliation = _reconciliation_groups(prepared)
    for group_id, reason in raw_reconciliation_errors.items():
        invalid_reconciliation.add(group_id)
        reports[group_id] = {
            "group_id": group_id,
            "mode": "unknown",
            "status": "unavailable",
            "layers": [],
            "unit_scopes": [],
            "component_count": 0,
            "declared_total_quote": None,
            "component_total_quote": None,
            "residual_quote": None,
            "reason": reason,
        }
    # A cross-layer event is permitted only when every component explicitly
    # belongs to one valid residual reconciliation group.
    for conflict_values, declared_layers in ((harm_conflicts, harm_layers), (attribution_conflicts, attribution_layers)):
        allowed: set[str] = set()
        for event_id in list(conflict_values):
            matches = {
                str(sample.get("reconciliation", {}).get("group_id"))
                for layer, (samples, _exclusions, _raw_count) in prepared.items()
                if layer in declared_layers[event_id]
                for sample in samples
                if sample.get("harm_event_id" if declared_layers is harm_layers else "attribution_event_id") == event_id
            }
            if len(matches) == 1:
                group = reports.get(next(iter(matches)))
                if group and group.get("status") == "reconciled" and group.get("mode") == "residual":
                    allowed.add(event_id)
        conflict_values.difference_update(allowed)
    for layer, (samples, exclusions, raw_count) in prepared.items():
        bad_harm = {value for value in harm_conflicts if layer in harm_layers[value]}
        bad_attr = {value for value in attribution_conflicts if layer in attribution_layers[value]}
        bad_cost = {value for value in cost_conflicts if layer in cost_layers[value]}
        if bad_harm:
            exclusions.extend(["cross_layer_harm_event"] * len(bad_harm))
        if bad_attr:
            exclusions.extend(["cross_layer_attribution_event"] * len(bad_attr))
        if bad_cost:
            exclusions.extend(["cross_layer_cost_event"] * len(bad_cost))
        if invalid_reconciliation:
            exclusions.extend(
                "reconciliation_mismatch"
                for sample in samples
                if sample.get("reconciliation", {}).get("group_id") in invalid_reconciliation
            )
        kept = [
            sample for sample in samples
            if sample.get("harm_event_id") not in bad_harm
            and sample.get("attribution_event_id") not in bad_attr
            and sample.get("cost_event_id") not in bad_cost
            and sample.get("reconciliation", {}).get("group_id") not in invalid_reconciliation
        ]
        prepared[layer] = (kept, exclusions, raw_count)
    return prepared, harm_conflicts, cost_conflicts, attribution_conflicts, reports


def _layer_result(
    layer: str,
    raw_samples: Iterable[Mapping[str, Any]],
    *,
    prepared: tuple[list[dict[str, Any]], list[str], int] | None = None,
    candidate_count: int,
    capital_days: float | None,
    risk_reject_count: int,
    global_ok: bool,
    global_reasons: list[str],
    comparison_method: str | None,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    samples, exclusions, raw_count = prepared or _normalized_samples(layer, raw_samples)
    unit_count = len(samples)
    profile = _COMPARISON_PROFILES.get(comparison_method or "")
    estimator = {
        "prospective_randomized": estimate_prospective_randomized,
        "paired_same_unit": estimate_paired_same_unit,
        "deterministic_decomposition": estimate_deterministic_decomposition,
        "directional_observational": estimate_deterministic_decomposition,
    }.get(comparison_method, estimate_deterministic_decomposition)
    estimate_result = estimator(samples)
    estimate = estimate_result.paired_net_delta_bp
    lower = estimate_result.lower_95_bp
    upper = estimate_result.upper_95_bp
    total_quote = estimate_result.harm_quote
    top1 = estimate_result.top1_absolute_contribution_share
    top5 = estimate_result.top5_absolute_contribution_share
    independent_unit_count = estimate_result.independent_unit_count
    temporal_stable = estimate_result.temporal_stable
    frequency = unit_count / candidate_count if candidate_count > 0 else None
    raw_gradient = frequency * lower if frequency is not None and lower is not None else None
    gradient = _finite(raw_gradient) if raw_gradient is not None else None
    reasons = list(global_reasons)
    reasons.extend(estimate_result.method_reasons)
    if raw_gradient is not None and gradient is None:
        reasons.append("non_finite_aggregate_metric")
    minimum_units = int(thresholds.get("minimum_independent_units") or 30)
    if independent_unit_count < minimum_units:
        reasons.append(f"independent_units_below_{minimum_units}")
    if layer == "Risk" and risk_reject_count < int(thresholds.get("minimum_risk_rejects") or 20):
        reasons.append("risk_rejects_below_20")
    if estimate is None or abs(estimate) < float(thresholds.get("minimum_absolute_effect_bp") or 5.0):
        reasons.append("absolute_effect_below_5bp")
    if lower is None or upper is None or lower <= 0 <= upper:
        reasons.append("cluster_interval_crosses_zero")
    if estimate is None or estimate <= 0:
        reasons.append("direction_does_not_reduce_harm")
    if not temporal_stable:
        reasons.append("temporal_halves_disagree")
    if top1 is None or top5 is None:
        reasons.append("concentration_unavailable")
    else:
        if top1 > float(thresholds.get("top1_absolute_contribution_share_max") or 0.25):
            reasons.append("top1_concentration_above_25pct")
        if top5 > float(thresholds.get("top5_absolute_contribution_share_max") or 0.50):
            reasons.append("top5_concentration_above_50pct")
    reasons.extend(exclusions)
    harm_per_100 = (
        total_quote / candidate_count * 100.0
        if total_quote is not None and candidate_count > 0
        else None
    )
    harm_per_day = (
        total_quote / capital_days
        if total_quote is not None and capital_days is not None and capital_days > 0
        else None
    )
    raw_normalized_metrics = (harm_per_100, harm_per_day)
    normalized_metrics = tuple(
        _finite(value) if value is not None else None for value in raw_normalized_metrics
    )
    if any(
        original is not None and normalized is None
        for original, normalized in zip(raw_normalized_metrics, normalized_metrics)
    ):
        reasons.append("non_finite_aggregate_metric")
    harm_per_100, harm_per_day = normalized_metrics
    eligible = bool(global_ok and not reasons)
    fact_unit, economic_unit = _expected_units(layer)
    return {
        "layer": layer,
        "fact_unit": fact_unit,
        "economic_unit": economic_unit,
        "raw_sample_count": raw_count,
        "eligible_sample_count": unit_count,
        "excluded_sample_count": raw_count - unit_count,
        "exclusion_waterfall": dict(sorted(Counter(exclusions).items())),
        "independent_unit_count": independent_unit_count,
        "cluster_count": len({sample["cluster_id"] for sample in samples}),
        "cluster_count_by_arm": (
            dict(estimate_result.cluster_count_by_arm)
            if estimate_result.cluster_count_by_arm is not None
            else None
        ),
        "comparison_method": comparison_method,
        "estimand": profile["estimand"] if profile else None,
        "evidence_role": profile["evidence_role"] if profile else None,
        "thresholds": dict(thresholds),
        "arm_counts": dict(estimate_result.arm_counts),
        "unassigned_arm_count": estimate_result.unassigned_arm_count,
        "smaller_arm_count": estimate_result.smaller_arm_count,
        "eligible_frequency": frequency,
        "paired_net_delta_bp": estimate,
        "lower_95_bp": lower,
        "upper_95_bp": upper,
        "gradient_lower_bp": gradient,
        "harm_quote": total_quote,
        "harm_quote_per_100_candidates": harm_per_100,
        "harm_quote_per_capital_day": harm_per_day,
        "top1_absolute_contribution_share": top1,
        "top5_absolute_contribution_share": top5,
        "gate_status": "eligible" if eligible else "ineligible",
        "gate_reasons": sorted(set(reasons)),
    }


def select_unique_max_direction(
    layer_samples: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    evidence_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a winner only when the declared method permits one."""
    if not isinstance(layer_samples, Mapping):
        layer_samples = {}
    if not isinstance(evidence_context, Mapping):
        evidence_context = {}
    global_ok, global_reasons = _global_gate(evidence_context)
    contract_ok, contract_reasons = _contract_gate(evidence_context)
    evidence_ok, evidence_reasons = _evidence_gate(evidence_context)
    comparison_method, method_reasons = _comparison_method(evidence_context)
    cohort_identity, identity_reasons = _randomized_cohort_identity(
        evidence_context, comparison_method
    )
    prepared, conflicts, cost_conflicts, attribution_conflicts, reconciliation = _prepare_layers(
        layer_samples
    )
    missing_layers = sorted(set(LAYERS) - set(layer_samples))
    unknown_layers = sorted(set(layer_samples) - set(LAYERS))
    malformed_layers = any(
        any(reason in {"layer_samples_malformed", "sample_not_object"} for reason in exclusions)
        for _samples, exclusions, _raw_count in prepared.values()
    )
    invalid_reconciliation = any(report["status"] != "reconciled" for report in reconciliation.values())
    if conflicts:
        global_reasons.append("cross_layer_harm_event")
    if cost_conflicts:
        global_reasons.append("cross_layer_cost_event")
    if attribution_conflicts:
        global_reasons.append("cross_layer_attribution_event")
    if malformed_layers:
        global_reasons.append("layer_samples_malformed")
    if missing_layers:
        global_reasons.append("layer_samples_incomplete")
    if unknown_layers:
        global_reasons.append("layer_samples_unknown_layer")
    if invalid_reconciliation:
        global_reasons.append("reconciliation_mismatch")
    declared_values = [
        float(report["declared_total_quote"])
        for report in reconciliation.values()
        if report.get("status") == "reconciled"
    ]
    declared_total_quote = _finite(sum(declared_values))
    if declared_total_quote is None and declared_values:
        global_reasons.append("reconciliation_non_finite")
        invalid_reconciliation = True
    supplied_total = _finite(
        evidence_context.get("declared_net_result_quote")
        if evidence_context.get("declared_net_result_quote") is not None
        else (evidence_context.get("reconciliation") or {}).get("declared_net_result_quote")
        if isinstance(evidence_context.get("reconciliation"), Mapping)
        else None
    )
    if supplied_total is not None and declared_total_quote is not None and not math.isclose(
        supplied_total, declared_total_quote, rel_tol=1e-9, abs_tol=RECONCILIATION_TOLERANCE_QUOTE
    ):
        global_reasons.append("reconciliation_net_result_mismatch")
        invalid_reconciliation = True
    global_reasons.extend(method_reasons)
    global_reasons.extend(identity_reasons)
    global_reasons.extend(contract_reasons)
    global_reasons.extend(evidence_reasons)
    global_ok = bool(
        global_ok
        and contract_ok
        and evidence_ok
        and not method_reasons
        and not identity_reasons
        and not conflicts
        and not cost_conflicts
        and not attribution_conflicts
        and not malformed_layers
        and not missing_layers
        and not unknown_layers
        and not invalid_reconciliation
    )
    thresholds = _thresholds_for(comparison_method)
    profile = _COMPARISON_PROFILES.get(comparison_method or "")
    unique_winner_eligible = bool(profile and profile["allows_unique_winner"])
    candidate_count = _positive_integer(evidence_context.get("candidate_count")) or 0
    capital_days = _finite(evidence_context.get("capital_days"))
    risk_reject_count = int(_finite(evidence_context.get("risk_reject_count")) or 0)
    layers = {
        layer: _layer_result(
            layer,
            layer_samples.get(layer, ()),
            prepared=prepared[layer],
            candidate_count=candidate_count,
            capital_days=capital_days,
            risk_reject_count=risk_reject_count,
            global_ok=global_ok,
            global_reasons=global_reasons,
            comparison_method=comparison_method,
            thresholds=thresholds,
        )
        for layer in LAYERS
    }
    eligible = [
        result
        for result in layers.values()
        if result["gate_status"] == "eligible" and unique_winner_eligible
    ]
    eligible.sort(key=lambda result: result["gradient_lower_bp"], reverse=True)
    winner = eligible[0] if len(eligible) == 1 else None
    runner_upper = max(
        (
            result["upper_95_bp"]
            for result in layers.values()
            if result is not winner and result["upper_95_bp"] is not None
        ),
        default=0.0,
    )
    separated = bool(winner is not None and winner["lower_95_bp"] is not None and winner["lower_95_bp"] > runner_upper)
    winner_layer = winner["layer"] if separated and winner is not None else None
    verdict = "unique_max_direction" if separated else "no_unique_direction"
    next_action = (
        "register_one_single_variable_forward_experiment"
        if separated
        else "rank_next_question_only"
        if comparison_method == "directional_observational"
        else "retain_measured_fact_no_behavior_promotion"
        if comparison_method == "deterministic_decomposition"
        else "close_largest_evidence_gap_only"
    )
    return {
        "contract_version": PROFIT_GRADIENT_CONTRACT_VERSION,
        "comparison_method": comparison_method,
        "estimand": profile["estimand"] if profile else None,
        "evidence_role": profile["evidence_role"] if profile else None,
        "evidence_integrity": evidence_context.get("evidence_integrity"),
        "reconciliation_contract_version": evidence_context.get("reconciliation_contract_version"),
        "cohort_identity": cohort_identity,
        "thresholds": thresholds,
        "unique_winner_eligible": unique_winner_eligible,
        "verdict": verdict,
        "winner_layer": winner_layer,
        "runner_up_harm_upper_bp": runner_upper,
        "global_gate_status": "eligible" if global_ok else "ineligible",
        "global_gate_reasons": sorted(set(global_reasons)),
        "attribution_integrity": (
            "ineligible"
            if (
                conflicts
                or cost_conflicts
                or attribution_conflicts
                or malformed_layers
                or missing_layers
                or unknown_layers
                or invalid_reconciliation
                or not contract_ok
                or not evidence_ok
            )
            else "eligible"
            if any(result["eligible_sample_count"] for result in layers.values())
            else "unavailable"
        ),
        "cross_layer_harm_event_count": len(conflicts),
        "cross_layer_cost_event_count": len(cost_conflicts),
        "cross_layer_attribution_event_count": len(attribution_conflicts),
        "incomplete_layers": [
            layer for layer, result in layers.items() if result["eligible_sample_count"] == 0
        ],
        "reconciliation": {
            "contract_version": RECONCILIATION_CONTRACT_VERSION,
            "tolerance_quote": RECONCILIATION_TOLERANCE_QUOTE,
            "group_count": len(reconciliation),
            "declared_total_quote": declared_total_quote,
            "component_total_quote": _finite(sum(
                float(report["component_total_quote"])
                for report in reconciliation.values()
                if report.get("status") == "reconciled"
            )),
            "groups": [reconciliation[key] for key in sorted(reconciliation)],
            "status": "unavailable" if invalid_reconciliation else "reconciled" if reconciliation else "unavailable",
        },
        "residual": {
            "status": (
                "unresolved" if conflicts or cost_conflicts or attribution_conflicts or invalid_reconciliation
                else "reconciled" if any(report.get("mode") == "residual" for report in reconciliation.values())
                else "none"
            ),
            "cross_layer_harm_event_count": len(conflicts),
            "cross_layer_cost_event_count": len(cost_conflicts),
            "economic_amount": (
                _finite(sum(
                    float(report["declared_total_quote"])
                    for report in reconciliation.values()
                    if report.get("status") == "reconciled" and report.get("mode") == "residual"
                ))
                if not (conflicts or cost_conflicts or attribution_conflicts or invalid_reconciliation)
                else None
            ),
            "reason": (
                "cross_layer_harm_event" if conflicts else
                "cross_layer_cost_event" if cost_conflicts else
                "cross_layer_attribution_event" if attribution_conflicts else
                "reconciliation_mismatch" if invalid_reconciliation else None
            ),
        },
        "layers": layers,
        "next_action": next_action,
        "automatic_behavior_change_authorized": False,
    }
