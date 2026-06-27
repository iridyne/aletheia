"""Command handlers for the Agent Shell control plane."""

import json
import logging
import re
from datetime import datetime, timezone

from aletheia.agent_runtime.orchestrator import run_agent
import aletheia.config.loader as config_loader
from aletheia.storage.repositories import (
    get_agent_run,
    get_strategy,
    get_trade_proposal,
    list_agent_runs,
    list_fills,
    list_knowledge_items,
    list_market_snapshots,
    list_opportunities,
    list_orders,
    list_positions,
    list_risk_decisions,
    list_run_steps,
    list_trade_proposals,
    list_strategies,
    list_tool_calls,
    update_opportunity,
    update_strategy,
)

logger = logging.getLogger("aletheia.agent_shell.commands")

_FORBIDDEN_EXTENSION_CAPABILITIES = {
    "credentials_access": "credentials access",
    "live_execution_access": "live execution access",
    "risk_limit_mutation": "risk-limit mutation",
    "audit_log_suppression": "audit-log suppression",
    "knowledge_hard_deletion": "knowledge hard deletion",
}

_REVIEW_REPORT_SPECS = {
    "trade_review": {
        "artifact_type": "review_report",
        "report_dir": "trade_review",
        "default_title": "Trade Review Report",
    },
    "risk_review": {
        "artifact_type": "review_report",
        "report_dir": "risk",
        "default_title": "Risk Review Report",
    },
    "agent_performance": {
        "artifact_type": "review_report",
        "report_dir": "agent_performance",
        "default_title": "Agent Performance Report",
    },
}

_DIAGNOSTIC_REPORT_SPECS = {
    "system_health": {
        "artifact_type": "diagnostic_report",
        "report_dir": "system_health",
        "default_title": "System Health Diagnostic Report",
    },
    "worker_health": {
        "artifact_type": "diagnostic_report",
        "report_dir": "worker_health",
        "default_title": "Worker Health Diagnostic Report",
    },
    "model_runtime_health": {
        "artifact_type": "diagnostic_report",
        "report_dir": "model_runtime_health",
        "default_title": "Model Runtime Health Diagnostic Report",
    },
    "data_source_health": {
        "artifact_type": "diagnostic_report",
        "report_dir": "data_source_health",
        "default_title": "Data Source Health Diagnostic Report",
    },
}


def _count_phrase(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _parse_json_list(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value)
    except Exception:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _join_summaries(items: list[str], *, limit: int = 3) -> str:
    rendered = []
    for item in items[:limit]:
        text = str(item).strip()
        if not text:
            continue
        rendered.append(text if text.endswith(".") else f"{text}.")
    if not rendered:
        return ""
    if len(items) > limit:
        rendered.append(f"{len(items) - limit} more item(s) are also active.")
    return " ".join(rendered)


def _strategy_inputs(params: dict, *, default_title: str) -> tuple[str, str, dict]:
    title = str(params.get("title") or params.get("name") or default_title).strip() or default_title
    content = str(params.get("content") or params.get("description") or "").strip()
    raw_parameters = params.get("parameters")
    parameters = raw_parameters if isinstance(raw_parameters, dict) else {}
    return title, content, parameters


def _default_schedule_for(agent_name: str) -> str | None:
    schedules = {
        "market_data": "every 5 minutes",
        "market_observer": "every 15 minutes",
        "paper_trader": "every 15 minutes",
        "pi_trading_decision": "every 15 minutes",
        "position_manager": "every 5 minutes",
        "exchange_sync": "every 5 minutes",
        "portfolio_snapshot": "every 10 minutes",
        "post_trade_reviewer": "every 4 hours",
        "risk_critic": "every 6 hours",
        "dreaming_agent": "daily at 02:00",
        "strategy_designer": "daily at 01:00",
    }
    return schedules.get(agent_name)


def _normalize_extension_capability(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _slash_command_is_valid(value: str) -> bool:
    return bool(re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9._-]*", value or ""))


def _extension_sandbox_checks(manifest: dict) -> list[dict[str, str]]:
    from aletheia.agent_shell.registry import get_command, FORBIDDEN_INTENTS

    checks: list[dict[str, str]] = []
    slash_command = str(manifest.get("slash_command") or "").strip()
    if _slash_command_is_valid(slash_command):
        checks.append(
            {"check": "slash_command", "status": "passed", "message": f"Slash command {slash_command} is valid."}
        )
    else:
        checks.append(
            {
                "check": "slash_command",
                "status": "failed",
                "message": "Slash command must start with '/' and contain only letters, numbers, '.', '_' or '-'.",
            }
        )

    target_command = str(manifest.get("target_command") or "").strip()
    command_def = get_command(target_command)
    if command_def is None or target_command in FORBIDDEN_INTENTS:
        checks.append(
            {
                "check": "target_command",
                "status": "failed",
                "message": f"Target command '{target_command}' is not an allowed typed command binding.",
            }
        )
    else:
        checks.append(
            {
                "check": "target_command",
                "status": "passed",
                "message": f"Target command '{target_command}' is registered and permission-gated.",
            }
        )

    if manifest.get("sandbox_only") is True:
        checks.append(
            {
                "check": "sandbox_only",
                "status": "passed",
                "message": "Manifest is marked sandbox-only.",
            }
        )
    else:
        checks.append(
            {
                "check": "sandbox_only",
                "status": "failed",
                "message": "Manifest must stay sandbox-only before human approval.",
            }
        )

    if manifest.get("requires_human_approval") is True:
        checks.append(
            {
                "check": "human_approval",
                "status": "passed",
                "message": "Manifest requires human approval before enablement.",
            }
        )
    else:
        checks.append(
            {
                "check": "human_approval",
                "status": "failed",
                "message": "Manifest must explicitly require human approval before enablement.",
            }
        )

    raw_capabilities = manifest.get("capabilities")
    if raw_capabilities in (None, "", []):
        capabilities: list[str] = []
    elif isinstance(raw_capabilities, list):
        capabilities = [_normalize_extension_capability(str(item)) for item in raw_capabilities if str(item).strip()]
    else:
        capabilities = [_normalize_extension_capability(str(raw_capabilities))]
    forbidden = sorted(
        _FORBIDDEN_EXTENSION_CAPABILITIES[name]
        for name in set(capabilities)
        if name in _FORBIDDEN_EXTENSION_CAPABILITIES
    )
    if forbidden:
        checks.append(
            {
                "check": "capabilities",
                "status": "failed",
                "message": f"Forbidden capabilities requested: {', '.join(forbidden)}.",
            }
        )
    else:
        checks.append(
            {
                "check": "capabilities",
                "status": "passed",
                "message": "No forbidden extension capabilities were requested.",
            }
        )

    default_parameters = manifest.get("default_parameters")
    if default_parameters in (None, {}) or isinstance(default_parameters, dict):
        checks.append(
            {
                "check": "default_parameters",
                "status": "passed",
                "message": "Default parameters are absent or structured as an object.",
            }
        )
    else:
        checks.append(
            {
                "check": "default_parameters",
                "status": "failed",
                "message": "Default parameters must be a JSON object when provided.",
            }
        )
    return checks


def _proposal_session_id() -> str:
    return config_loader.load_config().operator.main_session_id


def _report_spec(report_type: str, *, diagnostic: bool) -> dict | None:
    specs = _DIAGNOSTIC_REPORT_SPECS if diagnostic else _REVIEW_REPORT_SPECS
    return specs.get(report_type)


def _default_report_title(report_type: str, *, diagnostic: bool) -> str:
    spec = _report_spec(report_type, diagnostic=diagnostic) or {}
    base = str(spec.get("default_title") or "Operator Report").strip() or "Operator Report"
    return f"{base} {datetime.now(timezone.utc).date().isoformat()}"


def _create_report_artifact(
    *,
    artifact_type: str,
    report_type: str,
    title: str,
    body: str,
    summary: str,
    file_path: str,
    metadata: dict | None = None,
) -> str:
    from aletheia.agent_shell.operator_store import create_artifact, create_event

    session_id = _proposal_session_id()
    artifact_id = create_artifact(
        task_id=None,
        session_id=session_id,
        artifact_type=artifact_type,
        title=title,
        content={
            "report_type": report_type,
            "title": title,
            "summary": summary,
            "file_path": file_path,
            "body": body,
            "metadata": metadata or {},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    create_event(
        event_type=f"{artifact_type}_created",
        source="agent_shell_command",
        session_id=session_id,
        related_entity_type="report",
        related_entity_id=artifact_id,
        payload={
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "report_type": report_type,
            "title": title,
            "file_path": file_path,
            "summary": summary,
        },
    )
    return artifact_id


def _truthy_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)


def _is_strategy_under_review(strategy: dict | None) -> bool:
    return _truthy_flag((strategy or {}).get("under_review"))


def _is_paper_strategy_paused(strategy: dict | None) -> bool:
    return _truthy_flag((strategy or {}).get("paper_trading_paused"))


def _resolve_strategy(
    strategy_id: str | None,
    *,
    preferred_states: set[str] | None = None,
) -> tuple[dict | None, list[dict]]:
    strategies = list_strategies(limit=50)
    if strategy_id:
        return get_strategy(strategy_id), strategies
    if preferred_states:
        for strategy in strategies:
            if strategy.get("state") in preferred_states:
                return strategy, strategies
    return (strategies[0] if strategies else None), strategies


def _create_proposal_artifact(
    *,
    artifact_type: str,
    title: str,
    content: dict,
    related_entity_type: str,
    related_entity_id: str | None,
) -> str:
    from aletheia.agent_shell.operator_store import create_artifact, create_event

    session_id = _proposal_session_id()
    artifact_id = create_artifact(
        task_id=None,
        session_id=session_id,
        artifact_type=artifact_type,
        title=title,
        content=content,
    )
    create_event(
        event_type="proposal_artifact_created",
        source="agent_shell_command",
        session_id=session_id,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id or artifact_id,
        payload={
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "title": title,
        },
    )
    return artifact_id


def _format_operator_snapshot(heartbeat: dict | None, *, paused: bool) -> dict:
    default_mode = "Paused" if paused else "Observing"
    if heartbeat is None:
        return {
            "status": "offline",
            "mode": default_mode,
            "current_task_id": None,
            "current_intent": None,
            "queue_size": 0,
            "session_id": "operator-main",
        }

    operator = {
        "status": heartbeat.get("status", "offline"),
        "mode": heartbeat.get("mode") or default_mode,
        "current_task_id": heartbeat.get("current_task_id"),
        "current_intent": heartbeat.get("current_intent"),
        "queue_size": heartbeat.get("queue_size", 0),
        "session_id": heartbeat.get("session_id") or "operator-main",
        "last_seen_at": heartbeat.get("last_seen_at"),
        "last_error": heartbeat.get("last_error"),
    }
    last_seen_at = heartbeat.get("last_seen_at")
    if last_seen_at:
        try:
            seen = datetime.fromisoformat(str(last_seen_at).replace("Z", "+00:00"))
            stale_after = config_loader.load_config().operator.heartbeat_stale_after_sec
            if (datetime.now(timezone.utc) - seen).total_seconds() > stale_after and operator["status"] == "online":
                operator["status"] = "stale"
        except Exception:
            pass
    return operator


async def get_system_status(params: dict) -> dict:
    from aletheia.agent_runtime.health import compute_health
    cfg = config_loader.load_config()
    health = compute_health()
    try:
        from aletheia.agent_shell.operator_store import get_latest_heartbeat, list_tasks

        heartbeat = get_latest_heartbeat()
        failed_tasks = list_tasks(limit=20, status="failed")
        latest_task = list_tasks(limit=1)
    except Exception:
        heartbeat = None
        failed_tasks = []
        latest_task = []
    operator = _format_operator_snapshot(heartbeat, paused=cfg.runtime.paused)
    runs = list_agent_runs(limit=10)
    failed_runs = sum(1 for run in runs if run.get("status") in {"failed", "error"})
    positions = list_positions(status="open", limit=20)
    proposals = list_trade_proposals(limit=20)
    pending_proposals = sum(1 for proposal in proposals if proposal.get("status") == "pending")
    issues = health["issues"]
    issue_text = ", ".join(issues) if issues else "no current issues"
    operator_intent = operator.get("current_intent") or "monitoring system state"
    latest_task_type = latest_task[0]["task_type"] if latest_task else None
    latest_task_status = latest_task[0]["status"] if latest_task else None
    message = (
        f"Gateway is {'healthy' if health['healthy'] else f'degraded: {issue_text}'}. "
        f"Operator is {operator['status']} in {operator['mode']} mode, currently {operator_intent}. "
        f"There are {_count_phrase(failed_runs, 'failed recent agent run')}, "
        f"{_count_phrase(len(positions), 'open position')}, "
        f"{_count_phrase(pending_proposals, 'pending proposal')}, and "
        f"{_count_phrase(len(failed_tasks), 'failed operator task')}."
    )
    if latest_task_type and latest_task_status:
        message += f" Latest operator task: {latest_task_type} ({latest_task_status})."
    return {
        "message": message,
        "system": {
            "health": "healthy" if health["healthy"] else "degraded",
            "issues": issues,
            "worker_permission_mode": cfg.agent.permission_mode,
            "operator_permission_mode": cfg.operator.permission_mode,
            "live_state": cfg.live.state,
            "paused": cfg.runtime.paused,
            "kill_switch": cfg.runtime.kill_switch_active,
            "exchange_mode": cfg.exchange.mode,
            "scheduler_enabled": cfg.runtime.scheduler_enabled,
        },
        "operator": operator,
        "activity": {
            "recent_run_count": len(runs),
            "failed_recent_run_count": failed_runs,
            "open_position_count": len(positions),
            "pending_proposal_count": pending_proposals,
            "failed_operator_task_count": len(failed_tasks),
        },
    }


async def get_operator_status(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import get_latest_heartbeat, list_tasks

    cfg = config_loader.load_config()
    heartbeat = get_latest_heartbeat()
    operator = _format_operator_snapshot(heartbeat, paused=cfg.runtime.paused)
    recent_tasks = list_tasks(limit=10)
    active_task = next(
        (task for task in recent_tasks if task["id"] == operator.get("current_task_id")),
        None,
    )
    queue_size = int(operator.get("queue_size") or 0)
    current_intent = operator.get("current_intent") or "monitoring system state"

    message = (
        f"Operator is {operator['status']} in {operator['mode']} mode, currently {current_intent}, "
        f"with queue size {queue_size}."
    )
    if active_task is not None:
        message += (
            f" Active task: {active_task['task_type']} ({active_task['status']})."
        )
    if operator.get("last_error"):
        message += f" Last error: {operator['last_error']}."

    return {
        "message": message,
        "operator": operator,
        "active_task": active_task,
        "recent_tasks": recent_tasks[:5],
    }


async def get_agent_activity(params: dict) -> dict:
    runs = list_agent_runs(limit=15)
    return {
        "runs": [
            {"id": r["id"], "agent": r["agent_name"], "status": r["status"],
             "started": r["started_at"], "ended": r.get("ended_at"),
             "trigger": r.get("trigger_type", "scheduled")}
            for r in runs
        ],
        "total": len(runs),
    }


async def get_market_snapshot(params: dict) -> dict:
    snaps = list_market_snapshots(limit=10)
    return {
        "snapshots": [
            {"symbol": s["symbol"], "price": s["price"],
             "change_24h_pct": s.get("change_pct_24h"),
             "volume_24h": s.get("volume_24h"),
             "time": s.get("snapshot_at")}
            for s in snaps
        ],
    }


async def get_positions(params: dict) -> dict:
    positions = list_positions(status="open", limit=20)
    return {
        "positions": [
            {"id": p["id"], "symbol": p["symbol"], "direction": p["direction"],
             "quantity": p["quantity"], "entry_price": p["entry_price"],
             "unrealized_pnl": p.get("unrealized_pnl", 0),
             "opened_at": p.get("opened_at")}
            for p in positions
        ],
        "count": len(positions),
    }


async def explain_agent_run(params: dict) -> dict:
    run_id = params.get("run_id")
    if not run_id:
        runs = list_agent_runs(limit=1)
        if not runs:
            return {"error": "No agent runs found."}
        run_id = runs[0]["id"]

    run = get_agent_run(run_id)
    if not run:
        return {"error": f"Agent run '{run_id}' not found."}

    steps = list_run_steps(run_id, limit=20)
    tools = list_tool_calls(run_id, limit=20)
    return {
        "run": {
            "id": run["id"], "agent": run["agent_name"], "status": run["status"],
            "started": run["started_at"], "ended": run.get("ended_at"),
            "errors": run.get("errors", "[]"),
        },
        "steps": [{"action": s["action"], "output": s.get("output_data", "")[:200]} for s in steps],
        "tools_used": [{"tool": t["tool_name"], "permitted": t["permitted"]} for t in tools],
    }


async def explain_proposal(params: dict) -> dict:
    proposal_id = params.get("proposal_id")
    if not proposal_id:
        proposals = list_trade_proposals(limit=1)
        if not proposals:
            return {"error": "No proposals found."}
        proposal_id = proposals[0]["id"]

    proposal = get_trade_proposal(proposal_id)
    if not proposal:
        return {"error": f"Proposal '{proposal_id}' not found."}

    decisions = list_risk_decisions(limit=50)
    related = [d for d in decisions if d.get("proposal_id") == proposal_id]
    latest = related[0] if related else None
    violations = _parse_json_list(latest.get("violations")) if latest else []
    related_orders = [order for order in list_orders(limit=100) if order.get("proposal_id") == proposal_id]
    related_order_ids = {order["id"] for order in related_orders}
    related_fills = [fill for fill in list_fills(limit=100) if fill.get("order_id") in related_order_ids]

    if latest is None:
        message = (
            f"Proposal {proposal['symbol']} {proposal['direction']} is currently {proposal['status']}. "
            "There is no recorded risk decision yet."
        )
    elif latest.get("approved"):
        message = (
            f"Proposal {proposal['symbol']} {proposal['direction']} passed risk with "
            f"risk score {latest.get('risk_score')}. "
            f"Execution mode allowed: {latest.get('allowed_execution_mode') or proposal.get('execution_mode') or 'unknown'}."
        )
    else:
        violation_text = ", ".join(violations) if violations else "no explicit violations recorded"
        message = (
            f"Proposal {proposal['symbol']} {proposal['direction']} is {proposal['status']}. "
            f"Risk blocked it with risk score {latest.get('risk_score')}. "
            f"Violations: {violation_text}. "
            f"Risk message: {latest.get('message') or 'none'}."
        )

    return {
        "message": message,
        "proposal": {
            "id": proposal["id"], "symbol": proposal["symbol"],
            "direction": proposal["direction"], "status": proposal["status"],
            "rationale": proposal.get("agent_rationale", ""),
        },
        "latest_risk_decision": (
            {
                "approved": bool(latest["approved"]),
                "risk_score": latest["risk_score"],
                "violations": violations,
                "message": latest.get("message", ""),
                "allowed_execution_mode": latest.get("allowed_execution_mode"),
            }
            if latest
            else None
        ),
        "risk_decisions": [
            {"approved": bool(d["approved"]), "risk_score": d["risk_score"],
             "violations": _parse_json_list(d.get("violations", "[]")), "message": d.get("message", "")}
            for d in related
        ],
        "execution": {
            "order_count": len(related_orders),
            "fill_count": len(related_fills),
        },
    }


async def get_strategy_lifecycle(params: dict) -> dict:
    from aletheia.trading.strategy_lifecycle import (
        _allowed_targets,
        check_live_limited_gates,
        check_promotion_gates,
    )

    strategy_id = params.get("strategy_id")
    strategy, strategies = _resolve_strategy(strategy_id)

    if strategy is None:
        return {"error": "No strategies found."}

    allowed_targets = _allowed_targets(strategy["state"])
    under_review = _is_strategy_under_review(strategy)
    paper_trading_paused = _is_paper_strategy_paused(strategy)

    promotion_gates = None
    if strategy["state"] in {"paper_running", "paper_passed"}:
        promotion_gates = check_promotion_gates(strategy["id"])

    live_limited_gates = None
    if strategy["state"] in {"live_candidate", "live_limited"}:
        live_limited_gates = check_live_limited_gates(strategy["id"])

    gate_summary = ""
    if promotion_gates:
        gate_summary = (
            "promotion gates passed"
            if promotion_gates["passed"]
            else f"promotion gates blocked: {', '.join(promotion_gates['failures'])}"
        )
    elif live_limited_gates:
        gate_summary = (
            "live-limited gates passed"
            if live_limited_gates["passed"]
            else f"live-limited gates blocked: {', '.join(live_limited_gates['failures'])}"
        )
    else:
        gate_summary = "no gated promotion checks apply at the current state"

    operator_flags = []
    if under_review:
        operator_flags.append("under review")
    if paper_trading_paused:
        operator_flags.append("paper trading paused")
    operator_flag_summary = ""
    if operator_flags:
        operator_flag_summary = f" Operator flags: {', '.join(operator_flags)}."

    return {
        "message": (
            f"Strategy {strategy['name']} is in state {strategy['state']}. "
            f"Next allowed targets: {', '.join(allowed_targets) if allowed_targets else 'none'}. "
            f"{gate_summary}.{operator_flag_summary}"
        ),
        "strategy": {
            "id": strategy["id"],
            "name": strategy["name"],
            "state": strategy["state"],
            "description": strategy.get("description"),
            "under_review": under_review,
            "review_notes": strategy.get("review_notes"),
            "review_updated_at": strategy.get("review_updated_at"),
            "paper_trading_paused": paper_trading_paused,
            "paper_pause_reason": strategy.get("paper_pause_reason"),
            "paper_paused_at": strategy.get("paper_paused_at"),
            "updated_at": strategy.get("updated_at"),
        },
        "allowed_targets": allowed_targets,
        "promotion_gates": promotion_gates,
        "live_limited_gates": live_limited_gates,
        "strategy_count": len(strategies),
    }


async def get_failed_tasks(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import list_tasks

    failed_operator_tasks = list_tasks(limit=20, status="failed")
    recent_failed_runs = [
        run for run in list_agent_runs(limit=20)
        if run.get("status") in {"failed", "error"}
    ]
    operator_summaries = [
        (
            f"Operator task {task['task_type']} failed"
            if not task.get("error")
            else f"Operator task {task['task_type']} failed: {task['error']}"
        )
        for task in failed_operator_tasks
    ]
    run_summaries = []
    for run in recent_failed_runs:
        errors = _parse_json_list(run.get("errors"))
        detail = ", ".join(errors) if errors else "no error detail recorded"
        run_summaries.append(f"Agent run {run['agent_name']} failed: {detail}")

    message = (
        f"There are {_count_phrase(len(failed_operator_tasks), 'failed operator task')} and "
        f"{_count_phrase(len(recent_failed_runs), 'failed recent agent run')}."
    )
    extra = _join_summaries([*operator_summaries, *run_summaries], limit=4)
    if extra:
        message = f"{message} {extra}"
    return {
        "message": message,
        "failed_operator_tasks": [
            {
                "id": task["id"],
                "task_type": task["task_type"],
                "error": task.get("error"),
                "created_at": task.get("created_at"),
            }
            for task in failed_operator_tasks
        ],
        "failed_agent_runs": [
            {
                "id": run["id"],
                "agent": run["agent_name"],
                "status": run["status"],
                "errors": _parse_json_list(run.get("errors")),
                "started_at": run.get("started_at"),
            }
            for run in recent_failed_runs
        ],
    }


async def get_recommendations(params: dict) -> dict:
    from aletheia.agent_shell.operator_recommendations import build_recommendations

    recommendations = build_recommendations(limit=10)
    if not recommendations:
        message = "No backend recommendations right now."
    else:
        summary = _join_summaries(
            [item.get("summary", "") for item in recommendations],
            limit=4,
        )
        message = f"There are {_count_phrase(len(recommendations), 'active recommendation')} to review."
        if summary:
            message = f"{message} {summary}"
    return {
        "message": message,
        "recommendations": recommendations,
    }


async def pause_agents(params: dict) -> dict:
    cfg = config_loader.load_config()
    cfg.runtime.paused = True
    config_loader.save_config(cfg)
    try:
        from aletheia.agent_runtime.scheduler import pause_scheduler
        pause_scheduler()
    except Exception:
        pass
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "agent_shell", "source": "command_center",
                 "event_type": "gateway_paused", "message": "Agents paused via Agent Shell"})
    return {"paused": True, "message": "All scheduled agents have been paused."}


async def resume_agents(params: dict) -> dict:
    from aletheia.agent_runtime import resume_gate

    gate = await resume_gate.resume_if_llm_available(
        actor="agent_shell",
        source="command_center",
        cause="resume_agents_command",
    )
    if not gate["allowed"]:
        return {
            "error": f"Resume blocked: {resume_gate.summarize_resume_gate(gate)}",
            "resume_gate": gate,
        }
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "agent_shell", "source": "command_center",
                 "event_type": "gateway_resumed", "message": "Agents resumed via Agent Shell"})
    return {"paused": False, "message": "Scheduled agents have been resumed."}


async def create_research_note(params: dict) -> dict:
    title = params.get("title", "Untitled Research Note")
    content = params.get("content", "")
    if not content:
        return {"error": "Cannot create a research note without content."}

    from aletheia.knowledge.manager import create_research_note as _create
    item = _create(title=title, content=content)
    if item is None:
        return {"error": "Research note rejected by quality gate (content too short or placeholder)."}
    return {"created": True, "id": item.id, "title": title}


async def create_strategy_draft(params: dict) -> dict:
    from aletheia.knowledge.manager import create_strategy_memory
    from aletheia.trading.strategy_lifecycle import create_strategy

    title, content, parameters = _strategy_inputs(params, default_title="Operator Strategy Draft")
    strategy = create_strategy(name=title, description=content or None, parameters=parameters)
    memory = None
    if content:
        memory = create_strategy_memory(strategy.id, title, content)
    return {
        "created": True,
        "id": strategy.id,
        "title": title,
        "state": "draft",
        "message": f"Created strategy draft {title} in draft state.",
        "strategy_memory_id": memory.id if memory else None,
    }


async def create_dreaming_log(params: dict) -> dict:
    title = params.get("title", "Dreaming Log")
    content = params.get("content", "")
    if not content:
        return {"error": "Cannot create a dreaming log without content."}

    from aletheia.knowledge.manager import create_dreaming_log as _create

    item = _create(title=title, content=content)
    if item is None:
        return {"error": "Dreaming log rejected by quality gate (content too short or placeholder)."}
    return {"created": True, "id": item.id, "title": title}


async def mark_task_done(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import create_event, get_task, list_tasks, update_task

    task_id = params.get("task_id")
    task = get_task(task_id) if task_id else None
    if task is None:
        running = [item for item in list_tasks(limit=20) if item.get("status") == "running"]
        task = running[0] if running else None
    if task is None:
        return {"error": "No running operator task found to mark completed."}

    summary = str(params.get("summary") or "completed by operator").strip()
    output_data = task.get("output_data") if isinstance(task.get("output_data"), dict) else {}
    output_data = {**output_data, "summary": summary}
    update_task(task["id"], status="completed", output_data=output_data)
    create_event(
        event_type="task_completed",
        source="agent_shell_command",
        task_id=task["id"],
        related_entity_type="task",
        related_entity_id=task["id"],
        payload={"task_type": task["task_type"], "status": "completed", "summary": summary},
    )
    return {
        "completed": True,
        "task_id": task["id"],
        "message": f"Marked task {task['task_type']} completed.",
    }


async def create_diagnostic_todo(params: dict) -> dict:
    title = params.get("title", "Diagnostic Todo")
    content = params.get("content", title)

    from aletheia.agent_shell.file_artifacts import write_diagnostic_todo
    from aletheia.knowledge.manager import create_research_note as _create
    item = _create(title=f"[DIAG] {title}", content=content,
                   metadata={"type": "diagnostic_todo"})
    if item is None:
        return {"error": "Diagnostic todo rejected by quality gate (content too short or placeholder)."}
    todo_path = write_diagnostic_todo(
        todo_id=item.id,
        title=title,
        content=content,
        knowledge_item_id=item.id,
    )
    return {"created": True, "id": item.id, "title": title, "todo_path": str(todo_path)}


async def create_improvement_proposal(params: dict) -> dict:
    title = params.get("title", "Improvement Proposal")
    content = params.get("content", title)

    from aletheia.knowledge.manager import create_research_note as _create
    item = _create(title=f"[PROPOSAL] {title}", content=content,
                   metadata={"type": "improvement_proposal"})
    return {"created": True, "id": item.id, "title": title}


async def draft_extension_manifest(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import create_event, create_extension_record

    extension_name = str(
        params.get("extension_name") or params.get("name") or "Operator Extension"
    ).strip()
    slash_command = str(params.get("slash_command") or "").strip()
    description = str(params.get("description") or "").strip()
    target_command = str(params.get("target_command") or "").strip()
    default_parameters = params.get("default_parameters")
    default_parameters = default_parameters if isinstance(default_parameters, dict) else {}
    raw_capabilities = params.get("capabilities")
    if raw_capabilities in (None, "", []):
        capabilities: list[str] = []
    elif isinstance(raw_capabilities, list):
        capabilities = [_normalize_extension_capability(str(item)) for item in raw_capabilities if str(item).strip()]
    else:
        capabilities = [_normalize_extension_capability(str(raw_capabilities))]

    if not slash_command:
        return {"error": "Cannot draft an extension manifest without a slash command."}
    if not _slash_command_is_valid(slash_command):
        return {"error": "Slash command must start with '/' and use only letters, numbers, '.', '_' or '-'."}
    if not target_command:
        return {"error": "Cannot draft an extension manifest without a target command."}

    from aletheia.agent_shell.registry import get_command

    if get_command(target_command) is None:
        return {"error": f"Target command '{target_command}' is not registered."}

    manifest = {
        "extension_name": extension_name,
        "slash_command": slash_command,
        "description": description or f"Extension wrapper for {target_command}.",
        "target_command": target_command,
        "default_parameters": default_parameters,
        "capabilities": capabilities,
        "sandbox_only": True,
        "requires_human_approval": True,
    }
    session_id = _proposal_session_id()
    extension_id = create_extension_record(
        session_id=session_id,
        extension_name=extension_name,
        slash_command=slash_command,
        description=manifest["description"],
        target_command=target_command,
        manifest=manifest,
        created_by="operator",
    )
    create_event(
        event_type="extension_manifest_drafted",
        source="agent_shell_command",
        session_id=session_id,
        related_entity_type="extension",
        related_entity_id=extension_id,
        payload={
            "slash_command": slash_command,
            "target_command": target_command,
            "summary": f"Drafted extension manifest {slash_command}.",
        },
    )
    return {
        "created": True,
        "extension_id": extension_id,
        "status": "draft",
        "manifest": manifest,
        "message": f"Drafted extension manifest {slash_command}.",
    }


async def test_extension_sandbox(params: dict) -> dict:
    from aletheia.agent_shell.file_artifacts import write_scratch_note
    from aletheia.agent_shell.operator_store import (
        create_event,
        create_extension_run,
        get_extension,
        update_extension,
    )

    extension_id = str(params.get("extension_id") or "").strip()
    if not extension_id:
        return {"error": "Cannot run an extension sandbox test without an extension id."}

    extension = get_extension(extension_id)
    if extension is None:
        return {"error": f"Extension '{extension_id}' not found."}

    checks = _extension_sandbox_checks(extension.get("manifest") or {})
    passed = all(check["status"] == "passed" for check in checks)
    status = "passed" if passed else "failed"
    failing_messages = [check["message"] for check in checks if check["status"] != "passed"]
    summary = (
        "Sandbox validation passed."
        if passed
        else f"Sandbox validation failed: {' '.join(failing_messages)}"
    )
    report_body = "\n".join(
        [
            f"Extension: {extension['extension_name']}",
            f"Slash Command: {extension['slash_command']}",
            f"Target Command: {extension['target_command']}",
            "",
            "## Checks",
            *[
                f"- [{check['status'].upper()}] {check['check']}: {check['message']}"
                for check in checks
            ],
            "",
            "## Summary",
            summary,
        ]
    )
    scratch_path = write_scratch_note(
        note_id=extension_id,
        title=f"Extension Sandbox Test {extension['slash_command']}",
        body=report_body,
        metadata={
            "extension_id": extension_id,
            "slash_command": extension["slash_command"],
            "status": status,
        },
    )
    run_id = create_extension_run(
        extension_id=extension_id,
        session_id=str(extension.get("session_id") or _proposal_session_id()),
        run_type="sandbox_test",
        status=status,
        summary=summary,
        input_data={"manifest": extension.get("manifest") or {}},
        output_data={"checks": checks},
        error=None if passed else summary,
        scratch_path=str(scratch_path),
    )
    update_extension(
        extension_id,
        status="sandbox_passed" if passed else "sandbox_failed",
        latest_run_id=run_id,
    )
    create_event(
        event_type="extension_sandbox_tested",
        source="agent_shell_command",
        session_id=str(extension.get("session_id") or _proposal_session_id()),
        related_entity_type="extension",
        related_entity_id=extension_id,
        payload={
            "run_id": run_id,
            "status": status,
            "summary": summary,
            "scratch_path": str(scratch_path),
        },
    )
    return {
        "tested": True,
        "extension_id": extension_id,
        "run_id": run_id,
        "passed": passed,
        "status": status,
        "summary": summary,
        "checks": checks,
        "scratch_path": str(scratch_path),
    }


async def create_daily_review(params: dict) -> dict:
    title = params.get("title", "Daily Review")
    content = params.get("content", "")
    if not content:
        return {"error": "Cannot create a daily review without content."}

    from aletheia.knowledge.manager import create_daily_review as _create

    item = _create(title=title, content=content)
    if item is None:
        return {"error": "Daily review content did not pass quality gate (too short or placeholder)."}
    return {"created": True, "id": item.id, "title": title}


async def create_review_report(params: dict) -> dict:
    from aletheia.agent_shell.file_artifacts import write_operator_report

    report_type = str(params.get("report_type") or "").strip()
    spec = _report_spec(report_type, diagnostic=False)
    if spec is None:
        return {"error": f"Unsupported review report type: {report_type or 'missing'}."}

    title = str(params.get("title") or _default_report_title(report_type, diagnostic=False)).strip()
    content = str(params.get("content") or "").strip()
    if not content:
        return {"error": "Cannot create a review report without content."}

    summary = str(
        params.get("summary") or f"Created {report_type.replace('_', ' ')} report for operator review."
    ).strip()
    metadata = params.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    path = write_operator_report(
        report_dir=str(spec["report_dir"]),
        title=title,
        body=content,
        metadata={
            "report_type": report_type,
            **metadata,
        },
    )
    artifact_id = _create_report_artifact(
        artifact_type=str(spec["artifact_type"]),
        report_type=report_type,
        title=title,
        body=content,
        summary=summary,
        file_path=str(path),
        metadata=metadata,
    )
    return {
        "created": True,
        "artifact_id": artifact_id,
        "report_type": report_type,
        "title": title,
        "summary": summary,
        "path": str(path),
        "message": f"Created {report_type.replace('_', ' ')} report {title}.",
    }


async def create_diagnostic_report(params: dict) -> dict:
    from aletheia.agent_shell.file_artifacts import write_operator_report

    report_type = str(params.get("report_type") or "").strip()
    spec = _report_spec(report_type, diagnostic=True)
    if spec is None:
        return {"error": f"Unsupported diagnostic report type: {report_type or 'missing'}."}

    title = str(params.get("title") or _default_report_title(report_type, diagnostic=True)).strip()
    content = str(params.get("content") or "").strip()
    if not content:
        return {"error": "Cannot create a diagnostic report without content."}

    summary = str(
        params.get("summary") or f"Created {report_type.replace('_', ' ')} diagnostic report."
    ).strip()
    metadata = params.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    path = write_operator_report(
        report_dir=str(spec["report_dir"]),
        title=title,
        body=content,
        metadata={
            "report_type": report_type,
            **metadata,
        },
    )
    artifact_id = _create_report_artifact(
        artifact_type=str(spec["artifact_type"]),
        report_type=report_type,
        title=title,
        body=content,
        summary=summary,
        file_path=str(path),
        metadata=metadata,
    )
    return {
        "created": True,
        "artifact_id": artifact_id,
        "report_type": report_type,
        "title": title,
        "summary": summary,
        "path": str(path),
        "message": f"Created {report_type.replace('_', ' ')} diagnostic report {title}.",
    }


async def archive_low_value_notification(params: dict) -> dict:
    from aletheia.agent_shell.operator_recommendations import build_recommendations
    from aletheia.agent_shell.operator_store import create_artifact, create_event

    target_id = str(params.get("recommendation_id") or "").strip()
    recommendations = build_recommendations(limit=20)
    target = None
    if target_id:
        target = next((item for item in recommendations if item.get("id") == target_id), None)
    else:
        target = next((item for item in recommendations if item.get("severity") != "high"), None)

    if target is None:
        return {"error": "No archiveable recommendation found."}
    if target.get("severity") == "high":
        return {"error": "High-severity recommendations cannot be archived."}

    session_id = _proposal_session_id()
    content = {
        "recommendation_id": target["id"],
        "kind": target.get("kind"),
        "severity": target.get("severity"),
        "summary": target.get("summary"),
        "reason": str(params.get("reason") or "archived by operator").strip(),
    }
    artifact_id = create_artifact(
        task_id=None,
        session_id=session_id,
        artifact_type="notification_archive",
        title=f"Archived recommendation: {target['id']}",
        content=content,
    )
    create_event(
        event_type="notification_archived",
        source="agent_shell_command",
        session_id=session_id,
        related_entity_type="recommendation",
        related_entity_id=target["id"],
        payload={
            "artifact_id": artifact_id,
            "summary": target.get("summary"),
            "severity": target.get("severity"),
        },
    )
    return {
        "archived": True,
        "artifact_id": artifact_id,
        "recommendation_id": target["id"],
        "message": f"Archived recommendation {target['id']}.",
    }


async def mark_strategy_under_review(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import create_event

    strategy_id = params.get("strategy_id")
    strategy, _ = _resolve_strategy(strategy_id)
    if strategy is None:
        return {"error": "No strategies found."}

    review_notes = str(
        params.get("review_notes") or params.get("reason") or "operator review requested"
    ).strip()
    review_updated_at = datetime.now(timezone.utc).isoformat()
    update_strategy(
        strategy["id"],
        under_review=1,
        review_notes=review_notes,
        review_updated_at=review_updated_at,
        updated_at=review_updated_at,
    )
    create_event(
        event_type="strategy_under_review",
        source="agent_shell_command",
        session_id=_proposal_session_id(),
        related_entity_type="strategy",
        related_entity_id=strategy["id"],
        payload={
            "strategy_name": strategy["name"],
            "summary": f"{strategy['name']} marked under review.",
            "review_notes": review_notes,
        },
    )
    return {
        "updated": True,
        "strategy_id": strategy["id"],
        "message": f"Marked strategy {strategy['name']} under review.",
        "review_notes": review_notes,
    }


async def reconcile_positions(params: dict) -> dict:
    from aletheia.portfolio.position_reconciler import run_reconcile

    summary = run_reconcile()
    adopted_count = len(summary.get("adopted", []))
    closed_count = len(summary.get("closed_orphans", []))
    errors = summary.get("errors", [])

    parts = [f"sync_status={summary['sync_status']}"]
    parts.append(f"managed={summary['counts']['managed']}")
    parts.append(f"external={summary['counts']['external']}")
    parts.append(f"orphaned={summary['counts']['orphaned']}")
    if adopted_count:
        parts.append(f"adopted={adopted_count}")
    if closed_count:
        parts.append(f"closed_orphans={closed_count}")
    if errors:
        parts.append(f"errors={len(errors)}")

    return {
        "message": f"Position reconciliation complete: {', '.join(parts)}",
        "summary": summary,
    }


async def run_market_scan(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import create_event

    result = await run_agent("market_observer", trigger_type="manual")
    create_event(
        event_type="market_scan_requested",
        source="agent_shell_command",
        related_entity_type="agent_run",
        related_entity_id=result["run_id"],
        payload={"agent_name": "market_observer", "status": result["status"]},
    )
    message = (
        f"Triggered manual market scan. New run {result['run_id']} finished with status {result['status']}."
    )
    return {
        "message": message,
        "run": {
            "agent_name": "market_observer",
            "run_id": result["run_id"],
            "status": result["status"],
            "errors": result.get("errors", []),
            "outputs": result.get("outputs", []),
        },
    }


async def rerun_failed_agent_task(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import create_event

    failed_statuses = {"failed", "error", "completed_with_errors"}
    target_run = None
    run_id = params.get("run_id")
    agent_name = params.get("agent_name")
    recent_runs = list_agent_runs(limit=50)
    if run_id:
        target_run = next((run for run in recent_runs if run["id"] == run_id), None) or get_agent_run(run_id)
    elif agent_name:
        target_run = next(
            (
                run
                for run in recent_runs
                if run.get("agent_name") == agent_name and run.get("status") in failed_statuses
            ),
            None,
        )
    else:
        target_run = next((run for run in recent_runs if run.get("status") in failed_statuses), None)

    if target_run is None:
        return {"error": "No failed agent run found to rerun."}

    result = await run_agent(str(target_run["agent_name"]), trigger_type="manual")
    create_event(
        event_type="failed_task_rerun_requested",
        source="agent_shell_command",
        related_entity_type="agent_run",
        related_entity_id=result["run_id"],
        payload={
            "original_run_id": target_run["id"],
            "agent_name": target_run["agent_name"],
            "new_run_id": result["run_id"],
            "status": result["status"],
        },
    )
    return {
        "message": (
            f"Reran failed agent task for {target_run['agent_name']}. "
            f"Original run {target_run['id']}, new run {result['run_id']} is {result['status']}."
        ),
        "rerun": {
            "agent_name": target_run["agent_name"],
            "original_run_id": target_run["id"],
            "new_run_id": result["run_id"],
            "status": result["status"],
            "errors": result.get("errors", []),
            "outputs": result.get("outputs", []),
        },
    }


async def set_symbol_observe_only(params: dict) -> dict:
    from aletheia.data.symbols import InstrumentId, MARKET_SPOT, to_canonical
    from aletheia.files.jsonl import write_audit

    symbol = params.get("symbol")
    if not symbol:
        return {"error": "Cannot set observe-only without a symbol."}

    canonical = to_canonical(str(symbol))
    exchange_symbol = canonical.replace("/", "")
    cfg = config_loader.load_config()

    # In whitelist mode: remove from list. In blacklist mode: add to list.
    if cfg.risk.symbol_filter_mode == "blacklist":
        if exchange_symbol not in cfg.risk.symbol_list:
            cfg.risk.symbol_list = [*cfg.risk.symbol_list, exchange_symbol]
            cfg.exchange.symbol_list = list(cfg.risk.symbol_list)
    else:
        updated = [s for s in cfg.risk.symbol_list if s != exchange_symbol]
        if not updated:
            return {"error": "Cannot mark the last tradable symbol observe-only."}
        cfg.risk.symbol_list = updated
        cfg.exchange.symbol_list = updated

    observed_symbols = list(dict.fromkeys([*cfg.market_data.observed_symbols, canonical]))
    cfg.market_data.observed_symbols = observed_symbols

    if cfg.market_data.observed_instruments:
        observe_instrument = str(
            InstrumentId.from_parts(cfg.market_data.ccxt_exchange or "binance", canonical, MARKET_SPOT)
        )
        if observe_instrument not in cfg.market_data.observed_instruments:
            cfg.market_data.observed_instruments.append(observe_instrument)

    config_loader.save_config(cfg)
    write_audit(
        {
            "actor": "agent_shell",
            "source": "command_center",
            "event_type": "symbol_set_observe_only",
            "entity_type": "symbol",
            "entity_id": canonical,
            "message": f"{canonical} removed from tradable lists and kept under observation",
        }
    )
    return {
        "updated": True,
        "symbol": canonical,
        "message": f"{canonical} is now observe-only.",
        "symbol_list": list(cfg.risk.symbol_list),
        "observed_symbols": observed_symbols,
    }


async def create_paper_experiment(params: dict) -> dict:
    from aletheia.knowledge.manager import create_strategy_memory
    from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

    title, content, parameters = _strategy_inputs(params, default_title="Paper Experiment")
    strategy = create_strategy(name=title, description=content or None, parameters=parameters)
    memory = None
    if content:
        memory = create_strategy_memory(strategy.id, title, content)

    state = "draft"
    for target in ("research_note", "strategy_hypothesis", "paper_candidate"):
        result = promote_strategy(strategy.id, target)
        if not result.get("success"):
            return {"error": result.get("error", f"Failed to promote strategy to {target}.")}
        state = target

    return {
        "created": True,
        "id": strategy.id,
        "title": title,
        "state": state,
        "message": f"Created paper experiment {title} and advanced it to {state}.",
        "strategy_memory_id": memory.id if memory else None,
    }


async def pause_paper_strategy(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import create_event

    preferred_states = {"paper_candidate", "paper_running", "paper_passed"}
    strategy, _ = _resolve_strategy(params.get("strategy_id"), preferred_states=preferred_states)
    if strategy is None:
        return {"error": "No paper strategy found to pause."}

    reason = str(params.get("reason") or "paused by operator").strip()
    paused_at = datetime.now(timezone.utc).isoformat()
    update_strategy(
        strategy["id"],
        paper_trading_paused=1,
        paper_pause_reason=reason,
        paper_paused_at=paused_at,
        updated_at=paused_at,
    )

    reviewed_open_opportunities = 0
    for opportunity in list_opportunities(limit=200):
        if opportunity.get("linked_strategy_id") != strategy["id"]:
            continue
        if opportunity.get("status") != "open":
            continue
        update_opportunity(opportunity["id"], status="reviewed")
        reviewed_open_opportunities += 1

    summary = f"{strategy['name']} paused for paper trading."
    if reviewed_open_opportunities:
        summary += f" Reviewed {reviewed_open_opportunities} open linked opportunit(ies)."
    create_event(
        event_type="strategy_paused",
        source="agent_shell_command",
        session_id=_proposal_session_id(),
        related_entity_type="strategy",
        related_entity_id=strategy["id"],
        payload={
            "strategy_name": strategy["name"],
            "summary": summary,
            "reason": reason,
            "reviewed_open_opportunity_count": reviewed_open_opportunities,
        },
    )
    return {
        "paused": True,
        "strategy_id": strategy["id"],
        "message": summary,
        "reviewed_open_opportunity_count": reviewed_open_opportunities,
    }


async def propose_strategy_promotion(params: dict) -> dict:
    from aletheia.trading.strategy_lifecycle import (
        _allowed_targets,
        _live_lifecycle_gate,
        check_live_limited_gates,
        check_promotion_gates,
    )

    strategy_id = params.get("strategy_id")
    strategy, _ = _resolve_strategy(strategy_id)
    if strategy is None:
        return {"error": "No strategies found for proposal generation."}

    allowed_targets = _allowed_targets(strategy["state"])
    target_state = str(params.get("target_state") or (allowed_targets[0] if allowed_targets else "")).strip()
    if not target_state:
        return {"error": "Cannot create promotion proposal without a target state."}

    gate_preview: dict | None = None
    if target_state in {"paper_passed", "live_candidate"}:
        gate_preview = check_promotion_gates(strategy["id"])
    elif target_state == "live_limited":
        gate_preview = {
            "strategy_gate": check_live_limited_gates(strategy["id"]),
            "runtime_gate": _live_lifecycle_gate(),
        }

    rationale = str(params.get("rationale") or "").strip()
    content = {
        "strategy_id": strategy["id"],
        "strategy_name": strategy["name"],
        "current_state": strategy["state"],
        "target_state": target_state,
        "allowed_targets": allowed_targets,
        "gate_preview": gate_preview,
        "rationale": rationale,
        "requires_human_approval": True,
    }
    artifact_id = _create_proposal_artifact(
        artifact_type="strategy_promotion_proposal",
        title=f"Promote {strategy['name']} to {target_state}",
        content=content,
        related_entity_type="strategy",
        related_entity_id=strategy["id"],
    )
    return {
        "proposed": True,
        "artifact_id": artifact_id,
        "message": f"Created a promotion proposal for {strategy['name']} -> {target_state}.",
        "proposal": content,
    }


async def propose_prompt_change(params: dict) -> dict:
    target = str(params.get("target") or "").strip()
    content_text = str(params.get("content") or "").strip()
    rationale = str(params.get("rationale") or "").strip()
    if not target:
        return {"error": "Cannot create a prompt-change proposal without a target."}
    if not content_text and not rationale:
        return {"error": "Cannot create a prompt-change proposal without proposed content or rationale."}

    content = {
        "target": target,
        "proposed_change": content_text,
        "rationale": rationale,
        "requires_human_approval": True,
    }
    artifact_id = _create_proposal_artifact(
        artifact_type="prompt_change_proposal",
        title=f"Prompt change proposal: {target}",
        content=content,
        related_entity_type="prompt_surface",
        related_entity_id=target,
    )
    return {
        "proposed": True,
        "artifact_id": artifact_id,
        "message": f"Created a prompt-change proposal for {target}.",
        "proposal": content,
    }


async def propose_schedule_change(params: dict) -> dict:
    agent_name = str(params.get("agent_name") or "").strip()
    requested_schedule = str(params.get("requested_schedule") or params.get("schedule") or "").strip()
    rationale = str(params.get("rationale") or "").strip()
    if not agent_name:
        return {"error": "Cannot create a schedule-change proposal without an agent name."}
    if not requested_schedule:
        return {"error": "Cannot create a schedule-change proposal without a requested schedule."}

    content = {
        "agent_name": agent_name,
        "current_schedule": _default_schedule_for(agent_name),
        "requested_schedule": requested_schedule,
        "rationale": rationale,
        "requires_human_approval": True,
    }
    artifact_id = _create_proposal_artifact(
        artifact_type="schedule_change_proposal",
        title=f"Schedule change proposal: {agent_name}",
        content=content,
        related_entity_type="agent_schedule",
        related_entity_id=agent_name,
    )
    return {
        "proposed": True,
        "artifact_id": artifact_id,
        "message": f"Created a schedule-change proposal for {agent_name}.",
        "proposal": content,
    }


async def propose_live_limited_enablement(params: dict) -> dict:
    from aletheia.trading.strategy_lifecycle import _allowed_targets, _live_lifecycle_gate, check_live_limited_gates

    strategy_id = params.get("strategy_id")
    strategy, _ = _resolve_strategy(strategy_id)
    if strategy is None:
        return {"error": "No strategies found for live-limited enablement proposal."}

    gate_preview = {
        "allowed_targets": _allowed_targets(strategy["state"]),
        "strategy_gate": check_live_limited_gates(strategy["id"]),
        "runtime_gate": _live_lifecycle_gate(),
    }
    rationale = str(params.get("rationale") or "").strip()
    content = {
        "strategy_id": strategy["id"],
        "strategy_name": strategy["name"],
        "current_state": strategy["state"],
        "target_state": "live_limited",
        "gate_preview": gate_preview,
        "rationale": rationale,
        "requires_human_approval": True,
    }
    artifact_id = _create_proposal_artifact(
        artifact_type="live_limited_enablement_proposal",
        title=f"Live-limited enablement proposal: {strategy['name']}",
        content=content,
        related_entity_type="strategy",
        related_entity_id=strategy["id"],
    )
    return {
        "proposed": True,
        "artifact_id": artifact_id,
        "message": f"Created a live-limited enablement proposal for {strategy['name']}.",
        "proposal": content,
    }


async def propose_risk_boundary_change(params: dict) -> dict:
    changes = params.get("changes")
    changes = changes if isinstance(changes, dict) else {}
    rationale = str(params.get("rationale") or "").strip()
    if not changes:
        return {"error": "Cannot create a risk-boundary proposal without proposed changes."}

    content = {
        "changes": changes,
        "rationale": rationale,
        "current_risk_snapshot": config_loader.load_config().risk.model_dump(mode="json"),
        "requires_human_approval": True,
    }
    artifact_id = _create_proposal_artifact(
        artifact_type="risk_boundary_change_proposal",
        title="Risk boundary change proposal",
        content=content,
        related_entity_type="risk_config",
        related_entity_id="risk",
    )
    return {
        "proposed": True,
        "artifact_id": artifact_id,
        "message": "Created a risk-boundary change proposal.",
        "proposal": content,
    }


async def propose_command_center_behavior_change(params: dict) -> dict:
    target_command = str(params.get("target_command") or "").strip()
    requested_behavior = str(
        params.get("requested_behavior") or params.get("content") or ""
    ).strip()
    rationale = str(params.get("rationale") or "").strip()
    evidence = params.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}

    if not requested_behavior:
        return {
            "error": "Cannot create a Command Center behavior proposal without requested behavior details."
        }

    content = {
        "target_surface": "command_center",
        "target_command": target_command or None,
        "requested_behavior": requested_behavior,
        "rationale": rationale,
        "evidence": evidence,
        "requires_human_approval": True,
    }
    title_target = target_command or "general"
    artifact_id = _create_proposal_artifact(
        artifact_type="command_center_behavior_proposal",
        title=f"Command Center behavior proposal: {title_target}",
        content=content,
        related_entity_type="command_center",
        related_entity_id=target_command or "command_center",
    )
    return {
        "proposed": True,
        "artifact_id": artifact_id,
        "message": "Created a Command Center behavior proposal.",
        "proposal": content,
    }


async def propose_extension_enablement(params: dict) -> dict:
    from aletheia.agent_shell.operator_store import (
        get_extension,
        get_extension_run,
        list_extension_runs,
        update_extension,
    )

    extension_id = str(params.get("extension_id") or "").strip()
    if not extension_id:
        return {"error": "Cannot create an extension enablement proposal without an extension id."}

    extension = get_extension(extension_id)
    if extension is None:
        return {"error": f"Extension '{extension_id}' not found."}

    latest_run = None
    latest_run_id = extension.get("latest_run_id")
    if latest_run_id:
        latest_run = get_extension_run(str(latest_run_id))
    if latest_run is None:
        recent_runs = list_extension_runs(extension_id=extension_id, limit=1)
        latest_run = recent_runs[0] if recent_runs else None
    if latest_run is None or latest_run.get("status") != "passed":
        return {"error": "Extension enablement proposals require a passing sandbox test first."}

    rationale = str(params.get("rationale") or "").strip()
    content = {
        "extension_id": extension_id,
        "extension_name": extension.get("extension_name"),
        "slash_command": extension.get("slash_command"),
        "target_command": extension.get("target_command"),
        "manifest": extension.get("manifest") or {},
        "latest_sandbox_run_id": latest_run.get("id"),
        "latest_sandbox_status": latest_run.get("status"),
        "latest_sandbox_summary": latest_run.get("summary"),
        "rationale": rationale,
        "requires_human_approval": True,
    }
    artifact_id = _create_proposal_artifact(
        artifact_type="extension_enablement_proposal",
        title=f"Extension enablement proposal: {extension.get('slash_command')}",
        content=content,
        related_entity_type="extension",
        related_entity_id=extension_id,
    )
    update_extension(
        extension_id,
        status="proposed",
        proposal_artifact_id=artifact_id,
    )
    return {
        "proposed": True,
        "artifact_id": artifact_id,
        "message": f"Created an extension enablement proposal for {extension.get('slash_command')}.",
        "proposal": content,
    }


def _emit_self_heal_action(action: str, reason: str) -> None:
    """Record a system_action knowledge item for audit trail and self-heal."""
    try:
        from aletheia.storage.models import KnowledgeItem
        from aletheia.storage.repositories import insert_knowledge_item
        item = KnowledgeItem(
            item_type="system_action",
            title=f"self_heal:{action}",
            tags=json.dumps(["self_heal", action]),
        )
        insert_knowledge_item(item)
        logger.info("self_heal_action: %s — %s", action, reason)
    except Exception:
        pass


async def evaluate_trading_opportunity(params: dict) -> dict:
    """PI agent evaluates market state and submits a paper proposal if conviction >= 55."""
    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.agent_shell.trading_decision import (
        evaluate_trading_opportunity as _evaluate,
        submit_decision_as_proposal,
    )
    from aletheia.config.loader import load_config

    cfg = load_config()
    execution_mode = cfg.exchange.paper_execution_mode

    # Build three-layer enriched context
    ctx = build_enriched_trading_context()

    # Enrich with RuleSet signals (passed separately to prompt builder)
    try:
        from aletheia.trading.ruleset_scanner import scan_active_rulesets
        signals = scan_active_rulesets(execution_mode=execution_mode)
        ctx["ruleset_signals"] = [
            {
                "symbol": s.symbol,
                "signal_type": s.signal_type,
                "direction": s.direction,
                "strategy": s.strategy_name,
                "timeframe": s.timeframe,
            }
            for s in signals
        ]
    except Exception:
        ctx["ruleset_signals"] = []

    # Run PI agent decision loop
    decision = await _evaluate(ctx)

    result: dict = {
        "action": decision.action,
        "conviction": decision.conviction,
        "reasoning": decision.reasoning[:300],
        "debate_ran": decision.debate_ran,
        "debate_bull": decision.debate_bull_confidence,
        "debate_bear": decision.debate_bear_confidence,
        # R0/attribution: carry the source opportunity_id through to record_decision
        # so the pi_decisions(action='trade') row links back to its opportunity.
        "opportunity_id": decision.opportunity_id,
    }

    # trade / scale_in / flip all produce proposals
    if decision.action in ("trade", "scale_in", "flip") and decision.symbol:
        result["symbol"] = decision.symbol
        result["direction"] = decision.direction
        result["position_size_usdt"] = decision.position_size_usdt

        proposal_result = await submit_decision_as_proposal(decision, execution_mode)
        if proposal_result:
            result["proposal_id"] = proposal_result.get("proposal_id")
            result["proposal_status"] = proposal_result.get("status")
        else:
            result["proposal_error"] = "price_unavailable_or_proposal_rejected"

    # Record decision for next-cycle reflection loop
    try:
        from aletheia.agent_shell.reflection import record_decision
        record_decision(result)
    except Exception:
        pass

    # Self-heal: emit system_action when data environment is degraded
    try:
        data_health = ctx.get("layer1", {}).get("data_health", {})
        if data_health.get("signal_status") == "degraded":
            _emit_self_heal_action(
                "pause_low_conviction",
                reason=f"24h signal win_rate {data_health.get('signal_win_rate_24h', '?')}% below threshold",
            )
        elif not data_health.get("ohlcv_ready"):
            _emit_self_heal_action(
                "request_backfill",
                reason=f"only {data_health.get('members_with_full_coverage', 0)}/{data_health.get('total_members', 0)} members have full OHLCV coverage",
            )
    except Exception:
        pass

    return result


async def review_portfolio_state(params: dict) -> dict:
    """PI agent reviews open positions for overexposure and optionally closes / reduces."""
    import json as _json
    from aletheia.agent_shell.context import build_app_context
    from aletheia.agents.helpers import llm_structured
    from aletheia.portfolio.pnl import close_position, _latest_price

    ctx = build_app_context(sections=["market", "trading", "risk"])
    open_positions = ctx.get("trading", {}).get("open_positions", [])
    risk = ctx.get("risk", {})

    if not open_positions:
        return {"action": "none", "reason": "no_open_positions"}

    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["hold", "close_one", "reduce_exposure"]},
            "target_symbol": {"type": "string"},
            "reasoning": {"type": "string"},
        },
        "required": ["action", "reasoning"],
    }

    pos_summary = "\n".join(
        f"  {p['symbol']} {p['direction']} qty={p['quantity']} upnl={p.get('unrealized_pnl', '?')}"
        for p in open_positions
    )
    system = "You are a portfolio risk reviewer for an autonomous paper trading system."
    user = f"""Current open positions:
{pos_summary}

Risk limits:
  Max open positions (demo): {risk.get('max_open_positions_demo', '?')}
  Kill switch: {risk.get('kill_switch', False)}

Review the portfolio and decide:
- "hold": all positions look fine
- "close_one": one specific position should be closed (specify target_symbol)
- "reduce_exposure": overall exposure is too high (specify target_symbol to close)

Be conservative — only recommend closing if there is a clear risk reason.

Return JSON: {_json.dumps(schema)}"""

    class _Schema:
        @staticmethod
        def model_json_schema() -> dict:
            return schema

    review, err = await llm_structured(system, user, schema=_Schema())

    if err or review is None:
        return {"action": "none", "reason": err or "llm_unavailable"}

    action = review.get("action", "hold")
    result = {
        "action": action,
        "reasoning": review.get("reasoning", "")[:300],
        "target_symbol": review.get("target_symbol"),
    }

    if action in ("close_one", "reduce_exposure") and review.get("target_symbol"):
        sym = review["target_symbol"]
        matching = [p for p in list_positions(status="open") if p["symbol"] == sym]
        closed_count = 0
        for pos in matching[:1]:
            price = _latest_price(sym)
            if price:
                try:
                    # Route by execution_mode: demo/live positions MUST close via their
                    # exchange broker (real reduce + real exit price), not a local-only
                    # close that fabricates the exit and desyncs the exchange book.
                    from aletheia.portfolio.position_manager import close_position_routed
                    close_position_routed(
                        pos["id"], reason="pi_agent_portfolio_review",
                        fallback_exit_price=float(price),
                    )
                    closed_count += 1
                except Exception as e:
                    result["close_error"] = str(e)[:100]
        result["positions_closed"] = closed_count

    return result


HANDLERS: dict[str, callable] = {
    "get_system_status": get_system_status,
    "get_operator_status": get_operator_status,
    "get_agent_activity": get_agent_activity,
    "get_market_snapshot": get_market_snapshot,
    "get_positions": get_positions,
    "explain_agent_run": explain_agent_run,
    "explain_proposal": explain_proposal,
    "get_strategy_lifecycle": get_strategy_lifecycle,
    "get_failed_tasks": get_failed_tasks,
    "get_recommendations": get_recommendations,
    "pause_agents": pause_agents,
    "resume_agents": resume_agents,
    "create_research_note": create_research_note,
    "create_strategy_draft": create_strategy_draft,
    "create_dreaming_log": create_dreaming_log,
    "create_diagnostic_todo": create_diagnostic_todo,
    "mark_task_done": mark_task_done,
    "create_improvement_proposal": create_improvement_proposal,
    "draft_extension_manifest": draft_extension_manifest,
    "test_extension_sandbox": test_extension_sandbox,
    "create_daily_review": create_daily_review,
    "create_review_report": create_review_report,
    "create_diagnostic_report": create_diagnostic_report,
    "archive_low_value_notification": archive_low_value_notification,
    "mark_strategy_under_review": mark_strategy_under_review,
    "reconcile_positions": reconcile_positions,
    "run_market_scan": run_market_scan,
    "rerun_failed_agent_task": rerun_failed_agent_task,
    "set_symbol_observe_only": set_symbol_observe_only,
    "create_paper_experiment": create_paper_experiment,
    "pause_paper_strategy": pause_paper_strategy,
    "propose_strategy_promotion": propose_strategy_promotion,
    "propose_prompt_change": propose_prompt_change,
    "propose_schedule_change": propose_schedule_change,
    "propose_live_limited_enablement": propose_live_limited_enablement,
    "propose_risk_boundary_change": propose_risk_boundary_change,
    "propose_command_center_behavior_change": propose_command_center_behavior_change,
    "propose_extension_enablement": propose_extension_enablement,
    "evaluate_trading_opportunity": evaluate_trading_opportunity,
    "review_portfolio_state": review_portfolio_state,
}
