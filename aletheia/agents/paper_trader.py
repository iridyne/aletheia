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

PaperTrader v2: Uses ranked candidate selection via paper_trader_ranker. All eligible
opportunities are scored and the best candidate is selected, instead of first-match.
Factor feedback from closed trades influences scoring (avoid losing factors, prefer
winning ones). RuleSet entry validation is part of the ranking pipeline.
"""
from __future__ import annotations

import logging

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool
from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.execution.paper_tools import create_paper_proposal
from aletheia.storage.repositories import update_opportunity
from aletheia.trading.market_data import latest_snapshot

logger = logging.getLogger("aletheia.agents.paper_trader")

_PAPER_NOTIONAL_USDT = 25.0


@register_agent("paper_trader")
async def run_paper_trader(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    outputs: list[str] = []
    errors: list[str] = []

    cfg = load_config()

    if not is_tool_permitted("create_paper_proposal", permission_mode):
        log_tool(run_id, "create_paper_proposal", "paper", False)
        errors.append(f"create_paper_proposal not permitted in mode {permission_mode}")
        return {"outputs": outputs, "errors": errors}

    # ── Ranked candidate selection (v2) ──
    candidates = build_paper_candidates(limit=50)

    # Mark deterministically-skipped opportunities as reviewed (they won't change
    # on next tick: neutral_bias, low confidence, paused strategy, blocked factor).
    # This preserves the v1 behavior of consuming unactionable signals.
    _PERMANENT_SKIP_PREFIXES = ("neutral_bias", "confidence_below_", "paused_strategy:", "blocked_factor:")
    skipped = [c for c in candidates if c.get("skip_reason")]
    for c in skipped:
        reason = c.get("skip_reason", "")
        if any(reason.startswith(p) for p in _PERMANENT_SKIP_PREFIXES):
            update_opportunity(c["opportunity_id"], status="reviewed")
    for c in skipped[:10]:
        outputs.append(f"skip:{c.get('symbol','?')}:{c['skip_reason']}")

    selected = select_best_candidate(candidates)

    # Edge Lab: persist top-N candidates as shadow trades for counterfactual analysis.
    try:
        from aletheia.analytics.shadow_trades import persist_shadow_batch
        eligible = [c for c in candidates if not c.get("skip_reason")]
        persist_shadow_batch(eligible, selected, top_n=5)
    except Exception as _shadow_err:
        logger.debug("shadow trade persistence failed: %s", _shadow_err)

    if selected is None:
        outputs.append("no_ranked_candidate")
        return {"outputs": outputs, "errors": errors}

    # Use the selected candidate
    symbol = selected["symbol"]
    direction = selected["direction"]
    opp_id = selected["opportunity_id"]
    strategy_id = selected.get("strategy_id")

    snap = latest_snapshot(symbol)
    price = (snap or {}).get("price")
    if not price:
        errors.append(f"no_price_for:{symbol}")
        return {"outputs": outputs, "errors": errors}

    price = float(price)
    quantity = round(_PAPER_NOTIONAL_USDT / price, 6) or 0.000001

    # Protective stop from config %
    stop_pct = cfg.trading.default_stop_loss_pct
    if direction == "long":
        stop_price = round(price * (1 - stop_pct / 100.0), 8)
    else:
        stop_price = round(price * (1 + stop_pct / 100.0), 8)

    # Build rationale with scoring metadata
    rationale = (
        f"Autonomous paper trade from ranked candidate.\n"
        f"opportunity_id={opp_id}\n"
        f"trade_score={selected.get('trade_score')}\n"
        f"factor_key={selected.get('factor_key')}\n"
        f"factor_status={selected.get('factor_status')}\n"
        f"factor_expectancy={selected.get('factor_expectancy')}\n"
        f"universe_score={selected.get('universe_total_score')}\n"
        f"tradability_score={selected.get('tradability_score')}\n"
        f"late_entry_penalty={selected.get('late_entry_penalty')}\n"
        f"ruleset_entry_confirmed={selected.get('ruleset_entry_result')}"
    )

    result = create_paper_proposal(
        symbol=symbol,
        market_type="usdt_futures",
        direction=direction,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        execution_mode=cfg.exchange.paper_execution_mode,
        strategy_id=strategy_id,
        opportunity_id=opp_id,
        agent_rationale=rationale[:500],
    )
    log_tool(run_id, "create_paper_proposal", "paper", True, output=result.get("proposal_id"))

    # Mark the opportunity consumed for idempotency
    update_opportunity(opp_id, status="proposed")

    pid = result.get("proposal_id")
    outputs.append(f"proposal:{pid}:{result.get('status')}")
    return {"outputs": outputs, "errors": errors}
