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
import uuid
from typing import Any, Callable, Iterable

from aletheia.counterfactual.filter_replay import (
    build_family_ttl_replay,
    build_observer_pre_filter_replay,
    build_ranker_confidence_replay,
)
from aletheia.storage import repositories as repo
from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db


# Fallback gates when a replay does not declare its own gates in the top-level
# dict. family_ttl currently relies on these; observer_pre_filter declares its
# own stricter gates (0.80 coverage, 0.25 edge delta, 200 samples).
_MIN_COVERAGE = 0.70
_MIN_EDGE_DELTA_PCT = 0.05


def register_replay_ready_experiments(
    replays: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Preregister the least-permissive replay-ready alternative once."""
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

        control = _json(control_param)
        treatment = _json(treatment_param)
        experiment_key = _experiment_key(scope, control, treatment)
        record = {
            "id": uuid.uuid4().hex,
            "experiment_key": experiment_key,
            "created_at": timestamp,
            "started_at": timestamp,
            **scope,
            "control_parameter": control,
            "treatment_parameter": treatment,
            "assignment_policy": "prospective_first_exposure",
            "status": "collecting",
            "min_samples": max(20, int(replay.get("min_samples") or 20)),
            "min_coverage": float(replay.get("min_coverage") or _MIN_COVERAGE),
            "min_edge_delta_pct": float(
                replay.get("min_edge_delta_pct") or _MIN_EDGE_DELTA_PCT
            ),
            "source_replay": _json(replay),
            # This hard database constraint makes the observe-only boundary explicit.
            "production_mutation_allowed": 0,
        }
        if repo.insert_filter_shadow_experiment(record):
            summary["registered"] += 1
        else:
            summary["already_registered"] += 1
    return summary


def evaluate_active_shadow_experiments(
    *, now: datetime | None = None
) -> dict[str, int]:
    """Append one evidence snapshot for each collecting experiment."""
    timestamp = _iso(now)
    summary = {
        "evaluated": 0,
        "ready_for_review": 0,
        "completed_no_support": 0,
        "invalidated": 0,
    }
    for experiment in repo.list_filter_shadow_experiments(status="collecting"):
        if _production_version_changed(experiment):
            disposition = "invalidated"
            evaluation = _empty_evaluation(
                experiment, timestamp, disposition,
                note="Production filter version changed after experiment start.",
            )
            update = {
                "status": disposition,
                "last_evaluated_at": timestamp,
                "completed_at": timestamp,
                "invalidation_reason": "production_filter_version_changed",
            }
        else:
            evaluation, disposition = _evaluate_one(experiment, timestamp)
            update = {"status": disposition, "last_evaluated_at": timestamp}
            if disposition != "collecting":
                update["completed_at"] = timestamp
        with transaction():
            repo.insert_filter_shadow_evaluation(evaluation)
            repo.update_filter_shadow_experiment(experiment["id"], **update)
        summary["evaluated"] += 1
        if disposition != "collecting":
            summary[disposition] += 1
    return summary


def build_filter_shadow_experiment_report(limit: int = 50) -> list[dict[str, Any]]:
    """Return plans with their latest evidence snapshot for CLI/API diagnostics."""
    rows: list[dict[str, Any]] = []
    for experiment in repo.list_filter_shadow_experiments(limit=max(1, int(limit))):
        row = dict(experiment)
        row["control_parameter"] = _json_dict(row.get("control_parameter"))
        row["treatment_parameter"] = _json_dict(row.get("treatment_parameter"))
        row["source_replay"] = _json_dict(row.get("source_replay"))
        row["latest_evaluation"] = repo.latest_filter_shadow_evaluation(row["id"])
        rows.append(row)
    return rows


def _evaluate_one(
    experiment: dict[str, Any], timestamp: str
) -> tuple[dict[str, Any], str]:
    adapter = _ADAPTERS.get(str(experiment.get("filter_name") or ""))
    if adapter is None:
        return (
            _empty_evaluation(
                experiment,
                timestamp,
                "invalidated",
                note=f"Unknown filter_name {experiment.get('filter_name')!r} — no evaluator adapter.",
            ),
            "invalidated",
        )
    control, treatment = adapter.rebuild(experiment)
    if control is None or treatment is None:
        return (
            _empty_evaluation(
                experiment,
                timestamp,
                "invalidated",
                note="Replay could not reproduce the experiment's treatment cell.",
            ),
            "invalidated",
        )
    enough = (
        int(control.get("settled_n") or 0) >= int(experiment["min_samples"])
        and int(treatment.get("settled_n") or 0) >= int(experiment["min_samples"])
        and float(control.get("outcome_coverage") or 0.0) >= float(experiment["min_coverage"])
        and float(treatment.get("outcome_coverage") or 0.0) >= float(experiment["min_coverage"])
    )
    control_mean = control.get("raw_avg_net_pct")
    treatment_shrunk = treatment.get("shrunk_avg_net_pct")
    edge_delta = (
        round(float(treatment_shrunk) - float(control_mean), 6)
        if treatment_shrunk is not None and control_mean is not None
        else None
    )
    disposition = "collecting"
    if enough:
        supported = (
            treatment_shrunk is not None
            and edge_delta is not None
            and float(treatment_shrunk) >= float(experiment["min_edge_delta_pct"])
            and edge_delta >= float(experiment["min_edge_delta_pct"])
        )
        disposition = "ready_for_review" if supported else "completed_no_support"
    note = (
        "Prospective evidence gate met; manual review only, production unchanged."
        if disposition == "ready_for_review"
        else "Prospective evidence gate met without support; production unchanged."
        if disposition == "completed_no_support"
        else "Collecting future first-exposure control and treatment outcomes."
    )
    return ({
        "id": uuid.uuid4().hex,
        "experiment_id": experiment["id"],
        "created_at": timestamp,
        "evidence_through_at": timestamp,
        "disposition": disposition,
        "control_candidate_count": int(control.get("candidate_count") or 0),
        "control_settled_n": int(control.get("settled_n") or 0),
        "control_outcome_coverage": float(control.get("outcome_coverage") or 0.0),
        "control_raw_avg_net_pct": control_mean,
        "treatment_candidate_count": int(treatment.get("candidate_count") or 0),
        "treatment_settled_n": int(treatment.get("settled_n") or 0),
        "treatment_outcome_coverage": float(treatment.get("outcome_coverage") or 0.0),
        "treatment_raw_avg_net_pct": treatment.get("raw_avg_net_pct"),
        "treatment_shrunk_avg_net_pct": treatment_shrunk,
        "edge_delta_pct": edge_delta,
        "note": note,
    }, disposition)


def _production_version_changed(experiment: dict[str, Any]) -> bool:
    row = get_db().execute(
        "SELECT 1 FROM filter_decisions WHERE filter_name=? AND stage=? "
        "AND COALESCE(scanner_family,'unknown')=? AND direction=? AND market_type=? "
        "AND datetime(evaluated_at) >= datetime(?) AND filter_version<>? LIMIT 1",
        (
            experiment["filter_name"], experiment["stage"],
            experiment["scanner_family"], experiment["direction"],
            experiment["market_type"], experiment["started_at"],
            experiment["filter_version"],
        ),
    ).fetchone()
    return row is not None


def _empty_evaluation(
    experiment: dict[str, Any], timestamp: str, disposition: str, *, note: str
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "experiment_id": experiment["id"],
        "created_at": timestamp,
        "evidence_through_at": timestamp,
        "disposition": disposition,
        "note": note,
    }


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
            [dict[str, Any]],
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
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    control = str(
        _json_dict(experiment["control_parameter"]).get("minimum_confidence") or ""
    )
    treatment = str(
        _json_dict(experiment["treatment_parameter"]).get("minimum_confidence") or ""
    )
    replay = build_ranker_confidence_replay(
        filter_version=experiment["filter_version"],
        stage=experiment["stage"],
        scanner_family=experiment["scanner_family"],
        direction=experiment["direction"],
        market_type=experiment["market_type"],
        horizon=experiment["horizon"],
        evaluated_after=experiment["started_at"],
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


def _experiment_key(scope: dict[str, str], control: str, treatment: str) -> str:
    payload = _json({**scope, "control": control, "treatment": treatment})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
