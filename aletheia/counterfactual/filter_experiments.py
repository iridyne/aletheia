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
    build_ranker_confidence_replay_from_frozen,
)
from aletheia.observation_lab import units as lab_units
from aletheia.storage import repositories as repo
from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db


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
    # and ``observer_pre_filter`` remain on the legacy ``_production_version_changed``
    # invalidation until the ranker POC is validated in the field. Adding them
    # here without also porting their multi-horizon / bootstrap evaluators to
    # frozen units would silently change their invalidation semantics, which is
    # what the owner review explicitly warned against.
}

# Adapters whose ``_ranker_rebuild``-style path can consume frozen units.
# Kept separate from ``_ADAPTER_CAUSAL_IDENTITY`` on purpose — future work can
# declare an identity vector for a new adapter without also committing to the
# rebuild-from-frozen refactor in the same change.
_FROZEN_REBUILD_ADAPTERS: frozenset[str] = frozenset({"ranker_confidence_gate"})


def _adapter_causal_identity_hash(filter_name: str) -> str | None:
    """Hex-digest hash of the adapter's declared causal identity vector."""
    identity = _ADAPTER_CAUSAL_IDENTITY.get(filter_name)
    if identity is None:
        return None
    return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()


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
            # None for adapters that opt out of Lab identity semantics (Phase 2 migration).
            "adapter_identity_hash": _adapter_causal_identity_hash(scope["filter_name"]),
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
        invalidation = _invalidation_for(experiment)
        if invalidation is not None:
            reason_code, note = invalidation
            disposition = "invalidated"
            evaluation = _empty_evaluation(
                experiment, timestamp, disposition, note=note,
            )
            update = {
                "status": disposition,
                "last_evaluated_at": timestamp,
                "completed_at": timestamp,
                "invalidation_reason": reason_code,
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
    scope = {
        "filter_name": str(spec["filter_name"]),
        "filter_version": str(spec.get("filter_version") or ""),
        "stage": str(spec["stage"]),
        "scanner_family": str(spec["scanner_family"]),
        "direction": str(spec["direction"]).lower(),
        "market_type": str(spec["market_type"]).lower(),
        "horizon": str(spec["horizon"]),
    }
    control = _json(spec["control"])
    treatment = _json(spec["treatment"])
    experiment_key = _experiment_key(scope, control, treatment)
    record = {
        "id": uuid.uuid4().hex,
        "experiment_key": experiment_key,
        "created_at": timestamp,
        "started_at": timestamp,
        **scope,
        "control_parameter": control,
        "treatment_parameter": treatment,
        "assignment_policy": str(
            spec.get("assignment_policy") or "prospective_first_exposure"
        ),
        "status": "collecting",
        "min_samples": max(1, int(spec.get("min_samples") or 20)),
        "min_coverage": float(spec.get("min_coverage") or _MIN_COVERAGE),
        "min_edge_delta_pct": float(
            spec.get("min_edge_delta_pct") or _MIN_EDGE_DELTA_PCT
        ),
        "source_replay": _json(spec.get("source_replay") or spec),
        "production_mutation_allowed": 0,
        "adapter_identity_hash": _adapter_causal_identity_hash(scope["filter_name"]),
    }
    if not repo.insert_filter_shadow_experiment(record):
        raise ValueError(
            f"observation with identical scope + treatment already exists "
            f"(experiment_key={experiment_key})"
        )
    return record["id"]


def evaluate_one_experiment(
    experiment_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Run one evaluation pass restricted to a single experiment id.

    Same semantics as :func:`evaluate_active_shadow_experiments` — including
    the adapter causal identity invalidation check — but targeted, so an
    operator can force a fresh evaluation snapshot without waiting for the
    controller cycle. Used by the ``aletheia observe evaluate`` CLI.
    """
    target: dict[str, Any] | None = None
    for row in repo.list_filter_shadow_experiments(limit=1000):
        if row["id"] == experiment_id:
            target = row
            break
    if target is None:
        raise ValueError(f"unknown observation id: {experiment_id}")
    timestamp = _iso(now)
    invalidation = _invalidation_for(target)
    if invalidation is not None:
        reason_code, note = invalidation
        disposition = "invalidated"
        evaluation = _empty_evaluation(target, timestamp, disposition, note=note)
        update = {
            "status": disposition,
            "last_evaluated_at": timestamp,
            "completed_at": timestamp,
            "invalidation_reason": reason_code,
        }
    else:
        evaluation, disposition = _evaluate_one(target, timestamp)
        update = {"status": disposition, "last_evaluated_at": timestamp}
        if disposition != "collecting":
            update["completed_at"] = timestamp
    with transaction():
        repo.insert_filter_shadow_evaluation(evaluation)
        repo.update_filter_shadow_experiment(target["id"], **update)
    return evaluation


def capture_frozen_units_for(experiment: dict[str, Any], *, now: datetime | None = None) -> int:
    """Copy new first-exposure rows into ``filter_shadow_frozen_units``.

    Only adapters with a declared causal identity (currently
    ``ranker_confidence_gate``) participate in freezing — others keep the legacy
    live-replay path. The first-exposure query intentionally does NOT filter by
    ``filter_version``: a candidate's earliest exposure across all versions of
    the same scope wins, so unrelated ``filter_version`` bumps produce zero
    new frozen units and the experiment keeps collecting.

    Idempotent. Returns the number of freshly-inserted units.
    """
    filter_name = str(experiment.get("filter_name") or "")
    if filter_name not in _FROZEN_REBUILD_ADAPTERS:
        return 0
    timestamp = _iso(now)
    rows = get_db().execute(
        """
        WITH first_exposure AS (
            SELECT outcome_candidate_key,
                   decision,
                   input_snapshot,
                   parameter_snapshot,
                   evaluated_at,
                   filter_version,
                   ROW_NUMBER() OVER (
                       PARTITION BY outcome_candidate_key
                       ORDER BY datetime(evaluated_at), rowid
                   ) AS rn
            FROM filter_decisions
            WHERE filter_name = ?
              AND stage = ?
              AND COALESCE(scanner_family, 'unknown') = ?
              AND direction = ?
              AND market_type = ?
              AND decision IN ('passed', 'rejected')
              AND outcome_candidate_key IS NOT NULL
        )
        SELECT outcome_candidate_key, decision, input_snapshot, parameter_snapshot,
               evaluated_at AS first_evaluated_at, filter_version
        FROM first_exposure
        WHERE rn = 1
          AND julianday(evaluated_at) > julianday(?)
        ORDER BY datetime(evaluated_at), outcome_candidate_key
        """,
        (
            experiment["filter_name"],
            experiment["stage"],
            experiment["scanner_family"],
            experiment["direction"],
            experiment["market_type"],
            experiment["started_at"],
        ),
    ).fetchall()
    if not rows:
        return 0
    frozen: list[dict[str, Any]] = []
    for row in rows:
        frozen.append({
            "id": uuid.uuid4().hex,
            "outcome_candidate_key": row["outcome_candidate_key"],
            "decision": row["decision"],
            "input_snapshot": row["input_snapshot"] or "{}",
            "parameter_snapshot": row["parameter_snapshot"] or "{}",
            "first_evaluated_at": row["first_evaluated_at"],
            # Provenance only — never used for identity/comparability decisions.
            "source_runtime_commit": row["filter_version"],
            "first_seen_at": timestamp,
        })
    return lab_units.insert_frozen_units(experiment["id"], frozen)


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


def _invalidation_for(experiment: dict[str, Any]) -> tuple[str, str] | None:
    """Decide whether ``experiment`` must be invalidated on this evaluation pass.

    Returns ``(reason_code, human_note)`` or ``None`` when it should keep collecting.

    ``ranker_confidence_gate`` uses the adapter-declared causal identity vector
    (Issue #1072 owner review §4): unrelated ``filter_version`` bumps in production
    are provenance-only and do not invalidate; only a change in the adapter's own
    declared causal identity does. Other filter kinds keep the legacy strict
    version check pending Phase 2 migration.
    """
    filter_name = str(experiment.get("filter_name") or "")
    if filter_name in _ADAPTER_CAUSAL_IDENTITY:
        stored = experiment.get("adapter_identity_hash")
        current = _adapter_causal_identity_hash(filter_name)
        if stored and current and stored != current:
            return (
                "adapter_causal_identity_changed",
                "Adapter causal identity changed after experiment start — "
                "new cohort required. Historical evidence remains available.",
            )
        return None
    if _production_version_changed(experiment):
        return (
            "production_filter_version_changed",
            "Production filter version changed after experiment start.",
        )
    return None


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
