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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool
from aletheia.agents.paper_trader_ranker import build_paper_candidates
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.execution.paper_tools import create_paper_proposal
from aletheia.storage.repositories import update_opportunity, list_positions

logger = logging.getLogger("aletheia.agents.paper_trader")

_BASE_NOTIONAL_USDT = 25.0  # legacy fallback; prefer cfg.trading.default_notional_usdt
_ATR_TARGET_PCT = 1.5  # legacy fallback; prefer cfg.trading.vol_target_pct
_RECENT_SIGNAL_COOLDOWN_MINUTES = 30


@dataclass
class SignalBatchPlan:
    selected: list[dict]
    decisions: dict[str, tuple[str, str]]


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


def _recent_signal_pairs(window_minutes: int = _RECENT_SIGNAL_COOLDOWN_MINUTES) -> set[tuple[str, str]]:
    """Pairs recently handed to the System Operator via signal_pending."""
    try:
        from aletheia.storage.sqlite import get_db
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
        rows = get_db().execute(
            "SELECT symbol, direction FROM pi_decisions "
            "WHERE action='signal_pending' AND created_at >= ? "
            "AND symbol IS NOT NULL AND direction IS NOT NULL",
            (cutoff,),
        ).fetchall()
        return {(r["symbol"], r["direction"]) for r in rows}
    except Exception:
        return set()


def _select_signal_candidates(candidates: list[dict], limit: int = 3) -> list[dict]:
    """Pick a compact signal batch without duplicate symbols or pairs.

    Dedup is intentionally symbol-only (not market_type-aware): per spec
    OD-S01 (spec_market_type_dedup.py, commit db3c3f8), directional analysis is
    instrument-agnostic and the upstream pipeline already emits at most one
    instrument per canonical symbol (auto→perp). Adding a market_type key here
    would re-enable the same-symbol spot+perp double-observation OD-S01 removed.
    (Bug-review #10 reclassified as won't-fix / design-consistent.)
    """
    selected: list[dict] = []
    seen_symbols: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for candidate in candidates:
        sym = candidate.get("symbol")
        direction = candidate.get("direction")
        if not sym or not direction:
            continue
        pair = (sym, direction)
        if sym in seen_symbols or pair in seen_pairs:
            continue
        selected.append(candidate)
        seen_symbols.add(sym)
        seen_pairs.add(pair)
        if len(selected) >= limit:
            break
    return selected


def _plan_signal_batch(
    candidates: list[dict],
    *,
    open_symbols: set[str],
    recent_signal_pairs: set[tuple[str, str]],
    limit: int = 3,
) -> SignalBatchPlan:
    """Apply occupancy/cooldown before the batch cap so lower ranks can backfill."""
    selected: list[dict] = []
    decisions: dict[str, tuple[str, str]] = {}
    seen_symbols: set[str] = set()
    for candidate in candidates:
        opp_id = str(candidate.get("opportunity_id") or "")
        symbol = candidate.get("symbol")
        direction = candidate.get("direction")
        if not opp_id or not symbol or not direction:
            continue
        if symbol in seen_symbols:
            decisions[opp_id] = ("duplicate_symbol", "rejected")
            continue
        seen_symbols.add(symbol)
        if symbol in open_symbols:
            decisions[opp_id] = ("open_position_duplicate", "rejected")
            continue
        if (symbol, direction) in recent_signal_pairs:
            decisions[opp_id] = ("signal_cooldown", "rejected")
            continue
        if len(selected) >= limit:
            decisions[opp_id] = ("signal_batch_limit", "rejected")
            continue
        selected.append(candidate)
        decisions[opp_id] = ("signal_batch_limit", "passed")
    return SignalBatchPlan(selected=selected, decisions=decisions)


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
    recent_signal_pairs = _recent_signal_pairs()
    # Position C (2026-07-02): Path A (RuleSet-driven signals) was removed with the
    # strategy rules engine. The paper trader is now purely opportunity-driven: it
    # ranks market_observer opportunities and writes the top ones as signal_pending
    # for the System Operator to decide (PI-central). There is no RuleSet scan and no
    # deterministic-rule entry/exit path.

    # ── Opportunity-driven (ranked) ───────────────────────────────────────────
    candidates = build_paper_candidates(limit=50)

    _PERMANENT_SKIP_PREFIXES = (
        "neutral_bias", "confidence_below_", "paused_strategy:", "blocked_factor:",
        "equity_shadow_track", "execution_eligible=False", "universe_not_eligible",
        "ttl_expired", "factor_hard_blocked", "okx_demo_symbol_not_supported",
    )
    skipped = [c for c in candidates if c.get("skip_reason")]
    for c in skipped:
        reason = c.get("skip_reason", "")
        if any(reason.startswith(p) for p in _PERMANENT_SKIP_PREFIXES):
            update_opportunity(c["opportunity_id"], status="reviewed")
    for c in skipped[:10]:
        outputs.append(f"skip:{c.get('symbol','?')}:{c['skip_reason']}")

    # Phase 4a: Path B is now a pure signal collector — write top candidates as
    # signal_pending for System Operator to evaluate (System Operator is the sole decision maker).
    eligible = [c for c in candidates if not c.get("skip_reason")]
    if not eligible:
        _persist_filter_evidence(candidates, None, run_id=run_id)
        if not outputs:
            outputs.append("no_ranked_candidate")
        return {"outputs": outputs, "errors": errors}

    plan = _plan_signal_batch(
        eligible,
        open_symbols=open_symbols,
        recent_signal_pairs=recent_signal_pairs,
        limit=3,
    )
    _persist_filter_evidence(candidates, plan, run_id=run_id)
    for candidate in eligible:
        opp_id = str(candidate.get("opportunity_id") or "")
        filter_name, verdict = plan.decisions.get(opp_id, ("", ""))
        if verdict != "rejected":
            continue
        if filter_name == "open_position_duplicate":
            outputs.append(f"skip_existing_position:{candidate.get('symbol')}")
        elif filter_name == "signal_cooldown":
            outputs.append(
                f"skip_recent_signal:{candidate.get('symbol')}:{candidate.get('direction')}"
            )

    # Write up to 3 candidates after occupancy/cooldown rejects have been removed.
    signals_written = 0
    emitted_candidates: list[dict] = []
    for candidate in plan.selected:
        try:
            sym = candidate["symbol"]
            direction = candidate.get("direction")
            from aletheia.storage.models import PiDecision
            from aletheia.storage.repositories import insert_pi_decision
            pi_dec = PiDecision(
                symbol=candidate["symbol"],
                direction=direction,
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
            if direction:
                recent_signal_pairs.add((sym, direction))
            signals_written += 1
            emitted_candidates.append(candidate)
            try:
                from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
                notify_pi_daemon(source="paper_trader_path_b")
            except Exception:
                pass
        except Exception as e:
            logger.debug("signal_pending write failed for %s: %s", candidate.get("symbol"), e)

    # Counterfactual selection must reflect candidates actually emitted after all
    # occupancy/cooldown checks, not the theoretical rank-1 before those checks.
    try:
        from aletheia.analytics.shadow_trades import persist_shadow_batch

        persist_shadow_batch(eligible, emitted_candidates, top_n=5)
    except Exception as _shadow_err:
        logger.debug("shadow trade persistence failed: %s", _shadow_err)

    if signals_written:
        outputs.append(f"signals_pending:{signals_written}")
    else:
        outputs.append("no_signals_written")

    return {"outputs": outputs, "errors": errors}


def _persist_filter_evidence(
    candidates: list[dict],
    plan: SignalBatchPlan | None,
    *,
    run_id: str | None,
) -> None:
    """Best-effort append of ranker and selection facts; never changes decisions."""
    try:
        from aletheia.analytics.filter_evidence import record_candidate_filter_trace
        from aletheia.storage.repositories import transaction

        with transaction():
            for candidate in candidates:
                record_candidate_filter_trace(candidate, run_id=run_id)
            if plan is None:
                return
            for candidate in candidates:
                opp_id = str(candidate.get("opportunity_id") or "")
                if not opp_id or opp_id not in plan.decisions:
                    continue
                filter_name, decision = plan.decisions[opp_id]
                parameters = {"limit": 3} if filter_name == "signal_batch_limit" else {}
                if filter_name == "signal_cooldown":
                    parameters["cooldown_minutes"] = _RECENT_SIGNAL_COOLDOWN_MINUTES
                record_candidate_filter_trace(
                    candidate,
                    run_id=run_id,
                    trace=[{
                        "filter_name": filter_name,
                        "stage": "paper_trader_selection",
                        "decision": decision,
                        "reason_code": filter_name,
                        "inputs": {
                            "trade_score": candidate.get("trade_score"),
                            "symbol": candidate.get("symbol"),
                            "direction": candidate.get("direction"),
                        },
                        "parameters": parameters,
                    }],
                )
    except Exception as exc:
        logger.warning("paper trader filter evidence unavailable: %s", exc)


# Position C (2026-07-02): _handle_ruleset_entry removed with the rules engine.
# The paper trader no longer produces RuleSet-driven signals; it is purely
# opportunity-driven (ranked → signal_pending → PI decides).
