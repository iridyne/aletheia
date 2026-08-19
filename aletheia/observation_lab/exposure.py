"""Typed Lab-side exposure fact (Issue #1072 Cluster 4 / §"Exposure fact").

The production ``learning_exposures`` table remains the canonical write
path for legacy UFC until Packet 2 rewires Main Runtime to consume
manifests. This module records the **Lab-side** view: every eligible
prospective unit — including one whose manifest resolution declined —
gets a typed row on ``lab.db::lab_exposures``. That means:

- On an ``applied=True`` treatment/control assignment: row records the
  chosen arm, payload, and behavior-change flag.
- On a fail-closed conflict: row records ``applied=False`` with the
  specific ``conflict_result`` (per Cluster 4's conflict module) so the
  operator can see why an eligible unit received the baseline.
- On not-effective / expired / malformed inputs: row records
  ``applied=False`` with the resolver's typed reason.

Missing evidence is unavailable, never zero or control by implication
(new #1072 §"Exposure fact" verbatim).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aletheia.observation_lab.manifest import ExecutionManifest


@dataclass(frozen=True)
class LabExposureFact:
    """One eligible unit's Lab-side exposure record, ready to append."""

    observation_id: str
    assignment_unit_key: str
    candidate_key: str
    assignment: str                              # 'treatment' / 'control' / 'unassigned'
    applied: bool
    behavior_changed: bool
    payload: dict[str, Any]
    reason: str
    active_manifest_set: list[str]               # manifest_hash values the resolver saw
    conflict_result: str
    manifest_id: str | None = None
    manifest_hash: str | None = None
    source_runtime_commit: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def record_lab_exposure(fact: LabExposureFact) -> str:
    """Append one exposure row. Returns the generated ``exposure_id``.

    Idempotency lives at the caller — this function is a plain append.
    The append-only invariant is enforced by ``trg_lab_exposures_no_update``
    / ``trg_lab_exposures_no_delete`` triggers in the schema. If a caller
    needs "record only once per (observation, unit, cycle)", they must
    check before calling; the table intentionally has no application-layer
    dedup key so append-only is uniform.
    """
    from aletheia.observation_lab.db import get_lab_db

    exposure_id = uuid.uuid4().hex
    get_lab_db().execute(
        "INSERT INTO lab_exposures ("
        "exposure_id, observation_id, manifest_id, manifest_hash, "
        "assignment_unit_key, candidate_key, assignment, applied, "
        "behavior_changed, payload_json, reason, active_manifest_set_json, "
        "conflict_result, exposed_at, source_runtime_commit"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            exposure_id,
            fact.observation_id,
            fact.manifest_id,
            fact.manifest_hash,
            fact.assignment_unit_key,
            fact.candidate_key,
            fact.assignment,
            1 if fact.applied else 0,
            1 if fact.behavior_changed else 0,
            _canonical_json(fact.payload),
            fact.reason,
            _canonical_json(fact.active_manifest_set),
            fact.conflict_result,
            _now(),
            fact.source_runtime_commit,
        ),
    )
    return exposure_id


def list_lab_exposures(
    *, observation_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Read Lab-side exposures for one observation or the recent global tail."""
    from aletheia.observation_lab.db import get_lab_db

    if observation_id is not None:
        rows = get_lab_db().execute(
            "SELECT * FROM lab_exposures WHERE observation_id=? "
            "ORDER BY exposed_at DESC LIMIT ?",
            (observation_id, int(limit)),
        ).fetchall()
    else:
        rows = get_lab_db().execute(
            "SELECT * FROM lab_exposures ORDER BY exposed_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def fact_from_conflict(
    *,
    observation_id: str,
    assignment_unit_key: str,
    candidate_key: str,
    manifest_set: list[ExecutionManifest],
    conflict_outcome: str,
    conflict_reason: str,
    source_runtime_commit: str | None = None,
) -> LabExposureFact:
    """Build a fail-closed exposure fact when the conflict detector rejected.

    Used by the Main Runtime seam post-Packet-2: when
    ``resolve_manifest_conflict`` returns an ``undeclared_conflict:*``
    outcome for an eligible unit, this shape records the reason without
    applying any treatment. The caller MUST call ``record_lab_exposure``
    once per rejected manifest in the set — one row per affected window
    per new #1072 §"Treatment-bearing overlap".
    """
    return LabExposureFact(
        observation_id=observation_id,
        assignment_unit_key=assignment_unit_key,
        candidate_key=candidate_key,
        assignment="unassigned",
        applied=False,
        behavior_changed=False,
        payload={},
        reason=conflict_reason,
        active_manifest_set=[m.manifest_hash for m in manifest_set],
        conflict_result=conflict_outcome,
        source_runtime_commit=source_runtime_commit,
    )
