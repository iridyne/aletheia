"""Paper Trader agent — turns open opportunities into autonomous paper proposals.

USER-AUTHORIZED autonomous behavior (paper only). Containment:
- Only runs when permission mode is Paper or Full. Denied in Observe.
- Submits exclusively through the paper wrapper, which rejects any non-paper mode
  (only paper_internal / bitget_demo allowed) — the live path is unreachable here.
- Every order still passes the Risk Engine (kill switch, daily loss, etc.).
- Consumed opportunities are marked `proposed` for idempotency (no duplicates).

Execution target is `exchange.paper_execution_mode`: `bitget_demo` places real orders
on Bitget's demo (simulated) API; `paper_internal` fills locally. Bitget demo only
supports USDT futures, so paper trades are submitted as `usdt_futures`.

The position size is a small fixed paper quantity from instrument config, NOT an
LLM decision. The LLM is used only (optionally) for a short rationale string and
degrades gracefully when unavailable.
"""

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.execution.paper_tools import create_paper_proposal
from aletheia.storage.repositories import (
    list_opportunities, update_opportunity, list_strategies,
)
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.trading.market_data import latest_snapshot
from aletheia.data.symbols import to_canonical

# Conservative fixed paper notional target (USDT) per autonomous paper trade.
_PAPER_NOTIONAL_USDT = 25.0

# Confidence ranking for the min_confidence_to_trade gate.
_CONFIDENCE_RANK = {"speculative": 0, "medium": 1, "high": 2}


@register_agent("paper_trader")
async def run_paper_trader(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    outputs: list[str] = []
    errors: list[str] = []

    cfg = load_config()

    if not is_tool_permitted("create_paper_proposal", permission_mode):
        log_tool(run_id, "create_paper_proposal", "paper", False)
        errors.append(f"create_paper_proposal not permitted in mode {permission_mode}")
        return {"outputs": outputs, "errors": errors}
    # tradable_symbols are Bitget format (BTCUSDT); opportunities are canonical
    # (BTC/USDT) — normalise both sides before matching.
    allowed_symbols = {to_canonical(s) for s in tradable_symbols_from_config(cfg)}

    # Pick the most recent OPEN crypto opportunity for an allowed symbol.
    # equity and prediction asset_classes are observation-only — never traded.
    opp = None
    for row in list_opportunities(limit=50):
        ac = row.get("asset_class") or "crypto"  # NULL = legacy crypto row
        if ac != "crypto":
            continue
        if row.get("status") == "open" and row.get("symbol") in allowed_symbols:
            opp = row
            break
    if opp is None:
        outputs.append("no_open_opportunity")
        return {"outputs": outputs, "errors": errors}

    symbol = opp["symbol"]

    # Entry gate: only trade a clear direction at sufficient confidence. Non-traded
    # signals are NOT discarded — they're marked "reviewed" and kept for analysis.
    bias = (opp.get("direction_bias") or "neutral").lower()
    direction = "long" if bias in ("long", "bullish") else ("short" if bias in ("short", "bearish") else None)
    confidence = (opp.get("confidence") or "speculative").lower()
    min_conf = cfg.trading.min_confidence_to_trade
    conf_ok = _CONFIDENCE_RANK.get(confidence, 0) >= _CONFIDENCE_RANK.get(min_conf, 1)

    if direction is None or not conf_ok:
        skip_reason = "neutral_bias" if direction is None else f"confidence_below_{min_conf}:{confidence}"
        update_opportunity(opp["id"], status="reviewed")  # keep the signal, just don't trade it
        log_tool(run_id, "create_paper_proposal", "paper", False, output=skip_reason)
        outputs.append(f"skipped:{opp['id']}:{skip_reason}")
        return {"outputs": outputs, "errors": errors}

    snap = latest_snapshot(symbol)
    price = (snap or {}).get("price")
    if not price:
        errors.append(f"no_price_for:{symbol}")
        return {"outputs": outputs, "errors": errors}

    price = float(price)
    quantity = round(_PAPER_NOTIONAL_USDT / price, 6) or 0.000001
    # Protective stop from config %, on the correct side of entry for the direction.
    stop_pct = cfg.trading.default_stop_loss_pct
    if direction == "long":
        stop_price = round(price * (1 - stop_pct / 100.0), 8)
    else:
        stop_price = round(price * (1 + stop_pct / 100.0), 8)

    rationale = f"Autonomous paper trade from opportunity {opp['id']} ({direction} {symbol}, conf={confidence})."

    # Resolve strategy_id: prefer opportunity's linked_strategy_id, else find an
    # active (paper_running/paper_candidate) strategy to bind this trade to.
    strategy_id = opp.get("linked_strategy_id")
    if not strategy_id:
        active_states = {"paper_candidate", "paper_running"}
        for s in list_strategies(limit=50):
            if s.get("state") in active_states:
                strategy_id = s["id"]
                break

    # Bitget demo only supports USDT futures (no spot), so paper trades execute as
    # usdt_futures. execution_mode is config-driven: bitget_demo (real demo API) or
    # paper_internal (local sim fallback).
    result = create_paper_proposal(
        symbol=symbol,
        market_type="usdt_futures",
        direction=direction,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        execution_mode=cfg.exchange.paper_execution_mode,
        strategy_id=strategy_id,
        opportunity_id=opp["id"],
        agent_rationale=rationale[:500],
    )
    log_tool(run_id, "create_paper_proposal", "paper", True, output=result.get("proposal_id"))

    # Mark the opportunity consumed for idempotency regardless of risk outcome:
    # the proposal (approved or rejected) is the durable record of the attempt.
    update_opportunity(opp["id"], status="proposed")

    pid = result.get("proposal_id")
    outputs.append(f"proposal:{pid}:{result.get('status')}")
    return {"outputs": outputs, "errors": errors}
