"""Trade attribution — join closed positions back to their full causal chain.

Produces enriched rows linking position → order → proposal → opportunity →
strategy → universe_member → risk_decision for post-trade analysis.

Read-only. No DB mutations. No LLM calls.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from aletheia.analytics.outcome_integrity import (
    assess_reportable_outcome,
    eligible_strategy_outcomes,
    raw_account_events,
)
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.analytics.trade_attribution")

_NOT_RECORDED = "not recorded"
_AMBIGUOUS = "ambiguous_integrity_error"
_CONFLICTING = "conflicting_integrity_error"
_DECISION_IDENTITY_FIELDS = (
    "proposal_id",
    "opportunity_id",
    "symbol",
    "direction",
    "market_type",
)


def _recorded_json(raw: Any, expected_type: type) -> Any:
    if isinstance(raw, expected_type):
        return raw if raw else _NOT_RECORDED
    if not isinstance(raw, str) or not raw.strip():
        return _NOT_RECORDED
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _NOT_RECORDED
    return parsed if isinstance(parsed, expected_type) and parsed else _NOT_RECORDED


def proposal_decision_context(
    proposal_id: str | None,
    *,
    proposal: dict | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Read complete decision context through one exact Proposal identity."""
    if not proposal_id:
        return {
            "decision_context_status": _NOT_RECORDED,
            "pi_decision_reasoning": _NOT_RECORDED,
            "pi_decision_bear_case": _NOT_RECORDED,
            "pi_decision_assumptions": _NOT_RECORDED,
            "proposal_agent_rationale": _NOT_RECORDED,
            "decision_evidence_manifest": _NOT_RECORDED,
            "decision_learning_manifest": _NOT_RECORDED,
        }
    exact_proposal = proposal or _read_one(db, "trade_proposals", proposal_id, repo.get_trade_proposal)
    if db is None:
        decision = _safe_get(repo.get_pi_decision_for_proposal, proposal_id)
    else:
        decisions = db.execute(
            "SELECT * FROM pi_decisions WHERE proposal_id=? "
            "AND action!='signal_pending' ORDER BY datetime(created_at) ASC, rowid ASC LIMIT 2",
            (proposal_id,),
        ).fetchall()
        decision = (
            {"_integrity_error": _AMBIGUOUS, "proposal_id": proposal_id}
            if len(decisions) > 1
            else dict(decisions[0]) if decisions else None
        )

    def integrity_context(status: str) -> dict[str, Any]:
        rationale = (exact_proposal or {}).get("agent_rationale")
        return {
            "decision_context_status": status,
            "pi_decision_reasoning": status,
            "pi_decision_bear_case": status,
            "pi_decision_assumptions": status,
            "proposal_agent_rationale": (
                str(rationale) if rationale is not None and str(rationale).strip()
                else _NOT_RECORDED
            ),
            "decision_evidence_manifest": status,
            "decision_learning_manifest": status,
        }

    if (decision or {}).get("_integrity_error") == _AMBIGUOUS:
        return integrity_context(_AMBIGUOUS)
    if decision is None or exact_proposal is None:
        return integrity_context(_NOT_RECORDED)

    proposal_identity = {
        "proposal_id": exact_proposal.get("id"),
        "opportunity_id": exact_proposal.get("opportunity_id"),
        "symbol": exact_proposal.get("symbol"),
        "direction": exact_proposal.get("direction"),
        "market_type": exact_proposal.get("market_type"),
    }
    decision_identity = {
        field: decision.get(field) for field in _DECISION_IDENTITY_FIELDS
    }
    if any(
        not proposal_identity[field] or not decision_identity[field]
        for field in _DECISION_IDENTITY_FIELDS
    ):
        return integrity_context(_NOT_RECORDED)
    if (
        proposal_identity["proposal_id"] != proposal_id
        or decision_identity["proposal_id"] != proposal_id
    ):
        return integrity_context(_CONFLICTING)
    if any(
        proposal_identity[field] != decision_identity[field]
        for field in _DECISION_IDENTITY_FIELDS
    ):
        return integrity_context(_CONFLICTING)

    def text(value: Any) -> str:
        return str(value) if value is not None and str(value).strip() else _NOT_RECORDED

    return {
        "decision_context_status": "available",
        "pi_decision_reasoning": text(decision.get("reasoning")),
        "pi_decision_bear_case": text(decision.get("bear_case")),
        "pi_decision_assumptions": _recorded_json(
            decision.get("key_assumptions"), list
        ),
        "proposal_agent_rationale": text(
            (exact_proposal or {}).get("agent_rationale")
        ),
        "decision_evidence_manifest": _recorded_json(
            decision.get("evidence_manifest_json"), dict
        ),
        "decision_learning_manifest": _recorded_json(
            decision.get("learning_manifest_json"), dict
        ),
    }


def build_trade_attribution_for_position(
    position_id: str, *, db: Any = None
) -> dict[str, Any] | None:
    """Build one exact eligible Position attribution without pagination fallback."""
    source = db or repo.get_db()
    row = source.execute(
        "SELECT * FROM positions WHERE id=?",
        (position_id,),
    ).fetchone()
    if row is None:
        return None
    position = dict(row)
    if not assess_reportable_outcome(position).eligible:
        return None
    return _build_row(position, db=source)


def build_trade_attribution_rows(
    limit: int = 500,
    *,
    closed_after: str | None = None,
    symbol: str | None = None,
    db: Any = None,
    include_source_status: bool = False,
) -> list[dict] | tuple[list[dict], bool | None]:
    if (
        closed_after is None
        and symbol is None
        and db is None
        and not include_source_status
    ):
        positions = list(reversed(eligible_strategy_outcomes()))[:limit]
        source_truncated = None
    else:
        source_limit = 10_000
        source_rows = raw_account_events(
            closed_after=closed_after,
            symbol=symbol,
            limit=source_limit,
            db=db,
        )
        source_truncated = None if len(source_rows) >= source_limit else False
        positions = [
            row for row in source_rows if assess_reportable_outcome(row).eligible
        ]
        positions = list(reversed(positions))[:limit]
    rows = []
    for pos in positions:
        row = _build_row(pos, db=db)
        if row is not None:
            rows.append(row)
    return (rows, source_truncated) if include_source_status else rows


def _build_row(pos: dict, *, db: Any = None) -> dict | None:
    """Build a single attribution row from a closed position."""
    proposal = _read_one(db, "trade_proposals", pos.get("proposal_id"), repo.get_trade_proposal)
    order = _read_one(db, "orders", pos.get("order_id"), repo.get_order)
    direct_opportunity_id = pos.get("opportunity_id")
    if (
        proposal is None
        or order is None
        or order.get("proposal_id") != proposal.get("id")
        or not direct_opportunity_id
        or proposal.get("opportunity_id") != direct_opportunity_id
    ):
        return None
    trade_school = pos.get("trade_school") or proposal.get("trade_school")
    serenity_opp = None
    opportunity = None
    if trade_school == "serenity":
        serenity_opp = _read_one(
            db, "serenity_opportunities", direct_opportunity_id, repo.get_serenity_opportunity
        )
    else:
        opportunity = _read_one(db, "opportunities", direct_opportunity_id, repo.get_opportunity)
    strategy = _read_one(db, "strategy_records", pos.get("strategy_id"), repo.get_strategy)
    risk_decision = _read_one(
        db,
        "risk_decisions",
        order.get("risk_decision_id"),
        lambda decision_id: repo._select_one("risk_decisions", decision_id),
    )
    universe_member = _lookup_universe_member(opportunity, db=db)

    if opportunity is None and serenity_opp is None:
        return None
    decision_context = proposal_decision_context(
        proposal.get("id"), proposal=proposal, db=db
    )

    # Compute realized_pnl_pct
    entry_price = float(pos.get("entry_price") or 0)
    realized_pnl = float(pos.get("realized_pnl") or 0)
    quantity = float(pos.get("quantity") or 0)
    notional = entry_price * quantity if entry_price and quantity else 0
    realized_pnl_pct = (realized_pnl / notional * 100) if notional else 0.0

    # Holding hours
    holding_hours = _compute_holding_hours(pos.get("opened_at"), pos.get("closed_at"))

    # Parse risk_flags from universe_member
    risk_flags = _parse_json_list(universe_member.get("risk_flags")) if universe_member else []

    # Resolve source fields from whichever opportunity source matched
    _opp_id = (opportunity.get("id") if opportunity else None) or (serenity_opp.get("id") if serenity_opp else None)
    _source_agent = (opportunity.get("source_agent") if opportunity else None) or ("serenity_research" if serenity_opp else None)
    _scanner_family = (opportunity.get("scanner_family") if opportunity else None) or ("serenity" if serenity_opp else None)
    _confidence = opportunity.get("confidence") if opportunity else None
    _confidence_score = (opportunity.get("confidence_score") if opportunity else None) or (serenity_opp.get("confidence_score") if serenity_opp else None)
    _opp_status = (opportunity.get("status") if opportunity else None) or (serenity_opp.get("status") if serenity_opp else None)

    return {
        "position_id": pos.get("id"),
        "proposal_id": proposal.get("id") if proposal else None,
        "order_id": pos.get("order_id"),
        "opportunity_id": _opp_id,
        "strategy_id": pos.get("strategy_id"),
        "symbol": pos.get("symbol"),
        "market_type": pos.get("market_type"),
        "direction": pos.get("direction"),
        "execution_mode": pos.get("execution_mode"),
        "source_agent": _source_agent,
        "scanner_family": _scanner_family,
        "confidence": _confidence,
        "confidence_score": _confidence_score,
        "opportunity_status": _opp_status,
        "universe_member_id": opportunity.get("universe_member_id") if opportunity else None,
        "universe_rank": universe_member.get("rank") if universe_member else None,
        "universe_total_score": universe_member.get("total_score") if universe_member else None,
        "opportunity_score": universe_member.get("opportunity_score") if universe_member else None,
        "tradability_score": universe_member.get("tradability_score") if universe_member else None,
        "late_entry_penalty": universe_member.get("late_entry_penalty") if universe_member else None,
        "crowding_risk": universe_member.get("crowding_risk") if universe_member else None,
        "risk_flags": risk_flags,
        "entry_price": entry_price,
        "exit_price": float(pos.get("current_price") or entry_price),
        "quantity": quantity,
        "entry_fee": pos.get("entry_fee"),
        "exit_fee": pos.get("exit_fee"),
        "funding_cost": pos.get("funding_cost"),
        "funding_source": pos.get("funding_source"),
        "entry_price_source": pos.get("entry_price_source"),
        "entry_fill_verified": pos.get("entry_fill_verified"),
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": round(realized_pnl_pct, 4),
        "close_reason": pos.get("close_reason"),
        "close_category": pos.get("close_category"),
        "exit_price_source": pos.get("exit_price_source"),
        "exit_fill_verified": pos.get("exit_fill_verified"),
        "outcome_eligible": pos.get("outcome_eligible"),
        "outcome_exclusion_reason": pos.get("outcome_exclusion_reason"),
        "outcome_semantics_version": pos.get("outcome_semantics_version"),
        "trade_school": pos.get("trade_school"),
        "opened_at": pos.get("opened_at"),
        "closed_at": pos.get("closed_at"),
        "holding_hours": holding_hours,
        "risk_approved": risk_decision.get("approved") if risk_decision else None,
        "risk_violations": _parse_json_list(risk_decision.get("violations")) if risk_decision else [],
        **decision_context,
    }


# ---------------------------------------------------------------------------
# Factor key generation
# ---------------------------------------------------------------------------

def confidence_bucket(score: int | None, confidence: str | None) -> str:
    """Map confidence score/label to a bucket for factor grouping."""
    if score is not None:
        if score >= 80:
            return "very_high"
        if score >= 65:
            return "high"
        if score >= 50:
            return "medium"
        return "low"
    conf = (confidence or "").lower()
    if conf == "high":
        return "high"
    if conf == "medium":
        return "medium"
    return "low"


def late_entry_bucket(value: float | None) -> str:
    """Map late_entry_penalty to a bucket."""
    if value is None:
        return "unknown"
    if value >= 20:
        return "very_late"
    if value >= 10:
        return "late"
    if value > 0:
        return "mild"
    return "clean"


def factor_key(row: dict) -> str:
    """Generate a composite factor key for grouping trades."""
    scanner = row.get("scanner_family") or "unknown"
    symbol = row.get("symbol") or "unknown"
    direction = row.get("direction") or "unknown"
    conf = confidence_bucket(row.get("confidence_score"), row.get("confidence"))
    late = late_entry_bucket(row.get("late_entry_penalty"))
    return f"{scanner}:{symbol}:{direction}:{conf}:{late}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_one(db: Any, table: str, key: str | None, fallback) -> dict | None:
    if not key:
        return None
    if db is None:
        return _safe_get(fallback, key)
    try:
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (key,)).fetchone()
    except Exception:
        return None
    return dict(row) if row is not None else None


def _safe_get(fn, key: str | None) -> dict | None:
    if not key:
        return None
    try:
        return fn(key)
    except Exception:
        return None


def _lookup_universe_member(opportunity: dict | None, *, db: Any = None) -> dict | None:
    if not opportunity:
        return None
    member_id = opportunity.get("universe_member_id")
    return _read_one(db, "universe_members", member_id, repo.get_universe_member)


def _parse_json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _compute_holding_hours(opened_at: str | None, closed_at: str | None) -> float | None:
    if not opened_at or not closed_at:
        return None
    try:
        from datetime import datetime, timezone
        fmt_patterns = ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]
        t_open = _parse_dt(opened_at, fmt_patterns)
        t_close = _parse_dt(closed_at, fmt_patterns)
        if t_open and t_close:
            delta = (t_close - t_open).total_seconds() / 3600.0
            return round(delta, 2)
    except Exception:
        pass
    return None


def _parse_dt(s: str, patterns: list[str]):
    from datetime import datetime, timezone
    for p in patterns:
        try:
            dt = datetime.strptime(s, p)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
