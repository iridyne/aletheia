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


def _validate_proposal_identity(proposal, decision: Any, *, prefix: str) -> None:
    """Require one prepared Proposal to match its owning PiDecision identity."""
    getter = decision.get if isinstance(decision, dict) else lambda key: getattr(decision, key)
    if proposal.opportunity_id != getter("opportunity_id"):
        raise ValueError(f"{prefix}_opportunity_mismatch")
    if proposal.symbol != getter("symbol") or proposal.direction != getter("direction"):
        raise ValueError(f"{prefix}_identity_mismatch")
    if proposal.market_type != getter("market_type"):
        raise ValueError(f"{prefix}_market_type_mismatch")


def _reflection_record(decision_result: dict) -> dict:
    return {
        "action": decision_result.get("action"),
        "symbol": decision_result.get("symbol"),
        "direction": decision_result.get("direction"),
        "market_type": decision_result.get("market_type"),
        "conviction": decision_result.get("conviction"),
        "reasoning": decision_result.get("reasoning", "")[:200],
        "proposal_id": decision_result.get("proposal_id"),
        "proposal_status": decision_result.get("proposal_status"),
        "proposal_reason": decision_result.get("proposal_reason"),
        "opportunity_id": decision_result.get("opportunity_id"),
        # Phase 2 (Serenity Option A): discriminator for which table opportunity_id
        # points at. Default 'market_observer' keeps legacy rows resolving as before.
        "signal_source": decision_result.get("signal_source") or "market_observer",
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_reflection_record(record: dict) -> None:
    """Update the rotating convenience pointer after durable finalization."""
    try:
        _REFLECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REFLECTION_FILE.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug("reflection record failed: %s", e)


def _build_pi_decision(decision_result: dict):
    from aletheia.storage.models import PiDecision

    record = _reflection_record(decision_result)
    raw_assumptions = decision_result.get("key_assumptions")
    assumptions_json = (
        json.dumps(raw_assumptions) if isinstance(raw_assumptions, list) else "[]"
    )
    return PiDecision(
        symbol=record["symbol"],
        direction=record["direction"],
        market_type=record["market_type"],
        action=record["action"] or "skip",
        conviction=record.get("conviction") or 0,
        reasoning=record.get("reasoning") or "",
        proposal_id=record.get("proposal_id"),
        proposal_status=record.get("proposal_status"),
        proposal_reason=record.get("proposal_reason"),
        opportunity_id=record.get("opportunity_id"),
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


def claim_decision(decision_result: dict, *, proposal=None) -> tuple[dict, bool]:
    """Atomically persist the final intent and its prepared Proposal, if any.

    The immediate transaction makes the read+insert one SQLite writer claim. A
    concurrent wake observes the inserted row and reuses it.  Persisting the
    prepared Proposal in the same transaction removes the historical crash gap
    where an Opportunity became reviewed while no Proposal existed.
    """
    from aletheia.storage.repositories import (
        find_final_pi_decision_for_opportunity,
        insert_pi_decision,
        insert_trade_proposal,
        transaction,
        transition_opportunity_to_reviewed,
    )

    pi_dec = _build_pi_decision(decision_result)
    with transaction(immediate=True):
        existing = (
            find_final_pi_decision_for_opportunity(
                pi_dec.opportunity_id, pi_dec.signal_source
            )
            if pi_dec.opportunity_id else None
        )
        if existing:
            # A submitting claim may be resumed, but only with the same immutable
            # execution identity. Terminal rows simply win and are returned below.
            if (
                proposal is not None
                and existing.get("proposal_status") == "submitting"
            ):
                _validate_proposal_identity(
                    proposal, existing, prefix="existing_claim_prepared_proposal"
                )
            if pi_dec.signal_source == "market_observer":
                transition_opportunity_to_reviewed(pi_dec.opportunity_id)
            return existing, False
        if proposal is not None:
            _validate_proposal_identity(
                proposal, pi_dec, prefix="prepared_proposal"
            )
            insert_trade_proposal(proposal)
            pi_dec.proposal_id = proposal.id
            pi_dec.proposal_status = "submitting"
            pi_dec.proposal_reason = None
        insert_pi_decision(pi_dec)
        if pi_dec.opportunity_id and pi_dec.signal_source == "market_observer":
            transition_opportunity_to_reviewed(pi_dec.opportunity_id)
    return pi_dec.model_dump(), True


def recover_interrupted_decision(
    existing: dict,
    current_result: dict,
    proposal,
) -> tuple[dict, bool]:
    """Attach one prepared Proposal to a legacy submitting claim exactly once.

    New claims use :func:`claim_decision` and cannot enter this state.  This path
    exists only so rows created by the old claim-before-Proposal implementation
    do not remain a permanent idempotent no-op after an upgrade.
    """
    from aletheia.storage import repositories as repo

    decision_id = existing.get("id")
    if not decision_id:
        raise ValueError("interrupted_decision_missing_id")

    with repo.transaction(immediate=True):
        row = repo._select_one("pi_decisions", decision_id)
        if row is None:
            raise RuntimeError(f"pi_decision claim disappeared: {decision_id}")
        if row.get("proposal_id"):
            return row, False
        if row.get("proposal_status") != "submitting" or row.get("action") not in {
            "trade", "scale_in", "flip",
        }:
            return row, False

        for field in ("symbol", "direction", "market_type", "action", "opportunity_id"):
            if row.get(field) != current_result.get(field):
                raise ValueError(f"interrupted_decision_{field}_mismatch")
        stored_source = row.get("signal_source") or "market_observer"
        current_source = current_result.get("signal_source") or "market_observer"
        if stored_source != current_source:
            raise ValueError("interrupted_decision_signal_source_mismatch")
        # Conviction is durable audit context, not execution identity. A model
        # may rescore the same symbol/direction/instrument after restart; the
        # original row remains authoritative and must not become unrecoverable
        # merely because the retry returned (for example) 89 instead of 90.
        if (
            proposal.opportunity_id != row.get("opportunity_id")
            or proposal.symbol != row.get("symbol")
            or proposal.direction != row.get("direction")
            or proposal.market_type != row.get("market_type")
        ):
            raise ValueError("interrupted_proposal_identity_mismatch")

        repo.insert_trade_proposal(proposal)
        repo.update_pi_decision(
            decision_id,
            proposal_id=proposal.id,
            proposal_status="submitting",
            proposal_reason=None,
        )
        recovered = repo._select_one("pi_decisions", decision_id)
    if recovered is None:
        raise RuntimeError(f"pi_decision recovery unreadable: {decision_id}")
    return recovered, True


def finalize_decision(
    decision_id: str,
    decision_result: dict,
    *,
    publish_filter_evidence: bool = False,
) -> dict:
    """Settle a durable claim and publish its reflection/evidence views."""
    from aletheia.storage import repositories as repo

    # Serialize the terminal compare-and-set. A replay may observe the Proposal
    # terminal before the original caller updates PiDecision; it may settle that
    # submitting row, but it must never rewrite an already-terminal result or any
    # of the original decision identity.
    with repo.transaction(immediate=True):
        row = repo._select_one("pi_decisions", decision_id)
        if row is None:
            raise RuntimeError(f"pi_decision claim disappeared: {decision_id}")
        if row.get("proposal_status") == "submitting":
            proposal_id = row.get("proposal_id") or decision_result.get("proposal_id")
            incoming_id = decision_result.get("proposal_id")
            if row.get("proposal_id") and incoming_id and incoming_id != row.get("proposal_id"):
                raise ValueError("finalized_proposal_id_mismatch")

            proposal_status = decision_result.get("proposal_status")
            if proposal_status == "already_executed" and proposal_id:
                proposal = repo.get_trade_proposal(proposal_id)
                if proposal is not None:
                    proposal_status = proposal.get("status")

            repo.update_pi_decision(
                decision_id,
                proposal_id=proposal_id,
                proposal_status=proposal_status,
                proposal_reason=decision_result.get("proposal_reason"),
            )
        settled = repo._select_one("pi_decisions", decision_id)
    if settled is None:
        raise RuntimeError(f"pi_decision finalization unreadable: {decision_id}")

    if publish_filter_evidence:
        try:
            from aletheia.analytics.filter_evidence import record_operator_filter_trace

            published_result = dict(decision_result)
            for field in (
                "action",
                "symbol",
                "direction",
                "market_type",
                "conviction",
                "reasoning",
                "proposal_id",
                "proposal_status",
                "proposal_reason",
                "opportunity_id",
                "signal_source",
            ):
                published_result[field] = settled.get(field)
            record_operator_filter_trace(decision_id, published_result)
        except Exception as evidence_error:
            logger.warning("operator filter evidence unavailable: %s", evidence_error)
    _write_reflection_record(_reflection_record(settled))
    return settled


def record_decision(decision_result: dict) -> dict | None:
    """Compatibility entry point for callers without proposal side effects.

    The command path uses :func:`claim_decision` before submission and
    :func:`finalize_decision` afterwards. Direct reflection callers keep their
    historical best-effort behavior.
    """
    try:
        claimed, created = claim_decision(decision_result)
        if not created:
            return claimed
        return finalize_decision(
            claimed["id"], decision_result, publish_filter_evidence=True
        )
    except Exception as exc:
        logger.debug("pi_decision db persist failed: %s", exc)
        return None


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
