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
        "action": {"type": "string", "enum": ["trade", "skip", "close", "reduce", "watch", "scale_in", "flip"]},
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
    4. Return TradingDecision (caller submits proposal if action="trade"|"scale_in"|"flip")
    """
    decision_raw = await _assess_opportunity(trading_context)
    if decision_raw is None:
        return TradingDecision(
            action="skip", symbol=None, direction=None,
            conviction=0, reasoning="LLM assessment unavailable",
        )

    decision = _parse_decision(decision_raw)

    # "watch" → record interest, no trade (treated as informational skip)
    if decision.action == "watch":
        return decision

    # "scale_in" and "flip" are trade-like actions that go through debate+sizing
    trade_actions = ("trade", "scale_in", "flip")

    # Run debate for uncertain conviction range
    if (decision.action in trade_actions
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

    # Compute sizing for trade-like actions
    if decision.action in trade_actions and decision.conviction >= _MIN_CONVICTION_TO_TRADE:
        decision.position_size_usdt = _compute_position_size(decision.conviction, symbol=decision.symbol)
    elif decision.action in trade_actions:
        decision.action = "skip"

    return decision


async def submit_decision_as_proposal(decision: TradingDecision, execution_mode: str) -> dict | None:
    """Submit a TradingDecision as a paper proposal. Returns result dict or None."""
    if decision.action != "trade" or not decision.symbol or not decision.direction:
        return None

    from aletheia.trading.market_data import latest_snapshot
    from aletheia.execution.paper_tools import create_paper_proposal
    from aletheia.config.loader import load_config
    from aletheia.trust.guard_chain import run_guards, summarize

    cfg = load_config()
    snap = latest_snapshot(decision.symbol)
    price = float((snap or {}).get("price") or 0)
    if not price:
        logger.warning("no_price_for_decision_proposal: %s", decision.symbol)
        return None

    # Run guard chain (advisory — does not block)
    guard_verdicts = run_guards({
        "symbol": decision.symbol,
        "direction": decision.direction,
        "conviction": decision.conviction,
        "strategy_id": None,
    })
    guard_summary = summarize(guard_verdicts)
    if guard_summary["blocks"]:
        logger.warning("guard_chain_block: %s — %s", decision.symbol, guard_summary["details"])
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

    decision_meta = {
        "conviction": decision.conviction,
        "debate_ran": decision.debate_ran,
        "debate_bull": decision.debate_bull_confidence,
        "debate_bear": decision.debate_bear_confidence,
        "supporting": decision.supporting_signals[:5],
        "opposing": decision.opposing_signals[:5],
        "guards": guard_summary,
    }
    rationale = (
        f"{decision.reasoning[:200]}\n"
        f"---\n"
        f"{json.dumps(decision_meta)}"
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
        "You receive a structured three-layer context: (1) current environment snapshot, "
        "(2) your recent 7-day performance, (3) accumulated wisdom from past trades. "
        "The risk/guard system operates independently after your decision — "
        "focus on WHAT to trade, not HOW MUCH. Return a JSON object matching the schema."
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

def _compute_position_size(conviction: int, symbol: str | None = None) -> float:
    """Compute notional position size from conviction score (0-100).

    When vol_sizing_enabled and symbol is provided, uses ATR-normalized sizing.
    """
    from aletheia.config.loader import load_config
    cfg = load_config()
    if cfg.trading.vol_sizing_enabled and symbol:
        from aletheia.trading.sizing import vol_target_size
        return vol_target_size(symbol, conviction=conviction)
    from aletheia.trading.sizing import compute_position_size
    base = cfg.trading.default_notional_usdt
    return compute_position_size(conviction, base)


# ── prompt builders ───────────────────────────────────────────────────────────

def _build_assessment_prompt(ctx: dict) -> str:
    """Build the three-layer assessment prompt from enriched trading context."""
    layer1 = ctx.get("layer1", {})
    layer2 = ctx.get("layer2", {})
    layer3 = ctx.get("layer3", {})
    ruleset_signals = ctx.get("ruleset_signals", [])

    # ── Layer 1: Environment Snapshot ──
    positions = layer1.get("positions", [])
    pos_lines = "\n".join(
        f"  {p['symbol']} {p['direction']} qty={p['quantity']} entry={p['entry_price']} "
        f"uPnL={p.get('unrealized_pnl', '?')} hold={p.get('hold_hours', '?')}h"
        for p in positions[:5]
    ) or "  (none)"

    balance = layer1.get("balance")
    balance_line = (
        f"  Equity: ${balance['equity_usdt']:.0f} | Available: ${balance['available_usdt']:.0f}"
        if balance else "  (unavailable)"
    )

    today_pnl = layer1.get("today_pnl")
    pnl_line = f"  Today realized: ${today_pnl:+.2f}" if today_pnl is not None else "  (no trades today)"

    regime = layer1.get("regime")
    regime_line = (
        f"  {regime['state'].upper()} (conf={regime['confidence']:.0%}, "
        f"long_bias={regime['long_bias']:.0%}, short_bias={regime['short_bias']:.0%})"
        if regime else "  (unknown)"
    )

    features = layer1.get("features", [])
    feature_lines = "\n".join(
        f"  {f['symbol']}: RSI={f.get('rsi_14', '?'):.0f} ATR={f.get('atr_pct', '?'):.1f}% "
        f"1h={f.get('return_1h', 0):+.1f}% 4h={f.get('return_4h', 0):+.1f}% "
        f"24h={f.get('return_24h', 0):+.1f}% "
        f"FR={_fmt_funding(f.get('funding_rate'))} OI_Δ1h={f.get('oi_delta_1h', '?')}"
        if f.get('rsi_14') is not None else
        f"  {f['symbol']}: price={f.get('price', '?')}"
        for f in features[:10]
    ) or "  (no data)"

    risk = layer1.get("risk", {})

    signal_lines = "\n".join(
        f"  {s['symbol']} {s['signal_type']} {s.get('direction', '')} via {s['strategy']}"
        for s in ruleset_signals[:7]
    ) or "  (none active)"

    # ── Layer 2: Short-term Memory ──
    reflection = layer2.get("reflection")

    stats = layer2.get("outcome_stats")
    if stats:
        stats_block = (
            f"  7d: {stats['total_trades']} trades, {stats['win_rate']}% win rate, "
            f"PnL=${stats['total_pnl']:+.2f}, avg=${stats['avg_pnl_per_trade']:+.2f}/trade\n"
            f"  Best: ${stats['best_win']:+.2f} | Worst: ${stats['worst_loss']:+.2f}"
        )
    else:
        stats_block = "  (no recent trades)"

    per_sym = layer2.get("per_symbol", [])
    sym_lines = "\n".join(
        f"  {s['symbol']}: {s['trades']}T {s['wins']}W PnL=${s['pnl']:+.2f}"
        for s in per_sym[:5]
    ) or ""

    strat_perf = layer2.get("strategies", [])
    strat_lines = "\n".join(
        f"  {s['name']}: WR={s.get('win_rate', '?')}% PnL={s.get('total_pnl', '?')} trades={s.get('trade_count', 0)}"
        for s in strat_perf[:5]
    ) or "  (none active)"

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

    # ── Assemble prompt ──
    parts = [
        "# Layer 1: Environment Snapshot (current state)",
        "",
        "## Portfolio",
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
        "## Active Signals",
        signal_lines,
        "",
        f"## Risk: kill_switch={risk.get('kill_switch', False)}, "
        f"max_daily_loss={risk.get('max_daily_loss_pct', '?')}%, "
        f"max_positions={risk.get('max_open_positions', '?')}",
        "",
        "---",
        "# Layer 2: Short-term Memory (past 7 days)",
        "",
    ]
    if reflection:
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

    parts += [
        "",
        "---",
        "# Your Reasoning Space",
        "",
        "Consider freely — there are no fixed rules for what you must do:",
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
        "Actions available:",
        "- trade: open a new position",
        "- skip: no action now",
        "- close: close an existing position",
        "- reduce: reduce an existing position size",
        "- watch: mark a symbol for observation (interested but not ready)",
        "- scale_in: add to an existing winning position",
        "- flip: reverse direction (close existing + open opposite)",
        "",
        "Return: action, symbol (BTC/USDT format), direction (long/short), "
        "conviction (0-100), reasoning, risk_factors, supporting_signals, opposing_signals.",
    ]

    return "\n".join(parts)


def _fmt_funding(rate: float | None) -> str:
    if rate is None:
        return "?"
    return f"{rate * 100:+.4f}%"


def _build_bull_prompt(symbol: str, direction: str | None, ctx: dict) -> str:
    features = ctx.get("layer1", {}).get("features", [])
    snap = next((f for f in features if f.get("symbol") == symbol), {})
    regime = ctx.get("layer1", {}).get("regime")
    regime_str = f"Regime: {regime['state']} (conf={regime['confidence']:.0%})" if regime else ""

    return f"""You are a bull analyst. Make the STRONGEST case for {symbol} {direction or 'long'} RIGHT NOW.

Data: RSI={snap.get('rsi_14', '?')} ATR={snap.get('atr_pct', '?')}% 1h={snap.get('return_1h', '?')}% 4h={snap.get('return_4h', '?')}% 24h={snap.get('return_24h', '?')}% FR={_fmt_funding(snap.get('funding_rate'))}
{regime_str}

Be specific: technical setup, regime alignment, risk/reward.
Return confidence (0-100) where 100 = near-certain win. Provide 3-5 evidence points."""


def _build_bear_prompt(symbol: str, direction: str | None, ctx: dict) -> str:
    features = ctx.get("layer1", {}).get("features", [])
    snap = next((f for f in features if f.get("symbol") == symbol), {})
    regime = ctx.get("layer1", {}).get("regime")
    regime_str = f"Regime: {regime['state']} (conf={regime['confidence']:.0%})" if regime else ""

    return f"""You are a bear analyst. Find every reason why {symbol} {direction or 'long'} RIGHT NOW is a BAD idea.

Data: RSI={snap.get('rsi_14', '?')} ATR={snap.get('atr_pct', '?')}% 1h={snap.get('return_1h', '?')}% 4h={snap.get('return_4h', '?')}% 24h={snap.get('return_24h', '?')}% FR={_fmt_funding(snap.get('funding_rate'))}
{regime_str}

Challenge aggressively: overbought/oversold, adverse regime, funding cost, better entry timing.
Return confidence (0-100) where 100 = trade will definitely fail. Provide 3-5 counter-evidence points."""


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
