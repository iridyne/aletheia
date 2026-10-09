"""Prospective, observe-only validation for replay-ready filter thresholds.

Historical replay may nominate a treatment, but every experiment starts after
registration and uses only future first exposures. Reaching an evidence gate
changes analytical status to ``ready_for_review``; production settings are
never changed here.

Three filter kinds currently register experiments:

- ``family_ttl`` — 1-D TTL treatments. Ready marker is
  ``ready_for_controlled_experiment``; the least-permissive winner is the
  smallest treatment TTL strictly greater than the control TTL.
- ``observer_pre_filter`` — 2-D (RSI × ATR) grid treatments. Ready marker is
  ``ready_for_prospective_shadow``; the least-permissive winner is the grid
  cell closest to the aggressive control (largest RSI and ATR values) still
  meeting the evidence gate.
- ``ranker_confidence_gate`` — categorical confidence treatments. The shadow
  treatment is the least-permissive lower confidence threshold with support.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Iterable

from extensions.counterfactual.filter_replay import (
    build_family_ttl_replay,
    build_observer_pre_filter_replay,
    build_ranker_confidence_replay,
    build_ranker_confidence_replay_from_frozen,
)
from extensions.observation_lab import units as lab_units


# Fallback gates when a replay does not declare its own gates in the top-level
# dict. family_ttl currently relies on these; observer_pre_filter declares its
# own stricter gates (0.80 coverage, 0.25 edge delta, 200 samples).
_MIN_COVERAGE = 0.70
_MIN_EDGE_DELTA_PCT = 0.05


# Adapter-specific causal identity vectors (Issue #1072 owner review §4).
#
# Each entry declares the semantic contract a Source Adapter actually consumes.
# Only fields listed here can trigger ``adapter_causal_identity_changed``
# invalidation — an unrelated ``filter_version`` bump in production does NOT.
# Bumping any version string here is a deliberate act that new-cohorts every
# collecting experiment; historical evaluations remain unchanged (append-only).
#
# Owner review §4: "v1 禁止通过 Git diff、路径分类、LLM 判断或全局 dependency graph
# 自动推断 comparability" — nothing here is inferred from the codebase; every
# field is a hand-declared contract.
_ADAPTER_CAUSAL_IDENTITY: dict[str, dict[str, Any]] = {
    "ranker_confidence_gate": {
        "adapter_id": "ranker_confidence_gate",
        "adapter_version": "v1",
        "cohort_semantics_version": "prospective_first_exposure/v1",
        "evaluator_version": "shrunk_mean_edge_delta/v1",
        "outcome_contract": "filter_candidate_outcomes/net_return_pct",
        "input_schema": (
            "input_snapshot.confidence",
            "parameter_snapshot.minimum",
        ),
    },
    # Phase 2 (Issue #1072 §"Phase 2 — only after POC success"): ``family_ttl``
    # and ``observer_pre_filter`` remain on the legacy
    # ``production_filter_version_changed`` invalidation until the ranker POC is
    # validated in the field. Adding them
    # here without also porting their multi-horizon / bootstrap evaluators to
    # frozen units would silently change their invalidation semantics, which is
    # what the owner review explicitly warned against.
}


def _require_lab_authority() -> None:
    from extensions.observation_lab.canonical import get_authority_source

    try:
        authority = get_authority_source()
    except Exception as exc:
        raise RuntimeError("observation_authority_unavailable") from exc
    if authority.source != "lab":
        raise RuntimeError("observation_lab_authority_required")


def _hash_spec(spec: dict[str, Any]) -> str:
    return hashlib.sha256(_json(spec).encode("utf-8")).hexdigest()


def register_replay_ready_experiments(
    replays: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Preregister the least-permissive replay-ready alternative once."""
    _require_lab_authority()
    timestamp = _iso(now)
    summary = {"registered": 0, "already_registered": 0, "skipped": 0}
    for replay in replays:
        adapter = _ADAPTERS.get(str(replay.get("filter_name") or ""))
        if adapter is None or replay.get("candidate_cap_reached"):
            summary["skipped"] += 1
            continue
        alternative = adapter.select(replay.get("alternatives") or [])
        if alternative is None:
            summary["skipped"] += 1
            continue
        params = adapter.check(replay, alternative)
        if params is None:
            summary["skipped"] += 1
            continue
        control_param, treatment_param = params
        try:
            scope = {
                "filter_name": str(replay["filter_name"]),
                "filter_version": str(replay["filter_version"]),
                "stage": str(replay["stage"]),
                "scanner_family": str(replay["scanner_family"]),
                "direction": str(replay["direction"]),
                "market_type": str(replay["market_type"]),
                "horizon": str(replay["horizon"]),
            }
        except (KeyError, TypeError, ValueError):
            summary["skipped"] += 1
            continue
        if not all(scope.values()):
            summary["skipped"] += 1
            continue

        from extensions.observation_lab.canonical import register_observation_spec

        from extensions.observation_lab.db import lab_transaction

        with lab_transaction(immediate=True) as lab:
            existing = lab.execute(
                "SELECT 1 FROM observation_windows WHERE kind='filter_shadow' AND status='active' AND "
                + " AND ".join(f"json_extract(spec_json, '$.{key}')=?" for key in scope)
                + " AND json_extract(spec_json, '$.control')=json(?) LIMIT 1",
                (*scope.values(), _json(control_param)),
            ).fetchone()
            if existing is not None:
                summary["already_registered"] += 1
                continue
            registered = register_observation_spec(
                {
                    **scope,
                    "control": control_param,
                    "treatment": treatment_param,
                    "min_samples": max(20, int(replay.get("min_samples") or 20)),
                    "min_coverage": float(replay.get("min_coverage") or _MIN_COVERAGE),
                    "min_edge_delta_pct": float(replay.get("min_edge_delta_pct") or _MIN_EDGE_DELTA_PCT),
                },
                actor="learning_replay",
                source_hash=_hash_spec(replay),
                started_at=timestamp,
                return_created=True,
            )
        summary["registered" if registered.get("_created") else "already_registered"] += 1
    return summary


def build_filter_shadow_experiment_report(limit: int = 50) -> list[dict[str, Any]]:
    """Return plans with their latest evidence snapshot for CLI/API diagnostics."""
    _require_lab_authority()
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations

    return list_filter_shadow_observations(limit=limit) or []


# ── Filter-kind adapters ──────────────────────────────────────────────────

class _FilterAdapter:
    """Per-filter-kind hook for treatment selection and replay reconstruction."""

    def __init__(
        self,
        *,
        ready_marker: str,
        select: Callable[[list[dict[str, Any]]], dict[str, Any] | None],
        check: Callable[
            [dict[str, Any], dict[str, Any]],
            tuple[dict[str, Any], dict[str, Any]] | None,
        ],
    ) -> None:
        self.ready_marker = ready_marker
        self.select = select
        self.check = check


def _family_ttl_select(alternatives: list[dict[str, Any]]) -> dict[str, Any] | None:
    ready = [
        alt for alt in alternatives
        if alt.get("readiness") == "ready_for_controlled_experiment"
    ]
    return min(ready, key=lambda row: int(row["ttl_sec"])) if ready else None


def _family_ttl_check(
    replay: dict[str, Any], alternative: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    try:
        control_ttl = int(replay["current_ttl_sec"])
        treatment_ttl = int(alternative["ttl_sec"])
    except (KeyError, TypeError, ValueError):
        return None
    if control_ttl <= 0 or treatment_ttl <= control_ttl:
        return None
    return {"ttl_sec": control_ttl}, {"ttl_sec": treatment_ttl}




def _observer_select(alternatives: list[dict[str, Any]]) -> dict[str, Any] | None:
    ready = [
        alt for alt in alternatives
        if alt.get("readiness") == "ready_for_prospective_shadow"
    ]
    if not ready:
        return None
    # Least-permissive winner: grid cell closest to the aggressive control
    # (largest rsi_deviation, then largest atr_pct).
    return min(
        ready,
        key=lambda row: (-float(row["rsi_deviation"]), -float(row["atr_pct"])),
    )


def _observer_check(
    replay: dict[str, Any], alternative: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    control_param = replay.get("control_parameter") or {}
    try:
        control_rsi = float(control_param["rsi_deviation"])
        control_atr = float(control_param["atr_pct"])
        treatment_rsi = float(alternative["rsi_deviation"])
        treatment_atr = float(alternative["atr_pct"])
    except (KeyError, TypeError, ValueError):
        return None
    # Treatment must be strictly more permissive than control on at least one
    # axis (grid values decrease as thresholds loosen).
    if treatment_rsi > control_rsi or treatment_atr > control_atr:
        return None
    if treatment_rsi == control_rsi and treatment_atr == control_atr:
        return None
    return (
        {"rsi_deviation": control_rsi, "atr_pct": control_atr},
        {"rsi_deviation": treatment_rsi, "atr_pct": treatment_atr},
    )




_CONFIDENCE_LADDER = ("speculative", "medium", "high")


def _ranker_select(alternatives: list[dict[str, Any]]) -> dict[str, Any] | None:
    ready = [
        alt for alt in alternatives
        if alt.get("readiness") == "ready_for_controlled_experiment"
        and alt.get("minimum_confidence") in _CONFIDENCE_LADDER
    ]
    return max(
        ready,
        key=lambda alt: _CONFIDENCE_LADDER.index(alt["minimum_confidence"]),
    ) if ready else None


def _ranker_check(
    replay: dict[str, Any], alternative: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    control = str(replay.get("current_minimum_confidence") or "").lower()
    treatment = str(alternative.get("minimum_confidence") or "").lower()
    if control not in _CONFIDENCE_LADDER or treatment not in _CONFIDENCE_LADDER:
        return None
    if _CONFIDENCE_LADDER.index(treatment) >= _CONFIDENCE_LADDER.index(control):
        return None
    return {"minimum_confidence": control}, {"minimum_confidence": treatment}




_ADAPTERS: dict[str, _FilterAdapter] = {
    "family_ttl": _FilterAdapter(
        ready_marker="ready_for_controlled_experiment",
        select=_family_ttl_select,
        check=_family_ttl_check,
    ),
    "observer_pre_filter": _FilterAdapter(
        ready_marker="ready_for_prospective_shadow",
        select=_observer_select,
        check=_observer_check,
    ),
    "ranker_confidence_gate": _FilterAdapter(
        ready_marker="ready_for_controlled_experiment",
        select=_ranker_select,
        check=_ranker_check,
    ),
}



def _iso(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
