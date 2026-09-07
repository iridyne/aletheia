"""Prospective assignment and fail-closed numeric Learning influence."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from uuid import uuid4

_log = logging.getLogger(__name__)

# Process-scoped last-good `_policy_epoch` inputs, keyed by path_id.
# On transient `load_config` failures, we reuse the last successful `values`
# for the same path so a flaky read doesn't rotate the epoch twice (once
# to the sentinel, once back on recovery) and shred the sample pool.
_LAST_SUCCESS_POLICY_VALUES: dict[str, dict] = {}

from aletheia.config.loader import load_config
from aletheia.learning.policy_epochs import PolicyEpoch, build_policy_epoch
from aletheia.learning.registry import get_learning_path
from aletheia.universe.outcome_economics import COST_MODEL_VERSION
from aletheia.observation_lab.canonical import NumericTreatmentAdapter as _ObservationNumericAdapter

_COST_MODEL_MEASURED_PATH_IDS = frozenset({
    "universe_family_calibration",
    "ranker_case_score",
})


@dataclass(frozen=True)
class InfluenceDecision:
    value: float
    applied: bool
    assignment: str
    reason: str
    hypothesis_id: str | None = None
    experiment_id: str | None = None
    exposure_id: str | None = None


def _resolve_numeric_via_observation_lab(
    *,
    path_id: str,
    scope: dict,
    action: dict,
    source_cohort: str,
    target_horizon: str,
    candidate_key: str | None,
    baseline: float,
    treatment: float,
    consumer: str,
    consumer_stage: str,
    outcome_candidate_key: str | None,
    manifest: dict | None,
    assignment_key: str | None,
    exposed_at: str | None,
    outcome_seeds: list | None,
    require_outcome_seed: bool,
    deferred_exposure_writes: list[dict] | None,
    runtime_db: sqlite3.Connection | None,
) -> InfluenceDecision:
    """Use the canonical Lab seam without reading the legacy Experiment ledger."""
    from aletheia.observation_lab.canonical import (
        append_delivery_receipt,
        get_authority_source,
        _outbox_capacity_available,
        register_treatment_adapter,
        resolve_observation_treatment,
    )

    baseline_value = float(baseline)
    treatment_value = float(treatment)
    if not math.isfinite(baseline_value) or not math.isfinite(treatment_value):
        raise ValueError("numeric Learning treatment values must be finite")
    # A measurement-unsupported path can never attach a hypothesis, experiment or
    # exposure, so it never needs Lab authority. Probing authority first would
    # report `authority_source_unavailable` — a transient, actionable-looking
    # reason — for what is a permanent design state. The Learning Observability
    # contract requires the declared static fallback with `measurement_unsupported`
    # (docs/design/proposals/learning_observability.md §8.3).
    # `get_learning_path` raises on an undeclared path — that is a programming
    # error the allowlist must surface, not a reason code to swallow.
    if not get_learning_path(path_id).measurement_supported:
        return InfluenceDecision(
            baseline_value, False, "unassigned", "measurement_unsupported",
        )
    try:
        authority = get_authority_source(db=runtime_db)
    except (FileNotFoundError, RuntimeError, sqlite3.Error):
        return InfluenceDecision(
            baseline_value, False, "unassigned", "authority_source_unavailable",
        )
    if authority.source != "lab":
        return InfluenceDecision(
            baseline_value, False, "unassigned", "authority_source_unavailable",
        )
    receipt_id = (
        "observation-treatment:"
        + hashlib.sha256(json.dumps({
            "path_id": path_id,
            "candidate_key": candidate_key,
            "assignment_key": assignment_key or candidate_key,
            "consumer": consumer,
            "consumer_stage": consumer_stage,
            "authority_epoch": authority.epoch,
            "manifest_hash": (manifest or {}).get("manifest_hash"),
            "outcome_candidate_key": outcome_candidate_key,
        }, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
        if candidate_key
        else uuid4().hex
    )
    if deferred_exposure_writes is not None:
        from aletheia.storage.sqlite import get_db

        if not _outbox_capacity_available(runtime_db or get_db(), receipt_id=receipt_id):
            # Do not queue a receipt that cannot fit when the publication
            # transaction later commits.  The already-computed score stays on
            # the deterministic baseline and the ordinary Paper publication
            # can continue while the Lab bridge drains.
            return InfluenceDecision(
                baseline_value, False, "unassigned", "outbox_backpressure",
            )
    persist_receipt = deferred_exposure_writes is None
    adapter_id = str((manifest or {}).get("treatment_adapter_id") or f"learning.numeric:{path_id}")
    adapter_version = str((manifest or {}).get("treatment_adapter_version") or "v1")
    if (adapter_id, adapter_version) == (f"learning.numeric:{path_id}", "v1"):
        register_treatment_adapter(
            _ObservationNumericAdapter(
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                behavior_dimension=path_id,
                requires_outcome_seed=bool(require_outcome_seed or outcome_candidate_key),
            ),
            replace=True,
        )
    context = {
        "path_id": path_id,
        "scope": dict(scope),
        "action": dict(action),
        "source_cohort": source_cohort,
        "target_horizon": target_horizon,
        "candidate_key": candidate_key,
        "assignment_key": assignment_key or candidate_key,
        "baseline": {"value": baseline_value},
        "treatment": {"value": treatment_value},
        "interference_key": (manifest or {}).get("interference_key") or path_id,
        "outcome_candidate_key": outcome_candidate_key,
        "outcome_seed": (outcome_seeds or [None])[0],
    }
    gate_reason = _learning_gate_reason()
    if gate_reason is not None:
        receipt_values = {
            # No Lab Generation is selected while a global gate is active.  A
            # producer-only capture preserves the refusal without fabricating
            # an Exposure identity.
            "receipt_kind": "capture",
            "receipt_id": receipt_id,
            "treatment_adapter_id": adapter_id,
            "treatment_adapter_version": adapter_version,
            "assignment_unit_key": assignment_key or candidate_key,
            "candidate_key": candidate_key,
            "consumer": consumer,
            "consumer_stage": consumer_stage,
            "assignment": "unassigned",
            "applied": False,
            "behavior_changed": False,
            "baseline_payload": context["baseline"],
            "applied_payload": context["baseline"],
            "outcome_candidate_key": outcome_candidate_key,
            "outcome_seeds": outcome_seeds,
            "outcome_source": "fixed_horizon_candidate" if outcome_candidate_key else None,
            "resolution_reason": gate_reason,
            "authority_source": authority.source,
            "authority_epoch": authority.epoch,
            "runtime_session_id": (manifest or {}).get("runtime_session_id"),
            "producer_commit": (manifest or {}).get("producer_commit"),
        }
        if persist_receipt:
            try:
                receipt = append_delivery_receipt(db=runtime_db, **receipt_values)
            except RuntimeError as exc:
                if str(exc) != "observation_outbox_capacity":
                    raise
                # The bounded Lab transport is full.  Keep the producer on
                # deterministic baseline without growing the backlog.
                receipt = None
        else:
            deferred_exposure_writes.append({"canonical_receipt": receipt_values})
            receipt = receipt_id
        return InfluenceDecision(
            baseline_value, False, "unassigned", gate_reason, exposure_id=receipt,
        )

    decision = resolve_observation_treatment(
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        candidate_key=candidate_key,
        context=context,
        baseline=context["baseline"],
        now=exposed_at,
        db=runtime_db,
        outcome_candidate_key=outcome_candidate_key,
        outcome_seeds=outcome_seeds,
        consumer=consumer,
        consumer_stage=consumer_stage,
        runtime_session_id=(manifest or {}).get("runtime_session_id"),
        producer_commit=(manifest or {}).get("producer_commit"),
        receipt_id=receipt_id,
        persist_receipt=persist_receipt,
    )
    payload_value = decision.payload.get("value", baseline_value)
    if isinstance(payload_value, bool) or not isinstance(payload_value, (int, float)):
        payload_value = baseline_value
    payload_value = float(payload_value)
    if not persist_receipt and not (
        decision.reason == "outbox_backpressure" and decision.receipt_id is None
    ):
        deferred_exposure_writes.append({
            "canonical_receipt": {
                "receipt_kind": (
                    "treatment" if decision.observation_id is not None else "capture"
                ),
                "receipt_id": decision.receipt_id or receipt_id,
                "observation_id": decision.observation_id,
                "generation": decision.generation,
                "manifest_id": decision.manifest_id,
                "manifest_hash": decision.manifest_hash,
                "treatment_adapter_id": adapter_id,
                "treatment_adapter_version": adapter_version,
                "assignment_unit_key": assignment_key or candidate_key,
                "candidate_key": candidate_key,
                "consumer": consumer,
                "consumer_stage": consumer_stage,
                "assignment": decision.assignment,
                "applied": decision.applied,
                "behavior_changed": decision.behavior_changed,
                "baseline_payload": context["baseline"],
                "applied_payload": decision.payload,
                "outcome_candidate_key": outcome_candidate_key,
                "outcome_seeds": outcome_seeds,
                "outcome_source": "fixed_horizon_candidate" if outcome_candidate_key else None,
                "resolution_reason": decision.reason,
                "active_manifest_snapshot": list(decision.active_manifest_snapshot),
                "manifest_set_fingerprint": decision.manifest_set_fingerprint,
                "revocation_snapshot_fingerprint": decision.revocation_snapshot_fingerprint,
                "authority_source": authority.source,
                "authority_epoch": authority.epoch,
                "runtime_session_id": (manifest or {}).get("runtime_session_id"),
                "producer_commit": (manifest or {}).get("producer_commit"),
            },
        })
    return InfluenceDecision(
        payload_value if decision.behavior_changed else baseline_value,
        decision.behavior_changed,
        decision.assignment,
        decision.reason,
        hypothesis_id=None,
        experiment_id=decision.observation_id,
        exposure_id=decision.receipt_id,
    )


def _learning_gate_reason() -> str | None:
    from aletheia.learning.control import learning_influence_gate_reason
    return learning_influence_gate_reason()


def _policy_epoch_definition(
    path_id: str, *, persist_config_migrations: bool = True,
) -> PolicyEpoch:
    try:
        cfg = (
            load_config()
            if persist_config_migrations
            else load_config(persist_migrations=False)
        )
        values = {
            "behavior_contract_version": get_learning_path(
                path_id
            ).behavior_contract_version,
            "autonomous_learning": cfg.agent.autonomous_learning,
            "permission_mode": cfg.agent.permission_mode,
            "scanner_weights": cfg.universe.scanner_weights,
            "scanner_regime_scoring": cfg.universe.scanner_regime_scoring,
            # cfg.risk and cfg.trading are deliberately NOT folded into the
            # epoch hash. They contain scheduler/exchange-plumbing knobs
            # (max_open_positions, min_notional, throttle windows, etc.) whose
            # changes don't alter this path's *learning* behavior — but every
            # tweak used to rotate the epoch and shred the sample pool
            # (audit: LEARNING_MODULE_SDD_VS_REALITY §5 GAP-05, C-DF-10:
            # "policy epoch is rotated only on effective behavior changes").
            # Any path-relevant policy signal already reaches the hash via
            # scanner_weights / scanner_regime_scoring / universe_behavior /
            # operator_decision_context below.
            # Note: the running build fingerprint is intentionally NOT folded
            # into the epoch hash — a rebuild with an unchanged effective
            # policy must not rotate the epoch (retention: it churned
            # learning_policy_epochs every restart with zero policy delta).
            # The commit still reaches each Lab delivery receipt as
            # ``producer_commit`` (taken from the manifest), so build
            # provenance survives without inflating the epoch table.
            # Lifecycle and paper-influence settings change the effective
            # consumer policy, so a successor cannot inherit authority across
            # a settings change.  Scheduling fields (evaluation_interval_minutes,
            # contribution_evaluation_enabled) don't affect treatment behavior and
            # must not trigger unnecessary epoch rotation.
            "learning": {
                "lifecycle_automation_enabled": cfg.learning.lifecycle_automation_enabled,
                "paper_influence_enabled": cfg.learning.paper_influence_enabled,
                # Only whether THIS path is allowlisted matters for its epoch.
                # Adding or removing a different path must not rotate this epoch.
                "path_in_allowlist": path_id in (cfg.learning.paper_influence_paths or []),
                "auto_refreeze": cfg.learning.auto_refreeze,
                "paper_active_allocation_pct": cfg.learning.paper_active_allocation_pct,
            },
        }
        # P1-1: universe scoring/allocation config materially changes which candidates
        # win — it is path-relevant behavior the epoch previously ignored, so a change
        # to it must rotate the universe epoch (and invalidate prior universe verdicts).
        if path_id == "universe_family_calibration":
            u = cfg.universe
            values["universe_behavior"] = {
                "blacklist": sorted(set(u.blacklist)),
                "family_quota": u.family_quota,
                "family_quota_by_regime": u.family_quota_by_regime,
                "explore_ratio": u.explore_ratio,
                "max_candidates": u.max_candidates,
                "core_pool_size": u.core_pool_size,
                "min_volume_24h_usdt": u.min_volume_24h_usdt,
                "max_spread_bps": u.max_spread_bps,
                "min_depth_10bps_usd": u.min_depth_10bps_usd,
            }
        if path_id == "operator_learning_bundle":
            # Prompt-bundle contribution cannot pool observations across a
            # change in the candidate facts/decision-context contract. The
            # producing commit remains separate provenance; these effective
            # semantics rotate the actual Learning policy cohort.
            from aletheia.agent_shell.skip_policy import skip_policy_context
            from aletheia.learning.opportunity_information_policy import (
                opportunity_information_policy_context,
            )

            values["operator_decision_context"] = skip_policy_context()
            values["opportunity_information_policy"] = (
                opportunity_information_policy_context()
            )
            from aletheia.trading.sizing import (
                AVAILABLE_BALANCE_RISK_BUDGET_POLICY_VERSION,
            )

            # Entry sizing is part of the treatment identity.  A cohort that
            # mixes conviction/volatility sizing with account-risk budgeting
            # cannot support a causal outcome comparison — and neither can one
            # that mixes equity-based sizing with available-balance sizing, since
            # the latter shrinks each successive entry.  The version string is
            # what keeps those outcomes in separate pools.
            values["operator_sizing_policy"] = {
                "policy_version": AVAILABLE_BALANCE_RISK_BUDGET_POLICY_VERSION,
                "sizing_basis": "available_balance",
                "per_trade_risk_source": "risk_policy.loss.max_single_trade_risk_pct",
                "futures_notional_source": "cost_budget_times_effective_leverage",
                "demo_per_trade_risk_pct": cfg.risk.demo.loss.max_single_trade_risk_pct,
                "live_per_trade_risk_pct": cfg.risk.live.loss.max_single_trade_risk_pct,
            }
        _LAST_SUCCESS_POLICY_VALUES[path_id] = values
    except Exception as exc:
        cached = _LAST_SUCCESS_POLICY_VALUES.get(path_id)
        if cached is not None:
            _log.warning(
                "_policy_epoch config load failed for %r: %s — reusing last-good values",
                path_id, exc,
            )
            values = cached
        else:
            # No prior success — fall back to a path-stable sentinel so a
            # cold-start transient failure still yields a deterministic epoch
            # (rather than rotating on every retry).
            _log.warning(
                "_policy_epoch config load failed for %r: %s — no cached values; using sentinel",
                path_id, exc,
            )
            values = {"config": "unavailable", "path_id": path_id}
    if path_id in _COST_MODEL_MEASURED_PATH_IDS:
        values = {**values, "cost_model_version": COST_MODEL_VERSION}
    return build_policy_epoch(
        f"decision_policy:{path_id}", values, affected_path_ids=(path_id,)
    )


def current_policy_epoch(
    path_id: str, *, persist_config_migrations: bool = True,
) -> str | None:
    """Resolve the current persisted epoch without creating or rotating state."""
    from aletheia.learning.policy_epochs import find_open_policy_epoch

    return find_open_policy_epoch(_policy_epoch_definition(
        path_id,
        persist_config_migrations=persist_config_migrations,
    ))


def apply_numeric_treatment(*, path_id: str, scope: dict, action: dict, source_cohort: str,
target_horizon: str, candidate_key: str | None, baseline: float,
treatment: float, consumer: str, consumer_stage: str,
outcome_candidate_key: str | None = None, opportunity_id: str | None = None,
manifest: dict | None = None,
assignment_key: str | None = None,
expected_hypothesis_fingerprint: str | None = None,
expected_experiment_id: str | None = None,
suppress_shadow_if_paper_active: bool = False,
exposed_at: str | None = None,
outcome_seeds: list | None = None,
require_outcome_seed: bool = False,
deferred_exposure_writes: list[dict] | None = None,
runtime_db: sqlite3.Connection | None = None,) -> InfluenceDecision:
    """Apply numeric influence only through the canonical Lab authority."""
    return _resolve_numeric_via_observation_lab(
        path_id=path_id, scope=scope, action=action,
        source_cohort=source_cohort, target_horizon=target_horizon,
        candidate_key=candidate_key, baseline=baseline, treatment=treatment,
        consumer=consumer, consumer_stage=consumer_stage,
        outcome_candidate_key=outcome_candidate_key, manifest=manifest,
        assignment_key=assignment_key, exposed_at=exposed_at,
        outcome_seeds=outcome_seeds, require_outcome_seed=require_outcome_seed,
        deferred_exposure_writes=deferred_exposure_writes, runtime_db=runtime_db,
    )


def apply_boolean_treatment(
    *, path_id: str, scope: dict, action: dict, source_cohort: str,
    target_horizon: str, candidate_key: str | None, consumer: str,
    consumer_stage: str, outcome_candidate_key: str | None = None,
    opportunity_id: str | None = None, manifest: dict | None = None,
    runtime_db: sqlite3.Connection | None = None,
) -> InfluenceDecision:
    """Apply one default-off boolean treatment through the numeric authority seam.

    Prompt injection and learned proposal filters are binary effects, but they must
    use the same prospective assignment, exact-path allowlist, policy epoch,
    authority resolver, and immutable exposure ledger as numeric treatments.  A
    control or refrozen candidate therefore remains ``0.0``/baseline; only an
    authorized treatment candidate becomes ``1.0``/applied.
    """
    return apply_numeric_treatment(
        path_id=path_id,
        scope=scope,
        action=action,
        source_cohort=source_cohort,
        target_horizon=target_horizon,
        candidate_key=candidate_key,
        baseline=0.0,
        treatment=1.0,
        consumer=consumer,
        consumer_stage=consumer_stage,
        outcome_candidate_key=outcome_candidate_key,
        opportunity_id=opportunity_id,
        manifest=manifest,
        runtime_db=runtime_db,
    )
