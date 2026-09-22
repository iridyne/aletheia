"""Read-only historical Operator decisions and their causal outcomes."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("aletheia.agent_shell.reflection")


_REFLECTION_FILE: Path | None = None


def _reflection_file() -> Path:
    if _REFLECTION_FILE is not None:
        return _REFLECTION_FILE
    from aletheia.config.loader import get_data_dir

    return get_data_dir() / "pi_reflection.json"




def get_last_reflection() -> str | None:
    """Read the last historical PI decision and its causally linked outcome."""
    try:
        path = _reflection_file()
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
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
            f"Historical decision ({_time_ago(decided_at)}): "
            f"{action.upper()} {symbol} {direction} conviction={conviction}. "
            f"Reason: {reasoning}. {outcome_str}"
        )
    elif action == "skip":
        missed_str = _compute_missed_opportunity(
            opportunity_id, signal_source, direction, decided_at
        )
        return (
            f"Historical decision ({_time_ago(decided_at)}): SKIP"
            f"{f' on {symbol} {direction}' if symbol else ''}. "
            f"Reason: {reasoning}. {missed_str}"
        )
    else:
        return (
            f"Historical decision ({_time_ago(decided_at)}): {action.upper()}"
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
        if position.get("execution_mode") == "paper_internal":
            return "Outcome unavailable: archived simulator position."
        if position.get("status") == "open":
            upnl = position.get("unrealized_pnl")
            return (
                f"Outcome pending: linked position still open, uPnL=${float(upnl):+.2f}."
                if upnl is not None else "Outcome pending: linked position still open."
            )
        from aletheia.analytics.outcome_semantics import assess_realized_position
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
        from aletheia.storage.repositories import valid_keyed_shadow_projection_sql
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
                from aletheia.analytics.outcome_semantics import assess_candidate_outcome
                assessed = assess_candidate_outcome(dict(row))
                if assessed.eligible:
                    return (
                        "Causal 4h counterfactual: "
                        f"net={assessed.net_return_pct:+.2f}% ({direction or 'unknown'})."
                    )
                return f"Counterfactual unavailable: linked outcome excluded ({assessed.reason})."
        keyed_authority = valid_keyed_shadow_projection_sql()
        shadow = db.execute(
            "SELECT * FROM shadow_trades WHERE opportunity_id=? AND resolved_at IS NOT NULL "
            "AND invalidated_at IS NULL "
            f"AND (outcome_candidate_key IS NULL OR ({keyed_authority})) "
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
