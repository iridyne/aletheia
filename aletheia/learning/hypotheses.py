"""Canonical Learning hypothesis identity."""

from __future__ import annotations

import hashlib
import json
from typing import Any


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


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k).lower(): _normalize(v) for k, v in sorted(value.items()) if str(k).lower() not in {"summary", "title", "created_at", "updated_at"}}
    if isinstance(value, list):
        return sorted((_normalize(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(value, str):
        return value.strip().lower()
    return value
