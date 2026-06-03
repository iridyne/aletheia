"""Market Observer agent — observes market conditions and creates opportunities."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import insert_opportunity
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.trading.market_data import latest_snapshot
from aletheia.config.loader import load_config


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

    # Ask the LLM for a market read (degrades gracefully if unavailable)
    thesis, err = await llm_complete(
        system="You are a crypto market observer. Given current market data, write a brief "
               "neutral observation (2-3 sentences) about regime/conditions for the listed symbols. "
               "Do not give financial advice.",
        user=f"Current market data:\n{context}\n\nWrite a short market observation.",
    )
    if err:
        errors.append(err)
        thesis = f"Market observation (LLM unavailable). Data:\n{context}"
    log_tool(run_id, "llm_complete", "research_tool", True)

    if is_tool_permitted("create_opportunity", permission_mode):
        opp = Opportunity(
            symbol=symbols[0] if symbols else "BTCUSDT",
            market="spot",
            direction_bias="neutral",
            thesis=thesis[:500] if thesis else context,
            confidence="speculative",
            source_agent="market_observer",
        )
        insert_opportunity(opp)
        log_tool(run_id, "create_opportunity", "research_tool", True, output=opp.id)
        outputs.append(f"opportunity:{opp.id}")

    return {"outputs": outputs, "errors": errors}
