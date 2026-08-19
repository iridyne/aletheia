"""ExecutionManifest — the frozen Lab→Main Runtime treatment contract (#1072 Cluster 3).

Purpose (per new #1072 §"Minimal contracts / ExecutionManifest"):

Main Runtime receives only the minimum execution contract for a treatment-
bearing window: adapter id/version, assignment policy + salt + unit-key
template, payloads, eligibility, interference key, effective interval,
fallback, admission ref, and a manifest hash. Sample thresholds, fixed-look
state, and verdict semantics stay in Lab.

## Boundary properties

- **Immutable once published.** A publish is INSERT-only against
  ``execution_manifests``. Semantically different manifest for the same
  ``observation_id`` = new ``generation``. ``manifest_hash`` is UNIQUE
  across the whole table — republishing the same content is a no-op, not
  an error.
- **Hash is content-addressed.** ``manifest_hash = sha256(canonical json of
  everything except manifest_id / created_at)``. Two identical manifests in
  different processes get the same hash — that's the load-bearing property
  that lets Main Runtime prove it's applying the exact contract Lab issued.
- **Deterministic assignment is the ONLY thing Main Runtime touches.** The
  resolver here is a pure function: same ``(manifest, candidate_key,
  assignment_key)`` triple always yields the same arm and payload. No IO,
  no clock, no config.
- **Fail-closed semantics.** Missing / stale / not-yet-effective /
  expired-effective manifest → resolver returns ``ManifestResolution(
  applied=False, reason=<typed>)`` and the caller must apply
  ``deterministic_baseline``. The resolver never guesses.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


_HASH_EXCLUDED_FIELDS = frozenset({"manifest_id", "created_at"})


@dataclass(frozen=True)
class ExecutionManifest:
    """Frozen treatment contract Main Runtime consumes for one Observation.

    Field order/naming matches the ``execution_manifests`` table 1:1 so the
    dataclass ↔ row conversion is mechanical.
    """
    manifest_id: str
    observation_id: str
    generation: int
    spec_hash: str
    manifest_hash: str
    treatment_adapter_id: str
    treatment_adapter_version: str
    assignment_policy: str
    assignment_salt: str
    assignment_unit_key_template: str
    allocation_pct: float
    control_payload: dict[str, Any]
    treatment_payload: dict[str, Any]
    eligible_population: dict[str, Any]
    behavior_contract_hash: str
    interference_key: str
    composability_contract: str
    effective_from: str
    effective_until: str | None
    deterministic_baseline: dict[str, Any]
    unavailable_fallback: str
    owner_admission_ref: str | None
    created_at: str

    def to_row(self) -> dict[str, Any]:
        """Flat dict shaped for SQL binding. JSON-encodes container fields."""
        return {
            "manifest_id": self.manifest_id,
            "observation_id": self.observation_id,
            "generation": int(self.generation),
            "spec_hash": self.spec_hash,
            "manifest_hash": self.manifest_hash,
            "treatment_adapter_id": self.treatment_adapter_id,
            "treatment_adapter_version": self.treatment_adapter_version,
            "assignment_policy": self.assignment_policy,
            "assignment_salt": self.assignment_salt,
            "assignment_unit_key_template": self.assignment_unit_key_template,
            "allocation_pct": float(self.allocation_pct),
            "control_payload_json": _canonical_json(self.control_payload),
            "treatment_payload_json": _canonical_json(self.treatment_payload),
            "eligible_population_json": _canonical_json(self.eligible_population),
            "behavior_contract_hash": self.behavior_contract_hash,
            "interference_key": self.interference_key,
            "composability_contract": self.composability_contract,
            "effective_from": self.effective_from,
            "effective_until": self.effective_until,
            "deterministic_baseline_json": _canonical_json(self.deterministic_baseline),
            "unavailable_fallback": self.unavailable_fallback,
            "owner_admission_ref": self.owner_admission_ref,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ManifestResolution:
    """Output of ``resolve_treatment`` — path-agnostic single-unit decision."""
    applied: bool
    assignment: str                    # 'treatment' / 'control' / 'unassigned'
    manifest_id: str | None            # None when applied=False due to no-manifest
    manifest_hash: str | None
    payload: dict[str, Any]            # the value the resolver decided to apply
    reason: str
    behavior_changed: bool             # True iff assignment=='treatment' AND payload != baseline


def _canonical_json(value: Any) -> str:
    """Same shape used by ``hypothesis_fingerprint`` — sort keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _compute_manifest_hash(payload: dict[str, Any]) -> str:
    material = {k: v for k, v in payload.items() if k not in _HASH_EXCLUDED_FIELDS}
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def build_manifest(
    *,
    observation_id: str,
    generation: int,
    spec_hash: str,
    treatment_adapter_id: str,
    treatment_adapter_version: str,
    assignment_policy: str,
    assignment_salt: str,
    assignment_unit_key_template: str,
    allocation_pct: float,
    control_payload: dict[str, Any],
    treatment_payload: dict[str, Any],
    eligible_population: dict[str, Any],
    behavior_contract_hash: str,
    interference_key: str,
    composability_contract: str,
    effective_from: str,
    deterministic_baseline: dict[str, Any],
    unavailable_fallback: str,
    effective_until: str | None = None,
    owner_admission_ref: str | None = None,
) -> ExecutionManifest:
    """Construct an ExecutionManifest with a content-addressed hash.

    Two build calls with identical inputs produce identical ``manifest_hash``
    (up to ``manifest_id`` / ``created_at`` which are hash-excluded). Publish
    is idempotent on the hash.
    """
    if not 0.0 <= float(allocation_pct) <= 1.0:
        raise ValueError(f"allocation_pct out of range: {allocation_pct}")
    if composability_contract not in {"exclusive", "disjoint_population", "factorial"}:
        raise ValueError(f"unknown composability_contract: {composability_contract}")
    if unavailable_fallback not in {"baseline", "exclude"}:
        raise ValueError(f"unknown unavailable_fallback: {unavailable_fallback}")

    fields = {
        "manifest_id": uuid.uuid4().hex,
        "observation_id": observation_id,
        "generation": int(generation),
        "spec_hash": spec_hash,
        "manifest_hash": "",  # filled below
        "treatment_adapter_id": treatment_adapter_id,
        "treatment_adapter_version": treatment_adapter_version,
        "assignment_policy": assignment_policy,
        "assignment_salt": assignment_salt,
        "assignment_unit_key_template": assignment_unit_key_template,
        "allocation_pct": float(allocation_pct),
        "control_payload": dict(control_payload),
        "treatment_payload": dict(treatment_payload),
        "eligible_population": dict(eligible_population),
        "behavior_contract_hash": behavior_contract_hash,
        "interference_key": interference_key,
        "composability_contract": composability_contract,
        "effective_from": effective_from,
        "effective_until": effective_until,
        "deterministic_baseline": dict(deterministic_baseline),
        "unavailable_fallback": unavailable_fallback,
        "owner_admission_ref": owner_admission_ref,
        "created_at": _now(),
    }
    manifest_hash = _compute_manifest_hash(fields)
    fields["manifest_hash"] = manifest_hash
    return ExecutionManifest(**fields)


class ManifestGenerationConflict(ValueError):
    """Different content published for the same (observation_id, generation)."""


def publish_manifest(manifest: ExecutionManifest) -> bool:
    """Idempotently persist one manifest to lab.db.

    Returns True on first publish, False when the same ``manifest_hash``
    already exists (semantically identical content — silent no-op). Raises
    ``ManifestGenerationConflict`` when a **different** manifest already
    occupies the same ``(observation_id, generation)`` — the caller must
    bump ``generation`` for a semantic change.
    """
    from aletheia.observation_lab.db import get_lab_db

    db = get_lab_db()
    existing = db.execute(
        "SELECT manifest_hash FROM execution_manifests "
        "WHERE observation_id=? AND generation=?",
        (manifest.observation_id, int(manifest.generation)),
    ).fetchone()
    if existing is not None:
        if existing["manifest_hash"] == manifest.manifest_hash:
            return False  # Same content — silent no-op.
        raise ManifestGenerationConflict(
            f"observation {manifest.observation_id} generation "
            f"{manifest.generation} already published with a different hash"
        )
    row = manifest.to_row()
    cols = ", ".join(row.keys())
    placeholders = ", ".join(["?"] * len(row))
    db.execute(
        f"INSERT INTO execution_manifests ({cols}) VALUES ({placeholders})",
        list(row.values()),
    )
    return True


def load_active_manifests(
    *,
    now: str | None = None,
    interference_key: str | None = None,
) -> list[ExecutionManifest]:
    """Return manifests whose effective interval covers ``now``.

    ``interference_key`` optionally restricts the read to one population
    boundary — Main Runtime uses this per candidate to construct the
    "complete active manifest set for an eligible unit" that Cluster 4's
    conflict detector inspects.
    """
    from aletheia.observation_lab.db import get_lab_db

    when = now or _now()
    params: list[Any] = [when, when]
    clauses = [
        "datetime(effective_from) <= datetime(?)",
        "(effective_until IS NULL OR datetime(effective_until) > datetime(?))",
    ]
    if interference_key is not None:
        clauses.append("interference_key = ?")
        params.append(interference_key)
    rows = get_lab_db().execute(
        f"SELECT * FROM execution_manifests WHERE {' AND '.join(clauses)} "
        "ORDER BY datetime(effective_from), manifest_id",
        params,
    ).fetchall()
    return [_row_to_manifest(dict(row)) for row in rows]


def _row_to_manifest(row: dict[str, Any]) -> ExecutionManifest:
    return ExecutionManifest(
        manifest_id=row["manifest_id"],
        observation_id=row["observation_id"],
        generation=int(row["generation"]),
        spec_hash=row["spec_hash"],
        manifest_hash=row["manifest_hash"],
        treatment_adapter_id=row["treatment_adapter_id"],
        treatment_adapter_version=row["treatment_adapter_version"],
        assignment_policy=row["assignment_policy"],
        assignment_salt=row["assignment_salt"],
        assignment_unit_key_template=row["assignment_unit_key_template"],
        allocation_pct=float(row["allocation_pct"]),
        control_payload=json.loads(row["control_payload_json"]),
        treatment_payload=json.loads(row["treatment_payload_json"]),
        eligible_population=json.loads(row["eligible_population_json"]),
        behavior_contract_hash=row["behavior_contract_hash"],
        interference_key=row["interference_key"],
        composability_contract=row["composability_contract"],
        effective_from=row["effective_from"],
        effective_until=row.get("effective_until"),
        deterministic_baseline=json.loads(row["deterministic_baseline_json"]),
        unavailable_fallback=row["unavailable_fallback"],
        owner_admission_ref=row.get("owner_admission_ref"),
        created_at=row["created_at"],
    )


def resolve_treatment(
    *,
    manifest: ExecutionManifest,
    candidate_key: str,
    assignment_key: str,
    now: str | None = None,
) -> ManifestResolution:
    """Path-agnostic deterministic single-unit resolver.

    Contract:
    - Applied within ``[effective_from, effective_until)`` → deterministic
      arm via ``sha256(experiment_id + salt + assignment_key)`` split at
      ``allocation_pct``. Payload = treatment/control per arm.
    - Outside effective window → ``applied=False``, ``reason="not_effective"``.
    - Malformed inputs → ``applied=False``, ``reason="malformed_input"``.

    Callers (Main Runtime seam) must persist an exposure fact even when
    ``applied=False`` — that's the "unavailable / exclusion reason" the
    exposure schema in Cluster 4 records for audit.
    """
    when = now or _now()
    if datetime.fromisoformat(when) < datetime.fromisoformat(manifest.effective_from):
        return ManifestResolution(
            applied=False,
            assignment="unassigned",
            manifest_id=manifest.manifest_id,
            manifest_hash=manifest.manifest_hash,
            payload=dict(manifest.deterministic_baseline),
            reason="not_effective_yet",
            behavior_changed=False,
        )
    if manifest.effective_until is not None:
        if datetime.fromisoformat(when) >= datetime.fromisoformat(manifest.effective_until):
            return ManifestResolution(
                applied=False,
                assignment="unassigned",
                manifest_id=manifest.manifest_id,
                manifest_hash=manifest.manifest_hash,
                payload=dict(manifest.deterministic_baseline),
                reason="effective_window_expired",
                behavior_changed=False,
            )

    if not candidate_key or not assignment_key:
        return ManifestResolution(
            applied=False,
            assignment="unassigned",
            manifest_id=manifest.manifest_id,
            manifest_hash=manifest.manifest_hash,
            payload=dict(manifest.deterministic_baseline),
            reason="malformed_input",
            behavior_changed=False,
        )

    material = f"{manifest.observation_id}:{manifest.assignment_salt}:{assignment_key}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    # Same bucketing scheme learning.experiments.stable_assignment uses so
    # future Lab-native cohorts assign identically to legacy Learning cohorts
    # when the salt is preserved.
    fraction = int(digest[:16], 16) / float(1 << 64)
    assignment = "treatment" if fraction < manifest.allocation_pct else "control"

    payload = (
        dict(manifest.treatment_payload)
        if assignment == "treatment"
        else dict(manifest.control_payload)
    )
    behavior_changed = (
        assignment == "treatment"
        and payload != manifest.deterministic_baseline
        and payload != manifest.control_payload
    )
    return ManifestResolution(
        applied=True,
        assignment=assignment,
        manifest_id=manifest.manifest_id,
        manifest_hash=manifest.manifest_hash,
        payload=payload,
        reason=f"applied:{assignment}",
        behavior_changed=behavior_changed,
    )
