"""Canonical Learning hypothesis identity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aletheia.learning.models import LearningHypothesis


HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION = 1


def hypothesis_fingerprint(
    *, path_id: str, scope: dict[str, Any], action: dict[str, Any],
    target_horizon: str, source_cohort: str,
    behavior_contract_version: str | None = None,
    schema_version: int = HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION,
) -> str:
    payload = {
        "path_id": path_id.lower(),
        "scope": _normalize(scope),
        "action": _normalize(action),
        "target_horizon": target_horizon.lower(),
        "source_cohort": source_cohort.lower(),
        "schema_version": schema_version,
    }
    if behavior_contract_version is not None:
        payload["behavior_contract_version"] = behavior_contract_version.lower()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def nominate_learning_hypothesis(
    *,
    path_id: str,
    scope: dict[str, Any],
    action: dict[str, Any],
    source_type: str,
    source_artifact_ids: Iterable[str],
    source_cohort: str,
    consumer: str,
    target_horizon: str,
    evidence_summary: dict[str, Any] | None = None,
) -> LearningHypothesis:
    """Persist one scoped hypothesis candidate without opening authority state."""
    from aletheia.learning.ledger import (
        get_hypothesis_by_fingerprint,
        insert_hypothesis,
    )
    from aletheia.learning.models import LearningHypothesis
    from aletheia.learning.registry import get_learning_path

    path_id = str(path_id).strip()
    source_type = str(source_type).strip()
    source_cohort = str(source_cohort).strip()
    consumer = str(consumer).strip()
    target_horizon = str(target_horizon).strip()
    if not path_id or not source_type or not source_cohort or not consumer or not target_horizon:
        raise ValueError("hypothesis nomination identity is incomplete")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("hypothesis nomination scope is required")
    if not isinstance(action, dict) or not action:
        raise ValueError("hypothesis nomination action is required")
    artifact_ids = list(dict.fromkeys(
        str(value).strip()
        for value in source_artifact_ids
        if str(value).strip()
    ))
    if not artifact_ids:
        raise ValueError("hypothesis nomination source artifact is required")

    path = get_learning_path(path_id)
    if not path.is_learning_treatment:
        raise ValueError(f"hypothesis path is not a Learning treatment: {path_id}")
    fingerprint = hypothesis_fingerprint(
        path_id=path_id,
        scope=scope,
        action=action,
        target_horizon=target_horizon,
        source_cohort=source_cohort,
        behavior_contract_version=path.behavior_contract_version,
    )
    existing = get_hypothesis_by_fingerprint(fingerprint)
    if existing is not None:
        return LearningHypothesis.model_validate(existing)

    hypothesis = LearningHypothesis(
        fingerprint=fingerprint,
        behavior_contract_version=path.behavior_contract_version,
        fingerprint_schema_version=HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION,
        cohort_id=hashlib.sha256(source_cohort.encode("utf-8")).hexdigest()[:16],
        path_id=path_id,
        overlap_group=path.overlap_group,
        source_type=source_type,
        source_artifact_ids_json=json.dumps(artifact_ids, ensure_ascii=True),
        scope_json=json.dumps(_normalize(scope), sort_keys=True, separators=(",", ":")),
        action_json=json.dumps(_normalize(action), sort_keys=True, separators=(",", ":")),
        consumer=consumer,
        target_horizon=target_horizon,
        status="proposed",
        evidence_summary_json=json.dumps(
            _normalize(evidence_summary or {}),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    insert_hypothesis(hypothesis)
    persisted = get_hypothesis_by_fingerprint(fingerprint)
    if persisted is None:
        raise RuntimeError("learning hypothesis nomination was not persisted")
    return LearningHypothesis.model_validate(persisted)


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k).lower(): _normalize(v) for k, v in sorted(value.items()) if str(k).lower() not in {"summary", "title", "created_at", "updated_at"}}
    if isinstance(value, list):
        return sorted((_normalize(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(value, str):
        return value.strip().lower()
    return value
