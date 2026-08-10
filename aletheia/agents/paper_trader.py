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
import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool
from aletheia.agents.paper_trader_ranker import build_paper_candidate_workset
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.storage.repositories import (
    count_unresolved_signal_pending,
    expire_stale_queued_opportunities,
    list_all_open_positions,
    list_pending_signal_queue,
    prune_signal_pending_stale,
    transition_open_opportunity_to_queued,
    transition_queued_opportunity_to_open,
)

logger = logging.getLogger("aletheia.agents.paper_trader")

_BASE_NOTIONAL_USDT = 25.0  # legacy fallback; prefer cfg.trading.default_notional_usdt
_ATR_TARGET_PCT = 1.5  # legacy fallback; prefer cfg.trading.vol_target_pct
_RECENT_SIGNAL_COOLDOWN_MINUTES = 30
_PENDING_SIGNAL_MAX_AGE_MINUTES = 30
_PENDING_SIGNAL_QUEUE_CAPACITY = 5


@dataclass
class SignalBatchPlan:
    selected: list[dict]
    decisions: dict[str, tuple[str, str]]
    evictions: list[str] = dataclasses.field(default_factory=list)


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
    """Genuine closed paper/demo outcomes used for sample-aware Kelly.

    A lifecycle close is not automatically trading evidence. Administrative,
    incomplete-funding, and unattributed rows must not move a strategy from the
    conservative small-sample sizing band into full Kelly sizing.
    """
    if not strategy_id:
        return 0
    try:
        from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes

        return sum(
            1
            for row in eligible_strategy_outcomes()
            if str(row.get("strategy_id") or "") == strategy_id
            and "live" not in str(row.get("execution_mode") or "")
        )
    except Exception:
        return 0


def _recent_signal_pairs(
    window_minutes: int = _RECENT_SIGNAL_COOLDOWN_MINUTES,
    signal_source: str = "market_observer",
) -> set[tuple[str, str]]:
    """Pairs handed to the System Operator inside the fixed cooldown window.

    Scoped to a single producer namespace (default ``market_observer``) so a
    Serenity signal_pending row does not pollute market_observer's cooldown for
    the same (symbol, direction). ``_active_signal_setups`` already uses this
    scoping; the earlier signal_source-blind cooldown was a historical mismatch.
    """
    try:
        from aletheia.storage.sqlite import get_db
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
        rows = get_db().execute(
            "SELECT symbol, direction FROM pi_decisions "
            "WHERE action='signal_pending' AND datetime(created_at)>=datetime(?) "
            "AND symbol IS NOT NULL AND direction IS NOT NULL "
            "AND COALESCE(signal_source, 'market_observer')=?",
            (cutoff, signal_source or "market_observer"),
        ).fetchall()
        return {(r["symbol"], r["direction"]) for r in rows}
    except Exception:
        return set()


def _active_signal_setups() -> set[tuple[str, str, str]]:
    """Setup identities already owned by a live Operator queue carrier."""
    try:
        from aletheia.storage.sqlite import get_db

        rows = get_db().execute(
            "SELECT DISTINCT p.symbol,p.direction,COALESCE(o.scanner_family,'') "
            "FROM pi_decisions p JOIN opportunities o ON o.id=p.opportunity_id "
            "WHERE p.action='signal_pending' "
            "AND COALESCE(p.signal_source, 'market_observer')='market_observer' "
            "AND o.status='queued' "
            "AND datetime(o.valid_until) IS NOT NULL "
            "AND datetime(o.valid_until)>datetime('now') "
            "AND p.rowid=("
            " SELECT newer.rowid FROM pi_decisions newer "
            " WHERE newer.opportunity_id=p.opportunity_id "
            " AND COALESCE(newer.signal_source, 'market_observer')="
            "     COALESCE(p.signal_source, 'market_observer') "
            " AND newer.action='signal_pending' "
            " ORDER BY julianday(newer.created_at) DESC, newer.rowid DESC LIMIT 1"
            ") "
            "AND NOT EXISTS ("
            " SELECT 1 FROM pi_decisions final "
            " WHERE final.opportunity_id=p.opportunity_id "
            " AND COALESCE(final.signal_source, 'market_observer')="
            "     COALESCE(p.signal_source, 'market_observer') "
            " AND final.action!='signal_pending' "
            " AND COALESCE(final.proposal_status, '')!='submitting' "
            " AND (julianday(final.created_at)>julianday(p.created_at) "
            "      OR (julianday(final.created_at)=julianday(p.created_at) "
            "          AND final.rowid>p.rowid))"
            ")"
        ).fetchall()
        return {
            (str(row[0]), str(row[1]), str(row[2] or ""))
            for row in rows
            if row[0] and row[1]
        }
    except Exception:
        return set()


def _operator_evaluated_opportunity_ids(
    signal_source: str = "market_observer",
    max_age_hours: int = 24,
) -> set[str]:
    """Opportunity ids *finalized* by the same producer namespace within the
    lookback window.

    Only terminal operator decisions (``trade``, ``skip``) count.
    ``signal_queue_evicted`` is intentionally excluded: eviction moves the
    opportunity back to ``open`` and it must remain eligible for re-queuing —
    treating it as "already evaluated" would permanently block it (#331).

    The 24-hour window prevents the set from growing without bound as the DB
    ages. Genuine lifecycle races that resurface an ``open`` opportunity older
    than 24h are caught by ``transition_open_opportunity_to_queued``'s CAS at
    write time (paper_trader.py:~516).
    """
    try:
        from aletheia.storage.sqlite import get_db

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max(int(max_age_hours), 1))
        ).isoformat()
        rows = get_db().execute(
            "SELECT DISTINCT opportunity_id FROM pi_decisions "
            "WHERE action NOT IN ('signal_pending', 'signal_queue_evicted') "
            "AND opportunity_id IS NOT NULL "
            "AND COALESCE(signal_source, 'market_observer')=? "
            "AND datetime(created_at) >= datetime(?)",
            (signal_source or "market_observer", cutoff),
        ).fetchall()
        return {str(row[0]) for row in rows if row[0]}
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
    active_signal_setups: set[tuple[str, str, str]] | None = None,
    operator_evaluated_opportunity_ids: set[str] | None = None,
    limit: int = 3,
    available_queue_slots: int | None = None,
    current_queue: list[dict] | None = None,
) -> SignalBatchPlan:
    """Apply occupancy/cooldown/backpressure before the per-run batch cap.

    When available_queue_slots is 0 and current_queue is provided, priority
    replacement is attempted: a new candidate whose effective conviction exceeds
    the minimum-conviction occupant evicts that occupant and takes its slot.
    This converts FIFO allocation to score-priority allocation with no capacity
    change required.
    """
    selected: list[dict] = []
    decisions: dict[str, tuple[str, str]] = {}
    evictions: list[str] = []
    seen_symbols: set[str] = set()
    evaluated_ids = operator_evaluated_opportunity_ids or set()
    active_setups = active_signal_setups or set()
    # Symbol-level index used to catch opposing-direction carriers: if LONG is
    # already in the queue, SHORT for the same symbol is rejected too, and vice
    # versa.  Extracted once here rather than scanning the full set per candidate.
    active_symbols: set[str] = {s for (s, _, _) in active_setups}
    # Mutable view of current queue for priority-replacement bookkeeping.
    # Sorted ascending by conviction so index 0 is always the weakest occupant.
    queue_occupants: list[dict] = sorted(current_queue or [], key=lambda x: x["conviction"])
    for candidate in candidates:
        opp_id = str(candidate.get("opportunity_id") or "")
        symbol = candidate.get("symbol")
        direction = candidate.get("direction")
        if not opp_id or not symbol or not direction:
            continue
        if opp_id in evaluated_ids:
            decisions[opp_id] = ("operator_already_evaluated", "rejected")
            continue
        if symbol in seen_symbols:
            decisions[opp_id] = ("duplicate_symbol", "rejected")
            continue
        seen_symbols.add(symbol)
        if symbol in open_symbols:
            decisions[opp_id] = ("open_position_duplicate", "rejected")
            continue
        if symbol in active_symbols:
            scanner_family_str = str(candidate.get("scanner_family") or "")
            if (symbol, direction, scanner_family_str) in active_setups:
                decisions[opp_id] = ("active_operator_carrier", "rejected")
            else:
                decisions[opp_id] = ("opposing_direction_queued", "rejected")
            continue
        if (symbol, direction) in recent_signal_pairs:
            decisions[opp_id] = ("signal_cooldown", "rejected")
            continue
        slots_limit = max(available_queue_slots, 0) if available_queue_slots is not None else None
        if slots_limit is not None and len(selected) >= slots_limit:
            # Priority replacement: evict the weakest occupant when the new
            # candidate scores strictly higher than it.
            candidate_conviction = min(int(candidate.get("trade_score") or 0), 95)
            if queue_occupants and candidate_conviction > queue_occupants[0]["conviction"]:
                evicted = queue_occupants.pop(0)
                evictions.append(evicted["opportunity_id"])
                decisions[evicted["opportunity_id"]] = ("signal_queue_evicted", "evicted")
                selected.append(candidate)
                decisions[opp_id] = ("signal_batch_limit", "passed")
            else:
                decisions[opp_id] = ("signal_queue_capacity", "rejected")
            continue
        if len(selected) >= limit:
            decisions[opp_id] = ("signal_batch_limit", "rejected")
            continue
        selected.append(candidate)
        decisions[opp_id] = ("signal_batch_limit", "passed")
    return SignalBatchPlan(selected=selected, decisions=decisions, evictions=evictions)


@register_agent("paper_trader")
async def run_paper_trader(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    outputs: list[str] = []
    errors: list[str] = []
    artifact_refs: list[dict[str, str]] = []

    cfg = load_config()

    if not is_tool_permitted("create_paper_proposal", permission_mode):
        log_tool(run_id, "create_paper_proposal", "paper", False)
        errors.append(f"create_paper_proposal not permitted in mode {permission_mode}")
        return {"outputs": outputs, "errors": errors}

    # Filter evidence must identify this exact evaluation. Scheduler-triggered
    # runs already supply an AgentRun id; direct/manual callers get an internal
    # id rather than an empty batch identity.
    evaluation_run_id = run_id or f"paper-trader-{uuid4().hex}"

    def _result() -> dict:
        return {
            "outputs": outputs,
            "errors": errors,
            "output_artifacts": [
                *artifact_refs,
                *_filter_evidence_artifact_refs(evaluation_run_id),
            ],
        }

    execution_mode = _resolve_execution_mode(cfg.exchange.paper_execution_mode)

    try:
        expired_pending = expire_stale_queued_opportunities()
        if expired_pending:
            outputs.append(f"expired_pending:{expired_pending}")
    except Exception as exc:
        logger.warning("pending opportunity expiry unavailable: %s", exc)
    try:
        pruned_pending = prune_signal_pending_stale(_PENDING_SIGNAL_MAX_AGE_MINUTES)
        if pruned_pending:
            outputs.append(f"pruned_pending_carriers:{pruned_pending}")
    except Exception as exc:
        logger.warning("terminal pending carrier cleanup unavailable: %s", exc)

    # Snapshot of currently open symbols — used to skip redundant signal writes.
    open_symbols: set[str] = {p["symbol"] for p in list_all_open_positions()}
    recent_signal_pairs = _recent_signal_pairs()
    active_signal_setups = _active_signal_setups()
    # Position C (2026-07-02): Path A (RuleSet-driven signals) was removed with the
    # strategy rules engine. The paper trader is now purely opportunity-driven: it
    # ranks market_observer opportunities and writes the top ones as signal_pending
    # for the System Operator to decide (PI-central). There is no RuleSet scan and no
    # deterministic-rule entry/exit path.

    # ── Opportunity-driven (ranked) ───────────────────────────────────────────
    workset = build_paper_candidate_workset(limit=50)
    # Selection may need to backfill below the display shortlist when higher
    # ranks are temporarily occupied/cooling down. Keep the complete working set.
    candidates = list(workset.scanned_candidates)

    _PERMANENT_SKIP_PREFIXES = (
        "neutral_bias", "confidence_below_", "paused_strategy:", "blocked_factor:",
        "equity_shadow_track", "execution_eligible=False", "universe_not_eligible",
        "ttl_expired", "ttl_invalid", "factor_hard_blocked", "okx_demo_symbol_not_supported",
        "symbol_not_allowed:",
    )
    def _is_permanent_skip(candidate: dict) -> bool:
        return bool(
            candidate.get("skip_reason")
            and any(
                str(candidate.get("skip_reason") or "").startswith(prefix)
                for prefix in _PERMANENT_SKIP_PREFIXES
            )
        )

    def _settle_permanent_skips(batch: list[dict]) -> set[str]:
        """Return stale/failed ids whose rejection facts must not leak onward."""
        blocked_ids: set[str] = set()
        for candidate in batch:
            if not _is_permanent_skip(candidate):
                continue
            opportunity_id = str(candidate.get("opportunity_id") or "")
            reason = str(candidate.get("skip_reason") or "")
            terminal_status = "expired" if reason.startswith("ttl_expired") else "reviewed"
            try:
                finalized = _finalize_permanent_skip(
                    candidate,
                    terminal_status=terminal_status,
                    run_id=evaluation_run_id,
                )
                if not finalized:
                    blocked_ids.add(opportunity_id)
                    outputs.append(f"skip_stale_plan:{candidate.get('symbol')}")
            except Exception as exc:
                blocked_ids.add(opportunity_id)
                errors.append(
                    f"permanent_skip_evidence_failed:{opportunity_id or 'unknown'}"
                )
                logger.warning(
                    "permanent skip remains retryable opportunity=%s: %s",
                    opportunity_id,
                    type(exc).__name__,
                )
        return blocked_ids

    blocked_evidence_ids = _settle_permanent_skips(candidates)
    evidence_candidates = [
        candidate
        for candidate in candidates
        if str(candidate.get("opportunity_id") or "") not in blocked_evidence_ids
    ]
    displayed_skips = [
        candidate
        for candidate in candidates
        if candidate.get("skip_reason")
        and str(candidate.get("opportunity_id") or "") not in blocked_evidence_ids
    ]
    for c in displayed_skips[:10]:
        outputs.append(f"skip:{c.get('symbol','?')}:{c['skip_reason']}")

    # Phase 4a: Path B is now a pure signal collector — write top candidates as
    # signal_pending for System Operator to evaluate (System Operator is the sole decision maker).
    eligible = [c for c in candidates if not c.get("skip_reason")]
    if not eligible:
        evidence_persisted = _persist_filter_evidence(
            evidence_candidates, None, run_id=evaluation_run_id
        )
        if not evidence_persisted:
            errors.append("workset_filter_evidence_failed")
            outputs.append("no_signals_written")
            return _result()
        if not outputs:
            outputs.append("no_ranked_candidate")
        return _result()

    try:
        queue_depth = count_unresolved_signal_pending(_PENDING_SIGNAL_MAX_AGE_MINUTES)
        available_queue_slots = max(_PENDING_SIGNAL_QUEUE_CAPACITY - queue_depth, 0)
        current_queue = list_pending_signal_queue(_PENDING_SIGNAL_MAX_AGE_MINUTES)
    except Exception as exc:
        queue_depth = None
        available_queue_slots = 0
        current_queue = []
        errors.append("signal_queue_capacity_unavailable")
        logger.warning("pending queue capacity unavailable: %s", type(exc).__name__)

    plan = _plan_signal_batch(
        eligible,
        open_symbols=open_symbols,
        recent_signal_pairs=recent_signal_pairs,
        active_signal_setups=active_signal_setups,
        operator_evaluated_opportunity_ids=_operator_evaluated_opportunity_ids(),
        limit=_PENDING_SIGNAL_QUEUE_CAPACITY,
        available_queue_slots=available_queue_slots,
        current_queue=current_queue,
    )

    evidence_persisted = _persist_filter_evidence(
        evidence_candidates, plan, run_id=evaluation_run_id
    )
    if not evidence_persisted:
        failed_selected = [
            str(candidate.get("opportunity_id") or "unknown")
            for candidate in plan.selected
        ]
        if failed_selected:
            errors.extend(
                f"accepted_filter_evidence_failed:{opportunity_id}"
                for opportunity_id in failed_selected
            )
        else:
            errors.append("selection_filter_evidence_failed:no_selected_candidate")
        outputs.append("no_signals_written")
        return _result()
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
        elif filter_name == "active_operator_carrier":
            outputs.append(
                f"reuse_active_carrier:{candidate.get('symbol')}:"
                f"{candidate.get('direction')}:{candidate.get('scanner_family')}"
            )
        elif filter_name == "operator_already_evaluated":
            from aletheia.storage.sqlite import get_db

            terminalized = get_db().execute(
                "UPDATE opportunities SET status='reviewed' "
                "WHERE id=? AND status='open'",
                (opp_id,),
            )
            if terminalized.rowcount == 1:
                outputs.append(
                    f"skip_already_evaluated:{candidate.get('symbol')}"
                )
            else:
                outputs.append(f"skip_stale_plan:{candidate.get('symbol')}")
        elif filter_name == "signal_queue_capacity":
            outputs.append(
                f"backpressure:{candidate.get('symbol')}:"
                f"{queue_depth if queue_depth is not None else 'unknown'}/"
                f"{_PENDING_SIGNAL_QUEUE_CAPACITY}"
            )

    # Execute priority-replacement evictions before writing the new signals.
    # Each evicted opportunity is released back to open so the next run can
    # re-score it; the signal_pending pi_decision is superseded by the eviction row.
    for evicted_opp_id in plan.evictions:
        try:
            from aletheia.storage.models import PiDecision
            from aletheia.storage.repositories import insert_pi_decision, transaction

            eviction_dec = PiDecision(
                opportunity_id=evicted_opp_id,
                action="signal_queue_evicted",
                conviction=0,
                reasoning="evicted: lower-priority occupant replaced by higher-score candidate",
            )
            with transaction():
                transition_queued_opportunity_to_open(evicted_opp_id)
                insert_pi_decision(eviction_dec)
            outputs.append(f"evicted_low_priority:{evicted_opp_id}")
            artifact_refs.append({"artifact_type": "pi_decision", "artifact_id": eviction_dec.id})
        except Exception as exc:
            errors.append(f"eviction_failed:{evicted_opp_id}")
            logger.warning("signal_pending eviction failed for %s: %s", evicted_opp_id, type(exc).__name__)

    # Write up to 3 candidates after occupancy/cooldown rejects have been removed.
    signals_written = 0
    emitted_candidates: list[dict] = []
    for candidate in plan.selected:
        try:
            sym = candidate["symbol"]
            direction = candidate.get("direction")
            from aletheia.storage.models import PiDecision
            from aletheia.storage.repositories import insert_pi_decision, transaction
            _sig_conviction = min(int(candidate.get("trade_score", 0)), 95)
            pi_dec = PiDecision(
                symbol=candidate["symbol"],
                direction=direction,
                market_type=candidate.get("market_type") or candidate.get("market"),
                action="signal_pending",
                conviction=_sig_conviction,
                reasoning=(
                    f"Ranker candidate: score={candidate.get('trade_score')} "
                    f"scanner={candidate.get('scanner_family')} "
                    f"factor={candidate.get('factor_status')}"
                ),
                proposal_id=None,
                opportunity_id=candidate.get("opportunity_id"),
                debate_ran=False,
            )
            claimed = True
            with transaction():
                # Claim the source before writing its carrier. A plan may be stale by
                # the time this loop executes; only open→queued is legal, so a final
                # reviewed/expired row can never be moved backwards to queued.
                if candidate.get("opportunity_id"):
                    claimed = transition_open_opportunity_to_queued(
                        candidate["opportunity_id"]
                    )
                if claimed:
                    insert_pi_decision(pi_dec)
                    _persist_signal_enqueue_evidence(
                        candidate,
                        run_id=evaluation_run_id,
                        decision="passed",
                        reason_code="signal_enqueued",
                        raise_on_error=True,
                    )
            if not claimed:
                _persist_signal_enqueue_evidence(
                    candidate,
                    run_id=evaluation_run_id,
                    decision="rejected",
                    reason_code="opportunity_not_open",
                )
                outputs.append(f"skip_stale_plan:{candidate.get('symbol')}")
                continue
            if direction:
                recent_signal_pairs.add((sym, direction))
            artifact_refs.append({"artifact_type": "pi_decision", "artifact_id": pi_dec.id})
            signals_written += 1
            emitted_candidates.append(candidate)
            try:
                from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
                notify_pi_daemon(source="paper_trader_path_b")
            except Exception:
                pass
        except Exception as e:
            opportunity_id = str(candidate.get("opportunity_id") or "unknown")
            errors.append(f"signal_enqueue_failed:{opportunity_id}")
            _persist_signal_enqueue_evidence(
                candidate,
                run_id=evaluation_run_id,
                decision="error",
                reason_code="signal_enqueue_error",
                reason_detail=type(e).__name__,
            )
            logger.warning("signal_pending write failed for %s: %s", candidate.get("symbol"), e)

    # Counterfactual selection must reflect candidates actually emitted after all
    # occupancy/cooldown checks, not the theoretical rank-1 before those checks.
    try:
        from aletheia.counterfactual.trades import persist_shadow_batch

        persist_shadow_batch(eligible, emitted_candidates, top_n=5)
    except Exception as _shadow_err:
        logger.debug("shadow trade persistence failed: %s", _shadow_err)

    if signals_written:
        outputs.append(f"signals_pending:{signals_written}")
    else:
        outputs.append("no_signals_written")

    return _result()


def _filter_evidence_artifact_refs(run_id: str) -> list[dict[str, str]]:
    """Return only facts written under this exact evaluation identity."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    refs = [
        {"artifact_type": "filter_decision", "artifact_id": str(row[0])}
        for row in db.execute(
            "SELECT id FROM filter_decisions WHERE run_id=? ORDER BY evaluated_at, id",
            (run_id,),
        ).fetchall()
    ]
    refs.extend(
        {"artifact_type": "filter_decision_batch", "artifact_id": str(row[0])}
        for row in db.execute(
            "SELECT id FROM filter_decision_batches WHERE run_id=? ORDER BY evaluated_at, id",
            (run_id,),
        ).fetchall()
    )
    return refs


def _finalize_permanent_skip(
    candidate: dict,
    *,
    terminal_status: str,
    run_id: str | None,
) -> bool:
    """CAS one open Opportunity to terminal together with its rejection facts."""
    from aletheia.analytics.filter_evidence import record_candidate_filter_trace
    from aletheia.storage.repositories import transaction
    from aletheia.storage.sqlite import get_db

    if terminal_status not in {"reviewed", "expired"}:
        raise ValueError(f"unsupported permanent Opportunity status: {terminal_status}")

    expected_rejections = {
        (
            str(step.get("filter_name") or ""),
            str(step.get("stage") or "paper_ranker"),
            str(step.get("reason_code") or step.get("decision") or ""),
        )
        for step in candidate.get("filter_trace") or []
        if step.get("decision") == "rejected"
    }
    if not expected_rejections:
        raise RuntimeError("permanent_skip_rejection_fact_missing")

    opportunity_id = str(candidate.get("opportunity_id") or "")
    if not opportunity_id:
        raise RuntimeError("permanent_skip_opportunity_missing")

    db = get_db()
    with transaction(immediate=True):
        # transaction() deliberately joins an existing caller transaction. A
        # local savepoint is therefore required so a caught evidence exception
        # cannot leave the status UPDATE pending for a later unrelated commit.
        db.execute("SAVEPOINT paper_permanent_skip")
        try:
            claimed = db.execute(
                "UPDATE opportunities SET status=? WHERE id=? AND status='open'",
                (terminal_status, opportunity_id),
            )
            if claimed.rowcount != 1:
                db.execute("RELEASE SAVEPOINT paper_permanent_skip")
                return False
            record_candidate_filter_trace(
                candidate,
                run_id=run_id,
                raise_on_error=True,
            )
            actual_rejections = {
                (str(row[0]), str(row[1]), str(row[2]))
                for row in db.execute(
                    "SELECT filter_name, stage, reason_code FROM filter_decisions "
                    "WHERE opportunity_id=? AND decision='rejected'",
                    (opportunity_id,),
                ).fetchall()
            }
            if not expected_rejections.issubset(actual_rejections):
                raise RuntimeError("permanent_skip_rejection_fact_not_persisted")
        except Exception:
            db.execute("ROLLBACK TO SAVEPOINT paper_permanent_skip")
            db.execute("RELEASE SAVEPOINT paper_permanent_skip")
            raise
        else:
            db.execute("RELEASE SAVEPOINT paper_permanent_skip")
    return True


def _persist_filter_evidence(
    candidates: list[dict],
    plan: SignalBatchPlan | None,
    *,
    run_id: str | None,
) -> bool:
    """Atomically prove this exact bounded workset and advance its rotation."""
    try:
        from aletheia.analytics.filter_evidence import (
            record_candidate_filter_trace,
            record_filter_decision_batch,
        )
        from aletheia.storage.repositories import (
            mark_opportunities_ranked,
            transaction,
        )
        from aletheia.storage.sqlite import get_db

        if not run_id:
            raise ValueError("paper_trader_evaluation_run_id_required")

        evaluated_at = datetime.now(timezone.utc).isoformat()
        batch_facts: list[dict] = []
        for candidate in candidates:
            opportunity_id = str(candidate.get("opportunity_id") or "")
            skip_reason = str(candidate.get("skip_reason") or "")
            selection_filter = None
            selection_decision = None
            if plan is not None and opportunity_id in plan.decisions:
                selection_filter, selection_decision = plan.decisions[opportunity_id]
            if skip_reason:
                decision = "rejected"
                reason_code = skip_reason
            elif selection_decision in {"passed", "rejected"}:
                decision = selection_decision
                reason_code = selection_filter or "paper_trader_selection"
            else:
                decision = "error"
                reason_code = "selection_fact_missing"
            batch_facts.append({
                "opportunity_id": opportunity_id,
                "symbol": candidate.get("symbol"),
                "direction": candidate.get("direction"),
                "market_type": (
                    candidate.get("market_type") or candidate.get("market")
                ),
                "trade_score": candidate.get("trade_score"),
                "decision": decision,
                "reason_code": reason_code,
            })

        db = get_db()
        with transaction():
            # transaction() joins an existing caller transaction. Keep this
            # handoff atomic even in that case: one failed individual fact must
            # remove earlier facts and prevent a successful batch/rotation mark.
            db.execute("SAVEPOINT paper_filter_evidence")
            try:
                for candidate in candidates:
                    record_candidate_filter_trace(
                        candidate,
                        run_id=run_id,
                        raise_on_error=True,
                    )
                if plan is not None:
                    for candidate in candidates:
                        opp_id = str(candidate.get("opportunity_id") or "")
                        if not opp_id or opp_id not in plan.decisions:
                            continue
                        filter_name, decision = plan.decisions[opp_id]
                        parameters = (
                            {"limit": 3}
                            if filter_name == "signal_batch_limit"
                            else {}
                        )
                        if filter_name == "signal_cooldown":
                            parameters["cooldown_minutes"] = (
                                _RECENT_SIGNAL_COOLDOWN_MINUTES
                            )
                        record_candidate_filter_trace(
                            candidate,
                            run_id=run_id,
                            raise_on_error=True,
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
                batch_already_recorded = False
                try:
                    record_filter_decision_batch(
                        run_id=run_id,
                        candidate_kind="opportunity",
                        filter_name="paper_trader_workset",
                        stage="paper_trader_evaluation",
                        candidate_facts=batch_facts,
                        parameters={
                            "scan_limit": 250,
                            "selection_limit": 3,
                            "rotation": "balanced_never_seen_and_oldest_retry",
                        },
                        evaluated_at=evaluated_at,
                        raise_on_error=True,
                    )
                except RuntimeError as exc:
                    if str(exc) == "filter_evidence_batch_not_inserted":
                        # Batch dedup_key = hash(run_id|...) — same run_id means
                        # this evaluation was already committed by a prior call.
                        # Individual records also exist (INSERT OR IGNORE). Treat
                        # as idempotent success rather than aborting the SAVEPOINT.
                        logger.warning(
                            "paper trader filter batch already recorded run_id=%s "
                            "— idempotent skip",
                            run_id,
                        )
                        batch_already_recorded = True
                    else:
                        raise
                if not batch_already_recorded:
                    try:
                        mark_opportunities_ranked(
                            [
                                str(candidate.get("opportunity_id") or "")
                                for candidate in candidates
                            ],
                            evaluated_at,
                        )
                    except RuntimeError as exc:
                        if str(exc).startswith("ranked_opportunity_set_changed:"):
                            # Rotation cursor advance is best-effort; evidence
                            # records are already written. Partial advance means
                            # some opportunities will be re-presented next run.
                            logger.warning(
                                "paper trader ranked cursor partial: %s "
                                "— evidence committed without full rotation advance",
                                exc,
                            )
                        else:
                            raise
            except Exception:
                db.execute("ROLLBACK TO SAVEPOINT paper_filter_evidence")
                db.execute("RELEASE SAVEPOINT paper_filter_evidence")
                raise
            else:
                db.execute("RELEASE SAVEPOINT paper_filter_evidence")
        return True
    except Exception as exc:
        logger.warning("paper trader filter evidence unavailable: %s", exc)
        return False


def _persist_signal_enqueue_evidence(
    candidate: dict,
    *,
    run_id: str | None,
    decision: str,
    reason_code: str,
    reason_detail: str | None = None,
    raise_on_error: bool = False,
) -> bool:
    """Record actual queue insertion outcome separately from batch selection."""
    try:
        from aletheia.analytics.filter_evidence import record_candidate_filter_trace

        record_candidate_filter_trace(
            candidate,
            run_id=run_id,
            trace=[{
                "filter_name": "signal_enqueue",
                "stage": "paper_trader_enqueue",
                "decision": decision,
                "reason_code": reason_code,
                "reason_detail": reason_detail,
                "inputs": {"target_opportunity_status": "queued"},
                "parameters": {},
            }],
            raise_on_error=raise_on_error,
        )
        return True
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning(
            "signal enqueue evidence unavailable opportunity=%s: %s",
            candidate.get("opportunity_id"),
            exc,
        )
        return False


# Position C (2026-07-02): _handle_ruleset_entry removed with the rules engine.
# The paper trader no longer produces RuleSet-driven signals; it is purely
# opportunity-driven (ranked → signal_pending → PI decides).
