"""Market Observer agent — observes market conditions and creates opportunities."""

from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import insert_opportunity, list_strategies
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.config.loader import load_config

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}


class MarketRead(BaseModel):
    """Structured market read.

    Two design choices vs. the previous schema:
      1. `confidence_score` (0-100) sits alongside the categorical `confidence`.
         This makes "neutral / speculative" no longer a free hideout: the model
         must commit a number on the spectrum and explain why.
      2. `key_observations` forces ≥1 specific data citation from the snapshot.
         Empty list → the model is implicitly admitting "I had nothing concrete".
    """
    direction_bias: Literal["long", "short", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "speculative"] = "speculative"
    confidence_score: int = Field(
        50, ge=0, le=100,
        description="0=no idea, 50=balanced, 100=overwhelming evidence"
    )
    rationale: str = ""
    key_observations: list[str] = Field(
        default_factory=list,
        description="2-4 bullets citing specific numbers from the snapshot"
    )


_SYSTEM_PROMPT = """\
You are a crypto market observer for Aletheia paper-trading.

You will receive a verified market snapshot containing: latest prices, multi-
timeframe technical indicators (SMA/RSI/ATR/Bollinger/momentum), recent close
series, and cross-asset relative strength. Use this snapshot as your single
source of truth — do not invent numbers or claim levels not in the data.

Output a structured market read. Calibration:

- direction_bias:
  - "long" if multiple indicators across timeframes point up — e.g. price > SMA_50,
    RSI 50-70 with positive slope, positive 4h momentum, leading relative-strength rank.
  - "short" mirror.
  - "neutral" only when signals genuinely conflict OR the data is too thin
    (mention which in rationale). Do not default to neutral; commit if you have evidence.

- confidence:
  - "high" — multi-timeframe agreement and clear directional signal.
  - "medium" — 1-2 timeframe agreement, or mild signal.
  - "speculative" — noisy / conflicting / thin data. Speculative is honest
    when warranted, not a default.

- confidence_score: 0-100. 50 = genuinely balanced. Choose a non-50 value
  if you have any actionable read at all.

- key_observations: 2-4 short bullets, each citing a specific number from
  the snapshot (e.g. "BTC 1h RSI 68 + price > SMA_20: short-term overbought").
  Empty list signals "no concrete evidence to point at".

Not financial advice."""


@register_agent("market_observer")
async def run_market_observer(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
    """Observe market state and create opportunities if permitted."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    symbols = tradable_symbols_from_config(cfg)
    cfg_markets = cfg.exchange.enabled_markets
    market_type = "usdt_futures" if cfg_markets.get("usdt_futures") and not cfg_markets.get("spot") else "spot"

    # === New: build a verified multi-timeframe market context ===
    # Replaces the previous one-line "BTC 24h_change=0.05%" feed that caused
    # every opportunity to come back neutral / speculative. Falls back to the
    # legacy 1-liner internally if OHLCV fetch fails.
    try:
        from aletheia.trading.market_context import build_market_context
        context, ctx_debug = await build_market_context(symbols, market_type=market_type)
        log_tool(run_id, "build_market_context", "read_tool", True,
                 output=f"fallback={ctx_debug.get('fallback')} "
                        f"errs={len(ctx_debug.get('errors', []))}")
    except Exception as e:
        # Truly catastrophic — degrade further to legacy snapshot path so the
        # agent still produces something and LLM can answer honestly.
        from aletheia.trading.market_data import latest_snapshot
        context_lines = []
        for sym in symbols:
            snap = latest_snapshot(sym)
            log_tool(run_id, "get_market_snapshot", "read_tool", True, output=sym)
            if snap:
                context_lines.append(f"{sym}: price={snap['price']} 24h_change={snap.get('change_pct_24h')}%")
            else:
                context_lines.append(f"{sym}: no snapshot yet")
        context = "\n".join(context_lines)
        errors.append(f"market_context_fallback: {e}")

    # Experience loop (§6): pull relevant high-confidence prior lessons and
    # inject them as ADVISORY context. Read-only, quality-gated, degrades to nothing.
    memory_block = ""
    try:
        from aletheia.knowledge.retrieval import retrieve_lessons, format_memory_block
        lessons = retrieve_lessons(
            query=f"{' '.join(symbols)} market regime risk funding volatility",
            k=3,
            item_types=["research_note", "post_trade_review", "dreaming_log"],
        )
        memory_block = format_memory_block(lessons)
        log_tool(run_id, "retrieve_lessons", "read_tool", True, output=f"lessons:{len(lessons)}")
    except Exception:
        pass

    # Reasoning-trace breadcrumb so /api/agent-runs/{id}/steps shows what the
    # model actually saw — kills the "AI is doing nothing" perception when
    # output is honest neutral on flat markets.
    record_step(
        run_id, "market_read_input",
        input_data=f"symbols={symbols} ctx_len={len(context)} memory_lessons="
                   f"{len(memory_block) > 0}",
        output_data=context[:1500],  # truncate; full context can be 2-5kb
    )

    # Ask the LLM for a STRUCTURED market read (degrades gracefully if unavailable).
    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=f"Market data:\n{context}{memory_block}\n\nReturn the market read JSON.",
        schema=MarketRead,
        max_tokens=600,  # bumped from 400 — observations + rationale need room
    )
    log_tool(run_id, "llm_structured", "research_tool", True)

    direction = "neutral"
    confidence = "speculative"
    confidence_score: int | None = None
    rationale = ""
    observations: list[str] = []
    if err or not read:
        errors.append(err or "no_structured_read")
    else:
        d = str(read.get("direction_bias", "")).lower()
        c = str(read.get("confidence", "")).lower()
        if d in _VALID_DIRECTIONS:
            direction = d
        if c in _VALID_CONFIDENCE:
            confidence = c
        rationale = str(read.get("rationale", ""))[:500]
        cs = read.get("confidence_score")
        if isinstance(cs, (int, float)) and 0 <= cs <= 100:
            confidence_score = int(cs)
        obs = read.get("key_observations") or []
        if isinstance(obs, list):
            observations = [str(o)[:200] for o in obs[:4] if o]

    # Trace the structured output too — completes the read→decision picture.
    record_step(
        run_id, "market_read_output",
        input_data=None,
        output_data=(
            f"direction={direction} confidence={confidence} "
            f"score={confidence_score} obs={len(observations)}"
        ),
    )

    if is_tool_permitted("create_opportunity", permission_mode):
        symbol = symbols[0] if symbols else "BTCUSDT"
        # Bind to an active (tradable) strategy so the causal chain
        # opportunity -> proposal -> order -> fill -> position carries a strategy_id.
        # Honest: leave None if no active strategy exists.
        linked_strategy_id = None
        for s in list_strategies(limit=50):
            if s.get("state") in ("paper_running", "paper_candidate"):
                linked_strategy_id = s["id"]
                break

        # Compose thesis: rationale (model's narrative) + observations (citations)
        # + score so a downstream reader sees the full read at a glance.
        thesis_parts = []
        if rationale:
            thesis_parts.append(rationale)
        if observations:
            thesis_parts.append("Observations: " + " | ".join(observations))
        if confidence_score is not None:
            thesis_parts.append(f"score={confidence_score}/100")
        thesis = " ".join(thesis_parts)[:500] if thesis_parts else context[:500]

        opp = Opportunity(
            symbol=symbol,
            market=market_type,
            direction_bias=direction,
            thesis=thesis,
            confidence=confidence,
            source_agent="market_observer",
            linked_strategy_id=linked_strategy_id,
        )
        insert_opportunity(opp)
        log_tool(run_id, "create_opportunity", "research_tool", True, output=opp.id)
        outputs.append(f"opportunity:{opp.id}")

    return {"outputs": outputs, "errors": errors}
