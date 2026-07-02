"""Strategy lifecycle: forward-only state machine with promotion gates.

Validation layer: currently only **paper trading** validation exists (real-time
execution against live market via Bitget demo or internal paper broker). There is
no backtest, walk-forward, or PBO (Probability of Backtest Overfitting) layer —
strategies must accumulate 14+ days of live paper evidence before promotion gates
can pass. This is a known MVP limitation (not a bug); introducing a historical
backtest framework is explicitly out of scope per project red lines.
"""

import json
from datetime import datetime, timezone

from aletheia.files.jsonl import write_audit
from aletheia.storage.models import StrategyRecord
from aletheia.storage.repositories import (
    get_strategy,
    insert_strategy,
    list_knowledge_items,
    list_strategy_fills,
    list_strategy_orders,
    list_strategy_positions,
    list_strategy_risk_decisions,
    update_strategy,
)

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

_TERMINAL = {"deprecated"}

DEFAULT_PROMOTION_GATES = {
    "min_paper_runtime_days": 14,
    "min_paper_trades": 30,
    "positive_expectancy_required": True,
    "max_drawdown_pct_allowed": 5.0,
    "max_consecutive_losses_allowed": 5,
    "critical_risk_violations_allowed": 0,
    "post_trade_reviews_required": True,
}

# Permissions that are sufficient to permit live promotion/operation checks.
LIVE_PROMOTION_PERMISSION_MODES = {"Paper", "Full"}

# Risk violations that indicate strategy is not eligible for live-limited operation.
CRITICAL_RISK_TOKENS = {
    "live_disabled",
    "kill_switch_active",
    "account_mode_mismatch",
    "exceeds_max_position_notional",
    "exceeds_single_trade_risk",
    "max_daily_loss_exceeded",
    "stale_market_data",
    "strategy_not_live_eligible",
}


def _truthy_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)


def is_strategy_under_review(record: dict | None) -> bool:
    return _truthy_flag((record or {}).get("under_review"))


def is_paper_trading_paused(record: dict | None) -> bool:
    return _truthy_flag((record or {}).get("paper_trading_paused"))


def is_active_paper_strategy(record: dict | None) -> bool:
    if not record:
        return False
    return record.get("state") in {"paper_candidate", "paper_running"} and not is_paper_trading_paused(record)


def _allowed_targets(current: str) -> list[str]:
    """Forward target(s) plus retire/deprecate escape hatches for active states."""
    targets = list(FORWARD_TRANSITIONS.get(current, []))
    if current not in _TERMINAL:
        for escape in ("retired", "deprecated"):
            if escape != current and escape not in targets:
                targets.append(escape)
    return targets


def create_strategy(
    name: str,
    description: str | None = None,
    parameters: dict | None = None,
    direction_affinity: str = "both",
    applicable_regimes: str = "all",
) -> StrategyRecord:
    """Create a new strategy in draft state.

    `parameters` is an optional structured snapshot of the strategy's defining
    inputs (symbol, timeframe, entry/exit rules, sizing). It is persisted as a
    JSON string so promotion gates and post-trade reviews can later audit
    "what parameters was this strategy actually running" (trust §4.2). Defaults
    to an empty object for backward compatibility.

    `direction_affinity` declares the direction this strategy is designed for:
    "long" | "short" | "both". The paper ranker uses this to skip the strategy
    for mismatched opportunities in regime-aware filtering (Phase 2A).
    `applicable_regimes` is a comma-separated list of regime states where this
    strategy should be active, or "all" to run in every regime.
    """
    strategy = StrategyRecord(
        name=name,
        description=description,
        state="draft",
        parameters=json.dumps(parameters or {}),
        direction_affinity=direction_affinity,
        applicable_regimes=applicable_regimes,
    )
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


def _has_pi_approval(strategy_id: str) -> bool:
    """Check that PI Agent has written a recommend_promote record for this strategy."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM pi_decisions WHERE proposal_id = ? AND action = 'recommend_promote' LIMIT 1",
        (strategy_id,),
    ).fetchone()
    return row is not None


def promote_strategy(strategy_id: str, target_state: str) -> dict:
    """Promote a strategy to a new state (forward-only)."""
    record = get_strategy(strategy_id)
    if record is None:
        return {"success": False, "error": "Strategy not found"}

    current = record["state"]
    allowed = _allowed_targets(current)
    if target_state not in allowed:
        return {
            "success": False,
            "error": f"Cannot transition from '{current}' to '{target_state}'. Allowed: {allowed}",
        }

    if target_state == "paper_running":
        if not _has_pi_approval(strategy_id):
            return {
                "success": False,
                "error": "Promotion to paper_running requires PI Agent approval (recommend_promote record not found)",
            }
    if target_state == "paper_passed":
        gate_result = check_promotion_gates(strategy_id)
        if not gate_result["passed"]:
            return {"success": False, "error": f"Promotion gates failed: {gate_result['failures']}"}
    if target_state == "live_candidate":
        gate_result = check_promotion_gates(strategy_id)
        if not gate_result["passed"]:
            return {"success": False, "error": f"Promotion gates failed: {gate_result['failures']}"}
    if target_state == "live_limited":
        gate_result = check_live_limited_gates(strategy_id)
        if not gate_result["passed"]:
            return {"success": False, "error": f"Promotion gates failed: {gate_result['failures']}"}
        live_gate = _live_lifecycle_gate()
        if live_gate["failures"]:
            return {"success": False, "error": f"Live promotion gates failed: {live_gate['failures']}"}

    update_strategy(strategy_id, state=target_state, updated_at=datetime.now(timezone.utc).isoformat())
    if target_state in ("retired", "deprecated"):
        event_type = "strategy_retired"
        msg = f"Strategy retired: {current} -> {target_state}"
    else:
        event_type = "strategy_promoted"
        msg = f"Strategy promoted: {current} -> {target_state}"
    write_audit({
        "actor": "system",
        "source": "strategy_lifecycle",
        "event_type": event_type,
        "entity_type": "strategy",
        "entity_id": strategy_id,
        "before": {"state": current},
        "after": {"state": target_state},
        "message": msg,
    })
    return {"success": True, "old_state": current, "new_state": target_state}


def check_promotion_gates(strategy_id: str) -> dict:
    """Check if a strategy meets live-candidacy promotion gates."""
    failures: list[str] = []
    record = get_strategy(strategy_id)
    if record is None:
        return {"passed": False, "failures": ["strategy_not_found"], "metrics": {}}

    if record["state"] not in ("paper_running", "paper_passed"):
        failures.append(f"must_be_in_paper_running_or_paper_passed_state (currently: {record['state']})")

    # Phase 2A: if the strategy has a compiled RuleSet, it must have been evaluated
    # at least once (Path-A fill exists). A ruleset that was never run cannot prove
    # its entry conditions work — block promotion until evidence accumulates.
    import json as _json
    _params = {}
    try:
        _params = _json.loads(record.get("parameters") or "{}")
    except Exception:
        pass
    if _params.get("ruleset"):
        path_a_fills = list_strategy_fills(strategy_id, execution_mode_prefix="paper")
        if not path_a_fills:
            failures.append("ruleset_never_evaluated:0_path_a_fills")

    # Paper/development evidence is tied to paper/demo execution modes only.
    orders = list_strategy_orders(strategy_id, execution_mode_prefix="paper")
    fills = list_strategy_fills(strategy_id, execution_mode_prefix="paper")
    closed_positions = list_strategy_positions(strategy_id, status="closed")
    risk_decisions = list_strategy_risk_decisions(strategy_id)

    # §5.8: strategy-specific reviews first, then global fallback (MVP spec allows it).
    strategy_specific_reviews = [
        r for r in list_knowledge_items(item_type="post_trade_review", strategy_id=strategy_id, limit=1000)
        if (r.get("status") or "active") == "active"
    ]
    if strategy_specific_reviews:
        post_trade_reviews_used = "strategy_specific"
    else:
        global_reviews = [
            r for r in list_knowledge_items(item_type="post_trade_review", limit=100)
            if (r.get("status") or "active") == "active" and not r.get("strategy_id")
        ]
        strategy_specific_reviews = global_reviews
        post_trade_reviews_used = "global_fallback" if global_reviews else "missing"

    # §5.3: only count filled orders (not pending/cancelled).
    # Computed early so the runtime gate can use it for relaxation.
    filled_orders = [o for o in orders if o.get("status") == "filled"]
    paper_trade_count = len(fills) if fills else len(filled_orders)

    # Runtime gate: trade-count-driven relaxation.
    # High-frequency strategies (30+ trades) need only 3 days minimum instead of 14.
    _HIGH_TRADE_THRESHOLD = DEFAULT_PROMOTION_GATES["min_paper_trades"]  # 30
    _MIN_RUNTIME_DAYS_RELAXED = 3

    first_order_age_days = _first_order_age_days(orders)
    if first_order_age_days is None:
        failures.append("no_strategy_paper_orders")
    elif paper_trade_count >= _HIGH_TRADE_THRESHOLD:
        if first_order_age_days < _MIN_RUNTIME_DAYS_RELAXED:
            failures.append(
                f"insufficient_paper_runtime_days:{first_order_age_days}<"
                f"{_MIN_RUNTIME_DAYS_RELAXED}"
            )
    elif first_order_age_days < DEFAULT_PROMOTION_GATES["min_paper_runtime_days"]:
        failures.append(
            f"insufficient_paper_runtime_days:{first_order_age_days}<"
            f"{DEFAULT_PROMOTION_GATES['min_paper_runtime_days']}"
        )
    if paper_trade_count < DEFAULT_PROMOTION_GATES["min_paper_trades"]:
        failures.append(f"insufficient_paper_trades:{paper_trade_count}<{DEFAULT_PROMOTION_GATES['min_paper_trades']}")

    if not closed_positions:
        failures.append("insufficient_closed_positions_for_pnl_checks")
        realized_pnl = 0.0
        max_drawdown_pct = None
        max_consecutive_losses = None
    else:
        pnls = [float(p.get("realized_pnl") or 0.0) for p in closed_positions]
        realized_pnl = sum(pnls)
        max_drawdown_pct = _max_drawdown_pct(pnls)
        max_consecutive_losses = _max_consecutive_losses(pnls)
        if DEFAULT_PROMOTION_GATES["positive_expectancy_required"] and realized_pnl <= 0:
            failures.append(f"non_positive_expectancy:{realized_pnl}")
        if max_drawdown_pct > DEFAULT_PROMOTION_GATES["max_drawdown_pct_allowed"]:
            failures.append(
                f"max_drawdown_exceeded:{max_drawdown_pct}>{DEFAULT_PROMOTION_GATES['max_drawdown_pct_allowed']}"
            )
        if max_consecutive_losses > DEFAULT_PROMOTION_GATES["max_consecutive_losses_allowed"]:
            failures.append(
                f"max_consecutive_losses_exceeded:{max_consecutive_losses}>"
                f"{DEFAULT_PROMOTION_GATES['max_consecutive_losses_allowed']}"
            )

    critical_violations = _critical_risk_violations(risk_decisions)
    if critical_violations > DEFAULT_PROMOTION_GATES["critical_risk_violations_allowed"]:
        failures.append(
            f"critical_risk_violations:{critical_violations}>"
            f"{DEFAULT_PROMOTION_GATES['critical_risk_violations_allowed']}"
        )

    if DEFAULT_PROMOTION_GATES["post_trade_reviews_required"] and not strategy_specific_reviews:
        failures.append("post_trade_review_required")

    metrics = {
        "paper_runtime_days": first_order_age_days or 0,
        "paper_trades": paper_trade_count,
        "closed_positions": len(closed_positions),
        "realized_pnl": realized_pnl,
        "max_drawdown_pct": max_drawdown_pct,
        "max_consecutive_losses": max_consecutive_losses,
        "critical_risk_violations": critical_violations,
        "post_trade_reviews": len(strategy_specific_reviews),
        "post_trade_reviews_used": post_trade_reviews_used,
    }
    return {"passed": len(failures) == 0, "failures": failures, "metrics": metrics}


def check_live_limited_gates(strategy_id: str) -> dict:
    """Check live-eligible risk safety for promotion to live_limited."""
    record = get_strategy(strategy_id)
    if record is None:
        return {"passed": False, "failures": ["strategy_not_found"], "metrics": {}}

    risk_decisions = list_strategy_risk_decisions(strategy_id)
    critical_violations = _critical_risk_violations(risk_decisions)
    failures: list[str] = []
    if critical_violations > 0:
        failures.append(f"critical_risk_violations:{critical_violations}>0")

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "metrics": {"critical_risk_violations": critical_violations},
    }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _first_order_age_days(orders: list[dict]) -> int | None:
    dates = [_parse_dt(o.get("created_at")) for o in orders]
    dates = [d for d in dates if d is not None]
    if not dates:
        return None
    return (datetime.now(timezone.utc) - min(dates)).days


def _max_drawdown_pct(pnls: list[float]) -> float:
    from aletheia.config.loader import load_config

    cfg = load_config()
    capital_base = cfg.risk.max_live_limited_cap_usdt
    if not capital_base or capital_base <= 0:
        capital_base = getattr(cfg.risk, "starting_capital_usdt", None) or 100.0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return round((max_drawdown / capital_base) * 100.0, 6)


def _max_consecutive_losses(pnls: list[float]) -> int:
    current = 0
    longest = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _is_critical_violation(text: str) -> bool:
    """Check if a violation text matches a critical risk token exactly or as prefix."""
    for token in CRITICAL_RISK_TOKENS:
        if text == token or text.startswith(token + ":"):
            return True
    return False


def _critical_risk_violations(decisions: list[dict]) -> int:
    count = 0
    for decision in decisions:
        violations = decision.get("violations") or "[]"
        try:
            parsed = json.loads(violations)
        except json.JSONDecodeError:
            parsed = [violations]
        approved = bool(decision.get("approved"))
        # Only count rejections of LIVE proposals as critical. Use
        # proposal_execution_mode (the original requested mode) — NOT
        # allowed_execution_mode, which is downgraded to paper_internal on every
        # rejection and so never started with a live marker (CR-RE-01 dead branch).
        # Fall back to allowed_execution_mode for legacy rows written before the
        # proposal_execution_mode column existed.
        exec_mode = decision.get("proposal_execution_mode") or decision.get("allowed_execution_mode") or ""
        if not approved and exec_mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited"):
            count += 1
            continue
        for entry in parsed:
            text = str(entry)
            if _is_critical_violation(text):
                count += 1
                break
    return count


def _live_lifecycle_gate() -> dict:
    """Check runtime-level prerequisites for promotion from live_candidate to live_limited."""
    from aletheia.config.loader import load_config

    cfg = load_config()
    failures: list[str] = []
    if cfg.live.state != "live_limited":
        failures.append(f"live_state_not_limited:current={cfg.live.state}")
    if not cfg.live.first_live_confirmation_done:
        failures.append("live_first_confirmation_not_done")
    if cfg.agent.permission_mode not in LIVE_PROMOTION_PERMISSION_MODES:
        failures.append(f"permission_mode_not_live_promotion:mode={cfg.agent.permission_mode}")
    return {"failures": failures}
