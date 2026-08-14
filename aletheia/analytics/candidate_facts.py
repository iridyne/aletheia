"""Complete candidate-denominator facts, separate from Position economics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from typing import Any, Iterable
from aletheia.analytics.filter_evidence import decode_filter_decision_batch

from aletheia.storage import repositories as repo


FACT_CONTRACT_VERSION = "profit_loop_fact/v1"
_FIXED_HORIZONS = frozenset({"15m", "1h", "4h", "24h"})


def _utc(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _visibility_error(row: dict[str, Any]) -> str | None:
    fields = ("evaluated_at", "created_at", "entry_ts", "filled_at", "opened_at", "closed_at")
    populated = [row.get(field) for field in fields if row.get(field) not in (None, "")]
    if not populated:
        return "candidate_timestamp_missing"
    if any(_utc(value) is None for value in populated):
        return "candidate_timestamp_invalid"
    return None


def _visible(row: dict[str, Any], cutoff: datetime) -> bool:
    seen_timestamp = False
    for field in ("evaluated_at", "created_at", "entry_ts", "filled_at", "opened_at", "closed_at"):
        raw = row.get(field)
        if raw in (None, ""):
            continue
        seen_timestamp = True
        value = _utc(raw)
        if value is None or value > cutoff:
            return False
    return seen_timestamp


def _rows(db: Any, sql: str, params: Iterable[object] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, tuple(params)).fetchall()]


def _one(db: Any, table: str, identity: object) -> dict[str, Any] | None:
    if identity in (None, ""):
        return None
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if exists is None:
        return None
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    return dict(row) if row is not None else None


def _parsed(value: object, expected: type) -> object:
    if isinstance(value, expected):
        return value
    if not isinstance(value, str):
        return expected()
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return expected()
    return parsed if isinstance(parsed, expected) else expected()


def _batch_facts(
    db: Any,
    cutoff: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for batch in _rows(
        db,
        "SELECT * FROM filter_decision_batches ORDER BY datetime(evaluated_at), rowid",
    ):
        if not _visible(batch, cutoff):
            if (reason := _visibility_error(batch)) is not None:
                exclusions.append(
                    {
                        "source_table": "filter_decision_batches",
                        "source_batch_id": batch.get("id"),
                        "event_at": batch.get("evaluated_at"),
                        "availability": "unavailable",
                        "unavailable_reason": reason,
                    }
                )
            continue
        try:
            raw_facts = decode_filter_decision_batch(str(batch.get("candidate_facts") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            exclusions.append(
                {
                    "source_table": "filter_decision_batches",
                    "source_batch_id": batch.get("id"),
                    "event_at": batch.get("evaluated_at"),
                    "availability": "unavailable",
                    "unavailable_reason": "malformed_candidate_facts_json",
                }
            )
            continue
        for sequence, raw in enumerate(raw_facts):
            if not isinstance(raw, dict):
                exclusions.append(
                    {
                        "source_table": "filter_decision_batches",
                        "source_batch_id": batch.get("id"),
                        "event_at": batch.get("evaluated_at"),
                        "availability": "unavailable",
                        "unavailable_reason": "candidate_fact_not_object",
                    }
                )
                continue
            candidate_key = raw.get("candidate_key") or raw.get("outcome_candidate_key")
            if not isinstance(candidate_key, str) or not candidate_key.strip():
                exclusions.append(
                    {
                        "source_table": "filter_decision_batches",
                        "source_batch_id": batch.get("id"),
                        "event_at": batch.get("evaluated_at"),
                        "availability": "unavailable",
                        "unavailable_reason": "candidate_identity_missing",
                    }
                )
                continue
            fact = {
                **raw,
                "id": raw.get("id") or f"{batch['id']}:{sequence}",
                "candidate_key": candidate_key,
                "candidate_kind": raw.get("candidate_kind") or batch.get("candidate_kind"),
                "run_id": raw.get("run_id") or batch.get("run_id"),
                "filter_name": raw.get("filter_name") or batch.get("filter_name"),
                "stage": raw.get("stage") or batch.get("stage"),
                "decision": raw.get("decision") or raw.get("status") or "not_applicable",
                "reason_code": raw.get("reason_code") or "batch_fact",
                "filter_version": raw.get("filter_version") or batch.get("filter_version"),
                "code_commit": raw.get("code_commit") or batch.get("code_commit"),
                "evaluated_at": raw.get("evaluated_at") or batch.get("evaluated_at"),
                "sequence_no": raw.get("sequence_no", sequence),
                "source_table": "filter_decision_batches",
                "source_batch_id": batch["id"],
            }
            facts.append(fact)
    return facts, exclusions


def _terminal(
    filters: list[dict[str, Any]],
    opportunity: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    proposal: dict[str, Any] | None,
    risk: dict[str, Any] | None,
    order: dict[str, Any] | None,
    position: dict[str, Any] | None,
) -> str:
    rejected = next(
        (row for row in filters if str(row.get("decision") or "").lower() in {"rejected", "filtered", "error"}),
        None,
    )
    if rejected is not None and opportunity is None:
        return "filtered"
    if opportunity is None:
        return "opportunity_not_reached"
    action = str((decision or {}).get("action") or "").lower()
    if action in {"skip", "watch", "hold"}:
        return f"pi_{action}"
    if proposal is None:
        return "proposal_not_reached"
    if risk is not None and not bool(risk.get("approved")):
        return "risk_reject"
    if risk is None:
        return "risk_not_reached"
    if order is None:
        return "approved_unfilled"
    if position is None:
        return "filled_without_position" if str(order.get("status") or "").lower() == "filled" else "order_unfilled"
    return "traded"


def _identity_errors(
    ordered: list[dict[str, Any]],
    *,
    opportunity_id: str | None,
    decision: dict[str, Any] | None,
    proposal_id: str | None,
    proposal: dict[str, Any] | None,
    risk: dict[str, Any] | None,
    order: dict[str, Any] | None,
    position: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    for field, expected in (
        ("opportunity_id", opportunity_id),
        ("proposal_id", proposal_id),
    ):
        seen = {str(row[field]) for row in ordered if row.get(field)}
        if len(seen) > 1 or (expected and any(value != str(expected) for value in seen)):
            errors.append(f"candidate_{field}_mismatch")
    if decision is not None:
        if opportunity_id and decision.get("opportunity_id") not in {None, opportunity_id}:
            errors.append("decision_opportunity_mismatch")
        if decision.get("id") and decision.get("proposal_id") and proposal_id:
            if decision.get("proposal_id") != proposal_id:
                errors.append("decision_proposal_mismatch")
    if proposal is not None:
        if proposal.get("id") != proposal_id:
            errors.append("proposal_identity_mismatch")
        if opportunity_id and proposal.get("opportunity_id") != opportunity_id:
            errors.append("proposal_opportunity_mismatch")
    if risk is not None and proposal_id and risk.get("proposal_id") != proposal_id:
        errors.append("risk_proposal_mismatch")
    if order is not None:
        if proposal_id and order.get("proposal_id") != proposal_id:
            errors.append("order_proposal_mismatch")
        if risk is not None and order.get("risk_decision_id") != risk.get("id"):
            errors.append("order_risk_mismatch")
    if position is not None:
        if proposal_id and "proposal_id" in position and position.get("proposal_id") != proposal_id:
            errors.append("position_proposal_mismatch")
        if order is not None and "order_id" in position and position.get("order_id") != order.get("id"):
            errors.append("position_order_mismatch")
        if opportunity_id and "opportunity_id" in position and position.get("opportunity_id") != opportunity_id:
            errors.append("position_opportunity_mismatch")
    return list(dict.fromkeys(errors))


def build_candidate_facts(
    *,
    horizon: str = "24h",
    as_of_at: str | datetime | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Build first-exposure facts for every durable candidate, including rejects."""
    if horizon not in _FIXED_HORIZONS:
        raise ValueError(f"unsupported fixed horizon: {horizon}")
    source = db or repo.get_db()
    parsed_cutoff = _utc(as_of_at)
    if as_of_at is not None and parsed_cutoff is None:
        return {
            "contract_version": FACT_CONTRACT_VERSION,
            "fact_unit": "candidate_fact",
            "as_of_at": str(as_of_at),
            "read_only": True,
            "availability": "unavailable",
            "unavailable_count": 1,
            "unavailable_reasons": {"invalid_as_of_at": 1},
            "exclusions": [{"availability": "unavailable", "unavailable_reason": "invalid_as_of_at"}],
            "facts": [],
        }
    cutoff = parsed_cutoff or datetime.now(timezone.utc)
    explicit = _rows(
        source,
        "SELECT * FROM filter_decisions ORDER BY datetime(evaluated_at), sequence_no, rowid",
    )
    filter_rows: list[dict[str, Any]] = []
    filter_exclusions: list[dict[str, Any]] = []
    for row in explicit:
        if _visible(row, cutoff):
            filter_rows.append({**row, "source_table": "filter_decisions"})
        elif (reason := _visibility_error(row)) is not None:
            filter_exclusions.append(
                {
                    "source_table": "filter_decisions",
                    "source_batch_id": row.get("id"),
                    "event_at": row.get("evaluated_at"),
                    "availability": "unavailable",
                    "unavailable_reason": reason,
                }
            )
    batch_rows, batch_exclusions = _batch_facts(source, cutoff)
    batch_exclusions = [*filter_exclusions, *batch_exclusions]
    filter_rows.extend(batch_rows)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in filter_rows:
        identity = str(row.get("outcome_candidate_key") or row.get("candidate_key") or row.get("id"))
        grouped[identity].append(row)

    outcomes = _rows(
        source,
        "SELECT * FROM filter_candidate_outcomes WHERE horizon=? ORDER BY datetime(entry_ts), rowid",
        (horizon,),
    )
    outcome_by_key = {}
    outcome_exclusions: list[dict[str, Any]] = []
    for row in outcomes:
        if not _visible(row, cutoff):
            if (reason := _visibility_error(row)) is not None:
                outcome_exclusions.append(
                    {
                        "source_table": "filter_candidate_outcomes",
                        "source_batch_id": row.get("id"),
                        "event_at": row.get("entry_ts"),
                        "availability": "unavailable",
                        "unavailable_reason": reason,
                    }
                )
            continue
        settled_at = row.get("settled_at")
        if settled_at not in (None, ""):
            parsed_settled_at = _utc(settled_at)
            if parsed_settled_at is None:
                outcome_exclusions.append(
                    {
                        "source_table": "filter_candidate_outcomes",
                        "source_batch_id": row.get("id"),
                        "event_at": row.get("settled_at"),
                        "availability": "unavailable",
                        "unavailable_reason": "candidate_settled_at_invalid",
                    }
                )
                continue
            if parsed_settled_at > cutoff:
                continue
        candidate_key = row.get("outcome_candidate_key")
        if isinstance(candidate_key, str) and candidate_key.strip():
            outcome_by_key[candidate_key] = row
        else:
            outcome_exclusions.append(
                {
                    "source_table": "filter_candidate_outcomes",
                    "source_batch_id": row.get("id"),
                    "event_at": row.get("entry_ts"),
                    "availability": "unavailable",
                    "unavailable_reason": "candidate_identity_missing",
                }
            )

    batch_exclusions.extend(outcome_exclusions)
    facts: list[dict[str, Any]] = []
    terminal_counts: defaultdict[str, int] = defaultdict(int)
    for fact_id, rows in sorted(grouped.items(), key=lambda item: str(item[1][0].get("evaluated_at") or "")):
        ordered = sorted(
            rows,
            key=lambda row: (
                str(row.get("evaluated_at") or ""),
                int(row.get("sequence_no") or 0),
                str(row.get("id") or ""),
            ),
        )
        first = ordered[0]
        opportunity_id = next(
            (str(row["opportunity_id"]) for row in ordered if row.get("opportunity_id")),
            None,
        )
        candidate_key = str(first.get("candidate_key") or "")
        candidate_kind = str(first.get("candidate_kind") or "")
        decision_id = (
            candidate_key.removeprefix("pi_decision:")
            if candidate_kind == "pi_decision" and candidate_key.startswith("pi_decision:")
            else None
        )
        decision = _one(source, "pi_decisions", decision_id)
        if decision is not None and not _visible(decision, cutoff):
            decision = None
        if decision is None and opportunity_id and decision_id is None:
            decisions = _rows(
                source,
                "SELECT * FROM pi_decisions WHERE opportunity_id=? "
                "AND action!='signal_pending' ORDER BY datetime(created_at), rowid",
                (opportunity_id,),
            )
            decisions = [row for row in decisions if _visible(row, cutoff)]
            decision = decisions[0] if len(decisions) == 1 else None
        signal_source = str((decision or {}).get("signal_source") or "")
        opportunity = None
        if opportunity_id:
            if signal_source == "serenity":
                opportunity = _one(source, "serenity_opportunities", opportunity_id)
            elif signal_source == "market_observer":
                opportunity = _one(source, "opportunities", opportunity_id)
            else:
                market_opportunity = _one(source, "opportunities", opportunity_id)
                serenity_opportunity = _one(source, "serenity_opportunities", opportunity_id)
                opportunity = (
                    market_opportunity or serenity_opportunity
                    if (market_opportunity is None) != (serenity_opportunity is None)
                    else None
                )
        if opportunity is not None and not _visible(opportunity, cutoff):
            opportunity = None
        proposal_id = (
            candidate_key.removeprefix("proposal:")
            if candidate_kind == "proposal" and candidate_key.startswith("proposal:")
            else (decision or {}).get("proposal_id")
            or next((row.get("proposal_id") for row in ordered if row.get("proposal_id")), None)
        )
        proposal = _one(source, "trade_proposals", proposal_id)
        if proposal is not None and not _visible(proposal, cutoff):
            proposal = None
        orders = (
            _rows(
                source,
                "SELECT * FROM orders WHERE proposal_id=? "
                "AND COALESCE(trim(close_reason),'')='' "
                "ORDER BY datetime(created_at), rowid",
                (proposal_id,),
            )
            if proposal_id
            else []
        )
        orders = [row for row in orders if _visible(row, cutoff)]
        order = orders[0] if len(orders) == 1 else None
        risk = _one(source, "risk_decisions", (order or {}).get("risk_decision_id"))
        if risk is not None and not _visible(risk, cutoff):
            risk = None
        if risk is None and proposal_id:
            risks = _rows(
                source,
                "SELECT * FROM risk_decisions WHERE proposal_id=? "
                "AND decision_role='authoritative' ORDER BY datetime(created_at), rowid",
                (proposal_id,),
            )
            risks = [row for row in risks if _visible(row, cutoff)]
            risk = risks[0] if len(risks) == 1 else None
        position = _one(source, "positions", (order or {}).get("position_id"))
        if position is not None and not _visible(position, cutoff):
            position = None
        outcome = outcome_by_key.get(fact_id)
        identity_errors = _identity_errors(
            ordered,
            opportunity_id=opportunity_id,
            decision=decision,
            proposal_id=proposal_id,
            proposal=proposal,
            risk=risk,
            order=order,
            position=position,
        )
        terminal = (
            "unavailable"
            if identity_errors
            else _terminal(ordered, opportunity, decision, proposal, risk, order, position)
        )
        terminal_counts[terminal] += 1
        facts.append(
            {
                "contract_version": FACT_CONTRACT_VERSION,
                "fact_unit": "candidate_fact",
                "fact_id": fact_id,
                "event_at": first.get("evaluated_at"),
                "evidence_available_at": (outcome or {}).get("settled_at"),
                "as_of_at": cutoff.isoformat(),
                "availability": "unavailable" if identity_errors else "available",
                "unavailable_reason": ";".join(identity_errors) if identity_errors else None,
                "first_exposure": first,
                "filter_path": ordered,
                "terminal_state": terminal,
                "identity": {
                    "outcome_candidate_key": first.get("outcome_candidate_key"),
                    "candidate_key": first.get("candidate_key"),
                    "opportunity_id": opportunity_id,
                    "pi_decision_id": (decision or {}).get("id"),
                    "proposal_id": (proposal or {}).get("id"),
                    "risk_decision_id": (risk or {}).get("id"),
                    "order_id": (order or {}).get("id"),
                    "position_id": (position or {}).get("id"),
                },
                "funnel": {
                    "selection_reached": True,
                    "opportunity_reached": opportunity is not None,
                    "decision_reached": decision is not None,
                    "proposal_reached": proposal is not None,
                    "risk_reached": risk is not None,
                    "risk_approved": bool(risk.get("approved")) if risk else None,
                    "order_reached": order is not None,
                    "position_reached": position is not None,
                },
                "fixed_horizon_outcome": outcome,
                "fixed_horizon": horizon,
                "variable_horizon_position_outcome": None,
            }
        )

    outcome_count = sum(
        1
        for fact in facts
        if fact.get("availability") == "available"
        and fact["fixed_horizon_outcome"] is not None
        and fact["fixed_horizon_outcome"].get("status") == "settled"
    )
    unavailable_reasons: dict[str, int] = {}
    for exclusion in batch_exclusions:
        reason = str(exclusion.get("unavailable_reason") or "candidate_evidence_unavailable")
        unavailable_reasons[reason] = unavailable_reasons.get(reason, 0) + 1
    for fact in facts:
        if fact.get("availability") == "unavailable":
            unavailable_reasons["candidate_identity_mismatch"] = (
                unavailable_reasons.get("candidate_identity_mismatch", 0) + 1
            )
    return {
        "contract_version": FACT_CONTRACT_VERSION,
        "fact_unit": "candidate_fact",
        "as_of_at": cutoff.isoformat(),
        "fixed_horizon": horizon,
        "read_only": True,
        "availability": "unavailable" if unavailable_reasons else "available",
        "candidate_count": len(facts),
        "settled_outcome_count": outcome_count,
        "outcome_coverage": outcome_count / len(facts) if facts else None,
        "unavailable_count": sum(unavailable_reasons.values()),
        "unavailable_reasons": unavailable_reasons,
        "exclusions": batch_exclusions,
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "facts": facts,
    }
