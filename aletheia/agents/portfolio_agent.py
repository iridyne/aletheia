"""Portfolio Agent — summarizes positions/PnL and writes a portfolio snapshot."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted


@register_agent("portfolio_agent")
async def run_portfolio_agent(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Compute a portfolio snapshot from current open positions."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_positions", permission_mode):
        errors.append("get_positions not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    from aletheia.portfolio.pnl import compute_and_store_snapshot
    log_tool(run_id, "get_positions", "observe", True)
    snap = compute_and_store_snapshot()
    log_tool(run_id, "get_account_summary", "observe", True, output=snap.id)
    outputs.append(f"portfolio_snapshot:{snap.id}")

    return {"outputs": outputs, "errors": errors}
