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
from aletheia.storage.repositories import update_opportunity, get_strategy
from aletheia.trading.market_data import latest_snapshot
from aletheia.trading.ruleset_scanner import RuleSetSignal, scan_active_rulesets

logger = logging.getLogger("aletheia.agents.paper_trader")

_BASE_NOTIONAL_USDT = 25.0  # legacy fallback; prefer cfg.trading.default_notional_usdt
_ATR_TARGET_PCT = 1.5  # legacy fallback; prefer cfg.trading.vol_target_pct


def _resolve_venue(strategy_id: str | None, global_mode: str) -> str:
    """Resolve execution venue: strategy.execution_venue overrides global mode."""
    if not strategy_id:
        return global_mode
    strategy = get_strategy(strategy_id)
    if strategy and strategy.get("execution_venue"):
        return strategy["execution_venue"]
    return global_mode


def _atr_adjusted_notional(atr_pct: float | None, symbol: str | None = None) -> float:
    """Scale notional via volatility-target sizing when enabled.

    Delegates to unified vol_target_size() when vol_sizing_enabled=True.
    Falls back to legacy ATR inversion otherwise.
    """
    from aletheia.config.loader import load_config
    cfg = load_config()
    if cfg.trading.vol_sizing_enabled and symbol:
        from aletheia.trading.sizing import vol_target_size
        return vol_target_size(symbol, atr_pct_override=atr_pct)
    base = cfg.trading.default_notional_usdt
    if not atr_pct or atr_pct <= 0:
        return base
    target = cfg.trading.vol_target_pct
    scale = target / (atr_pct * 100)
    scale = max(0.4, min(2.0, scale))
    return round(base * scale, 2)


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
                sig_venue = _resolve_venue(sig.strategy_id, execution_mode)
                out = _handle_ruleset_entry(sig, cfg, sig_venue, run_id)
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

    # Edge Lab: persist top-N candidates as shadow trades for counterfactual analysis.
    try:
        from aletheia.analytics.shadow_trades import persist_shadow_batch
        eligible = [c for c in candidates if not c.get("skip_reason")]
        selected = select_best_candidate(candidates)
        persist_shadow_batch(eligible, selected, top_n=5)
    except Exception as _shadow_err:
        logger.debug("shadow trade persistence failed: %s", _shadow_err)

    # Phase 4a: Path B is now a pure signal collector — write top candidates as
    # signal_pending for PI Agent to evaluate (PI Agent is the sole decision maker).
    eligible = [c for c in candidates if not c.get("skip_reason")]
    if not eligible:
        if not outputs:
            outputs.append("no_ranked_candidate")
        return {"outputs": outputs, "errors": errors}

    # Write top 3 eligible candidates as pending signals for PI Agent
    signals_written = 0
    for candidate in eligible[:3]:
        try:
            from aletheia.storage.models import PiDecision
            from aletheia.storage.repositories import insert_pi_decision
            pi_dec = PiDecision(
                symbol=candidate["symbol"],
                direction=candidate.get("direction"),
                action="signal_pending",
                conviction=0,
                reasoning=(
                    f"Ranker candidate: score={candidate.get('trade_score')} "
                    f"scanner={candidate.get('scanner_family')} "
                    f"factor={candidate.get('factor_status')}"
                ),
                proposal_id=None,
                opportunity_id=candidate.get("opportunity_id"),
                debate_ran=False,
            )
            insert_pi_decision(pi_dec)
            # Mark the source opportunity reviewed so the next paper_trader cycle
            # doesn't re-rank and re-emit it as a duplicate signal_pending. Without
            # this, the same open opportunity was re-written every 5min — ETH short
            # appeared 4x in pending signals, and the PI Agent read the duplicates
            # as "pipeline data integrity compromised" and skipped every decision
            # (PT-1, freeze exception 2026-06-24). Mirrors the reviewed-marking
            # already done for permanently-skipped candidates at line ~113.
            if candidate.get("opportunity_id"):
                try:
                    update_opportunity(candidate["opportunity_id"], status="reviewed")
                except Exception as _e:
                    logger.debug("opportunity reviewed-mark failed: %s", _e)
            signals_written += 1
            try:
                from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
                notify_pi_daemon(source="paper_trader_path_b")
            except Exception:
                pass
        except Exception as e:
            logger.debug("signal_pending write failed for %s: %s", candidate.get("symbol"), e)

    if signals_written:
        outputs.append(f"signals_pending:{signals_written}")
    else:
        outputs.append("no_signals_written")

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
    """Record a RuleSet entry signal as signal_pending for PI Agent to evaluate.

    PI Agent is the sole decision maker — Path A writes signals, not proposals.
    """
    snap = latest_snapshot(sig.symbol)
    price = (snap or {}).get("price")
    if not price:
        logger.warning("no_price_for_entry:%s", sig.symbol)
        return None

    price = float(price)

    # Use RuleSet-derived stop if available, else fall back to config %
    stop_price = sig.stop_price
    if stop_price is None:
        stop_pct = cfg.trading.default_stop_loss_pct
        if sig.direction == "long":
            stop_price = round(price * (1 - stop_pct / 100.0), 8)
        else:
            stop_price = round(price * (1 + stop_pct / 100.0), 8)

    try:
        from aletheia.storage.models import PiDecision
        from aletheia.storage.repositories import insert_pi_decision
        pi_dec = PiDecision(
            symbol=sig.symbol,
            direction=sig.direction,
            action="signal_pending",
            conviction=0,
            reasoning=(
                f"RuleSet entry: strategy={sig.strategy_id[:8]} "
                f"name={sig.strategy_name} tf={sig.timeframe} "
                f"price={price} stop={stop_price}"
            ),
            proposal_id=None,
            debate_ran=False,
        )
        insert_pi_decision(pi_dec)
        try:
            from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
            notify_pi_daemon(source="paper_trader_ruleset")
        except Exception:
            pass
    except Exception as e:
        logger.debug("signal_pending write failed for ruleset %s: %s", sig.symbol, e)

    logger.info("ruleset_signal_pending: %s %s strategy=%s", sig.direction, sig.symbol, sig.strategy_id[:8])
    return f"ruleset_signal_pending:{sig.symbol}:{sig.strategy_id[:8]}"


