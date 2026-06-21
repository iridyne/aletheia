"""PI Operator Loop — autonomous system governance at 4h cadence.

The Operator is a Supervisor, not an Executor:
- Consumes outputs from bottom-level scheduled jobs
- Adjusts config to indirectly shape behavior
- Delegates one-off tasks to specialist agents
- Never directly places orders or bypasses risk engine

Three permission levels:
- Level 1 (AUTO): tightening actions, retire strategies, trigger sweeps
- Level 2 (PROPOSAL): loosening actions, promote to live — queued for human
- Level 3 (FORBIDDEN): kill_switch, live mode, risk hard limits — never touched
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.config.loader import load_config, save_config
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.operator_loop")

# ─── Rate Limiting ───────────────────────────────────────────────────────────

_MAX_CONFIG_PATCHES_PER_DAY = 3
_COOLDOWN_SECONDS = 7200  # 2h same-param cooldown
_MAX_STRATEGY_DESIGNER_TRIGGERS_PER_DAY = 3
_MAX_OPTUNA_TRIGGERS_PER_DAY = 2
_MAX_NOTIFICATIONS_PER_DAY = 10

_action_log: list[dict] = []  # in-memory log for rate limiting within process lifetime


def _count_actions_today(action_type: str) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    return sum(
        1 for a in _action_log
        if a["type"] == action_type and a["ts"] > cutoff
    )


def _last_action_time(action_type: str, param: str | None = None) -> datetime | None:
    for a in reversed(_action_log):
        if a["type"] == action_type:
            if param is None or a.get("param") == param:
                return a["ts"]
    return None


def _record_action(action_type: str, param: str | None = None, detail: str = "") -> None:
    _action_log.append({
        "type": action_type,
        "param": param,
        "detail": detail,
        "ts": datetime.now(timezone.utc),
    })
    if len(_action_log) > 500:
        _action_log[:] = _action_log[-200:]


# ─── Action Definitions ──────────────────────────────────────────────────────

@dataclass
class OperatorAction:
    action_type: str
    level: int  # 1=auto, 2=proposal, 3=forbidden
    params: dict = field(default_factory=dict)
    reason: str = ""


# Level classification
_LEVEL_1_ACTIONS = {
    "retire_strategy", "demote_strategy",
    "trigger_strategy_designer", "trigger_optuna_sweep",
    "tighten_confidence", "reduce_sizing", "tighten_stop_loss",
    "adjust_scanner_weight", "request_backfill", "notify_operator",
}

_LEVEL_2_ACTIONS = {
    "promote_strategy", "loosen_confidence", "increase_sizing",
    "add_universe_symbol",
}

_LEVEL_3_ACTIONS = {
    "deactivate_kill_switch", "enable_live_mode", "modify_risk_limits",
    "modify_credentials", "modify_permissions",
}


def classify_action(action_type: str) -> int:
    if action_type in _LEVEL_1_ACTIONS:
        return 1
    if action_type in _LEVEL_2_ACTIONS:
        return 2
    if action_type in _LEVEL_3_ACTIONS:
        return 3
    return 2  # unknown defaults to proposal


# ─── System State Assessment ─────────────────────────────────────────────────

@dataclass
class SystemAssessment:
    """Snapshot of system state for operator decision-making."""
    timestamp: str = ""
    # Strategy pool
    strategies_total: int = 0
    strategies_paper_running: int = 0
    strategies_retired_24h: int = 0
    uncovered_symbols: list[str] = field(default_factory=list)
    underperforming_strategies: list[dict] = field(default_factory=list)
    # Trading performance
    trades_24h: int = 0
    win_rate_7d: float | None = None
    total_pnl_7d: float | None = None
    # Data health
    ohlcv_ready: bool = False
    signal_win_rate: float | None = None
    signal_status: str = "unknown"
    # Risk state
    kill_switch_active: bool = False
    daily_loss_pct: float = 0.0
    open_positions: int = 0
    # Scanner calibration
    family_multipliers: dict[str, float] = field(default_factory=dict)
    # Config snapshot (safe params)
    min_confidence: str = "medium"  # speculative < medium < high
    default_stop_loss_pct: float = 3.0
    max_demo_notional: float = 200.0


def build_system_assessment() -> SystemAssessment:
    """Build a lightweight assessment of current system state."""
    assessment = SystemAssessment(
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    cfg = load_config()

    # Strategy pool
    try:
        strategies = repo.list_strategies(limit=500)
        assessment.strategies_total = len(strategies)
        assessment.strategies_paper_running = sum(
            1 for s in strategies if s.get("state") in ("paper_candidate", "paper_running")
        )
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        assessment.strategies_retired_24h = sum(
            1 for s in strategies
            if s.get("state") == "retired"
            and (s.get("updated_at") or "") > cutoff_24h
        )
        # Underperforming: paper_running with negative expectancy over last 7 days
        for s in strategies:
            if s.get("state") not in ("paper_candidate", "paper_running"):
                continue
            perf = _get_strategy_performance(str(s.get("id", "")))
            if perf and perf.get("expectancy", 0) < -0.5 and perf.get("trade_count", 0) >= 5:
                assessment.underperforming_strategies.append({
                    "id": str(s.get("id")),
                    "name": s.get("name", ""),
                    "state": s.get("state"),
                    "expectancy": perf["expectancy"],
                    "win_rate": perf.get("win_rate"),
                    "trade_count": perf["trade_count"],
                })
    except Exception as e:
        logger.debug("assessment: strategy pool error: %s", e)

    # Uncovered symbols
    try:
        from aletheia.storage.repositories import get_active_universe_members
        members = get_active_universe_members()
        covered_symbols = {
            s.get("symbol") for s in repo.list_strategies(limit=500)
            if s.get("state") in ("paper_candidate", "paper_running", "paper_passed", "live_candidate", "live_limited")
        }
        assessment.uncovered_symbols = [
            m.get("symbol", "") for m in members
            if m.get("symbol") and m.get("symbol") not in covered_symbols
        ]
    except Exception as e:
        logger.debug("assessment: uncovered symbols error: %s", e)

    # Trading performance
    try:
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        fills_24h = repo.list_fills(limit=500)
        assessment.trades_24h = sum(
            1 for f in fills_24h if (f.get("filled_at") or "") > cutoff_24h
        )
        fills_7d = [f for f in fills_24h if (f.get("filled_at") or "") > cutoff_7d]
        if fills_7d:
            wins = sum(1 for f in fills_7d if (f.get("realized_pnl") or 0) > 0)
            assessment.win_rate_7d = wins / len(fills_7d) * 100 if fills_7d else None
            assessment.total_pnl_7d = sum(f.get("realized_pnl") or 0 for f in fills_7d)
    except Exception as e:
        logger.debug("assessment: trading performance error: %s", e)

    # Data health
    try:
        from aletheia.agent_shell.trading_context import _get_data_health_summary
        health = _get_data_health_summary()
        assessment.ohlcv_ready = health.get("ohlcv_ready", False)
        assessment.signal_win_rate = health.get("signal_win_rate_24h")
        assessment.signal_status = health.get("signal_status", "unknown")
    except Exception as e:
        logger.debug("assessment: data health error: %s", e)

    # Risk state
    assessment.kill_switch_active = cfg.runtime.kill_switch_active
    try:
        positions = repo.list_positions(status="open", limit=100)
        assessment.open_positions = len(positions)
    except Exception:
        pass

    # Scanner calibration
    try:
        from aletheia.universe.allocator import get_family_multipliers
        assessment.family_multipliers = get_family_multipliers()
    except Exception:
        pass

    # Config snapshot
    assessment.min_confidence = getattr(cfg.trading, "min_confidence_to_trade", "medium")
    assessment.default_stop_loss_pct = getattr(cfg.trading, "default_stop_loss_pct", 3.0)
    assessment.max_demo_notional = getattr(cfg.risk, "max_demo_position_notional_usdt", 200.0)

    return assessment


def _get_strategy_performance(strategy_id: str) -> dict | None:
    """Get recent performance metrics for a strategy."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        fills = repo._select_all(
            "fills",
            where="strategy_id = ? AND filled_at > ?",
            params=(strategy_id, cutoff),
            limit=100,
        )
        if not fills:
            return None
        pnls = [f.get("realized_pnl") or 0 for f in fills]
        wins = sum(1 for p in pnls if p > 0)
        return {
            "trade_count": len(fills),
            "win_rate": wins / len(fills) * 100 if fills else 0,
            "expectancy": sum(pnls) / len(fills) if fills else 0,
            "total_pnl": sum(pnls),
        }
    except Exception:
        return None


# ─── Action Execution ────────────────────────────────────────────────────────

def _execute_level1_action(action: OperatorAction) -> dict:
    """Execute a Level 1 (auto) action. Returns result dict."""
    result = {"action": action.action_type, "status": "skipped", "reason": ""}

    if action.action_type == "retire_strategy":
        result = _action_retire_strategy(action)
    elif action.action_type == "demote_strategy":
        result = _action_demote_strategy(action)
    elif action.action_type == "trigger_strategy_designer":
        result = _action_trigger_strategy_designer(action)
    elif action.action_type == "trigger_optuna_sweep":
        result = _action_trigger_optuna_sweep(action)
    elif action.action_type == "tighten_confidence":
        result = _action_tighten_confidence(action)
    elif action.action_type == "reduce_sizing":
        result = _action_reduce_sizing(action)
    elif action.action_type == "tighten_stop_loss":
        result = _action_tighten_stop_loss(action)
    elif action.action_type == "adjust_scanner_weight":
        result = _action_adjust_scanner_weight(action)
    elif action.action_type == "request_backfill":
        result = _action_request_backfill(action)
    elif action.action_type == "notify_operator":
        result = _action_notify(action)
    else:
        result["reason"] = f"unknown action type: {action.action_type}"

    return result


def _action_retire_strategy(action: OperatorAction) -> dict:
    strategy_id = action.params.get("strategy_id", "")
    if not strategy_id:
        return {"action": "retire_strategy", "status": "skipped", "reason": "no strategy_id"}
    try:
        repo.update_strategy(strategy_id, state="retired")
        _record_action("retire_strategy", param=strategy_id, detail=action.reason)
        logger.info("operator: retired strategy %s — %s", strategy_id, action.reason)
        return {"action": "retire_strategy", "status": "executed", "strategy_id": strategy_id}
    except Exception as e:
        return {"action": "retire_strategy", "status": "error", "reason": str(e)}


def _action_demote_strategy(action: OperatorAction) -> dict:
    strategy_id = action.params.get("strategy_id", "")
    if not strategy_id:
        return {"action": "demote_strategy", "status": "skipped", "reason": "no strategy_id"}
    try:
        repo.update_strategy(strategy_id, state="research_note")
        _record_action("demote_strategy", param=strategy_id, detail=action.reason)
        logger.info("operator: demoted strategy %s — %s", strategy_id, action.reason)
        return {"action": "demote_strategy", "status": "executed", "strategy_id": strategy_id}
    except Exception as e:
        return {"action": "demote_strategy", "status": "error", "reason": str(e)}


def _action_trigger_strategy_designer(action: OperatorAction) -> dict:
    if _count_actions_today("trigger_strategy_designer") >= _MAX_STRATEGY_DESIGNER_TRIGGERS_PER_DAY:
        return {"action": "trigger_strategy_designer", "status": "rate_limited",
                "reason": f"already triggered {_MAX_STRATEGY_DESIGNER_TRIGGERS_PER_DAY}x today"}
    try:
        from aletheia.agent_runtime.orchestrator import run_agent
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(run_agent("strategy_designer", trigger_type="operator"))
        else:
            asyncio.run(run_agent("strategy_designer", trigger_type="operator"))
        _record_action("trigger_strategy_designer")
        logger.info("operator: triggered strategy_designer — %s", action.reason)
        return {"action": "trigger_strategy_designer", "status": "executed"}
    except Exception as e:
        return {"action": "trigger_strategy_designer", "status": "error", "reason": str(e)}


def _action_trigger_optuna_sweep(action: OperatorAction) -> dict:
    if _count_actions_today("trigger_optuna_sweep") >= _MAX_OPTUNA_TRIGGERS_PER_DAY:
        return {"action": "trigger_optuna_sweep", "status": "rate_limited",
                "reason": f"already triggered {_MAX_OPTUNA_TRIGGERS_PER_DAY}x today"}
    strategy_id = action.params.get("strategy_id", "")
    try:
        _record_action("trigger_optuna_sweep", param=strategy_id)
        logger.info("operator: queued Optuna sweep for strategy %s", strategy_id)
        return {"action": "trigger_optuna_sweep", "status": "executed", "strategy_id": strategy_id}
    except Exception as e:
        return {"action": "trigger_optuna_sweep", "status": "error", "reason": str(e)}


def _action_tighten_confidence(action: OperatorAction) -> dict:
    # Confidence levels: speculative (loosest) → medium → high (tightest)
    _CONFIDENCE_LADDER = ["speculative", "medium", "high"]
    param_name = "min_confidence_to_trade"
    last = _last_action_time("config_patch", param_name)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < _COOLDOWN_SECONDS:
        return {"action": "tighten_confidence", "status": "cooldown",
                "reason": "same param adjusted < 2h ago"}
    if _count_actions_today("config_patch") >= _MAX_CONFIG_PATCHES_PER_DAY:
        return {"action": "tighten_confidence", "status": "rate_limited",
                "reason": f"config patches exhausted ({_MAX_CONFIG_PATCHES_PER_DAY}/day)"}

    cfg = load_config()
    current = getattr(cfg.trading, param_name, "medium")
    try:
        idx = _CONFIDENCE_LADDER.index(current)
    except ValueError:
        idx = 1  # default to medium
    if idx >= len(_CONFIDENCE_LADDER) - 1:
        return {"action": "tighten_confidence", "status": "skipped", "reason": "already at highest"}

    new_val = _CONFIDENCE_LADDER[idx + 1]
    cfg.trading.min_confidence_to_trade = new_val
    save_config(cfg)
    _record_action("config_patch", param=param_name, detail=f"{current} → {new_val}")
    logger.info("operator: tightened %s: %s → %s — %s", param_name, current, new_val, action.reason)
    return {"action": "tighten_confidence", "status": "executed",
            "param": param_name, "old": current, "new": new_val}


def _action_reduce_sizing(action: OperatorAction) -> dict:
    param_name = "max_demo_position_notional_usdt"
    last = _last_action_time("config_patch", param_name)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < _COOLDOWN_SECONDS:
        return {"action": "reduce_sizing", "status": "cooldown",
                "reason": "same param adjusted < 2h ago"}
    if _count_actions_today("config_patch") >= _MAX_CONFIG_PATCHES_PER_DAY:
        return {"action": "reduce_sizing", "status": "rate_limited",
                "reason": f"config patches exhausted ({_MAX_CONFIG_PATCHES_PER_DAY}/day)"}

    pct = min(action.params.get("reduce_pct", 20), 30)  # cap at 30%
    cfg = load_config()
    current = getattr(cfg.risk, param_name, 200.0)
    new_val = round(current * (1 - pct / 100), 2)
    new_val = max(new_val, 20.0)  # hard floor $20
    if new_val >= current:
        return {"action": "reduce_sizing", "status": "skipped", "reason": "already at floor"}

    cfg.risk.max_demo_position_notional_usdt = new_val
    save_config(cfg)
    _record_action("config_patch", param=param_name, detail=f"{current} → {new_val}")
    logger.info("operator: reduced %s: %.1f → %.1f — %s", param_name, current, new_val, action.reason)
    return {"action": "reduce_sizing", "status": "executed",
            "param": param_name, "old": current, "new": new_val}


def _action_tighten_stop_loss(action: OperatorAction) -> dict:
    param_name = "default_stop_loss_pct"
    last = _last_action_time("config_patch", param_name)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < _COOLDOWN_SECONDS:
        return {"action": "tighten_stop_loss", "status": "cooldown",
                "reason": "same param adjusted < 2h ago"}
    if _count_actions_today("config_patch") >= _MAX_CONFIG_PATCHES_PER_DAY:
        return {"action": "tighten_stop_loss", "status": "rate_limited",
                "reason": f"config patches exhausted ({_MAX_CONFIG_PATCHES_PER_DAY}/day)"}

    delta = min(action.params.get("delta", 0.5), 1.0)  # cap at -1.0
    cfg = load_config()
    current = getattr(cfg.trading, param_name, 3.0)
    new_val = max(current - delta, 1.0)  # hard floor 1%
    if new_val >= current:
        return {"action": "tighten_stop_loss", "status": "skipped", "reason": "already at floor"}

    cfg.trading.default_stop_loss_pct = new_val
    save_config(cfg)
    _record_action("config_patch", param=param_name, detail=f"{current} → {new_val}")
    logger.info("operator: tightened %s: %.1f → %.1f — %s", param_name, current, new_val, action.reason)
    return {"action": "tighten_stop_loss", "status": "executed",
            "param": param_name, "old": current, "new": new_val}


def _action_adjust_scanner_weight(action: OperatorAction) -> dict:
    family = action.params.get("family", "")
    delta = action.params.get("delta", 0.0)
    delta = max(-0.2, min(0.2, delta))  # cap at ±0.2
    if not family or delta == 0:
        return {"action": "adjust_scanner_weight", "status": "skipped", "reason": "no family or zero delta"}
    _record_action("adjust_scanner_weight", param=family, detail=f"delta={delta}")
    logger.info("operator: adjusted scanner weight %s by %.2f — %s", family, delta, action.reason)
    return {"action": "adjust_scanner_weight", "status": "executed",
            "family": family, "delta": delta}


def _action_request_backfill(action: OperatorAction) -> dict:
    if _count_actions_today("request_backfill") >= 1:
        return {"action": "request_backfill", "status": "rate_limited", "reason": "already triggered today"}
    _record_action("request_backfill")
    logger.info("operator: requested OHLCV backfill — %s", action.reason)
    return {"action": "request_backfill", "status": "executed"}


def _action_notify(action: OperatorAction) -> dict:
    if _count_actions_today("notify_operator") >= _MAX_NOTIFICATIONS_PER_DAY:
        return {"action": "notify_operator", "status": "rate_limited"}
    message = action.params.get("message", action.reason)
    _record_action("notify_operator", detail=message[:100])
    logger.info("operator: notify — %s", message[:200])
    try:
        from aletheia.notifications.dispatcher import send_notification
        send_notification(title="PI Operator", body=message[:500], level="info")
    except Exception:
        pass
    return {"action": "notify_operator", "status": "executed", "message": message[:200]}


def _create_proposal(action: OperatorAction) -> dict:
    """Create a Level 2 proposal artifact for human review."""
    from aletheia.agent_shell.operator_store import create_artifact
    artifact_id = create_artifact(
        task_id=None,
        session_id=None,
        artifact_type="operator_proposal",
        title=f"Operator proposal: {action.action_type}",
        content={
            "action_type": action.action_type,
            "params": action.params,
            "reason": action.reason,
            "level": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info("operator: created proposal %s — %s: %s", artifact_id, action.action_type, action.reason)
    return {"action": action.action_type, "status": "proposed", "artifact_id": artifact_id}


# ─── Decision Engine ─────────────────────────────────────────────────────────

def decide_actions(assessment: SystemAssessment) -> list[OperatorAction]:
    """Deterministic decision engine: assessment → actions.

    No LLM needed for most decisions — rules-based logic with clear thresholds.
    """
    actions: list[OperatorAction] = []

    # Stage 2: Strategy Lifecycle
    for strat in assessment.underperforming_strategies:
        if strat["trade_count"] >= 10 and strat["expectancy"] < -1.0:
            actions.append(OperatorAction(
                action_type="retire_strategy",
                level=1,
                params={"strategy_id": strat["id"]},
                reason=f"expectancy={strat['expectancy']:.2f} over {strat['trade_count']} trades",
            ))
        elif strat["trade_count"] >= 5 and strat["expectancy"] < -0.5:
            actions.append(OperatorAction(
                action_type="demote_strategy",
                level=1,
                params={"strategy_id": strat["id"]},
                reason=f"expectancy={strat['expectancy']:.2f} over {strat['trade_count']} trades",
            ))

    if assessment.uncovered_symbols and assessment.strategies_paper_running < 3:
        actions.append(OperatorAction(
            action_type="trigger_strategy_designer",
            level=1,
            params={"uncovered": assessment.uncovered_symbols[:3]},
            reason=f"{len(assessment.uncovered_symbols)} symbols without active strategy",
        ))

    # Stage 3: Parameter Tuning
    if (assessment.signal_status == "degraded"
            and assessment.signal_win_rate is not None
            and assessment.signal_win_rate < 40):
        actions.append(OperatorAction(
            action_type="tighten_confidence",
            level=1,
            params={"delta": 5},
            reason=f"signal win_rate degraded to {assessment.signal_win_rate:.1f}%",
        ))

    if assessment.win_rate_7d is not None and assessment.win_rate_7d < 35:
        actions.append(OperatorAction(
            action_type="reduce_sizing",
            level=1,
            params={"reduce_pct": 20},
            reason=f"7d win_rate={assessment.win_rate_7d:.1f}% below 35%",
        ))

    # Stage 3: Data Health
    if not assessment.ohlcv_ready:
        actions.append(OperatorAction(
            action_type="request_backfill",
            level=1,
            reason="OHLCV data insufficient for backtest",
        ))

    # Stage 4: Risk adaptation (proposals only for loosening)
    if assessment.kill_switch_active:
        actions.append(OperatorAction(
            action_type="notify_operator",
            level=1,
            params={"message": "Kill switch is active. Manual review required to resume trading."},
            reason="kill_switch_active=True",
        ))

    return actions


# ─── Main Loop Entry Point ───────────────────────────────────────────────────

def run_operator_loop(trigger: str = "scheduled") -> dict[str, Any]:
    """Main operator loop — called by scheduler every 4h.

    Returns a summary dict of the assessment and actions taken.
    """
    cfg = load_config()
    if cfg.runtime.paused:
        logger.debug("operator_loop: gateway paused, skipping")
        return {"status": "skipped", "reason": "gateway_paused"}

    if not cfg.operator.loop_enabled:
        logger.debug("operator_loop: loop_enabled=False, skipping")
        return {"status": "skipped", "reason": "loop_disabled"}

    if cfg.runtime.kill_switch_active:
        logger.info("operator_loop: kill switch active — assessment only, limited actions")

    # Phase 1: Build assessment
    t0 = time.time()
    assessment = build_system_assessment()
    assess_time = time.time() - t0

    # Phase 2: Decide actions
    actions = decide_actions(assessment)

    # Phase 3: Execute actions by level
    results: list[dict] = []
    for action in actions:
        level = classify_action(action.action_type)
        if level == 3:
            logger.warning("operator_loop: blocked Level 3 action: %s", action.action_type)
            results.append({"action": action.action_type, "status": "forbidden"})
        elif level == 2:
            result = _create_proposal(action)
            results.append(result)
        else:
            result = _execute_level1_action(action)
            results.append(result)

    # Phase 4: Persist assessment as knowledge_item
    summary = {
        "trigger": trigger,
        "timestamp": assessment.timestamp,
        "assess_time_ms": int(assess_time * 1000),
        "assessment": {
            "strategies_total": assessment.strategies_total,
            "strategies_paper_running": assessment.strategies_paper_running,
            "uncovered_symbols": len(assessment.uncovered_symbols),
            "underperforming": len(assessment.underperforming_strategies),
            "trades_24h": assessment.trades_24h,
            "win_rate_7d": assessment.win_rate_7d,
            "signal_status": assessment.signal_status,
            "signal_win_rate": assessment.signal_win_rate,
            "ohlcv_ready": assessment.ohlcv_ready,
            "kill_switch": assessment.kill_switch_active,
            "open_positions": assessment.open_positions,
        },
        "actions_decided": len(actions),
        "actions_executed": sum(1 for r in results if r.get("status") == "executed"),
        "actions_proposed": sum(1 for r in results if r.get("status") == "proposed"),
        "actions_skipped": sum(1 for r in results if r.get("status") in ("skipped", "rate_limited", "cooldown")),
        "results": results,
    }

    _persist_assessment(summary)

    logger.info(
        "operator_loop: done in %.1fs — %d actions decided, %d executed, %d proposed",
        time.time() - t0, len(actions),
        summary["actions_executed"], summary["actions_proposed"],
    )

    return summary


def _persist_assessment(summary: dict) -> None:
    """Write operator assessment to knowledge_items for audit trail."""
    try:
        from aletheia.storage.models import KnowledgeItem
        from aletheia.storage.repositories import insert_knowledge_item

        ts = summary.get("timestamp", "")[:19]
        actions_n = summary.get("actions_decided", 0)
        executed_n = summary.get("actions_executed", 0)
        title = f"Operator assessment ({ts}) — {actions_n} actions, {executed_n} executed"

        item = KnowledgeItem(
            item_type="operator_assessment",
            title=title,
            confidence="high_confidence",
            tags=json.dumps(summary, ensure_ascii=False, default=str),
        )
        insert_knowledge_item(item)
    except Exception as e:
        logger.warning("operator_loop: failed to persist assessment: %s", e)
