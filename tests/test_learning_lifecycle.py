"""AT-02 lifecycle, authority, and durable event regressions."""

from __future__ import annotations

import json
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
    metric: str = "universe_family_calibration_net_return_v1",
):
    from aletheia.learning.ledger import insert_record
    from aletheia.learning.models import LearningContributionVerdict

    identifier = f"verdict-{experiment.experiment_id}-{verdict}-{epoch}-{horizon}-{contract}"
    record = LearningContributionVerdict(
        verdict_id=identifier,
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage=stage or experiment.stage,
        window_start=_iso(-9),
        window_end=_iso(-1),
        unique_treatment_n=50,
        unique_control_n=50,
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
            "breached": False,
        }),
        verdict=verdict,
        reason="test measurement",
        created_at=_iso(),
    )
    assert insert_record("learning_contribution_verdicts", record)
    return record


def _configure(monkeypatch, cfg):
    import aletheia.learning.authority as authority
    import aletheia.learning.lifecycle as lifecycle

    monkeypatch.setattr(authority, "load_config", lambda: cfg)
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    monkeypatch.setattr(authority, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(lifecycle, "global_learning_refreeze_reason", lambda: None)


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
    hypothesis, experiment = _experiment()
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


def test_shadow_positive_promotes_one_stage_with_fresh_successor_and_event(monkeypatch):
    hypothesis, experiment = _experiment()
    verdict = _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")
    old = get_record("learning_experiments", experiment.experiment_id)
    successor = get_record("learning_experiments", result.successor_experiment_id)
    event = get_db().execute("SELECT * FROM learning_lifecycle_events").fetchone()

    assert result.action == "promote_stage"
    assert old["status"] == "closed"
    assert old["ended_at"] is not None
    assert successor["stage"] == "paper_10"
    assert successor["parent_experiment_id"] == experiment.experiment_id
    assert successor["activation_verdict_id"] == verdict.verdict_id
    assert successor["assignment_salt"] != experiment.assignment_salt
    assert successor["stage_generation"] == 1
    assert event["dedup_key"] == f"{verdict.verdict_id}:shadow:promote_stage"
    assert event["action"] == "promote_stage"


def test_successor_needs_its_own_measurement_before_it_can_promote_again(monkeypatch):
    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle

    successor_id = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a").successor_experiment_id
    result = process_lifecycle(successor_id, current_policy_epoch="epoch-a")

    assert result.action == "hold"
    assert result.reason == "no_current_stage_measurement"


def test_lifecycle_retry_is_idempotent_and_does_not_duplicate_successor(monkeypatch):
    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle
    from aletheia.storage.sqlite import get_db

    first = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")
    second = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert second.successor_experiment_id == first.successor_experiment_id
    assert get_db().execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 1
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 2


def test_failed_successor_insert_rolls_back_event_and_current_stage(monkeypatch):
    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, "positive")
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
    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, "positive")
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


def test_canonical_defaults_keep_numeric_consumers_on_static_baseline(monkeypatch):
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

    assert cfg.learning.lifecycle_automation_enabled is False
    assert cfg.learning.paper_influence_enabled is False
    assert cfg.learning.paper_influence_paths == []
    assert decision.value == 1.0
    assert decision.applied is False
    assert decision.reason == "baseline:paper_influence_disabled"


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


@pytest.mark.parametrize(
    ("epoch", "horizon", "contract", "metric", "reason"),
    [
        ("stale", "4h", "v1", "universe_family_calibration_net_return_v1", "stale_policy_epoch"),
        ("epoch-a", "1h", "v1", "universe_family_calibration_net_return_v1", "horizon_mismatch"),
        ("epoch-a", "4h", "unknown", "universe_family_calibration_net_return_v1", "outcome_contract_mismatch"),
        ("epoch-a", "4h", "v1", "unknown", "metric_version_mismatch"),
    ],
)
def test_positive_with_stale_or_mismatched_cohort_cannot_promote(monkeypatch, epoch, horizon, contract, metric, reason):
    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, "positive", epoch=epoch, horizon=horizon, contract=contract, metric=metric)
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    from aletheia.learning.lifecycle import process_lifecycle

    result = process_lifecycle(experiment.experiment_id, current_policy_epoch="epoch-a")

    assert result.action == "hold"
    assert result.reason == reason


def test_global_reconciliation_refreeze_overrides_every_authority_gate(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_10", allocation=0.1)
    _measurement(hypothesis, experiment, "positive")
    _configure(monkeypatch, _settings(automation=True, paper=True, paths=[hypothesis.path_id]))
    import aletheia.learning.authority as authority
    from aletheia.learning.authority import resolve_learning_authority

    monkeypatch.setattr(authority, "global_learning_refreeze_reason", lambda: "reconciliation_integrity_incident:test")
    decision = resolve_learning_authority(hypothesis.path_id, experiment, "epoch-a")

    assert decision.authorized is False
    assert decision.reason == "reconciliation_integrity_incident"


def test_paper_active_keeps_a_control_holdout_and_never_allocation_above_ninety(monkeypatch):
    hypothesis, experiment = _experiment(stage="paper_active", allocation=0.95)
    _measurement(hypothesis, experiment, "positive")
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
    assert inspection["recommended_lifecycle_action"] == "promote_stage"
    assert inspection["authority_enabled"] is False
    assert inspection["readiness"]["ready_for_paper_influence"] is False
    assert inspection["treatment_control_progress"]["required_per_arm"] == experiment.min_samples_per_arm
    assert "next_evaluation_at" in inspection


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

    hypothesis, experiment = _experiment()
    _measurement(hypothesis, experiment, "positive")
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
