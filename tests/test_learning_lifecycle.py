"""AT-02 lifecycle, authority, and durable event regressions."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


_NOW = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)


def _iso(minutes: int = 0) -> str:
    return (_NOW + timedelta(minutes=minutes)).isoformat()


def _settings(
    *, automation: bool = False, paper: bool = False, paths: list[str] | None = None,
    auto_refreeze: bool = True,
):
    return SimpleNamespace(
        agent=SimpleNamespace(autonomous_learning=True),
        runtime=SimpleNamespace(paused=False),
        learning=SimpleNamespace(
            contribution_evaluation_enabled=True,
            lifecycle_automation_enabled=automation,
            paper_influence_enabled=paper,
            paper_influence_paths=paths or [],
            auto_refreeze=auto_refreeze,
            evaluation_interval_minutes=60,
            paper_active_allocation_pct=0.80,
        ),
    )


def _experiment(*, stage: str = "shadow", allocation: float = 0.5, status: str = "active"):
    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    token = f"{stage}-{allocation}-{status}".replace(".", "_")
    hypothesis = LearningHypothesis(
        hypothesis_id=f"hypothesis-{token}",
        fingerprint=f"lifecycle-fingerprint-{token}",
        cohort_id="universe-clean-net-v1",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="universe_forward_outcomes:clean_net:v2",
        scope_json='{"family":"momentum","direction":"long","market_type":"perp"}',
        action_json='{"type":"opportunity_multiplier","multiplier":1.1}',
        consumer="universe_allocator",
        target_horizon="4h",
        status="shadow",
    )
    assert insert_hypothesis(hypothesis)
    experiment = LearningExperiment(
        experiment_id=f"experiment-{token}",
        hypothesis_id=hypothesis.hypothesis_id,
        stage=stage,
        assignment_salt=f"salt-{token}",
        allocation_pct=allocation,
        status=status,
        started_at=_iso(-10),
        stage_generation=0,
    )
    assert insert_experiment(experiment)
    return hypothesis, experiment


def _measurement(
    hypothesis, experiment, verdict: str, *, stage: str | None = None,
    epoch: str = "epoch-a", horizon: str = "4h", contract: str = "v1",
    metric: str | None = None,
    reason: str = "test measurement",
    code_commit: str | None = "test-build",
    behavior_contract_hash: str | None = None,
    breached: bool = False,
    actual_intervention_treatment_n: int | None = None,
    authority_applied_treatment_n: int | None = None,
    assigned_treatment_n: int = 50,
    unique_treatment_n: int | None = None,
    unique_control_n: int = 50,
    harm_state: str | None = None,
    harm_indeterminate_reason: str | None = None,
    current_consecutive_market_days: int = 7,
    authority_rejection_reasons: dict[str, int] | None = None,
    created_at: str | None = None,
):
    from aletheia.learning.behavior_contracts import build_behavior_contract_hash
    from aletheia.learning.ledger import insert_record
    from aletheia.learning.models import LearningContributionVerdict
    from aletheia.learning.verdicts import METRIC_VERSION

    metric = metric or METRIC_VERSION
    behavior_contract_hash = behavior_contract_hash or build_behavior_contract_hash(
        path_id=hypothesis.path_id,
        policy_hash=epoch,
        target_horizon=horizon,
        outcome_contract_version=contract,
        metric_version=metric,
    )

    identifier = (
        f"verdict-{experiment.experiment_id}-{verdict}-{epoch}-{horizon}-{contract}"
        f"-{behavior_contract_hash}{'-breached' if breached else ''}"
    )
    actual_treatment_n = (
        0
        if reason in {
            "no_actual_intervention",
            "trial_integrity_ready_no_actual_intervention",
        }
        else assigned_treatment_n
        if actual_intervention_treatment_n is None
        else actual_intervention_treatment_n
    )
    authority_treatment_n = (
        actual_treatment_n
        if authority_applied_treatment_n is None
        else authority_applied_treatment_n
    )
    baseline_treatment_n = assigned_treatment_n - actual_treatment_n
    required_actual_n = max(1, int(getattr(experiment, "min_samples_per_arm", 50)))
    intervention_state = (
        "none"
        if actual_treatment_n == 0
        else "partial"
        if baseline_treatment_n
        else "complete"
    )
    resolved_harm_state = harm_state or ("breached" if breached else "clear")
    record = LearningContributionVerdict(
        verdict_id=identifier,
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage=stage or experiment.stage,
        window_start=_iso(-9),
        window_end=_iso(-1),
        unique_treatment_n=(
            assigned_treatment_n
            if unique_treatment_n is None else unique_treatment_n
        ),
        unique_control_n=unique_control_n,
        baseline=0.0,
        observed=0.5,
        delta=0.5,
        confidence_low=0.1,
        confidence_high=0.8,
        harm_metrics_json=json.dumps({
            "policy_epoch": epoch,
            "horizon": horizon,
            "outcome_contract_version": contract,
            "metric_version": metric,
            "assigned_treatment_n": assigned_treatment_n,
            "authority_applied_treatment_n": authority_treatment_n,
            "actual_intervention_treatment_n": actual_treatment_n,
            "baseline_treatment_n": baseline_treatment_n,
            "actual_intervention_state": intervention_state,
            "actual_intervention_reason": (
                "no_actual_intervention"
                if actual_treatment_n == 0
                else "insufficient_actual_intervention_samples"
                if actual_treatment_n < required_actual_n
                else "sufficient_actual_intervention_samples"
            ),
            "treatment_authority_rejection_reasons": (
                authority_rejection_reasons
                if authority_rejection_reasons is not None
                else {"test_authority_rejection": assigned_treatment_n - authority_treatment_n}
                if assigned_treatment_n > authority_treatment_n else {}
            ),
            "treatment_non_intervention_reasons": ({
                **(
                    {"authority:test_authority_rejection": assigned_treatment_n - authority_treatment_n}
                    if assigned_treatment_n > authority_treatment_n else {}
                ),
                **(
                    {"no_effective_treatment_delta": authority_treatment_n - actual_treatment_n}
                    if authority_treatment_n > actual_treatment_n else {}
                ),
            }
            ),
            "breached": breached,
            "harm_state": resolved_harm_state,
            "market_day_coverage": {
                "current_consecutive_market_days": current_consecutive_market_days,
            },
            **(
                {
                    "trial_integrity_readiness": {
                        "contract_version": "ufc-shadow-trial-integrity/v1",
                        "required_per_arm": min(
                            10,
                            max(1, int(experiment.min_samples_per_arm)),
                        ),
                        "required_market_days": min(
                            1,
                            max(0, int(experiment.min_market_days)),
                        ),
                        "qualified_treatment_n": assigned_treatment_n,
                        "qualified_control_n": unique_control_n,
                        "current_consecutive_market_days": (
                            current_consecutive_market_days
                        ),
                        "status": "ready",
                    },
                }
                if reason == "trial_integrity_ready_no_actual_intervention"
                else {}
            ),
            **(
                {"harm_indeterminate_reason": harm_indeterminate_reason}
                if harm_indeterminate_reason is not None else {}
            ),
            **({"breach_reasons": ["mae_p95_worsening"]} if breached else {}),
        }),
        verdict=verdict,
        reason=reason,
        created_at=created_at or _iso(),
        policy_epoch=epoch,
        target_horizon=horizon,
        outcome_contract_version=contract,
        metric_version=metric,
        code_commit=code_commit,
        behavior_contract_hash=behavior_contract_hash,
    )
    assert insert_record("learning_contribution_verdicts", record)
    return record


def _consume_confirmatory(
    hypothesis,
    experiment,
    monitoring,
    *,
    created_at: str | None = None,
):
    """Promote a unit-test measurement through the durable fixed-look seam."""
    from aletheia.learning.fixed_look import (
        consume_fixed_look_verdict,
        get_fixed_look_decision,
        register_fixed_look_decision,
    )
    from aletheia.storage.repositories import transaction
    from aletheia.storage.sqlite import get_db

    decision = get_fixed_look_decision(experiment.experiment_id)
    if decision is None:
        decision = register_fixed_look_decision(
            experiment,
            hypothesis,
            policy_epoch=str(monitoring.policy_epoch),
            behavior_contract_hash=str(monitoring.behavior_contract_hash),
        )
    if decision.status == "collecting":
        cutoff_json = json.dumps(
            {
                "assignment_unit_keys": ["test-treatment", "test-control"],
                "cutoff_exposed_at": monitoring.window_end,
                "cutoff_assignment_unit_key": "test-control",
                "selection_basis": "outcome_blind_exposure_prefix",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        cutoff_fingerprint = hashlib.sha256(cutoff_json.encode()).hexdigest()
        with transaction(immediate=True):
            get_db().execute(
                "UPDATE learning_inference_decisions SET status='cutoff_frozen', "
                "cutoff_json=?, cutoff_fingerprint=?, cutoff_frozen_at=?, updated_at=? "
                "WHERE experiment_id=? AND status='collecting'",
                (
                    cutoff_json,
                    cutoff_fingerprint,
                    _iso(),
                    _iso(),
                    experiment.experiment_id,
                ),
            )
        decision = get_fixed_look_decision(experiment.experiment_id)
    assert decision is not None and decision.status == "cutoff_frozen"
    confirmatory = monitoring.model_copy(update={
        "verdict_id": f"confirmatory-{monitoring.verdict_id}",
        **({"created_at": created_at} if created_at is not None else {}),
        "inference_contract_version": decision.inference_contract_version,
        "inference_role": "confirmatory",
        "authority_eligible": True,
        "inference_plan_hash": decision.plan_hash,
        "inference_cutoff_fingerprint": decision.cutoff_fingerprint,
    })
    assert consume_fixed_look_verdict(confirmatory)
    return confirmatory


def test_newer_confirmatory_cannot_mask_monitoring_invalid_safety(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    positive = _measurement(
        hypothesis,
        experiment,
        "positive",
        created_at=_iso(-2),
    )
    _consume_confirmatory(
        hypothesis,
        experiment,
        positive,
        created_at=_iso(1),
    )
    invalid = _measurement(
        hypothesis,
        experiment,
        "invalid",
        created_at=_iso(),
    )
    _configure(
        monkeypatch,
        _settings(automation=True, paper=True, paths=[hypothesis.path_id]),
    )
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.learning.lifecycle import process_lifecycle

    authority = resolve_learning_authority(
        hypothesis.path_id,
        experiment,
        "epoch-a",
    )
    transition = process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert authority.authorized is False
    assert authority.reason == "current_measurement_invalid"
    assert authority.current_verdict_id == invalid.verdict_id
    assert transition.action == "invalidate"
    assert transition.verdict_id == invalid.verdict_id


def _configure(monkeypatch, cfg):
    from aletheia.learning.behavior_contracts import build_behavior_contract_hash
    import aletheia.learning.authority as authority
    import aletheia.learning.lifecycle as lifecycle

    monkeypatch.setattr(authority, "load_config", lambda: cfg)
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    monkeypatch.setattr(authority, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(lifecycle, "global_learning_refreeze_reason", lambda: None)
    synthetic_contract = lambda **kwargs: build_behavior_contract_hash(
        path_id=kwargs["path_id"],
        policy_hash=kwargs["policy_epoch"],
        target_horizon=kwargs["target_horizon"],
        outcome_contract_version=kwargs["outcome_contract_version"],
        metric_version=kwargs["metric_version"],
    )
    monkeypatch.setattr(authority, "current_behavior_contract_hash", synthetic_contract)
    monkeypatch.setattr(lifecycle, "current_behavior_contract_hash", synthetic_contract)


@pytest.mark.parametrize(
    ("measurement", "action"),
    [
        ("positive", "promote_stage"),
        ("neutral", "hold"),
        ("insufficient_data", "hold"),
        ("negative", "refreeze"),
        ("invalid", "invalidate"),
    ],
)
def test_measurement_verdict_maps_to_distinct_lifecycle_action(measurement, action):
    from aletheia.learning.lifecycle import measurement_to_lifecycle_action

    assert measurement_to_lifecycle_action(measurement) == action
    assert measurement != action


def test_positive_with_automation_off_only_recommends_and_does_not_transition(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    verdict = _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings())
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.storage.sqlite import get_db

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.recommended_action == "promote_stage"
    assert result.action == "hold"
    assert result.successor_experiment_id is None
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0
    assert verdict.verdict == "positive"


def test_positive_verdict_with_insufficient_actual_interventions_cannot_promote(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    verdict = _measurement(
        hypothesis,
        experiment,
        "positive",
        actual_intervention_treatment_n=1,
        assigned_treatment_n=50,
    )
    _configure(monkeypatch, _settings(
        automation=True,
        paper=True,
        paths=[hypothesis.path_id],
    ))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.storage.sqlite import get_db

    result = process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert result.action == "hold"
    assert result.recommended_action == "promote_stage"
    assert result.reason == "insufficient_actual_intervention_samples"
    assert result.verdict_id == verdict.verdict_id
    assert get_db().execute(
        "SELECT COUNT(*) FROM learning_lifecycle_events"
    ).fetchone()[0] == 0


def test_positive_with_required_actual_interventions_can_promote_with_one_noop(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    verdict = _consume_confirmatory(hypothesis, experiment, _measurement(
        hypothesis,
        experiment,
        "positive",
        actual_intervention_treatment_n=50,
        authority_applied_treatment_n=51,
        assigned_treatment_n=51,
    ))
    _configure(monkeypatch, _settings(
        automation=True,
        paper=True,
        paths=[hypothesis.path_id],
    ))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.storage.sqlite import get_db

    result = process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert result.action == "promote_stage"
    assert result.recommended_action == "promote_stage"
    assert result.reason == "promoted_one_stage"
    assert result.verdict_id == verdict.verdict_id
    assert get_db().execute(
        "SELECT COUNT(*) FROM learning_lifecycle_events"
    ).fetchone()[0] == 1


def test_intervention_gate_rejects_mismatched_nonintervention_reason_counts():
    from aletheia.learning.authority import measurement_intervention_block_reason

    verdict = {
        "unique_treatment_n": 51,
        "harm_metrics_json": json.dumps({
            "assigned_treatment_n": 51,
            "authority_applied_treatment_n": 51,
            "actual_intervention_treatment_n": 50,
            "baseline_treatment_n": 1,
            "treatment_authority_rejection_reasons": {},
            "treatment_non_intervention_reasons": {},
        }),
    }

    assert measurement_intervention_block_reason(
        verdict,
        required_per_arm=50,
    ) == "treatment_intervention_count_mismatch"


def test_shadow_positive_never_auto_promotes_to_paper(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")
    old = get_record("learning_experiments", experiment.experiment_id)

    assert result.action == "hold"
    assert result.recommended_action == "admit_trial"
    assert result.reason == "shadow_requires_explicit_trial_admission"
    assert result.successor_experiment_id is None
    assert old["status"] == "active"
    assert old["ended_at"] is None
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0
    assert verdict.verdict == "positive"


def test_shadow_no_intervention_only_recommends_owner_trial_with_all_gates_enabled(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="no_actual_intervention",
    )
    _configure(monkeypatch, _settings(
        automation=True, paper=True, paths=[hypothesis.path_id],
    ))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    result = process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert result.action == "hold"
    assert result.recommended_action == "admit_trial"
    assert result.successor_experiment_id is None
    assert result.verdict_id == verdict.verdict_id
    assert get_record("learning_experiments", experiment.experiment_id)["status"] == "active"
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 1


def test_shadow_integrity_ready_only_recommends_owner_trial(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="trial_integrity_ready_no_actual_intervention",
        assigned_treatment_n=10,
        unique_control_n=10,
        harm_state="indeterminate",
        harm_indeterminate_reason="insufficient_samples",
        current_consecutive_market_days=1,
        authority_rejection_reasons={"experiment_stage_not_paper": 10},
    )
    _configure(monkeypatch, _settings(
        automation=True, paper=True, paths=[hypothesis.path_id],
    ))
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert result.action == "hold"
    assert result.recommended_action == "admit_trial"
    assert result.reason == "shadow_requires_explicit_trial_admission"
    assert result.verdict_id == verdict.verdict_id


def test_lifecycle_ignores_newer_stale_epoch_negative_measurement(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    current = _measurement(hypothesis, experiment, "positive", epoch="epoch-a")
    stale = _measurement(hypothesis, experiment, "negative", epoch="epoch-old")
    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "UPDATE learning_contribution_verdicts SET created_at=? WHERE verdict_id=?",
        (_iso(10), stale.verdict_id),
    )
    db.commit()
    _configure(monkeypatch, _settings())
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert result.verdict_id == current.verdict_id
    assert result.action == "hold"
    assert result.recommended_action == "promote_stage"


def test_lifecycle_consumes_an_older_build_verdict_when_behavior_matches(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    stale = _measurement(
        hypothesis,
        experiment,
        "negative",
        code_commit="old-build",
    )
    from aletheia.learning.ledger import insert_exposure
    from aletheia.learning.models import LearningExposure
    assert insert_exposure(LearningExposure(
        dedup_key="current-build-exposure",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment="control",
        consumer="universe_allocator",
        consumer_stage="opportunity_score:paper_10",
        path_id=hypothesis.path_id,
        overlap_group="universe",
        assignment_unit_key="run-current",
        candidate_key="current-candidate",
        outcome_candidate_key="current-candidate:4h",
        outcome_source="fixed_horizon_candidate",
        experiment_stage="paper_10",
        code_commit="current-build",
        concurrent_policy_epoch="epoch-a",
    ))
    _configure(monkeypatch, _settings())
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert result.verdict_id == stale.verdict_id
    assert result.action == "refreeze"
    assert result.reason == "negative_measurement_refreeze"


def test_lifecycle_holds_when_behavior_contract_is_unavailable(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(hypothesis, experiment, "negative")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    import aletheia.learning.lifecycle as lifecycle
    from aletheia.learning.ledger import insert_exposure
    from aletheia.learning.models import LearningExposure
    assert insert_exposure(LearningExposure(
        dedup_key="old-build-lifecycle-exposure",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment="control",
        consumer="universe_allocator",
        consumer_stage="opportunity_score:paper_10",
        path_id=hypothesis.path_id,
        overlap_group="universe",
        assignment_unit_key="old-run",
        candidate_key="old-candidate",
        outcome_candidate_key="old-candidate:4h",
        outcome_source="fixed_horizon_candidate",
        experiment_stage="paper_10",
        code_commit="persisted-old-build",
        concurrent_policy_epoch="epoch-a",
    ))
    monkeypatch.setattr(
        lifecycle,
        "current_behavior_contract_hash",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("missing contract")),
    )

    result = lifecycle.process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert result.action == "hold"
    assert result.reason == "behavior_contract_unavailable"


def test_lifecycle_uses_current_contract_verdict_not_newer_mismatched_row(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    current = _measurement(hypothesis, experiment, "positive", contract="v1")
    stale = _measurement(hypothesis, experiment, "negative", contract="v2")
    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "UPDATE learning_contribution_verdicts SET created_at=? WHERE verdict_id=?",
        (_iso(10), stale.verdict_id),
    )
    db.commit()
    _configure(monkeypatch, _settings())
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.verdict_id == current.verdict_id
    assert result.action == "hold"
    assert result.recommended_action == "promote_stage"


def test_wrong_hypothesis_negative_measurement_cannot_invalidate_experiment(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    foreign_hypothesis, _ = _experiment(allocation=0.6)
    verdict = _measurement(foreign_hypothesis, experiment, "negative")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.verdict_id == verdict.verdict_id
    assert result.action == "hold"
    assert result.reason == "measurement_hypothesis_mismatch"
    assert get_record("learning_experiments", experiment.experiment_id)["status"] == "active"


def test_fresh_successor_rejects_activation_verdict_from_previous_behavior(monkeypatch):
    hypothesis, _experiment_row, _activation, successor = _promoted_successor(
        monkeypatch,
        activation_behavior_contract_hash="old-behavior",
    )
    import aletheia.learning.authority as authority

    decision = authority.resolve_learning_authority(
        hypothesis.path_id,
        successor,
        "epoch-a",
    )

    assert decision.authorized is False
    assert decision.reason == "behavior_contract_mismatch"


def test_fresh_successor_stays_on_baseline_without_behavior_contract(monkeypatch):
    hypothesis, _experiment_row, _activation, successor = _promoted_successor(monkeypatch)
    import aletheia.learning.authority as authority
    from aletheia.learning.ledger import insert_exposure
    from aletheia.learning.models import LearningExposure
    assert insert_exposure(LearningExposure(
        dedup_key="old-build-authority-exposure",
        experiment_id=successor.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment="control",
        consumer="universe_allocator",
        consumer_stage="opportunity_score:paper_10",
        path_id=hypothesis.path_id,
        overlap_group="universe",
        assignment_unit_key="old-authority-run",
        candidate_key="old-authority-candidate",
        outcome_candidate_key="old-authority-candidate:4h",
        outcome_source="fixed_horizon_candidate",
        experiment_stage="paper_10",
        code_commit="persisted-old-build",
        concurrent_policy_epoch="epoch-a",
    ))
    monkeypatch.setattr(
        authority,
        "current_behavior_contract_hash",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("missing contract")),
    )

    decision = authority.resolve_learning_authority(
        hypothesis.path_id,
        successor,
        "epoch-a",
    )

    assert decision.authorized is False
    assert decision.reason == "behavior_contract_unavailable"


def test_lifecycle_pools_matching_behavior_across_builds_before_ordering_windows(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    old = _measurement(
        hypothesis,
        experiment,
        "positive",
        code_commit="old-build",
    )
    current = _measurement(
        hypothesis,
        experiment,
        "invalid",
        code_commit="test-build",
    )
    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "UPDATE learning_contribution_verdicts SET window_start=?, window_end=?, created_at=? "
        "WHERE verdict_id=?",
        (
            "1970-01-01T00:00:00+00:00",
            "1970-01-01T00:00:00+00:00",
            _iso(1),
            current.verdict_id,
        ),
    )
    db.execute(
        "UPDATE learning_contribution_verdicts SET window_end=? WHERE verdict_id=?",
        (_iso(20), old.verdict_id),
    )
    db.commit()
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.verdict_id == current.verdict_id
    assert result.action == "invalidate"


def test_lifecycle_prefers_the_newest_evaluation_when_a_cohort_empties(monkeypatch):
    """A sentinel window must not resurrect a superseded verdict from its own cohort.

    ``contribution_runner._window`` stamps 1970-01-01 on a cohort with no surviving
    samples, and that sentinel sorts *after* every real window under
    ``window_end DESC``.  Both verdicts here share one behavior cohort, so build
    filter cannot separate them and ordering alone decides: window ordering hands
    the controller a superseded ``positive`` to promote on while the newest
    evaluation of the same cohort says ``invalid``.
    """
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    superseded = _measurement(hypothesis, experiment, "positive")
    newest = _measurement(hypothesis, experiment, "invalid")
    from aletheia.storage.sqlite import get_db
    db = get_db()
    # The newest evaluation found nothing left to measure: sentinel window, no
    # samples, and a later created_at than the positive it supersedes.
    db.execute(
        "UPDATE learning_contribution_verdicts SET window_start=?, window_end=?, "
        "created_at=?, unique_treatment_n=0, unique_control_n=0 WHERE verdict_id=?",
        (
            "1970-01-01T00:00:00+00:00",
            "1970-01-01T00:00:00+00:00",
            _iso(30),
            newest.verdict_id,
        ),
    )
    db.execute(
        "UPDATE learning_contribution_verdicts SET created_at=? WHERE verdict_id=?",
        (_iso(0), superseded.verdict_id),
    )
    db.commit()
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.verdict_id == newest.verdict_id
    assert result.action == "invalidate"
    assert get_db().execute(
        "SELECT COUNT(*) FROM learning_lifecycle_events WHERE action='promote_stage'",
    ).fetchone()[0] == 0


@pytest.mark.parametrize("actor", ["learning_lifecycle_controller", "scheduler"])
def test_non_owner_identity_cannot_admit_shadow_trial(monkeypatch, actor):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="no_actual_intervention",
    )
    _configure(monkeypatch, _settings(
        automation=True, paper=True, paths=[hypothesis.path_id],
    ))
    from aletheia.learning.lifecycle import admit_shadow_trial
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    result = admit_shadow_trial(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        current_policy_epoch="epoch-a",
        admitted_by=actor,
        reason="automated admission attempt",
    )

    assert result.action == "hold"
    assert result.reason == "authenticated_owner_admission_unavailable"
    assert result.successor_experiment_id is None
    assert get_record("learning_experiments", experiment.experiment_id)["status"] == "active"
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 1


def test_owner_string_cannot_replace_an_authenticated_admission_boundary(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="no_actual_intervention",
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import admit_shadow_trial
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    first = admit_shadow_trial(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        current_policy_epoch="epoch-a",
        admitted_by="owner",
        reason="begin bounded paper trial",
    )
    second = admit_shadow_trial(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        current_policy_epoch="epoch-a",
        admitted_by="owner",
        reason="retry after response loss",
    )
    assert first.action == second.action == "hold"
    assert first.reason == second.reason == "authenticated_owner_admission_unavailable"
    assert first.successor_experiment_id is None
    predecessor = get_record("learning_experiments", experiment.experiment_id)
    assert predecessor["status"] == "active"
    assert predecessor["ended_at"] is None
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 1


def test_protected_trial_admission_rejects_stale_expected_epoch(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="no_actual_intervention",
    )
    _configure(monkeypatch, _settings(
        automation=True, paper=True, paths=[hypothesis.path_id],
    ))
    import aletheia.learning.lifecycle as lifecycle
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(lifecycle, "_policy_epoch", lambda _path_id: "epoch-current")
    result = lifecycle._execute_trial_admission(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-stale",
        reason="begin bounded paper trial",
    )

    assert result.action == "hold"
    assert result.reason == "policy_epoch_changed"
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("settings", "expected_reason"),
    [
        (_settings(automation=False, paper=True, paths=["universe_family_calibration"]),
         "lifecycle_automation_disabled"),
        (_settings(automation=True, paper=False, paths=["universe_family_calibration"]),
         "paper_influence_disabled"),
        (_settings(automation=True, paper=True, paths=[]), "path_not_allowlisted"),
        (
            SimpleNamespace(
                agent=SimpleNamespace(autonomous_learning=True),
                runtime=SimpleNamespace(paused=False),
                learning=SimpleNamespace(
                    contribution_evaluation_enabled=False,
                    lifecycle_automation_enabled=True,
                    paper_influence_enabled=True,
                    paper_influence_paths=["universe_family_calibration"],
                    auto_refreeze=True,
                    evaluation_interval_minutes=60,
                    paper_active_allocation_pct=0.80,
                ),
            ),
            "contribution_evaluation_disabled",
        ),
    ],
)
def test_protected_trial_admission_requires_every_paper_gate(
    monkeypatch,
    settings,
    expected_reason,
):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="no_actual_intervention",
    )
    _configure(monkeypatch, settings)
    import aletheia.learning.lifecycle as lifecycle
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(lifecycle, "_policy_epoch", lambda _path_id: "epoch-a")
    result = lifecycle._execute_trial_admission(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-a",
        reason="begin bounded paper trial",
    )

    assert result.action == "hold"
    assert result.reason == expected_reason
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0


def test_protected_trial_admission_is_idempotent_and_audits_owner(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="no_actual_intervention",
    )
    _configure(monkeypatch, _settings(
        automation=True, paper=True, paths=[hypothesis.path_id],
    ))
    import aletheia.learning.lifecycle as lifecycle
    from aletheia.storage.sqlite import get_db

    audits = []
    monkeypatch.setattr(lifecycle, "_policy_epoch", lambda _path_id: "epoch-a")
    monkeypatch.setattr(
        lifecycle,
        "_write_lifecycle_audit",
        lambda event, **identity: audits.append((event, identity)),
    )
    first = lifecycle._execute_trial_admission(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-a",
        reason="begin bounded paper trial",
    )
    second = lifecycle._execute_trial_admission(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-a",
        reason="retry after response loss",
    )

    assert first.action == second.action == "admit_trial"
    assert first.successor_experiment_id == second.successor_experiment_id
    assert second.reason == "already_transitioned"
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 1
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 2
    assert len(audits) == 1
    assert audits[0][1] == {}
    assert audits[0][0].audit_actor == "human"
    assert audits[0][0].audit_source == "dashboard"
    event = get_db().execute("SELECT * FROM learning_lifecycle_events").fetchone()
    assert "begin bounded paper trial" in event["reason"]


def test_protected_trial_admission_accepts_integrity_ready_shadow(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="trial_integrity_ready_no_actual_intervention",
        assigned_treatment_n=10,
        unique_control_n=10,
        harm_state="indeterminate",
        harm_indeterminate_reason="insufficient_samples",
        current_consecutive_market_days=1,
        authority_rejection_reasons={"experiment_stage_not_paper": 10},
    )
    _configure(monkeypatch, _settings(
        automation=True, paper=True, paths=[hypothesis.path_id],
    ))
    import aletheia.learning.lifecycle as lifecycle
    from aletheia.learning.ledger import get_record

    monkeypatch.setattr(lifecycle, "_policy_epoch", lambda _path_id: "epoch-a")
    result = lifecycle._execute_trial_admission(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-a",
        reason="begin bounded integrity-qualified paper trial",
    )

    assert result.action == "admit_trial"
    successor = get_record("learning_experiments", result.successor_experiment_id)
    assert successor["stage"] == "paper_10"
    assert successor["allocation_pct"] == pytest.approx(0.10)
    assert successor["min_samples_per_arm"] == 50
    assert successor["min_market_days"] == 7
    from aletheia.learning.authority import resolve_learning_authority

    authority = resolve_learning_authority(
        hypothesis.path_id,
        lifecycle.LearningExperiment.model_validate(successor),
        "epoch-a",
    )
    assert authority.authorized is True
    assert authority.reason == "admitted_shadow_trial"


def test_protected_trial_admission_rejects_undersized_integrity_verdict(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="trial_integrity_ready_no_actual_intervention",
        assigned_treatment_n=10,
        unique_control_n=9,
        harm_state="indeterminate",
        harm_indeterminate_reason="insufficient_samples",
        current_consecutive_market_days=1,
        authority_rejection_reasons={"experiment_stage_not_paper": 10},
    )
    _configure(monkeypatch, _settings(
        automation=True, paper=True, paths=[hypothesis.path_id],
    ))
    import aletheia.learning.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_policy_epoch", lambda _path_id: "epoch-a")
    result = lifecycle._execute_trial_admission(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-a",
        reason="attempt undersized trial",
    )

    assert result.action == "hold"
    assert result.reason == "trial_integrity_samples_insufficient"


def test_protected_trial_admission_rejects_missing_integrity_contract(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="trial_integrity_ready_no_actual_intervention",
        assigned_treatment_n=10,
        unique_control_n=10,
        harm_state="indeterminate",
        harm_indeterminate_reason="insufficient_samples",
        current_consecutive_market_days=1,
        authority_rejection_reasons={"experiment_stage_not_paper": 10},
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    metrics = json.loads(verdict.harm_metrics_json)
    metrics.pop("trial_integrity_readiness")
    db.execute(
        "UPDATE learning_contribution_verdicts SET harm_metrics_json=? "
        "WHERE verdict_id=?",
        (json.dumps(metrics), verdict.verdict_id),
    )
    db.commit()
    _configure(monkeypatch, _settings(
        automation=True, paper=True, paths=[hypothesis.path_id],
    ))
    import aletheia.learning.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_policy_epoch", lambda _path_id: "epoch-a")
    result = lifecycle._execute_trial_admission(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-a",
        reason="attempt malformed trial",
    )

    assert result.action == "hold"
    assert result.reason == "trial_integrity_contract_invalid"


def test_admission_retry_cannot_rebind_existing_verdict_to_another_experiment(
    monkeypatch,
):
    hypothesis, experiment = _experiment()
    other_hypothesis, other = _experiment(allocation=0.4)
    verdict = _measurement(
        hypothesis,
        experiment,
        "insufficient_data",
        reason="no_actual_intervention",
    )
    _configure(monkeypatch, _settings(
        automation=True,
        paper=True,
        paths=[hypothesis.path_id, other_hypothesis.path_id],
    ))
    import aletheia.learning.lifecycle as lifecycle
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(lifecycle, "_policy_epoch", lambda _path_id: "epoch-a")
    first = lifecycle._execute_trial_admission(
        experiment.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-a",
        reason="begin bounded paper trial",
    )
    rebound = lifecycle._execute_trial_admission(
        other.experiment_id,
        verdict_id=verdict.verdict_id,
        expected_policy_epoch="epoch-a",
        reason="attempt to rebind verdict",
    )

    assert first.action == "admit_trial"
    assert rebound.action == "hold"
    assert rebound.reason == "verdict_experiment_mismatch"
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 1
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 3


def test_successor_needs_its_own_measurement_before_it_can_promote_again(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _consume_confirmatory(
        hypothesis, experiment, _measurement(hypothesis, experiment, "positive")
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle

    successor_id = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a").successor_experiment_id
    result = process_lifecycle(successor_id, current_policy_epoch="epoch-a")

    assert result.action == "hold"
    assert result.reason == "no_current_stage_measurement"


def test_lifecycle_retry_is_idempotent_and_does_not_duplicate_successor(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _consume_confirmatory(
        hypothesis, experiment, _measurement(hypothesis, experiment, "positive")
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.storage.sqlite import get_db

    first = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")
    second = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert second.successor_experiment_id == first.successor_experiment_id
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 1
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 2


def test_failed_successor_insert_rolls_back_event_and_current_stage(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _consume_confirmatory(
        hypothesis, experiment, _measurement(hypothesis, experiment, "positive")
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    import aletheia.learning.lifecycle as lifecycle
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    def fail_insert(_successor):
        raise RuntimeError("simulated successor storage failure")

    monkeypatch.setattr(lifecycle, "insert_experiment", fail_insert)
    with pytest.raises(RuntimeError, match="simulated successor"):
        lifecycle.process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0
    persisted = get_record("learning_experiments", experiment.experiment_id)
    assert persisted["status"] == "active"
    assert persisted["ended_at"] is None


def test_positive_without_paper_influence_records_blocked_recommendation_not_authority(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _consume_confirmatory(
        hypothesis, experiment, _measurement(hypothesis, experiment, "positive")
    )
    _configure(monkeypatch, _settings(automation=True, paper=False, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.storage.sqlite import get_db

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")
    decision = resolve_learning_authority(hypothesis.path_id, experiment, "epoch-a")

    assert result.action == "hold"
    assert result.recommended_action == "promote_stage"
    assert decision.authorized is False
    assert decision.reason == "paper_influence_disabled"
    event = get_db().execute("SELECT * FROM learning_lifecycle_events").fetchone()
    assert event["action"] == "hold"
    assert "recommended_action=promote_stage" in event["reason"]


def test_empty_allowlist_and_default_config_always_resolve_to_baseline(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[]))
    from aletheia.learning.authority import resolve_learning_authority

    decision = resolve_learning_authority(hypothesis.path_id, experiment, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "path_not_allowlisted"


def _promoted_successor(
    monkeypatch,
    *,
    activation_code_commit: str = "test-build",
    activation_behavior_contract_hash: str | None = None,
):
    # Seed the post-authentication state directly, including the prospective
    # fixed-look plan that production admission writes in the same transaction.
    hypothesis, experiment = _experiment()
    activation = _measurement(
        hypothesis, experiment, "insufficient_data", reason="no_actual_intervention",
        code_commit=activation_code_commit,
        behavior_contract_hash=activation_behavior_contract_hash,
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.ledger import insert_experiment, insert_lifecycle_event
    from aletheia.learning.models import LearningExperiment, LearningLifecycleEvent
    from aletheia.storage.sqlite import get_db

    successor = LearningExperiment(
        hypothesis_id=hypothesis.hypothesis_id,
        stage="paper_10",
        assignment_salt="seeded-successor-salt",
        allocation_pct=0.1,
        status="active",
        parent_experiment_id=experiment.experiment_id,
        activation_verdict_id=activation.verdict_id,
        stage_generation=1,
        started_at=_iso(1),
    )
    event = LearningLifecycleEvent(
        dedup_key=f"{activation.verdict_id}:shadow:admit_trial",
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_id=experiment.experiment_id,
        successor_experiment_id=successor.experiment_id,
        verdict_id=activation.verdict_id,
        from_stage="shadow",
        to_stage="paper_10",
        action="admit_trial",
        reason="authenticated owner fixture",
        policy_epoch="epoch-a",
    )
    db = get_db()
    db.execute(
        "UPDATE learning_experiments SET status='closed', ended_at=? WHERE experiment_id=?",
        (_iso(1), experiment.experiment_id),
    )
    db.commit()
    assert insert_experiment(successor)
    assert insert_lifecycle_event(event)
    from aletheia.learning.behavior_contracts import build_behavior_contract_hash
    from aletheia.learning.fixed_look import register_fixed_look_decision
    from aletheia.learning.verdicts import METRIC_VERSION

    register_fixed_look_decision(
        successor,
        hypothesis,
        policy_epoch="epoch-a",
        behavior_contract_hash=build_behavior_contract_hash(
            path_id=hypothesis.path_id,
            policy_hash="epoch-a",
            target_horizon=hypothesis.target_horizon,
            outcome_contract_version="v1",
            metric_version=METRIC_VERSION,
        ),
    )
    return hypothesis, experiment, activation, successor


def test_owner_admitted_trial_keeps_authority_while_current_stage_is_underpowered(
    monkeypatch,
):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    current = _measurement(
        hypothesis,
        successor,
        "insufficient_data",
        reason="insufficient unique prospective treatment/control samples",
        assigned_treatment_n=1,
        actual_intervention_treatment_n=1,
        unique_control_n=1,
        harm_state="indeterminate",
        harm_indeterminate_reason="insufficient_samples",
    )
    from aletheia.learning.authority import resolve_learning_authority

    decision = resolve_learning_authority(hypothesis.path_id, successor, "epoch-a")

    assert decision.authorized is True
    assert decision.reason == "current_stage_measurement_underpowered"
    assert decision.current_verdict_id == current.verdict_id

    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "UPDATE learning_contribution_verdicts SET created_at=? WHERE verdict_id=?",
        (_iso(-1), current.verdict_id),
    )
    db.commit()
    complete = _consume_confirmatory(
        hypothesis, successor, _measurement(hypothesis, successor, "positive")
    )
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record

    transition = process_lifecycle(
        successor.experiment_id,
        current_policy_epoch="epoch-a",
    )
    promoted = get_record("learning_experiments", transition.successor_experiment_id)

    assert transition.action == "promote_stage"
    assert transition.verdict_id == complete.verdict_id
    assert promoted["stage"] == "paper_25"


def test_owner_admitted_trial_still_withdraws_immediately_on_harm_breach(monkeypatch):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    current = _measurement(
        hypothesis,
        successor,
        "negative",
        reason="harm gate breached: mae_p95_worsening",
        assigned_treatment_n=50,
        actual_intervention_treatment_n=50,
        breached=True,
    )
    from aletheia.learning.authority import resolve_learning_authority

    decision = resolve_learning_authority(hypothesis.path_id, successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "harm_gate_breached"
    assert decision.current_verdict_id == current.verdict_id


@pytest.mark.parametrize("verdict", ["negative", "invalid"])
def test_owner_admitted_trial_withdraws_on_negative_or_invalid(monkeypatch, verdict):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    current = _measurement(hypothesis, successor, verdict)
    from aletheia.learning.authority import resolve_learning_authority

    decision = resolve_learning_authority(hypothesis.path_id, successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == f"current_measurement_{verdict}"
    assert decision.current_verdict_id == current.verdict_id


def test_authority_rejects_activation_with_wrong_declared_horizon(monkeypatch):
    hypothesis, _parent, activation, successor = _promoted_successor(monkeypatch)
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.storage.sqlite import get_db

    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE learning_contribution_verdicts SET target_horizon='1h' "
        "WHERE verdict_id=?",
        (activation.verdict_id,),
    )
    if not was_in_tx:
        db.commit()

    from aletheia.learning.ledger import get_record
    from aletheia.learning.models import LearningExperiment
    persisted_successor = LearningExperiment(**get_record("learning_experiments", successor.experiment_id))
    decision = resolve_learning_authority(hypothesis.path_id, persisted_successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "horizon_mismatch"


def test_authority_rejects_activation_from_a_different_predecessor(monkeypatch):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    foreign_hypothesis, foreign_experiment = _experiment(allocation=0.6)
    foreign_activation = _measurement(foreign_hypothesis, foreign_experiment, "positive")
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.storage.sqlite import get_db

    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE learning_experiments SET activation_verdict_id=? WHERE experiment_id=?",
        (foreign_activation.verdict_id, successor.experiment_id),
    )
    if not was_in_tx:
        db.commit()

    from aletheia.learning.ledger import get_record
    from aletheia.learning.models import LearningExperiment
    persisted_successor = LearningExperiment(**get_record("learning_experiments", successor.experiment_id))
    decision = resolve_learning_authority(hypothesis.path_id, persisted_successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "activation_experiment_mismatch"


def test_authority_rejects_current_measurement_from_a_different_hypothesis(monkeypatch):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    foreign_hypothesis, _foreign_experiment = _experiment(allocation=0.6)
    _measurement(foreign_hypothesis, successor, "positive")
    from aletheia.learning.authority import resolve_learning_authority

    decision = resolve_learning_authority(hypothesis.path_id, successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "measurement_hypothesis_mismatch"


def test_authority_rejects_positive_current_measurement_with_insufficient_interventions(
    monkeypatch,
):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    _measurement(
        hypothesis,
        successor,
        "positive",
        actual_intervention_treatment_n=1,
        assigned_treatment_n=50,
    )
    from aletheia.learning.authority import resolve_learning_authority

    decision = resolve_learning_authority(hypothesis.path_id, successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "insufficient_actual_intervention_samples"


def test_authority_rejects_current_measurement_with_wrong_declared_horizon(monkeypatch):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    _measurement(hypothesis, successor, "positive", horizon="1h")
    from aletheia.learning.authority import resolve_learning_authority

    decision = resolve_learning_authority(hypothesis.path_id, successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "horizon_mismatch"


def test_authority_rejects_paper_stage_without_a_closed_predecessor(monkeypatch):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.learning.ledger import get_record

    before = get_record("learning_experiments", successor.experiment_id)
    decision = resolve_learning_authority(
        hypothesis.path_id,
        successor.model_copy(update={"parent_experiment_id": None}),
        "epoch-a",
    )
    after = get_record("learning_experiments", successor.experiment_id)

    assert decision.authorized is False
    assert decision.reason == "missing_predecessor_experiment"
    assert after == before


def test_authority_rejects_mismatched_hypothesis_path(monkeypatch):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.storage.sqlite import get_db

    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE learning_hypotheses SET path_id='ranker_factor_filter' WHERE hypothesis_id=?",
        (hypothesis.hypothesis_id,),
    )
    if not was_in_tx:
        db.commit()

    decision = resolve_learning_authority("universe_family_calibration", successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "hypothesis_path_mismatch"


def test_path_mismatch_cannot_fall_through_to_legacy_authority(monkeypatch):
    hypothesis, _parent, _activation, successor = _promoted_successor(monkeypatch)
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=["ranker_factor_filter"]))
    import aletheia.learning.authority as authority

    def legacy_must_not_run(*_args, **_kwargs):
        pytest.fail("a mismatched hypothesis path reached the legacy authority adapter")

    monkeypatch.setattr(authority, "_legacy_authority_adapter", legacy_must_not_run)
    decision = authority.resolve_learning_authority("ranker_factor_filter", successor, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "hypothesis_path_mismatch"


def test_retired_operator_path_cannot_recover_legacy_authority(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "UPDATE learning_hypotheses SET path_id='operator_learning_bundle' "
        "WHERE hypothesis_id=?",
        (hypothesis.hypothesis_id,),
    )
    db.commit()
    _configure(
        monkeypatch,
        _settings(automation=True, paper=True, paths=["operator_learning_bundle"]),
    )
    import aletheia.learning.authority as authority

    def legacy_must_not_run(*_args, **_kwargs):
        pytest.fail("the retired Operator path reached the legacy authority adapter")

    monkeypatch.setattr(authority, "_legacy_authority_adapter", legacy_must_not_run)
    decision = authority.resolve_learning_authority(
        "operator_learning_bundle", experiment, "epoch-a",
    )

    assert decision.authorized is False
    assert decision.reason == "measurement_unsupported"


def test_canonical_defaults_keep_unadmitted_shadow_consumers_on_static_baseline(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.learning.authority as authority
    import aletheia.learning.control as control
    import aletheia.learning.experiments as experiments

    cfg = AletheiaConfig()
    monkeypatch.setattr(authority, "load_config", lambda: cfg)
    monkeypatch.setattr(control, "load_config", lambda: cfg)
    monkeypatch.setattr(experiments, "load_config", lambda: cfg)
    monkeypatch.setattr(authority, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(control, "global_learning_refreeze_reason", lambda: None)

    decision = experiments.apply_numeric_treatment(
        path_id="universe_family_calibration",
        scope={"family": "momentum", "direction": "long", "market_type": "perp"},
        action={"type": "opportunity_multiplier", "multiplier": 1.1},
        source_cohort="universe_forward_outcomes:clean_net:v2", target_horizon="4h",
        candidate_key="candidate-default-closed", baseline=1.0, treatment=1.1,
        consumer="universe_allocator", consumer_stage="score",
    )

    assert cfg.learning.lifecycle_automation_enabled is True
    assert cfg.learning.paper_influence_enabled is True
    assert cfg.learning.paper_influence_paths == ["universe_family_calibration"]
    assert decision.value == 1.0
    assert decision.applied is False
    assert decision.reason == "baseline:experiment_stage_not_paper"


def test_refrozen_hypothesis_reopens_only_for_new_behavior_contract(monkeypatch):
    """A refreeze is durable: the next scanner tick must not reopen the enquiry.

    Regression (#442): ``_ensure_experiment`` resolved hypotheses by fingerprint and
    only asked whether an *experiment* was open, never whether the hypothesis was
    refrozen.  Invalidating an experiment therefore bought ~10 minutes -- the same
    hypothesis got a new experiment on the next tick and kept accruing evidence for
    a line of enquiry that had already been rejected.
    """
    from aletheia.config.schema import AletheiaConfig
    import aletheia.learning.authority as authority
    import aletheia.learning.control as control
    import aletheia.learning.experiments as experiments
    import aletheia.learning.lifecycle as lifecycle
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    cfg = AletheiaConfig()
    cfg.learning.auto_refreeze = True
    for module in (authority, control, experiments, lifecycle):
        monkeypatch.setattr(module, "load_config", lambda: cfg)
    monkeypatch.setattr(authority, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(control, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(lifecycle, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(experiments, "_code_fingerprint", lambda: "test-build")

    treatment = dict(
        path_id="universe_family_calibration",
        scope={"family": "momentum", "direction": "long", "market_type": "perp"},
        action={"type": "opportunity_multiplier", "multiplier": 1.1},
        source_cohort="universe_forward_outcomes:clean_net:v2",
        target_horizon="4h", baseline=1.0, treatment=1.1,
        consumer="universe_allocator", consumer_stage="score",
    )
    first = experiments.apply_numeric_treatment(
        candidate_key="candidate-before-refreeze", **treatment
    )
    assert first.experiment_id is not None

    db = get_db()
    first_exposure = get_record("learning_exposures", first.exposure_id)
    epoch = first_exposure["concurrent_policy_epoch"]
    experiment = SimpleNamespace(
        experiment_id=first.experiment_id, stage="shadow",
    )
    hypothesis = SimpleNamespace(
        hypothesis_id=first.hypothesis_id,
        path_id="universe_family_calibration",
    )
    _measurement(
        hypothesis,
        experiment,
        "invalid",
        epoch=epoch,
        behavior_contract_hash=first_exposure["behavior_contract_hash"],
    )

    result = lifecycle.process_lifecycle(
        first.experiment_id, current_policy_epoch=epoch
    )
    assert result.action == "invalidate"
    assert get_record("learning_hypotheses", first.hypothesis_id)["status"] == "refrozen"

    # The next tick on the identical scope resolves the same refrozen hypothesis.
    second = experiments.apply_numeric_treatment(
        candidate_key="candidate-after-refreeze", **treatment
    )

    assert second.hypothesis_id == first.hypothesis_id
    assert second.experiment_id is None
    assert second.applied is False
    assert second.value == 1.0
    assert second.reason == "hypothesis_refrozen"
    assert second.exposure_id is None
    assert db.execute(
        "SELECT COUNT(*) FROM learning_experiments WHERE hypothesis_id=?",
        (first.hypothesis_id,),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM learning_exposures WHERE hypothesis_id=?",
        (first.hypothesis_id,),
    ).fetchone()[0] == 1

    # A behavior-contract change is a distinct line of enquiry, not a retry of
    # the refrozen hypothesis. It starts a fresh shadow experiment at baseline.
    from dataclasses import replace

    current_path = experiments.get_learning_path("universe_family_calibration")
    successor_path = replace(
        current_path,
        behavior_contract_version="universe-family-calibration/successor-test",
    )
    monkeypatch.setattr(experiments, "get_learning_path", lambda _path_id: successor_path)

    successor = experiments.apply_numeric_treatment(
        candidate_key="candidate-after-contract-change", **treatment
    )

    assert successor.hypothesis_id != first.hypothesis_id
    assert successor.experiment_id is not None
    assert successor.exposure_id is not None
    assert successor.applied is False
    assert successor.value == 1.0
    assert successor.reason == "baseline:experiment_stage_not_paper"
    assert get_record("learning_hypotheses", successor.hypothesis_id)["status"] == "shadow"
    assert db.execute(
        "SELECT COUNT(*) FROM learning_experiments WHERE hypothesis_id=?",
        (successor.hypothesis_id,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize("measurement", ["neutral", "insufficient_data"])
def test_neutral_and_insufficient_hold_stage(monkeypatch, measurement):
    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, measurement)
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.action == "hold"
    assert result.recommended_action == "hold"
    assert result.successor_experiment_id is None


@pytest.mark.parametrize("measurement", ["negative", "invalid"])
def test_negative_and_invalid_fail_closed_in_authority_and_lifecycle(monkeypatch, measurement):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(hypothesis, experiment, measurement)
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record

    before_controller = resolve_learning_authority(hypothesis.path_id, experiment, "epoch-a")
    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")
    persisted = get_record("learning_experiments", experiment.experiment_id)

    assert before_controller.authorized is False
    assert before_controller.reason == f"current_measurement_{measurement}"
    assert result.action == ("invalidate" if measurement == "invalid" else "refreeze")
    assert persisted["status"] == ("invalid" if measurement == "invalid" else "refrozen")
    assert persisted["ended_at"] is not None


@pytest.mark.parametrize("measurement", ["negative", "invalid"])
def test_harm_breach_does_not_suppress_the_safety_action_it_is_evidence_for(
    monkeypatch, measurement,
):
    """A breached harm gate must never hold the refreeze/invalidate it argues for.

    Regression (#441): the harm check lived inside ``measurement_identity_reason``,
    which ``process_lifecycle`` consults *before* the safety branch.  A breaching
    experiment was therefore held on every controller pass while an otherwise
    identical clean one was reaped -- the gate was inverted, exempting the worst
    experiments from being stopped at all.
    """
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    verdict = _measurement(hypothesis, experiment, measurement, breached=True)
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")
    persisted = get_record("learning_experiments", experiment.experiment_id)

    assert result.action == ("invalidate" if measurement == "invalid" else "refreeze")
    assert result.reason != "harm_gate_breached"
    assert persisted["status"] == ("invalid" if measurement == "invalid" else "refrozen")
    assert persisted["ended_at"] is not None
    assert get_db().execute(
        "SELECT COUNT(*) FROM learning_lifecycle_events WHERE experiment_id=? AND verdict_id=?",
        (experiment.experiment_id, verdict.verdict_id),
    ).fetchone()[0] == 1


def test_harm_breach_blocks_authority_and_refreezes_instead_of_promoting(monkeypatch):
    """A determinate breach blocks the grant and drives the conservative withdrawal."""
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(hypothesis, experiment, "positive", breached=True)
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    decision = resolve_learning_authority(hypothesis.path_id, experiment, "epoch-a")
    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")
    persisted = get_record("learning_experiments", experiment.experiment_id)

    assert decision.authorized is False
    assert decision.reason == "harm_gate_breached"
    assert result.action == "refreeze"
    assert result.recommended_action == "refreeze"
    assert result.reason == "negative_measurement_refreeze"
    assert persisted["stage"] == "paper_10"
    assert persisted["ended_at"] is not None
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 1


@pytest.mark.parametrize("measurement", ["negative", "invalid"])
def test_context_stale_safety_measurement_can_withdraw_authority(monkeypatch, measurement):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(hypothesis, experiment, measurement, epoch="stale-epoch")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.action == ("invalidate" if measurement == "invalid" else "refreeze")
    assert result.reason == "stale_safety_measurement"
    assert get_record("learning_experiments", experiment.experiment_id)["ended_at"] is not None


def test_context_stale_harm_breach_can_withdraw_authority(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(hypothesis, experiment, "positive", epoch="stale-epoch", breached=True)
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.action == "refreeze"
    assert result.recommended_action == "refreeze"
    assert result.reason == "stale_safety_measurement"


def test_wrong_stage_stale_negative_cannot_mutate_current_experiment(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(
        hypothesis,
        experiment,
        "negative",
        stage="shadow",
        epoch="stale-epoch",
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.action == "hold"
    assert result.reason == "stage_mismatch"
    assert get_record("learning_experiments", experiment.experiment_id)["ended_at"] is None


def test_malformed_stale_negative_cannot_mutate_current_experiment(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    verdict = _measurement(
        hypothesis,
        experiment,
        "negative",
        epoch="stale-epoch",
    )
    from aletheia.storage.sqlite import get_db
    get_db().execute(
        "UPDATE learning_contribution_verdicts SET harm_metrics_json='{not json' "
        "WHERE verdict_id=?",
        (verdict.verdict_id,),
    )
    get_db().commit()
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.action == "hold"
    assert result.reason == "measurement_metadata_invalid"
    assert get_record("learning_experiments", experiment.experiment_id)["ended_at"] is None


@pytest.mark.parametrize("missing_field", ["policy_epoch", "behavior_contract_hash"])
def test_missing_identity_stale_negative_cannot_mutate_current_experiment(
    monkeypatch,
    missing_field,
):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    verdict = _measurement(
        hypothesis,
        experiment,
        "negative",
        epoch="stale-epoch",
    )
    from aletheia.storage.sqlite import get_db
    metrics = {
        "harm_state": "clear",
        "breached": False,
        "horizon": "4h",
        "outcome_contract_version": "v1",
        "metric_version": verdict.metric_version,
    }
    if missing_field != "policy_epoch":
        metrics["policy_epoch"] = "stale-epoch"
    get_db().execute(
        f"UPDATE learning_contribution_verdicts SET {missing_field}=NULL, "
        "harm_metrics_json=? WHERE verdict_id=?",
        (json.dumps(metrics), verdict.verdict_id),
    )
    get_db().commit()
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.action == "hold"
    assert result.reason == "measurement_metadata_invalid"
    assert get_record("learning_experiments", experiment.experiment_id)["ended_at"] is None


def test_indeterminate_harm_blocks_authority_without_claiming_breach():
    from aletheia.learning.authority import measurement_harm_block_reason

    verdict = {
        "harm_metrics_json": json.dumps({
            "harm_state": "indeterminate",
            "harm_indeterminate_reason": "insufficient_samples",
            "breached": False,
            "breach_reasons": [],
        }),
    }

    assert measurement_harm_block_reason(verdict) == "harm_gate_indeterminate"


def test_measurement_identity_reason_is_identity_only(monkeypatch):
    """Identity means 'this measurement describes something else', not 'it was bad'."""
    del monkeypatch
    import json as _json

    from aletheia.learning.authority import (
        measurement_harm_block_reason,
        measurement_identity_reason,
    )
    from aletheia.learning.verdicts import METRIC_VERSION

    verdict = {
        "stage": "paper_10",
        "policy_epoch": "epoch-a",
        "hypothesis_id": "hypothesis-a",
        "target_horizon": "4h",
        "outcome_contract_version": "v1",
        "metric_version": METRIC_VERSION,
        "code_commit": "test-build",
        "behavior_contract_hash": "behavior-current",
        "harm_metrics_json": _json.dumps({"harm_state": "breached", "breached": True}),
    }
    kwargs = dict(
        expected_horizon="4h",
        expected_behavior_contract_hash="behavior-current",
        expected_hypothesis_id="hypothesis-a",
    )

    assert measurement_identity_reason(verdict, "paper_10", "epoch-a", **kwargs) is None
    assert measurement_harm_block_reason(verdict) == "harm_gate_breached"

    clean = {**verdict, "harm_metrics_json": _json.dumps({"harm_state": "clear", "breached": False})}
    assert measurement_harm_block_reason(clean) is None
    assert measurement_harm_block_reason({"harm_metrics_json": "{not json"}) == (
        "measurement_metadata_invalid"
    )


@pytest.mark.parametrize(
    ("epoch", "horizon", "contract", "metric", "reason"),
    [
        ("stale", "4h", "v1", "universe_family_calibration_net_return_v1", "stale_policy_epoch"),
        ("epoch-a", "1h", "v1", "universe_family_calibration_net_return_v1", "horizon_mismatch"),
        ("epoch-a", "4h", "unknown", "universe_family_calibration_net_return_v1", "outcome_contract_mismatch"),
        (
            "epoch-a",
            "4h",
            "v1",
            "universe_family_calibration_cluster_allocation_utility_v4",
            "metric_version_mismatch",
        ),
    ],
)
def test_positive_with_stale_or_mismatched_cohort_cannot_promote(monkeypatch, epoch, horizon, contract, metric, reason):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(hypothesis, experiment, "positive", epoch=epoch, horizon=horizon, contract=contract, metric=metric)
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.action == "hold"
    assert result.reason == reason


def test_global_reconciliation_refreeze_overrides_every_authority_gate(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _consume_confirmatory(
        hypothesis, experiment, _measurement(hypothesis, experiment, "positive")
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    import aletheia.learning.authority as authority
    from aletheia.learning.authority import resolve_learning_authority

    monkeypatch.setattr(authority, "global_learning_refreeze_reason", lambda: "reconciliation_integrity_incident:test")
    decision = resolve_learning_authority(hypothesis.path_id, experiment, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "reconciliation_integrity_incident"


def test_paper_active_keeps_a_control_holdout_and_never_allocation_above_ninety(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_active", allocation=0.95)
    _consume_confirmatory(
        hypothesis, experiment, _measurement(hypothesis, experiment, "positive")
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.authority import resolve_learning_authority

    decision = resolve_learning_authority(hypothesis.path_id, experiment, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "allocation_without_control_holdout"


def test_read_only_inspection_reports_lifecycle_recommendation_and_authority(monkeypatch):
    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings())
    from aletheia.learning.lifecycle import inspect_learning_experiment

    inspection = inspect_learning_experiment(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert inspection["path_id"] == hypothesis.path_id
    assert inspection["current_stage"] == "shadow"
    assert inspection["policy_epoch"] == "epoch-a"
    assert inspection["target_horizon"] == "4h"
    assert inspection["latest_measurement_verdict"] == "positive"
    assert inspection["recommended_lifecycle_action"] == "admit_trial"
    assert inspection["authority_enabled"] is False
    assert inspection["readiness"]["ready_for_paper_influence"] is False
    assert inspection["treatment_control_progress"]["required_per_arm"] == experiment.min_samples_per_arm
    assert "next_evaluation_at" in inspection


def test_read_only_inspection_does_not_report_stale_epoch_verdict_as_current(monkeypatch):
    hypothesis, experiment = _experiment()
    current = _measurement(hypothesis, experiment, "positive", epoch="epoch-a")
    stale = _measurement(hypothesis, experiment, "negative", epoch="epoch-old")
    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "UPDATE learning_contribution_verdicts SET created_at=? WHERE verdict_id=?",
        (_iso(10), stale.verdict_id),
    )
    db.commit()
    _configure(monkeypatch, _settings())
    from aletheia.learning.lifecycle import inspect_learning_experiment

    inspection = inspect_learning_experiment(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert inspection["latest_measurement_verdict"] == current.verdict
    assert inspection["recommended_lifecycle_action"] == "admit_trial"


def test_read_only_inspection_reports_no_progress_without_behavior_contract(monkeypatch):
    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings())
    import aletheia.learning.lifecycle as lifecycle
    monkeypatch.setattr(
        lifecycle,
        "current_behavior_contract_hash",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("missing contract")),
    )

    inspection = lifecycle.inspect_learning_experiment(
        experiment.experiment_id,
        current_policy_epoch="epoch-a",
    )

    assert inspection["latest_measurement_verdict"] is None
    assert inspection["authority_enabled"] is False
    assert inspection["treatment_control_progress"]["treatment"] == 0
    assert inspection["treatment_control_progress"]["control"] == 0
    assert inspection["treatment_control_progress"]["cohorts"] == []


@pytest.mark.real_db
def test_concurrent_promotion_attempts_create_one_durable_successor(tmp_path, monkeypatch):
    """Two scheduler workers racing the same verdict remain deduplicated."""
    from concurrent.futures import ThreadPoolExecutor
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    sqlite._db_conn = None
    run_migrations()

    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _consume_confirmatory(
        hypothesis, experiment, _measurement(hypothesis, experiment, "positive")
    )
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.storage.sqlite import get_db

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _unused: process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a"),
            range(2),
        ))

    successor_ids = {result.successor_experiment_id for result in results}
    assert len(successor_ids) == 1
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 1
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 2


def test_contribution_evaluator_isolates_a_failing_experiment(monkeypatch):
    """One dirty experiment row must not abort the whole scheduled cycle (issue #160).

    The previous ``sum(evaluate_experiment(...) for row in rows)`` let the first
    failing experiment propagate out, mark the job failed, and skip every
    remaining experiment — and the next cycle crashed on the same first row,
    starving verdict production permanently.
    """
    from aletheia.learning import lifecycle
    from aletheia.learning import contribution_runner

    _good_hyp, good_exp = _experiment(stage="paper_10", allocation=0.10)
    _bad_hyp, bad_exp = _experiment(stage="paper_10", allocation=0.25)

    monkeypatch.setattr(lifecycle, "load_config", lambda: _settings())

    calls: list[str] = []

    def fake_evaluate(experiment_id):
        calls.append(experiment_id)
        if experiment_id == bad_exp.experiment_id:
            raise RuntimeError("dirty hypothesis row")
        return ["verdict"]

    monkeypatch.setattr(contribution_runner, "evaluate_experiment", fake_evaluate)

    result = lifecycle.run_contribution_evaluator()

    assert result["status"] == "degraded"
    assert result["failed_experiments"] == 1
    # Both were attempted; the good one still evaluated despite the bad one raising.
    assert good_exp.experiment_id in calls
    assert bad_exp.experiment_id in calls
    assert result["verdicts"] >= 1

    from aletheia.learning.control import get_learning_job_health

    health = get_learning_job_health("learning_contribution_evaluator")
    assert health.status == "degraded"
    assert health.consecutive_failures == 1
    assert health.last_failure_reason == "experiment_evaluation_failed:1"
    assert health.input_watermark is None


def test_lifecycle_controller_isolates_a_failing_experiment(monkeypatch):
    """The controller loop had no exception guard — one raise aborted the cycle (issue #160)."""
    from aletheia.learning import lifecycle
    from aletheia.learning.lifecycle import LifecycleResult

    _good_hyp, good_exp = _experiment(stage="paper_10", allocation=0.10)
    _bad_hyp, bad_exp = _experiment(stage="paper_10", allocation=0.25)

    seen: list[str] = []

    def fake_process(experiment_id, *, current_policy_epoch):
        seen.append(experiment_id)
        if experiment_id == bad_exp.experiment_id:
            raise RuntimeError("boom")
        return LifecycleResult("hold", "hold", "test", experiment_id)

    monkeypatch.setattr(lifecycle, "process_lifecycle", fake_process)

    result = lifecycle.run_lifecycle_controller()

    assert result["failed_experiments"] == 1
    assert good_exp.experiment_id in seen
    assert bad_exp.experiment_id in seen
    # Only the good experiment produced a lifecycle result.
    assert result["experiments"] == 1


def test_lifecycle_audit_failure_is_durable_and_reconciles(monkeypatch):
    from aletheia.files import jsonl
    from aletheia.learning import lifecycle
    from aletheia.learning.control import get_learning_job_health
    from aletheia.learning.ledger import get_record, insert_lifecycle_event
    from aletheia.learning.models import LearningLifecycleEvent

    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.10)
    event = LearningLifecycleEvent(
        dedup_key="audit-failure:test",
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_id=experiment.experiment_id,
        from_stage="paper_10",
        action="refreeze",
        reason="negative_measurement_refreeze",
        policy_epoch="epoch-test",
        audit_actor="human",
        audit_source="dashboard",
    )
    assert insert_lifecycle_event(event)

    monkeypatch.setattr(jsonl, "write_audit", lambda *_args, **_kwargs: False)
    assert lifecycle._write_lifecycle_audit(event) is False

    failed = get_record("learning_lifecycle_events", event.event_id)
    assert failed["audit_mirror_status"] == "failed"
    assert failed["audit_mirrored_at"] is None
    assert failed["audit_failure_reason"] == "jsonl_append_failed"
    degraded = get_learning_job_health("learning_lifecycle_audit_reconciler")
    assert degraded.status == "degraded"
    assert degraded.last_failure_reason == "audit_append_failed:1;pending:1"
    assert degraded.backlog_count == 1
    assert degraded.next_due_at is not None

    calls = []

    def succeed(payloads):
        calls.extend(payloads)
        return {payload["id"] for payload in payloads}

    monkeypatch.setattr(jsonl, "write_audits_deduplicated", succeed)
    result = lifecycle.reconcile_lifecycle_audits()

    assert result == {"attempted": 1, "mirrored": 1, "failed": 0, "pending": 0}
    mirrored = get_record("learning_lifecycle_events", event.event_id)
    assert mirrored["audit_mirror_status"] == "mirrored"
    assert mirrored["audit_mirrored_at"] is not None
    assert mirrored["audit_failure_reason"] is None
    from aletheia.learning.ledger import update_lifecycle_audit_mirror

    update_lifecycle_audit_mirror(
        event.event_id,
        status="failed",
        mirrored_at=None,
        failure_reason="late_failed_ack",
    )
    monotonic = get_record("learning_lifecycle_events", event.event_id)
    assert monotonic["audit_mirror_status"] == "mirrored"
    assert monotonic["audit_mirrored_at"] == mirrored["audit_mirrored_at"]
    assert calls[0]["actor"] == "human"
    assert calls[0]["source"] == "dashboard"
    assert calls[0]["id"] == f"learning-lifecycle:{event.event_id}"
    healthy = get_learning_job_health("learning_lifecycle_audit_reconciler")
    assert healthy.status == "healthy"
    assert healthy.backlog_count == 0
    assert healthy.last_failure_reason == "audit_append_failed:1;pending:1"


def test_lifecycle_audit_repairs_partial_write_after_status_changes(
    tmp_path, monkeypatch,
):
    from aletheia.files import jsonl
    from aletheia.learning import lifecycle
    from aletheia.learning.ledger import get_record, insert_lifecycle_event
    from aletheia.learning.models import LearningLifecycleEvent

    audit_tmp = tmp_path / "logs"
    audit_tmp.mkdir()
    monkeypatch.setattr(jsonl, "_log_dir", lambda: audit_tmp)
    monkeypatch.setattr(jsonl, "_last_audit_hash", None)

    event = LearningLifecycleEvent(
        dedup_key="audit-partial:status-change",
        hypothesis_id="hypothesis-partial",
        experiment_id="experiment-partial",
        from_stage="shadow",
        action="hold",
        reason="test_partial_write",
    )
    assert insert_lifecycle_event(event)
    real_append = jsonl._append_jsonl_line

    def partial_append(path, line):
        with path.open("ab") as stream:
            stream.write(line[: len(line) // 2].encode("utf-8"))
        raise OSError("disk full after partial lifecycle write")

    monkeypatch.setattr(jsonl, "_append_jsonl_line", partial_append)
    assert lifecycle._write_lifecycle_audit(event) is False
    assert get_record(
        "learning_lifecycle_events", event.event_id,
    )["audit_mirror_status"] == "failed"

    monkeypatch.setattr(jsonl, "_append_jsonl_line", real_append)
    assert lifecycle.reconcile_lifecycle_audits() == {
        "attempted": 1,
        "mirrored": 1,
        "failed": 0,
        "pending": 0,
    }
    records = [
        json.loads(line)
        for line in (audit_tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["id"] for record in records] == [
        f"learning-lifecycle:{event.event_id}"
    ]
