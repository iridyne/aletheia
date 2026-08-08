"""Deterministic identity for behaviorally equivalent Learning cohorts."""

from __future__ import annotations

from dataclasses import replace


def test_every_measured_path_declares_a_behavior_contract_version():
    from aletheia.learning.registry import LEARNING_PATHS

    assert all(
        path.behavior_contract_version
        for path in LEARNING_PATHS
        if path.measurement_supported
    )


def test_behavior_contract_hash_is_deterministic_and_commit_independent(monkeypatch):
    from aletheia.learning import behavior_contracts

    path = behavior_contracts.get_learning_path("universe_family_calibration")
    first = behavior_contracts.build_behavior_contract_hash(
        path_id=path.path_id,
        policy_hash="policy-hash-a",
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="universe_family_calibration_cluster_allocation_utility_v2",
    )
    second = behavior_contracts.build_behavior_contract_hash(
        path_id=path.path_id,
        policy_hash="policy-hash-a",
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="universe_family_calibration_cluster_allocation_utility_v2",
    )

    assert first == second
    assert len(first) == 64

    monkeypatch.setattr(
        behavior_contracts,
        "get_learning_path",
        lambda _path_id: replace(path, behavior_contract_version="v2"),
    )
    assert behavior_contracts.build_behavior_contract_hash(
        path_id=path.path_id,
        policy_hash="policy-hash-a",
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="universe_family_calibration_cluster_allocation_utility_v2",
    ) != first


def test_behavior_contract_hash_rotates_with_measured_contract_inputs():
    from aletheia.learning.behavior_contracts import build_behavior_contract_hash

    common = {
        "path_id": "universe_family_calibration",
        "policy_hash": "policy-hash-a",
        "target_horizon": "4h",
        "outcome_contract_version": "v1",
        "metric_version": "universe_family_calibration_cluster_allocation_utility_v2",
    }
    baseline = build_behavior_contract_hash(**common)

    for key, value in (
        ("policy_hash", "policy-hash-b"),
        ("target_horizon", "1h"),
        ("outcome_contract_version", "v2"),
        ("metric_version", "metric-v3"),
    ):
        assert build_behavior_contract_hash(**{**common, key: value}) != baseline


def test_declared_behavior_version_rotates_the_policy_epoch(monkeypatch):
    from aletheia.learning import experiments

    current_path = experiments.get_learning_path("universe_family_calibration")
    before = experiments._policy_epoch_definition(current_path.path_id)
    get_path = experiments.get_learning_path
    monkeypatch.setattr(
        experiments,
        "get_learning_path",
        lambda path_id: replace(
            get_path(path_id), behavior_contract_version="universe-family-calibration/v8"
        ) if path_id == current_path.path_id else get_path(path_id),
    )

    after = experiments._policy_epoch_definition(current_path.path_id)

    assert after.epoch_hash != before.epoch_hash


def test_behavior_hash_is_reproducible_across_distinct_epoch_ids():
    from aletheia.learning.behavior_contracts import current_behavior_contract_hash
    from aletheia.learning.ledger import insert_record
    from aletheia.learning.models import LearningPolicyEpoch
    from aletheia.storage.sqlite import get_db

    policy_hash = "a" * 64
    common = {
        "path_id": "universe_family_calibration",
        "target_horizon": "4h",
        "outcome_contract_version": "v1",
        "metric_version": "universe_family_calibration_cluster_allocation_utility_v2",
    }
    hashes = []
    for index, epoch_id in enumerate(("epoch-instance-a", "epoch-instance-b")):
        started_at = f"2026-07-30T00:00:0{index}+00:00"
        assert insert_record("learning_policy_epochs", LearningPolicyEpoch(
            epoch_id=epoch_id,
            epoch_kind="decision_policy:universe_family_calibration",
            effective_values_hash=policy_hash,
            effective_values_json="{}",
            affected_path_ids_json='["universe_family_calibration"]',
            started_at=started_at,
        ))
        hashes.append(current_behavior_contract_hash(policy_epoch=epoch_id, **common))
        get_db().execute(
            "UPDATE learning_policy_epochs SET ended_at=? WHERE epoch_id=?",
            (started_at, epoch_id),
        )
        get_db().commit()

    assert hashes[0] == hashes[1]


def test_policy_epoch_from_another_path_cannot_define_behavior_identity():
    import pytest

    from aletheia.learning.behavior_contracts import current_behavior_contract_hash
    from aletheia.learning.ledger import insert_record
    from aletheia.learning.models import LearningPolicyEpoch

    assert insert_record("learning_policy_epochs", LearningPolicyEpoch(
        epoch_id="wrong-path-epoch",
        epoch_kind="decision_policy:operator_learning_bundle",
        effective_values_hash="b" * 64,
        effective_values_json="{}",
        affected_path_ids_json='["operator_learning_bundle"]',
        started_at="2026-07-30T00:00:00+00:00",
    ))

    with pytest.raises(ValueError, match="policy epoch unavailable"):
        current_behavior_contract_hash(
            path_id="universe_family_calibration",
            policy_epoch="wrong-path-epoch",
            target_horizon="4h",
            outcome_contract_version="v1",
            metric_version="universe_family_calibration_cluster_allocation_utility_v2",
        )
