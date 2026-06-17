"""Paper Trader agent — turns open opportunities and RuleSet signals into paper proposals.

USER-AUTHORIZED autonomous behavior (paper only). Containment:
- Only runs when permission mode is Paper or Full. Denied in Observe.
- Submits exclusively through the paper wrapper, which rejects any non-paper mode
  (only paper_internal / bitget_demo allowed) — the live path is unreachable here.
- Every order still passes the Risk Engine (kill switch, daily loss, etc.).
- Consumed opportunities are marked `proposed` for idempotency (no duplicates).

Execution target is `exchange.paper_execution_mode`: `bitget_demo` places real orders
on Bitget's demo (simulated) API; `paper_internal` fills locally. Bitget demo only
supports USDT futures, so paper trades are submitted as `usdt_futures`.

PaperTrader v3 (RuleSet-Driven):
  Path A — RuleSet signals: for each paper_running strategy with a compiled RuleSet,
    evaluate_at_bar() produces entry/exit signals directly from OHLCV bars.
    Multiple strategies can fire simultaneously (multi-strategy parallel).
    Exit signals close existing positions. Entry signals open new ones.
  Path B — Opportunity-driven (fallback): original ranked candidate selection from
    market_observer opportunities. Runs after Path A to consume remaining signals.
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
from aletheia.trading.ruleset_scanner import RuleSetSignal, scan_active_rulesets

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

    execution_mode = cfg.exchange.paper_execution_mode

    # ── Path A: RuleSet-driven signals (multi-strategy parallel) ──────────────
    try:
        ruleset_signals = scan_active_rulesets(execution_mode=execution_mode)
    except Exception as e:
        logger.warning("ruleset scan failed: %s", e)
        ruleset_signals = []

    for sig in ruleset_signals:
        try:
            if sig.signal_type == "exit" and sig.position_id:
                out = _handle_ruleset_exit(sig, run_id)
            else:
                out = _handle_ruleset_entry(sig, cfg, execution_mode, run_id)
            if out:
                outputs.append(out)
        except Exception as e:
            errors.append(f"ruleset_signal_error:{sig.symbol}:{e}")

    # ── Path B: Opportunity-driven (fallback, ranked) ─────────────────────────
    candidates = build_paper_candidates(limit=50)

    _PERMANENT_SKIP_PREFIXES = ("neutral_bias", "confidence_below_", "paused_strategy:", "blocked_factor:", "equity_shadow_track")
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
        if not outputs:
            outputs.append("no_ranked_candidate")
        return {"outputs": outputs, "errors": errors}

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

    stop_pct = cfg.trading.default_stop_loss_pct
    if direction == "long":
        stop_price = round(price * (1 - stop_pct / 100.0), 8)
    else:
        stop_price = round(price * (1 + stop_pct / 100.0), 8)

    rationale = (
        f"Autonomous paper trade from ranked candidate.\n"
        f"opportunity_id={opp_id}\n"
        f"trade_score={selected.get('trade_score')}\n"
        f"factor_key={selected.get('factor_key')}\n"
        f"factor_status={selected.get('factor_status')}\n"
        f"ruleset_entry_confirmed={selected.get('ruleset_entry_result')}"
    )

    result = create_paper_proposal(
        symbol=symbol,
        market_type="usdt_futures",
        direction=direction,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        opportunity_id=opp_id,
        agent_rationale=rationale[:500],
    )
    log_tool(run_id, "create_paper_proposal", "paper", True, output=result.get("proposal_id"))
    update_opportunity(opp_id, status="proposed")

    pid = result.get("proposal_id")
    outputs.append(f"proposal:{pid}:{result.get('status')}")
    return {"outputs": outputs, "errors": errors}


# ── RuleSet signal handlers ───────────────────────────────────────────────────

def _handle_ruleset_exit(sig: RuleSetSignal, run_id: str | None) -> str | None:
    """Close a position triggered by RuleSet exit signal."""
    from aletheia.portfolio.pnl import close_position, _latest_price
    price = _latest_price(sig.symbol)
    if price is None:
        logger.warning("no_price_for_exit:%s", sig.symbol)
        return None
    try:
        close_position(sig.position_id, exit_price=float(price), reason="ruleset_exit_signal")
        logger.info("ruleset_exit: closed position %s for %s", sig.position_id, sig.symbol)
        return f"ruleset_exit:{sig.symbol}:{sig.strategy_id[:8]}"
    except Exception as e:
        logger.warning("ruleset_exit failed for %s: %s", sig.position_id, e)
        return None


def _handle_ruleset_entry(
    sig: RuleSetSignal,
    cfg,
    execution_mode: str,
    run_id: str | None,
) -> str | None:
    """Open a new position triggered by RuleSet entry signal."""
    snap = latest_snapshot(sig.symbol)
    price = (snap or {}).get("price")
    if not price:
        logger.warning("no_price_for_entry:%s", sig.symbol)
        return None

    price = float(price)
    quantity = round(_PAPER_NOTIONAL_USDT / price, 6) or 0.000001

    # Use RuleSet-derived stop if available, else fall back to config %
    stop_price = sig.stop_price
    if stop_price is None:
        stop_pct = cfg.trading.default_stop_loss_pct
        if sig.direction == "long":
            stop_price = round(price * (1 - stop_pct / 100.0), 8)
        else:
            stop_price = round(price * (1 + stop_pct / 100.0), 8)

    rationale = (
        f"RuleSet entry signal.\n"
        f"strategy={sig.strategy_id}\n"
        f"strategy_name={sig.strategy_name}\n"
        f"timeframe={sig.timeframe}\n"
        f"stop_price={stop_price}"
    )

    result = create_paper_proposal(
        symbol=sig.symbol,
        market_type="usdt_futures",
        direction=sig.direction,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        execution_mode=execution_mode,
        strategy_id=sig.strategy_id,
        opportunity_id=None,
        agent_rationale=rationale[:500],
    )
    log_tool(run_id, "create_paper_proposal", "paper", True, output=result.get("proposal_id"))

    pid = result.get("proposal_id")
    logger.info("ruleset_entry: proposal %s for %s %s", pid, sig.direction, sig.symbol)
    return f"ruleset_entry:{sig.symbol}:{sig.strategy_id[:8]}:{result.get('status')}"
