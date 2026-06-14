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

Money Loop Reset: If the bound strategy carries an executable RuleSet in
parameters["ruleset"], the paper trader verifies the entry signal against current
OHLCV bars BEFORE submitting the proposal. This replaces the old "trade on any
opportunity confidence" behavior for rule-equipped strategies.
"""
from __future__ import annotations

import json as _json
import logging

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.execution.paper_tools import create_paper_proposal
from aletheia.storage.repositories import (
    get_strategy,
    list_opportunities,
    update_opportunity,
    list_strategies,
    get_ohlcv_candles,
)
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.trading.market_data import latest_snapshot
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy, is_paper_trading_paused
from aletheia.data.symbols import to_canonical

logger = logging.getLogger("aletheia.agents.paper_trader")

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
    # Symbol filter: single list, semantics depend on mode.
    filter_mode = cfg.risk.symbol_filter_mode
    filter_set = {to_canonical(s) for s in tradable_symbols_from_config(cfg)}
    if filter_mode == "blacklist":
        def _symbol_allowed(sym: str) -> bool:
            return to_canonical(sym) not in filter_set
    else:
        def _symbol_allowed(sym: str) -> bool:
            return not filter_set or to_canonical(sym) in filter_set

    # Pick the most recent OPEN crypto opportunity for an allowed symbol.
    # equity and prediction asset_classes are observation-only — never traded.
    # Universe-sourced opportunities with expired TTL or execution_eligible=False are skipped.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    opp = None
    skipped_reasons: list[str] = []
    for row in list_opportunities(limit=50):
        ac = row.get("asset_class") or "crypto"  # NULL = legacy crypto row
        if ac != "crypto":
            skipped_reasons.append(f"skip:{row.get('symbol','?')}:asset_class={ac}")
            continue
        if not row.get("execution_eligible", True):
            skipped_reasons.append(f"skip:{row.get('symbol','?')}:execution_eligible=False")
            continue
        if row.get("status") != "open" or not _symbol_allowed(row.get("symbol", "")):
            continue
        # Universe guards: skip non-eligible or TTL-expired universe members
        if row.get("universe_member_id"):
            from aletheia.storage.repositories import get_universe_member
            member = get_universe_member(row["universe_member_id"])
            if member:
                if not member.get("execution_eligible", True):
                    skipped_reasons.append(f"skip:{row.get('symbol','?')}:execution_eligible=False")
                    continue
                if member.get("expires_at") and member["expires_at"] < now_iso:
                    skipped_reasons.append(f"skip:{row.get('symbol','?')}:ttl_expired")
                    continue
        opp = row
        break
    if skipped_reasons:
        outputs.extend(skipped_reasons)
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
    if strategy_id:
        linked_strategy = get_strategy(strategy_id)
        if linked_strategy and is_paper_trading_paused(linked_strategy):
            update_opportunity(opp["id"], status="reviewed")
            skip_reason = f"paused_strategy:{strategy_id}"
            log_tool(run_id, "create_paper_proposal", "paper", False, output=skip_reason)
            outputs.append(f"skipped:{opp['id']}:{skip_reason}")
            return {"outputs": outputs, "errors": errors}
    if not strategy_id:
        for s in list_strategies(limit=50):
            if is_active_paper_strategy(s):
                strategy_id = s["id"]
                break

    # ── Money Loop: RuleSet entry signal verification ──
    # If the bound strategy has executable rules, validate that the entry signal
    # fires on current OHLCV data BEFORE committing to a proposal.
    if strategy_id:
        bound_strategy = get_strategy(strategy_id)
        if bound_strategy:
            params_raw = bound_strategy.get("parameters") or "{}"
            try:
                params = _json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            except (ValueError, TypeError):
                params = {}
            ruleset_json = params.get("ruleset")
            if ruleset_json:
                try:
                    from aletheia.strategy.dsl import RuleSet
                    from aletheia.strategy.evaluator import BarData, evaluate_at_bar

                    ruleset = RuleSet.from_json(ruleset_json)
                    tf = params.get("timeframe") or "1h"
                    candles = get_ohlcv_candles(symbol, tf, limit=200)
                    if candles:
                        bars = [
                            BarData(
                                ts=int(c["ts"]), open=float(c["open"]),
                                high=float(c["high"]), low=float(c["low"]),
                                close=float(c["close"]), volume=float(c["volume"]),
                            )
                            for c in candles
                        ]
                        sig = evaluate_at_bar(ruleset, bars, len(bars) - 1)
                        if sig["entry"] is not True:
                            update_opportunity(opp["id"], status="reviewed")
                            skip_reason = f"ruleset_entry_not_met:{sig['entry']}"
                            log_tool(run_id, "create_paper_proposal", "paper", False, output=skip_reason)
                            outputs.append(f"skipped:{opp['id']}:{skip_reason}")
                            return {"outputs": outputs, "errors": errors}
                        outputs.append("ruleset_entry_confirmed")
                except Exception as e:
                    logger.debug("RuleSet validation skipped: %s", e)

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
