"""Paper Trader agent — turns open opportunities into autonomous paper proposals.

USER-AUTHORIZED autonomous behavior (paper only). Containment:
- Only runs when permission mode allows the `create_paper_proposal` paper_tool
  (PaperAuto / AllowEditing / Auto). Denied in Research / ReadOnly.
- Submits exclusively through the paper wrapper, which hard-codes paper_internal.
- Every order still passes the Risk Engine (kill switch, daily loss, etc.).
- Consumed opportunities are marked `proposed` for idempotency (no duplicates).

The position size is a small fixed paper quantity from instrument config, NOT an
LLM decision. The LLM is used only (optionally) for a short rationale string and
degrades gracefully when unavailable.
"""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.execution.paper_tools import create_paper_proposal
from aletheia.storage.repositories import (
    list_opportunities, update_opportunity, get_trade_proposal,
)
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.trading.market_data import latest_snapshot

# Conservative fixed paper notional target (USDT) per autonomous paper trade.
_PAPER_NOTIONAL_USDT = 25.0


@register_agent("paper_trader")
async def run_paper_trader(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
    outputs: list[str] = []
    errors: list[str] = []

    # Gate B: paper_tool permission. Logged even when denied so it is auditable.
    if not is_tool_permitted("create_paper_proposal", permission_mode):
        log_tool(run_id, "create_paper_proposal", "paper_tool", False)
        errors.append(f"create_paper_proposal not permitted in mode {permission_mode}")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    allowed_symbols = set(tradable_symbols_from_config(cfg))

    # Pick the most recent OPEN opportunity for an allowed symbol.
    opp = None
    for row in list_opportunities(limit=50):
        if row.get("status") == "open" and row.get("symbol") in allowed_symbols:
            opp = row
            break
    if opp is None:
        outputs.append("no_open_opportunity")
        return {"outputs": outputs, "errors": errors}

    symbol = opp["symbol"]
    snap = latest_snapshot(symbol)
    price = (snap or {}).get("price")
    if not price:
        errors.append(f"no_price_for:{symbol}")
        return {"outputs": outputs, "errors": errors}

    quantity = round(_PAPER_NOTIONAL_USDT / price, 6) or 0.000001
    direction = "long" if opp.get("direction_bias") in (None, "neutral", "long", "bullish") else "short"

    # Optional LLM rationale (graceful). Never blocks the trade.
    rationale, err = await llm_complete(
        system="You are a paper-trading assistant. In one sentence, state a neutral rationale "
               "for opening a small paper position based on the given opportunity. No advice.",
        user=f"Opportunity on {symbol}: {opp.get('thesis')}. Direction: {direction}.",
        max_tokens=120,
    )
    if err:
        errors.append(err)
        rationale = f"Autonomous paper trade from opportunity {opp['id']} ({direction} {symbol})."
    log_tool(run_id, "llm_complete", "research_tool", True)

    result = create_paper_proposal(
        symbol=symbol,
        market_type=opp.get("market", "spot"),
        direction=direction,
        quantity=quantity,
        price=float(price),
        execution_mode="paper_internal",
        opportunity_id=opp["id"],
        agent_rationale=(rationale or "")[:500],
    )
    log_tool(run_id, "create_paper_proposal", "paper_tool", True, output=result.get("proposal_id"))

    # Mark the opportunity consumed for idempotency regardless of risk outcome:
    # the proposal (approved or rejected) is the durable record of the attempt.
    update_opportunity(opp["id"], status="proposed")

    pid = result.get("proposal_id")
    outputs.append(f"proposal:{pid}:{result.get('status')}")
    return {"outputs": outputs, "errors": errors}
