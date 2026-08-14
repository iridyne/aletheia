"""Deterministic, read-only evidence compilation for ``profit_gradient/v3``.

The selector in :mod:`profit_gradient` is intentionally blind to the database.
This module is the narrow adapter that gives it one frozen, provenance-bound
artifact.  It never writes, fetches data, evaluates an LLM, or grants
behaviour authority.  Sources that do not expose an explicit paired/control
contract remain unavailable instead of being turned into a guessed delta.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from typing import Any, Iterable, Mapping

from aletheia.analytics.candidate_facts import (
    FACT_CONTRACT_VERSION as PROFIT_LOOP_FACT_CONTRACT_VERSION,
    build_candidate_facts,
)
from aletheia.learning.outcomes import assess_candidate_outcome
from aletheia.learning.verdicts import METRIC_VERSION
from aletheia.analytics.execution_contribution import (
    _paired_position_cost,
)
from aletheia.analytics.position_replay import build_position_replay
from aletheia.analytics.profit_gradient import LAYERS
from aletheia.analytics.profit_loop_counterfactual import (
    COUNTERFACTUAL_CONTRACT_VERSION,
    profit_loop_registry_hash,
)
from aletheia.storage.hashing import sha256_json
from aletheia.storage.sqlite import readonly_db


PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION = "profit_gradient_evidence/v1"
_COMPILER_PRODUCER = {
    "module": "aletheia.analytics.profit_gradient_evidence",
    "contract_version": PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION,
    "source_mode": "database_read_only",
    "manual_layer_samples": False,
}
_FIXED_CANDIDATE_HORIZON = "24h"
_GRADIENT_COMPARISON_METHOD = "paired_same_unit"
_GRADIENT_OUTCOME_CONTRACT_VERSION = "v1"
_GRADIENT_STATISTICAL_UNIT_VERSION = PROFIT_LOOP_FACT_CONTRACT_VERSION
_LAYER_PATH_IDS = {
    "Selection": (),
    "Decision": (),
    "Risk": (),
    "Execution": (),
    "Holding": (),
    "Exit": (),
    "Costs": (),
}
_EPOCH_KIND = "evidence_anchor:profit_loop_observability"
_PROFIT_LOOP_BEHAVIOR_CONTRACT = "profit_loop_counterfactual/v1"
_PROFIT_LOOP_REGISTRY_DURATION_DAYS = 30
_PROFIT_LOOP_ELIGIBILITY = (
    "opened_on_or_after_registration_closed_by_cutoff_complete_position_fact_with_frozen_hypothesis_and_1m_prefix"
)
_PROFIT_LOOP_STOPPING_RULE = "single_locked_retrospective_evaluation_after_fixed_cutoff_no_optional_stopping"
_SECRET_FRAGMENTS = (
    "secret", "password", "passphrase", "api_key", "token", "credential", "private_key"
)
_WATERFALL_ORDER = (
    "source_unavailable",
    "outside_epoch",
    "outside_cutoff",
    "stale_evidence",
    "ambiguous_identity",
    "missing_identity",
    "epoch_mismatch",
    "unverified_or_unsettled_outcome",
    "incomplete_path",
    "cost_incomplete",
    "accounting_mismatch",
    "denominator_or_economic_unit_mismatch",
    "nonfinite_delta",
    "duplicate_assignment_unit",
    "paired_evidence_unavailable",
)


def _parse_utc(value: Any, *, field: str = "cutoff_at") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _finite(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def _time(value: Any) -> datetime | None:
    try:
        return _parse_utc(value, field="timestamp") if value not in (None, "") else None
    except ValueError:
        return None


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if any(fragment in str(key).lower() for fragment in _SECRET_FRAGMENTS)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _json_value(value: Any) -> Any:
    """Make source values safe for deterministic, NaN-free hashing."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_value(item) for item in value)
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"__nonfinite__": repr(value)}
    if isinstance(value, datetime):
        return _iso(value)
    return value


def _hash(value: Any) -> str:
    normalized = _json_value(value)
    if not isinstance(normalized, (dict, list)):
        normalized = {"value": normalized}
    return sha256_json(normalized)


def _table_exists(db: Any, table: str) -> bool:
    try:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def _columns(db: Any, table: str) -> set[str]:
    if not _table_exists(db, table):
        return set()
    try:
        return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _rows(db: Any, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, tuple(params)).fetchall()]


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, **extra}


def _rollback_snapshot(db: Any) -> None:
    try:
        if getattr(db, "in_transaction", False):
            db.rollback()
    except sqlite3.Error:
        pass


def _epoch_hash(
    *,
    epoch_kind: str,
    effective_values: Mapping[str, Any],
    affected_path_ids: Iterable[str],
    safety_required: bool,
) -> str:
    values = {str(key): value for key, value in effective_values.items() if str(key) != "code_commit"}
    payload = {
        "epoch_kind": epoch_kind,
        "effective_values": values,
        "affected_path_ids": sorted(set(str(value) for value in affected_path_ids)),
        "safety_required": bool(safety_required),
    }
    return _hash(payload)


def _resolve_registration(db: Any, evidence_epoch_id: str, cutoff: datetime) -> dict[str, Any]:
    if not evidence_epoch_id.strip():
        return _unavailable("evidence_epoch_id_missing", evidence_epoch_id=evidence_epoch_id)
    if not _table_exists(db, "learning_policy_epochs"):
        return _unavailable("evidence_epoch_table_unavailable", evidence_epoch_id=evidence_epoch_id)
    try:
        rows = _rows(
            db,
            "SELECT epoch_id, epoch_kind, effective_values_hash, effective_values_json, "
            "affected_path_ids_json, source_event_id, safety_required, started_at, ended_at, created_at "
            "FROM learning_policy_epochs WHERE epoch_id=?",
            (evidence_epoch_id,),
        )
    except (sqlite3.Error, OSError) as exc:
        return _unavailable("evidence_epoch_source_unavailable", error_type=type(exc).__name__)
    if len(rows) != 1:
        return _unavailable(
            "evidence_epoch_not_found" if not rows else "evidence_epoch_identity_ambiguous",
            evidence_epoch_id=evidence_epoch_id,
        )
    row = rows[0]
    if row.get("epoch_kind") != _EPOCH_KIND:
        return _unavailable("evidence_epoch_kind_mismatch", evidence_epoch_id=evidence_epoch_id)
    try:
        effective = json.loads(row.get("effective_values_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _unavailable("evidence_epoch_effective_values_malformed", evidence_epoch_id=evidence_epoch_id)
    if not isinstance(effective, dict):
        return _unavailable("evidence_epoch_effective_values_malformed", evidence_epoch_id=evidence_epoch_id)

    try:
        affected_paths = json.loads(row.get("affected_path_ids_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _unavailable("evidence_epoch_paths_malformed", evidence_epoch_id=evidence_epoch_id)
    if not isinstance(affected_paths, list) or set(map(str, affected_paths)) != {"profit_loop_observability"}:
        return _unavailable("evidence_epoch_path_mismatch", evidence_epoch_id=evidence_epoch_id)
    if row.get("effective_values_hash") != _epoch_hash(
        epoch_kind=str(row.get("epoch_kind")),
        effective_values=effective,
        affected_path_ids=affected_paths,
        safety_required=bool(row.get("safety_required")),
    ):
        return _unavailable("evidence_epoch_hash_mismatch", evidence_epoch_id=evidence_epoch_id)

    started = _time(row.get("started_at"))
    ended = _time(row.get("ended_at")) if row.get("ended_at") else None
    if started is None or (row.get("ended_at") and ended is None):
        return _unavailable("evidence_epoch_timestamp_malformed", evidence_epoch_id=evidence_epoch_id)
    if cutoff < started:
        return _unavailable("cutoff_before_evidence_epoch", evidence_epoch_id=evidence_epoch_id)
    if cutoff > datetime.now(timezone.utc):
        return _unavailable("cutoff_not_reached", evidence_epoch_id=evidence_epoch_id)
    if ended is not None and cutoff > ended:
        return _unavailable("cutoff_after_evidence_epoch", evidence_epoch_id=evidence_epoch_id)

    duration_days = _finite(effective.get("duration_days"), nonnegative=True)
    if duration_days != _PROFIT_LOOP_REGISTRY_DURATION_DAYS:
        return _unavailable("evidence_epoch_duration_mismatch", evidence_epoch_id=evidence_epoch_id)
    if cutoff != started + timedelta(days=_PROFIT_LOOP_REGISTRY_DURATION_DAYS):
        return _unavailable("cutoff_epoch_mismatch", evidence_epoch_id=evidence_epoch_id)
    if (
        effective.get("path_id") != "profit_loop_observability"
        or effective.get("behavior_contract") != _PROFIT_LOOP_BEHAVIOR_CONTRACT
        or effective.get("registry_hash") != profit_loop_registry_hash()
        or effective.get("assignment_unit") != "position_id"
        or effective.get("eligibility") != _PROFIT_LOOP_ELIGIBILITY
        or effective.get("stopping_rule") != _PROFIT_LOOP_STOPPING_RULE
        or not effective.get("deploy_commit")
        or not effective.get("behavior_fingerprint")
    ):
        return _unavailable("evidence_epoch_identity_incomplete", evidence_epoch_id=evidence_epoch_id)

    return {
        "status": "available",
        "evidence_epoch_id": evidence_epoch_id,
        "epoch_kind": row.get("epoch_kind"),
        "effective_values_hash": row.get("effective_values_hash"),
        "effective_values": _json_value(_redact(effective)),
        "affected_path_ids_json": row.get("affected_path_ids_json"),
        "source_event_id": row.get("source_event_id"),
        "safety_required": bool(row.get("safety_required")),
        "started_at": _iso(started),
        "ended_at": _iso(ended) if ended else None,
        "created_at": row.get("created_at"),
        "deploy_commit": effective.get("deploy_commit"),
        "behavior_fingerprint": effective.get("behavior_fingerprint"),
        "behavior_contract": effective.get("behavior_contract"),
        "registry_hash": effective.get("registry_hash"),
    }


def _resolve_cost_model(
    db: Any,
    cost_model_id: str,
    cutoff: datetime,
    *,
    position_facts: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Resolve a model only from persisted immutable outcome identities.

    There is no writable cost-model registry in this MVP.  A model is therefore
    usable only when at least one durable outcome explicitly names the requested
    version.  Position-ledger economics are handled separately and never have
    estimated candidate costs subtracted from them.
    """
    if not cost_model_id.strip():
        return _unavailable("cost_model_id_missing")
    sources: list[dict[str, Any]] = []
    malformed_count = 0
    source_error = False
    for table, time_fields in (
        ("filter_candidate_outcomes", ("entry_ts", "settled_at")),
        ("universe_forward_outcomes", ("entry_ts", "settled_at")),
    ):
        columns = _columns(db, table)
        # Exploratory shadow rows and rows without a terminal status are not a
        # confirmatory cost-model registry.
        if not {"cost_model_version", "status", *time_fields} <= columns:
            continue
        selected = ["cost_model_version", "status", *time_fields]
        if "id" in columns:
            selected.insert(0, "id")
        try:
            for row in _rows(db, f"SELECT {', '.join(selected)} FROM {table}"):
                if row.get("cost_model_version") != cost_model_id:
                    continue
                if str(row.get("status") or "").lower() != "settled":
                    continue
                if any(row.get(field) in (None, "") for field in time_fields):
                    malformed_count += 1
                    continue
                parsed_times = [_time(row.get(field)) for field in time_fields]
                if any(value is None for value in parsed_times):
                    malformed_count += 1
                    continue
                if any(value > cutoff for value in parsed_times if value is not None):
                    continue
                sources.append({"table": table, **row})
        except (sqlite3.Error, OSError):
            source_error = True
            continue
    if source_error:
        return _unavailable("cost_model_source_unavailable", cost_model_id=cost_model_id)
    if malformed_count:
        return _unavailable(
            "cost_model_source_malformed",
            cost_model_id=cost_model_id,
            malformed_count=malformed_count,
        )
    for fact in position_facts:
        identity = fact.get("cost_identity")
        if (
            fact.get("availability") == "available"
            and isinstance(identity, Mapping)
            and identity.get("cost_model_id") == cost_model_id
            and fact.get("fact_id") not in (None, "")
        ):
            sources.append(
                {
                    "table": "position_replays",
                    "fact_id": str(fact["fact_id"]),
                    "cost_model_id": cost_model_id,
                }
            )
    if not sources:
        return _unavailable("cost_model_not_recorded", cost_model_id=cost_model_id)
    return {
        "status": "available",
        "cost_model_id": cost_model_id,
        "source_count": len(sources),
        "source_tables": sorted({str(row["table"]) for row in sources}),
        "source_hash": _hash(sorted(sources, key=lambda row: (row["table"], _hash(row)))),
    }


def _load_candidates(db: Any, cutoff: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:

    try:
        report = build_candidate_facts(
            horizon=_FIXED_CANDIDATE_HORIZON,
            as_of_at=_iso(cutoff),
            db=db,
        )
    except (Exception,):  # source errors become unavailable evidence, never zero economics
        return _unavailable("candidate_fact_source_unavailable"), []
    facts = [fact for fact in report.get("facts", []) if isinstance(fact, dict)]
    facts.sort(key=lambda fact: (str(fact.get("event_at") or ""), str(fact.get("fact_id") or "")))
    return {
        "status": "available",
        "contract_version": report.get("contract_version"),
        "fixed_horizon": report.get("fixed_horizon"),
        "candidate_count": len(facts),
        "settled_outcome_count": report.get("settled_outcome_count"),
        "unavailable_count": report.get("unavailable_count", 0),
        "source_hash": _hash(
            {
                "contract_version": report.get("contract_version"),
                "fixed_horizon": report.get("fixed_horizon"),
                "candidate_count": report.get("candidate_count"),
                "settled_outcome_count": report.get("settled_outcome_count"),
                "unavailable_count": report.get("unavailable_count", 0),
                "unavailable_reasons": report.get("unavailable_reasons", {}),
                "exclusions": report.get("exclusions", []),
                "facts": facts,
            }
        ),
        "exclusions": report.get("exclusions", []),
    }, facts


def _load_positions(db: Any, cutoff: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not _table_exists(db, "positions"):
        return _unavailable("position_source_unavailable"), []
    try:
        rows = _rows(db, "SELECT id, status, opened_at, closed_at FROM positions ORDER BY datetime(opened_at), id")
    except (sqlite3.Error, OSError):
        return _unavailable("position_source_unavailable"), []
    ids = []
    errors: Counter[str] = Counter()
    for row in rows:
        opened = _time(row.get("opened_at"))
        closed = _time(row.get("closed_at")) if row.get("closed_at") else None
        if row.get("status") == "closed" and (
            opened is None or closed is None
        ):
            errors["malformed_closed_position_timestamp"] += 1
            continue
        if opened is None or opened > cutoff or (closed is not None and closed > cutoff):
            continue
        if row.get("status") == "closed" and closed is not None:
            ids.append(str(row["id"]))
    facts: list[dict[str, Any]] = []
    for position_id in ids:
        try:
            fact = build_position_replay(position_id, as_of_at=_iso(cutoff), db=db)
        except Exception as exc:  # one corrupt Position must not hide the cohort
            errors[type(exc).__name__] += 1
            continue
        if isinstance(fact, dict):
            facts.append(fact)
        else:
            errors["invalid_position_fact"] += 1
    facts.sort(key=lambda fact: (str(fact.get("event_at") or ""), str(fact.get("fact_id") or "")))
    return {
        "status": "available",
        "closed_position_count": len(ids),
        "replayed_count": len(facts),
        "error_count": sum(errors.values()),
        "error_types": dict(sorted(errors.items())),
        "source_hash": _hash(
            {
                "position_ids": ids,
                "errors": dict(sorted(errors.items())),
                "facts": facts,
            }
        ),
    }, facts


def _exposure_pair_id(row: Mapping[str, Any]) -> str | None:
    for field in ("pair_id", "matched_pair_id", "cohort_pair_id"):
        value = row.get(field)
        if value not in (None, ""):
            return str(value)
    try:
        manifest = json.loads(row.get("manifest_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(manifest, Mapping):
        for field in ("pair_id", "matched_pair_id", "cohort_pair_id"):
            value = manifest.get(field)
            if value not in (None, ""):
                return str(value)
    return None


def _confirmatory_exposure_reason(
    db: Any,
    row: Mapping[str, Any],
    *,
    fact: Mapping[str, Any],
    layer: str | None,
    exposed_at: datetime,
) -> str | None:
    """Validate the durable experiment/hypothesis contract before pairing."""
    experiment_id = str(row.get("experiment_id") or "")
    hypothesis_id = str(row.get("hypothesis_id") or "")
    path_id = str(row.get("path_id") or "")
    if layer is not None and path_id not in _LAYER_PATH_IDS.get(layer, ()):
        return "paired_evidence_unavailable"
    if not _table_exists(db, "learning_experiments") or not _table_exists(db, "learning_hypotheses"):
        return "missing_identity"
    try:
        experiments = _rows(db, "SELECT * FROM learning_experiments WHERE experiment_id=?", (experiment_id,))
        hypotheses = _rows(db, "SELECT * FROM learning_hypotheses WHERE hypothesis_id=?", (hypothesis_id,))
    except (sqlite3.Error, OSError):
        return "source_unavailable"
    if len(experiments) != 1 or len(hypotheses) != 1:
        return "ambiguous_identity"
    experiment = experiments[0]
    hypothesis = hypotheses[0]
    if experiment.get("hypothesis_id") != hypothesis_id or hypothesis.get("path_id") != path_id:
        return "ambiguous_identity"
    if str(experiment.get("stage") or "") not in {"paper_10", "paper_25", "paper_50", "paper_active"}:
        return "paired_evidence_unavailable"
    if str(row.get("experiment_stage") or "") != str(experiment.get("stage") or ""):
        return "ambiguous_identity"
    if str(hypothesis.get("target_horizon") or "") != _FIXED_CANDIDATE_HORIZON:
        return "stale_evidence"
    if str(experiment.get("primary_metric") or "") != "net_return_pct":
        return "stale_evidence"
    if isinstance(fact.get("fixed_horizon_outcome"), Mapping):
        if str(row.get("outcome_contract_version") or "") != "v1":
            return "stale_evidence"
    started = _time(experiment.get("started_at"))
    ended = _time(experiment.get("ended_at")) if experiment.get("ended_at") else None
    if started is None or exposed_at < started or (ended is not None and exposed_at > ended):
        return "outside_epoch"
    policy_epoch = str(row.get("concurrent_policy_epoch") or "")
    try:
        policy_rows = _rows(
            db,
            "SELECT effective_values_hash FROM learning_policy_epochs WHERE epoch_id=? AND epoch_kind=?",
            (policy_epoch, f"decision_policy:{path_id}"),
        )
    except (sqlite3.Error, OSError):
        return "source_unavailable"
    if len(policy_rows) != 1 or not policy_rows[0].get("effective_values_hash"):
        return "epoch_mismatch"
    try:
        from aletheia.learning.behavior_contracts import build_behavior_contract_hash
        from aletheia.learning.verdicts import METRIC_VERSION

        expected_hash = build_behavior_contract_hash(
            path_id=path_id,
            policy_hash=str(policy_rows[0]["effective_values_hash"]),
            target_horizon=str(hypothesis["target_horizon"]),
            outcome_contract_version=str(row.get("outcome_contract_version") or ""),
            metric_version=METRIC_VERSION,
        )
    except (KeyError, TypeError, ValueError):
        return "epoch_mismatch"
    if str(row.get("behavior_contract_hash") or "") != expected_hash:
        return "epoch_mismatch"
    return None


def _lookup_assignment(
    db: Any,
    *,
    fact: Mapping[str, Any],
    cutoff: datetime,
    epoch_start: datetime,
    epoch_id: str,
    observed_at: datetime | None = None,
    registration: Mapping[str, Any] | None = None,
    layer: str | None = None,
    metadata: dict[str, str] | None = None,
) -> tuple[str, str, str | None, str | None, str | None, str | None]:
    """Return arm, unit, pair, policy epoch, producer commit, and exclusion reason."""
    strict = registration is not None
    direct: list[Mapping[str, Any]] = []
    for key in ("assignment", "learning_assignment", "experiment_assignment"):
        value = fact.get(key)
        if isinstance(value, Mapping):
            direct.append(value)
    if direct and not strict:
        value = direct[0]
        arm = str(value.get("arm") or value.get("assignment") or "").lower()
        cluster = str(value.get("cluster_id") or value.get("assignment_unit_key") or "")
        policy_epoch = str(value.get("policy_epoch") or value.get("concurrent_policy_epoch") or "")
        if arm not in {"treatment", "control"}:
            return "", "", None, None, None, "ambiguous_identity"
        if not cluster:
            return "", "", None, policy_epoch or None, None, "missing_identity"
        if metadata is not None:
            metadata.update(
                {
                    "outcome_contract_version": str(value.get("outcome_contract_version") or ""),
                    "stage": str(value.get("experiment_stage") or value.get("stage") or ""),
                }
            )
        return arm, cluster, str(value.get("pair_id") or "") or None, policy_epoch or None, None, None

    if not _table_exists(db, "learning_exposures"):
        return "", "", None, None, None, "missing_identity"
    columns = _columns(db, "learning_exposures")
    required = {"assignment", "assignment_unit_key", "exposed_at", "concurrent_policy_epoch"}
    if strict:
        required |= {
            "experiment_id", "hypothesis_id", "path_id", "outcome_contract_version",
            "experiment_stage", "behavior_contract_hash", "code_commit",
        }
        required.add("outcome_candidate_key" if isinstance(fact.get("fixed_horizon_outcome"), Mapping) else "position_id")
        if isinstance(fact.get("fixed_horizon_outcome"), Mapping):
            required.add("outcome_source")
    if not required <= columns:
        return "", "", None, None, None, "missing_identity"
    fact_identity = fact.get("identity") if isinstance(fact.get("identity"), Mapping) else {}
    outcome = fact.get("fixed_horizon_outcome")
    expected_outcome_key = (
        (outcome or {}).get("outcome_candidate_key")
        if isinstance(outcome, Mapping)
        else fact_identity.get("outcome_candidate_key")
    )
    identity_field = "outcome_candidate_key" if expected_outcome_key not in (None, "") else "position_id"
    identity_value = expected_outcome_key if identity_field == "outcome_candidate_key" else fact.get("fact_id")
    if identity_field not in columns and not strict and "candidate_key" in columns:
        identity_field = "candidate_key"
    if identity_field not in columns or identity_value in (None, ""):
        return "", "", None, None, None, "missing_identity"
    order = "datetime(exposed_at)" + (", exposure_id" if "exposure_id" in columns else "")
    try:
        rows = _rows(
            db,
            f"SELECT * FROM learning_exposures WHERE {identity_field}=? ORDER BY {order}",
            (identity_value,),
        )
    except (sqlite3.Error, OSError):
        return "", "", None, None, None, "source_unavailable"
    if not rows:
        return "", "", None, None, None, "missing_identity"
    expected_commit = str(registration.get("deploy_commit") or "") if strict else ""
    if strict:
        expected_fingerprint = str(registration.get("behavior_fingerprint") or "")
        if not expected_commit or not expected_fingerprint or not _table_exists(db, "runtime_sessions"):
            return "", "", None, None, None, "epoch_mismatch"
        try:
            runtime_rows = _rows(
                db,
                "SELECT session_id FROM runtime_sessions WHERE boot_commit=? AND behavior_fingerprint=?",
                (expected_commit, expected_fingerprint),
            )
        except (sqlite3.Error, OSError):
            return "", "", None, None, None, "source_unavailable"
        if not runtime_rows:
            return "", "", None, None, None, "epoch_mismatch"
    valid: list[dict[str, Any]] = []
    for row in rows:
        exposed = _time(row.get("exposed_at"))
        if exposed is None:
            return "", "", None, None, None, "stale_evidence"
        if observed_at is not None and exposed > observed_at:
            continue
        if not (epoch_start <= exposed <= cutoff):
            continue
        if strict:
            if isinstance(fact.get("fixed_horizon_outcome"), Mapping) and row.get("outcome_candidate_key") != expected_outcome_key:
                continue
            if isinstance(fact.get("fixed_horizon_outcome"), Mapping) and str(row.get("outcome_source") or "") != "fixed_horizon_candidate":
                continue
            required_values = required - {"outcome_candidate_key", "position_id", "outcome_source"}
            if not all(str(row.get(field) or "").strip() for field in required_values):
                return "", "", None, None, None, "missing_identity"
            if expected_commit and str(row.get("code_commit")) != expected_commit:
                continue
            contract_reason = _confirmatory_exposure_reason(
                db,
                row,
                fact=fact,
                layer=layer,
                exposed_at=exposed,
            )
            if contract_reason is not None:
                if contract_reason == "source_unavailable":
                    return "", "", None, None, None, contract_reason
                continue
        valid.append(row)
    if not valid:
        return "", "", None, None, None, "epoch_mismatch" if expected_commit else "missing_identity"
    identities = {
        (
            str(row.get("assignment") or "").lower(),
            str(row.get("assignment_unit_key") or ""),
            str(row.get("concurrent_policy_epoch") or ""),
            str(row.get("experiment_id") or ""),
            str(row.get("hypothesis_id") or ""),
            str(row.get("path_id") or ""),
            str(row.get("behavior_contract_hash") or ""),
            str(row.get("code_commit") or ""),
            str(row.get("outcome_contract_version") or ""),
            str(row.get("experiment_stage") or ""),
        )
        for row in valid
    }
    if len(identities) != 1:
        return "", "", None, None, None, "ambiguous_identity"
    identity = next(iter(identities))
    (
        arm,
        cluster,
        policy_epoch,
        experiment_id,
        hypothesis_id,
        path_id,
        behavior_hash,
        producer_commit,
        outcome_contract_version,
        stage,
    ) = identity
    if (
        arm not in {"treatment", "control"}
        or not cluster
        or not policy_epoch
        or (strict and (not outcome_contract_version or not stage))
    ):
        return "", "", None, None, None, "missing_identity"
    if strict:
        if not _table_exists(db, "learning_policy_epochs"):
            return "", "", None, None, None, "epoch_mismatch"
        try:
            policy_rows = _rows(
                db,
                "SELECT epoch_id FROM learning_policy_epochs WHERE epoch_id=? AND epoch_kind=?",
                (policy_epoch, f"decision_policy:{path_id}"),
            )
        except (sqlite3.Error, OSError):
            return "", "", None, None, None, "source_unavailable"
        if len(policy_rows) != 1:
            return "", "", None, None, None, "epoch_mismatch"
    pair_values = {
        pair_id
        for row in valid
        if (pair_id := _exposure_pair_id(row)) is not None
    }
    if len(pair_values) > 1:
        return "", "", None, None, None, "ambiguous_identity"
    pair_id = next(iter(pair_values), None)
    if pair_id:
        pair_id = "|".join(
            (experiment_id, hypothesis_id, path_id, policy_epoch, behavior_hash, producer_commit, pair_id)
        )
    if strict and layer is not None and path_id not in _LAYER_PATH_IDS.get(layer, ()):
        return arm, cluster, None, policy_epoch, str(valid[0].get("code_commit")), "paired_evidence_unavailable"
    if strict and not pair_id:
        # An assignment unit is not a treatment/control pair by itself.  Do not
        # invent a match from time, symbol, or a neighboring exposure.
        return arm, cluster, None, policy_epoch, str(valid[0].get("code_commit")), "paired_evidence_unavailable"
    if metadata is not None:
        metadata.update(
            {
                "outcome_contract_version": outcome_contract_version,
                "stage": stage,
            }
        )
    return arm, cluster, pair_id, policy_epoch, str(valid[0].get("code_commit") or "") or None, None


def _candidate_notional(db: Any, fact: Mapping[str, Any]) -> float | None:
    for root in (fact, fact.get("fixed_horizon_outcome")):
        if not isinstance(root, Mapping):
            continue
        for key in ("quote_notional", "notional_quote", "capital_quote"):
            value = _finite(root.get(key), positive=True)
            if value is not None:
                return value
    identity = fact.get("identity")
    proposal_id = identity.get("proposal_id") if isinstance(identity, Mapping) else None
    if not proposal_id or not _table_exists(db, "trade_proposals"):
        return None
    columns = _columns(db, "trade_proposals")
    if not {"quantity", "price"} <= columns:
        return None
    try:
        row = db.execute("SELECT quantity, price FROM trade_proposals WHERE id=?", (proposal_id,)).fetchone()
    except (sqlite3.Error, OSError):
        return None
    if row is None:
        return None
    quantity = _finite(row[0], positive=True)
    price = _finite(row[1], positive=True)
    return quantity * price if quantity is not None and price is not None else None


def _candidate_sample(
    db: Any,
    fact: Mapping[str, Any],
    *,
    layer: str,
    cutoff: datetime,
    epoch_start: datetime,
    cost_model_id: str,
    epoch_id: str,
    registration: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    outcome = fact.get("fixed_horizon_outcome")
    if not isinstance(outcome, Mapping):
        return None, "unverified_or_unsettled_outcome"
    occurred = _time(fact.get("event_at") or outcome.get("entry_ts"))
    if occurred is None:
        return None, "missing_identity"
    if occurred < epoch_start:
        return None, "outside_epoch"
    if occurred > cutoff:
        return None, "outside_cutoff"
    settled_at = _time(outcome.get("settled_at"))
    if settled_at is None:
        return None, "unverified_or_unsettled_outcome"
    if settled_at > cutoff:
        return None, "outside_cutoff"
    outcome_assessment = assess_candidate_outcome(outcome)
    if not outcome_assessment.eligible:
        return None, "unverified_or_unsettled_outcome"
    if outcome.get("horizon") != _FIXED_CANDIDATE_HORIZON:
        return None, "stale_evidence"
    if outcome.get("cost_model_version") != cost_model_id:
        return None, "cost_incomplete"
    key = str(fact.get("fact_id") or "")
    if not key:
        return None, "missing_identity"
    net_pct = _finite(outcome.get("net_return_pct"))
    notional = _candidate_notional(db, fact)
    if net_pct is None or notional is None:
        return None, "nonfinite_delta"
    assignment_metadata: dict[str, str] = {}
    arm, cluster, pair_id, policy_epoch, producer_commit, assignment_reason = _lookup_assignment(
        db,
        fact=fact,
        cutoff=cutoff,
        epoch_start=epoch_start,
        epoch_id=epoch_id,
        observed_at=occurred,
        registration=registration,
        layer=layer,
        metadata=assignment_metadata,
    )
    if assignment_reason is not None:
        return None, assignment_reason
    # A candidate outcome already contains the single fixed-horizon net cost
    # calculation.  Keep this as an arm observation; the compiler admits it to
    # confirmatory evidence only after an explicit treatment/control pairing.
    baseline_quote = net_pct / 100.0 * notional
    delta_quote = baseline_quote
    delta_bp = net_pct * 100.0
    if not math.isfinite(delta_quote) or not math.isfinite(delta_bp):
        return None, "nonfinite_delta"
    return {
        "fact_unit": "candidate_fact",
        "economic_unit": "fixed_horizon_net_return",
        "unit_id": key,
        "cluster_id": pair_id or cluster or str(outcome.get("entry_ts") or occurred.date().isoformat())[:10],
        "pair_id": pair_id,
        "cohort_id": pair_id.rsplit("|", 1)[0] if pair_id and "|" in pair_id else None,
        "occurred_at": _iso(occurred),
        "arm": arm,
        "policy_epoch": policy_epoch,
        "producer_commit": producer_commit,
        "outcome_contract_version": assignment_metadata.get("outcome_contract_version"),
        "stage": assignment_metadata.get("stage"),
        "arm_net_delta_bp": net_pct * 100.0,
        "arm_net_delta_quote": baseline_quote,
        "paired_net_delta_bp": delta_bp,
        "paired_net_delta_quote": delta_quote,
        "source_outcome_key": outcome.get("outcome_candidate_key"),
        "notional_quote": notional,
        "cost_model_id": cost_model_id,
        "cost_applied_once": True,
        "pairing": "unpaired_arm_observation_pending_explicit_pair",
    }, None


def _position_notional(fact: Mapping[str, Any]) -> float | None:
    position = ((fact.get("stages") or {}).get("exit") or {}).get("position")
    if not isinstance(position, Mapping):
        return None
    entry = _finite(position.get("entry_price"), positive=True)
    quantity = _finite(position.get("quantity"), positive=True)
    return entry * quantity if entry is not None and quantity is not None else None


def _position_identity(
    fact: Mapping[str, Any],
) -> tuple[str | None, datetime | None, datetime | None]:
    fact_id = str(fact.get("fact_id") or "") or None
    position = ((fact.get("stages") or {}).get("exit") or {}).get("position")
    if not isinstance(position, Mapping):
        position = {}
    return (
        fact_id,
        _time(position.get("opened_at") or fact.get("event_at")),
        _time(position.get("closed_at") or fact.get("evidence_available_at")),
    )


def _fact_cost_model_id(fact: Mapping[str, Any]) -> str | None:
    economics = fact.get("economics")
    position = ((fact.get("stages") or {}).get("exit") or {}).get("position")
    cost_identity = fact.get("cost_identity")
    for root in (fact, economics, position, cost_identity):
        if isinstance(root, Mapping):
            value = root.get("cost_model_id") or root.get("cost_model_version")
            if value not in (None, ""):
                return str(value)
    return None


def _position_provenance_matches(fact: Mapping[str, Any], registration: Mapping[str, Any]) -> bool:
    producer = fact.get("producer")
    if not isinstance(producer, Mapping) or producer.get("status") != "available":
        return False
    expected_commit = str(registration.get("deploy_commit") or "")
    expected_fingerprint = str(registration.get("behavior_fingerprint") or "")
    commits = {str(value) for value in producer.get("producer_commits") or () if value}
    fingerprints = {str(value) for value in producer.get("behavior_fingerprints") or () if value}
    return bool(
        producer.get("runtime_session_ids")
        and expected_commit
        and expected_fingerprint
        and commits == {expected_commit}
        and fingerprints == {expected_fingerprint}
    )


def _position_assignment(
    db: Any,
    fact: Mapping[str, Any],
    cutoff: datetime,
    epoch_start: datetime,
    epoch_id: str,
    *,
    observed_at: datetime | None = None,
    registration: Mapping[str, Any] | None = None,
    layer: str | None = None,
    metadata: dict[str, str] | None = None,
) -> tuple[str, str, str | None, str | None, str | None, str | None]:
    return _lookup_assignment(
        db,
        fact=fact,
        cutoff=cutoff,
        epoch_start=epoch_start,
        epoch_id=epoch_id,
        observed_at=observed_at,
        registration=registration,
        layer=layer,
        metadata=metadata,
    )


def _position_cost_sample(
    db: Any,
    fact: Mapping[str, Any],
    *,
    cutoff: datetime,
    epoch_start: datetime,
    cost_model_id: str,
    epoch_id: str,
    registration: Mapping[str, Any] | None = None,
    layer: str = "Costs",
) -> tuple[dict[str, Any] | None, str | None]:
    unit_id, opened_at, closed_at = _position_identity(fact)
    if not unit_id or opened_at is None:
        return None, "missing_identity"
    if opened_at < epoch_start:
        return None, "outside_epoch"
    if closed_at is None:
        return None, "missing_identity"
    if closed_at < epoch_start:
        return None, "outside_epoch"
    if closed_at > cutoff:
        return None, "outside_cutoff"
    economics = fact.get("economics")
    if not isinstance(economics, Mapping) or economics.get("availability") != "available":
        return None, "unverified_or_unsettled_outcome"
    if economics.get("residual_within_tolerance") is not True:
        return None, "accounting_mismatch"
    gross = _finite(economics.get("gross_pnl"))
    net = _finite(economics.get("net_pnl"))
    notional = _position_notional(fact)
    if gross is None or net is None or notional is None:
        return None, "nonfinite_delta"
    for field in ("entry_fee", "exit_fee", "funding_cost"):
        if _finite(economics.get(field)) is None:
            return None, "cost_incomplete"
    expected_cost_model = _fact_cost_model_id(fact)
    if registration is not None and expected_cost_model != cost_model_id:
        return None, "cost_incomplete"
    if registration is not None and not _position_provenance_matches(fact, registration):
        return None, "epoch_mismatch"
    assignment_metadata: dict[str, str] = {}
    arm, cluster, pair_id, policy_epoch, producer_commit, assignment_reason = _position_assignment(
        db, fact, cutoff, epoch_start, epoch_id,
        observed_at=opened_at,
        registration=registration,
        layer=layer,
        metadata=assignment_metadata,
    )
    if assignment_reason is not None:
        return None, assignment_reason
    delta_quote = gross - net
    delta_bp = delta_quote / notional * 10_000.0
    if not math.isfinite(delta_quote) or not math.isfinite(delta_bp):
        return None, "nonfinite_delta"
    return {
        "fact_unit": "position_fact",
        "economic_unit": "verified_settled_net_economics",
        "unit_id": unit_id,
        "cluster_id": pair_id or cluster or closed_at.date().isoformat(),
        "pair_id": pair_id,
        "occurred_at": _iso(closed_at),
        "arm": arm,
        "policy_epoch": policy_epoch,
        "producer_commit": producer_commit,
        "outcome_contract_version": assignment_metadata.get("outcome_contract_version"),
        "stage": assignment_metadata.get("stage"),
        "arm_net_delta_bp": -delta_quote / notional * 10_000.0,
        "arm_net_delta_quote": -delta_quote,
        "paired_net_delta_bp": -delta_bp,
        "paired_net_delta_quote": -delta_quote,
        "cost_model_id": cost_model_id,
        "cost_applied_once": True,
        "notional_quote": notional,
        "cohort_id": pair_id.rsplit("|", 1)[0] if pair_id and "|" in pair_id else None,
        "pairing": "verified_gross_vs_settled_net_economics",
    }, None


def _position_execution_sample(
    db: Any,
    fact: Mapping[str, Any],
    *,
    cutoff: datetime,
    epoch_start: datetime,
    cost_model_id: str,
    epoch_id: str,
    registration: Mapping[str, Any] | None = None,
    layer: str = "Execution",
) -> tuple[dict[str, Any] | None, str | None]:
    unit_id, opened_at, closed_at = _position_identity(fact)
    if not unit_id or opened_at is None:
        return None, "missing_identity"
    if opened_at < epoch_start:
        return None, "outside_epoch"
    if closed_at is None:
        return None, "missing_identity"
    if closed_at < epoch_start:
        return None, "outside_epoch"
    if closed_at > cutoff:
        return None, "outside_cutoff"
    economics = fact.get("economics")
    stages = fact.get("stages")
    execution = stages.get("execution") if isinstance(stages, Mapping) else None
    exit_stage = stages.get("exit") if isinstance(stages, Mapping) else None
    if not isinstance(economics, Mapping) or not isinstance(execution, Mapping) or not isinstance(exit_stage, Mapping):
        return None, "paired_evidence_unavailable"
    if economics.get("availability") != "available":
        return None, "unverified_or_unsettled_outcome"
    if economics.get("residual_within_tolerance") is not True:
        return None, "accounting_mismatch"
    entry_order = execution.get("entry_order")
    entry_fills = execution.get("entry_fills")
    close_orders = exit_stage.get("close_orders")
    close_fills = exit_stage.get("close_fills")
    if not isinstance(entry_order, Mapping) or not isinstance(entry_fills, list) or len(entry_fills) != 1:
        return None, "incomplete_path"
    if not isinstance(close_orders, list) or len(close_orders) != 1 or not isinstance(close_fills, list) or len(close_fills) != 1:
        return None, "incomplete_path"
    close_order = close_orders[0]
    entry_reference = _finite(entry_order.get("reference_price"), positive=True)
    close_reference = _finite(close_order.get("reference_price"), positive=True)
    quantity = _finite(((exit_stage.get("position") or {}).get("quantity")), positive=True)
    entry_fee = _finite(economics.get("entry_fee"))
    exit_fee = _finite(economics.get("exit_fee"))
    funding = _finite(economics.get("funding_cost"))
    actual_net = _finite(economics.get("net_pnl"))
    entry_price = _finite(entry_reference, positive=True)
    if None in (entry_reference, close_reference, quantity, entry_fee, exit_fee, funding, actual_net, entry_price):
        return None, "cost_incomplete"
    position = exit_stage.get("position") if isinstance(exit_stage.get("position"), Mapping) else {}
    try:
        _, chain_reason = _paired_position_cost(db, dict(position))
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error):
        chain_reason = "incomplete_path"
    if chain_reason is not None:
        return None, "incomplete_path"
    direction = str(position.get("direction") or "").lower()
    if direction == "long":
        reference_gross = (close_reference - entry_reference) * quantity
    elif direction == "short":
        reference_gross = (entry_reference - close_reference) * quantity
    else:
        return None, "missing_identity"
    reference_net = reference_gross - entry_fee - exit_fee - funding
    delta_quote = reference_net - actual_net
    notional = entry_reference * quantity
    delta_bp = delta_quote / notional * 10_000.0
    if not math.isfinite(delta_quote) or not math.isfinite(delta_bp):
        return None, "nonfinite_delta"
    expected_cost_model = _fact_cost_model_id(fact)
    if registration is not None and expected_cost_model != cost_model_id:
        return None, "cost_incomplete"
    if registration is not None and not _position_provenance_matches(fact, registration):
        return None, "epoch_mismatch"
    assignment_metadata: dict[str, str] = {}
    arm, cluster, pair_id, policy_epoch, producer_commit, assignment_reason = _position_assignment(
        db, fact, cutoff, epoch_start, epoch_id,
        observed_at=opened_at,
        registration=registration,
        layer=layer,
        metadata=assignment_metadata,
    )
    if assignment_reason is not None:
        return None, assignment_reason
    return {
        "fact_unit": "position_fact",
        "economic_unit": "verified_settled_net_economics",
        "unit_id": unit_id,
        "cluster_id": pair_id or cluster or closed_at.date().isoformat(),
        "pair_id": pair_id,
        "occurred_at": _iso(closed_at),
        "arm": arm,
        "policy_epoch": policy_epoch,
        "producer_commit": producer_commit,
        "outcome_contract_version": assignment_metadata.get("outcome_contract_version"),
        "stage": assignment_metadata.get("stage"),
        "arm_net_delta_bp": -delta_quote / notional * 10_000.0,
        "arm_net_delta_quote": -delta_quote,
        "paired_net_delta_bp": -delta_bp,
        "paired_net_delta_quote": -delta_quote,
        "cost_model_id": cost_model_id,
        "cost_applied_once": True,
        "notional_quote": notional,
        "cohort_id": pair_id.rsplit("|", 1)[0] if pair_id and "|" in pair_id else None,
        "pairing": "requested_reference_vs_actual_settled_execution",
    }, None


def _pair_confirmatory_samples(
    raw_samples: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Keep only explicit one-treatment/one-control assignment-unit pairs."""
    groups: dict[str, list[dict[str, Any]]] = {}
    excluded: list[tuple[str, str]] = []
    cohort_ids = {str(raw.get("cohort_id") or "") for raw in raw_samples if raw.get("cohort_id")}
    if len(cohort_ids) > 1:
        return [], [
            (str(raw.get("unit_id") or ""), "ambiguous_identity")
            for raw in raw_samples
        ]
    for raw in raw_samples:
        pair_id = str(raw.get("pair_id") or "")
        unit_id = str(raw.get("unit_id") or "")
        if not pair_id:
            excluded.append((unit_id, "paired_evidence_unavailable"))
            continue
        groups.setdefault(pair_id, []).append(raw)
    paired: list[dict[str, Any]] = []
    for pair_id, group in sorted(groups.items()):
        by_arm = {str(raw.get("arm") or "").lower(): raw for raw in group}
        if len(group) != 2 or set(by_arm) != {"treatment", "control"}:
            excluded.extend((str(raw.get("unit_id") or ""), "ambiguous_identity") for raw in group)
            continue
        treatment = by_arm["treatment"]
        control = by_arm["control"]
        treatment_quote = _finite(treatment.get("arm_net_delta_quote"))
        control_quote = _finite(control.get("arm_net_delta_quote"))
        treatment_bp = _finite(treatment.get("arm_net_delta_bp"))
        control_bp = _finite(control.get("arm_net_delta_bp"))
        treatment_notional = _finite(treatment.get("notional_quote"), positive=True)
        control_notional = _finite(control.get("notional_quote"), positive=True)
        if None in (treatment_quote, control_quote, treatment_bp, control_bp, treatment_notional, control_notional):
            excluded.extend((str(raw.get("unit_id") or ""), "nonfinite_delta") for raw in group)
            continue
        if not math.isclose(treatment_notional, control_notional, rel_tol=1e-9, abs_tol=1e-9):
            excluded.extend(
                (str(raw.get("unit_id") or ""), "denominator_or_economic_unit_mismatch")
                for raw in group
            )
            continue
        delta_bp = treatment_bp - control_bp
        delta_quote = delta_bp / 10_000.0 * treatment_notional
        if not math.isfinite(delta_quote) or not math.isfinite(delta_bp):
            excluded.extend((str(raw.get("unit_id") or ""), "nonfinite_delta") for raw in group)
            continue
        sample = dict(treatment)
        sample.update(
            {
                "unit_id": pair_id,
                "cluster_id": pair_id,
                "pair_id": pair_id,
                "treatment_unit_id": treatment.get("unit_id"),
                "control_unit_id": control.get("unit_id"),
                "arm": "paired",
                "arm_counts": {"treatment": 1, "control": 1},
                "paired_net_delta_bp": delta_bp,
                "paired_net_delta_quote": delta_quote,
                "pairing": "explicit_treatment_control_assignment_unit_pair",
                "cost_applied_once": True,
            }
        )
        paired.append(sample)
    return paired, excluded


def _add_layer_samples(
    *,
    db: Any,
    candidate_facts: list[dict[str, Any]],
    position_facts: list[dict[str, Any]],
    cutoff: datetime,
    epoch_start: datetime,
    cost_model_id: str,
    epoch_id: str,
    cost_status: str,
    registration_ok: bool,
    registration: Mapping[str, Any] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    samples: dict[str, list[dict[str, Any]]] = {layer: [] for layer in LAYERS}
    stats: dict[str, dict[str, Any]] = {
        layer: {"raw_count": 0, "eligible_count": 0, "reason_counts": Counter(), "excluded_units": []}
        for layer in LAYERS
    }
    seen: dict[str, set[str]] = {layer: set() for layer in LAYERS}

    def record(layer: str, raw: Mapping[str, Any] | None, reason: str | None) -> None:
        stats[layer]["raw_count"] += 1
        if raw is None:
            stats[layer]["reason_counts"][reason or "source_unavailable"] += 1
            return
        unit_id = str(raw.get("unit_id") or "")
        if not unit_id:
            stats[layer]["reason_counts"]["missing_identity"] += 1
            return
        if reason is not None:
            stats[layer]["reason_counts"][reason] += 1
            stats[layer]["excluded_units"].append(unit_id)
            seen[layer].add(unit_id)
            return
        if unit_id in seen[layer]:
            stats[layer]["reason_counts"]["duplicate_assignment_unit"] += 1
            stats[layer]["excluded_units"].append(unit_id)
            return
        seen[layer].add(unit_id)
        samples[layer].append(dict(raw))
        stats[layer]["eligible_count"] += 1

    if not registration_ok:
        for fact in candidate_facts:
            for layer in ("Selection", "Decision", "Risk"):
                record(layer, None, "epoch_mismatch")
        for fact in position_facts:
            for layer in LAYERS[3:]:
                record(layer, None, "epoch_mismatch")
        for layer in LAYERS:
            stats[layer]["reason_counts"] = dict(sorted(stats[layer]["reason_counts"].items()))
        return samples, stats

    for fact in candidate_facts:
        for layer in ("Selection", "Decision", "Risk"):
            if cost_status != "available":
                record(layer, None, "cost_incomplete")
                continue
            record(
                layer,
                *_candidate_sample(
                    db,
                    fact,
                    layer=layer,
                    cutoff=cutoff,
                    epoch_start=epoch_start,
                    cost_model_id=cost_model_id,
                    epoch_id=epoch_id,
                    registration=registration,
                ),
            )

    for fact in position_facts:
        # There is no confirmatory Holding/Exit pair in the current durable
        # contracts.  Keep those layers explicit and empty rather than importing
        # exploratory counterfactuals as if they were settled economics.
        record("Execution", *_position_execution_sample(
            db,
            fact,
            cutoff=cutoff,
            epoch_start=epoch_start,
            cost_model_id=cost_model_id,
            epoch_id=epoch_id,
            registration=registration,
        ) if cost_status == "available" else (None, "cost_incomplete"))
        record("Costs", *_position_cost_sample(
            db,
            fact,
            cutoff=cutoff,
            epoch_start=epoch_start,
            cost_model_id=cost_model_id,
            epoch_id=epoch_id,
            registration=registration,
        ) if cost_status == "available" else (None, "cost_incomplete"))
        record("Holding", None, "paired_evidence_unavailable")
        record("Exit", None, "paired_evidence_unavailable")

    for layer in ("Selection", "Decision", "Risk", "Execution", "Costs"):
        provisional = samples[layer]
        samples[layer] = []
        stats[layer]["eligible_count"] = 0
        paired, excluded = _pair_confirmatory_samples(provisional)
        for unit_id, reason in excluded:
            stats[layer]["reason_counts"][reason] += 1
            stats[layer]["excluded_units"].append(unit_id)
        for sample in paired:
            samples[layer].append(sample)
        stats[layer]["eligible_count"] = len(paired)

    all_cohort_ids = {
        str(sample.get("cohort_id"))
        for layer in ("Selection", "Decision", "Risk", "Execution", "Costs")
        for sample in samples[layer]
        if sample.get("cohort_id")
    }
    if len(all_cohort_ids) > 1:
        for layer in ("Selection", "Decision", "Risk", "Execution", "Costs"):
            for sample in samples[layer]:
                stats[layer]["reason_counts"]["ambiguous_identity"] += 1
                stats[layer]["excluded_units"].append(str(sample.get("unit_id") or ""))
            samples[layer] = []
            stats[layer]["eligible_count"] = 0

    for layer in LAYERS:
        samples[layer].sort(key=lambda sample: (sample["occurred_at"], sample["unit_id"]))
        stats[layer]["reason_counts"] = dict(sorted(stats[layer]["reason_counts"].items()))
        stats[layer]["excluded_units"] = sorted(set(stats[layer]["excluded_units"]))
    return samples, stats


def _coverage_context(
    *,
    registration: Mapping[str, Any],
    candidate_facts: list[dict[str, Any]],
    position_facts: list[dict[str, Any]],
    epoch_start: datetime,
    cutoff: datetime,
    candidate_source: Mapping[str, Any],
    position_source: Mapping[str, Any],
    cost_source: Mapping[str, Any],
    evidence_epoch_id: str,
    cost_model_id: str,
    layer_samples: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    cohort_candidates: list[dict[str, Any]] = []
    for fact in candidate_facts:
        outcome = fact.get("fixed_horizon_outcome")
        occurred = _time(fact.get("event_at") or (outcome or {}).get("entry_ts")) if isinstance(outcome, Mapping) else _time(fact.get("event_at"))
        if occurred is not None and epoch_start <= occurred <= cutoff:
            cohort_candidates.append(fact)
    cohort_positions: list[dict[str, Any]] = []
    for fact in position_facts:
        position = (((fact.get("stages") or {}).get("exit") or {}).get("position"))
        opened = _time(position.get("opened_at")) if isinstance(position, Mapping) else None
        closed = _time(position.get("closed_at")) if isinstance(position, Mapping) else None
        if opened is not None and closed is not None and epoch_start <= opened and closed <= cutoff:
            cohort_positions.append(fact)

    candidate_eligible = 0
    risk_reject_count = 0
    identity_values: dict[str, set[str]] = {
        "behavior_fingerprints": set(),
        "policy_epochs": set(),
        "market_data_epochs": set(),
        "cost_model_epochs": set(),
    }
    stage_values: set[str] = set()
    outcome_contract_values: set[str] = set()
    for fact in cohort_candidates:
        outcome = fact.get("fixed_horizon_outcome")
        if isinstance(outcome, Mapping):
            settled_at = _time(outcome.get("settled_at"))
            assessment = assess_candidate_outcome(outcome)
            if (
                assessment.eligible
                and settled_at is not None
                and settled_at <= cutoff
                and outcome.get("cost_model_version") == cost_model_id
            ):
                candidate_eligible += 1
            if outcome.get("cost_model_version"):
                identity_values["cost_model_epochs"].add(str(outcome["cost_model_version"]))
        if fact.get("terminal_state") == "risk_reject":
            risk_reject_count += 1
        for field, target in (
            ("behavior_fingerprint", "behavior_fingerprints"),
            ("policy_epoch", "policy_epochs"),
            ("policy_epoch_id", "policy_epochs"),
            ("market_data_epoch", "market_data_epochs"),
        ):
            value = fact.get(field)
            if value:
                identity_values[target].add(str(value))
        if isinstance(outcome, Mapping) and outcome.get("market_data_epoch"):
            identity_values["market_data_epochs"].add(str(outcome["market_data_epoch"]))

    for sample_group in (layer_samples or {}).values():
        for sample in sample_group:
            for field, target in (
                ("policy_epoch", "policy_epochs"),
                ("market_data_epoch", "market_data_epochs"),
            ):
                if sample.get(field):
                    identity_values[target].add(str(sample[field]))
            if sample.get("stage") or sample.get("experiment_stage"):
                stage_values.add(str(sample.get("stage") or sample.get("experiment_stage")))
            if sample.get("outcome_contract_version"):
                outcome_contract_values.add(str(sample["outcome_contract_version"]))

    for fact in cohort_positions:
        producer = fact.get("producer")
        if isinstance(producer, Mapping):
            for value in producer.get("behavior_fingerprints") or ():
                if value:
                    identity_values["behavior_fingerprints"].add(str(value))
        policy = fact.get("policy_identity")
        if isinstance(policy, Mapping) and policy.get("policy_epoch_id"):
            identity_values["policy_epochs"].add(str(policy["policy_epoch_id"]))
        market = fact.get("market_data_epoch")
        if market:
            identity_values["market_data_epochs"].add(str(market))

    causal_total = len(cohort_positions)
    causal_complete = sum(
        1 for fact in cohort_positions
        if ((fact.get("causal_link") or {}).get("status") == "available")
    )
    economics_complete = sum(
        1 for fact in cohort_positions
        if ((fact.get("economics") or {}).get("availability") == "available")
    )
    path_complete = sum(
        1 for fact in cohort_positions
        if (((fact.get("stages") or {}).get("holding") or {}).get("ohlcv") or {}).get("availability")
        == "available"
    )
    capital_days = 0.0
    capital_day_count = 0
    for fact in cohort_positions:
        position = (((fact.get("stages") or {}).get("exit") or {}).get("position"))
        if not isinstance(position, Mapping):
            continue
        notional = _finite(position.get("entry_price"), positive=True)
        quantity = _finite(position.get("quantity"), positive=True)
        opened = _time(position.get("opened_at"))
        closed = _time(position.get("closed_at"))
        if None in (notional, quantity, opened, closed) or closed <= opened:
            continue
        assert notional is not None and quantity is not None and opened is not None and closed is not None
        capital_days += notional * quantity * (closed - opened).total_seconds() / 86_400.0
        capital_day_count += 1

    candidate_complete = (
        candidate_source.get("status") == "available"
        and int(candidate_source.get("unavailable_count") or 0) == 0
    )
    position_complete = (
        position_source.get("status") == "available"
        and int(position_source.get("error_count") or 0) == 0
    )
    expected_behavior = str(registration.get("behavior_fingerprint") or "")
    if expected_behavior and identity_values["behavior_fingerprints"] == {expected_behavior}:
        behavior = [expected_behavior]
    else:
        behavior = sorted(identity_values["behavior_fingerprints"])
    policy_epochs = sorted(identity_values["policy_epochs"])
    market_epochs = sorted(identity_values["market_data_epochs"])
    cost_epochs = sorted(identity_values["cost_model_epochs"]) if cost_source.get("status") == "available" else []
    return {
        "evidence_epoch_id": evidence_epoch_id,
        "cutoff_at": _iso(cutoff),
        "registration_deploy_commit": registration.get("deploy_commit"),
        "candidate_count": len(cohort_candidates) if candidate_complete else None,
        "candidate_outcome_coverage": (
            None if not candidate_complete else candidate_eligible / len(cohort_candidates) if cohort_candidates else 0.0
        ),
        "position_causal_link_completeness": (
            None if not position_complete else causal_complete / causal_total if causal_total else 0.0
        ),
        "verified_settled_economics_coverage": (
            None if not position_complete else economics_complete / causal_total if causal_total else 0.0
        ),
        "ordered_path_coverage": (
            None if not position_complete else path_complete / causal_total if causal_total else 0.0
        ),
        "risk_reject_count": risk_reject_count,
        "capital_days": capital_days if capital_day_count else None,
        "behavior_fingerprints": behavior,
        "policy_epochs": policy_epochs,
        "market_data_epochs": market_epochs,
        "cost_model_epochs": cost_epochs,
        "comparison_method": _GRADIENT_COMPARISON_METHOD,
        "horizons": [_FIXED_CANDIDATE_HORIZON],
        "outcome_contract_versions": sorted(outcome_contract_values),
        "metric_versions": [METRIC_VERSION],
        "statistical_unit_versions": [_GRADIENT_STATISTICAL_UNIT_VERSION],
        "stages": sorted(stage_values),
        "fixed_horizon": _FIXED_CANDIDATE_HORIZON,
        "candidate_economic_unit": "fixed_horizon_net_return",
        "position_economic_unit": "verified_settled_net_economics",
        "confirmatory_evidence_only": True,
        "layer_path_mapping": {layer: list(paths) for layer, paths in _LAYER_PATH_IDS.items()},
    }


def _counterfactual_inventory(position_facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "available",
        "contract_version": COUNTERFACTUAL_CONTRACT_VERSION,
        "registry_hash": profit_loop_registry_hash(),
        "assignment_unit": "position_id",
        "evidence_class": "exploratory",
        "promotion_eligible": False,
        "position_fact_count": len(position_facts),
        "evaluated_count": 0,
        "reason": "exploratory_counterfactuals_excluded_from_confirmatory_gradient_samples",
    }


def _finalize(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("content_hash", None)
    result["content_hash"] = _hash(result)
    return result


def verify_profit_gradient_evidence(
    payload: Mapping[str, Any],
    *,
    revalidate_sources: bool = False,
) -> tuple[bool, str | None]:
    """Verify an artifact; optionally rebuild its frozen sources for CLI trust."""
    if payload.get("contract_version") != PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION:
        return False, "evidence_contract_version_mismatch"
    if payload.get("producer") != _COMPILER_PRODUCER:
        return False, "evidence_producer_mismatch"
    if payload.get("read_only") is not True or payload.get("behavior_changed") is not False:
        return False, "evidence_authority_metadata_invalid"
    required = {
        "registration", "evidence_context", "layer_samples", "exclusion_waterfall",
        "source_hashes", "source_status", "exploratory_evidence", "inputs", "producer", "content_hash",
    }
    if not required <= set(payload):
        return False, "evidence_artifact_fields_missing"
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping) or not all(
        isinstance(inputs.get(field), str) and inputs[field].strip()
        for field in ("evidence_epoch_id", "cutoff_at", "cost_model_id")
    ):
        return False, "evidence_inputs_invalid"
    for field in ("registration", "evidence_context", "exclusion_waterfall", "exploratory_evidence"):
        if not isinstance(payload.get(field), Mapping):
            return False, f"evidence_{field}_invalid"
    if not isinstance(payload["registration"].get("status"), str):
        return False, "evidence_registration_invalid"
    if "profit_loop_counterfactual" not in payload["exploratory_evidence"]:
        return False, "evidence_exploratory_inventory_missing"
    source_status = payload.get("source_status")
    if not isinstance(source_status, Mapping) or not {
        "evidence_epoch", "candidate_facts", "position_replays", "cost_model", "counterfactual"
    } <= set(source_status):
        return False, "evidence_source_status_invalid"
    if any(
        not isinstance(source_status[key], Mapping) or not isinstance(source_status[key].get("status"), str)
        for key in ("evidence_epoch", "candidate_facts", "position_replays", "cost_model", "counterfactual")
    ):
        return False, "evidence_source_status_invalid"
    layer_samples = payload.get("layer_samples")
    if not isinstance(layer_samples, Mapping) or set(layer_samples) != set(LAYERS):
        return False, "evidence_layer_set_invalid"
    source_hashes = payload.get("source_hashes")
    if not isinstance(source_hashes, Mapping) or not {
        "evidence_epoch", "candidate_facts", "position_replays", "cost_model", "counterfactual_registry"
    } <= set(source_hashes):
        return False, "evidence_source_hashes_missing"
    if any(not isinstance(source_hashes[key], str) or len(source_hashes[key]) != 64 for key in source_hashes):
        return False, "evidence_source_hash_invalid"
    supplied = payload.get("content_hash")
    if not isinstance(supplied, str) or len(supplied) != 64:
        return False, "evidence_content_hash_missing"
    expected = _hash({key: value for key, value in payload.items() if key != "content_hash"})
    if supplied != expected:
        return False, "evidence_content_hash_mismatch"
    if revalidate_sources:
        try:
            rebuilt = build_profit_gradient_evidence(
                evidence_epoch_id=str(inputs["evidence_epoch_id"]),
                cutoff_at=str(inputs["cutoff_at"]),
                cost_model_id=str(inputs["cost_model_id"]),
            )
        except (OSError, sqlite3.Error, ValueError):
            return False, "evidence_source_revalidation_failed"
        if rebuilt.get("content_hash") != supplied:
            return False, "evidence_source_revalidation_mismatch"
    return True, None


def build_profit_gradient_evidence(
    *,
    evidence_epoch_id: str,
    cutoff_at: str,
    cost_model_id: str,
) -> dict[str, Any]:
    """Build the frozen ``profit_gradient_evidence/v1`` artifact.

    All three inputs are identity selectors, not data.  Candidate samples and
    Position samples are derived from the database; there is no parameter for
    manually supplied layer samples.
    """
    if not isinstance(evidence_epoch_id, str) or not evidence_epoch_id.strip():
        raise ValueError("evidence_epoch_id must be a non-empty string")
    if not isinstance(cost_model_id, str) or not cost_model_id.strip():
        raise ValueError("cost_model_id must be a non-empty string")
    cutoff = _parse_utc(cutoff_at)
    cutoff_iso = _iso(cutoff)

    base: dict[str, Any] = {
        "contract_version": PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION,
        "read_only": True,
        "behavior_changed": False,
        "producer": dict(_COMPILER_PRODUCER),
        "inputs": {
            "evidence_epoch_id": evidence_epoch_id,
            "cutoff_at": cutoff_iso,
            "cost_model_id": cost_model_id,
        },
        "registration": _unavailable("database_unavailable", evidence_epoch_id=evidence_epoch_id),
        "evidence_context": {},
        "layer_samples": {layer: [] for layer in LAYERS},
        "exclusion_waterfall": {
            "order": list(_WATERFALL_ORDER),
            "layers": {},
            "reason_counts": {},
        },
        "source_hashes": {},
        "source_status": {},
        "exploratory_evidence": {},
        "content_hash": None,
    }

    read_db: Any = None
    began_read_snapshot = False
    try:
        db_context = readonly_db()
        with db_context as db:
            read_db = db
            if not bool(getattr(db, "in_transaction", False)):
                db.execute("BEGIN")
                began_read_snapshot = True
            registration = _resolve_registration(db, evidence_epoch_id, cutoff)
            epoch_start = _time(registration.get("started_at")) if registration.get("status") == "available" else cutoff
            assert epoch_start is not None
            candidate_source, candidate_facts = _load_candidates(db, cutoff)
            position_source, position_facts = _load_positions(db, cutoff)
            cost_source = _resolve_cost_model(
                db,
                cost_model_id,
                cutoff,
                position_facts=position_facts,
            )
            epoch_for_context = registration if registration.get("status") == "available" else {}
            layer_samples, layer_stats = _add_layer_samples(
                db=db,
                candidate_facts=candidate_facts,
                position_facts=position_facts,
                cutoff=cutoff,
                epoch_start=epoch_start,
                cost_model_id=cost_model_id,
                epoch_id=evidence_epoch_id,
                cost_status=str(cost_source.get("status") or "unavailable"),
                registration_ok=registration.get("status") == "available",
                registration=registration,
            )
            context = _coverage_context(
                registration=epoch_for_context,
                candidate_facts=candidate_facts,
                position_facts=position_facts,
                epoch_start=epoch_start,
                cutoff=cutoff,
                candidate_source=candidate_source,
                position_source=position_source,
                cost_source=cost_source,
                evidence_epoch_id=evidence_epoch_id,
                cost_model_id=cost_model_id,
                layer_samples=layer_samples,
            )
            counterfactual = _counterfactual_inventory(position_facts)
            source_status = {
                "evidence_epoch": registration,
                "candidate_facts": candidate_source,
                "position_replays": position_source,
                "cost_model": cost_source,
                "counterfactual": counterfactual,
            }
            source_hashes = {
                "evidence_epoch": _hash(registration),
                "candidate_facts": str(candidate_source.get("source_hash") or _hash(candidate_source)),
                "position_replays": str(position_source.get("source_hash") or _hash(position_source)),
                "cost_model": str(cost_source.get("source_hash") or _hash(cost_source)),
                "counterfactual_registry": counterfactual["registry_hash"],
            }
            reason_counts: Counter[str] = Counter()
            for stats in layer_stats.values():
                reason_counts.update(stats["reason_counts"])
            base.update(
                {
                    "registration": registration,
                    "evidence_context": context,
                    "layer_samples": layer_samples,
                    "exclusion_waterfall": {
                        "order": list(_WATERFALL_ORDER),
                        "layers": layer_stats,
                        "reason_counts": dict(sorted(reason_counts.items())),
                    },
                    "source_hashes": source_hashes,
                    "source_status": source_status,
                    "exploratory_evidence": {"profit_loop_counterfactual": counterfactual},
                }
            )
            if began_read_snapshot:
                _rollback_snapshot(db)
    except (FileNotFoundError, OSError, sqlite3.Error):
        if began_read_snapshot and read_db is not None:
            _rollback_snapshot(read_db)
        # Return a complete, hashable unavailable artifact.  CLI generation can
        # still archive the fact that the requested read was unavailable.
        base["source_status"] = {
            "evidence_epoch": base["registration"],
            "candidate_facts": _unavailable("database_unavailable"),
            "position_replays": _unavailable("database_unavailable"),
            "cost_model": _unavailable("database_unavailable", cost_model_id=cost_model_id),
            "counterfactual": _counterfactual_inventory([]),
        }
        base["exploratory_evidence"] = {
            "profit_loop_counterfactual": base["source_status"]["counterfactual"]
        }
        base["source_hashes"] = {
            "evidence_epoch": _hash(base["source_status"]["evidence_epoch"]),
            "candidate_facts": _hash(base["source_status"]["candidate_facts"]),
            "position_replays": _hash(base["source_status"]["position_replays"]),
            "cost_model": _hash(base["source_status"]["cost_model"]),
            "counterfactual_registry": str(
                base["source_status"]["counterfactual"].get("registry_hash") or ""
            ),
        }
        base["exclusion_waterfall"] = {
            "order": list(_WATERFALL_ORDER),
            "layers": {
                layer: {
                    "raw_count": 0,
                    "eligible_count": 0,
                    "reason_counts": {"source_unavailable": 1},
                    "excluded_units": [],
                }
                for layer in LAYERS
            },
            "reason_counts": {"source_unavailable": len(LAYERS)},
        }
    return _finalize(base)


__all__ = [
    "PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION",
    "build_profit_gradient_evidence",
    "verify_profit_gradient_evidence",
]
