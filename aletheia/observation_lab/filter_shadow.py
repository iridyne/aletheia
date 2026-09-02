"""Lab-owned adapter for prospective filter-shadow Observations.

Filter decisions and candidate outcomes remain Main-owned facts.  This module
freezes their exact first-exposure identity in Lab, then evaluates the virtual
control/treatment cohorts without consulting the legacy filter-shadow ledger.
The adapter is observe-only: it never publishes a treatment Manifest.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
from statistics import median
from typing import Any, Mapping

from aletheia.learning.outcomes import candidate_outcome_temporal_reason
from aletheia.observation_lab.db import lab_transaction, readonly_lab_db
from aletheia.storage.sqlite import get_db, register_instant_us

_SUPPORTED_FILTERS = frozenset({
    "family_ttl", "observer_pre_filter", "ranker_confidence_gate",
})
_VERSIONED_FILTERS = frozenset({"family_ttl", "observer_pre_filter"})
_CONFIDENCE_LADDER = ("speculative", "medium", "high")
_MAX_CAPTURE_UNITS = 5_000
_SHRINKAGE_K = 20.0


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _not_after(value: Any, cutoff: datetime) -> bool:
    parsed = _parse_time(value)
    return parsed is not None and parsed <= cutoff


def _generation(
    observation_id: str,
    generation: int | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    try:
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                return None
            if generation is None:
                root = lab.execute(
                    "SELECT * FROM observation_windows WHERE observation_id=? "
                    "ORDER BY generation DESC LIMIT 1",
                    (observation_id,),
                ).fetchone()
            else:
                root = lab.execute(
                    "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
                    (observation_id, int(generation)),
                ).fetchone()
            if root is None or str(root["kind"]) != "filter_shadow":
                return None
            contract = lab.execute(
                "SELECT * FROM observation_generation_contracts "
                "WHERE observation_id=? AND generation=?",
                (root["observation_id"], int(root["generation"])),
            ).fetchone()
            if contract is None:
                return None
            return dict(root), _object(root["spec_json"]), dict(contract)
    except (FileNotFoundError, OSError, sqlite3.Error):
        return None


def is_filter_shadow_generation(
    observation_id: str,
    generation: int | None,
) -> bool:
    target = _generation(observation_id, generation)
    if target is None:
        return False
    _, spec, _ = target
    return (
        not str(spec.get("path_id") or "").strip()
        and str(spec.get("filter_name") or "") in _SUPPORTED_FILTERS
    )


def _validate_spec(spec: Mapping[str, Any]) -> str | None:
    filter_name = str(spec.get("filter_name") or "")
    if filter_name not in _SUPPORTED_FILTERS:
        return "filter_shadow_adapter_unavailable"
    if filter_name in _VERSIONED_FILTERS and not str(spec.get("filter_version") or "").strip():
        return "filter_version_unavailable"
    control = _object(spec.get("control"))
    treatment = _object(spec.get("treatment"))
    if not control or not treatment:
        return "filter_shadow_parameters_unavailable"
    if filter_name == "family_ttl":
        try:
            control_ttl = int(control["ttl_sec"])
            treatment_ttl = int(treatment["ttl_sec"])
        except (KeyError, TypeError, ValueError):
            return "filter_shadow_parameters_unavailable"
        if control_ttl <= 0 or treatment_ttl <= control_ttl:
            return "filter_shadow_parameters_invalid"
    elif filter_name == "observer_pre_filter":
        try:
            control_rsi = _finite(control["rsi_deviation"])
            control_atr = _finite(control["atr_pct"])
            treatment_rsi = _finite(treatment["rsi_deviation"])
            treatment_atr = _finite(treatment["atr_pct"])
        except (KeyError, TypeError, ValueError):
            return "filter_shadow_parameters_unavailable"
        if None in {control_rsi, control_atr, treatment_rsi, treatment_atr}:
            return "filter_shadow_parameters_unavailable"
        if (
            treatment_rsi > control_rsi
            or treatment_atr > control_atr
            or (treatment_rsi == control_rsi and treatment_atr == control_atr)
        ):
            return "filter_shadow_parameters_invalid"
    else:
        control_confidence = str(
            control.get("minimum") or control.get("minimum_confidence") or ""
        ).strip().lower()
        treatment_confidence = str(
            treatment.get("minimum") or treatment.get("minimum_confidence") or ""
        ).strip().lower()
        if (
            control_confidence not in _CONFIDENCE_LADDER
            or treatment_confidence not in _CONFIDENCE_LADDER
            or _CONFIDENCE_LADDER.index(treatment_confidence)
            >= _CONFIDENCE_LADDER.index(control_confidence)
        ):
            return "filter_shadow_parameters_invalid"
    return None


def _source_rows(
    *,
    spec: Mapping[str, Any],
    root: Mapping[str, Any],
    as_of_at: str,
) -> tuple[list[dict[str, Any]], bool]:
    filter_name = str(spec["filter_name"])
    version = str(spec.get("filter_version") or "").strip()
    exact_version = filter_name in _VERSIONED_FILTERS
    collect_until = _parse_time(root.get("collect_until"))
    if collect_until is None:
        return [], False
    started_at = _parse_time(root.get("started_at"))
    if started_at is None:
        raise ValueError("observation_started_at_unavailable")
    if started_at >= collect_until:
        raise ValueError("observation_interval_unavailable")
    as_of = _parse_time(as_of_at)
    if as_of is None:
        return [], False
    upper_operator = "<=" if as_of < collect_until else "<"
    partition = (
        "fd.outcome_candidate_key, fd.filter_version"
        if exact_version else "fd.outcome_candidate_key"
    )
    version_clause = " AND fd.filter_version=?" if exact_version else ""
    db = get_db()
    register_instant_us(db)
    params: list[Any] = [
        filter_name,
        str(spec["stage"]),
        str(spec["scanner_family"]),
        str(spec["direction"]).lower(),
        str(spec["market_type"]).lower(),
    ]
    if exact_version:
        params.append(version)
    malformed = db.execute(
        "SELECT 1 FROM filter_decisions fd"
        " WHERE fd.filter_name=? AND fd.stage=?"
        " AND COALESCE(fd.scanner_family,'unknown')=?"
        " AND fd.direction=? AND fd.market_type=?"
        " AND fd.decision IN ('passed','rejected')"
        " AND fd.outcome_candidate_key IS NOT NULL"
        " AND fd.outcome_candidate_key <> ''"
        f"{version_clause}"
        " AND aletheia_instant_us(fd.evaluated_at) IS NULL LIMIT 1",
        tuple(params),
    ).fetchone()
    if malformed is not None:
        raise ValueError("filter_shadow_source_time_unavailable")
    params.extend((str(root["started_at"]), as_of_at, _MAX_CAPTURE_UNITS + 1))
    rows = db.execute(
        "WITH ranked AS ("
        " SELECT fd.*, ROW_NUMBER() OVER ("
        f" PARTITION BY {partition} "
        " ORDER BY aletheia_instant_us(fd.evaluated_at), fd.rowid"
        " ) AS _first_rank"
        " FROM filter_decisions fd"
        " WHERE fd.filter_name=? AND fd.stage=?"
        " AND COALESCE(fd.scanner_family,'unknown')=?"
        " AND fd.direction=? AND fd.market_type=?"
        " AND fd.decision IN ('passed','rejected')"
        " AND fd.outcome_candidate_key IS NOT NULL"
        " AND fd.outcome_candidate_key <> ''"
        f"{version_clause}"
        ") SELECT * FROM ranked WHERE _first_rank=1"
        " AND aletheia_instant_us(evaluated_at) > aletheia_instant_us(?)"
        f" AND aletheia_instant_us(evaluated_at) {upper_operator} aletheia_instant_us(?)"
        " ORDER BY aletheia_instant_us(evaluated_at), outcome_candidate_key LIMIT ?",
        tuple(params),
    ).fetchall()
    capped = len(rows) > _MAX_CAPTURE_UNITS
    return [dict(row) for row in rows[:_MAX_CAPTURE_UNITS]], capped


def _confidence(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in _CONFIDENCE_LADDER else None


def _arm_for_row(
    spec: Mapping[str, Any],
    row: Mapping[str, Any],
) -> tuple[str | None, str]:
    name = str(spec["filter_name"])
    control = _object(spec.get("control"))
    params = _object(row.get("parameter_snapshot"))
    if name == "family_ttl":
        try:
            matches_control = int(params["ttl_sec"]) == int(control["ttl_sec"])
        except (KeyError, TypeError, ValueError):
            matches_control = False
    elif name == "observer_pre_filter":
        row_rsi = _finite(params.get("rsi_deviation"))
        row_atr = _finite(params.get("atr_pct"))
        matches_control = (
            row_rsi is not None
            and row_atr is not None
            and row_rsi == _finite(control.get("rsi_deviation"))
            and row_atr == _finite(control.get("atr_pct"))
        )
    else:
        matches_control = _confidence(
            params.get("minimum") or params.get("minimum_confidence")
        ) == _confidence(
            control.get("minimum") or control.get("minimum_confidence")
        )
    if not matches_control:
        return None, "filter_shadow_invalid_causal_input"
    if str(row.get("decision") or "") == "passed":
        return "control", "filter_shadow_control"
    if str(row.get("decision") or "") != "rejected":
        return None, "filter_shadow_invalid_decision"
    inputs = _object(row.get("input_snapshot"))
    treatment = _object(spec.get("treatment"))
    if name == "family_ttl":
        now = _parse_time(inputs.get("now"))
        expires = _parse_time(inputs.get("expires_at"))
        try:
            row_ttl = int(params.get("ttl_sec"))
            treatment_ttl = int(treatment["ttl_sec"])
        except (KeyError, TypeError, ValueError):
            return None, "filter_shadow_invalid_causal_input"
        if now is None or expires is None or row_ttl <= 0:
            return None, "filter_shadow_invalid_causal_input"
        lateness = max(0, round((now - expires).total_seconds()))
        return (
            ("treatment", "filter_shadow_treatment")
            if row_ttl + lateness <= treatment_ttl
            else (None, "filter_shadow_no_trigger")
        )
    if name == "observer_pre_filter":
        rsi = _finite(inputs.get("rsi"))
        atr = _finite(inputs.get("atr_pct"))
        try:
            rsi_threshold = _finite(treatment["rsi_deviation"])
            atr_threshold = _finite(treatment["atr_pct"])
        except (KeyError, TypeError, ValueError):
            return None, "filter_shadow_invalid_causal_input"
        if None in {rsi, atr, rsi_threshold, atr_threshold}:
            return None, "filter_shadow_invalid_causal_input"
        return (
            ("treatment", "filter_shadow_treatment")
            if abs(rsi - 50.0) > rsi_threshold or atr > atr_threshold
            else (None, "filter_shadow_no_trigger")
        )
    confidence = _confidence(inputs.get("confidence"))
    control = _confidence(
        control.get("minimum") or control.get("minimum_confidence")
    )
    treatment_confidence = _confidence(
        treatment.get("minimum") or treatment.get("minimum_confidence")
    )
    if confidence is None or control is None or treatment_confidence is None:
        return None, "filter_shadow_invalid_causal_input"
    return (
        ("treatment", "filter_shadow_treatment")
        if _CONFIDENCE_LADDER.index(treatment_confidence)
        <= _CONFIDENCE_LADDER.index(confidence)
        < _CONFIDENCE_LADDER.index(control)
        else (None, "filter_shadow_no_trigger")
    )


def _row_identity(row: Mapping[str, Any], *, assignment: str | None) -> dict[str, Any]:
    outcome_key = str(row.get("outcome_candidate_key") or "")
    return {
        "source_id": str(row.get("id") or ""),
        "candidate_key": str(row.get("candidate_key") or outcome_key),
        "outcome_candidate_key": outcome_key,
        "decision": str(row.get("decision") or ""),
        "filter_version": str(row.get("filter_version") or ""),
        "input_snapshot": str(row.get("input_snapshot") or "{}"),
        "parameter_snapshot": str(row.get("parameter_snapshot") or "{}"),
        "evaluated_at": str(row.get("evaluated_at") or ""),
        "assignment": assignment,
    }


def _assert_identity(existing: Mapping[str, Any], expected: Mapping[str, Any], fields: tuple[str, ...]) -> None:
    if any(existing[field] != expected[field] for field in fields):
        raise sqlite3.IntegrityError("filter_shadow_canonical_identity_conflict")


def capture_filter_shadow_exposures(
    observation_id: str,
    generation: int | None = None,
    *,
    as_of_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Freeze first filter exposures from Main facts into canonical Lab rows."""
    target = _generation(observation_id, generation)
    if target is None:
        return {
            "available": False,
            "reason": "observation_generation_unavailable",
            "observation_id": observation_id,
            "generation": generation,
        }
    root, spec, contract = target
    actual_generation = int(root["generation"])
    at = _parse_time(as_of_at) if as_of_at is not None else datetime.now(timezone.utc)
    if at is None:
        raise ValueError("as_of_at_must_be_utc")
    collect_until = _parse_time(root.get("collect_until"))
    if collect_until is None:
        return {
            "available": False,
            "reason": "observation_collect_until_unavailable",
            "observation_id": observation_id,
            "generation": actual_generation,
        }
    source_at = min(at, collect_until)
    invalid = _validate_spec(spec)
    if invalid:
        return {
            "available": False,
            "reason": invalid,
            "observation_id": observation_id,
            "generation": actual_generation,
        }
    from aletheia.observation_lab.canonical import get_authority_source

    try:
        authority = get_authority_source()
    except (FileNotFoundError, OSError, RuntimeError, sqlite3.Error):
        return {
            "available": False,
            "reason": "observation_authority_unavailable",
            "observation_id": observation_id,
            "generation": actual_generation,
        }
    if authority.source != "lab":
        return {
            "available": False,
            "reason": "observation_lab_authority_required",
            "observation_id": observation_id,
            "generation": actual_generation,
        }
    try:
        rows, capped = _source_rows(
            spec=spec, root=root, as_of_at=source_at.isoformat(),
        )
    except ValueError as exc:
        return {
            "available": False,
            "reason": str(exc),
            "observation_id": observation_id,
            "generation": actual_generation,
        }
    if capped:
        return {
            "available": False,
            "reason": "filter_shadow_capture_cap_reached",
            "observation_id": observation_id,
            "generation": actual_generation,
        }
    captured_at = _now()
    units_inserted = 0
    exposures_inserted = 0
    excluded = 0
    source_rows: list[dict[str, Any]] = []
    with lab_transaction(immediate=True) as lab:
        current = lab.execute(
            "SELECT status FROM observation_windows WHERE observation_id=? AND generation=?",
            (observation_id, actual_generation),
        ).fetchone()
        if current is None:
            raise ValueError("observation_generation_unavailable")
        if str(current[0]) != "active":
            return {
                "available": True,
                "observation_id": observation_id,
                "generation": actual_generation,
                "status": str(current[0]),
                "units_inserted": 0,
                "exposures_inserted": 0,
                "excluded_units": 0,
            }
        payload_hash = _hash({})
        for sequence, row in enumerate(rows, start=1):
            known_at = _parse_time(row.get("evaluated_at"))
            if known_at is None:
                raise ValueError("filter_shadow_source_time_unavailable")
            assignment, reason = _arm_for_row(spec, row)
            if assignment is None:
                excluded += 1
            identity = _row_identity(row, assignment=assignment)
            source_hash = _hash(identity)
            candidate_key = identity["candidate_key"]
            unit_key = identity["outcome_candidate_key"]
            source_ref = identity["source_id"] or _hash(identity)
            unit_id = _hash({
                "observation_id": observation_id,
                "generation": actual_generation,
                "unit_key": unit_key,
            })
            existing_unit = lab.execute(
                "SELECT * FROM observation_units WHERE unit_id=?", (unit_id,)
            ).fetchone()
            unit_expected = {
                "spec_hash": root["spec_hash"],
                "unit_key": unit_key,
                "source_kind": "filter_decision",
                "source_ref": source_ref,
                "source_hash": source_hash,
                "known_at": known_at.isoformat(),
                "admission": "admitted" if assignment else "excluded",
                "reason": reason,
            }
            if existing_unit is not None:
                _assert_identity(
                    existing_unit, unit_expected,
                    ("spec_hash", "unit_key", "source_kind", "source_ref", "source_hash", "known_at", "admission", "reason"),
                )
            else:
                lab.execute(
                    "INSERT INTO observation_units "
                    "(unit_id, observation_id, generation, spec_hash, unit_key, source_kind, source_ref, source_hash, "
                    "known_at, captured_at, producer_commit, producer_runtime_session_id, admission, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        unit_id, observation_id, actual_generation, root["spec_hash"], unit_key,
                        "filter_decision", source_ref, source_hash, known_at.isoformat(), captured_at,
                        row.get("code_commit"), None,
                        "admitted" if assignment else "excluded", reason,
                    ),
                )
                units_inserted += 1
            source_rows.append({**identity, "source_hash": source_hash, "reason": reason})
            if assignment is None:
                continue
            receipt_id = f"filter-shadow:{observation_id}:{actual_generation}:{unit_key}"
            exposure_expected = {
                "observation_id": observation_id,
                "generation": actual_generation,
                "assignment_unit_key": unit_key,
                "candidate_key": candidate_key,
                "assignment": assignment,
                "applied": 0,
                "behavior_changed": 0,
                "baseline_payload_hash": payload_hash,
                "applied_payload_hash": payload_hash,
                "outcome_candidate_key": unit_key,
                "outcome_source": "fixed_horizon_candidate",
                "resolution_reason": reason,
                "behavior_contract_hash": contract["behavior_contract_hash"],
                "behavior_policy_epoch_ref": contract.get("behavior_policy_epoch_ref"),
                "authority_source": authority.source,
                "authority_epoch": authority.epoch,
                "exposed_at": known_at.isoformat(),
            }
            existing_exposure = lab.execute(
                "SELECT * FROM observation_exposures WHERE source_receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if existing_exposure is not None:
                _assert_identity(
                    existing_exposure, exposure_expected,
                    tuple(exposure_expected),
                )
            else:
                lab.execute(
                    "INSERT INTO observation_exposures "
                    "(exposure_id, source_receipt_id, receipt_sequence, observation_id, generation, manifest_id, manifest_hash, "
                    "assignment_unit_key, candidate_key, assignment, applied, behavior_changed, baseline_payload_hash, applied_payload_hash, "
                    "baseline_payload_json, applied_payload_json, outcome_candidate_key, outcome_source, resolution_reason, behavior_contract_hash, "
                    "behavior_policy_epoch_ref, active_manifest_snapshot_json, manifest_set_fingerprint, revocation_snapshot_fingerprint, "
                    "authority_source, authority_epoch, runtime_session_id, producer_commit, exposed_at, imported_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        receipt_id, receipt_id, sequence, observation_id, actual_generation,
                        None, None, unit_key, candidate_key, assignment, 0, 0,
                        payload_hash, payload_hash, "{}", "{}", unit_key,
                        "fixed_horizon_candidate", reason,
                    contract["behavior_contract_hash"], contract.get("behavior_policy_epoch_ref"),
                    "[]", None, None, authority.source, authority.epoch, None, row.get("code_commit"),
                        known_at.isoformat(), captured_at,
                    ),
                )
                exposures_inserted += 1
            from aletheia.observation_lab.canonical import upsert_retention_claim

            upsert_retention_claim(
                observation_id=observation_id,
                generation=actual_generation,
                source_kind="outcome_candidate_key",
                source_ref=unit_key,
                source_hash=_hash({
                    "receipt_id": receipt_id,
                    "sequence": sequence,
                    "outcome_candidate_key": unit_key,
                }),
                claim_domain="filter_candidate_outcomes",
                derived_from_fingerprint=payload_hash,
                db=lab,
            )
    return {
        "available": True,
        "observation_id": observation_id,
        "generation": actual_generation,
        "units_inserted": units_inserted,
        "exposures_inserted": exposures_inserted,
        "excluded_units": excluded,
        "source_fingerprint": _hash(source_rows),
    }


def _gate_values(spec: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[int, float, float] | None:
    gates = _object(contract.get("gates_json"))

    def declared(*names: str, default: Any) -> Any:
        for source in (gates, spec):
            for name in names:
                if name in source:
                    return source[name]
        return default

    try:
        min_samples = int(declared("min_samples", "min_samples_per_arm", default=20))
        min_coverage = _finite(declared("min_coverage", default=0.70))
        min_edge = _finite(declared("min_edge_delta_pct", default=0.05))
    except (TypeError, ValueError, OverflowError):
        return None
    if min_samples < 1 or min_coverage is None or min_edge is None:
        return None
    if not 0.0 <= min_coverage <= 1.0 or min_edge < 0.0:
        return None
    return min_samples, min_coverage, min_edge


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [
        float(row["net_return_pct"])
        for row in rows
        if row.get("status") == "settled"
        and row.get("temporal_exclusion_reason") is None
        and row.get("net_return_pct") is not None
    ]
    return {
        "candidate_count": len(rows),
        "settled_n": len(settled),
        "outcome_coverage": round(len(settled) / len(rows), 4) if rows else 0.0,
        "raw_avg_net_pct": round(sum(settled) / len(settled), 6) if settled else None,
        "median_net_pct": round(median(settled), 6) if settled else None,
    }


def _shrunk_mean(metrics: Mapping[str, Any], prior: float) -> float | None:
    count = int(metrics.get("settled_n") or 0)
    raw = metrics.get("raw_avg_net_pct")
    if not count or raw is None:
        return None
    return round(
        (count * float(raw) + _SHRINKAGE_K * prior) / (count + _SHRINKAGE_K),
        6,
    )


def evaluate_filter_shadow_observation(
    observation_id: str,
    generation: int | None = None,
    *,
    evaluation_kind: str = "monitoring",
    as_of_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Capture and evaluate one canonical filter-shadow generation."""
    if evaluation_kind != "monitoring":
        return {
            "available": False,
            "reason": "filter_shadow_confirmatory_unsupported",
            "observation_id": observation_id,
            "generation": generation,
            "evaluation_kind": evaluation_kind,
        }
    at = _parse_time(as_of_at) if as_of_at is not None else datetime.now(timezone.utc)
    if at is None:
        raise ValueError("as_of_at_must_be_utc")
    capture = capture_filter_shadow_exposures(
        observation_id, generation, as_of_at=at,
    )
    if not capture.get("available"):
        return {**capture, "evaluation_kind": evaluation_kind}
    target = _generation(observation_id, generation)
    if target is None:
        return {
            "available": False,
            "reason": "observation_generation_unavailable",
            "observation_id": observation_id,
            "generation": generation,
            "evaluation_kind": evaluation_kind,
        }
    root, spec, contract = target
    actual_generation = int(root["generation"])
    invalid = _validate_spec(spec)
    gates = _gate_values(spec, contract)
    if invalid or gates is None:
        return {
            "available": False,
            "reason": invalid or "filter_shadow_gates_unavailable",
            "observation_id": observation_id,
            "generation": actual_generation,
            "evaluation_kind": evaluation_kind,
        }
    with readonly_lab_db(allow_missing=True) as lab:
        if lab is None:
            return {
                "available": False,
                "reason": "lab_unavailable",
                "observation_id": observation_id,
                "generation": actual_generation,
                "evaluation_kind": evaluation_kind,
            }
        units = [dict(row) for row in lab.execute(
            "SELECT unit_key, source_hash, admission, reason, known_at FROM observation_units "
            "WHERE observation_id=? AND generation=? ORDER BY known_at, unit_key",
            (observation_id, actual_generation),
        ).fetchall() if _not_after(row["known_at"], at)]
        exposures = [dict(row) for row in lab.execute(
            "SELECT * FROM observation_exposures WHERE observation_id=? AND generation=? "
            "ORDER BY exposed_at, source_receipt_id",
            (observation_id, actual_generation),
        ).fetchall() if _not_after(row["exposed_at"], at)]
        cursor = lab.execute(
            "SELECT contiguous_sequence, source_checkpoint_fingerprint "
            "FROM observation_import_cursors WHERE source_name='main'"
        ).fetchone()
    from aletheia.counterfactual.filter_replay import (
        _bootstrap_delta_lcb,
        _fetch_outcomes_for,
        _improvement_concentration,
    )
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    outcome_keys = [
        str(row["outcome_candidate_key"])
        for row in exposures
        if row.get("outcome_candidate_key")
    ]

    def hydrate(horizon: str) -> list[dict[str, Any]]:
        outcomes = _fetch_outcomes_for(outcome_keys, horizon=horizon)
        symbols: dict[str, str] = {}
        if outcome_keys:
            db = get_db()
            for start in range(0, len(outcome_keys), 500):
                batch = outcome_keys[start:start + 500]
                placeholders = ", ".join("?" for _ in batch)
                for row in db.execute(
                    "SELECT outcome_candidate_key, symbol FROM filter_candidate_outcomes "
                    f"WHERE horizon=? AND cost_model_version=? AND outcome_candidate_key IN ({placeholders})",
                    (horizon, COST_MODEL_VERSION, *batch),
                ).fetchall():
                    symbols[str(row["outcome_candidate_key"])] = str(row["symbol"] or "")
        hydrated: list[dict[str, Any]] = []
        for row in exposures:
            key = str(row.get("outcome_candidate_key") or "")
            outcome = dict(outcomes.get(key) or {})
            outcome["assignment"] = row["assignment"]
            outcome["outcome_candidate_key"] = key
            outcome["symbol"] = symbols.get(key)
            outcome["first_evaluated_at"] = row["exposed_at"]
            outcome["temporal_exclusion_reason"] = candidate_outcome_temporal_reason(
                outcome, as_of_at=at,
            )
            hydrated.append(outcome)
        return hydrated

    hydrated = hydrate(str(spec["horizon"]))
    horizon_rows = {str(spec["horizon"]): hydrated}
    filter_name = str(spec["filter_name"])
    control_rows = [row for row in hydrated if row["assignment"] == "control"]
    newly_admitted_rows = [row for row in hydrated if row["assignment"] == "treatment"]
    control = _metrics(control_rows)
    newly_admitted = _metrics(newly_admitted_rows)
    treatment_rows = (
        [*control_rows, *newly_admitted_rows]
        if filter_name == "observer_pre_filter"
        else newly_admitted_rows
    )
    treatment = _metrics(treatment_rows)
    prior = float(control["raw_avg_net_pct"] or 0.0)
    treatment_shrunk = _shrunk_mean(treatment, prior)
    edge_delta = (
        round(treatment_shrunk - float(control["raw_avg_net_pct"]), 6)
        if treatment_shrunk is not None and control["raw_avg_net_pct"] is not None
        else None
    )
    bootstrap_delta_lcb = None
    improvement_concentration = None
    companion_horizons: dict[str, Any] = {}
    companion_horizons_ok = True
    if filter_name == "observer_pre_filter":
        bootstrap_delta_lcb = _bootstrap_delta_lcb(
            control_rows,
            treatment_rows,
            seed_material=(
                f"{spec.get('filter_version')}|{spec['scanner_family']}|"
                f"{spec['direction']}|{spec['market_type']}|{spec['treatment']}"
            ),
        )
        improvement_concentration = _improvement_concentration(
            newly_admitted_rows,
            control_mean=float(control["raw_avg_net_pct"] or 0.0),
        )
        for companion_horizon in ("1h", "4h"):
            companion_rows = hydrate(companion_horizon)
            horizon_rows[companion_horizon] = companion_rows
            companion_control_rows = [
                row for row in companion_rows if row["assignment"] == "control"
            ]
            companion_newly_rows = [
                row for row in companion_rows if row["assignment"] == "treatment"
            ]
            companion_control = _metrics(companion_control_rows)
            companion_union = _metrics([*companion_control_rows, *companion_newly_rows])
            companion_shrunk = _shrunk_mean(
                companion_union,
                float(companion_control["raw_avg_net_pct"] or 0.0),
            )
            companion_delta = (
                round(
                    companion_shrunk - float(companion_control["raw_avg_net_pct"]),
                    6,
                )
                if companion_shrunk is not None
                and companion_control["raw_avg_net_pct"] is not None
                else None
            )
            companion_horizons[companion_horizon] = {
                "control": companion_control,
                "newly_admitted": _metrics(companion_newly_rows),
                "treatment_union": companion_union,
                "treatment_shrunk_avg_net_pct": companion_shrunk,
                "delta_vs_control_pct": companion_delta,
            }
            if companion_delta is None or companion_delta < -0.25:
                companion_horizons_ok = False
    min_samples, min_coverage, min_edge = gates
    enough = (
        int(control["settled_n"]) >= min_samples
        and float(control["outcome_coverage"]) >= min_coverage
        and int(newly_admitted["settled_n"]) >= min_samples
        and float(newly_admitted["outcome_coverage"]) >= min_coverage
    )
    if enough:
        supported = (
            treatment_shrunk is not None
            and edge_delta is not None
            and treatment_shrunk >= min_edge
            and edge_delta >= min_edge
        )
        if filter_name == "observer_pre_filter":
            supported = bool(
                supported
                and bootstrap_delta_lcb is not None
                and bootstrap_delta_lcb > 0.0
                and improvement_concentration is not None
                and improvement_concentration["max_symbol_share"] <= 0.30
                and improvement_concentration["max_utc_day_share"] <= 0.30
                and companion_horizons_ok
            )
        disposition = "ready_for_review" if supported else "completed_no_support"
    else:
        disposition = "collecting"
    verdict = (
        "positive" if disposition == "ready_for_review"
        else "neutral" if disposition == "completed_no_support"
        else "insufficient_data"
    )
    note = (
        "Prospective evidence gate met; manual review only, production unchanged."
        if disposition == "ready_for_review"
        else "Prospective evidence gate met without support; production unchanged."
        if disposition == "completed_no_support"
        else "Collecting future first-exposure control and treatment outcomes."
    )
    source_fingerprint = _hash([
        {
            "unit_key": row["unit_key"],
            "source_hash": row["source_hash"],
            "admission": row["admission"],
            "reason": row["reason"],
        }
        for row in units
    ])
    outcome_fingerprint = _hash({
        horizon: [
            {
                "candidate": row["outcome_candidate_key"],
                "assignment": row["assignment"],
                "symbol": row.get("symbol"),
                "status": row.get("status"),
                "entry_ts": row.get("entry_ts"),
                "due_at": row.get("due_at"),
                "settled_at": row.get("settled_at"),
                "net_return_pct": row.get("net_return_pct"),
                "temporal_exclusion_reason": row.get("temporal_exclusion_reason"),
            }
            for row in rows
        ]
        for horizon, rows in horizon_rows.items()
    })
    source_cutoff = {
        "source_name": "main",
        "main_import_contiguous_sequence": int(cursor[0]) if cursor else 0,
        "source_checkpoint_fingerprint": (
            str(cursor[1]) if cursor and cursor[1] else None
        ),
        "collect_until": root["collect_until"],
        "filter_decision_fingerprint": source_fingerprint,
        "outcome_fingerprint": outcome_fingerprint,
    }
    excluded_reasons: dict[str, int] = {}
    for row in units:
        if row["admission"] == "admitted":
            continue
        reason = str(row.get("reason") or "unavailable")
        excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
    payload = {
        "source": "observation_lab_filter_shadow",
        "filter_name": spec["filter_name"],
        "assignment_policy": "prospective_first_exposure",
        "control": control,
        "treatment": treatment,
        "newly_admitted": newly_admitted,
        "treatment_union": treatment if filter_name == "observer_pre_filter" else None,
        "treatment_shrunk_avg_net_pct": treatment_shrunk,
        "edge_delta_pct": edge_delta,
        "bootstrap_delta_lcb_pct": bootstrap_delta_lcb,
        "improvement_concentration": improvement_concentration,
        "companion_horizons_ok": companion_horizons_ok,
        "companion_horizons": companion_horizons,
        "unit_count": len(units),
        "admitted_unit_count": sum(1 for row in units if row["admission"] == "admitted"),
        "excluded_unit_count": sum(1 for row in units if row["admission"] == "excluded"),
        "no_trigger_unit_count": excluded_reasons.get("filter_shadow_no_trigger", 0),
        "invalid_unit_count": sum(
            1 for row in units if str(row.get("reason") or "").endswith("invalid_causal_input")
        ),
        "unavailable_outcome_count": sum(
            1 for row in hydrated if row.get("temporal_exclusion_reason") is not None
        ),
        "excluded_unit_reasons": excluded_reasons,
        "disposition": disposition,
        "note": note,
    }
    input_fingerprint = _hash({
        "observation_id": observation_id,
        "generation": actual_generation,
        "spec_hash": root["spec_hash"],
        "contract_hash": contract["contract_hash"],
        "source_fingerprint": source_fingerprint,
        "outcome_fingerprint": outcome_fingerprint,
        "control": spec["control"],
        "treatment": spec["treatment"],
        "gates": gates,
    })
    with lab_transaction(immediate=True) as lab:
        from aletheia.observation_lab.canonical import (
            record_observation_evaluation,
            record_observation_result,
        )

        evaluation_id = record_observation_evaluation(
            observation_id=observation_id,
            generation=actual_generation,
            evaluation_kind=evaluation_kind,
            source_cutoff=_json(source_cutoff),
            input_fingerprint=input_fingerprint,
            exposure_fingerprint=source_fingerprint,
            outcome_fingerprint=outcome_fingerprint,
            result=payload,
            verdict=verdict,
            disposition="evaluated",
            db=lab,
        )
        result_id = record_observation_result(
            observation_id=observation_id,
            generation=actual_generation,
            spec_hash=str(root["spec_hash"]),
            source_cutoff=_json(source_cutoff),
            verdict=verdict,
            disposition="evaluated",
            result=payload,
            evaluation_id=evaluation_id,
            db=lab,
        )
    return {
        "available": True,
        "observation_id": observation_id,
        "generation": actual_generation,
        "path_id": None,
        "evaluation_kind": evaluation_kind,
        "source_cutoff": source_cutoff,
        "evaluations": [{
            "evaluation_id": evaluation_id,
            "result_id": result_id,
            **payload,
            "verdict": verdict,
            "reason": note,
        }],
        "verdict": verdict,
        "reason": note,
        "exposure_fingerprint": source_fingerprint,
        "safety_action": None,
    }


def list_filter_shadow_observations(
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]] | None:
    """Project canonical filter-shadow roots into the compatibility report shape."""
    try:
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                return None
            clauses = ["kind='filter_shadow'"]
            params: list[Any] = []
            if status:
                clauses.append("status=?")
                params.append(status)
            roots = lab.execute(
                "SELECT * FROM observation_windows WHERE "
                + " AND ".join(clauses)
                + " ORDER BY started_at DESC, observation_id, generation LIMIT ?",
                (*params, max(1, min(int(limit), 1000))),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for root in roots:
                spec = _object(root["spec_json"])
                latest = lab.execute(
                    "SELECT * FROM observation_evaluations WHERE observation_id=? AND generation=? "
                    "ORDER BY created_at DESC, evaluation_id DESC LIMIT 1",
                    (root["observation_id"], root["generation"]),
                ).fetchone()
                units = lab.execute(
                    "SELECT COUNT(*) FROM observation_units WHERE observation_id=? AND generation=?",
                    (root["observation_id"], root["generation"]),
                ).fetchone()[0]
                latest_payload = _object(latest["result_json"]) if latest is not None else {}
                projected_status = str(root["status"])
                if projected_status == "active":
                    projected_status = str(latest_payload.get("disposition") or "collecting")
                latest_view = None
                if latest is not None:
                    control = _object(latest_payload.get("control"))
                    treatment = _object(latest_payload.get("treatment"))
                    latest_view = {
                        **latest_payload,
                        "id": latest["evaluation_id"],
                        "evaluation_id": latest["evaluation_id"],
                        "disposition": latest_payload.get("disposition") or latest["disposition"],
                        "control_candidate_count": control.get("candidate_count", 0),
                        "control_settled_n": control.get("settled_n", 0),
                        "control_outcome_coverage": control.get("outcome_coverage", 0.0),
                        "control_raw_avg_net_pct": control.get("raw_avg_net_pct"),
                        "treatment_candidate_count": treatment.get("candidate_count", 0),
                        "treatment_settled_n": treatment.get("settled_n", 0),
                        "treatment_outcome_coverage": treatment.get("outcome_coverage", 0.0),
                        "treatment_raw_avg_net_pct": treatment.get("raw_avg_net_pct"),
                    }
                result.append({
                    "id": f"{root['observation_id']}:g{root['generation']}",
                    "observation_id": root["observation_id"],
                    "generation": int(root["generation"]),
                    "experiment_key": root["spec_hash"],
                    "filter_name": spec.get("filter_name"),
                    "filter_version": spec.get("filter_version"),
                    "stage": spec.get("stage"),
                    "scanner_family": spec.get("scanner_family"),
                    "direction": spec.get("direction"),
                    "market_type": spec.get("market_type"),
                    "horizon": spec.get("horizon"),
                    "control_parameter": spec.get("control") or {},
                    "treatment_parameter": spec.get("treatment") or {},
                    "assignment_policy": "prospective_first_exposure",
                    "status": projected_status,
                    "started_at": root["started_at"],
                    "min_samples": spec.get("min_samples"),
                    "min_coverage": spec.get("min_coverage"),
                    "min_edge_delta_pct": spec.get("min_edge_delta_pct"),
                    "frozen_units": int(units or 0),
                    "latest_evaluation": latest_view,
                })
            return result
    except (FileNotFoundError, OSError, sqlite3.Error):
        return None


__all__ = [
    "capture_filter_shadow_exposures",
    "evaluate_filter_shadow_observation",
    "is_filter_shadow_generation",
    "list_filter_shadow_observations",
]
