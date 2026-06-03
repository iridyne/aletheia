"""Market Observer agent — observes market conditions and creates opportunities."""

from aletheia.agents import register_agent
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import Opportunity, KnowledgeItem
from aletheia.storage.repositories import insert_opportunity, insert_knowledge_item


@register_agent("market_observer")
async def run_market_observer(permission_mode: str = "Research", **kwargs) -> dict:
    """Observe market state and create opportunities if permitted."""
    outputs = []
    errors = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    # In MVP, create a sample observation since we don't have live data yet without Bitget
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    if is_tool_permitted("create_opportunity", permission_mode):
        opp = Opportunity(
            symbol="BTCUSDT",
            market="spot",
            direction_bias="neutral",
            thesis="Market observation — awaiting full market data integration",
            confidence="speculative",
            source_agent="market_observer",
        )
        insert_opportunity(opp)
        outputs.append(f"opportunity:{opp.id}")

    return {"outputs": outputs, "errors": errors}
