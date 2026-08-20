"""Complete candidate-denominator facts, separate from Position economics."""

from __future__ import annotations

from collections import Counter, defaultdict
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


def _visible(row: dict[str, Any], cutoff: datetime) -> bool:
    for field in ("evaluated_at", "created_at", "entry_ts", "filled_at"):
        if row.get(field):
            value = _utc(row[field])
            return value is not None and value <= cutoff
    return True


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
        "SELECT * FROM filter_decision_batches"
        " WHERE datetime(evaluated_at) <= datetime(?)"
        " ORDER BY datetime(evaluated_at), rowid",
        (cutoff.isoformat(),),
    ):
        if not _visible(batch, cutoff):
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
        declared_count = batch.get("candidate_count")
        if declared_count is not None:
            try:
                declared_count = int(declared_count)
            except (TypeError, ValueError, OverflowError):
                declared_count = None
            if declared_count is None or declared_count < 0 or declared_count != len(raw_facts):
                exclusions.append(
                    {
                        "source_table": "filter_decision_batches",
                        "source_batch_id": batch.get("id"),
                        "event_at": batch.get("evaluated_at"),
                        "availability": "unavailable",
                        "unavailable_reason": "candidate_fact_count_mismatch",
                        "declared_candidate_count": declared_count,
                        "decoded_candidate_count": len(raw_facts),
                    }
                )
                continue
        for sequence, raw in enumerate(raw_facts):
            if not isinstance(raw, dict):
                continue
            fact = {
                **raw,
                "id": raw.get("id") or f"{batch['id']}:{sequence}",
                "candidate_key": raw.get("candidate_key")
                or raw.get("outcome_candidate_key")
                or f"{batch['id']}:{sequence}",
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
    cutoff = _utc(as_of_at) or datetime.now(timezone.utc)
    explicit = _rows(
        source,
        "SELECT * FROM filter_decisions"
        " WHERE datetime(evaluated_at) <= datetime(?)"
        " ORDER BY datetime(evaluated_at), sequence_no, rowid",
        (cutoff.isoformat(),),
    )
    filter_rows = [{**row, "source_table": "filter_decisions"} for row in explicit if _visible(row, cutoff)]
    batch_rows, batch_exclusions = _batch_facts(source, cutoff)
    filter_rows.extend(batch_rows)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in filter_rows:
        identity = str(row.get("outcome_candidate_key") or row.get("candidate_key") or row.get("id"))
        grouped[identity].append(row)

    outcomes = _rows(
        source,
        "SELECT * FROM filter_candidate_outcomes"
        " WHERE horizon=? AND (entry_ts IS NULL OR datetime(entry_ts) <= datetime(?))"
        " ORDER BY datetime(entry_ts), rowid",
        (horizon, cutoff.isoformat()),
    )
    outcome_by_key = {
        str(row["outcome_candidate_key"]): row
        for row in outcomes
        if _visible(row, cutoff) and (not row.get("settled_at") or (_utc(row.get("settled_at")) or cutoff) <= cutoff)
    }

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
        terminal = _terminal(ordered, opportunity, decision, proposal, risk, order, position)
        terminal_counts[terminal] += 1
        facts.append(
            {
                "contract_version": FACT_CONTRACT_VERSION,
                "fact_unit": "candidate_fact",
                "fact_id": fact_id,
                "event_at": first.get("evaluated_at"),
                "evidence_available_at": (outcome or {}).get("settled_at"),
                "as_of_at": cutoff.isoformat(),
                "availability": "available",
                "unavailable_reason": None,
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
        if fact["fixed_horizon_outcome"] is not None and fact["fixed_horizon_outcome"].get("status") == "settled"
    )
    unavailable_reasons = Counter(
        str(item.get("unavailable_reason") or "source_unavailable")
        for item in batch_exclusions
    )
    return {
        "contract_version": FACT_CONTRACT_VERSION,
        "fact_unit": "candidate_fact",
        "as_of_at": cutoff.isoformat(),
        "fixed_horizon": horizon,
        "read_only": True,
        "candidate_count": len(facts),
        "settled_outcome_count": outcome_count,
        "outcome_coverage": outcome_count / len(facts) if facts else None,
        "unavailable_count": len(batch_exclusions),
        "unavailable_reasons": dict(sorted(unavailable_reasons.items())),
        "exclusions": batch_exclusions,
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "facts": facts,
    }
