"""Lifecycle automation — applies analytics findings to strategy parameters.

Consumes:
  - exit_policy_advisor → writes suggested_exit_params to strategy
  - strategy_evidence → auto-pauses/retires failing strategies

Runs as part of the daily backtest_orchestrator cycle.
All mutations are audited. No LLM calls.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from aletheia.storage.repositories import (
    get_strategy,
    list_strategies,
    update_strategy,
)

logger = logging.getLogger("aletheia.analytics.lifecycle_automation")

# Exit policy suggestions that are safe to auto-apply as advisory hints
_APPLICABLE_SUGGESTIONS = {
    "switch_to_atr_stop",
    "increase_stop_loss_pct",
    "add_trailing_stop_candidate",
    "shorten_max_hold",
    "extend_max_hold",
    "increase_take_profit_pct",
}


def apply_exit_policy_feedback() -> dict:
    """Run exit_policy_advisor and write suggested params to strategy records.

    Only applies strategy-level advice (group_type='strategy') with high/medium
    confidence. Writes to strategy.parameters.suggested_exit_params (advisory —
    the paper_trader does NOT auto-apply these, but the UI surfaces them and
    strategy_designer reads them for the next iteration).

    Returns {applied: int, skipped: int}.
    """
    from aletheia.analytics.exit_policy_advisor import build_exit_policy_advice
    from aletheia.files.jsonl import write_audit

    advice_rows = build_exit_policy_advice(limit=500, min_trades=3)
    applied = 0
    skipped = 0

    for row in advice_rows:
        if row.get("group_type") != "strategy":
            continue
        if row.get("confidence") == "low":
            skipped += 1
            continue
        if row.get("suggested_change") in ("no_change", "pause_factor_until_more_data"):
            skipped += 1
            continue
        if row.get("suggested_change") not in _APPLICABLE_SUGGESTIONS:
            skipped += 1
            continue

        strategy_id = row.get("group_key")
        if not strategy_id:
            continue

        strategy = get_strategy(strategy_id)
        if not strategy:
            continue

        params_raw = strategy.get("parameters") or "{}"
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
        except (ValueError, TypeError):
            params = {}

        suggestion = {
            "suggested_change": row["suggested_change"],
            "diagnosis": row["diagnosis"],
            "confidence": row["confidence"],
            "avg_mfe_pct": row.get("avg_mfe_pct"),
            "avg_mae_pct": row.get("avg_mae_pct"),
            "trade_count": row.get("trade_count"),
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }

        existing = params.get("suggested_exit_params", {})
        if existing.get("suggested_change") == suggestion["suggested_change"]:
            skipped += 1
            continue

        params["suggested_exit_params"] = suggestion
        update_strategy(strategy_id, parameters=json.dumps(params))
        applied += 1

        write_audit({
            "actor": "system",
            "source": "lifecycle_automation",
            "event_type": "exit_policy_applied",
            "entity_type": "strategy",
            "entity_id": strategy_id,
            "message": (
                f"Exit policy advice applied: {suggestion['suggested_change']} "
                f"(diagnosis={suggestion['diagnosis']}, "
                f"confidence={suggestion['confidence']}, "
                f"trades={suggestion['trade_count']})"
            ),
        })

    return {"applied": applied, "skipped": skipped}


def apply_strategy_evidence_actions() -> dict:
    """Run strategy_evidence and auto-pause/retire strategies per recommendation.

    Actions:
      - retire_candidate → set state='retired', paper_trading_paused=1
      - pause_paper_trading → set paper_trading_paused=1
      - needs_executable_ruleset with state=paper_running → pause (can't execute)

    Returns {paused: int, retired: int, skipped: int}.
    """
    from aletheia.analytics.strategy_evidence import build_strategy_evidence_snapshot
    from aletheia.files.jsonl import write_audit

    evidence_rows = build_strategy_evidence_snapshot(limit=200)
    paused = 0
    retired = 0
    skipped = 0

    for row in evidence_rows:
        action = row.get("recommended_action")
        strategy_id = row.get("strategy_id")
        state = row.get("state")

        if not strategy_id or not action:
            skipped += 1
            continue

        if action == "retire_candidate" and state not in ("retired",):
            update_strategy(strategy_id, state="retired", paper_trading_paused=1)
            retired += 1
            write_audit({
                "actor": "system",
                "source": "lifecycle_automation",
                "event_type": "strategy_auto_retired",
                "entity_type": "strategy",
                "entity_id": strategy_id,
                "message": (
                    f"Strategy auto-retired: evidence_status={row.get('evidence_status')}, "
                    f"reasons={row.get('recommended_action_reasons')}"
                ),
            })

        elif action == "pause_paper_trading" and not row.get("paper_trading_paused"):
            update_strategy(strategy_id, paper_trading_paused=1)
            paused += 1
            write_audit({
                "actor": "system",
                "source": "lifecycle_automation",
                "event_type": "strategy_auto_paused",
                "entity_type": "strategy",
                "entity_id": strategy_id,
                "message": (
                    f"Strategy auto-paused: evidence_status={row.get('evidence_status')}, "
                    f"reasons={row.get('recommended_action_reasons')}"
                ),
            })

        elif action == "needs_executable_ruleset" and state == "paper_running":
            if not row.get("paper_trading_paused"):
                update_strategy(strategy_id, paper_trading_paused=1)
                paused += 1
                write_audit({
                    "actor": "system",
                    "source": "lifecycle_automation",
                    "event_type": "strategy_auto_paused",
                    "entity_type": "strategy",
                    "entity_id": strategy_id,
                    "message": "Strategy paused: no executable ruleset in paper_running state",
                })
        else:
            skipped += 1

    return {"paused": paused, "retired": retired, "skipped": skipped}


def run_lifecycle_automation() -> dict:
    """Run all lifecycle automation steps. Called from scheduler."""
    results = {}
    try:
        results["exit_policy"] = apply_exit_policy_feedback()
    except Exception as e:
        logger.warning("exit_policy_feedback failed: %s", e)
        results["exit_policy"] = {"error": str(e)}

    try:
        results["evidence_actions"] = apply_strategy_evidence_actions()
    except Exception as e:
        logger.warning("strategy_evidence_actions failed: %s", e)
        results["evidence_actions"] = {"error": str(e)}

    try:
        from aletheia.analytics.guard_promotion import run_promotion_check
        results["guard_promotion"] = run_promotion_check()
    except Exception as e:
        logger.warning("guard_promotion failed (non-fatal): %s", e)
        results["guard_promotion"] = {"error": str(e)}

    return results
