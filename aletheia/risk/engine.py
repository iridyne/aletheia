"""Risk Engine — enforces hard limits. Agent cannot modify these.

Change-controlled module (guide §31.3). Implements the §21.3 check matrix.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.storage.models import RiskDecision, TradeProposal
from aletheia.storage.repositories import insert_risk_decision, list_positions

# Live market data older than this (seconds) is considered stale for live orders.
_MAX_DATA_AGE_SECONDS = 120
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
    """Run all risk checks against a trade proposal. Returns a decision."""
    cfg = load_config()
    risk = cfg.risk
    violations = []

    # 0. Kill switch — global brake, rejects everything when engaged.
    if cfg.runtime.kill_switch_active:
        violations.append("kill_switch_active")

    # 1. Symbol allowed
    if proposal.symbol not in risk.allowed_symbols:
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
    if proposal.execution_mode != "paper_internal" and perm in ("ReadOnly", "Research"):
        violations.append(f"permission_mode_insufficient:{perm}")

    # 5. Max position size (for live)
    if proposal.execution_mode == "bitget_live_limited":
        if proposal.quantity and proposal.price:
            notional = proposal.quantity * proposal.price
            if notional > risk.max_live_position_notional_usdt:
                violations.append(f"exceeds_max_position_notional:{notional}>{risk.max_live_position_notional_usdt}")

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

    # 9. Account mode match — a live order requires exchange.mode == 'live' (and vice-versa).
    if _is_live(proposal.execution_mode) and cfg.exchange.mode != "live":
        violations.append(f"account_mode_mismatch:order=live,exchange={cfg.exchange.mode}")

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

        # 12. Max single trade risk (only when a stop is provided — else uncomputable).
        if proposal.stop_price and proposal.price and proposal.quantity:
            risk_amount = abs(proposal.price - proposal.stop_price) * proposal.quantity
            max_risk = capital_base * (risk.max_single_trade_risk_pct / 100.0)
            if max_risk > 0 and risk_amount > max_risk:
                violations.append(f"exceeds_single_trade_risk:{risk_amount}>{max_risk}")

        # 13. Strategy lifecycle state — live needs a live-eligible strategy.
        if proposal.strategy_id:
            from aletheia.storage.repositories import get_strategy
            strat = get_strategy(proposal.strategy_id)
            if strat is None or strat.get("state") not in _LIVE_ELIGIBLE_STATES:
                state = strat.get("state") if strat else "missing"
                violations.append(f"strategy_not_live_eligible:{state}")

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
