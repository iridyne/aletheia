"""Prospective assignment and fail-closed numeric Learning influence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import math
from uuid import uuid4

_log = logging.getLogger(__name__)

# Process-scoped last-good `_policy_epoch` inputs, keyed by path_id.
# On transient `load_config` failures, we reuse the last successful `values`
# for the same path so a flaky read doesn't rotate the epoch twice (once
# to the sentinel, once back on recovery) and shred the sample pool.
_LAST_SUCCESS_POLICY_VALUES: dict[str, dict] = {}

from aletheia.config.loader import load_config
from aletheia.learning.hypotheses import (
    HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION,
    hypothesis_fingerprint,
)
from aletheia.learning.behavior_contracts import current_behavior_contract_hash
from aletheia.learning.ledger import (
    get_record,
    get_hypothesis_by_fingerprint,
    get_exposure_by_dedup_key,
    get_open_experiment,
    insert_experiment,
    insert_exposure,
    insert_hypothesis,
)
from aletheia.learning.models import LearningExperiment, LearningExposure, LearningHypothesis
from aletheia.learning.registry import get_learning_path
from aletheia.learning.policy_epochs import PolicyEpoch, build_policy_epoch

# Hypothesis statuses that forbid opening a *new* experiment. They are durable
# terminal decisions on the hypothesis, not merely on one experiment row.
TERMINAL_HYPOTHESIS_STATUSES = frozenset({"refrozen", "idle_expired"})


@dataclass(frozen=True)
class InfluenceDecision:
    value: float
    applied: bool
    assignment: str
    reason: str
    hypothesis_id: str | None = None
    experiment_id: str | None = None
    exposure_id: str | None = None


def stable_assignment(experiment: LearningExperiment, candidate_key: str) -> str:
    raw = f"{experiment.experiment_id}:{experiment.assignment_salt}:{candidate_key}"
    fraction = int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16) / float(0xFFFFFFFFFFFFFFFF)
    return "treatment" if fraction < experiment.allocation_pct else "control"


def _learning_gate_reason() -> str | None:
    from aletheia.learning.control import learning_influence_gate_reason
    return learning_influence_gate_reason()


def _enforce_shadow_authority_provenance(
    *,
    path_id: str,
    experiment: LearningExperiment,
    authorized: bool,
    reason: str | None,
) -> tuple[bool, str]:
    """Keep every Shadow consumer on baseline and surface provenance drift.

    The authority resolver is the primary gate.  This final consumer-side
    invariant prevents a resolver regression or state race from granting Shadow
    behavior and makes any unexpected rejection reason immediately visible in
    the existing infra-event channel.  The normalized reason is also persisted
    on the immutable exposure, so the cohort can never silently admit it.
    """

    normalized_reason = str(reason or "authority_reason_unavailable")
    if experiment.stage != "shadow":
        return bool(authorized), normalized_reason
    if not authorized and normalized_reason == "experiment_stage_not_paper":
        return False, normalized_reason

    violation_reason = (
        f"shadow_authority_invariant_violation:{normalized_reason}"
    )
    _log.critical(
        "learning shadow authority invariant violated "
        "path=%s experiment=%s status=%s authorized=%s reason=%s",
        path_id,
        experiment.experiment_id,
        experiment.status,
        bool(authorized),
        normalized_reason,
    )
    try:
        from aletheia.infra.events import emit_infra_event

        emit_infra_event(
            severity="critical",
            source="learning_authority",
            code="shadow_authority_provenance_invalid",
            message=(
                "A Shadow Learning exposure reached the consumer with "
                "unexpected authority provenance and was forced to baseline."
            ),
            action="Inspect the experiment; do not admit or pool this cohort.",
            entity_id=experiment.experiment_id,
            metadata={
                "path_id": path_id,
                "experiment_stage": experiment.stage,
                "experiment_status": experiment.status,
                "authority_authorized": bool(authorized),
                "authority_reason": normalized_reason,
            },
            dedupe_sec=600,
        )
    except Exception:  # pragma: no cover - visibility must not weaken the gate
        _log.exception("failed to emit Shadow authority invariant event")
    return False, violation_reason


def _ensure_experiment(
    *, path_id: str, scope: dict, action: dict, source_cohort: str,
    target_horizon: str, consumer: str,
) -> tuple[LearningHypothesis, LearningExperiment | None]:
    """Resolve the hypothesis for this scope and its open experiment.

    Returns ``(hypothesis, None)`` when the hypothesis has been refrozen and has
    no open experiment: a refreeze is a durable decision that this line of
    enquiry is closed, so it must not be reopened.  Hypotheses are resolved by
    fingerprint, so without this the same refrozen hypothesis received a fresh
    experiment on the next scanner tick and the refreeze silently expired.
    """
    path = get_learning_path(path_id)
    fingerprint = hypothesis_fingerprint(
        path_id=path_id, scope=scope, action=action,
        target_horizon=target_horizon, source_cohort=source_cohort,
        behavior_contract_version=path.behavior_contract_version,
    )
    row = get_hypothesis_by_fingerprint(fingerprint)
    if row is None:
        hypothesis = LearningHypothesis(
            fingerprint=fingerprint,
            behavior_contract_version=path.behavior_contract_version,
            fingerprint_schema_version=HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION,
            cohort_id=hashlib.sha256(source_cohort.encode("utf-8")).hexdigest()[:16],
            path_id=path_id,
            overlap_group=path.overlap_group,
            source_type=source_cohort,
            scope_json=json.dumps(scope, sort_keys=True),
            action_json=json.dumps(action, sort_keys=True),
            consumer=consumer,
            target_horizon=target_horizon,
            status="shadow",
            harm_budget_json=json.dumps({
                "max_loss_rate_increase": 0.15,
                "max_mae_p95_worsening_pp": 0.25,
            }, sort_keys=True),
        )
        insert_hypothesis(hypothesis)
        row = get_hypothesis_by_fingerprint(fingerprint)
    if row is None:
        raise RuntimeError("learning hypothesis was not persisted")
    hypothesis = LearningHypothesis.model_validate(row)

    experiment_row = get_open_experiment(hypothesis.hypothesis_id)
    if experiment_row is None:
        if hypothesis.status in TERMINAL_HYPOTHESIS_STATUSES:
            _log.debug(
                "refusing new experiment on %s hypothesis %s (path=%s)",
                hypothesis.status, hypothesis.hypothesis_id, path_id,
            )
            return hypothesis, None
        experiment = LearningExperiment(
            hypothesis_id=hypothesis.hypothesis_id,
            assignment_salt=uuid4().hex,
            allocation_pct=0.5,
            min_samples_per_arm=50,
            min_market_days=7,
            promotion_gate_json=json.dumps({"prospective_only": True, "net_of_costs": True}),
            harm_gate_json="{}",
        )
        insert_experiment(experiment)
        experiment_row = get_open_experiment(hypothesis.hypothesis_id)
    if experiment_row is None:
        raise RuntimeError("learning experiment was not persisted")
    return hypothesis, LearningExperiment.model_validate(experiment_row)


def _verdict_authorizes(experiment: LearningExperiment, policy_epoch: str) -> bool:
    """Compatibility shim for callers; all authority lives in one resolver."""
    from aletheia.learning.authority import resolve_learning_authority
    hypothesis = get_record("learning_hypotheses", experiment.hypothesis_id)
    if hypothesis is None:
        return False
    return resolve_learning_authority(
        str(hypothesis.get("path_id") or ""), experiment, policy_epoch,
    ).authorized


def _code_fingerprint() -> str | None:
    """Commit the running process booted on (cached module global — no per-call
    subprocess, safe in the per-candidate scoring loop). None in tests / CLI where
    the boot commit was never captured."""
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit
        return get_boot_commit()
    except Exception:
        return None


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
            # The commit is still attributed on each ``learning_exposures``
            # row (see ``code_commit=_code_fingerprint()`` below) so build
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


def _policy_epoch(path_id: str) -> str:
    """Create/rotate the path epoch on a behavior-producing call."""
    from aletheia.learning.policy_epochs import persist_policy_epoch

    return persist_policy_epoch(_policy_epoch_definition(path_id))


def apply_numeric_treatment(
    *, path_id: str, scope: dict, action: dict, source_cohort: str,
    target_horizon: str, candidate_key: str | None, baseline: float,
    treatment: float, consumer: str, consumer_stage: str,
    outcome_candidate_key: str | None = None, opportunity_id: str | None = None,
    manifest: dict | None = None,
    assignment_key: str | None = None,
    expected_hypothesis_fingerprint: str | None = None,
    expected_experiment_id: str | None = None,
    suppress_shadow_if_paper_active: bool = False,
    exposed_at: str | None = None,
) -> InfluenceDecision:
    """Record prospective exposure and apply only an authorized paper treatment."""
    baseline_value = float(baseline)
    treatment_value = float(treatment)
    if not math.isfinite(baseline_value) or not math.isfinite(treatment_value):
        raise ValueError("numeric Learning treatment values must be finite")
    gate_reason = _learning_gate_reason()
    if gate_reason is not None:
        return InfluenceDecision(baseline_value, False, "disabled", gate_reason)
    path = get_learning_path(path_id)
    if not path.measurement_supported:
        return InfluenceDecision(
            baseline_value, False, "disabled", "measurement_unsupported"
        )
    if not candidate_key:
        return InfluenceDecision(baseline_value, False, "unassigned", "missing_candidate_key")

    if expected_hypothesis_fingerprint is not None or expected_experiment_id is not None:
        if not expected_hypothesis_fingerprint or not expected_experiment_id:
            return InfluenceDecision(
                baseline_value, False, "unassigned", "expected_paper_identity_incomplete"
            )
        path = get_learning_path(path_id)
        requested_fingerprint = hypothesis_fingerprint(
            path_id=path_id,
            scope=scope,
            action=action,
            target_horizon=target_horizon,
            source_cohort=source_cohort,
            behavior_contract_version=path.behavior_contract_version,
        )
        if requested_fingerprint != expected_hypothesis_fingerprint:
            return InfluenceDecision(
                baseline_value, False, "unassigned", "expected_paper_identity_mismatch"
            )
        hypothesis_row = get_hypothesis_by_fingerprint(
            expected_hypothesis_fingerprint
        )
        if hypothesis_row is None:
            return InfluenceDecision(
                baseline_value, False, "unassigned", "expected_paper_hypothesis_unavailable"
            )
        hypothesis = LearningHypothesis.model_validate(hypothesis_row)
        experiment_row = get_open_experiment(hypothesis.hypothesis_id)
        if (
            experiment_row is None
            or experiment_row.get("experiment_id") != expected_experiment_id
            or not str(experiment_row.get("stage") or "").startswith("paper_")
        ):
            return InfluenceDecision(
                baseline_value, False, "unassigned", "expected_paper_experiment_unavailable",
                hypothesis_id=hypothesis.hypothesis_id,
            )
        experiment = LearningExperiment.model_validate(experiment_row)
    else:
        if suppress_shadow_if_paper_active:
            from aletheia.learning.ledger import list_open_experiments
            from aletheia.storage.repositories import transaction

            # Serialize the path-level Paper check with hypothesis/experiment
            # creation. Admission cannot appear between these operations and
            # leave a second Shadow loop open beside it.
            with transaction(immediate=True):
                if any(
                    str(row.get("stage") or "")
                    in {"paper_10", "paper_25", "paper_50", "paper_active"}
                    for row in list_open_experiments(path_id=path_id)
                ):
                    return InfluenceDecision(
                        baseline_value,
                        False,
                        "unassigned",
                        "active_paper_identity_required",
                    )
                hypothesis, experiment = _ensure_experiment(
                    path_id=path_id,
                    scope=scope,
                    action=action,
                    source_cohort=source_cohort,
                    target_horizon=target_horizon,
                    consumer=consumer,
                )
        else:
            hypothesis, experiment = _ensure_experiment(
                path_id=path_id, scope=scope, action=action, source_cohort=source_cohort,
                target_horizon=target_horizon, consumer=consumer,
            )
    if experiment is None:
        # Terminal hypothesis with nothing open: record no exposure and apply
        # nothing. Accruing further samples here would silently restart a
        # completed lifecycle generation.
        return InfluenceDecision(
            baseline_value, False, "unassigned", f"hypothesis_{hypothesis.status}",
            hypothesis_id=hypothesis.hypothesis_id,
        )
    assignment = stable_assignment(experiment, assignment_key or candidate_key)
    assignment_unit_key = assignment_key or candidate_key
    policy_epoch = _policy_epoch(path_id)
    behavior_contract_hash = current_behavior_contract_hash(
        path_id=path_id,
        policy_epoch=policy_epoch,
        target_horizon=target_horizon,
        outcome_contract_version=path.outcome_contract_version,
        metric_version=path.metric_version,
    )
    from aletheia.learning.authority import resolve_learning_authority
    authority = resolve_learning_authority(path_id, experiment, policy_epoch)
    authority_authorized = authority.authorized
    authority_reason = authority.reason
    if not path.paper_authority_supported:
        authority_authorized = False
        authority_reason = (
            "experiment_stage_not_paper"
            if experiment.stage == "shadow"
            else "authority_unwired"
        )
    authorized, authority_reason = _enforce_shadow_authority_provenance(
        path_id=path_id,
        experiment=experiment,
        authorized=authority_authorized,
        reason=authority_reason,
    )
    # Permission provenance and realized behavior are intentionally distinct:
    # a treatment assignment can be authorized yet produce no numeric delta.
    authority_applied = authorized and assignment == "treatment"
    delta = treatment_value - baseline_value
    behavior_changed = (
        authority_applied
        and math.isfinite(delta)
        and delta != 0.0
    )
    value = treatment_value if behavior_changed else baseline_value
    behavior_change_reason = (
        "effective_treatment_delta"
        if behavior_changed
        else "no_effective_treatment_delta"
        if authority_applied
        else "control_assignment"
        if assignment == "control"
        else "authority_not_applied"
    )
    stage = experiment.stage if authorized else "shadow_refrozen"
    dedup_key = (
        f"{experiment.experiment_id}:{candidate_key}:{stage}:{policy_epoch}:"
        f"{behavior_contract_hash}"
    )
    exposure = LearningExposure(
        dedup_key=dedup_key,
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment=assignment,
        consumer=consumer,
        consumer_stage=f"{consumer_stage}:{stage}" if consumer_stage else stage,
        path_id=path_id,
        overlap_group=path.overlap_group,
        assignment_unit_key=assignment_unit_key,
        candidate_key=candidate_key,
        opportunity_id=opportunity_id,
        before_json=json.dumps({"value": baseline_value}, sort_keys=True),
        after_json=json.dumps({"value": value}, sort_keys=True),
        manifest_json=json.dumps({
            **(manifest or {}),
            "proposed_value": treatment_value,
            "authority_applied": authority_applied,
            "authority_reason": authority_reason,
            "behavior_changed": behavior_changed,
            "behavior_change_reason": behavior_change_reason,
        }, sort_keys=True),
        outcome_candidate_key=outcome_candidate_key,
        outcome_source="fixed_horizon_candidate" if outcome_candidate_key else None,
        outcome_contract_version=path.outcome_contract_version,
        experiment_stage=experiment.stage,
        concurrent_policy_epoch=policy_epoch,
        # P1-1: attribute the exposure to the running build so evidence produced by
        # different code versions is distinguishable (the fingerprint is also folded
        # into policy_epoch above, so a rebuild rotates the epoch on restart).
        code_commit=_code_fingerprint(),
        behavior_contract_hash=behavior_contract_hash,
        **({"exposed_at": exposed_at} if exposed_at is not None else {}),
    )
    inserted = insert_exposure(
        exposure,
        require_open_experiment_id=experiment.experiment_id,
        require_open_experiment_stage=experiment.stage,
    )
    if inserted is None:
        return InfluenceDecision(
            baseline_value,
            False,
            "unassigned",
            "experiment_closed_before_exposure",
            hypothesis.hypothesis_id,
            experiment.experiment_id,
        )
    if not inserted:
        stored = get_exposure_by_dedup_key(dedup_key)
        if stored:
            exposure = LearningExposure(**stored)
        else:
            return InfluenceDecision(
                baseline_value,
                False,
                "unassigned",
                "experiment_closed_before_exposure",
                hypothesis.hypothesis_id,
                experiment.experiment_id,
            )
    return InfluenceDecision(
        value, behavior_changed, assignment,
        (
            authority_reason
            if behavior_changed
            else f"baseline:{behavior_change_reason}"
            if authority_applied
            else f"baseline:{authority_reason}"
        ),
        hypothesis.hypothesis_id, experiment.experiment_id, exposure.exposure_id,
    )


def apply_boolean_treatment(
    *, path_id: str, scope: dict, action: dict, source_cohort: str,
    target_horizon: str, candidate_key: str | None, consumer: str,
    consumer_stage: str, outcome_candidate_key: str | None = None,
    opportunity_id: str | None = None, manifest: dict | None = None,
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
    )
