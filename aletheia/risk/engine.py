"""Risk Engine — enforces hard limits. Agent cannot modify these.

Change-controlled module (guide §31.3). Implements the §21.3 check matrix.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.data.symbols import to_canonical
from aletheia.storage.models import RiskDecision, TradeProposal
from aletheia.storage.repositories import insert_risk_decision, list_positions

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
    return execution_mode == "bitget_live_limited"


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
    if proposal.execution_mode in ("bitget_live_limited",) and live_state == "live_disabled":
        violations.append("live_disabled")

    # 4. Permission mode
    perm = cfg.agent.permission_mode
    if proposal.execution_mode != "paper_internal" and perm == "Observe":
        violations.append(f"permission_mode_insufficient:{perm}")

    # 5. Max position size. Live and demo each have a notional cap so paper
    #    promotion evidence is built from bounded trades (distinct tokens: a demo
    #    size rejection must not count toward live-critical violations).
    if proposal.quantity and proposal.price:
        notional = proposal.quantity * proposal.price
        if proposal.execution_mode == "bitget_live_limited":
            if notional > risk.max_live_position_notional_usdt:
                violations.append(f"exceeds_max_position_notional:{notional}>{risk.max_live_position_notional_usdt}")
        else:
            if notional > risk.max_demo_position_notional_usdt:
                violations.append(f"exceeds_demo_position_notional:{notional}>{risk.max_demo_position_notional_usdt}")

    # 6. Max open positions
    open_positions = list_positions(status="open")
    if proposal.execution_mode == "bitget_live_limited":
        live_positions = [p for p in open_positions if p.get("execution_mode") == "bitget_live_limited"]
        if len(live_positions) >= risk.max_open_positions_live:
            violations.append(f"max_live_positions_reached:{len(live_positions)}")
    else:
        demo_positions = [p for p in open_positions if p.get("execution_mode") != "bitget_live_limited"]
        if len(demo_positions) >= risk.max_open_positions_demo:
            violations.append(f"max_demo_positions_reached:{len(demo_positions)}")

    # 7. Short allowed
    if proposal.direction == "short":
        if proposal.execution_mode == "bitget_live_limited" and not risk.allow_short_live:
            violations.append("short_not_allowed_live")
        elif proposal.execution_mode != "bitget_live_limited" and not risk.allow_short_demo:
            violations.append("short_not_allowed_demo")

    # 8. Max daily loss — global brake. If today's realized loss breaches the limit,
    #    reject and auto-trip the kill switch (guide §21.3, §2.4).
    from aletheia.portfolio.pnl import today_realized_pnl
    capital_base = risk.max_live_limited_cap_usdt or 0.0
    daily_loss_limit = capital_base * (risk.max_daily_loss_pct / 100.0)
    realized = today_realized_pnl()
    if daily_loss_limit > 0 and realized <= -daily_loss_limit:
        violations.append(f"max_daily_loss_exceeded:{realized}<=-{daily_loss_limit}")
        if not cfg.runtime.kill_switch_active:
            from aletheia.risk.kill_switch import activate as _trip
            _trip(reason=f"daily loss {realized} breached limit -{daily_loss_limit}", actor="system")

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

    # 10. Max leverage (futures). Demo and live have separate caps.
    if proposal.market_type == "usdt_futures" and proposal.leverage:
        cap = risk.max_futures_leverage_live if _is_live(proposal.execution_mode) else risk.max_futures_leverage_demo
        if proposal.leverage > cap:
            violations.append(f"exceeds_max_leverage:{proposal.leverage}>{cap}")

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
        if proposal.execution_mode == "bitget_demo":
            demo_age = _market_data_age_seconds(proposal.symbol)
            if demo_age is None or demo_age > _MAX_DEMO_DATA_AGE_SECONDS:
                violations.append(f"stale_market_data_demo:age={demo_age}")

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
        message=result.message,
    )
    insert_risk_decision(decision)
    result.decision_id = decision.id

    return result


def _current_live_exposure() -> float:
    """Sum absolute notional exposure for live open positions."""
    total = 0.0
    for row in list_positions(status="open"):
        if row.get("execution_mode") != "bitget_live_limited":
            continue
        try:
            quantity = float(row.get("quantity") or 0.0)
            price = float(row.get("current_price") or row.get("entry_price") or 0.0)
            total += abs(quantity * price)
        except (TypeError, ValueError):
            continue
    return total
