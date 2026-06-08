"""Market Observer agent — observes market conditions and creates opportunities.

Per-symbol loop: each symbol gets its own LLM MarketRead call and its own
Opportunity row. This fixes the original "only BTC" bug where all symbol data
was sent to the LLM together but only symbols[0] was used for the resulting
opportunity.
"""

from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import insert_opportunity, list_strategies
from aletheia.trading.instruments import observed_instruments_from_config
from aletheia.config.loader import load_config

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}


class MarketRead(BaseModel):
    """Structured market read for a single symbol.

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

You will receive a verified market snapshot for ONE symbol containing:
latest price, multi-timeframe technical indicators (SMA/RSI/ATR/Bollinger/
momentum), recent close series. Use this snapshot as your single source of
truth — do not invent numbers or claim levels not in the data.

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


async def _analyze_symbol(
    symbol: str,
    market_type: str,
    permission_mode: str,
    run_id: str | None,
    linked_strategy_id: str | None,
    memory_block: str,
) -> tuple[str | None, str | None]:
    """Run market context build + LLM read for a single symbol.

    Returns (opportunity_id, error_str). Both may be None if the symbol is
    simply skipped (no permission, neutral signal, etc.).
    """
    from aletheia.trading.market_context import build_market_context

    try:
        context, ctx_debug = await build_market_context([symbol], market_type=market_type)
        log_tool(run_id, "build_market_context", "read_tool", True,
                 output=f"{symbol} fallback={ctx_debug.get('fallback')} "
                        f"errs={len(ctx_debug.get('errors', []))}")
    except Exception as e:
        from aletheia.trading.market_data import latest_snapshot
        snap = latest_snapshot(symbol)
        log_tool(run_id, "get_market_snapshot", "read_tool", True, output=symbol)
        if snap:
            context = f"{symbol}: price={snap['price']} 24h_change={snap.get('change_pct_24h')}%"
        else:
            context = f"{symbol}: no snapshot yet"
        return None, f"market_context_fallback:{symbol}:{e}"

    record_step(
        run_id, f"market_read_input:{symbol}",
        input_data=f"symbol={symbol} ctx_len={len(context)} memory={len(memory_block) > 0}",
        output_data=context[:1000],
    )

    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=f"Market data for {symbol}:\n{context}{memory_block}\n\nReturn the market read JSON.",
        schema=MarketRead,
        max_tokens=600,
    )
    log_tool(run_id, "llm_structured", "research_tool", True)

    direction = "neutral"
    confidence = "speculative"
    confidence_score: int | None = None
    rationale = ""
    observations: list[str] = []

    if err or not read:
        llm_error = err or "no_structured_read"
    else:
        llm_error = None
        d = str(read.get("direction_bias", "")).lower()
        c = str(read.get("confidence", "")).lower()
        if d in _VALID_DIRECTIONS:
            direction = d
        if c in _VALID_CONFIDENCE:
            confidence = c
        rationale = str(read.get("rationale", ""))
        cs = read.get("confidence_score")
        if isinstance(cs, (int, float)) and 0 <= cs <= 100:
            confidence_score = int(cs)
        obs = read.get("key_observations") or []
        if isinstance(obs, list):
            observations = [str(o)[:200] for o in obs[:4] if o]

    record_step(
        run_id, f"market_read_output:{symbol}",
        input_data=None,
        output_data=(
            f"direction={direction} confidence={confidence} "
            f"score={confidence_score} obs={len(observations)}"
        ),
    )

    if not is_tool_permitted("create_opportunity", permission_mode):
        return None, llm_error

    # Build thesis
    thesis_parts = []
    if rationale:
        thesis_parts.append(rationale)
    if observations:
        thesis_parts.append("Observations: " + " | ".join(observations))
    if confidence_score is not None:
        thesis_parts.append(f"score={confidence_score}/100")
    if thesis_parts:
        thesis = " ".join(thesis_parts)
    else:
        thesis = (
            f"No structured rationale produced (direction={direction}, "
            f"confidence={confidence}). Likely cause: LLM unavailable or "
            f"structured-output parse failure — see agent_runs/errors."
        )

    opp = Opportunity(
        symbol=symbol,
        market=market_type,
        direction_bias=direction,
        thesis=thesis,
        confidence=confidence,
        confidence_score=confidence_score,
        source_agent="market_observer",
        linked_strategy_id=linked_strategy_id,
        asset_class="crypto",
    )
    insert_opportunity(opp)
    log_tool(run_id, "create_opportunity", "research_tool", True, output=opp.id)
    return opp.id, llm_error


@register_agent("market_observer")
async def run_market_observer(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
    """Observe market state and create one Opportunity per symbol."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    instruments = observed_instruments_from_config(cfg)
    symbols = [i.symbol for i in instruments]  # canonical list for memory query

    # Find an active strategy to link all opportunities from this run.
    linked_strategy_id = None
    for s in list_strategies(limit=50):
        if s.get("state") in ("paper_running", "paper_candidate"):
            linked_strategy_id = s["id"]
            break

    # Experience-loop lessons block (shared across all symbols in one run).
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

    # Per-instrument loop: each instrument gets its own LLM call + Opportunity row.
    # instrument.market_type is the authoritative type; no more config-derived guessing.
    for instrument in instruments:
        try:
            opp_id, err = await _analyze_symbol(
                symbol=instrument.symbol,
                market_type=instrument.market_type,
                permission_mode=permission_mode,
                run_id=run_id,
                linked_strategy_id=linked_strategy_id,
                memory_block=memory_block,
            )
            if opp_id:
                outputs.append(f"opportunity:{opp_id}")
            if err:
                errors.append(f"{instrument}:{err}")
        except Exception as e:
            errors.append(f"{instrument}:unexpected:{e}")

    return {"outputs": outputs, "errors": errors}
