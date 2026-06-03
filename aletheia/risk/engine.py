"""Risk Engine — enforces hard limits. Agent cannot modify these."""

from dataclasses import dataclass, field

from aletheia.config.loader import load_config
from aletheia.storage.models import RiskDecision, TradeProposal
from aletheia.storage.repositories import insert_risk_decision, list_positions


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
