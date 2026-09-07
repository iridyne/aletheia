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

from aletheia.counterfactual.filter_replay import (
    build_family_ttl_replay,
    build_observer_pre_filter_replay,
    build_ranker_confidence_replay,
    build_ranker_confidence_replay_from_frozen,
)
from aletheia.observation_lab import units as lab_units


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
    from aletheia.observation_lab.canonical import get_authority_source

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

        from aletheia.observation_lab.canonical import register_observation_spec

        from aletheia.observation_lab.db import lab_transaction

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


def evaluate_active_shadow_experiments(
    *, now: datetime | None = None
) -> dict[str, int]:
    """Append one evidence snapshot for each collecting experiment."""
    _require_lab_authority()
    from aletheia.observation_lab.filter_shadow import (
        evaluate_filter_shadow_observation,
        list_filter_shadow_observations,
    )

    summary = {
        "evaluated": 0,
        "ready_for_review": 0,
        "completed_no_support": 0,
        "invalidated": 0,
    }
    for row in list_filter_shadow_observations(status="active", limit=1000) or []:
        result = evaluate_filter_shadow_observation(
            str(row["observation_id"]), int(row["generation"]), as_of_at=now,
        )
        summary["evaluated"] += 1
        disposition = str(
            ((result.get("evaluations") or [{}])[0]).get("disposition") or ""
        )
        if disposition in {"ready_for_review", "completed_no_support"}:
            summary[disposition] += 1
        elif result.get("safety_action") or result.get("verdict") in {"invalid", "negative"}:
            summary["invalidated"] += 1
    return summary


def register_observation_from_spec(
    spec: dict[str, Any], *, now: datetime | None = None
) -> str:
    """Register one Observation from a hand-authored spec dict.

    Complement to :func:`register_replay_ready_experiments` for the
    ``aletheia observe register <spec.yaml>`` CLI: bypasses the "recommendation
    → alternative selection" path so operators can register cohorts the
    automated controller wouldn't nominate. Same durable identity fields, same
    ``production_mutation_allowed=0`` boundary, same adapter causal identity
    hash — Lab treatment is uniform regardless of registration source.

    Raises ``ValueError`` on missing scope fields or when a matching cohort
    already exists (identical scope + control + treatment).
    """
    required = (
        "filter_name", "stage", "scanner_family", "direction",
        "market_type", "horizon", "control", "treatment",
    )
    missing = [key for key in required if not spec.get(key)]
    if missing:
        raise ValueError(f"spec is missing required field(s): {missing}")

    timestamp = _iso(now)
    _require_lab_authority()
    from aletheia.observation_lab.canonical import register_observation_spec

    return str(register_observation_spec(
        spec,
        actor="learning_replay",
        source_hash=_hash_spec(spec),
        started_at=timestamp,
    )["observation_id"])


def build_filter_shadow_experiment_report(limit: int = 50) -> list[dict[str, Any]]:
    """Return plans with their latest evidence snapshot for CLI/API diagnostics."""
    _require_lab_authority()
    from aletheia.observation_lab.filter_shadow import list_filter_shadow_observations

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
        rebuild: Callable[
            [dict[str, Any], str],
            tuple[dict[str, Any] | None, dict[str, Any] | None],
        ],
    ) -> None:
        self.ready_marker = ready_marker
        self.select = select
        self.check = check
        self.rebuild = rebuild


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


def _family_ttl_rebuild(
    experiment: dict[str, Any],
    *,
    as_of_at: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    control_param = _json_dict(experiment["control_parameter"])
    treatment_param = _json_dict(experiment["treatment_parameter"])
    try:
        control_ttl = int(control_param["ttl_sec"])
        treatment_ttl = int(treatment_param["ttl_sec"])
    except (KeyError, TypeError, ValueError):
        return None, None
    replay = build_family_ttl_replay(
        filter_version=experiment["filter_version"],
        stage=experiment["stage"],
        scanner_family=experiment["scanner_family"],
        direction=experiment["direction"],
        market_type=experiment["market_type"],
        horizon=experiment["horizon"],
        current_ttl_sec=control_ttl,
        alternative_ttls=[treatment_ttl],
        evaluated_after=experiment["started_at"],
        as_of_at=as_of_at,
        min_samples=int(experiment["min_samples"]),
    )
    if not replay.get("alternatives"):
        return None, None
    return replay["current_pass"], replay["alternatives"][0]


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


def _observer_rebuild(
    experiment: dict[str, Any],
    *,
    as_of_at: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    treatment_param = _json_dict(experiment["treatment_parameter"])
    try:
        treatment_rsi = float(treatment_param["rsi_deviation"])
        treatment_atr = float(treatment_param["atr_pct"])
    except (KeyError, TypeError, ValueError):
        return None, None
    replay = build_observer_pre_filter_replay(
        filter_version=experiment["filter_version"],
        scanner_family=experiment["scanner_family"],
        direction=experiment["direction"],
        market_type=experiment["market_type"],
        evaluated_since=experiment["started_at"],
        as_of_at=as_of_at,
        min_samples=int(experiment["min_samples"]),
    )
    match: dict[str, Any] | None = None
    for alt in replay.get("alternatives") or []:
        try:
            alt_rsi = float(alt["rsi_deviation"])
            alt_atr = float(alt["atr_pct"])
        except (KeyError, TypeError, ValueError):
            continue
        if alt_rsi == treatment_rsi and alt_atr == treatment_atr:
            match = alt
            break
    if match is None:
        return None, None
    # Project observer horizon_metrics into the flat shape expected by the
    # evaluator. Use the 24h horizon as the primary evidence horizon (matching
    # readiness definition in filter_replay); compare treatment_union against
    # control since the union is what would be observed if the filter loosened.
    primary = match.get("horizon_metrics", {}).get("24h") or {}
    control = primary.get("control") or {}
    treatment = primary.get("treatment_union") or {}
    if not control or not treatment:
        return None, None
    return control, treatment


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


def _ranker_rebuild(
    experiment: dict[str, Any],
    *,
    as_of_at: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    control = str(
        _json_dict(experiment["control_parameter"]).get("minimum_confidence") or ""
    )
    treatment = str(
        _json_dict(experiment["treatment_parameter"]).get("minimum_confidence") or ""
    )
    # Prefer Observation Lab frozen units when they exist: unrelated production
    # ``filter_version`` bumps then don't retroactively change the input snapshot
    # the evaluator sees. Falls back to the live replay on the first cycle
    # (before any unit has been captured).
    if lab_units.count_frozen_units(experiment["id"]) > 0:
        frozen = build_ranker_confidence_replay_from_frozen(
            experiment_id=experiment["id"],
            horizon=experiment["horizon"],
            current_minimum_confidence=control,
            treatment_minimum=treatment,
            min_samples=int(experiment["min_samples"]),
            as_of_at=as_of_at,
        )
        if frozen is not None:
            match = next((
                alt for alt in frozen.get("alternatives") or []
                if alt.get("minimum_confidence") == treatment
            ), None)
            return (frozen["current_pass"], match) if match is not None else (None, None)
    replay = build_ranker_confidence_replay(
        filter_version=experiment["filter_version"],
        stage=experiment["stage"],
        scanner_family=experiment["scanner_family"],
        direction=experiment["direction"],
        market_type=experiment["market_type"],
        horizon=experiment["horizon"],
        evaluated_after=experiment["started_at"],
        as_of_at=as_of_at,
        current_minimum_confidence=control,
        min_samples=int(experiment["min_samples"]),
    )
    match = next((
        alt for alt in replay.get("alternatives") or []
        if alt.get("minimum_confidence") == treatment
    ), None)
    return (replay["current_pass"], match) if match is not None else (None, None)


_ADAPTERS: dict[str, _FilterAdapter] = {
    "family_ttl": _FilterAdapter(
        ready_marker="ready_for_controlled_experiment",
        select=_family_ttl_select,
        check=_family_ttl_check,
        rebuild=_family_ttl_rebuild,
    ),
    "observer_pre_filter": _FilterAdapter(
        ready_marker="ready_for_prospective_shadow",
        select=_observer_select,
        check=_observer_check,
        rebuild=_observer_rebuild,
    ),
    "ranker_confidence_gate": _FilterAdapter(
        ready_marker="ready_for_controlled_experiment",
        select=_ranker_select,
        check=_ranker_check,
        rebuild=_ranker_rebuild,
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
