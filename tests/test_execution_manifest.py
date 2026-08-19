"""ExecutionManifest + resolver contract tests (Issue #1072 Cluster 3)."""

from __future__ import annotations

import pytest


def _sample_manifest(**overrides):
    from aletheia.observation_lab.manifest import build_manifest

    kwargs = dict(
        observation_id="ufc:exp-1:epoch-1:0",
        generation=1,
        spec_hash="abc123",
        treatment_adapter_id="universe_family_calibration",
        treatment_adapter_version="v11",
        assignment_policy="stable_assignment_by_key",
        assignment_salt="deadbeef",
        assignment_unit_key_template="universe:cluster:{scoring_cohort}:{family}:{direction}:{market_type}",
        allocation_pct=0.5,
        control_payload={"multiplier": 1.0},
        treatment_payload={"multiplier": 1.1},
        eligible_population={"path_id": "universe_family_calibration"},
        behavior_contract_hash="behavior-hash-1",
        interference_key="universe:momentum:long:perp",
        composability_contract="exclusive",
        effective_from="2026-08-19T00:00:00+00:00",
        deterministic_baseline={"multiplier": 1.0},
        unavailable_fallback="baseline",
    )
    kwargs.update(overrides)
    return build_manifest(**kwargs)


def test_build_manifest_hash_is_content_addressed():
    m1 = _sample_manifest()
    m2 = _sample_manifest()
    # Same inputs → same hash, even though manifest_id/created_at differ.
    assert m1.manifest_hash == m2.manifest_hash
    assert m1.manifest_id != m2.manifest_id


def test_build_manifest_hash_changes_when_content_changes():
    baseline = _sample_manifest()
    with_new_payload = _sample_manifest(treatment_payload={"multiplier": 1.2})
    with_new_salt = _sample_manifest(assignment_salt="cafebabe")
    with_new_alloc = _sample_manifest(allocation_pct=0.75)

    hashes = {
        baseline.manifest_hash,
        with_new_payload.manifest_hash,
        with_new_salt.manifest_hash,
        with_new_alloc.manifest_hash,
    }
    assert len(hashes) == 4


def test_build_manifest_rejects_out_of_range_allocation():
    with pytest.raises(ValueError, match="allocation_pct"):
        _sample_manifest(allocation_pct=1.5)


def test_build_manifest_rejects_unknown_composability():
    with pytest.raises(ValueError, match="composability"):
        _sample_manifest(composability_contract="quantum_entangled")


def test_publish_manifest_is_idempotent_on_same_hash():
    from aletheia.observation_lab.manifest import publish_manifest

    manifest = _sample_manifest()
    assert publish_manifest(manifest) is True
    # Same content → no-op, returns False rather than raising.
    assert publish_manifest(manifest) is False


def test_publish_manifest_rejects_semantically_different_content_for_same_generation():
    from aletheia.observation_lab.manifest import (
        ManifestGenerationConflict,
        build_manifest,
        publish_manifest,
    )

    m1 = _sample_manifest()
    assert publish_manifest(m1) is True

    # Same observation_id + generation, different content → typed error so
    # callers can distinguish "silent no-op" from "you must bump generation".
    m2 = build_manifest(
        observation_id=m1.observation_id,
        generation=m1.generation,
        spec_hash=m1.spec_hash,
        treatment_adapter_id=m1.treatment_adapter_id,
        treatment_adapter_version=m1.treatment_adapter_version,
        assignment_policy=m1.assignment_policy,
        assignment_salt=m1.assignment_salt,
        assignment_unit_key_template=m1.assignment_unit_key_template,
        allocation_pct=m1.allocation_pct,
        control_payload=m1.control_payload,
        treatment_payload={"multiplier": 1.2},  # <-- semantic change
        eligible_population=m1.eligible_population,
        behavior_contract_hash=m1.behavior_contract_hash,
        interference_key=m1.interference_key,
        composability_contract=m1.composability_contract,
        effective_from=m1.effective_from,
        deterministic_baseline=m1.deterministic_baseline,
        unavailable_fallback=m1.unavailable_fallback,
    )
    with pytest.raises(ManifestGenerationConflict):
        publish_manifest(m2)


def test_load_active_manifests_respects_effective_interval():
    from aletheia.observation_lab.manifest import load_active_manifests, publish_manifest

    active = _sample_manifest(
        observation_id="active-1",
        effective_from="2026-01-01T00:00:00+00:00",
        effective_until="2027-01-01T00:00:00+00:00",
    )
    future = _sample_manifest(
        observation_id="future-1",
        effective_from="2099-01-01T00:00:00+00:00",
    )
    expired = _sample_manifest(
        observation_id="expired-1",
        effective_from="2020-01-01T00:00:00+00:00",
        effective_until="2020-12-31T00:00:00+00:00",
    )
    for m in (active, future, expired):
        publish_manifest(m)

    now = "2026-06-01T00:00:00+00:00"
    ids = {m.observation_id for m in load_active_manifests(now=now)}
    assert ids == {"active-1"}


def test_resolve_treatment_is_deterministic_and_arms_split_at_allocation():
    from aletheia.observation_lab.manifest import resolve_treatment

    manifest = _sample_manifest(allocation_pct=0.5)

    # Same inputs, two calls → same output.
    r1 = resolve_treatment(
        manifest=manifest,
        candidate_key="opportunity:a",
        assignment_key="universe:cluster:x",
        now="2026-08-19T12:00:00+00:00",
    )
    r2 = resolve_treatment(
        manifest=manifest,
        candidate_key="opportunity:a",
        assignment_key="universe:cluster:x",
        now="2026-08-19T13:00:00+00:00",
    )
    assert r1.assignment == r2.assignment
    assert r1.payload == r2.payload

    # A batch of many distinct keys splits roughly at allocation.
    assignments = [
        resolve_treatment(
            manifest=manifest,
            candidate_key=f"opportunity:{i}",
            assignment_key=f"cluster:{i}",
            now="2026-08-19T12:00:00+00:00",
        ).assignment
        for i in range(400)
    ]
    treatment_count = sum(1 for a in assignments if a == "treatment")
    # 400 * 0.5 = 200; loose ±15% band around the true rate.
    assert 170 <= treatment_count <= 230


def test_resolve_treatment_not_effective_returns_baseline():
    from aletheia.observation_lab.manifest import resolve_treatment

    manifest = _sample_manifest(effective_from="2099-01-01T00:00:00+00:00")
    r = resolve_treatment(
        manifest=manifest,
        candidate_key="c",
        assignment_key="k",
        now="2026-08-19T00:00:00+00:00",
    )
    assert r.applied is False
    assert r.assignment == "unassigned"
    assert r.reason == "not_effective_yet"
    assert r.payload == manifest.deterministic_baseline


def test_resolve_treatment_expired_returns_baseline():
    from aletheia.observation_lab.manifest import resolve_treatment

    manifest = _sample_manifest(
        effective_from="2020-01-01T00:00:00+00:00",
        effective_until="2020-06-01T00:00:00+00:00",
    )
    r = resolve_treatment(
        manifest=manifest,
        candidate_key="c",
        assignment_key="k",
        now="2026-08-19T00:00:00+00:00",
    )
    assert r.applied is False
    assert r.reason == "effective_window_expired"


def test_resolve_treatment_rejects_malformed_input():
    from aletheia.observation_lab.manifest import resolve_treatment

    manifest = _sample_manifest()
    r = resolve_treatment(
        manifest=manifest,
        candidate_key="",
        assignment_key="k",
        now="2026-08-19T12:00:00+00:00",
    )
    assert r.applied is False
    assert r.reason == "malformed_input"
