"""Paper Trader agent — ranks open opportunities into pending signals for the System Operator.

USER-AUTHORIZED autonomous behavior (paper only). Containment:
- Only runs when permission mode is Paper or Full. Denied in Observe.
- Submits exclusively through the paper wrapper — the live path is unreachable here.
- Every order still passes the Risk Engine (kill switch, daily loss, etc.).
- Consumed opportunities are marked `proposed` for idempotency (no duplicates).

Execution target is `exchange.paper_execution_mode` with credential-based fallback:
if the configured exchange demo has no credentials, `paper_internal` is used automatically.
Exchange demo (bitget_demo/okx_demo/binance_testnet) uses the real exchange sandbox;
`paper_internal` fills locally with slippage simulation.

PaperTrader v4 (opportunity-driven, Position C 2026-07-02): the RuleSet rules engine
was deleted. The paper trader ranks market_observer opportunities deterministically
and writes the top candidates as signal_pending PiDecisions for the System Operator to
evaluate. The System Operator is the sole decision maker; the paper trader never opens or
closes positions itself.
"""
from __future__ import annotations

import logging

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool
from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.execution.paper_tools import create_paper_proposal
from aletheia.storage.repositories import update_opportunity, list_positions

logger = logging.getLogger("aletheia.agents.paper_trader")

_BASE_NOTIONAL_USDT = 25.0  # legacy fallback; prefer cfg.trading.default_notional_usdt
_ATR_TARGET_PCT = 1.5  # legacy fallback; prefer cfg.trading.vol_target_pct


_EXCHANGE_CRED_KEYS: dict[str, str] = {
    # execution_mode  → secrets key prefix for the relevant credential
    "bitget_demo":       "bitget.demo",
    "bitget_live_limited": "bitget.live",
    "okx_demo":          "okx.demo",
    "okx_live_limited":  "okx.live",
    "binance_testnet":   "binance.testnet",
    "binance_live_limited": "binance.live",
}


def _has_credentials(execution_mode: str) -> bool:
    """Return True when the credentials required for execution_mode are configured."""
    prefix = _EXCHANGE_CRED_KEYS.get(execution_mode)
    if not prefix:
        return False
    try:
        from aletheia.config.secrets import get_secret
        return bool(get_secret(f"{prefix}.api_key"))
    except Exception:
        return False


def _resolve_execution_mode(configured_mode: str) -> str:
    """Return the best available execution mode for paper trading.

    Priority: configured exchange demo (real sandbox) > paper_internal (local sim).
    Falls back to paper_internal when the configured exchange demo has no credentials,
    so the system degrades gracefully on a fresh install or when credentials are missing.
    """
    if configured_mode == "paper_internal":
        return "paper_internal"
    if _has_credentials(configured_mode):
        return configured_mode
    logger.info(
        "No credentials found for %s — falling back to paper_internal", configured_mode
    )
    return "paper_internal"


def _atr_adjusted_notional(
    atr_pct: float | None,
    symbol: str | None = None,
    strategy_id: str | None = None,
) -> float:
    """Scale notional via volatility-target sizing when enabled.

    P1-5: looks up paper_trade_count for strategy_id so new strategies start at
    0.1 Kelly and ramp up as evidence accumulates.
    """
    from aletheia.config.loader import load_config
    cfg = load_config()
    if cfg.trading.vol_sizing_enabled and symbol:
        from aletheia.trading.sizing import vol_target_size
        ptc = _get_strategy_paper_trade_count(strategy_id)
        return vol_target_size(symbol, atr_pct_override=atr_pct, paper_trade_count=ptc)
    base = cfg.trading.default_notional_usdt
    if not atr_pct or atr_pct <= 0:
        return base
    target = cfg.trading.vol_target_pct
    scale = target / atr_pct
    scale = max(0.4, min(2.0, scale))
    return round(base * scale, 2)


def _get_strategy_paper_trade_count(strategy_id: str | None) -> int:
    """Closed paper/demo positions count for a strategy — used for sample-aware Kelly."""
    if not strategy_id:
        return 0
    try:
        from aletheia.storage.sqlite import get_db
        row = get_db().execute(
            "SELECT count(*) FROM positions "
            "WHERE strategy_id = ? AND status = 'closed' AND execution_mode NOT LIKE '%live%'",
            (strategy_id,),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


@register_agent("paper_trader")
async def run_paper_trader(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    outputs: list[str] = []
    errors: list[str] = []

    cfg = load_config()

    if not is_tool_permitted("create_paper_proposal", permission_mode):
        log_tool(run_id, "create_paper_proposal", "paper", False)
        errors.append(f"create_paper_proposal not permitted in mode {permission_mode}")
        return {"outputs": outputs, "errors": errors}

    execution_mode = _resolve_execution_mode(cfg.exchange.paper_execution_mode)

    # Snapshot of currently open symbols — used to skip redundant signal writes.
    open_symbols: set[str] = {p["symbol"] for p in list_positions(status="open")}
    # Track symbols that received a signal_pending write in this cycle (cross-path dedup).
    cycle_pending_symbols: set[str] = set()

    # Position C (2026-07-02): Path A (RuleSet-driven signals) was removed with the
    # strategy rules engine. The paper trader is now purely opportunity-driven: it
    # ranks market_observer opportunities and writes the top ones as signal_pending
    # for the System Operator to decide (PI-central). There is no RuleSet scan and no
    # deterministic-rule entry/exit path.

    # ── Opportunity-driven (ranked) ───────────────────────────────────────────
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
    # signal_pending for System Operator to evaluate (System Operator is the sole decision maker).
    eligible = [c for c in candidates if not c.get("skip_reason")]
    if not eligible:
        if not outputs:
            outputs.append("no_ranked_candidate")
        return {"outputs": outputs, "errors": errors}

    # Write top 3 eligible candidates as pending signals for System Operator
    signals_written = 0
    for candidate in eligible[:3]:
        try:
            sym = candidate["symbol"]
            if sym in open_symbols or sym in cycle_pending_symbols:
                outputs.append(f"skip_existing_position:{sym}")
                continue
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
            # appeared 4x in pending signals, and the System Operator read the duplicates
            # as "pipeline data integrity compromised" and skipped every decision
            # (PT-1, freeze exception 2026-06-24). Mirrors the reviewed-marking
            # already done for permanently-skipped candidates at line ~113.
            if candidate.get("opportunity_id"):
                try:
                    update_opportunity(candidate["opportunity_id"], status="reviewed")
                except Exception as _e:
                    logger.debug("opportunity reviewed-mark failed: %s", _e)
            cycle_pending_symbols.add(sym)
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


# Position C (2026-07-02): _handle_ruleset_entry removed with the rules engine.
# The paper trader no longer produces RuleSet-driven signals; it is purely
# opportunity-driven (ranked → signal_pending → PI decides).


