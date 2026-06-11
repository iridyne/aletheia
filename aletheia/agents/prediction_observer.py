"""Prediction Observer agent — monitors Polymarket prediction markets.

Fetches active markets from Polymarket's public Gamma API (no auth needed),
asks the LLM to identify notable probability trends, and creates
asset_class="prediction" Opportunities for informational tracking.

Design choices:
- asset_class = "prediction" — paper_trader IGNORES these (observation-only).
- direction_bias maps to probability momentum:
    "long" = YES probability rising (bet is gaining)
    "short" = YES probability falling (bet is losing ground)
    "neutral" = stable probability
- symbol = market question slug (truncated to 80 chars for readability)
- market = "prediction"
- Degrades fully: any Polymarket API error → logs and returns empty.
"""

from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import insert_opportunity, list_strategies
from aletheia.config.loader import load_config
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}


class PredictionMarketRead(BaseModel):
    """Structured read for a prediction market.

    direction_bias here means probability momentum, not price direction:
    - "long"    = YES probability is rising (the event is becoming more likely)
    - "short"   = YES probability is falling (the event is becoming less likely)
    - "neutral" = probability is stable
    """
    direction_bias: Literal["long", "short", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "speculative"] = "speculative"
    confidence_score: int = Field(
        50, ge=0, le=100,
        description="0=no trend signal, 50=balanced, 100=strong trend signal"
    )
    rationale: str = ""
    key_observations: list[str] = Field(
        default_factory=list,
        description="2-3 bullets citing probability levels and volume"
    )


_SYSTEM_PROMPT = """\
You are a prediction market analyst for Aletheia.

You will receive a table of active Polymarket prediction markets. Each row shows:
- The YES probability (0–100%)
- 24h probability change (positive = rising, negative = falling)
- Trading volume in USD
- Resolution date

Your task: identify the 1-3 most notable markets by probability momentum.
For each notable market, output a structured read.

direction_bias (probability momentum interpretation):
- "long"    = YES probability is rising meaningfully (strong trend toward resolution YES)
- "short"   = YES probability is falling meaningfully (strong trend toward resolution NO)
- "neutral" = probability is stable or changes are noise

confidence:
- "high"    = large 24h probability shift (>10pp) AND high volume (>$100k)
- "medium"  = moderate shift (5-10pp) OR decent volume (>$10k)
- "speculative" = small shift or thin market

key_observations: 2-3 bullets citing probability values and volume.

Focus on markets most relevant to financial/macro themes when possible.
This is informational analysis only — not trading advice."""


@register_agent("prediction_observer")
async def run_prediction_observer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Fetch Polymarket data and create prediction Opportunities for tracking."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    categories = cfg.market_data.observed_prediction_markets
    if not categories:
        outputs.append("no_prediction_markets_configured")
        return {"outputs": outputs, "errors": errors}

    # Fetch markets from Polymarket
    from aletheia.data.polymarket_provider import fetch_active_markets, format_prediction_context
    try:
        markets = await fetch_active_markets(categories=categories, limit=15)
        log_tool(run_id, "fetch_prediction_markets", "observe", True,
                 output=f"markets:{len(markets)}")
    except Exception as e:
        errors.append(f"polymarket_fetch_failed:{e}")
        return {"outputs": outputs, "errors": errors}

    if not markets:
        outputs.append("no_active_markets")
        return {"outputs": outputs, "errors": errors}

    context = format_prediction_context(markets)
    record_step(
        run_id, "prediction_read_input",
        input_data=f"categories={categories} markets={len(markets)}",
        output_data=context[:1200],
    )

    # Find active strategy
    linked_strategy_id = None
    for s in list_strategies(limit=50):
        if is_active_paper_strategy(s):
            linked_strategy_id = s["id"]
            break

    # Ask LLM to identify notable markets — returns a list of reads
    # We ask for a single structured read summarizing the most notable market
    # to keep token cost low. If multiple markets are notable, the agent can
    # be extended to loop.
    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=(
            f"Prediction market data:\n{context}\n\n"
            "Identify the single most notable market by probability momentum "
            "and return the structured read JSON for it."
        ),
        schema=PredictionMarketRead,
        max_tokens=500,
    )
    log_tool(run_id, "llm_structured", "observe", True)

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
        rationale = str(read.get("rationale", ""))
        cs = read.get("confidence_score")
        if isinstance(cs, (int, float)) and 0 <= cs <= 100:
            confidence_score = int(cs)
        obs = read.get("key_observations") or []
        if isinstance(obs, list):
            observations = [str(o)[:200] for o in obs[:3] if o]

    record_step(
        run_id, "prediction_read_output",
        input_data=None,
        output_data=f"direction={direction} confidence={confidence} score={confidence_score}",
    )

    if not is_tool_permitted("create_opportunity", permission_mode):
        return {"outputs": outputs, "errors": errors}

    # Use the top market question as symbol (truncated slug)
    top_market = markets[0] if markets else None
    symbol_slug = "POLYMARKET"
    if top_market:
        q = top_market.question
        symbol_slug = (q[:77] + "...") if len(q) > 80 else q

    thesis_parts = []
    if rationale:
        thesis_parts.append(rationale)
    if observations:
        thesis_parts.append("Observations: " + " | ".join(observations))
    if confidence_score is not None:
        thesis_parts.append(f"score={confidence_score}/100")
    if top_market:
        thesis_parts.append(
            f"Top market YES={top_market.yes_probability * 100:.1f}% "
            f"vol=${top_market.volume_usd:,.0f}"
        )
    thesis = " ".join(thesis_parts) if thesis_parts else (
        f"Prediction market observation (direction={direction}, "
        f"confidence={confidence})"
    )

    opp = Opportunity(
        symbol=symbol_slug,
        market="prediction",
        direction_bias=direction,
        thesis=thesis,
        confidence=confidence,
        confidence_score=confidence_score,
        source_agent="prediction_observer",
        linked_strategy_id=linked_strategy_id,
        asset_class="prediction",
    )
    insert_opportunity(opp)
    log_tool(run_id, "create_opportunity", "observe", True, output=opp.id)
    outputs.append(f"opportunity:{opp.id}")

    return {"outputs": outputs, "errors": errors}
