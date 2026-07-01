"""Risk Engine — enforces hard limits. Agent cannot modify these.

Change-controlled module (guide §31.3). Implements the §21.3 check matrix.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.data.symbols import to_canonical
from aletheia.storage.models import RiskDecision, TradeProposal
from aletheia.storage.repositories import insert_risk_decision, list_positions

logger = logging.getLogger("aletheia.risk.engine")

# Live market data older than this (seconds) is considered stale for live orders.
_MAX_DATA_AGE_SECONDS = 120
# Demo orders get a looser freshness bound: market data is collected every 5 min, so
# the tight 120s live bound would reject most healthy demo trades. This catches only a
# genuinely dead feed (several missed collection cycles) — a cheap anti-foot-gun so
# autonomous demo trades aren't placed against a frozen price.
_MAX_DEMO_DATA_AGE_SECONDS = 1800

# Serialize proposal evaluation. The daily-loss check can auto-trip the kill switch
# mid-evaluation (writes config); without serialization a concurrently-evaluated
# proposal could read the pre-trip config and slip through (TOCTOU). APScheduler
# runs jobs on a threadpool, so this lock closes that window.
_eval_lock = threading.Lock()
# Strategy lifecycle states eligible for live execution.
_LIVE_ELIGIBLE_STATES = {"live_candidate", "live_limited"}


def _is_live(execution_mode: str) -> bool:
    return execution_mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited")


def _market_data_age_seconds(symbol: str) -> float | None:
    """Age in seconds of the latest market snapshot for symbol, or None if absent."""
    from aletheia.storage.repositories import list_market_snapshots
    rows = list_market_snapshots(symbol=symbol, limit=1)
    if not rows:
        return None
    ts = rows[0].get("snapshot_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return None


@dataclass
class RiskCheckResult:
    approved: bool = False
    risk_score: float = 0.0
    violations: list[str] = field(default_factory=list)
    allowed_execution_mode: str = "paper_internal"
    message: str = ""
    decision_id: str | None = None


def evaluate_proposal(proposal: TradeProposal) -> RiskCheckResult:
    """Run all risk checks against a trade proposal. Returns a decision.

    Serialized via _eval_lock so a kill-switch trip during one evaluation is
    visible to the next proposal (closes the daily-loss TOCTOU window).
    """
    with _eval_lock:
        return _evaluate_proposal_locked(proposal)


def _evaluate_proposal_locked(proposal: TradeProposal) -> RiskCheckResult:
    cfg = load_config()
    risk = cfg.risk
    violations = []

    # 0. Kill switch — global brake, rejects everything when engaged.
    if cfg.runtime.kill_switch_active:
        violations.append("kill_switch_active")

    # 1. Symbol filter (whitelist or blacklist mode, single list)
    sym_canonical = to_canonical(proposal.symbol)
    filter_set = {to_canonical(s) for s in risk.symbol_list}
    if risk.symbol_filter_mode == "blacklist":
        if sym_canonical in filter_set:
            violations.append(f"symbol_blocked:{proposal.symbol}")
    else:
        if filter_set and sym_canonical not in filter_set:
            violations.append(f"symbol_not_allowed:{proposal.symbol}")

    # 2. Market allowed
    if proposal.market_type == "spot" and not cfg.exchange.enabled_markets.get("spot"):
        violations.append("spot_market_disabled")
    if proposal.market_type == "usdt_futures" and not cfg.exchange.enabled_markets.get("usdt_futures"):
        violations.append("futures_market_disabled")

    # 3. Live state
    live_state = cfg.live.state
    if proposal.execution_mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited") and live_state == "live_disabled":
        violations.append("live_disabled")

    # 4. Permission mode
    perm = cfg.agent.permission_mode
    if proposal.execution_mode != "paper_internal" and perm == "Observe":
        violations.append(f"permission_mode_insufficient:{perm}")

    # 5. Max position size. Live and demo each have a notional cap so paper
    #    promotion evidence is built from bounded trades (distinct tokens: a demo
    #    size rejection must not count toward live-critical violations).
    #    Portfolio drawdown scaling: if current equity is significantly below peak,
    #    reduce the effective cap proportionally (industry standard: halve size at
    #    10% drawdown, reject at 20% drawdown).
    drawdown_scale = _portfolio_drawdown_scale(proposal.execution_mode)
    if proposal.quantity and proposal.price:
        notional = proposal.quantity * proposal.price
        if _is_live(proposal.execution_mode):
            effective_cap = risk.max_live_position_notional_usdt * drawdown_scale
            if notional > effective_cap:
                violations.append(f"exceeds_max_position_notional:{notional}>{effective_cap}")
        else:
            effective_cap = risk.max_demo_position_notional_usdt * drawdown_scale
            if notional > effective_cap:
                violations.append(f"exceeds_demo_position_notional:{notional}>{effective_cap}")

    # 6. Max open positions
    # trade_school policy (D2-C02): shared pool — serenity and main-track positions
    # occupy the same max_open_positions quota. Isolated pools (serenity only counts
    # against serenity quota) are a Phase 2 decision deferred until serenity_executor
    # is implemented. Until then ALL open positions count regardless of trade_school.
    open_positions = list_positions(status="open")
    if _is_live(proposal.execution_mode):
        live_positions = [p for p in open_positions if _is_live(p.get("execution_mode", ""))]
        if len(live_positions) >= risk.max_open_positions_live:
            violations.append(f"max_live_positions_reached:{len(live_positions)}")
    else:
        demo_positions = [p for p in open_positions if not _is_live(p.get("execution_mode", ""))]
        if len(demo_positions) >= risk.max_open_positions_demo:
            violations.append(f"max_demo_positions_reached:{len(demo_positions)}")

    # 6b. No duplicate open position for same symbol in the same pool (demo or live).
    #     Prevents the system from opening a second position when one already exists
    #     on a different broker in the same pool (e.g. paper_internal + okx_demo both
    #     holding ETHUSDT short at the same time).
    prop_is_live = _is_live(proposal.execution_mode)
    same_symbol_open = [
        p for p in open_positions
        if p.get("symbol") == proposal.symbol
        and _is_live(p.get("execution_mode", "")) == prop_is_live
    ]
    if same_symbol_open:
        violations.append(f"symbol_already_open:{proposal.symbol}")

    # 7. Short allowed
    if proposal.direction == "short":
        if _is_live(proposal.execution_mode) and not risk.allow_short_live:
            violations.append("short_not_allowed_live")
        elif not _is_live(proposal.execution_mode) and not risk.allow_short_demo:
            violations.append("short_not_allowed_demo")

    # 8. Max daily loss — global brake. If today's realized loss breaches the limit,
    #    reject and auto-trip the kill switch (guide §21.3, §2.4).
    #    Respects kill_switch_enabled_live / kill_switch_enabled_demo toggles:
    #    the daily-loss violation is always recorded, but the kill switch only
    #    auto-trips when the matching toggle is enabled.
    from aletheia.portfolio.pnl import today_realized_pnl
    # Capital base is mode-specific (CR-RE-03): demo daily-loss must scale off the
    # DEMO base (starting_capital_usdt), not the much smaller live cap. Using the
    # live cap ($100) for a demo account meant a ~$1 daily-loss limit, so any small
    # demo loss spuriously tripped the brake and rejected every subsequent demo
    # trade for the rest of the UTC day. Mirrors check #13's demo/live base split.
    if _is_live(proposal.execution_mode):
        capital_base = risk.max_live_limited_cap_usdt or 0.0
    else:
        capital_base = risk.starting_capital_usdt or 0.0
    daily_loss_limit = capital_base * (risk.max_daily_loss_pct / 100.0)
    realized = today_realized_pnl(execution_mode=proposal.execution_mode)
    if daily_loss_limit > 0 and realized <= -daily_loss_limit:
        violations.append(f"max_daily_loss_exceeded:{realized}<=-{daily_loss_limit}")
        is_live = _is_live(proposal.execution_mode)
        ks_enabled = risk.kill_switch_enabled_live if is_live else risk.kill_switch_enabled_demo
        if ks_enabled and not cfg.runtime.kill_switch_active:
            from aletheia.risk.kill_switch import activate as _trip
            _trip(reason=f"daily loss {realized} breached limit -{daily_loss_limit}", actor="system")

    # 9a. Exchange allowlist — reject if target exchange != allowed_exchange.
    # binance_testnet/binance_live_limited and okx_demo/okx_live_limited are self-contained
    # (own credentials, independent of cfg.exchange) so exempt from allowlist checks.
    if proposal.execution_mode in ("binance_testnet", "binance_live_limited", "okx_demo", "okx_live_limited"):
        pass  # self-contained exchange modes use their own credentials
    else:
        if risk.allowed_exchange and cfg.exchange.name != risk.allowed_exchange:
            violations.append(f"exchange_not_allowed:{cfg.exchange.name}!={risk.allowed_exchange}")
        # 9. Account mode match — order mode and exchange account must agree.
        #    A live order requires exchange.mode == 'live'; a real demo order requires
        #    exchange.mode == 'demo'. Without the demo side of this check, a bitget_demo
        #    proposal evaluated while exchange.mode == 'live' would place a REAL live order
        #    through the demo path (trust-boundary hole). paper_internal is local sim and
        #    is exempt (it never touches the exchange).
        if _is_live(proposal.execution_mode) and cfg.exchange.mode != "live":
            violations.append(f"account_mode_mismatch:order=live,exchange={cfg.exchange.mode}")
        if proposal.execution_mode == "bitget_demo" and cfg.exchange.mode != "demo":
            violations.append(f"account_mode_mismatch:order=demo,exchange={cfg.exchange.mode}")

    # 10. Max leverage (futures). Per-asset-tier caps + account-level flat cap.
    if proposal.market_type == "usdt_futures" and proposal.leverage:
        from aletheia.trading.instruments import get_max_leverage, get_asset_tier
        tier_cap = get_max_leverage(proposal.symbol, _is_live(proposal.execution_mode), cfg)
        if proposal.leverage > tier_cap:
            tier = get_asset_tier(proposal.symbol)
            violations.append(f"exceeds_max_leverage:{proposal.leverage}>{tier_cap}(tier={tier})")

    # Live-only checks: data freshness, single-trade risk, strategy lifecycle.
    if _is_live(proposal.execution_mode):
        # 11. Data freshness
        age = _market_data_age_seconds(proposal.symbol)
        if age is None or age > _MAX_DATA_AGE_SECONDS:
            violations.append(f"stale_market_data:age={age}")

        # 12. Total live exposure limit (open positions + proposed notional).
        if proposal.price and proposal.quantity:
            current_exposure = _current_live_exposure()
            projected = current_exposure + (proposal.price * proposal.quantity)
            live_exposure_cap = risk.max_total_live_exposure_pct / 100.0 * risk.max_live_limited_cap_usdt
            if live_exposure_cap > 0 and projected > live_exposure_cap:
                violations.append(f"max_total_live_exposure_exceeded:{projected}>{live_exposure_cap}")

        # 13. Max single trade risk must be calculable and within cap.
        if not proposal.stop_price:
            violations.append("max_single_trade_risk_missing_stop_price")
        elif proposal.price and proposal.quantity:
            risk_amount = abs(proposal.price - proposal.stop_price) * proposal.quantity
            max_risk = capital_base * (risk.max_single_trade_risk_pct / 100.0)
            if max_risk > 0 and risk_amount > max_risk:
                violations.append(f"exceeds_single_trade_risk:{risk_amount}>{max_risk}")

        # 14. Strategy lifecycle state — live needs a live-eligible strategy.
        from aletheia.storage.repositories import get_strategy
        if proposal.strategy_id is None:
            violations.append("strategy_not_live_eligible:missing")
        else:
            strat = get_strategy(proposal.strategy_id)
            if strat is None or strat.get("state") not in _LIVE_ELIGIBLE_STATES:
                state = strat.get("state") if strat else "missing"
                violations.append(f"strategy_not_live_eligible:{state}")
    else:
        # Demo single-trade risk: paper trades may omit a stop (we don't force one),
        # but when a stop is present the loss-at-stop must stay within the demo cap.
        # Distinct token keeps this out of the live-critical-violation count.
        if proposal.stop_price and proposal.price and proposal.quantity:
            demo_risk_amount = abs(proposal.price - proposal.stop_price) * proposal.quantity
            demo_capital = risk.starting_capital_usdt or 0.0
            demo_max_risk = demo_capital * (risk.max_demo_single_trade_risk_pct / 100.0)
            if demo_max_risk > 0 and demo_risk_amount > demo_max_risk:
                violations.append(f"exceeds_demo_single_trade_risk:{demo_risk_amount}>{demo_max_risk}")

        # Demo data freshness — bitget_demo places a REAL exchange order, so it must
        # not trade against a frozen feed. paper_internal (local sim) is exempt: it has
        # no exchange-side price and runs even when collection is paused. Looser bound
        # than live (see _MAX_DEMO_DATA_AGE_SECONDS).
        if proposal.execution_mode in ("bitget_demo", "okx_demo"):
            demo_age = _market_data_age_seconds(proposal.symbol)
            if demo_age is None or demo_age > _MAX_DEMO_DATA_AGE_SECONDS:
                violations.append(f"stale_market_data_demo:age={demo_age}")

    # 15. Lesson-based constraints — accepted lessons with actionable recommendations
    #     can impose symbol/direction blocks learned from past failures.
    lesson_violations = _check_lesson_constraints(proposal)
    violations.extend(lesson_violations)

    # 16. Portfolio correlation guard — sector concentration + portfolio BTC beta.
    correlation_violations = _check_correlation_exposure(proposal, cfg)
    violations.extend(correlation_violations)

    # 17. Account-level leverage ratio (futures only, additive).
    if proposal.market_type == "usdt_futures":
        acct_lev_violations = _check_account_leverage_ratio(proposal, cfg)
        violations.extend(acct_lev_violations)

    # 18. Liquidation buffer (futures only, additive).
    if proposal.market_type == "usdt_futures" and proposal.leverage and proposal.price:
        liq_violations = _check_liquidation_buffer(proposal, cfg)
        violations.extend(liq_violations)

    # 19. Per-symbol cumulative notional cap (CUM-ADD). Same-symbol adds average-merge
    #     into one position (paper_broker / live brokers), so the single-position cap
    #     (#5) does not bound repeated accumulation. Sum existing open notional for this
    #     symbol in the same live/demo class + the proposed notional; reject if over cap.
    per_symbol_cap = getattr(risk, "max_per_symbol_notional_usdt", 0.0)
    if per_symbol_cap and per_symbol_cap > 0 and proposal.price and proposal.quantity:
        prop_is_live = _is_live(proposal.execution_mode)
        existing_sym_notional = 0.0
        for p in open_positions:
            if p.get("symbol") != proposal.symbol:
                continue
            if _is_live(p.get("execution_mode", "")) != prop_is_live:
                continue
            try:
                q = float(p.get("quantity") or 0)
                px = float(p.get("current_price") or p.get("entry_price") or 0)
                existing_sym_notional += abs(q * px)
            except (TypeError, ValueError):
                continue
        projected = existing_sym_notional + abs(proposal.price * proposal.quantity)
        if projected > per_symbol_cap:
            violations.append(
                f"exceeds_per_symbol_notional:{proposal.symbol}:"
                f"{projected:.0f}>{per_symbol_cap:.0f}"
            )

    # Determine execution mode
    if not violations:
        allowed_mode = proposal.execution_mode
    else:
        allowed_mode = "paper_internal"

    approved = len(violations) == 0
    risk_score = len(violations) * 0.2

    result = RiskCheckResult(
        approved=approved,
        risk_score=min(risk_score, 1.0),
        violations=violations,
        allowed_execution_mode=allowed_mode,
        message="Approved" if approved else f"Rejected: {', '.join(violations)}",
    )

    # Persist decision
    import json
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=approved,
        risk_score=result.risk_score,
        violations=json.dumps(violations),
        allowed_execution_mode=allowed_mode,
        # Preserve the requested mode verbatim — allowed_mode is downgraded to
        # paper_internal on rejection, which erased the live/demo signal that
        # strategy_lifecycle needs to classify a live rejection as critical (CR-RE-01).
        proposal_execution_mode=proposal.execution_mode,
        message=result.message,
    )
    insert_risk_decision(decision)
    result.decision_id = decision.id

    return result


def _check_lesson_constraints(proposal) -> list[str]:
    """Check accepted lessons for actionable constraints.

    Lessons with category='risk' and recommendation_json containing:
      - {"action": "block_symbol", "symbol": "X"} → reject trades on symbol X
      - {"action": "block_direction", "direction": "short"} → reject shorts
      - {"action": "reduce_size", "factor": 0.5} → (advisory, logged but not enforced here)
      - {"action": "block_scanner", "scanner": "X"} → reject trades from scanner X
    """
    violations = []
    try:
        from aletheia.learning.lessons import LessonStore
        import json
        store = LessonStore()
        # L-Bug-E fix: 'accepted' is a transient state (lesson_reviewer calls
        # accept then mark_applied in the same run), so query 'applied' instead.
        active_lessons = store.list_all(status="applied", limit=50)
        sym_canonical = to_canonical(proposal.symbol)

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        for lesson in active_lessons:
            if not lesson.recommendation_json:
                continue
            try:
                rec = json.loads(lesson.recommendation_json)
            except (ValueError, TypeError):
                continue

            # Guard: skip expired lessons (TTL exceeded).
            if lesson.expires_at and now_iso > lesson.expires_at:
                logger.debug("lesson %s expired at %s, skipping", lesson.lesson_id, lesson.expires_at)
                continue

            # Guard: dry-run window — log advisory only, do not block.
            in_dry_run = lesson.dry_run_until and now_iso < lesson.dry_run_until
            if in_dry_run:
                logger.info(
                    "lesson %s in dry-run window (until %s), advisory only",
                    lesson.lesson_id, lesson.dry_run_until,
                )

            action = rec.get("action")
            if action == "block_symbol":
                blocked_sym = to_canonical(rec.get("symbol", ""))
                if blocked_sym and blocked_sym == sym_canonical:
                    token = f"lesson_block_symbol:{lesson.lesson_id}:{blocked_sym}"
                    if in_dry_run:
                        logger.info("dry-run advisory (would block): %s", token)
                    else:
                        violations.append(token)

            elif action == "block_direction":
                blocked_dir = rec.get("direction", "")
                if blocked_dir and proposal.direction == blocked_dir:
                    token = f"lesson_block_direction:{lesson.lesson_id}:{blocked_dir}"
                    if in_dry_run:
                        logger.info("dry-run advisory (would block): %s", token)
                    else:
                        violations.append(token)

            elif action == "block_scanner":
                blocked_scanner = rec.get("scanner", "")
                if blocked_scanner and getattr(proposal, "scanner_family", None) == blocked_scanner:
                    token = f"lesson_block_scanner:{lesson.lesson_id}:{blocked_scanner}"
                    if in_dry_run:
                        logger.info("dry-run advisory (would block): %s", token)
                    else:
                        violations.append(token)

            elif action == "reduce_size":
                # Advisory only — log but do not block (docstring intent preserved)
                factor = float(rec.get("factor", 1.0))
                logger.info(
                    "lesson reduce_size advisory: lesson=%s factor=%.2f (not enforced as hard block)",
                    lesson.lesson_id, factor,
                )

    except Exception as e:
        logger.warning("_check_lesson_constraints failed (safe-reject): %s", e)
        violations.append("lesson_check_error:internal_failure")
    return violations


def _portfolio_drawdown_scale(execution_mode: str) -> float:
    """Compute position size scaling factor based on portfolio drawdown.

    Uses recent portfolio_snapshots to detect peak-to-current drawdown.
    Returns 1.0 (no reduction) to 0.0 (reject entirely) as a multiplier
    on position size caps.

    Scale: 0-5% DD → 1.0, 5-10% DD → linear from 1.0 to 0.5, 10-20% → 0.5 to 0.0

    Data source by mode (this is the bug fix — see below):
      - Exchange-backed modes (okx_demo / *_demo / live): use the
        `exchange_account` snapshot rows, whose total_value_usdt is the REAL
        account equity. The per-mode rows (e.g. okx_demo) store position
        *notional* (Σ qty×price), NOT equity — so closing a winning position
        collapses notional (272→16) and the old code misread that as a 94%
        "drawdown", zeroing the size cap and rejecting every demo proposal
        while the real account sat untouched at ~$88k. Drawdown must track
        equity, not how much notional happens to be open right now.
      - paper_internal: no exchange account exists; its mark-to-market
        total_value_usdt IS its equity, so keep the existing path.
    """
    try:
        # View-completeness gate (P0, 2026-07-02): for exchange-backed modes, if the
        # active broker is missing from the unified view (e.g. OKX API down), the
        # equity snapshots may reflect the WRONG broker's balance — computing a
        # drawdown from that fabricates a false ~99% DD that zeroes the size cap.
        # Skip drawdown scaling (return 1.0, the $-notional caps still bound size).
        if execution_mode != "paper_internal":
            from aletheia.portfolio.exchange_sync import active_broker_view_complete
            _complete, _ = active_broker_view_complete()
            if not _complete:
                return 1.0

        from aletheia.storage.repositories import list_portfolio_snapshots
        snapshots = list_portfolio_snapshots(limit=50)
        if len(snapshots) < 3:
            return 1.0

        if execution_mode == "paper_internal":
            mode_snapshots = [
                s for s in snapshots
                if s.get("execution_mode") in ("paper_internal", "bitget_demo")
            ]
        else:
            # Real account equity lives in the exchange_account rows.
            mode_snapshots = [
                s for s in snapshots if s.get("execution_mode") == "exchange_account"
            ]
        if len(mode_snapshots) < 3:
            return 1.0

        values = [float(s.get("total_value_usdt") or 0) for s in mode_snapshots]
        peak = max(values)
        current = values[-1]

        if peak <= 0:
            return 1.0

        drawdown_pct = (peak - current) / peak * 100.0

        if drawdown_pct <= 5.0:
            return 1.0
        elif drawdown_pct <= 10.0:
            return 1.0 - (drawdown_pct - 5.0) / 5.0 * 0.5
        elif drawdown_pct <= 20.0:
            return 0.5 - (drawdown_pct - 10.0) / 10.0 * 0.5
        else:
            return 0.0
    except Exception:
        return 1.0


def _current_live_exposure() -> float:
    """Sum absolute notional exposure for live open positions."""
    total = 0.0
    for row in list_positions(status="open"):
        if not _is_live(row.get("execution_mode", "")):
            continue
        try:
            quantity = float(row.get("quantity") or 0.0)
            price = float(row.get("current_price") or row.get("entry_price") or 0.0)
            total += abs(quantity * price)
        except (TypeError, ValueError):
            continue
    return total


def _check_correlation_exposure(proposal: TradeProposal, cfg) -> list[str]:
    """Check #16: sector concentration + portfolio BTC beta.

    Returns an empty list when correlation_guard_enabled=False or data is insufficient.
    Never raises — any DB error silently returns [] (non-blocking guard).
    """
    risk = cfg.risk
    if not getattr(risk, "correlation_guard_enabled", True):
        return []
    violations: list[str] = []
    try:
        from aletheia.storage.repositories import list_positions, list_opportunities
        from aletheia.storage.sqlite import get_db

        open_positions = list_positions(status="open")
        if not open_positions:
            return []

        # ── Sector concentration ──────────────────────────────────────────────
        # Gather scanner_family for each open position via its opportunity_id
        families: list[str] = []
        prop_notional = 0.0
        if proposal.price and proposal.quantity:
            prop_notional = abs(proposal.price * proposal.quantity)

        pos_notionals: dict[str, float] = {}
        for pos in open_positions:
            sym = pos.get("symbol", "")
            try:
                qty = float(pos.get("quantity") or 0.0)
                price = float(pos.get("current_price") or pos.get("entry_price") or 0.0)
                pos_notionals[sym] = abs(qty * price)
            except (TypeError, ValueError):
                pos_notionals[sym] = 0.0

            opp_id = pos.get("opportunity_id")
            if opp_id:
                try:
                    db = get_db()
                    row = db.execute(
                        "SELECT scanner_family FROM opportunities WHERE id = ?", (opp_id,)
                    ).fetchone()
                    if row and row[0]:
                        families.append(row[0])
                except Exception:
                    pass

        # Determine proposal's own family from its opportunity
        prop_family: str | None = None
        if proposal.opportunity_id:
            try:
                db = get_db()
                row = db.execute(
                    "SELECT scanner_family FROM opportunities WHERE id = ?",
                    (proposal.opportunity_id,)
                ).fetchone()
                if row and row[0]:
                    prop_family = row[0]
            except Exception:
                pass

        if prop_family and families:
            total_notional = sum(pos_notionals.values()) + prop_notional
            if total_notional > 0:
                family_notional = sum(
                    n for sym, n in pos_notionals.items()
                    if _symbol_family(sym, open_positions) == prop_family
                ) + prop_notional
                sector_pct = (family_notional / total_notional) * 100
                max_pct = getattr(risk, "max_sector_concentration_pct", 60.0)
                if sector_pct > max_pct:
                    violations.append(
                        f"sector_concentration:{prop_family}:{sector_pct:.0f}%>{max_pct:.0f}%"
                    )

        # ── Portfolio BTC beta ────────────────────────────────────────────────
        max_beta = getattr(risk, "max_portfolio_beta", 3.0)
        prop_beta = _lookup_beta(proposal.symbol)
        if prop_beta is not None and prop_notional > 0:
            total_notional = sum(pos_notionals.values()) + prop_notional
            if total_notional > 0:
                weighted_beta = prop_beta * prop_notional
                for pos in open_positions:
                    sym = pos.get("symbol", "")
                    b = _lookup_beta(sym)
                    n = pos_notionals.get(sym, 0.0)
                    if b is not None and n > 0:
                        weighted_beta += abs(b) * n
                portfolio_beta = weighted_beta / total_notional
                if portfolio_beta > max_beta:
                    violations.append(
                        f"portfolio_beta:{portfolio_beta:.1f}>{max_beta:.1f}"
                    )
    except Exception as exc:
        logger.warning("_check_correlation_exposure failed (safe-reject): %s", exc)
        violations.append("correlation_check_error:internal_failure")
    return violations


def _lookup_beta(symbol: str) -> float | None:
    """Fetch the most recent btc_beta for a symbol from universe_feature_snapshots."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT btc_beta FROM universe_feature_snapshots "
            "WHERE symbol = ? ORDER BY ts DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return None


def _symbol_family(symbol: str, positions: list[dict]) -> str | None:
    """Look up scanner_family for a symbol via its open position's opportunity_id."""
    for pos in positions:
        if pos.get("symbol") != symbol:
            continue
        opp_id = pos.get("opportunity_id")
        if not opp_id:
            continue
        try:
            from aletheia.storage.sqlite import get_db
            db = get_db()
            row = db.execute(
                "SELECT scanner_family FROM opportunities WHERE id = ?", (opp_id,)
            ).fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Check #17: Account-level leverage ratio
# ---------------------------------------------------------------------------

def _check_account_leverage_ratio(proposal: TradeProposal, cfg) -> list[str]:
    """Reject if adding this trade would push account leverage ratio above cap.

    account_leverage = sum(|pos_notional| * pos_leverage) / equity
    Deliberate skip (returns []) only when equity is genuinely unknown — that is
    a documented can't-compute case, not an error. An actual exception fails CLOSED
    (returns a violation) per §17.2/EH-1: never allow leveraged exposure on a broken
    check.
    """
    risk = cfg.risk
    is_live = _is_live(proposal.execution_mode)
    max_ratio = (
        getattr(risk, "max_account_leverage_live", 1.5)
        if is_live else getattr(risk, "max_account_leverage_demo", 3.0)
    )
    try:
        open_positions = list_positions(status="open")
        relevant = [
            p for p in open_positions
            if _is_live(p.get("execution_mode", "")) == is_live
        ]

        weighted_notional = 0.0
        for p in relevant:
            try:
                qty = float(p.get("quantity") or 0)
                price = float(p.get("current_price") or p.get("entry_price") or 0)
                lev = float(p.get("leverage") or 1)
                weighted_notional += abs(qty * price) * lev
            except (TypeError, ValueError):
                continue

        if proposal.price and proposal.quantity:
            prop_lev = float(proposal.leverage or 1)
            weighted_notional += abs(proposal.price * proposal.quantity) * prop_lev

        equity = _account_equity(is_live, cfg)
        if not equity or equity <= 0:
            return []

        ratio = weighted_notional / equity
        if ratio > max_ratio:
            return [f"account_leverage_ratio:{ratio:.2f}x>{max_ratio:.1f}x"]
    except Exception as e:
        # Fail CLOSED (§17.2 / EH-1): a leverage check that errors must reject, not
        # silently allow leveraged exposure. Mirrors the lesson/correlation checks.
        logger.warning("_check_account_leverage_ratio failed (safe-reject): %s", e)
        return ["account_leverage_check_error:internal_failure"]
    return []


# ---------------------------------------------------------------------------
# Check #18: Liquidation buffer
# ---------------------------------------------------------------------------

def _check_liquidation_buffer(proposal: TradeProposal, cfg) -> list[str]:
    """Reject if estimated liquidation price is within buffer_pct of entry."""
    if not proposal.leverage or proposal.leverage <= 1:
        return []
    risk = cfg.risk
    buffer_pct = getattr(risk, "min_liquidation_buffer_pct", 5.0) / 100.0

    from aletheia.trading.instruments import calc_liquidation_price
    liq_price = calc_liquidation_price(
        proposal.price, proposal.direction, proposal.leverage, proposal.symbol
    )
    if liq_price is None:
        return []

    gap_pct = abs(proposal.price - liq_price) / proposal.price
    if gap_pct < buffer_pct:
        return [
            f"liquidation_too_close:{gap_pct*100:.1f}%<{buffer_pct*100:.1f}%"
            f"(liq={liq_price:.2f},lev={proposal.leverage}x)"
        ]
    return []


# ---------------------------------------------------------------------------
# Shared helpers for leverage checks
# ---------------------------------------------------------------------------

def _account_equity(is_live: bool, cfg) -> float | None:
    """Best estimate of account equity in USDT."""
    try:
        from aletheia.portfolio import exchange_sync
        cached = exchange_sync.get_cached_state()
        if cached:
            equity = cached.get("equity_usdt") or cached.get("total_equity_usdt")
            if equity:
                return float(equity)
    except Exception:
        pass
    if is_live:
        return float(cfg.risk.max_live_limited_cap_usdt or 0) or None
    return float(cfg.risk.starting_capital_usdt or 0) or None


def check_account_leverage(cfg=None) -> dict:
    """Public API: compute current account leverage ratio (for dashboard/monitoring)."""
    if cfg is None:
        cfg = load_config()
    is_live = cfg.exchange.mode == "live"
    max_ratio = (
        getattr(cfg.risk, "max_account_leverage_live", 1.5)
        if is_live else getattr(cfg.risk, "max_account_leverage_demo", 3.0)
    )
    open_positions = list_positions(status="open")
    relevant = [
        p for p in open_positions
        if _is_live(p.get("execution_mode", "")) == is_live
    ]
    weighted_notional = 0.0
    positions_detail = []
    for p in relevant:
        try:
            qty = float(p.get("quantity") or 0)
            price = float(p.get("current_price") or p.get("entry_price") or 0)
            lev = float(p.get("leverage") or 1)
            notional = abs(qty * price)
            weighted_notional += notional * lev
            positions_detail.append({
                "symbol": p.get("symbol"),
                "notional": notional,
                "leverage": lev,
            })
        except (TypeError, ValueError):
            continue

    equity = _account_equity(is_live, cfg)
    ratio = weighted_notional / equity if equity and equity > 0 else None
    return {
        "ratio": ratio,
        "cap": max_ratio,
        "violation": ratio > max_ratio if ratio is not None else False,
        "equity": equity,
        "positions": positions_detail,
    }
