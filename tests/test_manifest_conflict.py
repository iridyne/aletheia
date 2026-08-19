"""Manifest-set conflict resolution tests (Issue #1072 Cluster 4).

The conflict resolver is the safety gate new #1072 §"Treatment-bearing
overlap" mandates: undeclared conflict MUST fail closed, no last-write-wins,
no priority tiebreak.
"""

from __future__ import annotations

import pytest


def _make_manifest(**overrides):
    from aletheia.observation_lab.manifest import build_manifest

    kwargs = dict(
        observation_id="obs-x",
        generation=1,
        spec_hash="s",
        treatment_adapter_id="t",
        treatment_adapter_version="v1",
        assignment_policy="stable",
        assignment_salt="salt",
        assignment_unit_key_template="{cluster}",
        allocation_pct=0.5,
        control_payload={"m": 1.0},
        treatment_payload={"m": 1.1},
        eligible_population={"path_id": "test"},
        behavior_contract_hash="bh",
        interference_key="ifk",
        composability_contract="exclusive",
        effective_from="2026-01-01T00:00:00+00:00",
        deterministic_baseline={"m": 1.0},
        unavailable_fallback="baseline",
    )
    kwargs.update(overrides)
    return build_manifest(**kwargs)


def test_no_active_manifest_yields_no_manifest_outcome():
    from aletheia.observation_lab.conflict import resolve_manifest_conflict

    r = resolve_manifest_conflict([])
    assert r.outcome == "no_manifest"
    assert r.admitted == () and r.rejected == ()


def test_single_manifest_is_admitted():
    from aletheia.observation_lab.conflict import resolve_manifest_conflict

    m = _make_manifest(observation_id="only-one")
    r = resolve_manifest_conflict([m])
    assert r.outcome == "single_manifest"
    assert r.admitted == (m,)
    assert r.rejected == ()


def test_two_exclusive_manifests_fail_closed():
    """Load-bearing invariant: exclusive vs. anything = undeclared conflict."""
    from aletheia.observation_lab.conflict import resolve_manifest_conflict

    m1 = _make_manifest(observation_id="obs-a", assignment_salt="a")
    m2 = _make_manifest(observation_id="obs-b", assignment_salt="b")

    r = resolve_manifest_conflict([m1, m2])
    assert r.outcome == "undeclared_conflict:exclusive"
    assert {m.manifest_hash for m in r.rejected} == {m1.manifest_hash, m2.manifest_hash}
    assert r.admitted == ()


def test_mixed_composability_fails_closed():
    """Heterogeneous contract set is undeclared — no arbitrary priority."""
    from aletheia.observation_lab.conflict import resolve_manifest_conflict

    m1 = _make_manifest(
        observation_id="obs-a",
        assignment_salt="a",
        composability_contract="disjoint_population",
        eligible_population={"path_id": "test", "population_key": "A"},
    )
    m2 = _make_manifest(
        observation_id="obs-b",
        assignment_salt="b",
        composability_contract="factorial",
    )
    r = resolve_manifest_conflict([m1, m2])
    assert r.outcome == "undeclared_conflict:mixed_composability"
    assert {m.manifest_hash for m in r.rejected} == {m1.manifest_hash, m2.manifest_hash}


def test_disjoint_populations_with_distinct_keys_are_admitted():
    from aletheia.observation_lab.conflict import resolve_manifest_conflict

    m1 = _make_manifest(
        observation_id="obs-a",
        assignment_salt="a",
        composability_contract="disjoint_population",
        eligible_population={"path_id": "test", "population_key": "high_conf"},
    )
    m2 = _make_manifest(
        observation_id="obs-b",
        assignment_salt="b",
        composability_contract="disjoint_population",
        eligible_population={"path_id": "test", "population_key": "low_conf"},
    )
    r = resolve_manifest_conflict([m1, m2])
    assert r.outcome == "disjoint_populations_ok"
    assert {m.manifest_hash for m in r.admitted} == {m1.manifest_hash, m2.manifest_hash}
    assert r.rejected == ()


def test_disjoint_declared_but_population_key_collides_fails_closed():
    from aletheia.observation_lab.conflict import resolve_manifest_conflict

    m1 = _make_manifest(
        observation_id="obs-a",
        assignment_salt="a",
        composability_contract="disjoint_population",
        eligible_population={"path_id": "test", "population_key": "same"},
    )
    m2 = _make_manifest(
        observation_id="obs-b",
        assignment_salt="b",
        composability_contract="disjoint_population",
        eligible_population={"path_id": "test", "population_key": "same"},
    )
    r = resolve_manifest_conflict([m1, m2])
    assert r.outcome == "undeclared_conflict:overlapping_population"
    assert {m.manifest_hash for m in r.rejected} == {m1.manifest_hash, m2.manifest_hash}


def test_disjoint_declared_but_missing_population_key_fails_closed():
    from aletheia.observation_lab.conflict import resolve_manifest_conflict

    m1 = _make_manifest(
        observation_id="obs-a",
        assignment_salt="a",
        composability_contract="disjoint_population",
        eligible_population={"path_id": "test"},  # no population_key
    )
    m2 = _make_manifest(
        observation_id="obs-b",
        assignment_salt="b",
        composability_contract="disjoint_population",
        eligible_population={"path_id": "test", "population_key": "b"},
    )
    r = resolve_manifest_conflict([m1, m2])
    assert r.outcome == "undeclared_conflict:overlapping_population"


def test_factorial_manifests_are_admitted():
    from aletheia.observation_lab.conflict import resolve_manifest_conflict

    m1 = _make_manifest(
        observation_id="obs-a",
        assignment_salt="a",
        composability_contract="factorial",
    )
    m2 = _make_manifest(
        observation_id="obs-b",
        assignment_salt="b",
        composability_contract="factorial",
    )
    r = resolve_manifest_conflict([m1, m2])
    assert r.outcome == "factorial_ok"
    assert {m.manifest_hash for m in r.admitted} == {m1.manifest_hash, m2.manifest_hash}


def test_record_lab_exposure_persists_row_and_rejects_update():
    """Append-only trigger fires on UPDATE."""
    import sqlite3

    from aletheia.observation_lab.exposure import (
        LabExposureFact,
        list_lab_exposures,
        record_lab_exposure,
    )
    from aletheia.observation_lab.db import get_lab_db

    fact = LabExposureFact(
        observation_id="obs-a",
        assignment_unit_key="cluster:x",
        candidate_key="opportunity:1",
        assignment="treatment",
        applied=True,
        behavior_changed=True,
        payload={"m": 1.1},
        reason="applied:treatment",
        active_manifest_set=["hash1"],
        conflict_result="single_manifest",
        manifest_id="m-1",
        manifest_hash="hash1",
    )
    exposure_id = record_lab_exposure(fact)
    rows = list_lab_exposures(observation_id="obs-a")
    assert len(rows) == 1
    assert rows[0]["exposure_id"] == exposure_id
    assert rows[0]["assignment"] == "treatment"
    assert rows[0]["applied"] == 1
    assert rows[0]["conflict_result"] == "single_manifest"

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        get_lab_db().execute(
            "UPDATE lab_exposures SET assignment='control' WHERE exposure_id=?",
            (exposure_id,),
        )


def test_fact_from_conflict_builds_a_rejected_exposure_shape():
    """Cluster 4 wiring helper: one rejected-exposure row per affected window."""
    from aletheia.observation_lab.exposure import fact_from_conflict

    m1 = _make_manifest(observation_id="obs-a", assignment_salt="a")
    m2 = _make_manifest(observation_id="obs-b", assignment_salt="b")
    fact = fact_from_conflict(
        observation_id="obs-a",
        assignment_unit_key="cluster:1",
        candidate_key="opportunity:1",
        manifest_set=[m1, m2],
        conflict_outcome="undeclared_conflict:exclusive",
        conflict_reason="exclusive_manifest_conflict:2",
    )
    assert fact.applied is False
    assert fact.assignment == "unassigned"
    assert fact.conflict_result == "undeclared_conflict:exclusive"
    assert fact.active_manifest_set == [m1.manifest_hash, m2.manifest_hash]
    assert fact.payload == {}
