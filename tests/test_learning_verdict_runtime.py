"""Focused regression tests for the read-only Learning contribution verdict engine."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


_BASE = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)


def _iso(days: int = 0, minutes: int = 0) -> str:
    return (_BASE + timedelta(days=days, minutes=minutes)).isoformat()


def _experiment(
    *, min_samples: int = 1, min_days: int = 0,
    path_id: str = "universe_family_calibration", stage: str = "paper_10",
):
    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    suffix = f"{path_id}:{min_samples}:{min_days}"
    hypothesis = LearningHypothesis(
        hypothesis_id=f"hypothesis-{suffix}",
        fingerprint=f"fingerprint-{suffix}",
        cohort_id="universe-clean-net-v1",
        path_id=path_id,
        overlap_group="universe",
        source_type="universe_forward_outcomes:clean_net:v2",
        scope_json='{"family":"momentum","direction":"long","market_type":"perp"}',
        action_json='{"type":"opportunity_multiplier","multiplier":1.1}',
        consumer="universe_allocator",
        target_horizon="4h",
        effect_threshold=0.1,
        harm_budget_json=json.dumps({"max_loss_rate_increase": 0.25, "max_mae_p95_worsening_pct": 0.25}),
    )
    assert insert_hypothesis(hypothesis)
    experiment = LearningExperiment(
        experiment_id=f"experiment-{suffix}",
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="test-salt",
        min_samples_per_arm=min_samples,
        min_market_days=min_days,
        harm_gate_json=json.dumps({"max_loss_rate_increase": 0.25, "max_mae_p95_worsening_pct": 0.25}),
        status="active",
        stage=stage,
    )
    assert insert_experiment(experiment)
    return hypothesis, experiment


def _exposure(
    experiment,
    hypothesis,
    *,
    candidate: str,
    assignment: str,
    epoch: str = "epoch-a",
    contract: str = "v1",
    exposed_at: str | None = None,
    outcome_key: str | None = None,
    authority_applied: bool | None = None,
    code_commit: str | None = "test-commit",
    behavior_contract_hash: str | None = "behavior-v1",
    assignment_unit_key: str | None = None,
):
    from aletheia.learning.ledger import insert_exposure, insert_record
    from aletheia.learning.models import LearningExposure, LearningPolicyEpoch

    insert_record("learning_policy_epochs", LearningPolicyEpoch(
        epoch_id=epoch,
        epoch_kind=f"decision_policy:{hypothesis.path_id}",
        effective_values_hash=f"policy-hash:{epoch}",
        effective_values_json="{}",
        affected_path_ids_json=json.dumps([hypothesis.path_id]),
        started_at=_iso(-1),
        ended_at=_iso(),
    ), ignore_duplicates=True)

    exposure = LearningExposure(
        exposure_id=f"exposure-{candidate}-{assignment}-{epoch}-{contract}",
        dedup_key=f"{experiment.experiment_id}:{candidate}:{assignment}:{epoch}:{contract}",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment=assignment,
        consumer="universe_allocator",
        consumer_stage="opportunity_score:shadow_refrozen",
        path_id=hypothesis.path_id,
        overlap_group="universe",
        candidate_key=candidate,
        assignment_unit_key=assignment_unit_key or candidate,
        outcome_candidate_key=outcome_key if outcome_key is not None else candidate,
        outcome_source="fixed_horizon_candidate",
        outcome_contract_version=contract,
        manifest_json=json.dumps({
            "authority_applied": (
                assignment == "treatment"
                if authority_applied is None else authority_applied
            ),
        }),
        experiment_stage=experiment.stage,
        code_commit=code_commit,
        behavior_contract_hash=behavior_contract_hash,
        concurrent_policy_epoch=epoch,
        exposed_at=exposed_at or _iso(),
    )
    assert insert_exposure(exposure)
    return exposure


def _outcome(
    candidate: str,
    *,
    horizon: str = "4h",
    net: float | None = 0.0,
    mae: float | None = -0.1,
    entry_at: str | None = None,
    status: str = "settled",
    cost_model_version: str | None = "cost-v1",
    suffix: str = "",
):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = entry_at or _iso(minutes=1)
    entry_dt = datetime.fromisoformat(entry.replace("Z", "+00:00"))
    due = (entry_dt + timedelta(hours=4)).isoformat()
    settled = (entry_dt + timedelta(hours=4, minutes=1)).isoformat()
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, outcome_candidate_key, member_id, snapshot_id, symbol, venue, market_type, "
        "scanner_family, direction, horizon, entry_price_usd, entry_ts, due_at, settled_at, "
        "net_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, "
        "mae_pct, cost_model_version, status) "
        "VALUES (?, ?, ?, ?, 'BTC/USDT', 'test', 'perp', 'momentum', 'long', ?, 100, ?, ?, ?, ?, "
        "0.1, 0.1, 0.0, ?, ?, ?)",
        (
            f"outcome-{candidate}-{horizon}-{suffix or 'primary'}",
            candidate,
            f"member-{candidate}-{suffix or 'primary'}",
            f"snapshot-{candidate}-{suffix or 'primary'}",
            horizon,
            entry,
            due,
            settled,
            net,
            mae,
            cost_model_version,
            status,
        ),
    )


def _filter_outcome(
    candidate: str,
    *,
    horizon: str = "4h",
    net: float | None = 0.0,
    mae: float | None = -0.1,
    entry_at: str | None = None,
    status: str = "settled",
    cost_model_version: str | None = "cost-v1",
    allocation_selected: bool | None = True,
    suffix: str = "",
):
    from aletheia.storage.sqlite import get_db

    entry = entry_at or _iso(minutes=1)
    entry_dt = datetime.fromisoformat(entry.replace("Z", "+00:00"))
    due = (entry_dt + timedelta(hours=4)).isoformat()
    settled = (entry_dt + timedelta(hours=4, minutes=1)).isoformat()
    get_db().execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, scanner_family, direction, "
        "horizon, allocation_selected, entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
        "estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, mae_pct, "
        "cost_model_version, status) "
        "VALUES (?, ?, 'test', 'BTC/USDT', 'perp', 'momentum', 'long', ?, ?, 100, ?, ?, ?, ?, "
        "0.1, 0.1, 0.0, ?, ?, ?)",
        (
            f"filter-outcome-{candidate}-{horizon}-{suffix or 'primary'}",
            candidate,
            horizon,
            allocation_selected,
            entry,
            due,
            settled,
            net,
            mae,
            cost_model_version,
            status,
        ),
    )


def _filter_outcome_matrix(
    candidate: str,
    *,
    target_net: float,
    allocation_selected: bool = True,
):
    for horizon in ("15m", "1h", "4h", "24h"):
        _filter_outcome(
            candidate,
            horizon=horizon,
            net=target_net if horizon == "4h" else 99.0,
            allocation_selected=allocation_selected,
        )


def _run(experiment_id: str):
    from aletheia.learning.contribution_runner import evaluate_experiment

    return evaluate_experiment(experiment_id)


def test_full_arm_filter_outcomes_are_primary_and_universe_mirror_is_not_double_counted():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="full-arm-treatment", assignment="treatment",
        outcome_key="full-arm-treatment:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="mirrored-treatment", assignment="treatment",
        outcome_key="mirrored-treatment:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="selected-control", assignment="control",
        outcome_key="selected-control:4h",
    )
    _filter_outcome("full-arm-treatment", net=1.4)
    _filter_outcome("mirrored-treatment", net=0.6)
    _outcome("mirrored-treatment:4h", net=0.6)
    _outcome("selected-control:4h", net=0.2)
    _filter_outcome("same-symbol-unrelated-candidate", net=99.0)

    verdict = _run(experiment.experiment_id)[0]
    from aletheia.learning.cohorts import build_experiment_cohorts
    cohort = build_experiment_cohorts(experiment.experiment_id)[0]

    assert (verdict.unique_treatment_n, verdict.unique_control_n) == (2, 1)
    assert verdict.observed == pytest.approx(1.0)
    assert verdict.baseline == pytest.approx(0.2)
    assert verdict.delta == pytest.approx(0.8)
    assert {sample.outcome_candidate_key for sample in cohort.samples} == {
        "full-arm-treatment:4h", "mirrored-treatment:4h", "selected-control:4h",
    }
    metrics = json.loads(verdict.harm_metrics_json)
    assert metrics["outcome_source_member_counts"] == {
        "filter_candidate_outcomes": 2,
        "universe_forward_outcomes_legacy_fallback": 1,
    }
    assert metrics["mirrored_universe_row_n"] == 1


def test_full_arm_metric_measures_allocation_utility_not_exogenous_candidate_mean():
    hypothesis, experiment = _experiment()
    for candidate, assignment, unit, net, selected in (
        ("selected-treatment", "treatment", "treatment-run", 2.0, True),
        ("dropped-treatment", "treatment", "treatment-run", -8.0, False),
        ("selected-control", "control", "control-run", 0.5, True),
        ("dropped-control", "control", "control-run", 5.0, False),
    ):
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment=assignment,
            outcome_key=f"{candidate}:4h",
            assignment_unit_key=unit,
        )
        _filter_outcome(candidate, net=net, allocation_selected=selected)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.observed == pytest.approx(1.0)
    assert verdict.baseline == pytest.approx(0.25)
    assert verdict.delta == pytest.approx(0.75)
    assert metrics["allocation_selected_member_n"] == 2
    assert metrics["allocation_total_member_n"] == 4


def test_normal_sibling_horizons_do_not_create_wrong_horizon_evidence():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="matrix-treatment", assignment="treatment",
        outcome_key="matrix-treatment:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="matrix-control", assignment="control",
        outcome_key="matrix-control:4h",
    )
    _filter_outcome_matrix("matrix-treatment", target_net=1.0)
    _filter_outcome_matrix("matrix-control", target_net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.delta == pytest.approx(1.0)
    assert "wrong_horizon" not in metrics["exclusion_reasons"]


def test_canonical_and_selected_mirror_economic_disagreement_fails_closed():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="conflict", assignment="treatment",
        outcome_key="conflict:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="clean", assignment="control",
        outcome_key="clean:4h",
    )
    _filter_outcome("conflict", net=1.0)
    _outcome("conflict:4h", net=-1.0)
    _filter_outcome("clean", net=0.0)

    verdict = _run(experiment.experiment_id)[0]

    assert verdict.verdict == "invalid"
    assert json.loads(verdict.harm_metrics_json)["invalid_reasons"] == {
        "conflicting_mirrored_outcome": 1,
    }
    assert len(json.loads(verdict.harm_metrics_json)["evidence_conflict_fingerprints"]) == 1


def test_mirror_inferred_selection_and_explicit_selection_have_distinct_provenance():
    hypothesis, experiment = _experiment()
    for candidate, assignment, net in (
        ("legacy-treatment", "treatment", 1.0),
        ("legacy-control", "control", 0.0),
    ):
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment=assignment,
            outcome_key=f"{candidate}:4h",
        )
        _filter_outcome(candidate, net=net, allocation_selected=None)
        _outcome(f"{candidate}:4h", net=net)

    inferred = _run(experiment.experiment_id)[0]
    inferred_metrics = json.loads(inferred.harm_metrics_json)
    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "UPDATE filter_candidate_outcomes SET allocation_selected=1 "
        "WHERE outcome_candidate_key IN ('legacy-treatment', 'legacy-control')"
    )
    db.commit()
    explicit = _run(experiment.experiment_id)[0]
    explicit_metrics = json.loads(explicit.harm_metrics_json)

    assert inferred.verdict_id != explicit.verdict_id
    assert inferred_metrics["allocation_selection_source_member_counts"] == {
        "universe_forward_outcomes_mirror_inference": 2,
    }
    assert explicit_metrics["allocation_selection_source_member_counts"] == {
        "filter_candidate_outcomes": 2,
    }


def test_fallback_and_later_canonical_outcome_create_distinct_verdict_history():
    hypothesis, experiment = _experiment()
    for candidate, assignment, net in (
        ("fallback-treatment", "treatment", 1.0),
        ("fallback-control", "control", 0.0),
    ):
        _exposure(experiment, hypothesis, candidate=candidate, assignment=assignment)
        _outcome(candidate, net=net)

    fallback = _run(experiment.experiment_id)[0]
    for candidate, net in (("fallback-treatment", 1.0), ("fallback-control", 0.0)):
        _filter_outcome(candidate, net=net, allocation_selected=True)
    canonical = _run(experiment.experiment_id)[0]

    assert fallback.verdict_id != canonical.verdict_id
    assert json.loads(fallback.harm_metrics_json)["outcome_source_member_counts"] == {
        "universe_forward_outcomes_legacy_fallback": 2,
    }
    assert json.loads(canonical.harm_metrics_json)["outcome_source_member_counts"] == {
        "filter_candidate_outcomes": 2,
    }


def test_changed_conflicting_evidence_creates_a_distinct_invalid_verdict():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="changing-conflict", assignment="treatment",
        outcome_key="changing-conflict:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="changing-clean", assignment="control",
        outcome_key="changing-clean:4h",
    )
    _filter_outcome("changing-conflict", net=1.0)
    _outcome("changing-conflict:4h", net=-1.0)
    _filter_outcome("changing-clean", net=0.0)
    first = _run(experiment.experiment_id)[0]

    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "UPDATE universe_forward_outcomes SET net_return_pct=-2.0 "
        "WHERE outcome_candidate_key='changing-conflict:4h'"
    )
    db.commit()
    second = _run(experiment.experiment_id)[0]

    assert first.verdict == second.verdict == "invalid"
    assert first.verdict_id != second.verdict_id


@pytest.mark.parametrize("conflicting_nets", [(1.0, -2.0), (-2.0, 1.0)])
def test_conflicting_universe_fallback_duplicates_fail_closed_in_any_insertion_order(
    conflicting_nets,
):
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="ambiguous", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="clean-control", assignment="control")
    for index, net in enumerate(conflicting_nets):
        _outcome("ambiguous", net=net, suffix=str(index))
    _outcome("clean-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"] == {"ambiguous_duplicate_outcome": 1}
    assert verdict.unique_treatment_n == 0


def test_conflicting_allocation_result_excludes_sample_without_invalidating_cohort():
    """A stale/backfilled allocation flag must not invalidate the whole cohort.

    ``conflicting_allocation_result`` fires when the filter ``allocation_selected``
    flag (largely a ``_backfill_fco_allocation_selected`` NULL→0 artifact) disagrees
    with an authoritative universe_forward_outcomes mirror. This is a missing/ambiguous
    *allocation identity*, not a genuine economic value conflict, so per #323/#324 it
    must fail closed as a benign exclusion — the one ambiguous sample is dropped, but
    the cohort is not branded corrupt and refrozen. (issues #160/#323/#324)
    """
    from aletheia.learning.cohorts import build_experiment_cohorts

    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="amb", assignment="treatment",
        outcome_key="amb:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="clean", assignment="control",
        outcome_key="clean:4h",
    )
    # Filter says NOT selected while an authoritative universe mirror exists.
    _filter_outcome("amb", net=0.0, allocation_selected=False, status="pending")
    _outcome("amb:4h", net=0.5)
    _filter_outcome("clean", net=0.0)

    cohort = build_experiment_cohorts(experiment.experiment_id)[0]

    assert "conflicting_allocation_result" not in cohort.invalid_reasons
    assert cohort.exclusion_reasons["conflicting_allocation_result"] == 1
    assert not cohort.invalid_reasons

    verdict = _run(experiment.experiment_id)[0]
    assert verdict.verdict != "invalid"


def test_missing_allocation_result_is_excluded_not_invalid():
    """A pending outcome with unknown allocation stays unsettled, not invalid (#324)."""
    from aletheia.learning.cohorts import build_experiment_cohorts

    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="pend", assignment="treatment",
        outcome_key="pend:4h",
    )
    # allocation_selected NULL, no universe mirror, non-opportunity path → unknown.
    _filter_outcome("pend", net=0.0, allocation_selected=None, status="pending")

    cohort = build_experiment_cohorts(experiment.experiment_id)[0]

    assert "missing_allocation_result" not in cohort.invalid_reasons
    assert cohort.exclusion_reasons["missing_allocation_result"] == 1
    assert not cohort.invalid_reasons


def test_missing_outcome_candidate_key_excludes_only_that_exposure():
    """One keyless exposure must not invalidate a cohort of otherwise-clean samples (#323)."""
    from aletheia.learning.cohorts import build_experiment_cohorts

    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="keyless", assignment="treatment",
        outcome_key="",
    )
    _exposure(
        experiment, hypothesis, candidate="good", assignment="control",
        outcome_key="good:4h",
    )
    _filter_outcome("good", net=0.0)

    cohort = build_experiment_cohorts(experiment.experiment_id)[0]

    assert "missing_outcome_candidate_key" not in cohort.invalid_reasons
    assert cohort.exclusion_reasons["missing_outcome_candidate_key"] == 1
    assert not cohort.invalid_reasons


def test_groups_treatment_and_control_and_reports_cost_aware_means():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="control", assignment="control")
    _outcome("treatment", net=1.2)
    _outcome("control", net=0.2)

    verdict = _run(experiment.experiment_id)[0]

    assert verdict.unique_treatment_n == 1
    assert verdict.unique_control_n == 1
    assert verdict.observed == pytest.approx(1.2)
    assert verdict.baseline == pytest.approx(0.2)
    assert verdict.delta == pytest.approx(1.0)


def test_policy_epoch_and_outcome_contract_isolate_cohorts():
    hypothesis, experiment = _experiment()
    for epoch, contract, side, net in (
        ("epoch-a", "v1", "treatment", 1.0),
        ("epoch-a", "v1", "control", 0.0),
        ("epoch-b", "v1", "treatment", 2.0),
        ("epoch-b", "v1", "control", 0.0),
        ("epoch-b", "v2", "treatment", 3.0),
        ("epoch-b", "v2", "control", 0.0),
    ):
        candidate = f"{epoch}-{contract}-{side}"
        _exposure(experiment, hypothesis, candidate=candidate, assignment=side, epoch=epoch, contract=contract)
        _outcome(candidate, net=net)

    verdicts = _run(experiment.experiment_id)
    metrics = [json.loads(verdict.harm_metrics_json) for verdict in verdicts]

    assert {(item["policy_epoch"], item["outcome_contract_version"]) for item in metrics} == {
        ("epoch-a", "v1"), ("epoch-b", "v1"), ("epoch-b", "v2"),
    }
    assert {verdict.delta for verdict in verdicts} == {1.0, 2.0, 3.0}


def test_same_behavior_contract_pools_exposures_across_commits():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="old-treatment", assignment="treatment",
        code_commit="old-build", behavior_contract_hash="behavior-a",
    )
    _exposure(
        experiment, hypothesis, candidate="new-control", assignment="control",
        code_commit="new-build", behavior_contract_hash="behavior-a",
    )
    _outcome("old-treatment", net=1.0)
    _outcome("new-control", net=0.0)

    from aletheia.learning.cohorts import build_experiment_cohorts

    cohorts = build_experiment_cohorts(experiment.experiment_id)

    assert len(cohorts) == 1
    assert cohorts[0].key.behavior_contract_hash == "behavior-a"
    assert cohorts[0].source_code_commits == {"old-build", "new-build"}


def test_mixed_known_and_unknown_commit_provenance_is_reported_incomplete():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="known-treatment", assignment="treatment",
        code_commit="known-build", behavior_contract_hash="behavior-a",
    )
    _exposure(
        experiment, hypothesis, candidate="unknown-control", assignment="control",
        code_commit=None, behavior_contract_hash="behavior-a",
    )
    _outcome("known-treatment", net=1.0)
    _outcome("unknown-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.code_commit is None
    assert metrics["source_code_commits"] == ["known-build"]
    assert metrics["source_code_commit_unknown_n"] == 1
    assert metrics["code_commit_provenance_complete"] is False


def test_different_behavior_contracts_stay_isolated_on_same_commit():
    hypothesis, experiment = _experiment()
    for contract_hash, side in (("behavior-a", "treatment"), ("behavior-b", "control")):
        candidate = f"{contract_hash}-{side}"
        _exposure(
            experiment, hypothesis, candidate=candidate, assignment=side,
            code_commit="same-build", behavior_contract_hash=contract_hash,
        )
        _outcome(candidate)

    from aletheia.learning.cohorts import build_experiment_cohorts

    cohorts = build_experiment_cohorts(experiment.experiment_id)

    assert {cohort.key.behavior_contract_hash for cohort in cohorts} == {
        "behavior-a", "behavior-b",
    }


def test_policy_epoch_from_another_path_is_invalid_cohort_evidence():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="wrong-path-epoch", assignment="control",
        epoch="epoch-wrong-path",
    )
    _outcome("wrong-path-epoch")
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "UPDATE learning_policy_epochs SET epoch_kind=? WHERE epoch_id=?",
        ("decision_policy:operator_learning_bundle", "epoch-wrong-path"),
    )
    get_db().commit()
    from aletheia.learning.cohorts import build_experiment_cohorts

    cohort = build_experiment_cohorts(experiment.experiment_id)[0]

    assert cohort.samples == []
    assert cohort.invalid_reasons == {"policy_epoch_path_mismatch": 1}


def test_exact_outcome_key_and_horizon_are_required_not_symbol_or_approximation():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="exact-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="exact-control", assignment="control")
    _outcome("same-symbol-but-wrong-key", net=99.0)
    _outcome("exact-treatment", horizon="1h", net=99.0)
    _outcome("exact-treatment", horizon="4h", net=1.0)
    _outcome("exact-control", horizon="4h", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.delta == pytest.approx(1.0)
    assert metrics["exclusion_reasons"]["wrong_horizon"] == 1


def test_duplicate_exposure_and_identical_outcome_rows_count_once():
    hypothesis, experiment = _experiment()
    treatment = _exposure(experiment, hypothesis, candidate="duplicate-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="duplicate-control", assignment="control")
    _outcome("duplicate-treatment", net=1.0)
    _outcome("duplicate-treatment", net=1.0, suffix="duplicate")
    _outcome("duplicate-control", net=0.0)
    # A retried audit record with a distinct dedup key must not manufacture another sample.
    from aletheia.learning.ledger import insert_exposure
    assert insert_exposure(treatment.model_copy(update={
        "exposure_id": "exposure-duplicate-treatment-retry",
        "dedup_key": "retry-audit-row",
    }))

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert (verdict.unique_treatment_n, verdict.unique_control_n) == (1, 1)
    assert metrics["duplicate_exposure_n"] == 1
    assert metrics["duplicate_outcome_n"] == 1


def test_assignment_cluster_counts_as_one_statistical_unit():
    hypothesis, experiment = _experiment()
    for candidate, assignment, unit, net in (
        ("cluster-t-1", "treatment", "cluster-treatment", 1.0),
        ("cluster-t-2", "treatment", "cluster-treatment", 3.0),
        ("cluster-c-1", "control", "cluster-control", 0.2),
        ("cluster-c-2", "control", "cluster-control", 0.4),
    ):
        _exposure(
            experiment, hypothesis, candidate=candidate, assignment=assignment,
            assignment_unit_key=unit,
        )
        _outcome(candidate, net=net)

    verdict = _run(experiment.experiment_id)[0]

    assert (verdict.unique_treatment_n, verdict.unique_control_n) == (1, 1)
    assert verdict.observed == pytest.approx(2.0)
    assert verdict.baseline == pytest.approx(0.3)


def test_incomplete_assignment_cluster_cannot_become_a_survivor_only_sample():
    hypothesis, experiment = _experiment()
    for candidate, assignment, unit in (
        ("missing-t-1", "treatment", "cluster-treatment"),
        ("missing-t-2", "treatment", "cluster-treatment"),
        ("complete-c-1", "control", "cluster-control"),
        ("complete-c-2", "control", "cluster-control"),
    ):
        _exposure(
            experiment, hypothesis, candidate=candidate, assignment=assignment,
            assignment_unit_key=unit,
        )
    _outcome("missing-t-1", net=4.0)
    _outcome("complete-c-1", net=0.1)
    _outcome("complete-c-2", net=0.2)

    verdict = _run(experiment.experiment_id)[0]

    assert verdict.verdict == "insufficient_data"
    assert (verdict.unique_treatment_n, verdict.unique_control_n) == (0, 1)


def test_incomplete_cost_model_makes_cohort_invalid():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="bad-cost-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="bad-cost-control", assignment="control")
    _outcome("bad-cost-treatment", net=1.0, cost_model_version=None)
    _outcome("bad-cost-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    assert verdict.verdict == "invalid"
    assert json.loads(verdict.harm_metrics_json)["invalid_reasons"]["missing_cost_model_version"] == 1


def test_unsettled_outcomes_are_excluded_without_becoming_zero_cost_samples():
    hypothesis, experiment = _experiment(min_samples=2)
    _exposure(experiment, hypothesis, candidate="pending-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="settled-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="pending-control", assignment="control")
    _exposure(experiment, hypothesis, candidate="settled-control", assignment="control")
    _outcome("pending-treatment", status="pending", net=None)
    _outcome("settled-treatment", net=1.0)
    _outcome("pending-control", status="pending", net=None)
    _outcome("settled-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.verdict == "insufficient_data"
    assert (verdict.unique_treatment_n, verdict.unique_control_n) == (1, 1)
    assert metrics["exclusion_reasons"]["unsettled"] == 2


def test_retrospective_or_pre_clean_window_outcome_makes_cohort_invalid():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="late-treatment", assignment="treatment", exposed_at=_iso())
    _exposure(experiment, hypothesis, candidate="clean-control", assignment="control")
    _outcome("late-treatment", net=1.0, entry_at=_iso(minutes=-1))
    _outcome("clean-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    assert verdict.verdict == "invalid"
    assert json.loads(verdict.harm_metrics_json)["invalid_reasons"]["retrospective_or_contaminated_exposure"] == 1


def test_insufficient_unique_samples_remain_insufficient_data():
    hypothesis, experiment = _experiment(min_samples=2, min_days=1)
    _exposure(experiment, hypothesis, candidate="only-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="only-control", assignment="control")
    _outcome("only-treatment", net=1.0)
    _outcome("only-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    assert verdict.verdict == "insufficient_data"
    assert verdict.unique_treatment_n == verdict.unique_control_n == 1


def test_small_non_negative_effect_is_neutral_not_positive():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="neutral-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="neutral-control", assignment="control")
    _outcome("neutral-treatment", net=0.05)
    _outcome("neutral-control", net=0.0)

    assert _run(experiment.experiment_id)[0].verdict == "neutral"


def test_positive_delta_with_harm_pass_records_reproducible_bootstrap():
    hypothesis, experiment = _experiment(min_samples=2, min_days=1)
    for day, assignment, net in (
        (0, "treatment", 1.0), (1, "treatment", 1.2),
        (0, "control", 0.0), (1, "control", 0.1),
    ):
        candidate = f"positive-{assignment}-{day}"
        _exposure(experiment, hypothesis, candidate=candidate, assignment=assignment, exposed_at=_iso(day))
        _outcome(candidate, net=net, entry_at=_iso(day, 1), mae=-0.1)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.verdict == "positive"
    assert verdict.confidence_low is not None and verdict.confidence_low > 0
    assert metrics["metric_version"]
    assert metrics["bootstrap_seed"] == 20260712
    assert metrics["bootstrap_resamples"] == 2000
    assert metrics["breached"] is False


def test_negative_delta_or_harm_breach_is_negative():
    hypothesis, experiment = _experiment(min_samples=2, min_days=1)
    for day, assignment, net, mae in (
        (0, "treatment", -1.0, -1.0), (1, "treatment", -1.2, -1.1),
        (0, "control", 1.0, -0.1), (1, "control", 1.1, -0.1),
    ):
        candidate = f"negative-{assignment}-{day}"
        _exposure(experiment, hypothesis, candidate=candidate, assignment=assignment, exposed_at=_iso(day))
        _outcome(candidate, net=net, mae=mae, entry_at=_iso(day, 1))

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.verdict == "negative"
    assert verdict.delta < 0
    assert metrics["breached"] is True
    assert metrics["breach_reasons"]


@pytest.mark.parametrize(
    "treatment_nets",
    [(1.0, 1.2), (-1.0, -1.2)],
    ids=["apparent_positive", "apparent_negative"],
)
def test_shadow_without_actual_intervention_is_not_a_causal_verdict(treatment_nets):
    hypothesis, experiment = _experiment(
        min_samples=2, min_days=1, stage="shadow",
    )
    for day, assignment, net in (
        (0, "treatment", treatment_nets[0]), (1, "treatment", treatment_nets[1]),
        (0, "control", 0.0), (1, "control", 0.1),
    ):
        candidate = f"shadow-{assignment}-{day}"
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment=assignment,
            exposed_at=_iso(day),
            authority_applied=False,
        )
        _outcome(candidate, net=net, entry_at=_iso(day, 1))

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)
    from aletheia.learning.cohorts import build_experiment_cohorts

    cohort = build_experiment_cohorts(experiment.experiment_id)[0]
    assert all(sample.authority_applied is False for sample in cohort.samples)
    assert verdict.verdict == "insufficient_data"
    assert verdict.reason == "no_actual_intervention"
    assert metrics["authority_applied_treatment_n"] == 0
    assert verdict.policy_epoch == "epoch-a"
    assert verdict.stage == "shadow"
    assert verdict.target_horizon == "4h"
    assert verdict.outcome_contract_version == "v1"
    assert verdict.metric_version == metrics["metric_version"]
    assert verdict.code_commit == "test-commit"


def test_real_shadow_flow_stays_shadow_after_at01_and_at02(monkeypatch):
    """Real producer -> exposure -> outcome -> AT-01 -> AT-02 integration seam."""
    from aletheia.config.schema import AletheiaConfig
    from aletheia.learning import authority, control, experiments, lifecycle
    from aletheia.learning.contribution_runner import evaluate_experiment
    from aletheia.learning.experiments import apply_numeric_treatment, stable_assignment
    from aletheia.learning.ledger import get_record
    from aletheia.learning.models import LearningExperiment
    from aletheia.storage.sqlite import get_db

    cfg = AletheiaConfig()
    cfg.learning.lifecycle_automation_enabled = True
    cfg.learning.paper_influence_enabled = True
    cfg.learning.paper_influence_paths = ["universe_family_calibration"]
    for module in (authority, control, experiments, lifecycle):
        monkeypatch.setattr(module, "load_config", lambda: cfg)
    monkeypatch.setattr(control, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(authority, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(lifecycle, "global_learning_refreeze_reason", lambda: None)

    # Model this as a historical producer run: the fixed 4h outcome must begin
    # after exposure and finish before the verdict is evaluated.
    evaluation_now = datetime.now(timezone.utc)
    historical_exposure_at = evaluation_now - timedelta(hours=5)
    exposure_model = experiments.LearningExposure

    def historical_exposure(**kwargs):
        kwargs.setdefault("exposed_at", historical_exposure_at.isoformat())
        return exposure_model(**kwargs)

    monkeypatch.setattr(experiments, "LearningExposure", historical_exposure)

    common = dict(
        path_id="universe_family_calibration",
        scope={"family": "momentum", "direction": "long", "market_type": "perp"},
        action={"type": "opportunity_multiplier", "multiplier": 1.1},
        source_cohort="universe_forward_outcomes:clean_net:v2",
        target_horizon="4h",
        baseline=1.0,
        treatment=1.1,
        consumer="universe_allocator",
        consumer_stage="opportunity_score",
    )
    first_candidate = "shadow-flow-seed"
    first = apply_numeric_treatment(
        candidate_key=first_candidate,
        outcome_candidate_key=first_candidate,
        **common,
    )
    experiment = LearningExperiment(**get_record("learning_experiments", first.experiment_id))
    opposite = "control" if first.assignment == "treatment" else "treatment"
    second_candidate = next(
        f"shadow-flow-{index}" for index in range(1_000)
        if stable_assignment(experiment, f"shadow-flow-{index}") == opposite
    )
    second = apply_numeric_treatment(
        candidate_key=second_candidate,
        outcome_candidate_key=second_candidate,
        **common,
    )

    assert first.applied is False
    assert second.applied is False
    exposures = [
        get_record("learning_exposures", first.exposure_id),
        get_record("learning_exposures", second.exposure_id),
    ]
    assert all(json.loads(row["manifest_json"])["authority_applied"] is False for row in exposures)

    db = get_db()
    db.execute(
        "UPDATE learning_experiments SET min_samples_per_arm=1, min_market_days=0 "
        "WHERE experiment_id=?",
        (experiment.experiment_id,),
    )
    db.commit()
    latest_exposure = max(
        datetime.fromisoformat(str(row["exposed_at"]).replace("Z", "+00:00"))
        for row in exposures
    )
    prospective_entry_at = latest_exposure + timedelta(minutes=1)
    settled_at = prospective_entry_at + timedelta(hours=4, minutes=1)
    assert settled_at <= evaluation_now
    prospective_entry = prospective_entry_at.isoformat()
    for candidate, net in ((first_candidate, 1.0), (second_candidate, 0.0)):
        _outcome(candidate, net=net, entry_at=prospective_entry)
    verdict = evaluate_experiment(experiment.experiment_id)[0]
    epoch = exposures[0]["concurrent_policy_epoch"]
    result = lifecycle.process_lifecycle(
        experiment.experiment_id,
        current_policy_epoch=epoch,
    )

    persisted = get_record("learning_experiments", experiment.experiment_id)
    assert verdict.verdict == "insufficient_data"
    assert verdict.reason == "no_actual_intervention"
    assert result.action == "hold"
    assert result.successor_experiment_id is None
    assert persisted["stage"] == "shadow"
    assert persisted["ended_at"] is None
    assert db.execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0] == 0


def test_pre_floor_outcomes_are_excluded_not_invalid():
    """Outcomes before the data-quality floor must be excluded (not corrupt the cohort)."""
    from aletheia.analytics.learning_window import LEARNING_DATA_FLOOR_ISO
    from datetime import datetime, timezone, timedelta

    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="pre-floor-t", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="pre-floor-c", assignment="control")

    floor_dt = datetime.fromisoformat(LEARNING_DATA_FLOOR_ISO.replace("Z", "+00:00"))
    pre_floor = (floor_dt - timedelta(hours=1)).isoformat()
    _outcome("pre-floor-t", net=1.0, entry_at=pre_floor)
    _outcome("pre-floor-c", net=0.0, entry_at=pre_floor)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    # Pre-floor observations are excluded, not corrupt → verdict is insufficient_data
    assert verdict.verdict == "insufficient_data", verdict.reason
    assert "dirty_or_missing_entry_time" not in metrics.get("invalid_reasons", {})
    assert metrics["exclusion_reasons"].get("dirty_or_missing_entry_time", 0) >= 1
    # No valid samples → sample counts are zero
    assert verdict.unique_treatment_n == verdict.unique_control_n == 0


def test_unidentifiable_assignment_overlap_is_invalid_and_not_attributed():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="overlap", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="overlap", assignment="control")
    _outcome("overlap", net=1.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = json.loads(verdict.harm_metrics_json)

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"]["unidentifiable_assignment_overlap"] == 1
    assert verdict.unique_treatment_n == verdict.unique_control_n == 0


def test_same_inputs_are_idempotent_and_inspection_is_read_only():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="idem-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="idem-control", assignment="control")
    _outcome("idem-treatment", net=1.0)
    _outcome("idem-control", net=0.0)

    first = _run(experiment.experiment_id)
    second = _run(experiment.experiment_id)
    from aletheia.learning.contribution_runner import inspect_experiment
    from aletheia.storage.sqlite import get_db

    assert [row.verdict_id for row in first] == [row.verdict_id for row in second]
    assert get_db().execute(
        "SELECT COUNT(*) FROM learning_contribution_verdicts WHERE experiment_id=?",
        (experiment.experiment_id,),
    ).fetchone()[0] == 1
    inspection = inspect_experiment(experiment.experiment_id)
    assert inspection["experiment"]["experiment_id"] == experiment.experiment_id
    assert len(inspection["cohorts"]) == len(inspection["verdicts"]) == 1


def test_verdict_created_at_is_evaluation_time_not_observation_window_end():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="clock-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="clock-control", assignment="control")
    _outcome("clock-treatment", net=1.0)
    _outcome("clock-control", net=0.0)

    before = datetime.now(timezone.utc)
    verdict = _run(experiment.experiment_id)[0]
    after = datetime.now(timezone.utc)
    created_at = datetime.fromisoformat(verdict.created_at.replace("Z", "+00:00"))
    window_end = datetime.fromisoformat(verdict.window_end.replace("Z", "+00:00"))

    assert before <= created_at <= after
    assert created_at > window_end


def test_excluded_cohort_verdict_created_at_is_not_unix_epoch():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="excluded", assignment="treatment")
    _outcome("excluded", net=1.0, entry_at=_iso(minutes=-1))

    before = datetime.now(timezone.utc)
    verdict = _run(experiment.experiment_id)[0]
    after = datetime.now(timezone.utc)
    created_at = datetime.fromisoformat(verdict.created_at.replace("Z", "+00:00"))

    assert verdict.verdict == "invalid"
    assert before <= created_at <= after


def test_measurement_verdict_never_authorizes_or_changes_learning_lifecycle():
    hypothesis, experiment = _experiment(min_samples=2, min_days=1)
    for day, assignment, net in (
        (0, "treatment", 1.0), (1, "treatment", 1.2),
        (0, "control", 0.0), (1, "control", 0.1),
    ):
        candidate = f"baseline-{assignment}-{day}"
        _exposure(experiment, hypothesis, candidate=candidate, assignment=assignment, exposed_at=_iso(day))
        _outcome(candidate, net=net, entry_at=_iso(day, 1))

    verdict = _run(experiment.experiment_id)[0]
    from aletheia.learning.experiments import _verdict_authorizes
    from aletheia.learning.ledger import get_record
    from aletheia.learning.models import LearningExperiment

    persisted_experiment = LearningExperiment(**get_record("learning_experiments", experiment.experiment_id))
    assert verdict.verdict == "positive"
    assert _verdict_authorizes(persisted_experiment, "epoch-a") is False
    assert get_record("learning_hypotheses", hypothesis.hypothesis_id)["status"] == hypothesis.status
    assert get_record("learning_experiments", experiment.experiment_id)["status"] == "active"


def test_legacy_authority_verdict_without_behavior_hash_cannot_authorize(monkeypatch):
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="authority-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="authority-control", assignment="control")
    _outcome("authority-treatment", net=1.0)
    _outcome("authority-control", net=0.0)
    from aletheia.learning.ledger import insert_record
    from aletheia.learning.models import LearningContributionVerdict, LearningExperiment
    from aletheia.learning.experiments import _verdict_authorizes
    from aletheia.config.schema import AletheiaConfig
    import aletheia.learning.authority as authority

    cfg = AletheiaConfig()
    cfg.learning.paper_influence_enabled = True
    cfg.learning.paper_influence_paths = [hypothesis.path_id]
    monkeypatch.setattr(authority, "load_config", lambda: cfg)
    monkeypatch.setattr(
        authority,
        "current_behavior_contract_hash",
        lambda **_kwargs: "behavior-v1",
    )

    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="manual-authority-promotion",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        window_start=_iso(-1),
        window_end=_iso(-1, 1),
        unique_treatment_n=1,
        unique_control_n=1,
        baseline=0.0,
        observed=1.0,
        delta=1.0,
        harm_metrics_json='{"breached":false}',
        verdict="promote",
        reason="separately authorized historical decision",
        policy_epoch="epoch-a",
        stage=experiment.stage,
        created_at=_iso(-1, 1),
    ))
    assert _verdict_authorizes(LearningExperiment(**experiment.model_dump()), "epoch-a") is False

    measurement = _run(experiment.experiment_id)[0]

    assert measurement.verdict in {"positive", "neutral"}
    assert _verdict_authorizes(LearningExperiment(**experiment.model_dump()), "epoch-a") is False


def test_unsupported_learning_path_is_not_processed_or_persisted():
    hypothesis, experiment = _experiment(path_id="totally_fake_path_id")
    _exposure(experiment, hypothesis, candidate="other-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="other-control", assignment="control")
    _outcome("other-treatment", net=1.0)
    _outcome("other-control", net=0.0)
    from aletheia.storage.sqlite import get_db

    assert _run(experiment.experiment_id) == []
    assert get_db().execute("SELECT COUNT(*) FROM learning_contribution_verdicts").fetchone()[0] == 0


def test_operator_learning_bundle_null_allocation_is_implicitly_selected():
    """operator_learning_bundle filter outcomes carry allocation_selected=NULL by contract.

    These rows represent chosen opportunities (emitted-opportunity observations), not
    Universe full-arm allocation members.  The cohort builder must treat NULL as
    implicitly True rather than failing with missing_allocation_result.  See issue #324.
    """
    hypothesis, experiment = _experiment(path_id="operator_learning_bundle")
    opp_key = "opportunity:test-opp-abc123"
    _exposure(
        experiment, hypothesis,
        candidate=opp_key,
        assignment="treatment",
        outcome_key=f"{opp_key}:4h",
    )
    _filter_outcome(opp_key, net=0.05, allocation_selected=None)

    from aletheia.learning.cohorts import build_experiment_cohorts
    cohorts = build_experiment_cohorts(experiment.experiment_id)

    assert len(cohorts) == 1
    assert len(cohorts[0].samples) == 1
    sample = cohorts[0].samples[0]
    assert sample.allocation_selected is True
    assert sample.allocation_selection_source == "opportunity_keyed_path_implicit"
    assert sample.assignment == "treatment"
    assert sample.net_return_pct == pytest.approx(0.05)
