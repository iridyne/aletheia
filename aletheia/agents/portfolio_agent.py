"""Portfolio Agent — summarizes positions/PnL and writes a portfolio snapshot."""

import time

from aletheia.agents import register_agent
from aletheia.agents.helpers import elapsed_ms, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted


@register_agent("portfolio_agent")
async def run_portfolio_agent(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Compute a portfolio snapshot from current open positions."""
    outputs: list[str] = []
    errors: list[str] = []
    artifact_refs: list[dict[str, str]] = []

    if not is_tool_permitted("get_positions", permission_mode):
        errors.append("get_positions not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    from aletheia.portfolio.pnl import compute_and_store_snapshot
    log_tool(run_id, "get_positions", "observe", True)
    started_at = time.monotonic()
    snap = compute_and_store_snapshot()
    log_tool(
        run_id,
        "get_account_summary",
        "observe",
        True,
        output=snap.id,
        duration_ms=elapsed_ms(started_at),
    )
    outputs.append(f"portfolio_snapshot:{snap.id}")
    artifact_refs.append({"artifact_type": "portfolio_snapshot", "artifact_id": snap.id})

    return {"outputs": outputs, "errors": errors, "output_artifacts": artifact_refs}
