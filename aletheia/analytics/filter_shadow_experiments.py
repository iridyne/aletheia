"""Prospective, observe-only validation for replay-ready filter thresholds.

Historical replay may nominate a treatment, but every experiment starts after
registration and uses only future first exposures. Reaching an evidence gate
changes analytical status to ``ready_for_review``; production settings are
never changed here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Any, Iterable

from aletheia.analytics.filter_replay import build_family_ttl_replay
from aletheia.storage import repositories as repo
from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db


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
        alternative = _select_treatment(replay)
        if alternative is None or replay.get("candidate_cap_reached"):
            summary["skipped"] += 1
            continue
        try:
            control_ttl = int(replay["current_ttl_sec"])
            treatment_ttl = int(alternative["ttl_sec"])
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
        if (
            scope["filter_name"] != "family_ttl"
            or not all(scope.values())
            or control_ttl <= 0
            or treatment_ttl <= control_ttl
        ):
            summary["skipped"] += 1
            continue

        control = _json({"ttl_sec": control_ttl})
        treatment = _json({"ttl_sec": treatment_ttl})
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
            "min_coverage": _MIN_COVERAGE,
            "min_edge_delta_pct": _MIN_EDGE_DELTA_PCT,
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
    control_ttl = int(_json_dict(experiment["control_parameter"])["ttl_sec"])
    treatment_ttl = int(_json_dict(experiment["treatment_parameter"])["ttl_sec"])
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
    control = replay["current_pass"]
    treatment = replay["alternatives"][0]
    enough = (
        int(control["settled_n"]) >= int(experiment["min_samples"])
        and int(treatment["settled_n"]) >= int(experiment["min_samples"])
        and float(control["outcome_coverage"]) >= float(experiment["min_coverage"])
        and float(treatment["outcome_coverage"]) >= float(experiment["min_coverage"])
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
        "control_candidate_count": int(control["candidate_count"]),
        "control_settled_n": int(control["settled_n"]),
        "control_outcome_coverage": float(control["outcome_coverage"]),
        "control_raw_avg_net_pct": control_mean,
        "treatment_candidate_count": int(treatment["candidate_count"]),
        "treatment_settled_n": int(treatment["settled_n"]),
        "treatment_outcome_coverage": float(treatment["outcome_coverage"]),
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


def _select_treatment(replay: dict[str, Any]) -> dict[str, Any] | None:
    ready = [
        row for row in replay.get("alternatives") or []
        if row.get("readiness") == "ready_for_controlled_experiment"
    ]
    return min(ready, key=lambda row: int(row["ttl_sec"])) if ready else None


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
