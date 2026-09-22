"""Focused regression tests for the read-only Learning contribution verdict engine."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.universe.outcome_economics import COST_MODEL_VERSION

_BASE = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)


def _iso(days: int = 0, minutes: int = 0) -> str:
    return (_BASE + timedelta(days=days, minutes=minutes)).isoformat()


def _historical_record(table, record):
    from aletheia.storage.sqlite import get_db

    data = record.model_dump()
    db = get_db()
    cursor = db.execute(
        f"INSERT INTO {table} ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
        tuple(data.values()),
    )
    db.commit()
    return cursor.rowcount == 1


def _experiment(
    *, min_samples: int = 1, min_days: int = 0,
    path_id: str = "universe_family_calibration", stage: str = "paper_10",
):
    from extensions.learning.ledger import insert_hypothesis
    from extensions.learning.models import LearningExperiment, LearningHypothesis

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
        harm_budget_json=json.dumps({"max_loss_rate_increase": 0.15, "max_mae_p95_worsening_pp": 0.25}),
    )
    assert insert_hypothesis(hypothesis)
    experiment = LearningExperiment(
        experiment_id=f"experiment-{suffix}",
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="test-salt",
        min_samples_per_arm=min_samples,
        min_market_days=min_days,
        harm_gate_json="{}",
        status="active",
        stage=stage,
    )
    assert _historical_record("learning_experiments", experiment)
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
    authority_reason: str | None = None,
    behavior_changed: bool | None = None,
    code_commit: str | None = "test-commit",
    behavior_contract_hash: str | None = "behavior-v1",
    assignment_unit_key: str | None = None,
):
    from extensions.learning.ledger import insert_record
    from extensions.learning.models import LearningExposure, LearningPolicyEpoch

    insert_record("learning_policy_epochs", LearningPolicyEpoch(
        epoch_id=epoch,
        epoch_kind=f"decision_policy:{hypothesis.path_id}",
        effective_values_hash=f"policy-hash:{epoch}",
        effective_values_json="{}",
        affected_path_ids_json=json.dumps([hypothesis.path_id]),
        started_at=_iso(-1),
        ended_at=_iso(),
    ), ignore_duplicates=True)

    applied = (
        assignment == "treatment"
        if authority_applied is None else authority_applied
    )
    changed = applied if behavior_changed is None else behavior_changed
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
        before_json=json.dumps({"value": 0.0}),
        after_json=json.dumps({"value": 1.0 if changed else 0.0}),
        manifest_json=json.dumps({
            "authority_applied": applied,
            "behavior_changed": changed,
            "behavior_change_reason": (
                "effective_treatment_delta"
                if changed
                else "no_effective_treatment_delta"
                if applied
                else "control_assignment"
                if assignment == "control"
                else "authority_not_applied"
            ),
            **(
                {"authority_reason": authority_reason}
                if authority_reason is not None else {}
            ),
        }),
        experiment_stage=experiment.stage,
        code_commit=code_commit,
        behavior_contract_hash=behavior_contract_hash,
        concurrent_policy_epoch=epoch,
        exposed_at=exposed_at or _iso(),
    )
    assert _historical_record("learning_exposures", exposure)
    return exposure


def _outcome(
    candidate: str,
    *,
    horizon: str = "4h",
    net: float | None = 0.0,
    mae: float | None = -0.1,
    entry_at: str | None = None,
    status: str = "settled",
    cost_model_version: str | None = COST_MODEL_VERSION,
    min_tradable_edge_pct: float | None = 0.05,
    settlement_price_source: str = "horizon_ohlcv",
    settlement_lag_sec: float = 0.0,
    suffix: str = "",
):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = entry_at or _iso(minutes=1)
    entry_dt = datetime.fromisoformat(entry.replace("Z", "+00:00"))
    due = (entry_dt + timedelta(hours=4)).isoformat()
    settled = (entry_dt + timedelta(hours=4, minutes=1)).isoformat()
    directional = net + 0.2 if net is not None else None
    exit_price = 100.0 * (1.0 + directional / 100.0) if directional is not None else None
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, outcome_candidate_key, member_id, snapshot_id, symbol, venue, market_type, "
        "scanner_family, direction, horizon, entry_price_usd, exit_price_usd, "
        "entry_ts, due_at, settled_at, "
        "directional_return_pct, net_return_pct, min_tradable_edge_pct, estimated_fee_pct, "
        "estimated_spread_slippage_pct, estimated_funding_pct, "
        "mae_pct, settlement_price_source, settlement_lag_sec, "
        "cost_model_version, status) "
        "VALUES (?, ?, ?, ?, 'BTC/USDT', 'test', 'perp', 'momentum', 'long', ?, 100, ?, ?, ?, ?, ?, ?, ?, "
        "0.1, 0.1, 0.0, ?, ?, ?, ?, ?)",
        (
            f"outcome-{candidate}-{horizon}-{suffix or 'primary'}",
            candidate,
            f"member-{candidate}-{suffix or 'primary'}",
            f"snapshot-{candidate}-{suffix or 'primary'}",
            horizon,
            exit_price,
            entry,
            due,
            settled,
            directional,
            net,
            min_tradable_edge_pct,
            mae,
            settlement_price_source,
            settlement_lag_sec,
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
    cost_model_version: str | None = COST_MODEL_VERSION,
    min_tradable_edge_pct: float | None = 0.05,
    allocation_selected: bool | None = True,
    settlement_price_source: str = "horizon_ohlcv",
    settlement_lag_sec: float = 0.0,
    suffix: str = "",
):
    from aletheia.storage.sqlite import get_db

    entry = entry_at or _iso(minutes=1)
    entry_dt = datetime.fromisoformat(entry.replace("Z", "+00:00"))
    due = (entry_dt + timedelta(hours=4)).isoformat()
    settled = (entry_dt + timedelta(hours=4, minutes=1)).isoformat()
    directional = net + 0.2 if net is not None else None
    exit_price = 100.0 * (1.0 + directional / 100.0) if directional is not None else None
    get_db().execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, scanner_family, direction, "
        "horizon, allocation_selected, entry_price_usd, exit_price_usd, "
        "entry_ts, due_at, settled_at, "
        "directional_return_pct, net_return_pct, min_tradable_edge_pct, "
        "estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, mae_pct, "
        "settlement_price_source, settlement_lag_sec, cost_model_version, status) "
        "VALUES (?, ?, 'test', 'BTC/USDT', 'perp', 'momentum', 'long', ?, ?, 100, ?, ?, ?, ?, ?, ?, ?, "
        "0.1, 0.1, 0.0, ?, ?, ?, ?, ?)",
        (
            f"filter-outcome-{candidate}-{horizon}-{suffix or 'primary'}",
            candidate,
            horizon,
            allocation_selected,
            exit_price,
            entry,
            due,
            settled,
            directional,
            net,
            min_tradable_edge_pct,
            mae,
            settlement_price_source,
            settlement_lag_sec,
            cost_model_version,
            status,
        ),
    )








def test_latest_measurement_verdict_orders_by_exact_created_instant():
    from extensions.learning.ledger import (
        get_latest_measurement_verdict,
        insert_record,
    )
    from extensions.learning.models import LearningContributionVerdict

    hypothesis, experiment = _experiment()
    common = dict(
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage=experiment.stage,
        window_start="2026-08-25T00:00:00+00:00",
        window_end="2026-08-25T01:00:00+00:00",
        unique_treatment_n=10,
        unique_control_n=10,
        policy_epoch="epoch-a",
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="clean_net_delta_v1",
        behavior_contract_hash="behavior-v1",
        inference_role="monitoring",
        harm_metrics_json=json.dumps({"harm_state": "clear"}),
    )
    assert _historical_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="zz-older",
        verdict="positive",
        reason="older",
        created_at="2026-08-25T00:00:00.100000+00:00",
        **common,
    ))
    assert _historical_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="aa-newer",
        verdict="negative",
        reason="newer",
        created_at="2026-08-25T00:00:00.900000+00:00",
        **common,
    ))

    latest = get_latest_measurement_verdict(
        experiment.experiment_id,
        policy_epoch="epoch-a",
        stage=experiment.stage,
        hypothesis_id=hypothesis.hypothesis_id,
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="clean_net_delta_v1",
        behavior_contract_hash="behavior-v1",
        inference_role="monitoring",
    )

    assert latest is not None
    assert latest["verdict_id"] == "aa-newer"


def test_latest_measurement_verdict_treats_offset_equivalent_instants_as_ties():
    from extensions.learning.ledger import (
        get_latest_measurement_verdict,
        insert_record,
    )
    from extensions.learning.models import LearningContributionVerdict

    hypothesis, experiment = _experiment(path_id="ranker_factor_filter")
    common = dict(
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage=experiment.stage,
        window_start="2026-08-25T00:00:00+00:00",
        window_end="2026-08-25T01:00:00+00:00",
        unique_treatment_n=10,
        unique_control_n=10,
        policy_epoch="epoch-a",
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="clean_net_delta_v1",
        behavior_contract_hash="behavior-v1",
        inference_role="monitoring",
        harm_metrics_json=json.dumps({"harm_state": "clear"}),
    )
    assert _historical_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="aa-same-instant",
        verdict="positive",
        reason="same instant lower id",
        created_at="2026-08-25T08:00:00+08:00",
        **common,
    ))
    assert _historical_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="zz-same-instant",
        verdict="neutral",
        reason="same instant higher id",
        created_at="2026-08-25T00:00:00+00:00",
        **common,
    ))

    latest = get_latest_measurement_verdict(
        experiment.experiment_id,
        inference_role="monitoring",
    )

    assert latest is not None
    assert latest["verdict_id"] == "zz-same-instant"


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


def _cohorts(experiment_id, **kwargs):
    from extensions.learning.cohorts import build_experiment_cohorts
    from extensions.learning.models import LearningExperiment, LearningHypothesis
    from aletheia.storage.sqlite import get_db

    db = get_db()
    experiment = LearningExperiment(**dict(db.execute(
        "SELECT * FROM learning_experiments WHERE experiment_id=?", (experiment_id,),
    ).fetchone()))
    hypothesis = LearningHypothesis(**dict(db.execute(
        "SELECT * FROM learning_hypotheses WHERE hypothesis_id=?", (experiment.hypothesis_id,),
    ).fetchone()))
    rows = [dict(row) for row in db.execute(
        "SELECT * FROM learning_exposures WHERE experiment_id=?", (experiment_id,),
    )]
    return build_experiment_cohorts(
        experiment_id, _source_rows=rows, _source_experiment=experiment,
        _source_hypothesis=hypothesis,
        _source_valid_policy_epochs={row[0] for row in db.execute("SELECT epoch_id FROM learning_policy_epochs WHERE epoch_kind=?", (f"decision_policy:{hypothesis.path_id}",))},
        **kwargs,
    )


def _run(experiment_id: str, **kwargs):
    from extensions.learning.verdicts import evaluate_cohort

    return [evaluate_cohort(cohort) for cohort in _cohorts(experiment_id, **kwargs)]


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
    cohort = _cohorts(experiment.experiment_id)[0]

    assert (verdict.unique_treatment_n, verdict.unique_control_n) == (2, 1)
    assert verdict.observed == pytest.approx(1.0)
    assert verdict.baseline == pytest.approx(0.2)
    assert verdict.delta == pytest.approx(0.8)
    assert {sample.outcome_candidate_key for sample in cohort.samples} == {
        "full-arm-treatment:4h", "mirrored-treatment:4h", "selected-control:4h",
    }
    metrics = verdict.harm_metrics
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
    metrics = verdict.harm_metrics

    assert verdict.observed == pytest.approx(1.0)
    assert verdict.baseline == pytest.approx(0.25)
    assert verdict.delta == pytest.approx(0.75)
    assert metrics["allocation_selected_member_n"] == 2
    assert metrics["allocation_total_member_n"] == 4
    assert metrics["treatment_selected_member_n"] == 1
    assert metrics["treatment_member_n"] == 2
    assert metrics["control_selected_member_n"] == 1
    assert metrics["control_member_n"] == 2


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
    metrics = verdict.harm_metrics

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
    assert verdict.harm_metrics["invalid_reasons"] == {
        "conflicting_mirrored_outcome": 1,
    }
    metrics = verdict.harm_metrics
    assert len(metrics["evidence_conflict_fingerprints"]) == 1
    assert metrics["evidence_conflict_total_n"] == 1
    assert metrics["evidence_conflict_provenance_truncated"] is False
    conflict = metrics["evidence_conflicts"][0]
    assert conflict["reason"] == "conflicting_mirrored_outcome"
    assert conflict["outcome_candidate_key"] == "conflict:4h"
    assert conflict["canonical"]["source"] == "filter_candidate_outcomes"
    assert conflict["canonical"]["row_id"]
    assert conflict["mirrors"][0]["source"] == "universe_forward_outcomes"
    assert conflict["mirrors"][0]["row_id"]
    assert conflict["differing_fields"]["net_return_pct"] == {
        "canonical": 1.0,
        "mirrors": [-1.0],
    }


def test_canonical_and_mirror_temporal_disagreement_fails_closed():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="temporal-mirror", assignment="treatment",
        outcome_key="temporal-mirror:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="temporal-clean", assignment="control",
        outcome_key="temporal-clean:4h",
    )
    _filter_outcome("temporal-mirror", net=1.0)
    _outcome("temporal-mirror:4h", net=1.0)
    _filter_outcome("temporal-clean", net=0.0)

    from aletheia.storage.sqlite import get_db

    db = get_db()
    mirror = db.execute(
        "SELECT settled_at FROM universe_forward_outcomes "
        "WHERE outcome_candidate_key='temporal-mirror:4h'"
    ).fetchone()
    invalid_due = (
        datetime.fromisoformat(mirror["settled_at"]) + timedelta(minutes=1)
    ).isoformat()
    db.execute(
        "UPDATE universe_forward_outcomes SET due_at=? "
        "WHERE outcome_candidate_key='temporal-mirror:4h'",
        (invalid_due,),
    )
    db.commit()

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"] == {"conflicting_mirrored_outcome": 1}
    assert "due_at" in metrics["evidence_conflicts"][0]["differing_fields"]


def test_canonical_and_mirror_source_label_difference_is_not_an_economic_conflict():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="source-label-treatment", assignment="treatment",
        outcome_key="source-label-treatment:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="source-label-control", assignment="control",
        outcome_key="source-label-control:4h",
    )
    _filter_outcome("source-label-treatment", net=1.0)
    _outcome(
        "source-label-treatment:4h",
        net=1.0,
        settlement_price_source="horizon_ohlcv_local",
    )
    _filter_outcome("source-label-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict != "invalid"
    assert "conflicting_mirrored_outcome" not in metrics["invalid_reasons"]


def test_valid_canonical_and_invalid_mirror_price_provenance_conflict():
    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="provenance-treatment", assignment="treatment",
        outcome_key="provenance-treatment:4h",
    )
    _exposure(
        experiment, hypothesis, candidate="provenance-control", assignment="control",
        outcome_key="provenance-control:4h",
    )
    _filter_outcome("provenance-treatment", net=1.0)
    _outcome(
        "provenance-treatment:4h",
        net=1.0,
        settlement_price_source="late_ticker",
        settlement_lag_sec=3600.0,
    )
    _filter_outcome("provenance-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"] == {"conflicting_mirrored_outcome": 1}
    conflict = metrics["evidence_conflicts"][0]
    assert conflict["canonical"]["settlement_price_source"] == "horizon_ohlcv"
    assert conflict["mirrors"][0]["settlement_price_source"] == "late_ticker"


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
    inferred_metrics = inferred.harm_metrics
    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "UPDATE filter_candidate_outcomes SET allocation_selected=1 "
        "WHERE outcome_candidate_key IN ('legacy-treatment', 'legacy-control')"
    )
    db.commit()
    explicit = _run(experiment.experiment_id)[0]
    explicit_metrics = explicit.harm_metrics

    assert inferred.harm_metrics != explicit.harm_metrics
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

    assert fallback.harm_metrics != canonical.harm_metrics
    assert fallback.harm_metrics["outcome_source_member_counts"] == {
        "universe_forward_outcomes_legacy_fallback": 2,
    }
    assert canonical.harm_metrics["outcome_source_member_counts"] == {
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
        "UPDATE universe_forward_outcomes SET directional_return_pct=-1.8, "
        "exit_price_usd=entry_price_usd * 0.982, net_return_pct=-2.0 "
        "WHERE outcome_candidate_key='changing-conflict:4h'"
    )
    db.commit()
    second = _run(experiment.experiment_id)[0]

    assert first.verdict == second.verdict == "invalid"
    assert first.harm_metrics != second.harm_metrics


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
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"] == {"ambiguous_duplicate_outcome": 1}
    assert verdict.unique_treatment_n == 0


def test_due_only_universe_duplicate_conflict_fails_closed():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="due-ambiguous", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="due-clean", assignment="control")
    _outcome("due-ambiguous", net=1.0, suffix="valid")
    _outcome("due-ambiguous", net=1.0, suffix="invalid")
    _outcome("due-clean", net=0.0)

    from aletheia.storage.sqlite import get_db

    db = get_db()
    invalid = db.execute(
        "SELECT settled_at FROM universe_forward_outcomes "
        "WHERE id='outcome-due-ambiguous-4h-invalid'"
    ).fetchone()
    db.execute(
        "UPDATE universe_forward_outcomes SET due_at=? "
        "WHERE id='outcome-due-ambiguous-4h-invalid'",
        (
            (
                datetime.fromisoformat(invalid["settled_at"])
                + timedelta(minutes=1)
            ).isoformat(),
        ),
    )
    db.commit()

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"] == {"ambiguous_duplicate_outcome": 1}


def test_future_duplicate_cannot_invalidate_historical_cohort():

    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="future-duplicate", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="future-clean", assignment="control")
    _outcome("future-duplicate", net=1.0, suffix="mature")
    _outcome(
        "future-duplicate",
        net=-99.0,
        suffix="future",
        entry_at=_iso(days=2),
    )
    _outcome("future-clean", net=0.0)
    cutoff = _BASE + timedelta(days=1)

    cohort = _cohorts(
        experiment.experiment_id,
        as_of_at=cutoff,
    )[0]
    verdicts = _run(
        experiment.experiment_id,
        as_of_at=cutoff,
    )

    assert not cohort.invalid_reasons
    assert {sample.candidate_key for sample in cohort.samples} == {
        "future-duplicate",
        "future-clean",
    }
    assert verdicts
    assert all(verdict.verdict != "invalid" for verdict in verdicts)


def test_future_row_keeps_higher_priority_temporal_invalid_reason():
    from aletheia.storage.sqlite import get_db

    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="future-invalid", assignment="treatment")
    _outcome(
        "future-invalid",
        net=1.0,
        entry_at=_iso(days=2),
    )
    db = get_db()
    db.execute(
        "UPDATE universe_forward_outcomes SET due_at=? "
        "WHERE outcome_candidate_key='future-invalid'",
        (_iso(days=2, minutes=-1),),
    )
    db.commit()

    verdict = _run(
        experiment.experiment_id,
        as_of_at=_BASE + timedelta(days=1),
    )[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"] == {"due_before_entry": 1}


def test_conflicting_allocation_result_excludes_sample_without_invalidating_cohort():
    """A stale/backfilled allocation flag must not invalidate the whole cohort.

    ``conflicting_allocation_result`` fires when the filter ``allocation_selected``
    flag (largely a ``_backfill_fco_allocation_selected`` NULL→0 artifact) disagrees
    with an authoritative universe_forward_outcomes mirror. This is a missing/ambiguous
    *allocation identity*, not a genuine economic value conflict, so per #323/#324 it
    must fail closed as a benign exclusion — the one ambiguous sample is dropped, but
    the cohort is not branded corrupt and refrozen. (issues #160/#323/#324)
    """

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

    cohort = _cohorts(experiment.experiment_id)[0]

    assert "conflicting_allocation_result" not in cohort.invalid_reasons
    assert cohort.exclusion_reasons["conflicting_allocation_result"] == 1
    assert not cohort.invalid_reasons

    verdict = _run(experiment.experiment_id)[0]
    assert verdict.verdict != "invalid"


def test_missing_allocation_result_is_excluded_not_invalid():
    """A pending outcome with unknown allocation stays unsettled, not invalid (#324)."""

    hypothesis, experiment = _experiment()
    _exposure(
        experiment, hypothesis, candidate="pend", assignment="treatment",
        outcome_key="pend:4h",
    )
    # allocation_selected NULL, no universe mirror, non-opportunity path → unknown.
    _filter_outcome("pend", net=0.0, allocation_selected=None, status="pending")

    cohort = _cohorts(experiment.experiment_id)[0]

    assert "missing_allocation_result" not in cohort.invalid_reasons
    assert cohort.exclusion_reasons["missing_allocation_result"] == 1
    assert not cohort.invalid_reasons


def test_missing_outcome_candidate_key_excludes_only_that_exposure():
    """One keyless exposure must not invalidate a cohort of otherwise-clean samples (#323)."""

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

    cohort = _cohorts(experiment.experiment_id)[0]

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
    metrics = [verdict.harm_metrics for verdict in verdicts]

    assert {(item["policy_epoch"], item["outcome_contract_version"]) for item in metrics} == {
        ("epoch-a", "v1"), ("epoch-b", "v1"), ("epoch-b", "v2"),
    }
    verdict_by_identity = {
        (verdict.harm_metrics["policy_epoch"], verdict.harm_metrics["outcome_contract_version"]): verdict
        for verdict in verdicts
    }
    assert verdict_by_identity[("epoch-a", "v1")].delta == 1.0
    assert verdict_by_identity[("epoch-b", "v1")].delta == 2.0
    mismatched = verdict_by_identity[("epoch-b", "v2")]
    assert mismatched.verdict == "invalid"
    assert mismatched.delta is None
    assert mismatched.harm_metrics["invalid_reasons"] == {
        "outcome_contract_version_mismatch": 2,
    }


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


    cohorts = _cohorts(experiment.experiment_id)

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
    metrics = verdict.harm_metrics

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


    cohorts = _cohorts(experiment.experiment_id)

    assert {cohort.key.behavior_contract_hash for cohort in cohorts} == {
        "behavior-a", "behavior-b",
    }


def test_v5_v6_identity_does_not_mix_v4_v5_evidence(monkeypatch):
    from dataclasses import replace

    from extensions.learning import behavior_contracts
    from extensions.learning.registry import get_learning_path
    from extensions.learning.verdicts import METRIC_VERSION

    hypothesis, experiment = _experiment()
    path = get_learning_path(hypothesis.path_id)
    common = {
        "path_id": hypothesis.path_id,
        "policy_hash": "policy-hash:epoch-a",
        "target_horizon": hypothesis.target_horizon,
        "outcome_contract_version": "v1",
    }
    current_hash = behavior_contracts.build_behavior_contract_hash(
        **common,
        metric_version=METRIC_VERSION,
    )
    monkeypatch.setattr(
        behavior_contracts,
        "get_learning_path",
        lambda _path_id: replace(
            path,
            behavior_contract_version="universe-family-calibration/v5",
        ),
    )
    prior_hash = behavior_contracts.build_behavior_contract_hash(
        **common,
        metric_version="universe_family_calibration_cluster_allocation_utility_v4",
    )
    assert current_hash != prior_hash

    _exposure(
        experiment,
        hypothesis,
        candidate="prior-identity-treatment",
        assignment="treatment",
        behavior_contract_hash=prior_hash,
    )
    _outcome("prior-identity-treatment", net=9.0)
    _exposure(
        experiment,
        hypothesis,
        candidate="current-identity-control",
        assignment="control",
        behavior_contract_hash=current_hash,
    )
    _outcome("current-identity-control", net=0.0)

    cohorts = {
        cohort.key.behavior_contract_hash: cohort
        for cohort in _cohorts(experiment.experiment_id)
    }

    assert len(cohorts) == 2
    assert len(cohorts[prior_hash].treatment_samples) == 1
    assert len(cohorts[prior_hash].control_samples) == 0
    assert len(cohorts[current_hash].treatment_samples) == 0
    assert len(cohorts[current_hash].control_samples) == 1


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

    cohort = _cohorts(experiment.experiment_id)[0]

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
    metrics = verdict.harm_metrics

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
    assert _historical_record("learning_exposures", treatment.model_copy(update={
        "exposure_id": "exposure-duplicate-treatment-retry",
        "dedup_key": "retry-audit-row",
    }))

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

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
    assert verdict.harm_metrics["invalid_reasons"]["missing_cost_model_version"] == 1


def test_mixed_cost_model_versions_make_cohort_invalid_and_auditable():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="current-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="legacy-control", assignment="control")
    _outcome("current-treatment", net=1.0)
    _outcome("legacy-control", net=0.0, cost_model_version="legacy-v0")

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert metrics["expected_cost_model_version"] == COST_MODEL_VERSION
    assert metrics["invalid_reasons"]["cost_model_version_mismatch"] == 1


@pytest.mark.parametrize("outcome_source", ["universe", "filter"])
def test_linked_outcome_counts_require_explicit_exact_cost_model(outcome_source):
    from extensions.learning.ledger import linked_settled_exposure_counts

    hypothesis, experiment = _experiment()
    for candidate, assignment, version in (
        ("current-linked", "treatment", COST_MODEL_VERSION),
        ("legacy-linked", "control", "legacy-v0"),
    ):
        outcome_key = candidate if outcome_source == "universe" else f"{candidate}:4h"
        _exposure(
            experiment, hypothesis, candidate=candidate, assignment=assignment,
            outcome_key=outcome_key,
        )
        if outcome_source == "universe":
            _outcome(candidate, cost_model_version=version)
        else:
            _filter_outcome(candidate, cost_model_version=version)

    current = linked_settled_exposure_counts(
        experiment.experiment_id,
        cost_model_version=COST_MODEL_VERSION,
        policy_epoch="epoch-a",
    )
    historical = linked_settled_exposure_counts(
        experiment.experiment_id,
        cost_model_version="legacy-v0",
        policy_epoch="epoch-a",
    )

    assert current == {"control": 0, "treatment": 1}
    assert historical == {"control": 1, "treatment": 0}


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
    metrics = verdict.harm_metrics

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
    assert verdict.harm_metrics["invalid_reasons"]["retrospective_or_contaminated_exposure"] == 1


def test_insufficient_unique_samples_remain_insufficient_data():
    hypothesis, experiment = _experiment(min_samples=2, min_days=1)
    _exposure(experiment, hypothesis, candidate="only-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="only-control", assignment="control")
    _outcome("only-treatment", net=1.0)
    _outcome("only-control", net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    assert verdict.verdict == "insufficient_data"
    assert verdict.unique_treatment_n == verdict.unique_control_n == 1


def test_elapsed_span_cannot_bridge_missing_market_days():
    hypothesis, experiment = _experiment(min_samples=2, min_days=7)
    for day in (0, 7):
        for assignment, net in (("treatment", 1.0), ("control", 0.0)):
            candidate = f"gapped-{assignment}-{day}"
            _exposure(
                experiment,
                hypothesis,
                candidate=candidate,
                assignment=assignment,
                exposed_at=_iso(day),
            )
            _outcome(candidate, net=net, entry_at=_iso(day, 1))

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "insufficient_data"
    assert verdict.reason == "market_day_coverage_gap"
    coverage = metrics["market_day_coverage"]
    assert coverage["basis"] == "eligible_assignment_unit_exposure_utc_day_either_arm"
    assert coverage["status"] == "gap"
    assert coverage["required_consecutive_days"] == 7
    assert coverage["max_allowed_missing_days"] == 0
    assert coverage["current_consecutive_market_days"] == 1
    assert coverage["longest_consecutive_market_days"] == 1
    assert coverage["missing_market_day_n"] == 6
    assert coverage["missing_market_days"] == [
        _iso(day)[:10] for day in range(1, 7)
    ]
    assert coverage["current_window_start"] == _iso(7)[:10]
    assert coverage["current_window_end"] == _iso(7)[:10]


def test_seven_consecutive_market_days_with_fifty_units_per_arm_is_eligible():
    hypothesis, experiment = _experiment(min_samples=50, min_days=7)
    for index in range(50):
        day = index % 7
        for assignment, net in (("treatment", 1.0), ("control", 0.0)):
            candidate = f"continuous-{assignment}-{index}"
            _exposure(
                experiment,
                hypothesis,
                candidate=candidate,
                assignment=assignment,
                exposed_at=_iso(day, index),
            )
            _outcome(candidate, net=net, entry_at=_iso(day, index + 1))

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "positive"
    assert metrics["market_day_coverage"]["status"] == "qualified"
    assert metrics["market_day_coverage"]["longest_consecutive_market_days"] == 7
    assert metrics["harm_state"] == "clear"


def test_malformed_exposure_time_fails_closed_for_continuity():
    hypothesis, experiment = _experiment(min_samples=1, min_days=1)
    _exposure(
        experiment,
        hypothesis,
        candidate="malformed-time-treatment",
        assignment="treatment",
    )
    _exposure(
        experiment,
        hypothesis,
        candidate="malformed-time-control",
        assignment="control",
    )
    _outcome("malformed-time-treatment", net=1.0)
    _outcome("malformed-time-control", net=0.0)

    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "UPDATE learning_exposures SET exposed_at='not-a-time' WHERE candidate_key=?",
        ("malformed-time-treatment",),
    )
    db.commit()

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"]["missing_exposure_time"] == 1


def test_small_non_negative_effect_is_neutral_not_positive():
    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="neutral-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="neutral-control", assignment="control")
    _outcome("neutral-treatment", net=0.05)
    _outcome("neutral-control", net=0.0)

    assert _run(experiment.experiment_id)[0].verdict == "neutral"


def test_positive_delta_uses_randomization_inference_while_harm_is_indeterminate():
    hypothesis, experiment = _experiment(min_samples=10, min_days=1)
    for index in range(10):
        for assignment, net in (("treatment", 1.0), ("control", 0.0)):
            candidate = f"positive-{assignment}-{index}"
            _exposure(
                experiment, hypothesis, candidate=candidate, assignment=assignment,
                exposed_at=_iso(index // 5, index % 5),
            )
            _outcome(candidate, net=net, entry_at=_iso(index // 5, index % 5 + 1), mae=-0.1)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "positive"
    assert verdict.confidence_low is None
    assert verdict.confidence_high is None
    assert metrics["metric_version"]
    inference = metrics["randomization_inference"]
    assert inference["contract_version"] == "conditional-randomization-itt/v1"
    assert inference["iid_outcome_assumption"] is False
    assert inference["p_value"] <= 0.10
    assert inference["seed"] == 20260712
    assert metrics["iid_bootstrap"] == "retired_overlapping_horizon_outcomes"
    assert metrics["harm_state"] == "indeterminate"
    assert metrics["breached"] is False


def test_small_perfect_separation_cannot_impersonate_randomized_evidence():
    hypothesis, experiment = _experiment(min_samples=2, min_days=1)
    for index in range(2):
        for assignment, net in (("treatment", 1.0), ("control", 0.0)):
            candidate = f"small-separation-{assignment}-{index}"
            _exposure(
                experiment, hypothesis, candidate=candidate, assignment=assignment,
                exposed_at=_iso(index),
            )
            _outcome(candidate, net=net, entry_at=_iso(index, 1), mae=-0.1)

    verdict = _run(experiment.experiment_id)[0]
    inference = verdict.harm_metrics["randomization_inference"]

    assert verdict.delta == 1.0
    assert verdict.verdict == "neutral"
    assert inference["mode"] == "exact"
    assert inference["assignment_space_size"] == 6
    assert inference["p_value"] == pytest.approx(1 / 6)


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
    metrics = verdict.harm_metrics

    assert verdict.verdict == "negative"
    assert verdict.delta < 0
    assert metrics["harm_state"] == "indeterminate"
    assert metrics["breached"] is False
    assert metrics["breach_reasons"] == []


def test_underpowered_harm_is_indeterminate_and_does_not_claim_a_breach():
    hypothesis, experiment = _experiment(min_samples=1)
    for assignment, net, mae in (
        ("treatment", -10.0, -10.0),
        ("control", 10.0, -0.1),
    ):
        candidate = f"underpowered-{assignment}"
        _exposure(experiment, hypothesis, candidate=candidate, assignment=assignment)
        _outcome(candidate, net=net, mae=mae)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "negative"
    assert metrics["harm_state"] == "indeterminate"
    assert metrics["harm_indeterminate_reason"] == "insufficient_samples"
    assert metrics["harm_required_per_arm"] == 50
    assert metrics["breached"] is False
    assert metrics["breach_reasons"] == []


def test_ranker_shadow_no_intervention_precedes_harm_breach(monkeypatch):
    hypothesis, experiment = _experiment(
        min_samples=1,
        path_id="ranker_case_score",
        stage="paper_10",
    )
    for index in range(1):
        for assignment, net in (("treatment", -10.0), ("control", 10.0)):
            candidate = f"ranker-shadow-{assignment}-{index}"
            _exposure(
                experiment,
                hypothesis,
                candidate=candidate,
                assignment=assignment,
                authority_applied=False,
                authority_reason="experiment_stage_not_paper",
                behavior_changed=False,
                contract="ranker-case-filter-candidate/v1",
            )
            _filter_outcome(candidate, net=net, mae=-10.0)

    from extensions.learning import verdicts

    monkeypatch.setattr(
        verdicts,
        "_harm_metrics",
        lambda *_args, **_kwargs: {
            "breached": True,
            "breach_reasons": ["synthetic_harm_breach"],
            "harm_state": "breached",
        },
    )

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert metrics["breached"] is True
    assert verdict.verdict == "insufficient_data"
    assert verdict.reason == "no_actual_intervention"


def test_loss_rate_gate_is_reachable_after_fifty_samples_per_arm():
    hypothesis, experiment = _experiment(min_samples=50)
    from aletheia.storage.sqlite import get_db
    get_db().execute(
        "UPDATE learning_hypotheses SET harm_budget_json=? WHERE hypothesis_id=?",
        (json.dumps({"max_loss_rate_increase": 0.25}), hypothesis.hypothesis_id),
    )
    get_db().commit()
    for index in range(50):
        treatment_net = -0.1 if index < 10 else 1.0
        for assignment, net in (("treatment", treatment_net), ("control", 0.1)):
            candidate = f"loss-rate-{assignment}-{index}"
            _exposure(experiment, hypothesis, candidate=candidate, assignment=assignment)
            _outcome(candidate, net=net, mae=-0.1)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.delta > 0
    assert verdict.verdict == "negative"
    assert metrics["harm_state"] == "breached"
    assert metrics["harm_gates"]["max_loss_rate_increase"] == 0.15
    assert metrics["loss_rate_delta"] == pytest.approx(0.2)
    assert metrics["breach_reasons"] == ["loss_rate_increase"]


def test_conflict_provenance_bounds_rows_inside_one_conflict():
    from extensions.learning.cohorts import _conflict_provenance

    rows = [
        ("universe_forward_outcomes", {
            "id": index,
            "net_return_pct": float(index),
        })
        for index in range(30)
    ]

    conflict = _conflict_provenance(
        "ambiguous_duplicate_outcome",
        "bounded:4h",
        rows,
    )

    assert conflict["row_count"] == 30
    assert conflict["row_provenance_truncated"] is True
    assert len(conflict["rows"]) == 20
    assert len(conflict["differing_fields"]["net_return_pct"]) == 20


def test_bounded_conflict_provenance_keeps_a_triggering_mirror():
    from extensions.learning.cohorts import _conflict_provenance

    canonical = ("filter_candidate_outcomes", {"id": 1, "net_return_pct": 1.0})
    matching = [
        ("universe_forward_outcomes", {"id": index, "net_return_pct": 1.0})
        for index in range(2, 24)
    ]
    differing = (
        "universe_forward_outcomes",
        {"id": 99, "net_return_pct": -1.0},
    )

    conflict = _conflict_provenance(
        "conflicting_mirrored_outcome",
        "bounded-trigger:4h",
        [canonical, *matching, differing],
        canonical_index=0,
    )

    assert conflict["row_provenance_truncated"] is True
    assert conflict["differing_fields"]["net_return_pct"]["mirrors"][0] == -1.0
    assert conflict["mirrors"][0]["row_id"] == 99


def test_mae_harm_fields_are_absolute_percentage_points():
    hypothesis, experiment = _experiment(min_samples=50)
    for index in range(50):
        for assignment, net, mae in (
            ("treatment", 1.0, -0.36),
            ("control", 0.1, -0.10),
        ):
            candidate = f"mae-pp-{assignment}-{index}"
            _exposure(experiment, hypothesis, candidate=candidate, assignment=assignment)
            _outcome(candidate, net=net, mae=mae)

    metrics = _run(experiment.experiment_id)[0].harm_metrics

    assert metrics["harm_state"] == "breached"
    assert metrics["mae_p95_worsening_pp"] == pytest.approx(0.26)
    assert metrics["harm_gates"]["max_mae_p95_worsening_pp"] == 0.25
    assert "mae_p95_worsening_pct" not in metrics
    assert metrics["breach_reasons"] == ["mae_p95_worsening"]




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
                authority_reason="experiment_stage_not_paper",
            )
        _outcome(candidate, net=net, entry_at=_iso(day, 1))

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    cohort = _cohorts(experiment.experiment_id)[0]
    assert all(sample.authority_applied is False for sample in cohort.samples)
    assert cohort.inspection()["unique_treatment_n"] == 2
    assert cohort.inspection()["assigned_treatment_n"] == 2
    assert cohort.inspection()["actual_intervention_treatment_n"] == 0
    assert cohort.inspection()["baseline_treatment_n"] == 2
    assert verdict.verdict == "insufficient_data"
    assert verdict.reason == "no_actual_intervention"
    assert metrics["authority_applied_treatment_n"] == 0
    assert metrics["policy_epoch"] == "epoch-a"
    assert metrics["stage"] == "shadow"
    assert metrics["horizon"] == "4h"
    assert metrics["outcome_contract_version"] == "v1"
    assert metrics["source_code_commits"] == ["test-commit"]


def test_shadow_integrity_readiness_does_not_wait_for_promotion_window():
    hypothesis, experiment = _experiment(
        min_samples=50, min_days=7, stage="shadow",
    )
    for index in range(10):
        for assignment in ("treatment", "control"):
            candidate = f"integrity-{assignment}-{index}"
            _exposure(
                experiment,
                hypothesis,
                candidate=candidate,
                assignment=assignment,
                exposed_at=_iso(),
                authority_applied=False,
                authority_reason="experiment_stage_not_paper",
            )
            _outcome(candidate, net=0.1 if assignment == "treatment" else 0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "insufficient_data"
    assert verdict.reason == "trial_integrity_ready_no_actual_intervention"
    assert verdict.unique_treatment_n == 10
    assert verdict.unique_control_n == 10
    assert metrics["market_day_coverage"]["current_consecutive_market_days"] == 1
    assert metrics["market_day_coverage"]["status"] == "gap"
    assert metrics["harm_state"] == "indeterminate"
    assert metrics["actual_intervention_treatment_n"] == 0
    assert metrics["trial_integrity_readiness"] == {
        "contract_version": "ufc-shadow-trial-integrity/v1",
        "required_per_arm": 10,
        "required_market_days": 1,
        "qualified_treatment_n": 10,
        "qualified_control_n": 10,
        "current_consecutive_market_days": 1,
        "status": "ready",
    }


def test_shadow_control_provenance_violation_is_invalid_before_outcome_settlement():
    hypothesis, experiment = _experiment(
        min_samples=50, min_days=7, stage="shadow",
    )
    _exposure(
        experiment,
        hypothesis,
        candidate="shadow-control-provenance-invalid",
        assignment="control",
        authority_applied=False,
        behavior_changed=False,
        authority_reason="experiment_not_active",
    )

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert verdict.reason == (
        "invalid evidence: shadow_authority_provenance_invalid"
    )
    assert metrics["invalid_reasons"] == {
        "shadow_authority_provenance_invalid": 1,
    }
    assert metrics["exclusion_reasons"] == {}


def test_shadow_integrity_readiness_requires_ten_complete_units_per_arm():
    hypothesis, experiment = _experiment(
        min_samples=50, min_days=7, stage="shadow",
    )
    for assignment, count in (("treatment", 10), ("control", 9)):
        for index in range(count):
            candidate = f"underpowered-{assignment}-{index}"
            _exposure(
                experiment,
                hypothesis,
                candidate=candidate,
                assignment=assignment,
                exposed_at=_iso(),
                authority_applied=False,
                authority_reason="experiment_stage_not_paper",
            )
            _outcome(candidate, net=0.0)

    verdict = _run(experiment.experiment_id)[0]

    assert verdict.verdict == "insufficient_data"
    assert verdict.reason == "insufficient unique prospective treatment/control samples"


def test_baseline_treatment_units_do_not_satisfy_actual_intervention_gate():
    hypothesis, experiment = _experiment(min_samples=2, stage="paper_10")
    for candidate, applied, net, reason in (
        ("actual-treatment", True, 1.0, "current_stage_measurement_healthy"),
        ("baseline-treatment-a", False, 50.0, "harm_gate_indeterminate"),
        ("baseline-treatment-b", False, 60.0, "runtime_paused"),
    ):
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment="treatment",
            authority_applied=applied,
            authority_reason=reason,
        )
        _outcome(candidate, net=net)
    for index in range(2):
        candidate = f"actual-gate-control-{index}"
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment="control",
            authority_applied=False,
            authority_reason="current_stage_measurement_healthy",
        )
        _outcome(candidate, net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "insufficient_data"
    assert verdict.reason == "insufficient_actual_intervention_samples"
    assert verdict.unique_treatment_n == 3
    assert verdict.unique_control_n == 2
    assert verdict.observed == pytest.approx(37.0)
    assert metrics["assigned_treatment_n"] == 3
    assert metrics["authority_applied_treatment_n"] == 1
    assert metrics["actual_intervention_treatment_n"] == 1
    assert metrics["baseline_treatment_n"] == 2
    assert metrics["actual_intervention_state"] == "partial"
    assert metrics["actual_intervention_reason"] == (
        "insufficient_actual_intervention_samples"
    )
    assert metrics["treatment_authority_rejection_reasons"] == {
        "harm_gate_indeterminate": 1,
        "runtime_paused": 1,
    }


def test_pending_outcomes_preserve_exposed_intervention_units():
    hypothesis, experiment = _experiment(min_samples=1, stage="paper_10")
    for candidate in ("pending-treatment-a", "pending-treatment-b"):
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment="treatment",
            assignment_unit_key="pending-treatment-cluster",
            authority_applied=True,
            authority_reason="current_stage_measurement_healthy",
        )
        _filter_outcome(candidate, status="pending", net=None)
    _exposure(
        experiment,
        hypothesis,
        candidate="pending-control",
        assignment="control",
        assignment_unit_key="pending-control-cluster",
        authority_applied=False,
        authority_reason="current_stage_measurement_healthy",
    )
    _filter_outcome("pending-control", status="pending", net=None)
    _exposure(
        experiment,
        hypothesis,
        candidate="future-control",
        assignment="control",
        assignment_unit_key="future-control-cluster",
        authority_applied=False,
        authority_reason="current_stage_measurement_healthy",
    )
    _filter_outcome(
        "future-control",
        entry_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "insufficient_data"
    assert verdict.unique_treatment_n == 0
    assert verdict.unique_control_n == 0
    assert metrics["actual_intervention_treatment_n"] == 0
    assert metrics["exposed_treatment_assignment_unit_n"] == 1
    assert metrics["exposed_authority_applied_treatment_unit_n"] == 1
    assert metrics["exposed_actual_intervention_treatment_unit_n"] == 1
    assert metrics["exposed_control_assignment_unit_n"] == 2
    assert metrics["exclusion_reasons"]["unsettled"] == 3
    assert metrics["exclusion_reasons"]["settlement_after_as_of"] == 1


def test_exposed_units_reject_invalid_control_and_cross_arm_provenance():
    hypothesis, experiment = _experiment(min_samples=1, stage="paper_10")
    _exposure(
        experiment,
        hypothesis,
        candidate="invalid-control",
        assignment="control",
        authority_applied=True,
    )
    _filter_outcome("invalid-control", status="pending", net=None)
    for assignment in ("treatment", "control"):
        candidate = f"cross-arm-{assignment}"
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment=assignment,
            assignment_unit_key="cross-arm-cluster",
            authority_applied=assignment == "treatment",
        )
        _filter_outcome(candidate, status="pending", net=None)

    metrics = _run(experiment.experiment_id)[0].harm_metrics

    assert metrics["exposed_treatment_assignment_unit_n"] == 0
    assert metrics["exposed_authority_applied_treatment_unit_n"] == 0
    assert metrics["exposed_actual_intervention_treatment_unit_n"] == 0
    assert metrics["exposed_control_assignment_unit_n"] == 0
    assert metrics["invalid_reasons"]["conflicting_assignment_unit"] == 1


def test_primary_effect_remains_intent_to_treat_with_baseline_fallback():
    hypothesis, experiment = _experiment(min_samples=2, stage="paper_10")
    for candidate, applied, net, reason in (
        ("applied-treatment-a", True, 1.0, "current_stage_measurement_healthy"),
        ("applied-treatment-b", True, 1.2, "current_stage_measurement_healthy"),
        ("excluded-baseline-treatment", False, -100.0, "runtime_paused"),
    ):
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment="treatment",
            authority_applied=applied,
            authority_reason=reason,
        )
        _outcome(candidate, net=net)
    for index in range(2):
        candidate = f"effect-control-{index}"
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment="control",
            authority_applied=False,
            authority_reason="current_stage_measurement_healthy",
        )
        _outcome(candidate, net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "negative"
    assert verdict.unique_treatment_n == 3
    assert verdict.observed == pytest.approx((1.0 + 1.2 - 100.0) / 3)
    assert verdict.delta == pytest.approx((1.0 + 1.2 - 100.0) / 3)
    assert metrics["assigned_treatment_n"] == 3
    assert metrics["actual_intervention_treatment_n"] == 2
    assert metrics["baseline_treatment_n"] == 1
    assert metrics["actual_intervention_state"] == "partial"
    assert metrics["actual_intervention_reason"] == (
        "sufficient_actual_intervention_samples"
    )
    assert metrics["treatment_authority_rejection_reasons"] == {
        "runtime_paused": 1,
    }


def test_required_effective_interventions_can_be_positive_with_one_noop_in_itt():
    hypothesis, experiment = _experiment(min_samples=50, stage="paper_10")
    for index in range(50):
        candidate = f"effective-treatment-{index}"
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment="treatment",
            authority_applied=True,
            authority_reason="current_stage_measurement_healthy",
        )
        _outcome(candidate, net=1.0)
    _exposure(
        experiment,
        hypothesis,
        candidate="authorized-noop-treatment",
        assignment="treatment",
        authority_applied=True,
        authority_reason="current_stage_measurement_healthy",
        behavior_changed=False,
    )
    _outcome("authorized-noop-treatment", net=1.0)
    for index in range(50):
        candidate = f"qualified-control-{index}"
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment="control",
            authority_applied=False,
            authority_reason="current_stage_measurement_healthy",
        )
        _outcome(candidate, net=0.0)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

    assert verdict.verdict == "positive"
    assert verdict.unique_treatment_n == 51
    assert verdict.observed == pytest.approx(1.0)
    assert metrics["authority_applied_treatment_n"] == 51
    assert metrics["actual_intervention_treatment_n"] == 50
    assert metrics["baseline_treatment_n"] == 1
    assert metrics["actual_intervention_state"] == "partial"
    assert metrics["actual_intervention_reason"] == (
        "sufficient_actual_intervention_samples"
    )
    assert metrics["treatment_authority_rejection_reasons"] == {}
    assert metrics["treatment_non_intervention_reasons"] == {
        "no_effective_treatment_delta": 1,
    }


def test_cluster_actual_intervention_requires_any_effective_member_delta():
    hypothesis, experiment = _experiment(min_samples=1, stage="paper_10")
    for candidate, unit, changed in (
        ("all-noop-a", "all-noop-cluster", False),
        ("all-noop-b", "all-noop-cluster", False),
        ("mixed-noop", "mixed-cluster", False),
        ("mixed-effective", "mixed-cluster", True),
    ):
        _exposure(
            experiment,
            hypothesis,
            candidate=candidate,
            assignment="treatment",
            assignment_unit_key=unit,
            authority_applied=True,
            authority_reason="current_stage_measurement_healthy",
            behavior_changed=changed,
        )
        _outcome(candidate, net=1.0)


    cohort = _cohorts(experiment.experiment_id)[0]
    samples = {sample.assignment_unit_key: sample for sample in cohort.samples}

    assert cohort.invalid_reasons == {}
    assert samples["all-noop-cluster"].authority_applied is True
    assert samples["all-noop-cluster"].behavior_changed is False
    assert samples["mixed-cluster"].authority_applied is True
    assert samples["mixed-cluster"].behavior_changed is True
    assert cohort.inspection()["authority_applied_treatment_n"] == 2
    assert cohort.inspection()["actual_intervention_treatment_n"] == 1
    assert cohort.inspection()["baseline_treatment_n"] == 1
    assert cohort.inspection()["treatment_non_intervention_reasons"] == {
        "no_effective_treatment_delta": 1,
    }


def test_conflicting_behavior_change_provenance_invalidates_the_unit():
    hypothesis, experiment = _experiment(min_samples=1, stage="paper_10")
    exposure = _exposure(
        experiment,
        hypothesis,
        candidate="conflicting-behavior-change",
        assignment="treatment",
        authority_applied=True,
        behavior_changed=True,
    )
    _outcome("conflicting-behavior-change", net=1.0)
    from aletheia.storage.sqlite import get_db

    manifest = json.loads(exposure.manifest_json)
    manifest["behavior_changed"] = False
    get_db().execute(
        "UPDATE learning_exposures SET manifest_json=? WHERE exposure_id=?",
        (json.dumps(manifest), exposure.exposure_id),
    )
    get_db().commit()

    cohort = _cohorts(experiment.experiment_id)[0]

    assert cohort.samples == []
    assert cohort.invalid_reasons == {"invalid_behavior_change_provenance": 1}


def test_behavior_change_without_treatment_permission_invalidates_the_unit():
    hypothesis, experiment = _experiment(min_samples=1, stage="paper_10")
    _exposure(
        experiment,
        hypothesis,
        candidate="unauthorized-behavior-change",
        assignment="treatment",
        authority_applied=False,
        behavior_changed=True,
    )
    _outcome("unauthorized-behavior-change", net=1.0)

    cohort = _cohorts(experiment.experiment_id)[0]

    assert cohort.samples == []
    assert cohort.invalid_reasons == {"invalid_behavior_change_provenance": 1}





def test_pre_floor_outcomes_are_excluded_not_invalid():
    """Outcomes before the data-quality floor must be excluded (not corrupt the cohort)."""
    from datetime import datetime, timedelta

    from aletheia.analytics.learning_window import LEARNING_DATA_FLOOR_ISO

    hypothesis, experiment = _experiment()
    _exposure(experiment, hypothesis, candidate="pre-floor-t", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="pre-floor-c", assignment="control")

    floor_dt = datetime.fromisoformat(LEARNING_DATA_FLOOR_ISO.replace("Z", "+00:00"))
    pre_floor = (floor_dt - timedelta(hours=1)).isoformat()
    _outcome("pre-floor-t", net=1.0, entry_at=pre_floor)
    _outcome("pre-floor-c", net=0.0, entry_at=pre_floor)

    verdict = _run(experiment.experiment_id)[0]
    metrics = verdict.harm_metrics

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
    metrics = verdict.harm_metrics

    assert verdict.verdict == "invalid"
    assert metrics["invalid_reasons"]["unidentifiable_assignment_overlap"] == 1
    assert verdict.unique_treatment_n == verdict.unique_control_n == 0









def test_measurement_verdict_never_authorizes_or_changes_learning_lifecycle():
    hypothesis, experiment = _experiment(min_samples=10, min_days=1)
    for index in range(10):
        for assignment, net in (("treatment", 1.0), ("control", 0.0)):
            candidate = f"baseline-{assignment}-{index}"
            _exposure(
                experiment, hypothesis, candidate=candidate, assignment=assignment,
                exposed_at=_iso(index // 5, index % 5),
            )
            _outcome(candidate, net=net, entry_at=_iso(index // 5, index % 5 + 1))

    verdict = _run(experiment.experiment_id)[0]
    from extensions.learning.ledger import get_record

    assert verdict.verdict == "positive"
    assert get_record("learning_hypotheses", hypothesis.hypothesis_id)["status"] == hypothesis.status
    assert get_record("learning_experiments", experiment.experiment_id)["status"] == "active"





def test_unsupported_learning_path_is_not_processed_or_persisted():
    hypothesis, experiment = _experiment(path_id="totally_fake_path_id")
    _exposure(experiment, hypothesis, candidate="other-treatment", assignment="treatment")
    _exposure(experiment, hypothesis, candidate="other-control", assignment="control")
    _outcome("other-treatment", net=1.0)
    _outcome("other-control", net=0.0)
    from aletheia.storage.sqlite import get_db

    assert _run(experiment.experiment_id) == []
    assert get_db().execute("SELECT COUNT(*) FROM learning_contribution_verdicts").fetchone()[0] == 0


def test_operator_learning_bundle_is_not_measured_from_candidate_market_return():
    hypothesis, experiment = _experiment(path_id="operator_learning_bundle")
    opp_key = "opportunity:test-opp-abc123"
    _exposure(
        experiment, hypothesis,
        candidate=opp_key,
        assignment="treatment",
        outcome_key=f"{opp_key}:4h",
    )
    _filter_outcome(opp_key, net=0.05, allocation_selected=None)

    cohorts = _cohorts(experiment.experiment_id)

    assert cohorts == []


def test_frozen_assignment_domain_excludes_later_invalid_evidence():
    hypothesis, experiment = _experiment()
    for candidate, assignment, net in (("fixed-t", "treatment", 1.0), ("fixed-c", "control", 0.0)):
        _exposure(experiment, hypothesis, candidate=candidate, assignment=assignment)
        _outcome(candidate, net=net)
    fixed_units = frozenset({"fixed-t", "fixed-c"})
    from extensions.learning.verdicts import evaluate_cohort

    before = evaluate_cohort(_cohorts(experiment.experiment_id, assignment_unit_keys=fixed_units)[0])
    _exposure(experiment, hypothesis, candidate="later-invalid", assignment="treatment", contract="wrong")
    after = evaluate_cohort(_cohorts(experiment.experiment_id, assignment_unit_keys=fixed_units)[0])
    assert before == after
    assert any(cohort.invalid_reasons for cohort in _cohorts(experiment.experiment_id))
