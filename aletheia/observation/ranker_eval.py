"""Filter-shadow-equivalent evaluator over frozen sidecar rows."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from aletheia.counterfactual.filter_replay import (
    _confidence_value,
    _json_dict,
    _outcome_metrics,
    _shrunk_mean,
)
from aletheia.observation.ranker_source import stable_hash, utcnow
from aletheia.observation.storage import ObservationError, lab_db


_CONFIDENCE_LADDER = ("speculative", "medium", "high")


def _replay_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {
        "input_snapshot": data.get("input_snapshot"),
        "parameter_snapshot": data.get("parameter_snapshot"),
        "status": data.get("outcome_status"),
        "net_return_pct": data.get("net_return_pct"),
    }


def evaluation_payload(
    observation: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Mirror the current ranker-confidence prospective shadow metric semantics."""
    passed = [_replay_row(row) for row in rows if row["decision"] == "passed"]
    rejected = [_replay_row(row) for row in rows if row["decision"] == "rejected"]
    control = _outcome_metrics(passed)
    control_rank = _CONFIDENCE_LADDER.index(observation["control_minimum"])
    treatment_rank = _CONFIDENCE_LADDER.index(observation["treatment_minimum"])
    admitted: list[dict[str, Any]] = []
    invalid_inputs = 0
    for row in rejected:
        confidence = _confidence_value(_json_dict(row.get("input_snapshot")).get("confidence"))
        if confidence is None:
            invalid_inputs += 1
            continue
        rank = _CONFIDENCE_LADDER.index(confidence)
        if treatment_rank <= rank < control_rank:
            admitted.append(row)

    treatment = _outcome_metrics(admitted)
    prior_mean = float(control.get("raw_avg_net_pct") or 0.0)
    treatment_shrunk = _shrunk_mean(treatment, prior_mean)
    control_mean = control.get("raw_avg_net_pct")
    edge_delta = (
        round(float(treatment_shrunk) - float(control_mean), 6)
        if treatment_shrunk is not None and control_mean is not None
        else None
    )
    enough = (
        int(control["settled_n"]) >= int(observation["min_samples"])
        and int(treatment["settled_n"]) >= int(observation["min_samples"])
        and float(control["outcome_coverage"]) >= float(observation["min_coverage"])
        and float(treatment["outcome_coverage"]) >= float(observation["min_coverage"])
    )
    disposition = "collecting"
    if enough:
        supported = (
            treatment_shrunk is not None
            and edge_delta is not None
            and float(treatment_shrunk) >= float(observation["min_edge_delta_pct"])
            and edge_delta >= float(observation["min_edge_delta_pct"])
        )
        disposition = "ready_for_review" if supported else "completed_no_support"
    note = (
        "Prospective evidence gate met; manual review only, production unchanged."
        if disposition == "ready_for_review"
        else "Prospective evidence gate met without support; production unchanged."
        if disposition == "completed_no_support"
        else "Collecting future first-exposure control and treatment outcomes."
    )
    return {
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
        "invalid_replay_inputs": invalid_inputs,
        "note": note,
    }


def evaluate_ranker_observation(
    observation_id: str, *, lab_path: Path | None = None
) -> dict[str, Any]:
    with lab_db(lab_path) as lab:
        observation_row = lab.execute(
            "SELECT * FROM ranker_confidence_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        if observation_row is None:
            raise ObservationError(f"observation not found: {observation_id}")
        observation = dict(observation_row)
        if observation["status"] == "invalidated":
            return {"observation_id": observation_id, "disposition": "invalidated"}
        rows = [
            dict(row)
            for row in lab.execute(
                "SELECT * FROM ranker_confidence_units WHERE observation_id=? "
                "ORDER BY datetime(first_evaluated_at), outcome_candidate_key",
                (observation_id,),
            ).fetchall()
        ]
        payload = evaluation_payload(observation, rows)
        fingerprint = stable_hash({
            "evaluator_version": observation["evaluator_version"],
            **payload,
        })
        created_at = utcnow()
        lab.execute(
            "INSERT OR IGNORE INTO ranker_confidence_evaluations ("
            "observation_id,created_at,evaluation_fingerprint,disposition,"
            "control_candidate_count,control_settled_n,control_outcome_coverage,"
            "control_raw_avg_net_pct,treatment_candidate_count,treatment_settled_n,"
            "treatment_outcome_coverage,treatment_raw_avg_net_pct,"
            "treatment_shrunk_avg_net_pct,edge_delta_pct,invalid_replay_inputs,note"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                observation_id, created_at, fingerprint, payload["disposition"],
                payload["control_candidate_count"], payload["control_settled_n"],
                payload["control_outcome_coverage"], payload["control_raw_avg_net_pct"],
                payload["treatment_candidate_count"], payload["treatment_settled_n"],
                payload["treatment_outcome_coverage"], payload["treatment_raw_avg_net_pct"],
                payload["treatment_shrunk_avg_net_pct"], payload["edge_delta_pct"],
                payload["invalid_replay_inputs"], payload["note"],
            ),
        )
        lab.execute(
            "UPDATE ranker_confidence_observations SET last_evaluated_at=?, "
            "status=CASE WHEN ?='collecting' THEN status ELSE ? END WHERE observation_id=?",
            (created_at, payload["disposition"], payload["disposition"], observation_id),
        )
        lab.commit()
    return {"observation_id": observation_id, **payload}
