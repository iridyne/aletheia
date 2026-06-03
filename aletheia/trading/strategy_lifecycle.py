"""Strategy lifecycle — forward-only state machine with promotion gates."""

from datetime import datetime

from aletheia.storage.models import StrategyRecord
from aletheia.storage.repositories import insert_strategy, update_strategy, get_strategy
from aletheia.files.jsonl import write_audit

LIFECYCLE_STATES = [
    "draft",
    "research_note",
    "strategy_hypothesis",
    "paper_candidate",
    "paper_running",
    "paper_passed",
    "live_candidate",
    "live_limited",
    "retired",
    "deprecated",
]

# Forward progress per state. retire/deprecate are escape hatches allowed from any
# active (non-terminal) state — see _allowed_targets().
FORWARD_TRANSITIONS = {
    "draft": ["research_note"],
    "research_note": ["strategy_hypothesis"],
    "strategy_hypothesis": ["paper_candidate"],
    "paper_candidate": ["paper_running"],
    "paper_running": ["paper_passed"],
    "paper_passed": ["live_candidate"],
    "live_candidate": ["live_limited"],
    "live_limited": [],
    "retired": ["deprecated"],
    "deprecated": [],
}

# Terminal states cannot be retired/deprecated further (deprecated is the end).
_TERMINAL = {"deprecated"}


def _allowed_targets(current: str) -> list[str]:
    """Forward target(s) plus retire/deprecate escape hatches for active states."""
    targets = list(FORWARD_TRANSITIONS.get(current, []))
    if current not in _TERMINAL:
        for escape in ("retired", "deprecated"):
            if escape != current and escape not in targets:
                targets.append(escape)
    return targets

DEFAULT_PROMOTION_GATES = {
    "min_paper_runtime_days": 14,
    "min_paper_trades": 30,
    "positive_expectancy_required": True,
    "max_drawdown_pct_allowed": 5.0,
    "max_consecutive_losses_allowed": 5,
    "critical_risk_violations_allowed": 0,
    "post_trade_reviews_required": True,
}


def create_strategy(name: str, description: str | None = None) -> StrategyRecord:
    """Create a new strategy in draft state."""
    strategy = StrategyRecord(name=name, description=description, state="draft")
    insert_strategy(strategy)
    write_audit({
        "actor": "agent",
        "source": "strategy_lifecycle",
        "event_type": "strategy_created",
        "entity_type": "strategy",
        "entity_id": strategy.id,
        "message": f"Strategy '{name}' created in draft state",
    })
    return strategy


def promote_strategy(strategy_id: str, target_state: str) -> dict:
    """Promote a strategy to a new state (forward-only)."""
    record = get_strategy(strategy_id)
    if record is None:
        return {"success": False, "error": "Strategy not found"}

    current = record["state"]

    # Validate transition (forward progress + retire/deprecate escape hatches)
    allowed = _allowed_targets(current)
    if target_state not in allowed:
        return {
            "success": False,
            "error": f"Cannot transition from '{current}' to '{target_state}'. Allowed: {allowed}",
        }

    # Check promotion gates for live transitions
    if target_state == "live_candidate":
        gate_result = check_promotion_gates(strategy_id)
        if not gate_result["passed"]:
            return {"success": False, "error": f"Promotion gates failed: {gate_result['failures']}"}

    update_strategy(strategy_id, state=target_state, updated_at=datetime.utcnow().isoformat())
    write_audit({
        "actor": "system",
        "source": "strategy_lifecycle",
        "event_type": "strategy_promoted",
        "entity_type": "strategy",
        "entity_id": strategy_id,
        "before": {"state": current},
        "after": {"state": target_state},
        "message": f"Strategy promoted: {current} -> {target_state}",
    })
    return {"success": True, "old_state": current, "new_state": target_state}


def check_promotion_gates(strategy_id: str) -> dict:
    """Check if a strategy meets promotion gates for live candidacy.

    MVP: simplified check. Full version would query trade history.
    """
    failures = []
    # In MVP, just check that the strategy exists and is in paper_passed
    record = get_strategy(strategy_id)
    if record is None:
        failures.append("strategy_not_found")
    elif record["state"] != "paper_passed":
        failures.append(f"must_be_in_paper_passed_state (currently: {record['state']})")

    return {"passed": len(failures) == 0, "failures": failures}
