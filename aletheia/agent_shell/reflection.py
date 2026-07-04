"""System Operator Reflection Loop — records decisions and injects outcomes.

Implements the "last decision reflection" pattern from arXiv:2510.08068
(+31% improvement via verbal feedback). Zero extra LLM cost: just writes
the previous decision's outcome back into the next prompt.

Storage: single JSON file at data/pi_reflection.json (rotated each cycle).
No new DB tables needed.
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
        )
        insert_pi_decision(pi_dec)
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
        missed_str = _compute_missed_opportunity(symbol, decided_at) if symbol else ""
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
    """Check what happened to a trade decision since it was made."""
    if not symbol:
        return ""

    try:
        from aletheia.storage.repositories import list_positions
        positions = list_positions(status="open", limit=20)
        for p in positions:
            if p.get("symbol") == symbol:
                upnl = p.get("unrealized_pnl")
                if upnl is not None:
                    return f"Outcome: position still open, uPnL=${upnl:+.2f}."
                return "Outcome: position still open."

        closed = list_positions(status="closed", limit=50)
        for p in closed:
            if p.get("symbol") == symbol and (p.get("opened_at") or "") >= decided_at:
                rpnl = p.get("realized_pnl")
                if rpnl is not None:
                    return f"Outcome: closed, realized=${rpnl:+.2f}."
                return "Outcome: closed."
    except Exception:
        pass

    if proposal_id:
        return "Outcome: proposal submitted, position status unknown."
    return ""


def _compute_missed_opportunity(symbol: str | None, decided_at: str) -> str:
    """Check if a skipped symbol moved favorably after the skip."""
    if not symbol:
        return ""

    try:
        from aletheia.storage.repositories import list_universe_feature_snapshots
        snaps = list_universe_feature_snapshots(limit=50)
        for s in snaps:
            if s.get("symbol") == symbol:
                ret_4h = s.get("return_4h")
                if ret_4h is not None and abs(ret_4h) > 1.0:
                    direction = "up" if ret_4h > 0 else "down"
                    return f"Since skip: {symbol} moved {direction} {abs(ret_4h):.1f}% in 4h."
                return ""
    except Exception:
        pass
    return ""


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
