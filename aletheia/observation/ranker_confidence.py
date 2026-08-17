"""Public surface for the narrow ranker-confidence Observation Lab POC."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aletheia.observation.ranker_eval import evaluate_ranker_observation
from aletheia.observation.ranker_source import (
    register_ranker_observation,
    sync_ranker_observation,
)
from aletheia.observation.storage import ObservationError, lab_db


def get_ranker_observation(
    observation_id: str, *, lab_path: Path | None = None
) -> dict[str, Any]:
    with lab_db(lab_path) as lab:
        row = lab.execute(
            "SELECT * FROM ranker_confidence_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise ObservationError(f"observation not found: {observation_id}")
        result = dict(row)
        result["unit_count"] = int(lab.execute(
            "SELECT COUNT(*) FROM ranker_confidence_units WHERE observation_id=?",
            (observation_id,),
        ).fetchone()[0])
        latest = lab.execute(
            "SELECT * FROM ranker_confidence_evaluations WHERE observation_id=? "
            "ORDER BY id DESC LIMIT 1",
            (observation_id,),
        ).fetchone()
        result["latest_evaluation"] = dict(latest) if latest is not None else None
        return result


def list_ranker_observations(*, lab_path: Path | None = None) -> list[dict[str, Any]]:
    with lab_db(lab_path) as lab:
        return [
            dict(row)
            for row in lab.execute(
                "SELECT * FROM ranker_confidence_observations "
                "ORDER BY datetime(created_at) DESC, observation_id"
            ).fetchall()
        ]


def run_ranker_observations(*, lab_path: Path | None = None) -> list[dict[str, Any]]:
    """Run one bounded sync/evaluate pass; this is not a daemon or scheduler."""
    results: list[dict[str, Any]] = []
    for observation in list_ranker_observations(lab_path=lab_path):
        if observation["status"] != "collecting":
            continue
        sync = sync_ranker_observation(observation["observation_id"], lab_path=lab_path)
        evaluation = evaluate_ranker_observation(
            observation["observation_id"], lab_path=lab_path
        )
        results.append({"sync": sync, "evaluation": evaluation})
    return results
