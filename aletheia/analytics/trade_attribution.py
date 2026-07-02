"""Trade attribution — join closed positions back to their full causal chain.

Produces enriched rows linking position → order → proposal → opportunity →
strategy → universe_member → risk_decision for post-trade analysis.

Read-only. No DB mutations. No LLM calls.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.analytics.trade_attribution")


def build_trade_attribution_rows(limit: int = 500) -> list[dict]:
    """Build attribution rows for closed paper/demo positions.

    Each row joins position back through the full causal chain. Missing joins
    produce None fields, never errors.
    """
    positions = repo.list_positions(status="closed", limit=limit)
    rows: list[dict] = []

    for pos in positions:
        row = _build_row(pos)
        if row is not None:
            rows.append(row)

    return rows


def _build_row(pos: dict) -> dict | None:
    """Build a single attribution row from a closed position."""
    proposal = _safe_get(repo.get_trade_proposal, pos.get("proposal_id"))
    order = _safe_get(repo.get_order, pos.get("order_id")) if pos.get("order_id") else None
    opportunity = _safe_get(repo.get_opportunity, proposal.get("opportunity_id")) if proposal else None
    strategy = _safe_get(repo.get_strategy, pos.get("strategy_id"))
    risk_decision = _lookup_risk_decision(proposal.get("id") if proposal else None)
    universe_member = _lookup_universe_member(opportunity)

    # Serenity fallback: when the position's opportunity_id points to
    # serenity_opportunities (not market_observer's opportunities table),
    # the join above returns None. Fall back to serenity attribution.
    serenity_opp = None
    if opportunity is None and pos.get("trade_school") == "serenity" and pos.get("opportunity_id"):
        serenity_opp = _safe_get(repo.get_serenity_opportunity, pos["opportunity_id"])

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
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": round(realized_pnl_pct, 4),
        "close_reason": pos.get("close_reason"),
        "trade_school": pos.get("trade_school"),
        "opened_at": pos.get("opened_at"),
        "closed_at": pos.get("closed_at"),
        "holding_hours": holding_hours,
        "risk_approved": risk_decision.get("approved") if risk_decision else None,
        "risk_violations": _parse_json_list(risk_decision.get("violations")) if risk_decision else [],
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

def _safe_get(fn, key: str | None) -> dict | None:
    if not key:
        return None
    try:
        return fn(key)
    except Exception:
        return None


def _lookup_risk_decision(proposal_id: str | None) -> dict | None:
    if not proposal_id:
        return None
    try:
        decisions = repo.list_risk_decisions(limit=200)
        for d in decisions:
            if d.get("proposal_id") == proposal_id:
                return d
    except Exception:
        pass
    return None


def _lookup_universe_member(opportunity: dict | None) -> dict | None:
    if not opportunity:
        return None
    member_id = opportunity.get("universe_member_id")
    if not member_id:
        return None
    try:
        return repo.get_universe_member(member_id)
    except Exception:
        return None


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
