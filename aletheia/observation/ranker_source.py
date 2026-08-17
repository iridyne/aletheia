"""Read-only Production adapter for the ranker-confidence sidecar POC."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from aletheia.analytics.filter_evidence import FIRST_EXPOSURE_CTE_SQL
from aletheia.counterfactual.filter_replay import _confidence_value, _json_dict
from aletheia.observation.storage import ObservationError, lab_db
from aletheia.storage.sqlite import readonly_db


ADAPTER_VERSION = "ranker-confidence-sidecar/v1"
EVALUATOR_VERSION = "filter-shadow-equivalent/v1"
FILTER_NAME = "ranker_confidence_gate"
_CONFIDENCE_LADDER = ("speculative", "medium", "high")
_TERMINAL = {"ready_for_review", "completed_no_support", "invalidated", "closed"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _validate_thresholds(control: Any, treatment: Any) -> tuple[str, str]:
    control_s = _confidence_value(control)
    treatment_s = _confidence_value(treatment)
    if control_s is None or treatment_s is None:
        raise ObservationError("ranker confidence control/treatment is invalid")
    if _CONFIDENCE_LADDER.index(treatment_s) >= _CONFIDENCE_LADDER.index(control_s):
        raise ObservationError("treatment must lower the frozen confidence threshold")
    return control_s, treatment_s


def register_ranker_observation(
    source_experiment_id: str,
    *,
    observation_id: str | None = None,
    lab_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Copy one existing observe-only ranker experiment plan into the sidecar."""
    source_id = str(source_experiment_id or "").strip()
    if not source_id:
        raise ValueError("source_experiment_id is required")
    with readonly_db() as source:
        if source is None:  # pragma: no cover
            raise ObservationError("Production database is unavailable")
        row = source.execute(
            "SELECT * FROM filter_shadow_experiments WHERE id=?", (source_id,)
        ).fetchone()
    if row is None:
        raise ObservationError(f"filter shadow experiment not found: {source_id}")
    source_row = dict(row)
    if source_row.get("filter_name") != FILTER_NAME:
        raise ObservationError("Observation Lab v1 only supports ranker_confidence_gate")
    if int(source_row.get("production_mutation_allowed") or 0) != 0:
        raise ObservationError("source experiment is not observe-only")

    control_json = _json_dict(source_row.get("control_parameter"))
    treatment_json = _json_dict(source_row.get("treatment_parameter"))
    control, treatment = _validate_thresholds(
        control_json.get("minimum_confidence"), treatment_json.get("minimum_confidence")
    )
    frozen = {
        "filter_name": FILTER_NAME,
        "filter_version": str(source_row.get("filter_version") or ""),
        "stage": str(source_row.get("stage") or ""),
        "scanner_family": str(source_row.get("scanner_family") or "unknown"),
        "direction": str(source_row.get("direction") or ""),
        "market_type": str(source_row.get("market_type") or ""),
        "horizon": str(source_row.get("horizon") or ""),
        "control_minimum": control,
        "treatment_minimum": treatment,
        "min_samples": int(source_row.get("min_samples") or 20),
        "min_coverage": float(source_row.get("min_coverage") or 0.70),
        "min_edge_delta_pct": float(source_row.get("min_edge_delta_pct") or 0.05),
        "adapter_version": ADAPTER_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
    }
    required = ("filter_version", "stage", "scanner_family", "direction", "market_type", "horizon")
    if not all(frozen[key] for key in required):
        raise ObservationError("source experiment has incomplete causal identity")
    identity_hash = stable_hash(frozen)
    obs_id = str(observation_id or f"ranker-{source_id}").strip()
    created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )
    started_at = str(source_row.get("started_at") or source_row.get("created_at") or "")
    if not started_at:
        raise ObservationError("source experiment has no prospective start time")

    with lab_db(lab_path) as lab:
        existing = lab.execute(
            "SELECT * FROM ranker_confidence_observations "
            "WHERE observation_id=? OR source_experiment_id=?",
            (obs_id, source_id),
        ).fetchone()
        if existing is not None:
            existing_row = dict(existing)
            if existing_row["causal_identity_hash"] != identity_hash:
                raise ObservationError("existing sidecar observation has a different identity")
            return existing_row
        lab.execute(
            "INSERT INTO ranker_confidence_observations ("
            "observation_id,source_experiment_id,created_at,started_at,filter_version,stage,"
            "scanner_family,direction,market_type,horizon,control_minimum,treatment_minimum,"
            "min_samples,min_coverage,min_edge_delta_pct,adapter_version,evaluator_version,"
            "causal_identity_hash,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'collecting')",
            (
                obs_id, source_id, created_at, started_at, frozen["filter_version"], frozen["stage"],
                frozen["scanner_family"], frozen["direction"], frozen["market_type"], frozen["horizon"],
                control, treatment, frozen["min_samples"], frozen["min_coverage"],
                frozen["min_edge_delta_pct"], ADAPTER_VERSION, EVALUATOR_VERSION, identity_hash,
            ),
        )
        lab.commit()
        return dict(lab.execute(
            "SELECT * FROM ranker_confidence_observations WHERE observation_id=?", (obs_id,)
        ).fetchone())


def _source_rows(observation: dict[str, Any]) -> list[dict[str, Any]]:
    params = (
        observation["horizon"], observation["filter_version"], observation["stage"],
        observation["scanner_family"], observation["direction"], observation["market_type"],
        observation["started_at"],
    )
    with readonly_db() as source:
        if source is None:  # pragma: no cover
            raise ObservationError("Production database is unavailable")
        rows = source.execute(
            FIRST_EXPOSURE_CTE_SQL
            + "SELECT fd.outcome_candidate_key, fd.decision, fd.input_snapshot, "
            "fd.parameter_snapshot, fd.first_evaluated_at, fd.code_commit, "
            "fo.status AS outcome_status, fo.net_return_pct "
            "FROM ranked_filter_decisions fd "
            "LEFT JOIN filter_candidate_outcomes fo "
            " ON fo.outcome_candidate_key=fd.outcome_candidate_key AND fo.horizon=? "
            "WHERE fd.exposure_rank=1 AND fd.filter_name='ranker_confidence_gate' "
            " AND fd.filter_version=? AND fd.stage=? "
            " AND COALESCE(fd.scanner_family,'unknown')=? "
            " AND fd.direction=? AND fd.market_type=? "
            " AND julianday(fd.first_evaluated_at) > julianday(?) "
            "ORDER BY datetime(fd.first_evaluated_at), fd.outcome_candidate_key",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _invalidate(lab, observation_id: str, reason: str) -> None:
    lab.execute(
        "UPDATE ranker_confidence_observations SET status='invalidated', "
        "invalidation_reason=?, last_synced_at=? WHERE observation_id=?",
        (reason, utcnow(), observation_id),
    )
    lab.commit()


def sync_ranker_observation(
    observation_id: str, *, lab_path: Path | None = None
) -> dict[str, int | str]:
    """Copy immutable first exposures and only append newly matured outcomes."""
    with lab_db(lab_path) as lab:
        row = lab.execute(
            "SELECT * FROM ranker_confidence_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise ObservationError(f"observation not found: {observation_id}")
        observation = dict(row)
        if observation["status"] in _TERMINAL:
            return {"observation_id": observation_id, "inserted": 0, "settled": 0, "status": observation["status"]}

    source_rows = _source_rows(observation)
    inserted = 0
    settled = 0
    with lab_db(lab_path) as lab:
        for source_row in source_rows:
            minimum = _confidence_value(
                _json_dict(source_row.get("parameter_snapshot")).get("minimum")
            )
            if minimum != observation["control_minimum"]:
                _invalidate(lab, observation_id, "source_causal_identity_mismatch")
                raise ObservationError("ranker source threshold no longer matches frozen identity")
            key = str(source_row.get("outcome_candidate_key") or "")
            if not key:
                _invalidate(lab, observation_id, "source_candidate_identity_missing")
                raise ObservationError("ranker first exposure has no outcome candidate key")
            existing = lab.execute(
                "SELECT * FROM ranker_confidence_units WHERE observation_id=? AND outcome_candidate_key=?",
                (observation_id, key),
            ).fetchone()
            immutable = {
                "decision": str(source_row["decision"]),
                "first_evaluated_at": str(source_row["first_evaluated_at"]),
                "input_snapshot": str(source_row.get("input_snapshot") or "{}"),
                "parameter_snapshot": str(source_row.get("parameter_snapshot") or "{}"),
            }
            outcome_status = source_row.get("outcome_status")
            net_return = source_row.get("net_return_pct")
            if existing is None:
                lab.execute(
                    "INSERT INTO ranker_confidence_units (observation_id,outcome_candidate_key,decision,"
                    "first_evaluated_at,input_snapshot,parameter_snapshot,source_code_commit,outcome_status,"
                    "net_return_pct) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        observation_id, key, immutable["decision"], immutable["first_evaluated_at"],
                        immutable["input_snapshot"], immutable["parameter_snapshot"],
                        source_row.get("code_commit"), outcome_status, net_return,
                    ),
                )
                inserted += 1
                settled += int(outcome_status == "settled" and net_return is not None)
                continue
            existing_row = dict(existing)
            if any(existing_row[field] != value for field, value in immutable.items()):
                _invalidate(lab, observation_id, "first_exposure_snapshot_changed")
                raise ObservationError("canonical first-exposure snapshot changed after capture")
            was_settled = existing_row.get("outcome_status") == "settled" and existing_row.get("net_return_pct") is not None
            is_settled = outcome_status == "settled" and net_return is not None
            if was_settled and is_settled and float(existing_row["net_return_pct"]) != float(net_return):
                _invalidate(lab, observation_id, "settled_outcome_changed")
                raise ObservationError("settled canonical outcome changed after capture")
            if not was_settled and is_settled:
                lab.execute(
                    "UPDATE ranker_confidence_units SET outcome_status=?, net_return_pct=? "
                    "WHERE observation_id=? AND outcome_candidate_key=?",
                    (outcome_status, net_return, observation_id, key),
                )
                settled += 1
            elif not was_settled and outcome_status != existing_row.get("outcome_status"):
                lab.execute(
                    "UPDATE ranker_confidence_units SET outcome_status=? "
                    "WHERE observation_id=? AND outcome_candidate_key=?",
                    (outcome_status, observation_id, key),
                )
        synced_at = utcnow()
        lab.execute(
            "UPDATE ranker_confidence_observations SET last_synced_at=? WHERE observation_id=?",
            (synced_at, observation_id),
        )
        lab.commit()
        status = str(lab.execute(
            "SELECT status FROM ranker_confidence_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()[0])
    return {"observation_id": observation_id, "inserted": inserted, "settled": settled, "status": status}
