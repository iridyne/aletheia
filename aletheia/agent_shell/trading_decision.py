"""PI Agent trading decision engine.

Provides evaluate_trading_opportunity() — the PI agent's ability to act as
Trading Decision Authority. Given a rich context snapshot, it uses structured
LLM output to decide whether to trade, then optionally runs a Bull/Bear debate
when conviction is uncertain, and finally computes a conviction-based position size.

This is intentionally a THIN layer over existing infrastructure:
  - Context comes from agent_shell/context.py (existing)
  - Proposals go through create_paper_proposal (existing, goes through Risk Engine)
  - Sizing respects existing notional caps from config/risk engine
  - No new DB tables; decisions are recorded as agent_shell audit events

Safety: PI agent can only submit paper proposals (PaperAuto permission gate).
Live trading is never reachable from this path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aletheia.agent_shell.trading_decision")


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


@dataclass
class TradingDecision:
    action: str              # "trade" | "skip" | "close" | "reduce"
    symbol: str | None
    direction: str | None    # "long" | "short"
    conviction: int          # 0-100
    reasoning: str
    risk_factors: list[str] = field(default_factory=list)
    supporting_signals: list[str] = field(default_factory=list)
    opposing_signals: list[str] = field(default_factory=list)
    position_size_usdt: float | None = None
    stop_price: float | None = None
    debate_ran: bool = False
    debate_bull_confidence: int | None = None
    debate_bear_confidence: int | None = None


@dataclass
class DebateResult:
    bull_confidence: int
    bull_thesis: str
    bull_evidence: list[str]
    bear_confidence: int
    bear_thesis: str
    bear_evidence: list[str]
    final_conviction: int    # synthesized from both sides


# ── schemas for structured LLM output ────────────────────────────────────────

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["trade", "skip", "close", "reduce"]},
        "symbol": {"type": "string"},
        "direction": {"type": "string", "enum": ["long", "short"]},
        "conviction": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasoning": {"type": "string"},
        "risk_factors": {"type": "array", "items": {"type": "string"}},
        "supporting_signals": {"type": "array", "items": {"type": "string"}},
        "opposing_signals": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "conviction", "reasoning"],
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
    """PI Agent trading decision loop.

    Steps:
    1. Assess opportunities using structured LLM with full context
    2. If conviction in uncertain range (40-80), run Bull/Bear debate
    3. Compute conviction-based position size
    4. Return TradingDecision (caller submits proposal if action="trade")
    """
    decision_raw = await _assess_opportunity(trading_context)
    if decision_raw is None:
        return TradingDecision(
            action="skip", symbol=None, direction=None,
            conviction=0, reasoning="LLM assessment unavailable",
        )

    decision = _parse_decision(decision_raw)

    # Run debate for uncertain conviction range
    if (decision.action == "trade"
            and decision.symbol
            and _DEBATE_RANGE[0] <= decision.conviction <= _DEBATE_RANGE[1]):
        debate = await _run_debate(decision.symbol, decision.direction, trading_context)
        if debate:
            decision.debate_ran = True
            decision.debate_bull_confidence = debate.bull_confidence
            decision.debate_bear_confidence = debate.bear_confidence
            decision.conviction = debate.final_conviction
            if decision.conviction < _MIN_CONVICTION_TO_TRADE:
                decision.action = "skip"
                decision.reasoning += f" [debate lowered conviction to {decision.conviction} → skip]"

    # Compute sizing
    if decision.action == "trade" and decision.conviction >= _MIN_CONVICTION_TO_TRADE:
        decision.position_size_usdt = _compute_position_size(decision.conviction)
    else:
        decision.action = "skip"

    return decision


async def submit_decision_as_proposal(decision: TradingDecision, execution_mode: str) -> dict | None:
    """Submit a TradingDecision as a paper proposal. Returns result dict or None."""
    if decision.action != "trade" or not decision.symbol or not decision.direction:
        return None

    from aletheia.trading.market_data import latest_snapshot
    from aletheia.execution.paper_tools import create_paper_proposal
    from aletheia.config.loader import load_config

    cfg = load_config()
    snap = latest_snapshot(decision.symbol)
    price = float((snap or {}).get("price") or 0)
    if not price:
        logger.warning("no_price_for_decision_proposal: %s", decision.symbol)
        return None

    notional = decision.position_size_usdt or 25.0
    quantity = round(notional / price, 6) or 0.000001

    stop_price = decision.stop_price
    if stop_price is None:
        stop_pct = cfg.trading.default_stop_loss_pct
        if decision.direction == "long":
            stop_price = round(price * (1 - stop_pct / 100.0), 8)
        else:
            stop_price = round(price * (1 + stop_pct / 100.0), 8)

    rationale = (
        f"PI Agent decision.\n"
        f"conviction={decision.conviction}\n"
        f"reasoning={decision.reasoning[:200]}\n"
        f"debate_ran={decision.debate_ran}\n"
        f"bull={decision.debate_bull_confidence} bear={decision.debate_bear_confidence}"
    )

    return create_paper_proposal(
        symbol=decision.symbol,
        market_type="usdt_futures",
        direction=decision.direction,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        execution_mode=execution_mode,
        strategy_id=None,
        opportunity_id=None,
        agent_rationale=rationale[:500],
    )


# ── LLM calls ─────────────────────────────────────────────────────────────────

async def _assess_opportunity(ctx: dict) -> dict | None:
    """Call LLM with full trading context; return raw decision dict."""
    from aletheia.agents.helpers import llm_structured

    system = (
        "You are the Trading Decision Authority for an autonomous paper trading system. "
        "Return a JSON object matching the schema provided."
    )
    user = _build_assessment_prompt(ctx)
    schema_text = _json_schema_hint(_DECISION_SCHEMA)
    full_user = f"{user}\n\nReturn JSON: {schema_text}"
    result, err = await llm_structured(system, full_user, schema=_DummySchema(_DECISION_SCHEMA))
    if err:
        logger.warning("LLM assessment failed: %s", err)
        return None
    return result


async def _run_debate(symbol: str, direction: str | None, ctx: dict) -> DebateResult | None:
    """Run Bull/Bear adversarial debate for a symbol."""
    import asyncio
    from aletheia.agents.helpers import llm_structured

    bull_user = _build_bull_prompt(symbol, direction, ctx)
    bear_user = _build_bear_prompt(symbol, direction, ctx)
    bull_schema_hint = _json_schema_hint(_BULL_SCHEMA)
    bear_schema_hint = _json_schema_hint(_BEAR_SCHEMA)

    system_bull = "You are a bull analyst. Return a JSON object matching the schema."
    system_bear = "You are a bear analyst. Return a JSON object matching the schema."

    (bull_result, bull_err), (bear_result, bear_err) = await asyncio.gather(
        llm_structured(system_bull, f"{bull_user}\n\nReturn JSON: {bull_schema_hint}", schema=_DummySchema(_BULL_SCHEMA)),
        llm_structured(system_bear, f"{bear_user}\n\nReturn JSON: {bear_schema_hint}", schema=_DummySchema(_BEAR_SCHEMA)),
    )
    if bull_err or bear_err or not bull_result or not bear_result:
        logger.debug("debate failed bull_err=%s bear_err=%s", bull_err, bear_err)
        return None

    bull_conf = int(bull_result.get("confidence", 50))
    bear_conf = int(bear_result.get("confidence", 50))
    advantage = bull_conf - bear_conf
    final = max(0, min(100, 50 + advantage))

    return DebateResult(
        bull_confidence=bull_conf,
        bull_thesis=bull_result.get("thesis", ""),
        bull_evidence=bull_result.get("evidence", []),
        bear_confidence=bear_conf,
        bear_thesis=bear_result.get("thesis", ""),
        bear_evidence=bear_result.get("evidence", []),
        final_conviction=final,
    )


# ── sizing ────────────────────────────────────────────────────────────────────

def _compute_position_size(conviction: int) -> float:
    """Compute notional position size from conviction score (0-100)."""
    from aletheia.config.loader import load_config
    cfg = load_config()
    base = getattr(cfg.trading, "default_notional_usdt", 25.0)
    ratio = conviction / 75.0   # 75 = "normal" → 1× base
    multiplier = max(_MIN_CONVICTION_MULTIPLIER, min(_MAX_CONVICTION_MULTIPLIER, ratio))
    return round(base * multiplier, 2)


# ── prompt builders ───────────────────────────────────────────────────────────

def _build_assessment_prompt(ctx: dict) -> str:
    open_positions = ctx.get("trading", {}).get("open_positions", [])
    recent_proposals = ctx.get("proposals", {}).get("recent", [])
    market_snaps = ctx.get("market", [])[:10]
    strategies = ctx.get("strategies", [])
    ruleset_signals = ctx.get("ruleset_signals", [])
    lessons = ctx.get("lessons", [])
    risk = ctx.get("risk", {})

    pos_summary = "\n".join(
        f"  {p['symbol']} {p['direction']} qty={p['quantity']} entry={p['entry_price']} upnl={p.get('unrealized_pnl', '?')}"
        for p in open_positions[:5]
    ) or "  (none)"

    market_summary = "\n".join(
        f"  {s['symbol']} price={s['price']} 24h={s.get('change_24h', '?')}%"
        for s in market_snaps
    ) or "  (none)"

    signal_summary = "\n".join(
        f"  {s['symbol']} {s['signal_type']} via {s['strategy']}"
        for s in ruleset_signals[:5]
    ) or "  (none)"

    lesson_summary = "\n".join(
        f"  [{l.get('action')}] {l.get('symbol', 'any')}: {l.get('body', '')[:80]}"
        for l in lessons[:5]
    ) or "  (none)"

    return f"""You are the Trading Decision Authority for an autonomous paper trading system.
Your role: decide whether to open, close, or skip a trade right now.

## Current Portfolio
{pos_summary}

## Market Conditions (latest snapshots)
{market_summary}

## Active RuleSet Signals
{signal_summary}

## Recent Lessons (accepted)
{lesson_summary}

## Risk State
Kill switch: {risk.get('kill_switch', False)}
Max daily loss %: {risk.get('max_daily_loss_pct', '?')}
Max open positions (demo): {risk.get('max_open_positions_demo', '?')}

## Instructions
Evaluate the current state and return a trading decision. Consider:
- Are there high-conviction entry opportunities (price momentum, ruleset signals, strong thesis)?
- Is the current portfolio overexposed or well-balanced?
- Do any accepted lessons block specific symbols or directions?
- What is the best single action to take right now?

If action="trade": specify symbol (canonical BTC/USDT format), direction (long/short), and conviction (0-100).
If action="skip": explain why now is not the right time.
If action="close": specify which symbol to close and why.
Conviction above 75 = high confidence, 55-75 = moderate, below 55 = skip."""


def _build_bull_prompt(symbol: str, direction: str | None, ctx: dict) -> str:
    market_snaps = {s["symbol"]: s for s in ctx.get("market", [])}
    snap = market_snaps.get(symbol, {})
    return f"""You are a bull analyst. Your job is to make the STRONGEST possible case for trading {symbol} {direction or 'long'} RIGHT NOW.

Market data: price={snap.get('price', '?')}, 24h change={snap.get('change_24h', '?')}%

Using all available evidence, build the most compelling bull thesis. Be specific about:
- Technical setup (momentum, trend, support levels)
- Macro/regime context if favorable
- Risk/reward at current price

Return confidence (0-100) where 100 = near-certain win. Provide 3-5 concrete evidence points."""


def _build_bear_prompt(symbol: str, direction: str | None, ctx: dict) -> str:
    market_snaps = {s["symbol"]: s for s in ctx.get("market", [])}
    snap = market_snaps.get(symbol, {})
    return f"""You are a bear analyst. Your job is to find every reason why trading {symbol} {direction or 'long'} RIGHT NOW is a BAD idea.

Market data: price={snap.get('price', '?')}, 24h change={snap.get('change_24h', '?')}%

Challenge the bull case aggressively. Focus on:
- What could go wrong immediately (resistance, overbought, funding rates)
- Macro risks or adverse regime
- Better entry timing exists

Return confidence (0-100) where 100 = trade will definitely fail. Provide 3-5 concrete counter-evidence points."""


# ── helpers ────────────────────────────────────────────────────────────────────

def _parse_decision(raw: dict) -> TradingDecision:
    return TradingDecision(
        action=raw.get("action", "skip"),
        symbol=raw.get("symbol"),
        direction=raw.get("direction"),
        conviction=int(raw.get("conviction", 0)),
        reasoning=raw.get("reasoning", ""),
        risk_factors=raw.get("risk_factors", []),
        supporting_signals=raw.get("supporting_signals", []),
        opposing_signals=raw.get("opposing_signals", []),
    )
