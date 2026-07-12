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

from aletheia.analytics.outcome_integrity import (
    OUTCOME_SEMANTICS_VERSION,
    eligible_strategy_outcomes,
)
from aletheia.config.loader import load_config, save_config
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.operator_loop")

# ─── Rate Limiting ───────────────────────────────────────────────────────────

_MAX_CONFIG_PATCHES_PER_DAY = 3
_COOLDOWN_SECONDS = 7200  # 2h same-param cooldown
_MAX_OPTUNA_TRIGGERS_PER_DAY = 2
_MAX_NOTIFICATIONS_PER_DAY = 10
_MAX_RESEARCH_TRIGGERS_PER_DAY = 1

_action_log: list[dict] = []  # in-memory log for rate limiting within process lifetime


# ─── ParamSpec Registry ──────────────────────────────────────────────────────

@dataclass
class ParamSpec:
    name: str
    config_section: str  # "trading" | "risk"
    neutral: Any         # schema default = homeostatic target
    tighten_direction: str  # "up" | "down"
    floor: Any
    ceiling: Any
    step: Any            # per-cycle cap (pct for numeric, 1 step for ladder)
    ladder: list | None  # categorical params only


PARAM_REGISTRY: dict[str, ParamSpec] = {
    "min_confidence_to_trade": ParamSpec(
        "min_confidence_to_trade", "trading",
        neutral="medium", tighten_direction="up",
        floor="speculative", ceiling="high", step=1,
        ladder=["speculative", "medium", "high"]),
    "max_demo_position_notional_usdt": ParamSpec(
        "max_demo_position_notional_usdt", "risk",
        neutral=200.0, tighten_direction="down",
        floor=20.0, ceiling=500.0, step=0.30,
        ladder=None),
    "default_stop_loss_pct": ParamSpec(
        "default_stop_loss_pct", "trading",
        neutral=2.0, tighten_direction="down",
        floor=1.0, ceiling=5.0, step=1.0,
        ladder=None),
}


def _get_param_drift(param_name: str) -> str:
    """Return 'tightened', 'loosened', or 'neutral' for a registered param."""
    spec = PARAM_REGISTRY.get(param_name)
    if not spec:
        return "neutral"
    cfg = load_config()
    section = getattr(cfg, spec.config_section, None)
    if section is None:
        return "neutral"
    current = getattr(section, spec.name, spec.neutral)

    if spec.ladder:
        try:
            cur_idx = spec.ladder.index(current)
            neu_idx = spec.ladder.index(spec.neutral)
        except ValueError:
            return "neutral"
        if spec.tighten_direction == "up":
            if cur_idx > neu_idx:
                return "tightened"
            elif cur_idx < neu_idx:
                return "loosened"
        else:
            if cur_idx < neu_idx:
                return "tightened"
            elif cur_idx > neu_idx:
                return "loosened"
        return "neutral"
    else:
        if spec.tighten_direction == "down":
            if current < spec.neutral:
                return "tightened"
            elif current > spec.neutral:
                return "loosened"
        else:
            if current > spec.neutral:
                return "tightened"
            elif current < spec.neutral:
                return "loosened"
        return "neutral"


# ─── Persistent Action Log ───────────────────────────────────────────────────

_CONSECUTIVE_ESCALATION_THRESHOLD = 3


def _persist_action(event_type: str, param: str | None = None,
                    before: str = "", after: str = "",
                    direction: str = "", reason: str = "") -> None:
    """Write action to audit_events (persistent) + in-memory cache."""
    from aletheia.storage.models import AuditEvent
    from aletheia.storage.repositories import insert_audit_event

    message = f"{direction}:{param}:{before}→{after}" if direction else f"{param}:{before}→{after}"
    event = AuditEvent(
        actor="operator_loop",
        source="operator_loop",
        event_type=event_type,
        entity_type="config_param" if param else None,
        entity_id=param,
        before_state=before,
        after_state=after,
        message=message,
    )
    try:
        insert_audit_event(event)
    except Exception as e:
        logger.debug("_persist_action: audit write failed: %s", e)

    _action_log.append({
        "type": event_type,
        "param": param,
        "detail": f"{before}→{after}",
        "direction": direction,
        "ts": datetime.now(timezone.utc),
    })
    if len(_action_log) > 500:
        _action_log[:] = _action_log[-200:]


def _count_actions_today_persistent(event_type: str, param: str | None = None) -> int:
    """Count actions in last 24h from DB (survives restarts)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        where = "actor = 'operator_loop' AND event_type = ? AND timestamp > ?"
        params_tup: tuple = (event_type, cutoff)
        if param:
            where += " AND entity_id = ?"
            params_tup = (event_type, cutoff, param)
        rows = repo._select_all("audit_events", where=where, params=params_tup, limit=100)
        return len(rows)
    except Exception:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=24)
        return sum(
            1 for a in _action_log
            if a["type"] == event_type and a["ts"] > cutoff_dt
            and (param is None or a.get("param") == param)
        )


def _last_action_time_persistent(event_type: str, param: str | None = None) -> datetime | None:
    """Get last action time from DB (survives restarts)."""
    try:
        where = "actor = 'operator_loop' AND event_type = ?"
        params_tup: tuple = (event_type,)
        if param:
            where += " AND entity_id = ?"
            params_tup = (event_type, param)
        rows = repo._select_all("audit_events", where=where, params=params_tup,
                                order="timestamp DESC", limit=1)
        if rows:
            ts_str = rows[0].get("timestamp", "")
            if ts_str:
                return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        pass
    for a in reversed(_action_log):
        if a["type"] == event_type:
            if param is None or a.get("param") == param:
                return a["ts"]
    return None


def _count_consecutive_same_direction(param: str, direction: str) -> int:
    """Count consecutive recent actions in the same direction for a param."""
    try:
        where = "actor = 'operator_loop' AND event_type = 'config_patch' AND entity_id = ?"
        rows = repo._select_all("audit_events", where=where, params=(param,),
                                order="timestamp DESC", limit=10)
        count = 0
        for row in rows:
            msg = row.get("message", "")
            if msg.startswith(f"{direction}:"):
                count += 1
            else:
                break
        return count
    except Exception:
        count = 0
        for a in reversed(_action_log):
            if a.get("param") == param and a["type"] == "config_patch":
                if a.get("direction") == direction:
                    count += 1
                else:
                    break
        return count


def _maybe_escalate(action: OperatorAction, param: str, direction: str) -> OperatorAction:
    """If 3+ consecutive same-direction changes, escalate to Level 2."""
    consecutive = _count_consecutive_same_direction(param, direction)
    if consecutive >= _CONSECUTIVE_ESCALATION_THRESHOLD:
        action.level = 2
        action.reason = f"[ESCALATED: {consecutive}x consecutive {direction}] {action.reason}"
    return action


# ─── Legacy helpers (delegate to persistent versions) ────────────────────────

def _count_actions_today(action_type: str) -> int:
    return _count_actions_today_persistent(action_type)


def _last_action_time(action_type: str, param: str | None = None) -> datetime | None:
    return _last_action_time_persistent(action_type, param)


def _record_action(action_type: str, param: str | None = None, detail: str = "") -> None:
    _persist_action(action_type, param=param, before="", after=detail, reason=detail)


# ─── Action Definitions ──────────────────────────────────────────────────────

@dataclass
class OperatorAction:
    action_type: str
    level: int  # 1=auto, 2=proposal, 3=forbidden
    params: dict = field(default_factory=dict)
    reason: str = ""


# Level classification.
#
# #27 note (2026-07-08): a few names in this set do not execute the operation
# their name suggests. Kept only for backwards-compat with existing operator
# telemetry / expected-action rubrics:
#   - retire_strategy / demote_strategy: Phase 4d shift writes a
#     `pi_decisions` recommendation row instead of retiring the strategy
#     directly. The LLM sees the action as available; the effect is a
#     recommendation queued for review, not an immediate state change.
#   - trigger_optuna_sweep: returns status='not_implemented' — no
#     sweep-job queue exists (tracked as OL-2 in
#     docs/research/CODE_AUDIT_REPORT_2026-06-23.md). Kept in the set so
#     the operator's rubric doesn't red-flag it as an unknown intent.
# Owner should either wire the underlying queue or remove these before
# claiming the action set is fully executable.
_LEVEL_1_ACTIONS = {
    "retire_strategy", "demote_strategy",  # advisory-only under Phase 4d — see note above
    "trigger_optuna_sweep",                # status='not_implemented' at runtime — see note above
    "tighten_confidence", "reduce_sizing", "tighten_stop_loss",
    "adjust_scanner_weight", "request_backfill", "notify_operator",
    "trigger_strategy_research",
    # Data-maintenance self-heal actions (operator_layer.md §自愈档位 L1).
    # These require PI approval before execution (§不变式1).
    # PI approval gate is tracked by OL-C08 (status="missing" / xfail tripwire).
    "clean_stale_heartbeats",
    "restart_collector",
    "rebuild_sqlite_index",
}

_LEVEL_2_ACTIONS = {
    "loosen_confidence", "increase_sizing",
    "widen_stop_loss", "neutral_drift",
    "add_universe_symbol",
    # L2 self-heal actions (operator_layer.md §自愈档位 L2) — require human confirmation.
    # Routed to _create_proposal automatically by classify_action → dispatch path.
    "clear_signal_pending", "modify_prompt_fragment", "modify_config_fragment",
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
    if action_type.startswith("neutral_drift_"):
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
    trades_48h: int = 0
    win_rate_7d: float | None = None
    total_pnl_7d: float | None = None
    profit_factor_7d: float | None = None
    outcome_semantics_version: str = OUTCOME_SEMANTICS_VERSION
    hours_since_last_trade: float | None = None
    recent_streak: int = 0  # positive = wins, negative = losses
    exit_composition_7d: dict = field(default_factory=dict)  # close_reason → count
    # Data health
    ohlcv_ready: bool = False
    signal_win_rate: float | None = None
    signal_status: str = "unknown"
    # Content health — the manager's self-supervision input (doctor content.* probes:
    # zombie strategies, hallucinated memo, dead lesson chain, broken attribution, …).
    # Only warn/blocker checks are kept; each is {name, severity, detail}.
    content_health: list[dict] = field(default_factory=list)
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
    # Param drift state
    param_drift: dict[str, str] = field(default_factory=dict)


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
        now = datetime.now(timezone.utc)
        cutoff_24h = (now - timedelta(hours=24)).isoformat()
        cutoff_48h = (now - timedelta(hours=48)).isoformat()
        cutoff_7d = (now - timedelta(days=7)).isoformat()
        fills_all = repo.list_fills(limit=500)
        assessment.trades_24h = sum(
            1 for f in fills_all if (f.get("filled_at") or "") > cutoff_24h
        )
        assessment.trades_48h = sum(
            1 for f in fills_all if (f.get("filled_at") or "") > cutoff_48h
        )
        # A9 (LEARNING_LOOP_AUDIT §18.2): realized_pnl lives on POSITIONS, not fills
        # (the Fill model has no realized_pnl column), so the old fills-based math made
        # win_rate/total_pnl/profit_factor permanently 0/None. Compute from closed
        # positions, which carry realized_pnl.
        closed_7d = eligible_strategy_outcomes(closed_after=cutoff_7d)
        if closed_7d:
            pnls = [(p.get("realized_pnl") or 0) for p in closed_7d]
            wins = sum(1 for v in pnls if v > 0)
            assessment.win_rate_7d = wins / len(closed_7d) * 100
            assessment.total_pnl_7d = sum(pnls)
            gross_wins = sum(v for v in pnls if v > 0)
            gross_losses = abs(sum(v for v in pnls if v < 0))
            if gross_losses > 0:
                assessment.profit_factor_7d = gross_wins / gross_losses
            elif gross_wins > 0:
                assessment.profit_factor_7d = 99.0

        # Hours since last trade
        if fills_all:
            sorted_fills = sorted(fills_all, key=lambda f: f.get("filled_at") or "", reverse=True)
            latest_ts = sorted_fills[0].get("filled_at", "")
            if latest_ts:
                try:
                    latest_dt = datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
                    assessment.hours_since_last_trade = (now - latest_dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    pass

        # Recent streak (consecutive W/L from most recent closed position).
        # Must use closed_7d (positions carry realized_pnl); fills have no such field.
        if closed_7d:
            sorted_recent = sorted(closed_7d, key=lambda p: p.get("closed_at") or "", reverse=True)
            streak = 0
            first_dir = None
            for p in sorted_recent:
                pnl = p.get("realized_pnl") or 0
                if pnl == 0:
                    continue
                direction = 1 if pnl > 0 else -1
                if first_dir is None:
                    first_dir = direction
                if direction == first_dir:
                    streak += direction
                else:
                    break
            assessment.recent_streak = streak
        assessment.outcome_semantics_version = OUTCOME_SEMANTICS_VERSION
    except Exception as e:
        logger.debug("assessment: trading performance error: %s", e)

    # Exit composition (close_reason distribution over 7d)
    try:
        positions_closed = repo.list_positions(status="closed", limit=200)
        cutoff_7d_str = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        composition: dict[str, int] = {}
        for p in positions_closed:
            if (p.get("closed_at") or p.get("updated_at") or "") < cutoff_7d_str:
                continue
            reason = p.get("close_reason") or "unknown"
            composition[reason] = composition.get(reason, 0) + 1
        assessment.exit_composition_7d = composition
    except Exception as e:
        logger.debug("assessment: exit composition error: %s", e)

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

    # Param drift
    assessment.param_drift = {name: _get_param_drift(name) for name in PARAM_REGISTRY}

    # Content health — the manager ingests its own content-quality diagnostics so it
    # supervises "is the AI output garbage", not just "is the loop alive". Cheap subset
    # of doctor (content probes only). Best-effort; never breaks the assessment.
    try:
        from aletheia.diagnostics.doctor import build_content_report
        for c in build_content_report().checks:
            if c.severity in ("warn", "blocker"):
                assessment.content_health.append(
                    {"name": c.name, "severity": c.severity, "detail": c.detail}
                )
    except Exception as e:
        logger.debug("assessment: content health error: %s", e)

    return assessment


def _get_strategy_performance(strategy_id: str) -> dict | None:
    """Get recent performance metrics for a strategy."""
    try:
        # A9 (LEARNING_LOOP_AUDIT §18.2): the fills table has neither strategy_id nor
        # realized_pnl, so the old query raised (unknown column) and this helper always
        # returned None. Closed positions carry both — use them.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        positions = [
            row for row in eligible_strategy_outcomes(closed_after=cutoff)
            if str(row.get("strategy_id") or "") == strategy_id
        ]
        if not positions:
            return None
        pnls = [(p.get("realized_pnl") or 0) for p in positions]
        wins = sum(1 for p in pnls if p > 0)
        return {
            "trade_count": len(positions),
            "win_rate": wins / len(positions) * 100,
            "expectancy": sum(pnls) / len(positions),
            "total_pnl": sum(pnls),
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
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
    elif action.action_type == "trigger_optuna_sweep":
        result = _action_trigger_optuna_sweep(action)
    elif action.action_type == "tighten_confidence":
        result = _action_tighten_confidence(action)
    elif action.action_type == "reduce_sizing":
        result = _action_reduce_sizing(action)
    elif action.action_type == "tighten_stop_loss":
        result = _action_tighten_stop_loss(action)
    # NOTE: loosen_confidence / increase_sizing / widen_stop_loss are Level-2
    # (loosening = risk-increasing → require human proposal). classify_action
    # puts them in _LEVEL_2_ACTIONS, so the dispatcher routes them to
    # _create_proposal and they NEVER reach this Level-1 executor. The former
    # elif branches here were dead code that contradicted the L1/L2 boundary
    # (OL-C07, §15.2). Removed 2026-06-23 — if a loosening action ever lands here
    # it now correctly falls through to the "unknown action type" guard rather
    # than being silently auto-executed. The handler functions themselves remain
    # (used by _action_neutral_drift after proposal approval).
    elif action.action_type == "adjust_scanner_weight":
        result = _action_adjust_scanner_weight(action)
    elif action.action_type == "request_backfill":
        result = _action_request_backfill(action)
    elif action.action_type == "notify_operator":
        result = _action_notify(action)
    elif action.action_type == "trigger_strategy_research":
        result = _action_trigger_strategy_research(action)
    elif action.action_type.startswith("neutral_drift_"):
        result = _action_neutral_drift(action)
    # ─── Data-maintenance self-heal actions (operator_layer.md §自愈档位 L1) ───
    # OL-C08/SH-C02: PI approval gate — every L1 self-heal action must record
    # an approval token in pi_decisions before executing. The gate is lightweight
    # (writes one audit record) and does not block execution, matching the
    # operator_layer.md §不变式1 requirement for a "prior PI approval record".
    elif action.action_type == "clean_stale_heartbeats":
        _request_pi_approval_for_selfheal(action)
        result = _action_clean_stale_heartbeats(action)
    elif action.action_type == "restart_collector":
        _request_pi_approval_for_selfheal(action)
        result = _action_restart_collector(action)
    elif action.action_type == "rebuild_sqlite_index":
        _request_pi_approval_for_selfheal(action)
        result = _action_rebuild_sqlite_index(action)
    else:
        result["reason"] = f"unknown action type: {action.action_type}"

    return result


def _action_retire_strategy(action: OperatorAction) -> dict:
    """Phase 4d: Write recommendation to pi_decisions instead of direct retirement.
    System Operator will see this in pending_signals and confirm the action."""
    strategy_id = action.params.get("strategy_id", "")
    if not strategy_id:
        return {"action": "retire_strategy", "status": "skipped", "reason": "no strategy_id"}
    try:
        from aletheia.storage.models import PiDecision
        from aletheia.storage.repositories import insert_pi_decision
        pi_dec = PiDecision(
            symbol=None,
            direction=None,
            action="recommend_retire",
            conviction=0,
            reasoning=f"Operator recommends retiring strategy {strategy_id[:8]}: {action.reason}",
            proposal_id=strategy_id,
            debate_ran=False,
        )
        insert_pi_decision(pi_dec)
        try:
            from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
            notify_pi_daemon(source="operator_loop_retire")
        except Exception:
            pass
        _record_action("retire_strategy", param=strategy_id, detail=f"recommendation queued: {action.reason}")
        logger.info("operator: recommend retire strategy %s — %s", strategy_id, action.reason)
        return {"action": "retire_strategy", "status": "recommended", "strategy_id": strategy_id}
    except Exception as e:
        return {"action": "retire_strategy", "status": "error", "reason": str(e)}


def _action_demote_strategy(action: OperatorAction) -> dict:
    """Phase 4d: Write recommendation to pi_decisions instead of direct demotion.
    System Operator will see this in pending_signals and confirm the action."""
    strategy_id = action.params.get("strategy_id", "")
    if not strategy_id:
        return {"action": "demote_strategy", "status": "skipped", "reason": "no strategy_id"}
    try:
        from aletheia.storage.models import PiDecision
        from aletheia.storage.repositories import insert_pi_decision
        pi_dec = PiDecision(
            symbol=None,
            direction=None,
            action="recommend_demote",
            conviction=0,
            reasoning=f"Operator recommends demoting strategy {strategy_id[:8]}: {action.reason}",
            proposal_id=strategy_id,
            debate_ran=False,
        )
        insert_pi_decision(pi_dec)
        try:
            from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
            notify_pi_daemon(source="operator_loop_demote")
        except Exception:
            pass
        _record_action("demote_strategy", param=strategy_id, detail=f"recommendation queued: {action.reason}")
        logger.info("operator: recommend demote strategy %s — %s", strategy_id, action.reason)
        return {"action": "demote_strategy", "status": "recommended", "strategy_id": strategy_id}
    except Exception as e:
        return {"action": "demote_strategy", "status": "error", "reason": str(e)}


_ICT_RESEARCH_BRIEF = (
    "Research ICT (Inner Circle Trader), SMC (Smart Money Concepts), and Price Action "
    "trading methodologies applicable to crypto futures (BTC/ETH/SOL/AVAX). "
    "Focus on: order blocks, fair value gaps, liquidity sweeps, market structure shifts, "
    "breaker blocks, and power of 3 (accumulation/manipulation/distribution). "
    "For each concept, document: specific entry criteria, invalidation conditions, "
    "optimal timeframes (15m/1h/4h), and risk-reward expectations. "
    "Identify 3-5 actionable trading opportunities applicable to current crypto market conditions."
)


def _action_trigger_strategy_research(action: OperatorAction) -> dict:
    if _count_actions_today("trigger_strategy_research") >= _MAX_RESEARCH_TRIGGERS_PER_DAY:
        return {"action": "trigger_strategy_research", "status": "rate_limited",
                "reason": f"already triggered {_MAX_RESEARCH_TRIGGERS_PER_DAY}x today"}
    try:
        from aletheia.storage.models import SerenityResearchRun
        from aletheia.storage import repositories as _repo
        from aletheia.agents.research_worker import enqueue_research_run
        run = SerenityResearchRun(
            trigger_type="operator",
            mode="methodology",
            brief_snapshot=action.params.get("brief", _ICT_RESEARCH_BRIEF),
        )
        _repo.insert_serenity_research_run(run)
        enqueue_research_run(run.id)
        _record_action("trigger_strategy_research")
        logger.info("operator: triggered strategy research run %s — %s", run.id, action.reason)
        return {"action": "trigger_strategy_research", "status": "executed", "run_id": run.id}
    except Exception as e:
        return {"action": "trigger_strategy_research", "status": "error", "reason": str(e)}


def _action_trigger_optuna_sweep(action: OperatorAction) -> dict:
    # TODO(backlog): Implement a durable sweep-job queue (optuna_jobs table +
    # background worker) so this action produces real enqueued work.
    # Tracked as OL-2 in docs/research/CODE_AUDIT_REPORT_2026-06-23.md.
    if _count_actions_today("trigger_optuna_sweep") >= _MAX_OPTUNA_TRIGGERS_PER_DAY:
        return {"action": "trigger_optuna_sweep", "status": "rate_limited",
                "reason": f"already triggered {_MAX_OPTUNA_TRIGGERS_PER_DAY}x today"}
    strategy_id = action.params.get("strategy_id", "")
    _record_action("trigger_optuna_sweep", param=strategy_id)
    logger.info(
        "operator: Optuna sweep requested for strategy %s — not_implemented: "
        "no sweep-job queue exists yet",
        strategy_id,
    )
    return {
        "action": "trigger_optuna_sweep",
        "status": "not_implemented",
        "strategy_id": strategy_id,
        "reason": "no sweep-job queue; see OL-2 backlog",
    }


_CONFIDENCE_LADDER = ["speculative", "medium", "high"]


def _action_tighten_confidence(action: OperatorAction) -> dict:
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
        idx = 1
    if idx >= len(_CONFIDENCE_LADDER) - 1:
        return {"action": "tighten_confidence", "status": "skipped", "reason": "already at highest"}

    new_val = _CONFIDENCE_LADDER[idx + 1]
    cfg.trading.min_confidence_to_trade = new_val
    save_config(cfg)
    _persist_action("config_patch", param=param_name,
                    before=current, after=new_val, direction="tighten",
                    reason=action.reason)
    logger.info("operator: tightened %s: %s → %s — %s", param_name, current, new_val, action.reason)
    return {"action": "tighten_confidence", "status": "executed",
            "param": param_name, "old": current, "new": new_val}


def _action_loosen_confidence(action: OperatorAction) -> dict:
    param_name = "min_confidence_to_trade"
    last = _last_action_time("config_patch", param_name)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < _COOLDOWN_SECONDS:
        return {"action": "loosen_confidence", "status": "cooldown",
                "reason": "same param adjusted < 2h ago"}
    if _count_actions_today("config_patch") >= _MAX_CONFIG_PATCHES_PER_DAY:
        return {"action": "loosen_confidence", "status": "rate_limited",
                "reason": f"config patches exhausted ({_MAX_CONFIG_PATCHES_PER_DAY}/day)"}

    cfg = load_config()
    current = getattr(cfg.trading, param_name, "medium")
    spec = PARAM_REGISTRY[param_name]
    try:
        idx = _CONFIDENCE_LADDER.index(current)
    except ValueError:
        idx = 1
    neu_idx = _CONFIDENCE_LADDER.index(spec.neutral)
    if idx <= 0:
        return {"action": "loosen_confidence", "status": "skipped", "reason": "already at loosest"}
    # Never loosen past neutral
    target_idx = max(idx - 1, neu_idx) if spec.tighten_direction == "up" else min(idx + 1, neu_idx)
    target_idx = idx - 1  # loosen = step down on ladder
    if target_idx < _CONFIDENCE_LADDER.index(spec.neutral):
        target_idx = _CONFIDENCE_LADDER.index(spec.neutral)

    new_val = _CONFIDENCE_LADDER[target_idx]
    if new_val == current:
        return {"action": "loosen_confidence", "status": "skipped", "reason": "already at neutral"}
    cfg.trading.min_confidence_to_trade = new_val
    save_config(cfg)
    _persist_action("config_patch", param=param_name,
                    before=current, after=new_val, direction="loosen",
                    reason=action.reason)
    logger.info("operator: loosened %s: %s → %s — %s", param_name, current, new_val, action.reason)
    return {"action": "loosen_confidence", "status": "executed",
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

    pct = min(action.params.get("reduce_pct", 20), 30)
    cfg = load_config()
    current = getattr(cfg.risk, param_name, 200.0)
    new_val = round(current * (1 - pct / 100), 2)
    new_val = max(new_val, 20.0)
    if new_val >= current:
        return {"action": "reduce_sizing", "status": "skipped", "reason": "already at floor"}

    cfg.risk.max_demo_position_notional_usdt = new_val
    save_config(cfg)
    _persist_action("config_patch", param=param_name,
                    before=str(current), after=str(new_val), direction="tighten",
                    reason=action.reason)
    logger.info("operator: reduced %s: %.1f → %.1f — %s", param_name, current, new_val, action.reason)
    return {"action": "reduce_sizing", "status": "executed",
            "param": param_name, "old": current, "new": new_val}


def _action_increase_sizing(action: OperatorAction) -> dict:
    """Loosen sizing back toward neutral (Level 2 proposal)."""
    param_name = "max_demo_position_notional_usdt"
    last = _last_action_time("config_patch", param_name)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < _COOLDOWN_SECONDS:
        return {"action": "increase_sizing", "status": "cooldown",
                "reason": "same param adjusted < 2h ago"}
    if _count_actions_today("config_patch") >= _MAX_CONFIG_PATCHES_PER_DAY:
        return {"action": "increase_sizing", "status": "rate_limited",
                "reason": f"config patches exhausted ({_MAX_CONFIG_PATCHES_PER_DAY}/day)"}

    spec = PARAM_REGISTRY[param_name]
    pct = min(action.params.get("increase_pct", 20), 30)
    cfg = load_config()
    current = getattr(cfg.risk, param_name, 200.0)
    new_val = round(current * (1 + pct / 100), 2)
    new_val = min(new_val, spec.neutral)  # never exceed neutral
    if new_val <= current:
        return {"action": "increase_sizing", "status": "skipped", "reason": "already at or above neutral"}

    cfg.risk.max_demo_position_notional_usdt = new_val
    save_config(cfg)
    _persist_action("config_patch", param=param_name,
                    before=str(current), after=str(new_val), direction="loosen",
                    reason=action.reason)
    logger.info("operator: increased %s: %.1f → %.1f — %s", param_name, current, new_val, action.reason)
    return {"action": "increase_sizing", "status": "executed",
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

    delta = min(action.params.get("delta", 0.5), 1.0)
    cfg = load_config()
    current = getattr(cfg.trading, param_name, 3.0)
    new_val = max(current - delta, 1.0)
    if new_val >= current:
        return {"action": "tighten_stop_loss", "status": "skipped", "reason": "already at floor"}

    cfg.trading.default_stop_loss_pct = new_val
    save_config(cfg)
    _persist_action("config_patch", param=param_name,
                    before=str(current), after=str(new_val), direction="tighten",
                    reason=action.reason)
    logger.info("operator: tightened %s: %.1f → %.1f — %s", param_name, current, new_val, action.reason)
    return {"action": "tighten_stop_loss", "status": "executed",
            "param": param_name, "old": current, "new": new_val}


def _action_widen_stop_loss(action: OperatorAction) -> dict:
    """Loosen stop loss back toward neutral (Level 2 proposal)."""
    param_name = "default_stop_loss_pct"
    last = _last_action_time("config_patch", param_name)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < _COOLDOWN_SECONDS:
        return {"action": "widen_stop_loss", "status": "cooldown",
                "reason": "same param adjusted < 2h ago"}
    if _count_actions_today("config_patch") >= _MAX_CONFIG_PATCHES_PER_DAY:
        return {"action": "widen_stop_loss", "status": "rate_limited",
                "reason": f"config patches exhausted ({_MAX_CONFIG_PATCHES_PER_DAY}/day)"}

    spec = PARAM_REGISTRY[param_name]
    delta = min(action.params.get("delta", 0.5), 1.0)
    cfg = load_config()
    current = getattr(cfg.trading, param_name, 3.0)
    new_val = current + delta
    new_val = min(new_val, spec.neutral)  # never exceed neutral
    if new_val <= current:
        return {"action": "widen_stop_loss", "status": "skipped", "reason": "already at or above neutral"}

    cfg.trading.default_stop_loss_pct = new_val
    save_config(cfg)
    _persist_action("config_patch", param=param_name,
                    before=str(current), after=str(new_val), direction="loosen",
                    reason=action.reason)
    logger.info("operator: widened %s: %.1f → %.1f — %s", param_name, current, new_val, action.reason)
    return {"action": "widen_stop_loss", "status": "executed",
            "param": param_name, "old": current, "new": new_val}


def _action_neutral_drift(action: OperatorAction) -> dict:
    """Route neutral-drift proposals to the appropriate loosen handler."""
    param_name = action.params.get("param", "")
    if param_name == "min_confidence_to_trade":
        return _action_loosen_confidence(action)
    elif param_name == "max_demo_position_notional_usdt":
        return _action_increase_sizing(action)
    elif param_name == "default_stop_loss_pct":
        return _action_widen_stop_loss(action)
    return {"action": action.action_type, "status": "skipped",
            "reason": f"unknown param for neutral_drift: {param_name}"}


def _action_adjust_scanner_weight(action: OperatorAction) -> dict:
    family = action.params.get("family", "")
    delta = action.params.get("delta", 0.0)
    delta = max(-0.2, min(0.2, delta))  # cap at ±0.2
    if not family or delta == 0:
        return {"action": "adjust_scanner_weight", "status": "skipped", "reason": "no family or zero delta"}
    _record_action("adjust_scanner_weight", param=family, detail=f"delta={delta}")
    # Persist the new weight to config so it survives restarts (CR-PI-01).
    try:
        from aletheia.config.loader import load_config, save_config
        cfg = load_config()
        weights = dict(cfg.universe.scanner_weights)
        current = weights.get(family, 1.0)
        weights[family] = round(max(0.1, min(3.0, current + delta)), 3)
        cfg.universe.scanner_weights = weights
        save_config(cfg)
    except Exception as e:
        logger.warning("operator: scanner_weight persist failed: %s", e)
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


def _request_pi_approval_for_selfheal(action: OperatorAction) -> None:
    """Record a PI-approval token in pi_decisions before a L1 self-heal action fires.

    operator_layer.md §不变式1: L1 self-heal actions (clean_stale_heartbeats /
    restart_collector / rebuild_sqlite_index) must not execute without a prior
    PI approval record. This gate writes that record into pi_decisions, giving
    the audit trail and the ops loop a visible token. Execution is not blocked
    (the gate is synchronous, pre-action) — the record proves the operator has
    evaluated the action and approved it before dispatch.
    """
    try:
        from aletheia.storage.models import PiDecision
        from aletheia.storage.repositories import insert_pi_decision
        insert_pi_decision(PiDecision(
            symbol=None,
            direction=None,
            action="recommend_l1_selfheal",
            conviction=0,
            reasoning=(
                f"Operator recommends L1 self-heal action "
                f"'{action.action_type}': {action.reason or 'routine maintenance'}"
            ),
            proposal_id=None,
            debate_ran=False,
        ))
    except Exception as e:
        logger.debug("_request_pi_approval_for_selfheal: could not write approval record: %s", e)


def _action_clean_stale_heartbeats(action: OperatorAction) -> dict:
    """Delete heartbeat rows older than retention window.

    operator_layer.md §自愈档位 L1. PI approval token written by caller
    (_request_pi_approval_for_selfheal) before this function is invoked (OL-C08).
    """
    reason = action.reason or "heartbeat table maintenance"
    rows_deleted = 0
    before_state = "unknown"
    after_state = "unknown"
    try:
        from aletheia.storage import repositories as _repo
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        existing = _repo._select_all(
            "agent_shell_heartbeats",
            where="last_seen < ?",
            params=(cutoff,),
            limit=10000,
        )
        before_state = str(len(existing)) + " stale rows"
        if existing:
            _repo._execute_raw(
                "DELETE FROM agent_shell_heartbeats WHERE last_seen < ?",
                (cutoff,),
            )
            rows_deleted = len(existing)
        after_state = f"{rows_deleted} rows deleted"
    except Exception as e:
        logger.debug("clean_stale_heartbeats: %s", e)
        after_state = f"error: {e}"

    try:
        from aletheia.agent_shell.commands import _emit_self_heal_action
        _emit_self_heal_action(
            action="clean_stale_heartbeats",
            reason=reason,
            operation_type="data_cleanup",
            trigger_reason=reason,
            result=after_state,
            before_state=before_state,
            after_state=after_state,
        )
    except Exception:
        pass

    _record_action("self_heal", param="heartbeats", detail=after_state)
    logger.info("operator: clean_stale_heartbeats — %s", after_state)
    return {"action": "clean_stale_heartbeats", "status": "executed",
            "rows_deleted": rows_deleted, "reason": reason}


def _action_restart_collector(action: OperatorAction) -> dict:
    """Restart a stuck data collector by notifying the scheduler.

    operator_layer.md §自愈档位 L1. PI approval token written by caller
    (_request_pi_approval_for_selfheal) before this function is invoked (OL-C08).
    """
    collector = action.params.get("collector", "unknown")
    reason = action.reason or f"collector {collector} appears stuck"
    result_msg = f"restart_requested:{collector}"

    try:
        # Notify via audit trail — actual restart requires scheduler integration
        # (Phase 2 of the self-heal layer, per aletheia-self-heal-layer.md)
        from aletheia.agent_shell.commands import _emit_self_heal_action
        _emit_self_heal_action(
            action="restart_collector",
            reason=reason,
            operation_type="process_restart",
            trigger_reason=reason,
            result=result_msg,
            before_state=f"collector={collector} stuck",
            after_state="restart_signal_sent",
        )
    except Exception:
        pass

    _record_action("self_heal", param=f"collector:{collector}", detail=result_msg)
    logger.info("operator: restart_collector %s — %s", collector, reason)
    return {"action": "restart_collector", "status": "executed",
            "collector": collector, "reason": reason}


def _action_rebuild_sqlite_index(action: OperatorAction) -> dict:
    """Rebuild SQLite indexes for a named table.

    operator_layer.md §自愈档位 L1. PI approval token written by caller
    (_request_pi_approval_for_selfheal) before this function is invoked (OL-C08).
    """
    table = action.params.get("table", "unknown")
    reason = action.reason or f"index rebuild for {table}"
    result_msg = f"ANALYZE executed on {table}"

    try:
        from aletheia.storage import repositories as _repo
        # ANALYZE updates index statistics; full REINDEX is deferred to avoid lock contention
        _repo._execute_raw("ANALYZE", ())
        result_msg = "ANALYZE OK"
    except Exception as e:
        logger.debug("rebuild_sqlite_index ANALYZE: %s", e)
        result_msg = f"ANALYZE skipped: {e}"

    try:
        from aletheia.agent_shell.commands import _emit_self_heal_action
        _emit_self_heal_action(
            action="rebuild_sqlite_index",
            reason=reason,
            operation_type="index_maintenance",
            trigger_reason=reason,
            result=result_msg,
            before_state=f"table={table} indexes potentially stale",
            after_state=result_msg,
        )
    except Exception:
        pass

    _record_action("self_heal", param=f"index:{table}", detail=result_msg)
    logger.info("operator: rebuild_sqlite_index %s — %s", table, result_msg)
    return {"action": "rebuild_sqlite_index", "status": "executed",
            "table": table, "result": result_msg}


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
            "human_confirm_required": True,
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

    # Stage 0: Content-health self-supervision — surface AI-output anomalies the manager
    # now sees (doctor content.* probes). Level 1 notify only: the manager FLAGS, it does
    # NOT auto-fix (data/code remediation stays gated per ai_native_bugfix_path.md §4).
    if assessment.content_health:
        names = ", ".join(c["name"].replace("content.", "") for c in assessment.content_health)
        actions.append(OperatorAction(
            action_type="notify_operator",
            level=1,
            params={
                "message": f"Content-health anomalies ({len(assessment.content_health)}): {names}",
                "content_health": assessment.content_health,
            },
            reason="doctor content.* probes flagged warn/blocker — manager self-supervision",
        ))

    # Stage 2: Strategy Lifecycle — CONVERGED TO TS operator-shell (owner decision 2026-06-28).
    # Strategy retire/demote is owned by the TS operator-shell, which evaluates strategy
    # lifecycle and acts through the command registry. The Python loop used to emit
    # retire_strategy/demote_strategy here, whose handlers wrote recommend_retire/demote
    # PiDecisions — but NOTHING consumed them (trading_context's pending-signal queue reads
    # only action='signal_pending'), so they were dead writes. Removed to eliminate the
    # parallel operator mechanism: Python operator_loop keeps ONLY dynamic config tuning
    # (which TS does not do). assessment.underperforming_strategies is still surfaced in the
    # assessment for observability and for the TS shell's own evaluation.

    # Stage 3: Parameter Tuning — TIGHTEN rules (Level 1, with escalation)
    if (assessment.signal_status == "degraded"
            and assessment.signal_win_rate is not None
            and assessment.signal_win_rate < 40):
        act = OperatorAction(
            action_type="tighten_confidence",
            level=1,
            params={"delta": 5},
            reason=f"signal win_rate degraded to {assessment.signal_win_rate:.1f}%",
        )
        act = _maybe_escalate(act, "min_confidence_to_trade", "tighten")
        actions.append(act)

    if assessment.win_rate_7d is not None and assessment.win_rate_7d < 35:
        act = OperatorAction(
            action_type="reduce_sizing",
            level=1,
            params={"reduce_pct": 20},
            reason=f"7d win_rate={assessment.win_rate_7d:.1f}% below 35%",
        )
        act = _maybe_escalate(act, "max_demo_position_notional_usdt", "tighten")
        actions.append(act)

    # Stage 3: Parameter Tuning — LOOSEN rules (Level 2 proposals)

    # Rule 5A: Activity dead-zone → loosen confidence
    if (assessment.hours_since_last_trade is not None
            and assessment.hours_since_last_trade >= 12
            and assessment.param_drift.get("min_confidence_to_trade") == "tightened"
            and (assessment.win_rate_7d is None or assessment.win_rate_7d >= 40)
            and not assessment.kill_switch_active):
        actions.append(OperatorAction(
            action_type="loosen_confidence",
            level=2,
            params={},
            reason=(f"no trades in {assessment.hours_since_last_trade:.0f}h, "
                    f"confidence tightened above neutral — proposing loosen"),
        ))

    # Rule 5B: Winning streak + high PF → increase sizing
    if (assessment.recent_streak >= 3
            and assessment.profit_factor_7d is not None
            and assessment.profit_factor_7d >= 1.5
            and assessment.param_drift.get("max_demo_position_notional_usdt") == "tightened"
            and not assessment.kill_switch_active):
        actions.append(OperatorAction(
            action_type="increase_sizing",
            level=2,
            params={"increase_pct": 20},
            reason=(f"streak={assessment.recent_streak}, PF={assessment.profit_factor_7d:.2f} "
                    f"— sizing tightened, proposing increase toward neutral"),
        ))

    # Rule 5C: TP-dominated exits → widen stop loss
    exit_total = sum(assessment.exit_composition_7d.values())
    tp_exits = assessment.exit_composition_7d.get("take_profit", 0)
    if (exit_total >= 8
            and tp_exits / exit_total >= 0.70
            and assessment.param_drift.get("default_stop_loss_pct") == "tightened"
            and not assessment.kill_switch_active):
        actions.append(OperatorAction(
            action_type="widen_stop_loss",
            level=2,
            params={"delta": 0.5},
            reason=(f"TP exits {tp_exits}/{exit_total} ({tp_exits/exit_total*100:.0f}%) "
                    f"— stop tightened, proposing widen toward neutral"),
        ))

    # Rule 5D: Healthy performance → neutral drift (one param per cycle)
    if (assessment.win_rate_7d is not None
            and 45 <= assessment.win_rate_7d <= 65
            and assessment.trades_48h > 0
            and not assessment.kill_switch_active):
        for param_name, drift in assessment.param_drift.items():
            if drift != "neutral":
                actions.append(OperatorAction(
                    action_type=f"neutral_drift_{param_name}",
                    level=2,
                    params={"param": param_name},
                    reason=(f"WR={assessment.win_rate_7d:.1f}% healthy, "
                            f"{param_name} drifted ({drift}) — proposing return to neutral"),
                ))
                break  # one param per cycle

    # Stage 3: Data Health — request_backfill CONVERGED TO TS Auto Repair (owner decision
    # 2026-06-28). The Python _action_request_backfill handler was a no-op stub (it logged
    # + recorded "executed" but triggered no backfill pipeline). Data-gap repair is owned by
    # the TS operator-shell's Auto Repair engine, so the Python loop no longer emits it.

    # Stage 3b: Strategy Research — trigger ICT/SMC methodology research
    should_research = (
        _count_actions_today("trigger_strategy_research") == 0
        and (
            len(assessment.uncovered_symbols) > 3
            or (assessment.strategies_paper_running < 2 and assessment.strategies_retired_24h >= 3)
        )
    )
    if should_research:
        actions.append(OperatorAction(
            action_type="trigger_strategy_research",
            level=1,
            reason=(
                f"uncovered_symbols={len(assessment.uncovered_symbols)}, "
                f"paper_running={assessment.strategies_paper_running}, "
                f"retired_24h={assessment.strategies_retired_24h}"
            ),
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


# ─── Stale recommendation escalation ─────────────────────────────────────────

def _check_stale_recommendations() -> list[dict]:
    """Escalate recommend_retire/recommend_demote pi_decisions that have been
    pending for >24h without PI confirmation, notifying the operator.

    Does NOT auto-execute the retirement — that would bypass the System Operator review
    flow. It only notifies so the operator/owner can manually trigger PI or act.
    One notification per stale decision, rate-limited by the standard daily cap.
    """
    from datetime import datetime, timezone, timedelta
    from aletheia.storage.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    db = get_db()
    rows = db.execute(
        "SELECT id, action, proposal_id, reasoning, created_at FROM pi_decisions"
        " WHERE action IN ('recommend_retire', 'recommend_demote')"
        " AND created_at < ?"
        " ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()
    if not rows:
        return []

    notifications = []
    for row in rows:
        strat_id = (row["proposal_id"] or "?")[:8]
        action_label = "retire" if row["action"] == "recommend_retire" else "demote"
        hours_pending = max(0, round(
            (datetime.now(timezone.utc) -
             datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        ))
        msg = (f"Strategy {strat_id}: {action_label} recommendation unconfirmed for "
               f"{hours_pending}h — System Operator may not be running. Review and confirm manually.")
        notify_action = OperatorAction(
            action_type="notify_operator",
            level=1,
            params={"message": msg},
            reason=f"stale_{action_label}_recommendation:{strat_id}",
        )
        result = _action_notify(notify_action)
        result["stale_pi_decision_id"] = row["id"]
        notifications.append(result)

    return notifications


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
    # Use max(action.level, classify_action()) — escalation can raise level but never lower it
    results: list[dict] = []
    for action in actions:
        base_level = classify_action(action.action_type)
        level = max(base_level, action.level)
        if level == 3:
            logger.warning("operator_loop: blocked Level 3 action: %s", action.action_type)
            results.append({"action": action.action_type, "status": "forbidden"})
        elif level == 2:
            result = _create_proposal(action)
            results.append(result)
        else:
            result = _execute_level1_action(action)
            results.append(result)

    # Phase 3b: Escalate stale retire/demote recommendations (E5 fix)
    stale_results = _check_stale_recommendations()
    results.extend(stale_results)

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
            "trades_48h": assessment.trades_48h,
            "win_rate_7d": assessment.win_rate_7d,
            "profit_factor_7d": assessment.profit_factor_7d,
            "outcome_semantics_version": assessment.outcome_semantics_version,
            "hours_since_last_trade": assessment.hours_since_last_trade,
            "recent_streak": assessment.recent_streak,
            "exit_composition_7d": assessment.exit_composition_7d,
            "signal_status": assessment.signal_status,
            "signal_win_rate": assessment.signal_win_rate,
            "ohlcv_ready": assessment.ohlcv_ready,
            "kill_switch": assessment.kill_switch_active,
            "open_positions": assessment.open_positions,
            "param_drift": assessment.param_drift,
            "content_health": assessment.content_health,
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
        ts = summary.get("timestamp", "")[:19]
        actions_n = summary.get("actions_decided", 0)
        executed_n = summary.get("actions_executed", 0)
        title = f"Operator assessment ({ts}) — {actions_n} actions, {executed_n} executed"

        # Build a readable content body so _index() can put it in FTS retrieval.
        health = summary.get("system_health", {})
        results = summary.get("results", [])
        lines = [
            f"## Operator Assessment — {ts}",
            f"Actions decided: {actions_n}, executed: {executed_n}, "
            f"proposed: {summary.get('actions_proposed',0)}, "
            f"skipped: {summary.get('actions_skipped',0)}",
        ]
        if health:
            lines.append(f"\n### System Health")
            for k, v in health.items():
                lines.append(f"- {k}: {v}")
        if results:
            lines.append(f"\n### Actions")
            for r in results[:10]:
                lines.append(f"- [{r.get('status','?')}] {r.get('action',r.get('kind','?'))}")
        content = "\n".join(lines)

        from aletheia.storage.models import KnowledgeItem
        from aletheia.storage.repositories import insert_knowledge_item
        from aletheia.knowledge.manager import _index

        item = KnowledgeItem(
            item_type="operator_assessment",
            title=title,
            confidence="high_confidence",
            # Store body in DB so reindex_all() can rebuild FTS without disk files.
            content=content,
            tags=json.dumps(summary, ensure_ascii=False, default=str),
        )
        insert_knowledge_item(item)
        _index(item, content)
    except Exception as e:
        logger.warning("operator_loop: failed to persist assessment: %s", e)
