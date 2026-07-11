"""System Operator Reflection Loop — records decisions and injects outcomes.

Implements a last-decision reflection pattern without an extra LLM call. Only
causally linked proposal/position or opportunity/fixed-horizon outcomes are
rendered; symbol-only movement is never treated as the decision's result.

Storage: a rotating JSON pointer plus the durable pi_decisions audit row.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("aletheia.agent_shell.reflection")

_REFLECTION_FILE = Path("data/pi_reflection.json")


def record_decision(decision_result: dict) -> None:
    """Record the System Operator's latest decision for next-cycle reflection.

    Called after evaluate_trading_opportunity() returns.
    Persists to both JSON (reflection) and DB (pi_decisions for audit trail).
    """
    record = {
        "action": decision_result.get("action"),
        "symbol": decision_result.get("symbol"),
        "direction": decision_result.get("direction"),
        "conviction": decision_result.get("conviction"),
        "reasoning": decision_result.get("reasoning", "")[:200],
        "proposal_id": decision_result.get("proposal_id"),
        "opportunity_id": decision_result.get("opportunity_id"),
        # Phase 2 (Serenity Option A): discriminator for which table opportunity_id
        # points at. Default 'market_observer' keeps legacy rows resolving as before.
        "signal_source": decision_result.get("signal_source") or "market_observer",
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }

    # JSON file for reflection loop
    try:
        _REFLECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REFLECTION_FILE.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug("reflection record failed: %s", e)

    # DB persistence for audit trail + downstream consumers (Phase 2 of PI sole decision maker)
    try:
        from aletheia.storage.models import PiDecision
        from aletheia.storage.repositories import insert_pi_decision
        raw_assumptions = decision_result.get("key_assumptions")
        assumptions_json = (
            json.dumps(raw_assumptions) if isinstance(raw_assumptions, list) else "[]"
        )
        pi_dec = PiDecision(
            symbol=record["symbol"],
            direction=record["direction"],
            action=record["action"] or "skip",
            conviction=record.get("conviction") or 0,
            reasoning=record.get("reasoning") or "",
            proposal_id=record.get("proposal_id"),
            opportunity_id=decision_result.get("opportunity_id"),
            signal_source=record.get("signal_source") or "market_observer",
            debate_ran=decision_result.get("debate_ran", False),
            debate_initial_conviction=decision_result.get("debate_initial_conviction"),
            debate_bull_confidence=(
                decision_result.get("debate_bull_confidence")
                if decision_result.get("debate_bull_confidence") is not None
                else decision_result.get("debate_bull")
            ),
            debate_bear_confidence=(
                decision_result.get("debate_bear_confidence")
                if decision_result.get("debate_bear_confidence") is not None
                else decision_result.get("debate_bear")
            ),
            debate_adjustment=decision_result.get("debate_adjustment"),
            bear_case=decision_result.get("bear_case") or "",
            key_assumptions=assumptions_json,
            learning_manifest_json=json.dumps(
                decision_result.get("learning_manifest") or {}, sort_keys=True
            ),
        )
        insert_pi_decision(pi_dec)
        try:
            from aletheia.analytics.filter_evidence import record_operator_filter_trace

            record_operator_filter_trace(pi_dec.id, decision_result)
        except Exception as evidence_error:
            logger.warning("operator filter evidence unavailable: %s", evidence_error)
    except Exception as e:
        logger.debug("pi_decision db persist failed: %s", e)


def get_last_reflection() -> str | None:
    """Build a natural-language reflection of the last PI decision + its outcome.

    Returns a one-paragraph string for prompt injection, or None if unavailable.
    """
    try:
        if not _REFLECTION_FILE.exists():
            return None
        raw = json.loads(_REFLECTION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    action = raw.get("action")
    symbol = raw.get("symbol")
    direction = raw.get("direction")
    conviction = raw.get("conviction", 0)
    reasoning = raw.get("reasoning", "")
    decided_at = raw.get("decided_at", "")
    proposal_id = raw.get("proposal_id")
    opportunity_id = raw.get("opportunity_id")
    signal_source = raw.get("signal_source") or "market_observer"

    if not action or not decided_at:
        return None

    # Compute outcome if it was a trade
    outcome_str = _compute_outcome(symbol, proposal_id, decided_at)

    if action == "trade" and symbol:
        return (
            f"Last decision ({_time_ago(decided_at)}): "
            f"{action.upper()} {symbol} {direction} conviction={conviction}. "
            f"Reason: {reasoning}. {outcome_str}"
        )
    elif action == "skip":
        missed_str = _compute_missed_opportunity(
            opportunity_id, signal_source, direction, decided_at
        )
        return (
            f"Last decision ({_time_ago(decided_at)}): SKIP"
            f"{f' on {symbol} {direction}' if symbol else ''}. "
            f"Reason: {reasoning}. {missed_str}"
        )
    else:
        return (
            f"Last decision ({_time_ago(decided_at)}): {action.upper()}"
            f"{f' {symbol}' if symbol else ''} conviction={conviction}. "
            f"Reason: {reasoning}. {outcome_str}"
        )


def _compute_outcome(symbol: str | None, proposal_id: str | None, decided_at: str) -> str:
    """Resolve an executed outcome only through the decision's proposal id."""
    if not proposal_id:
        return "Outcome unavailable: no causally linked proposal/position."
    try:
        from aletheia.storage.sqlite import get_db
        row = get_db().execute(
            "SELECT * FROM positions WHERE proposal_id=? ORDER BY opened_at DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return "Outcome unavailable: proposal has no causally linked position."
        position = dict(row)
        if position.get("status") == "open":
            upnl = position.get("unrealized_pnl")
            return (
                f"Outcome pending: linked position still open, uPnL=${float(upnl):+.2f}."
                if upnl is not None else "Outcome pending: linked position still open."
            )
        from aletheia.learning.outcomes import assess_realized_position
        assessed = assess_realized_position(position)
        if not assessed.eligible:
            return f"Outcome unavailable: linked close excluded ({assessed.reason})."
        return (
            f"Outcome: causally linked close, realized=${float(position['realized_pnl']):+.2f} "
            f"(net={assessed.net_return_pct:+.2f}%)."
        )
    except Exception:
        return "Outcome unavailable: causal lookup failed."


def _compute_missed_opportunity(
    opportunity_id: str | None,
    signal_source: str,
    direction: str | None,
    decided_at: str,
) -> str:
    """Resolve a skip only from its exact fixed-horizon candidate outcome."""
    if not opportunity_id:
        return "Counterfactual unavailable: no causally linked opportunity."
    if signal_source != "market_observer":
        return "Counterfactual unavailable: source has no registered fixed-horizon outcome."
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        opp = db.execute(
            "SELECT universe_member_id FROM opportunities WHERE id=?", (opportunity_id,)
        ).fetchone()
        if opp and opp[0]:
            row = db.execute(
                "SELECT * FROM universe_forward_outcomes WHERE member_id=? AND horizon='4h' "
                "AND status='settled' ORDER BY settled_at DESC LIMIT 1",
                (opp[0],),
            ).fetchone()
            if row:
                from aletheia.learning.outcomes import assess_candidate_outcome
                assessed = assess_candidate_outcome(dict(row))
                if assessed.eligible:
                    return (
                        "Causal 4h counterfactual: "
                        f"net={assessed.net_return_pct:+.2f}% ({direction or 'unknown'})."
                    )
                return f"Counterfactual unavailable: linked outcome excluded ({assessed.reason})."
        shadow = db.execute(
            "SELECT * FROM shadow_trades WHERE opportunity_id=? AND resolved_at IS NOT NULL "
            "ORDER BY resolved_at DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
        if shadow:
            data = dict(shadow)
            if data.get("net_pnl_pct") is not None and data.get("cost_model_version"):
                return f"Causal shadow counterfactual: net={float(data['net_pnl_pct']):+.2f}%."
    except Exception:
        return "Counterfactual unavailable: causal lookup failed."
    return "Counterfactual unavailable: linked outcome not settled."


def _time_ago(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds() / 60)}min ago"
        return f"{hours:.1f}h ago"
    except (ValueError, TypeError):
        return "unknown time"
