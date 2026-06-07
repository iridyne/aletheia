"""Market Observer agent — observes market conditions and creates opportunities."""

from pydantic import BaseModel

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import insert_opportunity
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.trading.market_data import latest_snapshot
from aletheia.config.loader import load_config

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}


class MarketRead(BaseModel):
    """Structured market read the observer asks the LLM to produce."""
    direction_bias: str = "neutral"
    confidence: str = "speculative"
    rationale: str = ""


@register_agent("market_observer")
async def run_market_observer(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
    """Observe market state (from stored snapshots) and create opportunities if permitted."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    symbols = tradable_symbols_from_config(cfg)

    # Gather real market context from stored snapshots
    context_lines = []
    for sym in symbols:
        snap = latest_snapshot(sym)
        log_tool(run_id, "get_market_snapshot", "read_tool", True, output=sym)
        if snap:
            context_lines.append(f"{sym}: price={snap['price']} 24h_change={snap.get('change_pct_24h')}%")
        else:
            context_lines.append(f"{sym}: no snapshot yet")
    context = "\n".join(context_lines)

    # Ask the LLM for a STRUCTURED market read (degrades gracefully if unavailable).
    read, err = await llm_structured(
        system="You are a crypto market observer. Given current market data, return a JSON object "
               "with direction_bias (one of long/short/neutral), confidence (one of "
               "high/medium/speculative), and a brief rationale. Be conservative: use neutral / "
               "speculative unless the data clearly supports a directional read. No financial advice.",
        user=f"Current market data:\n{context}\n\nReturn the market read JSON.",
        schema=MarketRead,
    )
    log_tool(run_id, "llm_structured", "research_tool", True)

    direction = "neutral"
    confidence = "speculative"
    rationale = ""
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

    if is_tool_permitted("create_opportunity", permission_mode):
        symbol = symbols[0] if symbols else "BTCUSDT"
        cfg_markets = cfg.exchange.enabled_markets
        market_type = "usdt_futures" if cfg_markets.get("usdt_futures") and not cfg_markets.get("spot") else "spot"
        opp = Opportunity(
            symbol=symbol,
            market=market_type,
            direction_bias=direction,
            thesis=(rationale or context)[:500],
            confidence=confidence,
            source_agent="market_observer",
        )
        insert_opportunity(opp)
        log_tool(run_id, "create_opportunity", "research_tool", True, output=opp.id)
        outputs.append(f"opportunity:{opp.id}")

    return {"outputs": outputs, "errors": errors}
