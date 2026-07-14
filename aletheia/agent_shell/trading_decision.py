"""System Operator trading decision engine.

Provides evaluate_trading_opportunity() — the System Operator's ability to act as
Trading Decision Authority. Given a rich context snapshot, it uses structured
LLM output to decide whether to trade, then optionally runs a Bull/Bear debate
when conviction is uncertain, and finally computes a conviction-based position size.

This is intentionally a THIN layer over existing infrastructure:
  - Context comes from agent_shell/context.py (existing)
  - Proposals go through create_paper_proposal (existing, goes through Risk Engine)
  - Sizing respects existing notional caps from config/risk engine
  - No new DB tables; decisions are recorded as agent_shell audit events

Safety: System Operator can only submit paper proposals (PaperAuto permission gate).
Live trading is never reachable from this path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("aletheia.agent_shell.trading_decision")

# Max chars of the human-readable thesis stored in agent_rationale. The rationale
# is "<thesis>\n---\n<decision_meta JSON>"; ONLY the thesis is length-capped here.
# The JSON half must NEVER be truncated — a sliced JSON is unparseable and both
# the Proposals drawer (parseDecisionMeta) and analytics/reasoning_feedback.py
# silently lose all structured fields. The historical bug applied a second
# `[:500]` cap to the *combined* string, guillotining the ~1.1KB JSON mid-token.
# trade_proposals.agent_rationale is TEXT (no DB limit), so a generous thesis cap
# plus a whole JSON blob is safe. 600 fits every observed thesis (~60-400 chars).
_MAX_RATIONALE_TEXT = 600


class _DummySchema:
    """Shim so llm_structured() can call .model_json_schema() on a plain dict schema."""
    def __init__(self, schema: dict) -> None:
        self._schema = schema

    def model_json_schema(self) -> dict:
        return self._schema


def _json_schema_hint(schema: dict) -> str:
    return json.dumps(schema)

_MIN_CONVICTION_TO_TRADE = 55       # below this, skip (even after debate)
_DEBATE_RANGE = (40, 80)            # conviction in this range triggers debate
_MAX_CONVICTION_MULTIPLIER = 2.0    # cap: 2× base notional at 100% conviction
_MIN_CONVICTION_MULTIPLIER = 0.5    # floor: 0.5× base notional at low conviction
_BIAS_BRAKE_WINDOW_HOURS = 24
_BIAS_BRAKE_MIN_PROPOSALS = 20
_BIAS_BRAKE_MAX_DIRECTION_PCT = 0.90
_BIAS_BRAKE_BYPASS_CONVICTION = 85


class DecisionUnavailable(RuntimeError):
    """A transient Operator input/preflight dependency is not authoritative yet.

    Callers must not turn this into a durable skip or trade claim.  The pending
    queue identity remains available so the daemon can retry after its cooldown.
    """

    def __init__(self, reason: str, *, stage: str = "operator_evaluation") -> None:
        self.reason = reason
        self.stage = stage
        super().__init__(reason)


# ── bias-brake deadlock breaker ────────────────────────────────────────
_BREAKER_STREAK_THRESHOLD = 15   # consecutive same-dir brake skips before auto-bypass
_STREAK_LOOKBACK_HOURS = 6       # how far back to count the streak


def _brake_streak(direction):
    """Count consecutive brake-rejected skip decisions for `direction`."""
    if direction not in ("long", "short"):
        return 0
    try:
        from aletheia.storage.sqlite import get_db
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_STREAK_LOOKBACK_HOURS)).isoformat()
        rows = get_db().execute(
            "SELECT direction, reasoning FROM pi_decisions "
            "WHERE action='skip' AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 200",
            (cutoff,),
        ).fetchall()
    except Exception:
        return 0
    streak = 0
    for r in rows:
        if r["direction"] != direction:
            continue
        reasoning = r["reasoning"] or ""
        if "directional proposal brake" in reasoning or ("brake" in reasoning.lower() and "direction" in reasoning.lower()):
            streak += 1
        else:
            break
    return streak



@dataclass
class TradingDecision:
    action: str              # active model surface: "trade" | "skip"
    symbol: str | None
    direction: str | None    # "long" | "short"
    conviction: int          # 0-100
    reasoning: str
    risk_factors: list[str] = field(default_factory=list)
    supporting_signals: list[str] = field(default_factory=list)
    opposing_signals: list[str] = field(default_factory=list)
    position_size_usdt: float | None = None
    stop_price: float | None = None
    take_profit: float | None = None
    # Exit-intent plan M2: the Operator's intended holding horizon (hours). Drives the
    # per-position time-stop — hitting it wakes the Operator for a must-answer decision
    # rather than auto-closing; auto-close only at the far backstop (see position_manager
    # + risk.horizon_hard_backstop_mult). None → legacy school max_hold applies.
    expected_horizon_hours: float | None = None
    # Exit-intent plan M3.5 (D10): for a proactive `close`, the Operator chooses the
    # order type — "market" (exit now) or "limit" (rest an exit at limit_price for a
    # better fill). None → market. Only meaningful on close actions.
    order_type: str | None = None
    limit_price: float | None = None
    # Futures: which venue + leverage the PI chose. market_type defaults to
    # usdt_futures (the demo/perp venue) when the LLM omits it; leverage is the
    # PI's requested level, clamped to the per-tier cap at submit time.
    market_type: str | None = None
    leverage: int | None = None
    debate_ran: bool = False
    debate_initial_conviction: int | None = None
    debate_bull_confidence: int | None = None
    debate_bear_confidence: int | None = None
    debate_adjustment: int | None = None
    # R0 attribution: backfilled from the source opportunity by
    # _attach_opportunity_context so the proposal carries a real opportunity_id /
    # strategy_id and the decision_meta records scanner_family (LEARNING_LOOP_AUDIT
    # §R0 + BACKEND_ISSUES §1.1 causal-chain strategy_id).
    opportunity_id: str | None = None
    learning_candidate_key: str | None = None
    strategy_id: str | None = None
    scanner_family: str | None = None
    # Phase 2 (Serenity Option A): which table opportunity_id resolves against.
    # 'market_observer' → opportunities.id; 'serenity' → serenity_opportunities.id.
    # Stamped by _attach_opportunity_context (market_observer takes precedence).
    signal_source: str = "market_observer"
    # S4 / P2 (2026-06-28, AI Berkshire forced-verdict): explicit counter-argument.
    # System Operator must fill bear_case even when recommending a trade — forces acknowledgment
    # of what could go wrong. Empty string = not provided (legacy / pre-S4 decisions).
    bear_case: str = ""
    # P1 (2026-06-28, AI Berkshire belief decomposition): the critical assumptions
    # this decision depends on. At most 3 items, kept concise. Empty = not provided.
    key_assumptions: list[str] = field(default_factory=list)
    # Append-only observation facts for the exact gates evaluated in this call.
    # Persisted only after the authoritative PiDecision row is written.
    filter_trace: list[dict] = field(default_factory=list)
    sizing_trace: dict = field(default_factory=dict)
    # One concise AI reason for each value that expresses an entry.  The final
    # numbers are still resolved by submit_decision_as_proposal from the market
    # snapshot, configured defaults, and venue/risk caps; this only explains why
    # the AI considers those five choices appropriate.  Empty/missing values are
    # retained as explicitly absent evidence, never filled in after the fact.
    parameter_rationale: dict[str, str] = field(default_factory=dict)
    # Set only for decisions parsed from the System Operator's LLM response.
    # Programmatic/historical callers retain compatibility, while an AI trade
    # cannot silently bypass the explanation requirement.
    parameter_rationale_required: bool = False


@dataclass
class DebateResult:
    bull_confidence: int
    bull_thesis: str
    bull_evidence: list[str]
    bear_confidence: int
    bear_thesis: str
    bear_evidence: list[str]


# ── schemas for structured LLM output ────────────────────────────────────────

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["trade", "skip", "close", "reduce", "scale_in", "flip", "adjust_position"]},
        "symbol": {"type": "string"},
        "direction": {"type": "string", "enum": ["long", "short"]},
        "conviction": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasoning": {"type": "string"},
        # Forced counter-argument: even a trade recommendation must state the
        # strongest concrete failure case instead of presenting one-sided confidence.
        "bear_case": {"type": "string", "minLength": 1},
        # market_type: which venue expresses the view. "usdt_futures" (perp) supports
        # leverage and pays/earns funding; "spot" has no leverage and no funding cost.
        "market_type": {"type": "string", "enum": ["spot", "usdt_futures"]},
        # leverage: integer ≥1, only meaningful for usdt_futures. The risk engine
        # clamps this to the per-asset-tier cap, so a high request is safely reduced.
        "leverage": {"type": "integer", "minimum": 1, "maximum": 20},
        # Entry discipline (owner discussion #6): the operator may commit an explicit
        # stop and take-profit price at decision time. When omitted, the proposal
        # builder derives them from config defaults so the four-element entry
        # (entry/stop/tp/reward:risk) is always complete before the limit order.
        "stop_price": {"type": "number"},
        "take_profit": {"type": "number"},
        # M2: Operator's intended holding horizon in hours (drives the time-stop /
        # must-answer horizon wake). Optional — omitted → legacy school max_hold.
        "expected_horizon_hours": {"type": "number"},
        # M3.5 (D10): proactive-close order type + optional resting limit price.
        "order_type": {"type": "string", "enum": ["market", "limit"]},
        "limit_price": {"type": "number"},
        # Required for trade-like actions by the prompt below.  It remains
        # optional in the top-level schema because skip/close/reduce decisions
        # do not have five new-entry parameters to explain.
        # Required for trade by the prompt below. It remains optional in the
        # top-level schema because skip has no entry parameters to explain.
        "parameter_rationale": {
            "type": "object",
            "properties": {
                "entry_price": {"type": "string"},
                "stop_loss": {"type": "string"},
                "take_profit": {"type": "string"},
                "notional_usdt": {"type": "string"},
                "leverage": {"type": "string"},
            },
        },
        "risk_factors": {"type": "array", "items": {"type": "string"}},
        "supporting_signals": {"type": "array", "items": {"type": "string"}},
        "opposing_signals": {"type": "array", "items": {"type": "string"}},
        # P1: structured assumption list — at most 3, kept concise
        "key_assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "conviction", "reasoning", "bear_case"],
}

_BULL_SCHEMA = {
    "type": "object",
    "properties": {
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "thesis": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["confidence", "thesis", "evidence"],
}

_BEAR_SCHEMA = {
    "type": "object",
    "properties": {
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "thesis": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["confidence", "thesis", "evidence"],
}


# ── main entry point ──────────────────────────────────────────────────────────

async def evaluate_trading_opportunity(trading_context: dict) -> TradingDecision:
    """System Operator trading decision loop.

    Steps:
    1. Assess opportunities using structured LLM with full context
    2. If conviction in uncertain range (40-80), run Bull/Bear debate
    3. Compute conviction-based position size
    4. Return TradingDecision (caller submits a proposal only for action="trade")
    """
    decision_raw = await _assess_opportunity(trading_context)
    if decision_raw is None:
        raise DecisionUnavailable("llm_assessment_unavailable")

    decision = _parse_decision(decision_raw)
    # R0: match the pick back to its source opportunity (id + scanner_family)
    # before any return path, so skip records carry attribution.
    _attach_opportunity_context(decision, trading_context)

    trade_actions = ("trade",)
    trade_intent = decision.action in trade_actions

    bias_brake_reason = _proposal_bias_brake_reason(decision)
    if bias_brake_reason:
        # Deadlock breaker: after N consecutive same-direction brake
        # skips, auto-bypass the brake so a trending market can still
        # produce trades instead of endlessly skipping.
        streak = _brake_streak(decision.direction) if decision.direction else 0
        if streak >= _BREAKER_STREAK_THRESHOLD:
            decision.filter_trace.append({
                "filter_name": "direction_bias_brake",
                "stage": "system_operator",
                "decision": "bypassed",
                "reason_code": "deadlock_breaker",
                "inputs": {"streak": streak, "threshold": _BREAKER_STREAK_THRESHOLD},
            })
        else:
            decision.filter_trace.append({
                "filter_name": "direction_bias_brake",
                "stage": "system_operator",
                "decision": "rejected",
                "reason_code": "proposal_direction_concentration",
            "inputs": {"conviction": decision.conviction, "direction": decision.direction},
            "parameters": {
                "window_hours": _BIAS_BRAKE_WINDOW_HOURS,
                "min_proposals": _BIAS_BRAKE_MIN_PROPOSALS,
                "max_direction_pct": _BIAS_BRAKE_MAX_DIRECTION_PCT,
                "bypass_conviction": _BIAS_BRAKE_BYPASS_CONVICTION,
            },
        })
        decision.action = "skip"
        decision.reasoning = f"{decision.reasoning} [{bias_brake_reason}]"
        return decision
    if trade_intent:
        decision.filter_trace.append({
            "filter_name": "direction_bias_brake",
            "stage": "system_operator",
            "decision": "passed",
            "reason_code": "proposal_direction_not_concentrated",
            "inputs": {"conviction": decision.conviction, "direction": decision.direction},
            "parameters": {
                "window_hours": _BIAS_BRAKE_WINDOW_HOURS,
                "min_proposals": _BIAS_BRAKE_MIN_PROPOSALS,
                "max_direction_pct": _BIAS_BRAKE_MAX_DIRECTION_PCT,
                "bypass_conviction": _BIAS_BRAKE_BYPASS_CONVICTION,
            },
        })

    # Run debate for uncertain conviction range
    if (decision.action in trade_actions
            and decision.symbol
            and _DEBATE_RANGE[0] <= decision.conviction <= _DEBATE_RANGE[1]):
        initial_conviction = decision.conviction
        debate = await _run_debate(decision.symbol, decision.direction, trading_context)
        if debate is None:
            raise DecisionUnavailable(
                "required_debate_unavailable", stage="operator_debate"
            )
        decision.debate_ran = True
        decision.debate_initial_conviction = initial_conviction
        decision.debate_bull_confidence = debate.bull_confidence
        decision.debate_bear_confidence = debate.bear_confidence
        # Debate adjusts the initial conviction; it does not replace it. Bull
        # confidence is success probability for the proposed direction and bear
        # confidence is its failure probability, so the difference is correctly
        # signed for either long or short.
        advantage = debate.bull_confidence - debate.bear_confidence
        adjusted = int(round(initial_conviction + advantage * 0.3))
        decision.conviction = max(0, min(100, adjusted))
        decision.debate_adjustment = decision.conviction - initial_conviction
        if decision.conviction < _MIN_CONVICTION_TO_TRADE:
            decision.action = "skip"
            decision.reasoning += (
                f" [debate adjusted conviction {initial_conviction}→{decision.conviction} "
                f"(bull {debate.bull_confidence} vs bear {debate.bear_confidence}, "
                f"dir={decision.direction}) → skip]"
            )
        decision.filter_trace.append({
            "filter_name": "debate_downgrade",
            "stage": "system_operator_debate",
            "decision": "rejected" if decision.action == "skip" else "passed",
            "reason_code": (
                "debate_below_min_conviction"
                if decision.action == "skip"
                else "debate_at_or_above_min_conviction"
            ),
            "inputs": {
                "initial_conviction": initial_conviction,
                "final_conviction": decision.conviction,
                "bull_confidence": debate.bull_confidence,
                "bear_confidence": debate.bear_confidence,
            },
            "parameters": {
                "debate_range": list(_DEBATE_RANGE),
                "minimum_conviction": _MIN_CONVICTION_TO_TRADE,
                "adjustment_weight": 0.3,
            },
        })

    # Compute sizing for the single executable entry action.
    if decision.action in trade_actions and decision.conviction >= _MIN_CONVICTION_TO_TRADE:
        decision.filter_trace.append({
            "filter_name": "operator_conviction_gate",
            "stage": "system_operator",
            "decision": "passed",
            "reason_code": "conviction_at_or_above_minimum",
            "inputs": {"conviction": decision.conviction},
            "parameters": {"minimum_conviction": _MIN_CONVICTION_TO_TRADE},
        })
        ptc = _get_strategy_paper_trade_count(decision.strategy_id)
        decision.position_size_usdt = _compute_position_size(
            decision.conviction,
            symbol=decision.symbol,
            direction=decision.direction,
            paper_trade_count=ptc,
            opportunity_id=decision.opportunity_id,
            learning_candidate_key=decision.learning_candidate_key,
            sizing_trace=decision.sizing_trace,
        )
    elif trade_intent:
        decision.filter_trace.append({
            "filter_name": "operator_conviction_gate",
            "stage": "system_operator",
            "decision": "rejected",
            "reason_code": "conviction_below_minimum",
            "inputs": {"conviction": decision.conviction},
            "parameters": {"minimum_conviction": _MIN_CONVICTION_TO_TRADE},
        })
        decision.action = "skip"

    return decision


def _latest_exact_entry_snapshot(
    symbol: str,
    market_type: str,
    venue: str,
) -> dict | None:
    """Return metadata for the exact venue/instrument used by local entry pricing.

    Freshness authority remains in ``get_fresh_price_for_protective_close``. This
    helper only preserves the exact snapshot id/timestamp in Proposal rationale and
    distinguishes an old exact observation from no exact observation at all.
    """
    if not venue:
        return None
    from aletheia.data.symbols import to_bitget, to_canonical
    from aletheia.storage.repositories import list_market_snapshots

    canonical = to_canonical(symbol)
    symbols = [canonical]
    legacy = to_bitget(canonical)
    if legacy != canonical:
        symbols.append(legacy)
    physical_market = "perp" if market_type == "usdt_futures" else market_type
    markets = [physical_market]
    if market_type == "usdt_futures":
        markets.append("usdt_futures")
    rows: list[dict] = []
    for stored_symbol in symbols:
        for stored_market in markets:
            rows.extend(list_market_snapshots(
                symbol=stored_symbol,
                exchange=venue,
                market_type=stored_market,
                limit=1,
            ))
    return (
        max(rows, key=lambda row: str(row.get("snapshot_at") or ""))
        if rows else None
    )


async def prepare_decision_as_proposal(decision: TradingDecision, execution_mode: str) -> dict:
    """Resolve one trade intent into an immutable Proposal without persisting it.

    The caller can commit this Proposal in the same short transaction as the
    authoritative PiDecision claim.  No Risk or broker side effect happens here.
    """
    identity_failure = None
    if decision.action != "trade":
        identity_failure = "invalid_action"
    elif not decision.symbol:
        identity_failure = "missing_symbol"
    elif not decision.direction:
        identity_failure = "missing_direction"
    if identity_failure:
        decision.filter_trace.append({
            "filter_name": "proposal_identity",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": identity_failure,
            "inputs": {"action": decision.action},
            "parameters": {
                "required_fields": ["symbol", "direction"],
                "trade_actions": ["trade"],
            },
        })
        raise DecisionUnavailable(identity_failure, stage="proposal_preflight")

    missing_parameter_reasons = [
        key for key in _ENTRY_PARAMETER_KEYS
        if not isinstance(decision.parameter_rationale.get(key), str)
        or not decision.parameter_rationale[key].strip()
    ]
    if decision.parameter_rationale_required and missing_parameter_reasons:
        decision.filter_trace.append({
            "filter_name": "parameter_rationale_gate",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": "missing_parameter_rationale",
            "inputs": {"action": decision.action},
            "parameters": {"required_fields": list(_ENTRY_PARAMETER_KEYS)},
            "missing": missing_parameter_reasons,
        })
        logger.info(
            "proposal skipped: LLM decision omitted parameter reasons (%s)",
            ", ".join(missing_parameter_reasons),
        )
        raise DecisionUnavailable(
            "missing_parameter_rationale", stage="proposal_preflight"
        )

    overheated = _proposal_direction_overheated(decision.direction, decision.conviction)
    if overheated:
        decision.filter_trace.append({
            "filter_name": "direction_bias_brake",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": "direction_overheated",
            "inputs": {"direction": decision.direction, "conviction": decision.conviction},
            "parameters": {"min_sample": 15, "max_share": 0.95, "override_conviction": 75},
        })
        logger.info("proposal skipped by direction diversity brake: %s", overheated)
        return {"status": "skipped", "reason": "direction_overheated", "detail": overheated}
    decision.filter_trace.append({
        "filter_name": "direction_bias_brake",
        "stage": "proposal_submission",
        "decision": "passed",
        "reason_code": "direction_not_overheated",
        "inputs": {"direction": decision.direction, "conviction": decision.conviction},
        "parameters": {"min_sample": 15, "max_share": 0.95, "override_conviction": 75},
    })

    from aletheia.trading.market_data import latest_snapshot
    from aletheia.config.loader import load_config
    from aletheia.trust.guard_chain import run_guards, summarize

    cfg = load_config()

    # Resolve the instrument before looking up price. Spot and perp rows share the
    # canonical symbol but have different prices and freshness histories.
    market_type = decision.market_type
    if not market_type:
        market_type = "usdt_futures" if decision.direction == "short" else "spot"
    elif market_type == "spot" and decision.direction == "short":
        # Spot cannot express a native short; the risk engine retains the hard
        # backstop, while this keeps the proposal on the intended perp instrument.
        market_type = "usdt_futures"

    snap: dict | None
    if execution_mode == "paper_internal":
        from aletheia.data.price_feed import (
            get_fresh_price_for_protective_close,
            protective_price_venue,
        )

        venue = protective_price_venue(
            execution_mode,
            configured_data_venue=cfg.market_data.ccxt_exchange,
        )
        price = float(
            get_fresh_price_for_protective_close(
                decision.symbol,
                market_type,
                venue=venue or "",
            )
            or 0
        )
        snap = _latest_exact_entry_snapshot(
            decision.symbol, market_type, venue or ""
        )
        if snap is None:
            snap = {
                "symbol": decision.symbol,
                "market_type": (
                    "perp" if market_type == "usdt_futures" else market_type
                ),
                "exchange": venue,
            }
        snap = dict(snap)
        snap["price"] = price or snap.get("price")
        snap["price_authority"] = "fresh_exact_market_mark"
    else:
        snap = latest_snapshot(decision.symbol, market_type=market_type)
        price = float((snap or {}).get("price") or 0)
    if not price:
        reason = (
            "market_price_stale"
            if execution_mode == "paper_internal" and snap.get("id")
            else "market_price_unavailable"
        )
        decision.filter_trace.append({
            "filter_name": "proposal_price_availability",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": reason,
            "inputs": {
                "symbol": decision.symbol,
                "market_type": market_type,
                "execution_mode": execution_mode,
                "venue": (snap or {}).get("exchange"),
            },
            "parameters": {
                "minimum_price": 0,
                "memory_max_age_seconds": 90,
                "database_max_age_seconds": 120,
            },
        })
        logger.warning("%s: %s", reason, decision.symbol)
        raise DecisionUnavailable(reason, stage="proposal_preflight")
    decision.filter_trace.append({
        "filter_name": "proposal_price_availability",
        "stage": "proposal_submission",
        "decision": "passed",
        "reason_code": "market_price_available",
        "inputs": {"symbol": decision.symbol, "market_type": market_type},
        "parameters": {
            "minimum_price": 0,
            "exact_instrument_required": execution_mode == "paper_internal",
        },
    })

    # Guard chain is a PRE-RISK OBSERVABILITY layer (see trust/guard_chain docstring):
    # advisory only. It enriches the audit trail — guard_summary is attached to the
    # proposal rationale below (decision_meta["guards"]) — but MUST NOT hard-block the
    # PI's decision. The formal Risk Engine downstream (create_paper_proposal → risk
    # evaluation) is the real safety gate. Previously a guard "block" verdict silently
    # `return None`, which (a) contradicted guard_chain's own observability design and
    # (b) dropped the PI's decision with no detail surfaced to the caller (E1 fix).
    # Now: log the advisory, keep the verdict in the rationale, and let the proposal
    # flow to the Risk Engine, which decides.
    guard_verdicts = run_guards({
        "symbol": decision.symbol,
        "direction": decision.direction,
        "conviction": decision.conviction,
        "strategy_id": None,
    })
    guard_summary = summarize(guard_verdicts)
    if guard_summary["blocks"]:
        logger.info("guard_chain advisory (not blocking — Risk Engine is the gate): %s — %s",
                    decision.symbol, guard_summary["details"])

    notional = decision.position_size_usdt or 25.0
    quantity = round(notional / price, 6) or 0.000001

    # Phase 4 execution-school discriminator. Derived from the DECISION-level
    # signal_source (Phase 2): a 'serenity'-sourced decision opens a 'serenity'
    # position with the wider long-horizon stop. The risk engine stays school-
    # agnostic — only position_manager branches on it.
    trade_school = "serenity" if getattr(decision, "signal_source", None) == "serenity" else "short_term"

    stop_price = decision.stop_price
    if stop_price is None:
        stop_pct = (
            cfg.serenity.stop_loss_pct
            if trade_school == "serenity"
            else cfg.trading.default_stop_loss_pct
        )
        if decision.direction == "long":
            stop_price = round(price * (1 - stop_pct / 100.0), 8)
        else:
            stop_price = round(price * (1 + stop_pct / 100.0), 8)

    # Commit take-profit + reward:risk before execution. take_profit is the
    # operator's explicit price, else the configured default
    # anchored to the entry (price). risk_reward = reward-per-unit / risk-per-unit
    # from the committed entry/stop/tp. The risk engine rejects every native local
    # paper entry (and native exchange limit entry) missing any of the four.
    take_profit = decision.take_profit
    if take_profit is None:
        tp_pct = cfg.trading.default_take_profit_pct
        if decision.direction == "long":
            take_profit = round(price * (1 + tp_pct / 100.0), 8)
        else:
            take_profit = round(price * (1 - tp_pct / 100.0), 8)
    _risk_per_unit = abs(price - stop_price)
    _reward_per_unit = abs(take_profit - price)
    risk_reward = round(_reward_per_unit / _risk_per_unit, 2) if _risk_per_unit > 0 else None

    # Leverage only applies to futures. Clamp the PI's request to the per-asset-tier
    # cap (the risk engine enforces the same cap as a hard reject — clamping here keeps
    # the proposal inside the gate so a sane futures trade is not silently rejected for
    # an over-eager leverage number). Spot carries no leverage.
    leverage: int | None = None
    leverage_cap: int | None = None
    if market_type == "usdt_futures" and decision.leverage and decision.leverage > 1:
        from aletheia.trading.instruments import get_max_leverage
        is_live = "live" in (execution_mode or "")
        leverage_cap = get_max_leverage(decision.symbol, is_live, cfg)
        leverage = max(1, min(decision.leverage, leverage_cap))

    entry_parameters = _build_entry_parameter_snapshot(
        decision=decision,
        market_type=market_type,
        entry_price=price,
        quantity=quantity,
        stop_price=stop_price,
        take_profit=take_profit,
        requested_notional=notional,
        leverage=leverage,
        leverage_cap=leverage_cap,
        market_snapshot=snap,
    )

    decision_meta = {
        "conviction": decision.conviction,
        "debate_ran": decision.debate_ran,
        "debate_initial_conviction": decision.debate_initial_conviction,
        "debate_bull": decision.debate_bull_confidence,
        "debate_bear": decision.debate_bear_confidence,
        "debate_adjustment": decision.debate_adjustment,
        "supporting_signals": decision.supporting_signals[:5],
        "opposing_signals": decision.opposing_signals[:5],
        "bear_case": decision.bear_case,
        "key_assumptions": decision.key_assumptions[:3],
        # Legacy keys kept for existing dashboard/feedback readers.
        "supporting": decision.supporting_signals[:5],
        "opposing": decision.opposing_signals[:5],
        # R0: scanner_family is parsed back out of this JSON by
        # analytics/reasoning_feedback.py and fed to the PI prompt. Hard-coded
        # absence here is why reasoning_feedback scanner was permanently unknown.
        "scanner_family": decision.scanner_family,
        "sizing_learning": decision.sizing_trace,
        "guards": guard_summary,
        # Immutable proposal evidence for why each entry value was used.  The
        # proposal hash covers agent_rationale, so this snapshot cannot be
        # silently changed without breaking the Trading-as-Git chain.
        "entry_parameters": entry_parameters,
    }
    # Cap ONLY the human thesis; append the full JSON AFTER capping so the JSON
    # is always whole. Do NOT re-truncate the combined string (the old `[:500]`
    # bug sliced the JSON into unparseable garbage — see _MAX_RATIONALE_TEXT).
    thesis = decision.reasoning or ""
    if len(thesis) > _MAX_RATIONALE_TEXT:
        thesis = thesis[:_MAX_RATIONALE_TEXT].rstrip() + "…"
    rationale = f"{thesis}\n---\n{json.dumps(decision_meta)}"

    from aletheia.storage.models import TradeProposal
    from aletheia.trading.entry_snapshot import capture_attribution_snapshot_json

    proposal = TradeProposal(
        symbol=decision.symbol,
        market_type=market_type,
        direction=decision.direction,
        order_type="market" if execution_mode == "paper_internal" else "limit",
        quantity=quantity,
        price=price,
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        risk_reward=risk_reward,
        execution_mode=execution_mode,
        strategy_id=decision.strategy_id,
        opportunity_id=decision.opportunity_id,
        agent_rationale=rationale,
        attribution_snapshot=capture_attribution_snapshot_json(decision.symbol),
        trade_school=trade_school,
        # Exit-intent plan M2: carry the Operator's holding horizon and stop
        # provenance to the position at open. stop_source='operator' iff the AI
        # authored the stop (else config default filled it in above).
        expected_horizon_hours=decision.expected_horizon_hours,
        stop_source="operator" if decision.stop_price is not None else "default",
    )
    return {"status": "prepared", "proposal_id": proposal.id, "proposal": proposal}


async def submit_decision_as_proposal(decision: TradingDecision, execution_mode: str) -> dict:
    """Compatibility entry point that prepares, persists, and executes one Proposal."""
    prepared = await prepare_decision_as_proposal(decision, execution_mode)
    proposal = prepared.get("proposal")
    if proposal is None:
        return prepared

    from aletheia.execution.paper_tools import create_paper_proposal

    return create_paper_proposal(
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        leverage=proposal.leverage,
        stop_price=proposal.stop_price,
        take_profit=proposal.take_profit,
        risk_reward=proposal.risk_reward,
        execution_mode=proposal.execution_mode,
        strategy_id=proposal.strategy_id,
        opportunity_id=proposal.opportunity_id,
        agent_rationale=proposal.agent_rationale,
        trade_school=proposal.trade_school,
    )


_ENTRY_PARAMETER_KEYS = (
    "entry_price",
    "stop_loss",
    "take_profit",
    "notional_usdt",
    "leverage",
)


def _build_entry_parameter_snapshot(
    *,
    decision: TradingDecision,
    market_type: str,
    entry_price: float,
    quantity: float,
    stop_price: float,
    take_profit: float,
    requested_notional: float,
    leverage: int | None,
    leverage_cap: int | None,
    market_snapshot: dict | None = None,
) -> dict[str, dict]:
    """Build the immutable, honest explanation of the final entry values.

    AI provides only short reasons.  This function owns the numerical facts and
    their source labels after defaults/caps are applied, so a dashboard cannot
    mistake a configured fallback or a risk-cap clamp for an AI-selected value.
    """
    supplied = decision.parameter_rationale if isinstance(decision.parameter_rationale, dict) else {}
    snapshot = market_snapshot or {}

    def reason_for(key: str) -> str | None:
        value = supplied.get(key)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value[:600] if value else None

    actual_notional = round(quantity * entry_price, 8)
    estimated_loss = round(quantity * abs(entry_price - stop_price), 8)
    requested_leverage = decision.leverage if market_type == "usdt_futures" else None
    final_leverage = leverage or 1
    if market_type != "usdt_futures":
        leverage_source = "not_applicable_spot"
    elif requested_leverage and leverage_cap and final_leverage != requested_leverage:
        leverage_source = "risk_cap_clamped"
    elif requested_leverage and requested_leverage > 1:
        leverage_source = "ai_requested"
    else:
        leverage_source = "default_1x"

    return {
        "entry_price": {
            "final_value": entry_price,
            "value_source": snapshot.get("price_authority") or "market_snapshot",
            "market_snapshot_id": snapshot.get("id"),
            "market_type": snapshot.get("market_type"),
            "snapshot_at": snapshot.get("snapshot_at"),
            "reason": reason_for("entry_price"),
        },
        "stop_loss": {
            "final_value": stop_price,
            "value_source": "ai_requested" if decision.stop_price is not None else "default_config",
            "distance_pct": round(abs(entry_price - stop_price) / entry_price * 100, 6) if entry_price else None,
            "reason": reason_for("stop_loss"),
        },
        "take_profit": {
            "final_value": take_profit,
            "value_source": "ai_requested" if decision.take_profit is not None else "default_config",
            "distance_pct": round(abs(take_profit - entry_price) / entry_price * 100, 6) if entry_price else None,
            "reason": reason_for("take_profit"),
        },
        "notional_usdt": {
            "requested_value": requested_notional,
            "actual_value": actual_notional,
            "estimated_loss_at_stop_usdt": estimated_loss,
            "value_source": "ai_requested" if decision.position_size_usdt is not None else "default_config",
            "reason": reason_for("notional_usdt"),
        },
        "leverage": {
            "requested_value": requested_leverage,
            "final_value": final_leverage,
            "maximum_allowed": leverage_cap,
            "estimated_margin_usdt": round(actual_notional / final_leverage, 8) if final_leverage else None,
            "value_source": leverage_source,
            "reason": reason_for("leverage"),
        },
    }


def _proposal_direction_overheated(
    direction: str | None,
    conviction: int,
    *,
    window_hours: int = 24,
    min_sample: int = 15,
    max_share: float = 0.95,
    override_conviction: int = 75,
) -> str | None:
    """Return a skip reason when native proposals are one-sided.

    This is a pre-proposal quality brake, not a risk/execution gate. It prevents
    normal-conviction decisions from adding yet another same-direction proposal
    when the recent proposal stream is already overwhelmingly one-sided.
    """
    if direction not in ("long", "short") or conviction >= override_conviction:
        return None
    try:
        from datetime import datetime, timedelta, timezone
        from collections import Counter
        from aletheia.storage.sqlite import get_db

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        rows = get_db().execute(
            "SELECT direction FROM trade_proposals "
            "WHERE created_at >= ? AND source != 'adopted' AND direction IS NOT NULL",
            (cutoff,),
        ).fetchall()
        dirs = [r["direction"] for r in rows if r["direction"] in ("long", "short")]
        total = len(dirs)
        if total < min_sample:
            return None
        counts = Counter(dirs)
        top_dir, top_n = counts.most_common(1)[0]
        share = top_n / total if total else 0.0
        if top_dir == direction and share > max_share:
            return (
                f"{window_hours}h native proposals are {top_n}/{total} {top_dir} "
                f"({share:.0%}); conviction {conviction} below override {override_conviction}"
            )
    except Exception:
        return None
    return None


# ── LLM calls ─────────────────────────────────────────────────────────────────

async def _assess_opportunity(ctx: dict) -> dict | None:
    """Call LLM with full trading context; return raw decision dict."""
    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.router import get_provider_for_tier

    system = (
        "You are the Trading Decision Authority for an autonomous paper trading system. "
        "You receive a structured three-layer context: (1) current environment snapshot, "
        "(2) your recent 7-day performance, (3) accumulated wisdom from past trades. "
        "The risk/guard system operates independently after your decision — "
        "focus on WHAT to trade, not HOW MUCH. Return a JSON object matching the schema."
    )
    user = _build_assessment_prompt(ctx)
    schema_text = _json_schema_hint(_DECISION_SCHEMA)
    full_user = f"{user}\n\nReturn JSON: {schema_text}"
    result, err = await llm_structured(system, full_user, schema=_DummySchema(_DECISION_SCHEMA),
                                       provider=get_provider_for_tier("deep"))
    if err or result is None:
        logger.warning("LLM assessment failed: %s", err)
        raise DecisionUnavailable("llm_assessment_unavailable")
    return result


async def _run_debate(symbol: str, direction: str | None, ctx: dict) -> DebateResult | None:
    """Run Bull/Bear adversarial debate for a symbol."""
    import asyncio
    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.router import get_provider_for_tier

    bull_user = _build_bull_prompt(symbol, direction, ctx)
    bear_user = _build_bear_prompt(symbol, direction, ctx)
    bull_schema_hint = _json_schema_hint(_BULL_SCHEMA)
    bear_schema_hint = _json_schema_hint(_BEAR_SCHEMA)

    system_bull = "You are a bull analyst. Return a JSON object matching the schema."
    system_bear = "You are a bear analyst. Return a JSON object matching the schema."

    _deep_provider = get_provider_for_tier("deep")
    (bull_result, bull_err), (bear_result, bear_err) = await asyncio.gather(
        llm_structured(system_bull, f"{bull_user}\n\nReturn JSON: {bull_schema_hint}",
                       schema=_DummySchema(_BULL_SCHEMA), provider=_deep_provider),
        llm_structured(system_bear, f"{bear_user}\n\nReturn JSON: {bear_schema_hint}",
                       schema=_DummySchema(_BEAR_SCHEMA), provider=_deep_provider),
    )
    if bull_err or bear_err or not bull_result or not bear_result:
        logger.debug("debate failed bull_err=%s bear_err=%s", bull_err, bear_err)
        raise DecisionUnavailable(
            "required_debate_unavailable", stage="operator_debate"
        )

    bull_conf = int(bull_result.get("confidence", 50))
    bear_conf = int(bear_result.get("confidence", 50))

    # NOTE: the bull/bear advantage is applied in evaluate_trading_opportunity
    # (initial_conviction + advantage*0.3); DebateResult intentionally carries the
    # raw confidences, not a pre-synthesized verdict. (Removed the dead
    # final_conviction field that was computed here but never read.)
    return DebateResult(
        bull_confidence=bull_conf,
        bull_thesis=bull_result.get("thesis", ""),
        bull_evidence=bull_result.get("evidence", []),
        bear_confidence=bear_conf,
        bear_thesis=bear_result.get("thesis", ""),
        bear_evidence=bear_result.get("evidence", []),
    )


# ── sizing ────────────────────────────────────────────────────────────────────

def _compute_position_size(
    conviction: int,
    symbol: str | None = None,
    direction: str | None = None,
    paper_trade_count: int = 0,
    opportunity_id: str | None = None,
    learning_candidate_key: str | None = None,
    sizing_trace: dict | None = None,
) -> float:
    """Compute notional position size from conviction score (0-100).

    P1-5: paper_trade_count is passed through to vol_target_size so new strategies
    start at 0.1 Kelly and ramp up as evidence accumulates (30+ trades = full Kelly).
    """
    from aletheia.config.loader import load_config
    cfg = load_config()
    if symbol:
        from aletheia.trading.sizing import vol_target_size
        return vol_target_size(
            symbol,
            conviction=conviction,
            direction=direction,
            paper_trade_count=paper_trade_count,
            candidate_key=f"opportunity:{opportunity_id}" if opportunity_id else None,
            outcome_candidate_key=(
                f"{learning_candidate_key}:4h" if learning_candidate_key else None
            ),
            opportunity_id=opportunity_id,
            sizing_trace=sizing_trace,
        )
    from aletheia.trading.sizing import compute_position_size
    base = cfg.trading.default_notional_usdt
    return compute_position_size(conviction, base)


def _get_strategy_paper_trade_count(strategy_id: str | None) -> int:
    """Return closed paper/demo positions count for a strategy — used for sample-aware Kelly."""
    if not strategy_id:
        return 0
    try:
        from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
        return sum(
            1 for row in eligible_strategy_outcomes()
            if str(row.get("strategy_id") or "") == strategy_id
            and "live" not in str(row.get("execution_mode") or "")
        )
    except Exception:
        return 0


def _proposal_bias_brake_reason(decision: TradingDecision) -> str | None:
    """Stop ordinary-conviction proposals from amplifying a one-sided 24h run.

    This is a proposal-layer guardrail, not a trading signal. It never forces the
    opposite direction; it only turns another same-direction trade into skip when
    the recent proposal stream is already dominated by that direction.
    """
    if decision.action != "trade":
        return None
    if decision.direction not in ("long", "short"):
        return None
    if decision.conviction >= _BIAS_BRAKE_BYPASS_CONVICTION:
        return None
    try:
        from aletheia.storage.sqlite import get_db
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_BIAS_BRAKE_WINDOW_HOURS)).isoformat()
        rows = list(get_db().execute(
            "SELECT direction, COUNT(*) n FROM trade_proposals "
            "WHERE created_at >= ? AND source != 'adopted' "
            "GROUP BY direction ORDER BY n DESC",
            (cutoff,),
        ))
    except Exception as e:
        logger.debug("proposal bias brake skipped: %s", e)
        return None

    total = sum(int(r["n"]) for r in rows)
    if total < _BIAS_BRAKE_MIN_PROPOSALS or not rows:
        return None
    top_dir = rows[0]["direction"]
    top_n = int(rows[0]["n"])
    top_pct = top_n / total if total else 0
    if top_dir != decision.direction or top_pct <= _BIAS_BRAKE_MAX_DIRECTION_PCT:
        return None
    return (
        f"directional proposal brake: recent {total} proposals are "
        f"{top_pct:.0%} {top_dir}; waiting for cleaner evidence before adding another {top_dir}"
    )


# ── prompt builders ───────────────────────────────────────────────────────────

_OI_DIV_LABEL = {1: "hidden_acc", -1: "weak_rally", 0: ""}

def _fmt_family_edge(edge: dict | None) -> str:
    """Format per-(scanner_family, direction) live edge for the opportunity line.

    Thesis evidence (P1a): shows the PI the actual settled track record of this
    candidate's scanner family + direction so it weighs the pick against real
    outcomes, not just the current signal. Empty string when no settled data yet
    (never fabricate an edge from zero samples). n>=5 lends the number weight.
    """
    if not edge or not edge.get("n"):
        return ""
    n = edge["n"]
    wr = edge.get("win_rate")
    ar = edge.get("avg_return")
    wr_s = f"{wr:.0f}%WR" if wr is not None else "?WR"
    ar_s = f"{ar:+.2f}%avg_net" if ar is not None else ""
    weak = " (thin)" if n < 5 else ""
    return f" {{edge:{n}T {wr_s} {ar_s}{weak}}}"


def _fmt_position_x_context(context: dict | None) -> str:
    if not context or context.get("status") != "ok":
        return ""
    rendered = str(context.get("rendered") or "").strip()
    return f" | {rendered}" if rendered else ""


def _direction_skew_note(rows: list[dict], label: str) -> str:
    dirs = [
        str(r.get("direction") or r.get("direction_bias") or "").lower()
        for r in rows
    ]
    dirs = [d for d in dirs if d in ("long", "short")]
    if len(dirs) < 5:
        return ""
    long_n = sum(1 for d in dirs if d == "long")
    short_n = sum(1 for d in dirs if d == "short")
    top = max(long_n, short_n)
    if top / len(dirs) < 0.8:
        return ""
    top_dir = "long" if long_n >= short_n else "short"
    return (
        f"  Direction diversity check: {label} is {top}/{len(dirs)} {top_dir}. "
        "A trending market CAN be one-sided; do not treat skew as a veto. "
        "Only skip if the setup lacks independent confirmation."
    )


def _fmt_feature_line(f: dict) -> str:
    """Format one universe feature entry for the assessment prompt."""
    sym = f["symbol"]
    rsi = f.get("rsi_14")
    atr = f.get("atr_pct")
    r1 = f.get("return_1h") or 0.0
    r4 = f.get("return_4h") or 0.0
    r24 = f.get("return_24h") or 0.0
    fr_str = _fmt_funding(f.get("funding_rate"))
    oi = f.get("oi_delta_1h")
    rs = f.get("relative_strength_24h")
    vr = f.get("vol_ratio")
    tkr = f.get("taker_buy_ratio")
    div = f.get("oi_price_divergence")
    crowding = f.get("crowding_signal", False)

    if rsi is None and atr is None:
        return f"  {sym}: price={f.get('price', '?')}"
    rsi_s = f"{rsi:.0f}" if rsi is not None else "?"
    atr_s = f"{atr:.1f}" if atr is not None else "?"
    base = (
        f"  {sym}: RSI={rsi_s} ATR={atr_s}% "
        f"1h={r1:+.1f}% 4h={r4:+.1f}% 24h={r24:+.1f}% "
        f"FR={fr_str} OI_Δ={oi if oi is not None else '?'}"
    )
    extras = []
    if rs is not None:
        extras.append(f"RS24h={rs:+.1f}%")
    if vr is not None:
        extras.append(f"vol_r={vr:.1f}x")
    if tkr is not None:
        extras.append(f"taker={tkr:.2f}")
    if div and div != 0:
        extras.append(_OI_DIV_LABEL.get(div, ""))
    if crowding:
        extras.append("CROWDED")
    return base + (" [" + " ".join(x for x in extras if x) + "]" if extras else "")


_SIGNAL_RULES = """\
Signal interpretation (same logic as the scanner chain):
  FR>+0.01%: crowded longs → bearish bias for trend; bullish for mean-reversion
  FR<-0.01%: crowded shorts → bullish bias for trend; bearish for mean-reversion
  OI_div=hidden_acc (OI↑+price↓): possible accumulation → potential long setup
  OI_div=weak_rally (OI↓+price↑): distribution risk → potential short setup
  taker>1.2: aggressive buyers confirm momentum; taker<0.8: aggressive sellers
  vol_ratio>2x: volume breakout — confirms any directional trade
  CROWDED: high funding + large OI move together — elevated reversal risk"""


def _build_assessment_prompt(ctx: dict) -> str:
    """Build the three-layer assessment prompt from enriched trading context."""
    layer1 = ctx.get("layer1", {})
    layer2 = ctx.get("layer2", {})
    layer3 = ctx.get("layer3", {})

    # ── Layer 1: Environment Snapshot ──
    positions = layer1.get("positions", [])
    positions_meta = layer1.get("positions_meta") or {}
    total_positions = int(positions_meta.get("total", len(positions)))
    visible_positions = int(
        positions_meta.get("prompt_visible_count", min(len(positions), 5))
    )
    hidden_positions = int(
        positions_meta.get(
            "prompt_overflow_count", max(total_positions - visible_positions, 0)
        )
    )
    position_count_line = (
        f"Open positions: {total_positions} total; showing {visible_positions}; "
        f"{hidden_positions} not shown"
    )
    pos_lines = "\n".join(
        f"  id={p.get('id', '?')} {p.get('symbol', '?')} "
        f"[{p.get('market_type', '?')}] {p.get('direction', '?')} "
        f"qty={p.get('quantity', '?')} entry={p.get('entry_price', '?')} "
        f"uPnL={p.get('unrealized_pnl', '?')} hold={p.get('hold_hours', '?')}h"
        f"{_fmt_position_x_context(p.get('structure_context'))}"
        for p in positions[:5]
    ) or "  (none)"

    balance = layer1.get("balance")
    if balance:
        # equity_usdt is deliberately None when the paper account has an
        # unsettled close (get_paper_account: "a missing close result makes the
        # spendable account value unknown"); available_usdt can be None on the
        # cached-exchange branch. Render "unknown" honestly instead of crashing
        # the entire evaluation on a NoneType format spec — a None here otherwise
        # kills every pi_trading_decision cycle.
        _equity = balance.get("equity_usdt")
        _available = balance.get("available_usdt")
        _equity_str = f"${_equity:.0f}" if _equity is not None else "unknown (unsettled closes)"
        _available_str = f"${_available:.0f}" if _available is not None else "unknown"
        balance_line = f"  Equity: {_equity_str} | Available: {_available_str}"
    else:
        balance_line = "  (unavailable)"

    today_pnl = layer1.get("today_pnl")
    pnl_line = f"  Today realized: ${today_pnl:+.2f}" if today_pnl is not None else "  (no trades today)"

    regime = layer1.get("regime")
    # "long_bias=25%, short_bias=75%" read like directional VETOES to the LLM
    # (i.e. "never go long"); they are probability weights. Renamed to *_weight
    # with an explicit note so the LLM treats them as priors, not prohibitions.
    regime_line = (
        f"  {regime['state'].upper()} (conf={regime['confidence']:.0%}, "
        f"long_weight={regime['long_bias']:.0%}, short_weight={regime['short_bias']:.0%}) "
        f"[probability weights, not vetoes]"
        if regime else "  (unknown)"
    )

    features = layer1.get("features", [])
    feature_lines = "\n".join(
        _fmt_feature_line(f) if f.get('rsi_14') is not None else
        f"  {f['symbol']}: price={f.get('price', '?')}"
        for f in features[:10]
    ) or "  (no data)"

    risk = layer1.get("risk", {})

    # ── Layer 2: Short-term Memory ──
    reflection = layer2.get("reflection")

    stats = layer2.get("outcome_stats")
    _bypass = False
    try:
        from aletheia.config.loader import load_config
        _bypass = load_config().agent.bypass_distress_gate
    except Exception:
        pass
    if _bypass:
        stats_block = "  (performance stats suppressed — demo/test mode)"
    elif stats:
        # Render win_rate as "NW/ML (X% win rate)" — a bare "46.6% win rate" next
        # to a negative PnL was misread by the LLM as "-45.8% drawdown" and drove
        # a phantom-crisis all-skip. Explicit W/L counts make the metric
        # unambiguous. PnL labelled "realized_PnL" to distinguish from drawdown.
        stats_block = (
            f"  7d: {stats['total_trades']} trades, "
            f"{stats['wins']}W/{stats['losses']}L ({stats['win_rate']}% win rate), "
            f"realized_PnL=${stats['total_pnl']:+.2f}, avg=${stats['avg_pnl_per_trade']:+.2f}/trade\n"
            f"  Best: ${stats['best_win']:+.2f} | Worst: ${stats['worst_loss']:+.2f}"
        )
    else:
        stats_block = "  (no recent trades)"

    per_sym = layer2.get("per_symbol", [])
    sym_lines = "" if _bypass else (
        "\n".join(
            f"  {s['symbol']}: {s['trades']}T {s['wins']}W PnL=${s['pnl']:+.2f}"
            for s in per_sym[:5]
        ) or ""
    )

    strat_perf = layer2.get("strategies", [])
    strat_lines = "  (strategy performance suppressed — demo/test mode)" if _bypass else (
        "\n".join(
            f"  {s['name']}: WR={s.get('win_rate', '?')}% PnL={s.get('total_pnl', '?')} trades={s.get('trade_count', 0)}"
            for s in strat_perf[:5]
        ) or "  (none active)"
    )

    trade_analytics_lines = "" if _bypass else _fmt_trade_analytics(layer2.get("trade_analytics"))

    # ── Layer 3: Long-term Wisdom ──
    lessons = layer3.get("lessons", [])
    lesson_lines = "\n".join(
        f"  [{l['type']}] {l.get('symbol') or 'general'}: {l['content']}"
        for l in lessons[:7]
    ) or "  (none)"

    guards = layer3.get("guard_candidates", [])
    guard_lines = "\n".join(
        f"  ⚠ {g.get('symbol', '?')}: {g['content']}"
        for g in guards[:3]
    ) or ""

    serenity_opps = layer3.get("serenity_opportunities", [])
    # D2: show the INDEPENDENTLY assessed tier (authoritative gate), not the self-grade.
    # Phase 2: also surface the deterministic score/bucket/evidence_count so the PI
    # weighs research picks on the same transparent scale the Serenity scorer uses.
    # EQ-R: equity stock tokens are observation-only — labelled [EQUITY/OBS] so the PI
    # knows not to include them as order candidates.
    _serenity_lines: list[str] = []
    for o in serenity_opps:
        _is_eq = o.get("asset_class") == "equity" or not o.get("execution_eligible", True)
        _eq_tag = " [EQUITY/OBS — research context only, do not trade]" if _is_eq else ""
        _serenity_lines.append(
            f"  {o['symbol']}{_eq_tag} {o.get('direction', '?')} "
            f"[conf={o.get('confidence_score', '?')} tier={o.get('assessed_tier') or o.get('evidence_tier', '?')} "
            f"score={o.get('score', '?')} bucket={o.get('bucket', '?')} evidence={o.get('evidence_count', 0)}]: "
            f"{o.get('thesis', '')}"
            + (f"\n      key evidence: {o['key_evidence']}" if o.get("key_evidence") else "")
        )
    serenity_lines = "\n".join(_serenity_lines) or ""

    # ── Data Health Alerts ──
    data_health = layer1.get("data_health", {})
    health_alerts: list[str] = []
    # Only inject the conservative-bias alert below MEANINGFUL coverage (<50%).
    # Firing it at 13/15 (87%) put an "operate conservatively" instruction in
    # every prompt regardless of signal quality — a third systematic skip-bias
    # stacked on the win_rate/regime misreads (2026-06-23 audit).
    _ready = data_health.get("members_with_full_coverage", 0)
    _total = data_health.get("total_members", 0) or 1
    if _ready < _total * 0.5:
        health_alerts.append(
            "ℹ️ Historical data sparse "
            f"({_ready}/{_total} members ready, <50%). "
            "Backtest/Optuna coverage thin — weight live signals over backtested ones."
        )
    if data_health.get("signal_status") == "degraded":
        wr = data_health.get("signal_win_rate_24h", "?")
        # Labelled signal_accuracy_24h (not win_rate) to avoid colliding with the
        # 7d trade win_rate in the stats block — two same-named metrics let the
        # LLM stack them into a double-crisis reading.
        health_alerts.append(
            f"⚠️ SIGNAL DEGRADATION: 24h universe signal_accuracy_24h={wr}% (below 40%). "
            "Consider: reducing position sizing, tightening stops, skipping low-conviction trades."
        )

    # ── Assemble prompt ──
    parts = []
    if health_alerts:
        parts.append("# ⚠️ System Health Alerts")
        parts.extend(health_alerts)
        parts.append("")

    parts += [
        "# Layer 1: Environment Snapshot (current state)",
        "",
        "## Portfolio",
        position_count_line,
        pos_lines,
        "",
        "## Account",
        balance_line,
        pnl_line,
        "",
        "## Market Regime",
        regime_line,
        "",
        "## Technical Features (top universe assets)",
        feature_lines,
        "",
        _SIGNAL_RULES,
        "",
    ]

    # Opportunities from market_observer
    opps = layer1.get("opportunities", [])
    if opps:
        opp_lines = "\n".join(
            f"  {o['symbol']} [{o.get('market', '?')}] {o.get('direction_bias', '?')} "
            f"conf={o.get('confidence_score', '?')} "
            f"[{o.get('scanner_family', '')}]{_fmt_family_edge(o.get('family_edge'))}: {o.get('thesis', '')}"
            f"{(' | obs: ' + '; '.join(o['key_observations'])) if o.get('key_observations') else ''}"
            f"{_fmt_position_x_context(o.get('structure_context'))}"
            for o in opps[:8]
        )
        parts += ["## Open Opportunities (market_observer output)", opp_lines, ""]
        skew_note = _direction_skew_note(opps[:8], "open opportunities")
        if skew_note:
            parts += [skew_note, ""]

    # Factor feedback — blocked/preferred categories
    factors = layer1.get("factor_feedback", [])
    if factors:
        factor_lines = "\n".join(
            f"  {f['factor_key']} ({f['group_type']}): {f['status']} "
            f"WR={f.get('win_rate', '?')}% exp={f.get('expectancy', '?')} "
            f"n={f.get('trade_count', 0)}"
            for f in factors
        )
        parts += ["## Factor Feedback (historical pattern performance)", factor_lines, ""]

    parts += [
        f"## Risk: kill_switch={risk.get('kill_switch', False)}, "
        f"max_daily_loss={risk.get('max_daily_loss_pct', '?')}%, "
        f"max_positions={risk.get('max_open_positions', '?')}",
        "",
    ]

    # Futures limits — leverage ceilings + account leverage headroom so the PI's
    # market_type/leverage choice is bounded by reality, not guessed.
    futures = layer1.get("futures") or {}
    if futures:
        f_parts = ["## Futures Limits (leverage ceilings + account headroom)"]
        lim = futures.get("leverage_limits") or {}
        if lim:
            lim_lines = ", ".join(
                f"{s}({v['tier']}≤{v['max_demo']}x)" for s, v in list(lim.items())[:8]
            )
            f_parts.append(f"  Per-symbol max leverage (demo): {lim_lines}")
        alr = futures.get("account_leverage_ratio")
        cap = futures.get("account_leverage_cap")
        if alr is not None or cap is not None:
            f_parts.append(
                f"  Account leverage: {alr if alr is not None else '?'}x / cap {cap if cap is not None else '?'}x"
            )
        amp = futures.get("available_margin_pct")
        if amp is not None:
            f_parts.append(f"  Available margin: {amp}% of equity")
        near = futures.get("positions_near_liquidation") or []
        if near:
            near_str = ", ".join(f"{n['symbol']} (liq {n['gap_pct']}% away)" for n in near)
            f_parts.append(f"  ⚠ NEAR LIQUIDATION: {near_str}")
        parts += f_parts + [""]

    parts += [
        "---",
        "# Layer 2: Short-term Memory (past 7 days)",
        "",
    ]
    if reflection and not _bypass:
        parts.append("## Last Decision Reflection")
        parts.append(f"  {reflection}")
        parts.append("")
    parts += [
        "## Recent Performance",
        stats_block,
    ]
    if sym_lines:
        parts.append(sym_lines)
    parts += [
        "",
        "## Strategy Performance",
        strat_lines,
        "",
    ]
    if trade_analytics_lines:
        parts += [
            "## Trades Analytics (closed positions)",
            trade_analytics_lines,
            "",
        ]

    # A10 (LEARNING_LOOP_AUDIT / PIPELINE_GAPS D1): these three analytics signals
    # were COMPUTED in layer2 (trading_context._build_layer2_short_term) but never
    # rendered into the prompt — so calibration, the trading memo, and reasoning
    # feedback were silently dropped before reaching the System Operator. They are real,
    # data-derived signals; surface them.
    if not _bypass:
        memo = layer2.get("trading_memo")
        if memo and memo.get("rendered"):
            parts += ["## Trading Memo (insights from historical performance analysis)",
                      memo["rendered"], ""]
        calibration_fb = layer2.get("calibration_feedback")
        if calibration_fb:
            parts += ["## Conviction Calibration (your past conviction vs actual win-rate)",
                      calibration_fb, ""]
        reasoning_fb = layer2.get("reasoning_feedback")
        if reasoning_fb:
            parts += ["## Reasoning Feedback (which signal patterns have actually worked)",
                      reasoning_fb, ""]

    # Executable candidates are the ranker/pending exact-identity intersection.
    ranker = (
        layer2.get("executable_candidates")
        if "executable_candidates" in layer2
        else layer2.get("ranker_candidates", [])
    )
    if ranker:
        ranker_lines = "\n".join(
            f"  {c['symbol']} {c.get('direction', '?')} score={c.get('trade_score', '?')}"
            for c in ranker[:6]
        )
        parts += [
            "## Executable Candidates (ranked picks backed by durable pending carriers)",
            ranker_lines,
            "",
        ]
        skew_note = _direction_skew_note(ranker[:6], "executable candidates")
        if skew_note:
            parts += [skew_note, ""]
    analysis_only = layer2.get("analysis_only_candidates") or []
    if analysis_only:
        preview_lines = "\n".join(
            f"  {c['symbol']} {c.get('direction', '?')} score={c.get('trade_score', '?')}"
            for c in analysis_only[:6]
        )
        parts += [
            "## Analysis-only Ranker Preview (not queued; never select for execution)",
            preview_lines,
            "",
        ]
    excluded_summary = layer2.get("excluded_candidates_summary") or []
    if excluded_summary:
        excluded_lines = []
        for row in excluded_summary[:6]:
            examples = ", ".join(
                f"{e.get('symbol')} {e.get('direction')}"
                for e in (row.get("examples") or [])[:3]
                if e.get("symbol")
            )
            suffix = f" examples={examples}" if examples else ""
            excluded_lines.append(f"  {row.get('reason')}: {row.get('count', 0)}{suffix}")
        parts += ["## Excluded Candidate Summary (not executable now)", "\n".join(excluded_lines), ""]

    # Phase 4b: Pending signals awaiting your decision
    pending = layer2.get("pending_signals", [])
    if pending:
        pending_lines = "\n".join(
            f"  {p['symbol']} {p.get('direction', '?')} "
            f"[{p.get('market_type') or 'unknown market'}]: {p.get('reasoning', '')}"
            for p in pending
        )
        queue = layer2.get("pending_queue") or {}
        queue_line = (
            f"Queue: {queue.get('unresolved_total', '?')} unresolved, "
            f"{queue.get('overflow_count', '?')} beyond this visible batch; "
            "oldest visible signal is listed first."
        )
        parts += [
            "## ⚡ Pending Signals (awaiting your decision)",
            "These signals were collected by the scanner pipeline. You are the sole decision maker —",
            "make exactly one trade or skip decision for the oldest suitable visible signal.",
            queue_line,
            pending_lines, "",
        ]
        skew_note = _direction_skew_note(pending, "pending signals")
        if skew_note:
            parts += [skew_note, ""]

    parts += [
        "---",
        "# Layer 3: Long-term Wisdom",
        "",
        "## Lessons & Guards",
        lesson_lines,
    ]
    if guard_lines:
        parts.append("")
        parts.append("## Pending Guard Candidates (patterns that failed recently)")
        parts.append(guard_lines)
    if serenity_lines:
        parts.append("")
        parts.append("## Research-sourced Opportunities (from Serenity agent)")
        parts.append(
            "  These are LONG-HORIZON, fundamentals/supply-chain theses — a different "
            "school from the short-term price-action signals above, NOT same-day setups. "
            "`tier` is an automatic evidence floor (a deterministic evaluator's grade, "
            "not a conviction), `score`/`bucket` are computed, not human-vetted. Treat "
            "each as analyst input to weigh independently — you are the sole decision "
            "maker; do not rubber-stamp."
        )
        parts.append(serenity_lines)

    # L-Bug-B: dreaming-agent post-trade lessons (advisory reflections, not risk
    # blocks). These ps_lessons previously had no path into the PI prompt at all.
    ps_lessons = layer3.get("ps_lessons", [])
    if ps_lessons:
        parts.append("")
        parts.append("## Recent Post-trade Lessons (dreaming agent reflections)")
        for l in ps_lessons[:5]:
            parts.append(
                f"  [{l.get('severity') or 'info'}] {l.get('category') or 'general'}: "
                f"{l.get('summary', '')}"
            )

    parts += [
        "",
        "---",
        "# Your Reasoning Space",
        "",
        "Reason freely about whether to act, but execution authority is not free-form:",
        "- For trade, choose only an exact symbol, direction, and market_type "
        "shown in Pending Signals / Executable Candidates.",
        "- Analysis-only Ranker Preview rows are not queued and must never be selected "
        "for execution. If no durable pending signal is suitable, skip.",
        "- What is the dominant market narrative right now? Is it already priced in?",
        "- Am I seeing confluence (multiple signals aligning) or conflict?",
        "- Given my recent 7-day performance, should I be more or less aggressive?",
        "- Is there an asymmetric risk/reward setup I'm missing?",
        "- Do the lessons or guard candidates warn against this specific setup?",
        "- What would make me change my mind about this trade?",
        "",
        "The guard chain handles risk limits independently — focus purely on:",
        "SHOULD this trade happen, in WHAT direction, with WHAT conviction?",
        "",
        "## Market type & leverage",
        "- market_type: for a new entry, copy the exact market_type from its durable "
        "Pending Signal; never relabel spot as futures or futures as spot downstream. "
        "A short requires an existing usdt_futures carrier (spot cannot be sold short).",
        "- leverage: integer ≥1, only for usdt_futures. Scale with conviction — 1 (none) when "
        "uncertain, 2-3 for solid setups, higher only for the strongest confluence. The risk "
        "engine clamps to a per-asset-tier cap (majors allow more than alts), so an over-eager "
        "number is reduced, not rejected. Leave at 1 if unsure.",
        "",
        "Actions available:",
        "- trade: open a new position",
        "- skip: no action now",
        "",
        "Return: action, symbol (BTC/USDT format), direction (long/short), "
        "conviction (0-100), market_type (spot/usdt_futures), leverage (integer ≥1), "
        "reasoning, bear_case (required: strongest concrete reason this decision can fail), "
        "risk_factors, supporting_signals, opposing_signals, "
        "key_assumptions (array of ≤3 short strings: critical premises this decision rests on).",
        "",
        "For every trade action, also return parameter_rationale with five "
        "short, setup-specific reasons: entry_price, stop_loss, take_profit, notional_usdt, "
        "and leverage. Explain why each exact choice fits this trade now. These are shown to "
        "the operator next to the final values. Do not claim that you calculated a value that "
        "the system will derive from a market snapshot, configured default, or risk cap.",
    ]

    return "\n".join(parts)


def _fmt_trade_analytics(trade_analytics: dict | None) -> str:
    if not trade_analytics:
        return ""

    lines: list[str] = []
    totals = trade_analytics.get("totals") or {}
    total_trades = int(totals.get("trade_count") or 0)
    if total_trades:
        lines.append(
            "  total: "
            f"{total_trades} trades, WR={totals.get('win_rate', '?')}%, "
            f"PnL=${float(totals.get('total_pnl') or 0):+.2f}, "
            f"avg_hold={totals.get('avg_hold_hours', '?')}h, "
            f"median_hold={totals.get('median_hold_hours', '?')}h"
        )

    for src in (trade_analytics.get("by_source") or [])[:3]:
        if not src.get("trade_count"):
            continue
        lines.append(
            f"  source {src.get('source_type', 'unknown')}: "
            f"{src.get('trade_count', 0)}T WR={src.get('win_rate', '?')}% "
            f"PnL=${float(src.get('total_pnl') or 0):+.2f} "
            f"expectancy={src.get('expectancy_pct', '?')}%"
        )

    for fam in (trade_analytics.get("by_family") or [])[:4]:
        if not fam.get("trade_count"):
            continue
        family = fam.get("family") or fam.get("scanner_family") or "unknown"
        lines.append(
            f"  family {family}: "
            f"{fam.get('trade_count', 0)}T WR={fam.get('win_rate', '?')}% "
            f"expectancy={fam.get('expectancy_pct', '?')}% "
            f"alpha_vs_btc={fam.get('alpha_vs_btc_pct', '?')}"
        )

    hold = [
        h for h in (trade_analytics.get("hold_distribution") or [])
        if h.get("count", 0)
    ][:4]
    if hold:
        lines.append(
            "  hold "
            + ", ".join(
                f"{h.get('bucket', '?')}={h.get('count', 0)} ({h.get('pct', 0)}%)"
                for h in hold
            )
        )

    return "\n".join(lines)


def _fmt_funding(rate: float | None) -> str:
    if rate is None:
        return "?"
    return f"{rate * 100:+.4f}%"


def _build_bull_prompt(symbol: str, direction: str | None, ctx: dict) -> str:
    features = ctx.get("layer1", {}).get("features", [])
    snap = next((f for f in features if f.get("symbol") == symbol), {})
    regime = ctx.get("layer1", {}).get("regime")
    regime_str = f"Regime: {regime['state']} (conf={regime['confidence']:.0%})" if regime else ""

    oi_div = snap.get("oi_price_divergence")
    deriv_extras = f" OI_Δ={snap.get('oi_delta_1h', '?')} taker={snap.get('taker_buy_ratio', '?')} RS24h={snap.get('relative_strength_24h', '?')}%"
    div_note = f" [{_OI_DIV_LABEL[oi_div]}]" if oi_div else ""
    _dir = direction or "long"
    if _dir == "short":
        note = "Note: FR>+0.01% = crowded longs → supports short; taker<0.8 = seller aggression → supports short; OI_div=weak_rally = distribution → supports short."
    else:
        note = "Note: FR<-0.01% = crowded shorts → bullish; taker>1.2 = buyer aggression; OI_div=hidden_acc = potential reversal long."
    return f"""You are a bull analyst. Make the STRONGEST case for {symbol} {_dir} RIGHT NOW.

Data: RSI={snap.get('rsi_14', '?')} ATR={snap.get('atr_pct', '?')}% 1h={snap.get('return_1h', '?')}% 4h={snap.get('return_4h', '?')}% 24h={snap.get('return_24h', '?')}% FR={_fmt_funding(snap.get('funding_rate'))}{deriv_extras}{div_note}
{regime_str}
{note}

Be specific: technical setup, regime alignment, risk/reward.
Return confidence (0-100) where 100 = near-certain win. Provide 3-5 evidence points."""


def _build_bear_prompt(symbol: str, direction: str | None, ctx: dict) -> str:
    features = ctx.get("layer1", {}).get("features", [])
    snap = next((f for f in features if f.get("symbol") == symbol), {})
    regime = ctx.get("layer1", {}).get("regime")
    regime_str = f"Regime: {regime['state']} (conf={regime['confidence']:.0%})" if regime else ""

    oi_div = snap.get("oi_price_divergence")
    deriv_extras = f" OI_Δ={snap.get('oi_delta_1h', '?')} taker={snap.get('taker_buy_ratio', '?')} RS24h={snap.get('relative_strength_24h', '?')}%"
    div_note = f" [{_OI_DIV_LABEL[oi_div]}]" if oi_div else ""
    _dir = direction or "long"
    if _dir == "short":
        note = "Note: FR<-0.01% = crowded shorts → fade risk (short squeeze); taker>1.2 = buyer aggression → challenges short; OI_div=hidden_acc = potential reversal → challenges short."
    else:
        note = "Note: FR>+0.01% = crowded longs → bearish; taker<0.8 = seller aggression; OI_div=weak_rally = distribution risk."
    return f"""You are a bear analyst. Find every reason why {symbol} {_dir} RIGHT NOW is a BAD idea.

Data: RSI={snap.get('rsi_14', '?')} ATR={snap.get('atr_pct', '?')}% 1h={snap.get('return_1h', '?')}% 4h={snap.get('return_4h', '?')}% 24h={snap.get('return_24h', '?')}% FR={_fmt_funding(snap.get('funding_rate'))}{deriv_extras}{div_note}
{regime_str}
{note}

Challenge aggressively: overbought/oversold, adverse regime, funding cost, better entry timing.
Return confidence (0-100) where 100 = trade will definitely fail. Provide 3-5 counter-evidence points."""


# ── helpers ────────────────────────────────────────────────────────────────────

def _parse_decision(raw: dict) -> TradingDecision:
    mt = raw.get("market_type")
    if mt not in ("spot", "usdt_futures"):
        mt = None
    lev_raw = raw.get("leverage")
    try:
        leverage = int(lev_raw) if lev_raw is not None else None
    except (TypeError, ValueError):
        leverage = None
    if leverage is not None and leverage < 1:
        leverage = 1
    raw_assumptions = raw.get("key_assumptions")
    key_assumptions = raw_assumptions if isinstance(raw_assumptions, list) else []

    def _num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _int(v, default=0):
        # #12: guard against non-integer LLM conviction (e.g. "high", "8.5", null-ish).
        # A garbled conviction falls back to 0, which is below the gate and safely skips.
        try:
            return int(float(v)) if v is not None else default
        except (TypeError, ValueError):
            return default

    raw_parameter_rationale = raw.get("parameter_rationale")
    parameter_rationale: dict[str, str] = {}
    if isinstance(raw_parameter_rationale, dict):
        for key in _ENTRY_PARAMETER_KEYS:
            value = raw_parameter_rationale.get(key)
            if isinstance(value, str) and value.strip():
                parameter_rationale[key] = value.strip()[:600]

    raw_action = str(raw.get("action") or "skip").strip().lower()
    supported_action = raw_action in {"trade", "skip"}
    decision = TradingDecision(
        action=raw_action if supported_action else "skip",
        symbol=raw.get("symbol"),
        direction=raw.get("direction"),
        conviction=_int(raw.get("conviction", 0)),
        reasoning=raw.get("reasoning", ""),
        bear_case=str(raw.get("bear_case") or "")[:500],
        market_type=mt,
        leverage=leverage,
        stop_price=_num(raw.get("stop_price")),
        take_profit=_num(raw.get("take_profit")),
        expected_horizon_hours=_num(raw.get("expected_horizon_hours")),
        order_type=raw.get("order_type"),
        limit_price=_num(raw.get("limit_price")),
        risk_factors=raw.get("risk_factors", []),
        supporting_signals=raw.get("supporting_signals", []),
        opposing_signals=raw.get("opposing_signals", []),
        key_assumptions=key_assumptions,
        parameter_rationale=parameter_rationale,
        parameter_rationale_required=True,
    )
    if not supported_action:
        decision.reasoning = (
            f"{decision.reasoning} [unsupported_action:{raw_action}; normalized to skip]"
        ).strip()
        decision.filter_trace.append({
            "filter_name": "operator_action_support",
            "stage": "system_operator",
            "decision": "rejected",
            "reason_code": "unsupported_action",
            "inputs": {"raw_action": raw_action},
            "parameters": {"supported_actions": ["trade", "skip"]},
        })
    return decision


def _resolve_pending_carrier_identity(
    carrier: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return one exact pending identity, recovering only legacy Serenity market type.

    Current ``signal_pending`` producers persist all four authority fields.  Historical
    Serenity rows predate ``pi_decisions.market_type`` and therefore carry NULL in that
    one column.  They remain usable only when their durable SerenityOpportunity proves
    the same id, symbol, and direction and supplies a non-empty market type.  No other
    partial carrier is treated as a wildcard.
    """
    if not isinstance(carrier, dict):
        return None
    resolved = dict(carrier)
    opportunity_id = resolved.get("opportunity_id")
    symbol = resolved.get("symbol")
    direction = resolved.get("direction")
    market_type = resolved.get("market_type")
    signal_source = resolved.get("signal_source") or "market_observer"
    if not opportunity_id or not symbol or not direction:
        return None
    if signal_source not in {"market_observer", "serenity"}:
        return None
    if signal_source == "market_observer":
        if not market_type:
            return None
        resolved["signal_source"] = signal_source
        return resolved

    try:
        from aletheia.storage.repositories import get_serenity_opportunity

        source = get_serenity_opportunity(opportunity_id)
    except Exception:
        return None
    if (
        not source
        or source.get("id") != opportunity_id
        or source.get("symbol") != symbol
        or source.get("direction") != direction
        or not source.get("market_type")
        or (market_type and source.get("market_type") != market_type)
    ):
        return None
    resolved["market_type"] = market_type or source["market_type"]
    resolved["signal_source"] = "serenity"
    return resolved


def _attach_opportunity_context(decision: TradingDecision, ctx: dict) -> None:
    """Backfill opportunity_id + signal_source from the source opportunity (R0 + Phase 2).

    The System Operator freely picks a symbol+direction from the whole context; this
    matches that pick back to the opportunity it came from so the decision
    records a real opportunity_id (not the hard-coded None that severed the
    attribution chain). Sources are matched in priority order:

      1. market_observer `pending_signals` — the exact queue carrier emitted by
         paper_trader. It wins over a different same-symbol open opportunity.
      2. serenity `pending_signals` for Opportunity decisions. Layer 1/ranker rows
         are analysis-only and cannot authorize either entry or terminal identity.
      3. Legacy analysis-only helper contexts that omit the pending seam may still
         use Layer 1/ranker/Serenity attribution. Serenity sets
         signal_source='serenity' so opportunity_id is later
         resolved against serenity_opportunities (NOT opportunities) — the two id
         spaces are indistinguishable bare uuid4().hex[:12] strings.

    Best-effort and honest: if the pick maps to neither (e.g. it came from raw
    features/signals), the fields stay None rather than being fabricated. See
    LEARNING_LOOP_AUDIT_2026-06-26 §R0 + docs/plans/serenity_execution_path.md §3.2.
    """
    # M1: non-entry actions short-circuit — no opportunity context to attach
    if decision.action in {"close", "reduce", "adjust_position"}:
        return

    # The exact queued carrier is more specific than a later same-symbol/direction
    # open row, so consume it before heuristic Layer-1 matches.
    layer2 = ctx.get("layer2") or {}
    raw_pending = layer2.get("pending_signals") or []
    pending = [
        resolved
        for carrier in raw_pending
        if (resolved := _resolve_pending_carrier_identity(carrier)) is not None
    ]
    if decision.action == "trade":
        compatible_by_identity: dict[tuple, dict] = {}
        for carrier in pending:
            if decision.opportunity_id and carrier.get("opportunity_id") != decision.opportunity_id:
                continue
            if decision.symbol and carrier.get("symbol") != decision.symbol:
                continue
            if decision.direction and carrier.get("direction") != decision.direction:
                continue
            if decision.market_type and carrier.get("market_type") != decision.market_type:
                continue
            identity = (
                carrier.get("signal_source") or "market_observer",
                carrier.get("opportunity_id"),
                carrier.get("symbol"),
                carrier.get("direction"),
                carrier.get("market_type"),
            )
            compatible_by_identity[identity] = carrier
        compatible = list(compatible_by_identity.values())
        if len(compatible) > 1:
            decision.filter_trace.append({
                "filter_name": "pending_signal_identity",
                "stage": "operator_context_binding",
                "decision": "rejected",
                "reason_code": "pending_identity_ambiguous",
                "inputs": {
                    "symbol": decision.symbol,
                    "direction": decision.direction,
                    "market_type": decision.market_type,
                    "compatible_carriers": len(compatible),
                },
                "parameters": {
                    "required_identity": [
                        "opportunity_id", "signal_source", "symbol",
                        "direction", "market_type",
                    ],
                },
            })
            return
        if not compatible:
            return
        # Every later branch now sees the same unique authority row. Missing
        # model fields may be inferred only from this one durable identity.
        pending = compatible
    if not decision.symbol:
        carrier = pending[0] if pending else None
        if carrier is None:
            return
        decision.symbol = carrier.get("symbol")
        decision.direction = carrier.get("direction")
        decision.market_type = carrier.get("market_type") or decision.market_type
        decision.opportunity_id = carrier.get("opportunity_id")
        decision.signal_source = carrier.get("signal_source") or "market_observer"
        try:
            if decision.signal_source == "serenity":
                from aletheia.storage.repositories import get_serenity_opportunity

                source = get_serenity_opportunity(decision.opportunity_id)
                if source and not decision.market_type:
                    decision.market_type = source.get("market_type")
            else:
                from aletheia.storage.repositories import get_opportunity

                source = get_opportunity(decision.opportunity_id)
                if source:
                    decision.strategy_id = source.get("linked_strategy_id")
                    decision.scanner_family = source.get("scanner_family")
                    if not decision.market_type:
                        decision.market_type = source.get("market")
                    try:
                        raw_meta = source.get("metadata") or "{}"
                        meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
                    except (TypeError, ValueError):
                        meta = {}
                    decision.learning_candidate_key = meta.get("learning_candidate_key")
        except Exception:
            pass
        return

    for p in pending:
        if (p.get("signal_source") or "market_observer") != "market_observer":
            continue
        if p.get("symbol") != decision.symbol:
            continue
        pdir = p.get("direction")
        pmarket = p.get("market_type")
        if not pdir or not pmarket:
            continue
        if decision.direction and pdir != decision.direction:
            continue
        if decision.market_type and pmarket != decision.market_type:
            continue
        oid = p.get("opportunity_id")
        if not oid:
            continue
        decision.opportunity_id = oid
        decision.signal_source = "market_observer"
        decision.direction = decision.direction or pdir
        decision.market_type = decision.market_type or pmarket
        # Recover the source fields omitted from PiDecision. The id remains an
        # honest attribution anchor even if this best-effort lookup fails.
        try:
            from aletheia.storage.repositories import get_opportunity

            src_opp = get_opportunity(oid)
            if src_opp:
                try:
                    raw_meta = src_opp.get("metadata") or "{}"
                    meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
                except (TypeError, ValueError):
                    meta = {}
                decision.learning_candidate_key = meta.get("learning_candidate_key")
                decision.strategy_id = src_opp.get("linked_strategy_id")
                decision.scanner_family = src_opp.get("scanner_family")
                if not decision.market_type and src_opp.get("market"):
                    decision.market_type = src_opp.get("market")
        except Exception:
            pass
        return

    # Every decision about a fresh Opportunity may inherit identity only from the
    # durable pending queue. Layer-1/ranker rows remain analysis context: without
    # this return, a heartbeat skip could silently retire an open Opportunity
    # that paper_trader/Serenity never queued.
    if decision.action in {"trade", "skip"}:
        for p in pending:
            if p.get("signal_source") != "serenity":
                continue
            if p.get("symbol") != decision.symbol:
                continue
            pdir = p.get("direction")
            pmarket = p.get("market_type")
            if not pdir or not pmarket:
                continue
            if decision.direction and pdir != decision.direction:
                continue
            if decision.market_type and pmarket != decision.market_type:
                continue
            oid = p.get("opportunity_id")
            if not oid:
                continue
            decision.opportunity_id = oid
            decision.signal_source = "serenity"
            decision.direction = decision.direction or pdir
            decision.market_type = decision.market_type or pmarket
            if not decision.market_type:
                try:
                    from aletheia.storage.repositories import get_serenity_opportunity

                    source = get_serenity_opportunity(oid)
                    if source and source.get("market_type"):
                        decision.market_type = source["market_type"]
                except Exception:
                    pass
            return
        # Production Operator context always carries the pending queue seam, even
        # when its value is zero. Stop there so Layer-1/ranker previews cannot own
        # a heartbeat terminal. A legacy analysis-only helper context may omit the
        # seam entirely; it can retain best-effort in-memory attribution, while
        # claim_decision still refuses to persist it without a durable carrier.
        if "pending_signals" in layer2 or "pending_queue" in layer2:
            return

    opps = (ctx.get("layer1") or {}).get("opportunities") or []
    for o in opps:
        if o.get("symbol") != decision.symbol:
            continue
        bias = o.get("direction_bias")
        # Require direction agreement only when both sides declare one; an
        # opportunity with no bias still matches on symbol.
        if decision.direction and bias and bias != decision.direction:
            continue
        decision.opportunity_id = o.get("id")
        try:
            raw_meta = o.get("metadata") or "{}"
            meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
        except (TypeError, ValueError):
            meta = {}
        decision.learning_candidate_key = meta.get("learning_candidate_key")
        decision.strategy_id = o.get("linked_strategy_id")
        decision.scanner_family = o.get("scanner_family")
        decision.signal_source = "market_observer"
        # Inherit the instrument type from the source opportunity when the PI did
        # not explicitly choose one — so a spot opportunity trades as spot rather
        # than defaulting to perp (owner principle 3: no fixed instrument bias).
        if not decision.market_type and o.get("market"):
            decision.market_type = o.get("market")
        return

    # #152: even when a same-symbol Layer-1 opportunity was seen but its direction
    # conflicted (so the loop above did not attach), fall through to the ranker +
    # serenity fallbacks — a matching-direction ranker candidate (same market_observer
    # provenance) or serenity pick still carries the real opportunity_id, so the
    # attribution chain (DL-INV-4) is preserved instead of severed.

    # Ranker candidates are a second deterministic source of market_observer
    # opportunity ids. They may include candidates not currently visible in the
    # compact Layer-1 opportunity list, so use them before falling back to Serenity.
    ranker = (layer2.get("executable_candidates") or []) + (layer2.get("ranker_candidates") or [])
    for c in ranker:
        if c.get("symbol") != decision.symbol:
            continue
        cdir = c.get("direction") or c.get("direction_bias")
        if decision.direction and cdir and cdir != decision.direction:
            continue
        oid = c.get("opportunity_id")
        if not oid:
            continue
        decision.opportunity_id = oid
        decision.learning_candidate_key = c.get("learning_candidate_key")
        decision.strategy_id = c.get("strategy_id")
        decision.scanner_family = c.get("scanner_family")
        decision.signal_source = c.get("signal_source") or "market_observer"
        if not decision.market_type and c.get("market_type"):
            decision.market_type = c.get("market_type")
        return

    # Serenity direct match (Phase 2 fix): pending_signals carry opportunity_id
    # from auto_promote. Match directly — avoids the layer3 limit=5 truncation
    # that would otherwise sever the attribution chain for promoted opps.
    for p in pending:
        if p.get("signal_source") != "serenity":
            continue
        if p.get("symbol") != decision.symbol:
            continue
        pdir = p.get("direction")
        pmarket = p.get("market_type")
        if not pdir or not pmarket:
            continue
        if decision.direction and pdir != decision.direction:
            continue
        if decision.market_type and pmarket != decision.market_type:
            continue
        oid = p.get("opportunity_id")
        if not oid:
            continue
        decision.opportunity_id = oid
        decision.signal_source = "serenity"
        decision.direction = decision.direction or pdir
        decision.market_type = decision.market_type or pmarket
        # Legacy pending rows lack market_type. Recover it from the source
        # serenity_opportunity without weakening the attribution anchor.
        if not decision.market_type:
            try:
                from aletheia.storage.repositories import get_serenity_opportunity
                src_opp = get_serenity_opportunity(oid)
                if src_opp and src_opp.get("market_type"):
                    decision.market_type = src_opp["market_type"]
            except Exception:
                pass
        return

    # Serenity fallback (Phase 2): the pick matched no market_observer opportunity
    # and no pending_signal. Try the research long-hold picks surfaced in Layer 3.
    # Only anchor on an opportunity that actually carries an id (the attribution anchor).
    serenity_opps = (ctx.get("layer3") or {}).get("serenity_opportunities") or []
    for o in serenity_opps:
        # EQ-R: observation-only equity tokens must never become execution anchors.
        # "equity_tradeable" (Serenity research-produced) IS eligible — skip only "equity".
        if not o.get("execution_eligible", True):
            continue
        if o.get("asset_class") == "equity":
            continue
        if o.get("symbol") != decision.symbol:
            continue
        sdir = o.get("direction")
        if decision.direction and sdir and sdir != decision.direction:
            continue
        oid = o.get("id")
        if not oid:
            continue
        decision.opportunity_id = oid
        decision.signal_source = "serenity"
        # #21/#102: serenity_opportunities dicts store the instrument under
        # "market_type" (trading_context builds them that way), NOT "market" — the
        # old o.get("market") always returned None, so a serenity spot pick fell
        # through to the short->perp fallback. Read the correct key.
        if not decision.market_type and o.get("market_type"):
            decision.market_type = o.get("market_type")
        return
