"""Read-only Trading Mandate compliance projections.

All records are joined by exact durable identities.  This module never writes
SQLite state and never re-evaluates historical rows against current config.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any

from aletheia.analytics.outcome_integrity import assess_reportable_outcome
from aletheia.config.loader import load_config
from aletheia.trading.trading_mandate import (
    TRADING_MANDATE_FIT_VERSION,
    TRADING_MANDATE_VERSION,
    resolve_trading_mandate,
    validate_frozen_trading_mandate_provenance,
)

COMPLIANCE_CONTRACT_VERSION = "trading_mandate_compliance/v1"
_FIT_STATUSES = (
    "in_scope",
    "out_of_scope",
    "not_assessable",
    "not_configured",
    "mandate_unavailable",
    "not_recorded",
)


def _json(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _utc(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _configured() -> dict[str, Any]:
    try:
        resolution = resolve_trading_mandate(load_config(persist_migrations=False))
        return resolution.as_dict()
    except Exception:
        return {
            "status": "mandate_unavailable",
            "identity": None,
            "reason_codes": ["mandate_unavailable"],
            "validation_errors": [],
        }


def _proposal_mandate(proposal: dict[str, Any] | None) -> dict[str, Any]:
    if proposal is None:
        return {
            "status": "not_recorded",
            "identity": None,
            "fit": None,
            "reason_codes": ["mandate_provenance_not_recorded"],
        }
    snapshot = _json(proposal.get("attribution_snapshot"))
    if not isinstance(snapshot, dict):
        return {
            "status": "not_recorded",
            "identity": None,
            "fit": None,
            "reason_codes": ["mandate_provenance_not_recorded"],
        }
    has_identity = "trading_mandate_identity" in snapshot
    has_fit = "trading_mandate_fit" in snapshot
    if not has_identity and not has_fit:
        return {
            "status": "not_recorded",
            "identity": None,
            "fit": None,
            "reason_codes": ["mandate_provenance_not_recorded"],
        }
    identity = snapshot.get("trading_mandate_identity")
    fit = snapshot.get("trading_mandate_fit")
    try:
        validate_frozen_trading_mandate_provenance(snapshot)
    except ValueError:
        return {
            "status": "mandate_unavailable",
            "identity": identity if isinstance(identity, dict) else None,
            "fit": fit,
            "reason_codes": ["malformed_frozen_mandate_provenance"],
        }
    status = fit.get("status") if isinstance(fit, dict) else None
    if status not in _FIT_STATUSES[:-1]:
        return {
            "status": "mandate_unavailable",
            "identity": identity,
            "fit": fit,
            "reason_codes": ["unsupported_frozen_mandate_fit_status"],
        }
    reasons = fit.get("reasons") if isinstance(fit, dict) else None
    return {
        "status": status,
        "identity": identity,
        "fit": fit,
        "reason_codes": [str(reason) for reason in reasons if reason]
        if isinstance(reasons, list)
        else [],
    }


def _source_identities(db: Any, proposal: dict[str, Any] | None) -> dict[str, Any]:
    if proposal is None:
        return {"opportunity_id": None, "decision_id": None, "proposal_id": None}
    decision_rows = db.execute(
        "SELECT id FROM pi_decisions WHERE proposal_id=? "
        "AND action!='signal_pending' ORDER BY datetime(created_at), rowid",
        (proposal.get("id"),),
    ).fetchall()
    return {
        "opportunity_id": proposal.get("opportunity_id"),
        "decision_id": decision_rows[0]["id"] if len(decision_rows) == 1 else None,
        "proposal_id": proposal.get("id"),
    }


def _row(db: Any, table: str, identity: object) -> dict[str, Any] | None:
    if identity in (None, ""):
        return None
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    return dict(row) if row is not None else None


def _fit_status(proposal: dict[str, Any] | None) -> str:
    return _proposal_mandate(proposal)["status"]


def _fit_reasons(proposal: dict[str, Any] | None) -> list[str]:
    return _proposal_mandate(proposal)["reason_codes"]


def _horizon_declaration(proposal: dict[str, Any] | None) -> tuple[float | None, float | None, str]:
    evidence = _proposal_mandate(proposal)
    fit = evidence.get("fit")
    if not isinstance(fit, dict):
        return None, None, evidence["status"]
    checks = fit.get("checks")
    if not isinstance(checks, list):
        return None, None, evidence["status"]
    check = next(
        (item for item in checks if isinstance(item, dict) and item.get("dimension") == "holding_horizon"),
        None,
    )
    if not isinstance(check, dict) or check.get("status") == "not_declared":
        return None, None, "not_declared"
    declared = check.get("declared")
    if not isinstance(declared, dict):
        return None, None, evidence["status"]
    return _finite(declared.get("min_hours")), _finite(declared.get("max_hours")), evidence["status"]


def _duration(position: dict[str, Any]) -> float | None:
    opened = _utc(position.get("opened_at"))
    closed = _utc(position.get("closed_at"))
    if opened is None or closed is None or closed < opened:
        return None
    return (closed - opened).total_seconds() / 3600.0


def _single_report(
    *,
    db: Any,
    record_type: str,
    record: dict[str, Any] | None,
    configured: dict[str, Any],
) -> dict[str, Any]:
    if record is None:
        return {
            "contract_version": COMPLIANCE_CONTRACT_VERSION,
            "read_only": True,
            "record_type": record_type,
            "availability": "unavailable",
            "unavailable_reason": f"{record_type}_not_found",
            "configured_mandate": configured,
            "trading_mandate_identity": None,
            "trading_mandate_fit": None,
            "frozen_mandate_identity": None,
            "frozen_mandate_fit": None,
            "source_identities": None,
            "observational_statement": (
                "No compliance conclusion is available without the exact requested record."
            ),
        }
    proposal = record if record_type == "proposal" else _row(db, "trade_proposals", record.get("proposal_id"))
    evidence = _proposal_mandate(proposal)
    source_ids = _source_identities(db, proposal)
    return {
        "contract_version": COMPLIANCE_CONTRACT_VERSION,
        "read_only": True,
        "record_type": record_type,
        "availability": "available",
        "unavailable_reason": None,
        "configured_mandate": configured,
        "mandate_identity": evidence["identity"],
        "trading_mandate_identity": evidence["identity"],
        "trading_mandate_fit": evidence["fit"],
        "frozen_mandate_identity": evidence["identity"],
        "frozen_mandate_fit": evidence["fit"],
        "fit_status": evidence["status"],
        "source_identities": {
            **source_ids,
            "position_id": record.get("id") if record_type == "position" else None,
        },
        "record": record,
        "observational_statement": (
            "This is a descriptive, read-only observation. It does not prove causality, "
            "recommend action, enforce a mandate, or mutate trading behavior."
        ),
    }


def _aggregate_report(*, db: Any, hours: int, configured: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, int(hours)))
    proposals = [
        dict(row)
        for row in db.execute("SELECT * FROM trade_proposals ORDER BY datetime(created_at), rowid").fetchall()
        if (created := _utc(row["created_at"])) is not None and created >= cutoff
    ]
    positions = [
        dict(row)
        for row in db.execute("SELECT * FROM positions ORDER BY datetime(opened_at), rowid").fetchall()
        if (opened := _utc(row["opened_at"])) is not None and opened >= cutoff
    ]
    proposals_by_id = {row["id"]: row for row in proposals}
    proposal_statuses = Counter({status: 0 for status in _FIT_STATUSES})
    reason_counts: Counter[str] = Counter()
    behavior_commits: set[str] = set()
    behavior_fingerprints: set[str] = set()
    runtime_sessions: set[str] = set()
    frozen_identities: set[str] = set()
    for proposal in proposals:
        evidence = _proposal_mandate(proposal)
        proposal_statuses[evidence["status"]] += 1
        reason_counts.update(_fit_reasons(proposal))
        for field, target in (
            ("producer_commit", behavior_commits),
            ("behavior_fingerprint", behavior_fingerprints),
            ("runtime_session_id", runtime_sessions),
        ):
            if proposal.get(field):
                target.add(str(proposal[field]))
        if evidence["identity"] is not None:
            frozen_identities.add(json.dumps(evidence["identity"], sort_keys=True, separators=(",", ":")))

    position_statuses = Counter({status: 0 for status in _FIT_STATUSES})
    duration_summary = {
        "declared_horizon_positions": 0,
        "measurable_duration_positions": 0,
        "compatible_count": 0,
        "incompatible_count": 0,
        "not_declared_count": 0,
        "not_available_count": 0,
    }
    overrun = {
        "declared_max_horizon_positions": 0,
        "measurable_count": 0,
        "overrun_count": 0,
        "not_measurable_count": 0,
    }
    outcome_by_status: dict[str, dict[str, Any]] = {
        status: {"eligible_count": 0, "net_pnl": None} for status in _FIT_STATUSES
    }
    exclusions: Counter[str] = Counter()
    for position in positions:
        proposal = proposals_by_id.get(position.get("proposal_id"))
        if proposal is None:
            proposal = _row(db, "trade_proposals", position.get("proposal_id"))
        evidence = _proposal_mandate(proposal)
        status = evidence["status"]
        position_statuses[status] += 1
        if position.get("producer_commit"):
            behavior_commits.add(str(position["producer_commit"]))
        if position.get("behavior_fingerprint"):
            behavior_fingerprints.add(str(position["behavior_fingerprint"]))
        if position.get("runtime_session_id"):
            runtime_sessions.add(str(position["runtime_session_id"]))

        minimum, maximum, horizon_status = _horizon_declaration(proposal)
        if minimum is None and maximum is None:
            if horizon_status == "not_declared":
                duration_summary["not_declared_count"] += 1
            else:
                duration_summary["not_available_count"] += 1
        else:
            duration_summary["declared_horizon_positions"] += 1
            actual_hours = _duration(position)
            if actual_hours is None:
                duration_summary["not_available_count"] += 1
            else:
                duration_summary["measurable_duration_positions"] += 1
                compatible = (minimum is None or actual_hours >= minimum) and (
                    maximum is None or actual_hours <= maximum
                )
                duration_summary["compatible_count" if compatible else "incompatible_count"] += 1
            if maximum is not None:
                overrun["declared_max_horizon_positions"] += 1
                actual_hours = _duration(position)
                if actual_hours is None:
                    overrun["not_measurable_count"] += 1
                else:
                    overrun["measurable_count"] += 1
                    if actual_hours > maximum:
                        overrun["overrun_count"] += 1

        try:
            outcome_assessment = assess_reportable_outcome(position, db=db)
            eligible = outcome_assessment.eligible
            exclusion_reason = outcome_assessment.reason
        except Exception:
            eligible = False
            exclusion_reason = "outcome_assessment_unavailable"
        if eligible:
            net = _finite(position.get("realized_pnl"))
            if net is None:
                exclusions["realized_pnl_not_finite"] += 1
                continue
            bucket = outcome_by_status[status]
            bucket["eligible_count"] += 1
            bucket["net_pnl"] = net if bucket["net_pnl"] is None else bucket["net_pnl"] + net
        else:
            exclusions[str(exclusion_reason or "outcome_not_eligible")] += 1

    for status in _FIT_STATUSES:
        outcome_by_status[status]["net_pnl"] = (
            round(outcome_by_status[status]["net_pnl"], 12)
            if outcome_by_status[status]["net_pnl"] is not None
            else None
        )
    frozen_identity_values = [json.loads(value) for value in sorted(frozen_identities)]
    return {
        "contract_version": COMPLIANCE_CONTRACT_VERSION,
        "read_only": True,
        "availability": "available",
        "window": {
            "hours": max(1, int(hours)),
            "from": cutoff.isoformat(),
            "to": now.isoformat(),
        },
        "configured_mandate": configured,
        "mandate_identity": configured.get("identity"),
        "trading_mandate_identity": configured.get("identity"),
        "counts": {
            "proposals_total": len(proposals),
            "positions_total": len(positions),
            "proposals_by_fit_status": dict(proposal_statuses),
            "positions_by_fit_status": dict(position_statuses),
        },
        "reason_code_distribution": dict(sorted(reason_counts.items())),
        "holding_duration_compatibility": duration_summary,
        "horizon_overrun": overrun,
        "eligible_genuine_net_outcomes": {
            "by_fit_status": outcome_by_status,
            "total_eligible_count": sum(item["eligible_count"] for item in outcome_by_status.values()),
        },
        "denominators": {
            "proposal_rows_considered": len(proposals),
            "position_rows_considered": len(positions),
            "proposal_fit_status_denominator": sum(proposal_statuses.values()),
            "position_fit_status_denominator": sum(position_statuses.values()),
            "eligible_genuine_outcome_denominator": sum(
                item["eligible_count"] for item in outcome_by_status.values()
            ),
        },
        "exclusions": {
            "positions_by_reason": dict(sorted(exclusions.items())),
            "historical_or_missing_mandate_evidence": position_statuses["not_recorded"],
            "unavailable_or_malformed_mandate_evidence": (
                position_statuses["mandate_unavailable"]
            ),
        },
        "cohort_identity": {
            "fit_contract_version": TRADING_MANDATE_FIT_VERSION,
            "mandate_contract_version": TRADING_MANDATE_VERSION,
            "frozen_mandate_identities": frozen_identity_values,
            "producer_commits": sorted(behavior_commits),
            "behavior_fingerprints": sorted(behavior_fingerprints),
            "runtime_session_ids": sorted(runtime_sessions),
            "join": "positions.proposal_id -> trade_proposals.id",
            "source": "frozen_proposal_evidence_only",
        },
        "observational_statement": (
            "Observational comparison only: fit status is descriptive provenance and "
            "does not prove that mandate compliance caused profit or loss. No recommendation, "
            "enforcement decision, leaderboard, promotion result, or trading mutation is produced."
        ),
    }


def build_mandate_compliance_report(
    *,
    proposal_id: str | None = None,
    position_id: str | None = None,
    hours: int = 720,
    db: Any = None,
) -> dict[str, Any]:
    """Build a single-record or aggregate report using read-only SQL only."""
    if proposal_id and position_id:
        raise ValueError("choose either proposal_id or position_id")
    configured = _configured()
    if db is None:
        from aletheia.storage.sqlite import readonly_db

        try:
            with readonly_db() as read_db:
                return build_mandate_compliance_report(
                    proposal_id=proposal_id,
                    position_id=position_id,
                    hours=hours,
                    db=read_db,
                )
        except FileNotFoundError:
            return {
                "contract_version": COMPLIANCE_CONTRACT_VERSION,
                "read_only": True,
                "availability": "unavailable",
                "unavailable_reason": "database_not_available",
                "configured_mandate": configured,
                "observational_statement": (
                    "No compliance conclusion is available without the runtime database."
                ),
            }
    source = db
    if proposal_id:
        return _single_report(
            db=source,
            record_type="proposal",
            record=_row(source, "trade_proposals", proposal_id),
            configured=configured,
        )
    if position_id:
        return _single_report(
            db=source,
            record_type="position",
            record=_row(source, "positions", position_id),
            configured=configured,
        )
    return _aggregate_report(db=source, hours=hours, configured=configured)