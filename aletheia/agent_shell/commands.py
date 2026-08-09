"""Command handlers for the Agent Shell control plane."""

import json
import hashlib
import logging
import math
import re
from datetime import datetime, timedelta, timezone

from aletheia.agent_runtime.orchestrator import run_agent
import aletheia.config.loader as config_loader
from aletheia.storage.repositories import (
    get_agent_run,
    get_trade_proposal,
    list_all_open_positions,
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
    list_tool_calls,
    update_opportunity,
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
    from aletheia.agent_runtime.pause_ownership import MANUAL_OWNER, acquire_pause

    acquire_pause(
        MANUAL_OWNER,
        reason="agent_shell_pause",
        actor="agent_shell",
        source="command_center",
    )
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
    # PCI-G07: reject docs/ prefix path — PI must not overwrite constitution docs via low-risk commands
    if str(params.get("path") or "").startswith("docs/") or str(title).startswith("docs/"):
        return {"error": "Path rejected: docs/ prefix is reserved for human-authored documents (PCI-G07)."}
    if not content:
        return {"error": "Cannot create a research note without content."}

    from aletheia.knowledge.manager import create_research_note as _create
    item = _create(title=title, content=content)
    if item is None:
        return {"error": "Research note rejected by quality gate (content too short or placeholder)."}
    return {"created": True, "id": item.id, "title": title}


async def create_dreaming_log(params: dict) -> dict:
    title = params.get("title", "Dreaming Log")
    content = params.get("content", "")
    # PCI-G07: reject docs/ prefix path — PI must not overwrite constitution docs via low-risk commands
    if str(params.get("path") or "").startswith("docs/") or str(title).startswith("docs/"):
        return {"error": "Path rejected: docs/ prefix is reserved for human-authored documents (PCI-G07)."}
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
    return {"created": True, "id": item.id if item else None, "title": title}


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
    # PCI-G07: reject docs/ prefix path — PI must not overwrite constitution docs via low-risk commands
    if str(params.get("path") or "").startswith("docs/") or str(title).startswith("docs/"):
        return {"error": "Path rejected: docs/ prefix is reserved for human-authored documents (PCI-G07)."}
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
async def propose_risk_boundary_change(params: dict) -> dict:
    changes = params.get("changes")
    changes = changes if isinstance(changes, dict) else {}
    rationale = str(params.get("rationale") or "").strip()
    if not changes:
        return {"error": "Cannot create a risk-boundary proposal without proposed changes."}

    risk_mode = str(params.get("risk_mode") or "").strip().lower()
    policy_id = str(params.get("policy_id") or "").strip()
    requested_policy_hash = str(params.get("policy_hash") or "").strip()
    policy_revision = params.get("policy_revision")
    if risk_mode not in {"demo", "live"}:
        return {"error": "Cannot create a risk-boundary proposal without a valid risk_mode."}
    if (
        not policy_id
        or not requested_policy_hash
        or isinstance(policy_revision, bool)
        or not isinstance(policy_revision, int)
    ):
        return {"error": "Cannot create a risk-boundary proposal without policy identity."}

    from aletheia.risk.policy import risk_policy_hash as compute_risk_policy_hash

    cfg = config_loader.load_config()
    current_policy = getattr(cfg.risk, risk_mode)
    current_identity = (
        current_policy.policy_id,
        current_policy.revision,
        compute_risk_policy_hash(current_policy),
    )
    requested_identity = (policy_id, policy_revision, requested_policy_hash)
    if requested_identity != current_identity:
        return {
            "error": (
                "Cannot create a risk-boundary proposal for a stale policy identity; "
                "refresh risk recommendations first."
            )
        }

    content = {
        "risk_mode": risk_mode,
        "policy_id": policy_id,
        "policy_revision": policy_revision,
        "policy_hash": requested_policy_hash,
        "changes": changes,
        "rationale": rationale,
        "current_risk_snapshot": cfg.risk.model_dump(mode="json"),
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


def _emit_self_heal_action(
    action: str,
    reason: str,
    operation_type: str = "",
    trigger_reason: str = "",
    result: str = "",
    before_state: str = "",
    after_state: str = "",
) -> None:
    """Record a system_action knowledge item for audit trail and self-heal.

    The 5 structured fields (operation_type / trigger_reason / result /
    before_state / after_state) are required by operator_layer.md §不变式2.
    They are stored as JSON in KnowledgeItem.content for structured querying.
    """
    try:
        from aletheia.storage.models import KnowledgeItem
        from aletheia.storage.repositories import insert_knowledge_item
        structured_content = json.dumps({
            "operation_type": operation_type or action,
            "trigger_reason": trigger_reason or reason,
            "result": result,
            "before_state": before_state,
            "after_state": after_state,
        })
        item = KnowledgeItem(
            item_type="system_action",
            title=f"self_heal:{action}",
            tags=json.dumps(["self_heal", action]),
            content=structured_content,
        )
        insert_knowledge_item(item)
        logger.info("self_heal_action: %s — %s", action, reason)
    except Exception:
        pass


def _pending_carrier_authority_failure(decision) -> str | None:
    """Return the fail-closed reason, or None for one executable queue identity."""
    if any(
        step.get("reason_code") == "pending_identity_ambiguous"
        for step in (getattr(decision, "filter_trace", None) or [])
    ):
        return "pending_identity_ambiguous"

    opportunity_id = getattr(decision, "opportunity_id", None)
    signal_source = getattr(decision, "signal_source", None) or "market_observer"
    if signal_source not in {"market_observer", "serenity"}:
        return "pending_source_invalid"
    if not opportunity_id:
        return "pending_signal_required"

    from aletheia.storage.repositories import (
        find_final_pi_decision_for_opportunity,
        find_pending_pi_decision_for_opportunity,
    )
    from aletheia.agent_shell.trading_decision import (
        _resolve_pending_carrier_identity,
    )

    pending = _resolve_pending_carrier_identity(
        find_pending_pi_decision_for_opportunity(opportunity_id, signal_source)
    )
    if pending is None:
        return "pending_signal_required"
    # A carrier is execution authority, so partial legacy identity cannot be
    # treated as a wildcard. Missing direction/market would otherwise let one
    # queued row authorize the opposite side or a different instrument class.
    for field in ("symbol", "direction", "market_type"):
        expected = getattr(decision, field, None)
        actual = pending.get(field)
        if not expected or not actual or expected != actual:
            return "pending_signal_required"

    if signal_source != "market_observer":
        return None

    # Queue freshness is not enough: the source Opportunity may have expired
    # after ranking. Validate its own lifecycle under the writer lock before a
    # new Proposal claim. An already-durable final claim is recovery authority
    # and is therefore resumed instead of being cancelled by later TTL/config.
    from aletheia.storage.repositories import (
        expire_queued_opportunity_with_ttl_evidence,
        get_opportunity,
        transaction,
    )

    existing = None
    source_valid_until = None
    with transaction(immediate=True):
        existing = find_final_pi_decision_for_opportunity(
            opportunity_id, signal_source
        )
        if existing is None:
            source = get_opportunity(opportunity_id)
            source_status = str((source or {}).get("status") or "")
            if source_status != "queued":
                return (
                    "pending_source_expired"
                    if source_status == "expired"
                    else "pending_source_not_queued"
                )
            source_valid_until = source.get("valid_until")
            if source_valid_until:
                try:
                    expires_at = datetime.fromisoformat(
                        str(source_valid_until).replace("Z", "+00:00")
                    )
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    return "pending_source_expiry_invalid"
                checked_at = datetime.now(timezone.utc)
                if expires_at <= checked_at:
                    expire_queued_opportunity_with_ttl_evidence(
                        opportunity_id,
                        evaluated_at=checked_at.isoformat(),
                        reason_detail=(
                            "source Opportunity valid_until elapsed before "
                            "System Operator Proposal preflight"
                        ),
                        parameters={
                            "expiry_basis": "valid_until",
                            "valid_until": str(source_valid_until),
                            "authority_check": "operator_preflight",
                        },
                    )
                    return "pending_source_expired"

    if existing is not None:
        return None
    if source_valid_until:
        # The source deadline is the canonical clock. The pending-row age below
        # exists only for historical Opportunities that have no valid_until.
        return None
    try:
        from aletheia.agent_shell.trading_context import _PENDING_SIGNAL_MAX_AGE_MINUTES

        created_at = datetime.fromisoformat(str(pending.get("created_at")).replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        fresh = created_at >= datetime.now(timezone.utc) - timedelta(
            minutes=_PENDING_SIGNAL_MAX_AGE_MINUTES
        )
    except (TypeError, ValueError):
        fresh = False
    if fresh:
        return None

    # Once a durable claim exists, queue age no longer decides whether its
    # already-owned submission may recover. This covers both historical orphan
    # claims and the atomic claim+Proposal commit followed by process death.
    return "pending_signal_required"


def _has_durable_pending_carrier(decision) -> bool:
    """Return whether a new-entry decision owns one exact durable queue carrier."""
    return _pending_carrier_authority_failure(decision) is None


def _configured_paper_execution_mode() -> str:
    return str(config_loader.load_config().exchange.paper_execution_mode)


def _proposal_status_from_execution_result(execution_result: dict) -> str | None:
    """Persist the Proposal's real state, not a caller-facing recovery action."""
    return execution_result.get("proposal_status") or execution_result.get("status")


def _record_no_evaluable_candidate_observation(
    queue: dict,
    data_health: dict,
    *,
    trigger: str,
    policy_epoch_id: str,
) -> dict:
    """Write a bounded, non-trading observation for an empty pending queue."""
    from aletheia.storage.models import OperatorEvaluationEvent
    from aletheia.storage.repositories import record_operator_evaluation_event

    now = datetime.now(timezone.utc)
    window_start = now.replace(
        minute=now.minute - (now.minute % 5), second=0, microsecond=0,
    ).isoformat()
    queue_snapshot = {
        "observation_window_start": window_start,
        "unresolved_total": int(queue.get("unresolved_total") or 0),
        "visible_count": int(queue.get("visible_count") or 0),
        "overflow_count": int(queue.get("overflow_count") or 0),
        "oldest_at": queue.get("oldest_at"),
        "expired_unconsumed_rows": int(queue.get("expired_unconsumed_rows") or 0),
    }
    # Aggregate health remains operational telemetry.  Store only its compact,
    # typed summary; no prompt, provider exception, or credential can enter this
    # event stream.
    health_summary = {
        "signal_status": data_health.get("signal_status"),
        "signal_win_rate_24h": data_health.get("signal_win_rate_24h"),
        "members_with_full_coverage": data_health.get("members_with_full_coverage"),
        "total_members": data_health.get("total_members"),
        "ohlcv_ready": data_health.get("ohlcv_ready"),
    }
    queue_json = json.dumps(queue_snapshot, sort_keys=True, separators=(",", ":"))
    dedup_payload = {
        "event_kind": "no_evaluable_candidate",
        "trigger": str(trigger or "manual"),
        "policy_epoch_id": policy_epoch_id,
        "queue_snapshot": queue_snapshot,
    }
    dedup_key = hashlib.sha256(
        json.dumps(dedup_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return record_operator_evaluation_event(OperatorEvaluationEvent(
        dedup_key=dedup_key,
        event_kind="no_evaluable_candidate",
        trigger=str(trigger or "manual"),
        pending_unresolved_total=queue_snapshot["unresolved_total"],
        pending_visible_count=queue_snapshot["visible_count"],
        pending_oldest_created_at=queue_snapshot["oldest_at"],
        queue_snapshot_json=queue_json,
        data_health_json=json.dumps(health_summary, sort_keys=True, separators=(",", ":")),
        policy_epoch_id=policy_epoch_id,
    ))


# Buffer added to defer_until when extending Opportunity.valid_until forward.
# The PI daemon now wakes from the durable defer deadline itself; these 300s are
# bounded evaluation/queue slack so the expiry sweep cannot consume the carrier
# while the promised second look is running.
_DEFER_VALID_UNTIL_MARGIN_SEC = 300

# Max low-conviction defers before a candidate is converted to a terminal skip.
# A candidate the model consistently rates below _MIN_CONVICTION_TO_TRADE (58)
# would otherwise be retried every wake cycle forever, burning LLM calls. After
# this many defers, the Opportunity is marked 'reviewed' (removed from the
# active queue) and valid_until is no longer extended.
_MAX_DEFERRALS_BEFORE_TERMINAL = 5

# Defer reasons that count toward the retry-limit threshold. Other defer reasons
# (e.g. missing data, transient unavailability) are not counting.
_RETRY_LIMITED_DEFER_REASONS = frozenset({
    "operator_low_conviction",
    "debate_not_confirmed",
})


def _deferred_context_dedup_key(
    context_fingerprint: str,
    *,
    opportunity_id: str,
    policy_epoch_id: str,
    window_minutes: int,
    event_kind: str = "operator_decision_deferred_unchanged_context",
) -> tuple[str, str]:
    """Return a stable key for one bounded defer suppression window.

    ``event_kind`` is part of the hash so that ``operator_decision_deferred_low_conviction``
    and ``operator_decision_deferred_unchanged_context`` cannot collide on the
    same (fingerprint, opportunity, epoch, window) tuple. Without this, the
    second event would be dropped by ``INSERT OR IGNORE`` on ``dedup_key``.
    """
    now = datetime.now(timezone.utc)
    window_seconds = max(int(window_minutes), 1) * 60
    window_epoch = int(now.timestamp()) // window_seconds * window_seconds
    window_start = datetime.fromtimestamp(window_epoch, timezone.utc).isoformat()
    payload = {
        "event_kind": event_kind,
        "context_fingerprint": context_fingerprint,
        "opportunity_id": opportunity_id,
        "policy_epoch_id": policy_epoch_id,
        "window_start": window_start,
    }
    return (
        hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        window_start,
    )


def _count_recent_defers_for_candidate(
    symbol: str,
    direction: str,
    market_type: str,
    *,
    window_hours: int = 48,
) -> int:
    """Count retry-limited defer events for a symbol+direction+market_type.

    Used by the retry-limit guard to decide whether a candidate has exhausted
    its defer budget and should be converted to a terminal skip.
    """
    from aletheia.storage.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    # Join events → opportunities on symbol/direction/market_type identity.
    # The queue_snapshot_json carries normalized symbol/direction/market_type.
    row = get_db().execute(
        """
        SELECT COUNT(*) FROM operator_evaluation_events e
        WHERE e.created_at > ?
          AND e.event_kind LIKE 'operator_decision_deferred_%'
          AND (
            json_extract(e.queue_snapshot_json, '$.defer_reason')
            IN ('operator_low_conviction', 'debate_not_confirmed')
          )
          AND json_extract(e.queue_snapshot_json, '$.symbol') = ?
          AND json_extract(e.queue_snapshot_json, '$.direction') = ?
          AND json_extract(e.queue_snapshot_json, '$.market_type') = ?
        """,
        (cutoff, symbol, direction, market_type),
    ).fetchone()
    return int(row[0]) if row else 0


def _existing_deferred_context_observation(
    context_fingerprint: str,
    *,
    opportunity_id: str,
    policy_epoch_id: str,
    window_minutes: int,
    symbol: str | None = None,
    direction: str | None = None,
    market_type: str | None = None,
    material_fingerprint: str | None = None,
    symbol_level: bool = False,
) -> dict | None:
    """Return the most recent defer event in this fingerprint window, any kind.

    Callers use this to skip re-invoking the model when the candidate has
    already been deferred inside the current 120-min material window. Since
    Phase C (2026-07-18) introduced ``operator_decision_deferred_low_conviction``
    alongside the legacy ``operator_decision_deferred_unchanged_context``, this
    query MUST match every ``operator_decision_deferred_*`` event_kind — otherwise
    a low-conviction defer fails to suppress the next LLM call, and the pending
    carrier gets re-evaluated repeatedly under the same unchanged context.

    Since the dedup_key is now event_kind-scoped (see ``_deferred_context_dedup_key``),
    every kind gets its own key and we query without filtering by dedup_key alone.
    We look up any defer event tied to this (fingerprint, opportunity, epoch) tuple
    inside the window by recomputing every plausible key.

    Market-quiet fast-path: for market-judgment defer reasons
    (``direct_skip_not_confirmed``, ``operator_low_conviction``,
    ``debate_not_confirmed``), the operator's decision does not improve when
    features refresh — the context_fingerprint changes with each feature snapshot
    but the underlying judgment is the same. We therefore bypass the fingerprint
    check and suppress re-evaluation by opportunity_id + defer_until alone.
    This prevents the tight re-evaluation loop that fires on every feature refresh.
    """
    from aletheia.storage.sqlite import get_db

    now_iso = datetime.now(timezone.utc).isoformat()
    # Market-quiet fast-path: for market-judgment defer reasons the operator's
    # decision does not improve when features refresh, so we suppress re-eval
    # without recomputing the (churny) context fingerprint.
    #
    # #321 symbol-level mode: universe refresh mints a fresh opportunity_id for the
    # same symbol every ~10 min, so opportunity_id matching misses the prior defer
    # and re-burns an LLM re-eval. When enabled AND the material fingerprint is
    # available, match by symbol+direction+market_type GUARDED by the material
    # fingerprint on EVERY branch — a fresh opportunity_id for an unchanged setup
    # is recognised and suppressed, while any real market move flips the
    # fingerprint, fails the guard, and reopens evaluation (DL-INV-23). Legacy
    # opportunity_id matching is retained when the flag is off or the fingerprint
    # is unavailable (fail open = evaluate).
    if symbol_level and material_fingerprint and symbol:
        from aletheia.agent_shell.trading_context import (
            _normalize_direction as _nd,
            _normalize_market_type as _nm,
        )

        market_quiet_row = get_db().execute(
            "SELECT * FROM operator_evaluation_events "
            "WHERE json_extract(queue_snapshot_json, '$.symbol') = ? "
            "AND json_extract(queue_snapshot_json, '$.direction') = ? "
            "AND json_extract(queue_snapshot_json, '$.market_type') = ? "
            "AND json_extract(queue_snapshot_json, '$.material_fingerprint') = ? "
            "AND event_kind LIKE 'operator_decision_deferred_%' "
            "AND ("
            "    (json_extract(queue_snapshot_json, '$.defer_until') > ? "
            "     AND json_extract(queue_snapshot_json, '$.defer_reason') IN "
            "             ('operator_low_conviction', 'debate_not_confirmed')) "
            "    OR json_extract(queue_snapshot_json, '$.defer_reason') LIKE "
            "             'direct_skip_not_confirmed%'"
            ") "
            "ORDER BY created_at DESC LIMIT 1",
            (symbol, _nd(direction), _nm(market_type), material_fingerprint, now_iso),
        ).fetchone()
    else:
        # Legacy per-opportunity match. direct_skip suppresses regardless of
        # defer_until; conviction/debate honor the defer_until window.
        market_quiet_row = get_db().execute(
            "SELECT * FROM operator_evaluation_events "
            "WHERE json_extract(queue_snapshot_json, '$.opportunity_id') = ? "
            "AND event_kind LIKE 'operator_decision_deferred_%' "
            "AND ("
            "    (json_extract(queue_snapshot_json, '$.defer_until') > ? "
            "     AND json_extract(queue_snapshot_json, '$.defer_reason') IN "
            "             ('operator_low_conviction', 'debate_not_confirmed')) "
            "    OR json_extract(queue_snapshot_json, '$.defer_reason') LIKE "
            "             'direct_skip_not_confirmed%'"
            ") "
            "ORDER BY created_at DESC LIMIT 1",
            (opportunity_id, now_iso),
        ).fetchone()
    if market_quiet_row is not None:
        return dict(market_quiet_row)

    known_kinds = (
        "operator_decision_deferred_unchanged_context",
        "operator_decision_deferred_low_conviction",
    )
    keys = []
    for kind in known_kinds:
        dk, _ = _deferred_context_dedup_key(
            context_fingerprint,
            opportunity_id=opportunity_id,
            policy_epoch_id=policy_epoch_id,
            window_minutes=window_minutes,
            event_kind=kind,
        )
        keys.append(dk)
    placeholders = ",".join("?" for _ in keys)
    row = get_db().execute(
        f"SELECT * FROM operator_evaluation_events "
        f"WHERE dedup_key IN ({placeholders}) "
        f"AND event_kind LIKE 'operator_decision_deferred_%' "
        "AND json_extract(queue_snapshot_json, '$.defer_until')>? "
        "ORDER BY created_at DESC LIMIT 1",
        (*keys, now_iso),
    ).fetchone()
    return dict(row) if row is not None else None


def _record_deferred_context_observation(
    ctx: dict,
    *,
    context_fingerprint: str,
    opportunity_id: str,
    policy_epoch_id: str,
    trigger: str,
    reason: str,
    window_minutes: int,
    event_kind: str = "operator_decision_deferred_unchanged_context",
) -> dict:
    """Persist a non-terminal defer fact without consuming a queue carrier.

    ``event_kind`` selects the observation type so diagnostic aggregations can
    distinguish the several defer causes (unchanged context, low conviction,
    debate-not-confirmed, missing candidate technical facts).

    Defer is the authoritative signal for "candidate remains worth another
    Operator look at ``defer_until``". This function therefore extends the
    underlying Opportunity's ``valid_until`` forward to at least
    ``defer_until + _DEFER_VALID_UNTIL_MARGIN_SEC`` so the promised re-evaluation
    is guaranteed inside the Opportunity's lifecycle. The event insert and the
    ``valid_until`` extension commit atomically.
    """
    from aletheia.storage.models import OperatorEvaluationEvent
    from aletheia.storage.repositories import (
        extend_opportunity_valid_until_forward_only,
        record_operator_evaluation_event,
        transaction,
    )

    queue = (ctx.get("layer2") or {}).get("pending_queue") or {}
    dedup_key, window_start = _deferred_context_dedup_key(
        context_fingerprint,
        opportunity_id=opportunity_id,
        policy_epoch_id=policy_epoch_id,
        window_minutes=window_minutes,
        event_kind=event_kind,
    )
    defer_deadline = (
        datetime.fromisoformat(window_start) + timedelta(minutes=max(window_minutes, 1))
    )
    defer_deadline_basis = "unchanged_context_window"
    selected = next(
        (
            row
            for row in (ctx.get("layer2") or {}).get("pending_signals") or []
            if str(row.get("opportunity_id") or "") == opportunity_id
        ),
        None,
    )
    defer_until = defer_deadline.isoformat()
    extended_valid_until = (
        defer_deadline + timedelta(seconds=_DEFER_VALID_UNTIL_MARGIN_SEC)
    ).isoformat()
    # #321: persist the symbol-level material fingerprint + normalized identity so
    # emit-source suppression and the symbol-level LLM fast-path can recognize
    # "same setup, market unchanged" across the fresh opportunity_id that universe
    # refresh mints each cycle. Normalized here so writer/reader match exactly.
    # Always written (data-harmless); consumers gate on skip_policy flags.
    from aletheia.agent_shell.trading_context import (
        material_fingerprint_from_candidate_ctx,
        _normalize_direction,
        _normalize_market_type,
    )
    material_fingerprint = material_fingerprint_from_candidate_ctx(ctx, opportunity_id)
    sel = selected or {}
    snapshot = {
        "context_fingerprint": context_fingerprint,
        "material_fingerprint": material_fingerprint,
        "symbol": sel.get("symbol"),
        "direction": _normalize_direction(
            sel.get("direction") or sel.get("direction_bias")
        ),
        "market_type": _normalize_market_type(
            sel.get("market_type") or sel.get("market")
        ),
        "opportunity_id": opportunity_id,
        "observation_window_start": window_start,
        "defer_until": defer_until,
        "defer_deadline_basis": defer_deadline_basis,
        "defer_reason": reason,
        "event_kind": event_kind,
        "unresolved_total": int(queue.get("unresolved_total") or 0),
        "visible_count": int(queue.get("visible_count") or 0),
        "overflow_count": int(queue.get("overflow_count") or 0),
        "oldest_at": queue.get("oldest_at"),
    }

    # Retry-limit guard: a candidate the model consistently rates below the
    # trade threshold would be deferred forever, burning LLM calls. After
    # _MAX_DEFERRALS_BEFORE_TERMINAL retry-limited defers, convert to a
    # terminal skip: mark the Opportunity 'reviewed' and stop extending
    # valid_until so it naturally expires out of the active queue.
    if reason in _RETRY_LIMITED_DEFER_REASONS:
        sel = selected or {}
        defer_count = _count_recent_defers_for_candidate(
            str(sel.get("symbol") or ""),
            str(_normalize_direction(sel.get("direction") or sel.get("direction_bias")) or ""),
            str(_normalize_market_type(sel.get("market_type") or sel.get("market")) or ""),
        )
        if defer_count >= _MAX_DEFERRALS_BEFORE_TERMINAL:
            logger.warning(
                "decision_retry_exhausted: %s %s %s defer_count=%d >= %d, terminal skip",
                sel.get("symbol"), sel.get("direction"), sel.get("market_type"),
                defer_count, _MAX_DEFERRALS_BEFORE_TERMINAL,
            )
            from aletheia.storage.repositories import transition_opportunity_to_reviewed
            transition_opportunity_to_reviewed(opportunity_id)
            # Record a terminal skip observation (not a defer).
            record_operator_evaluation_event(OperatorEvaluationEvent(
                dedup_key=f"retry_exhausted:{opportunity_id}:{defer_until}",
                event_kind="operator_skip_validation",
                trigger=str(trigger or "manual"),
                pending_unresolved_total=snapshot["unresolved_total"],
                pending_visible_count=snapshot["visible_count"],
                pending_oldest_created_at=snapshot["oldest_at"],
                queue_snapshot_json=json.dumps({
                    **snapshot,
                    "defer_reason": "retry_exhausted_terminal",
                    "defer_count": defer_count,
                }, sort_keys=True, separators=(",", ":")),
                data_health_json="{}",
                policy_epoch_id=policy_epoch_id,
            ))
            return {"status": "terminal_skip", "reason": "retry_exhausted", "defer_count": defer_count}

    # Extending valid_until FIRST inside the same transaction guarantees the
    # event insert observes the widened lifecycle. If either write fails the
    # whole defer record rolls back, avoiding a half-recorded state where the
    # Opportunity's valid_until moves but no defer event exists (or vice versa).
    with transaction(immediate=True):
        extend_rows_updated = extend_opportunity_valid_until_forward_only(
            opportunity_id, extended_valid_until
        )
        event_row = record_operator_evaluation_event(OperatorEvaluationEvent(
            dedup_key=dedup_key,
            event_kind=event_kind,
            trigger=str(trigger or "manual"),
            pending_unresolved_total=snapshot["unresolved_total"],
            pending_visible_count=snapshot["visible_count"],
            pending_oldest_created_at=snapshot["oldest_at"],
            queue_snapshot_json=json.dumps(
                snapshot, sort_keys=True, separators=(",", ":")
            ),
            data_health_json="{}",
            policy_epoch_id=policy_epoch_id,
        ))
    # GAP-4 defense-in-depth: if extend was a no-op AND defer_deadline still
    # exceeds the opportunity's valid_until, the Opportunity is likely terminal
    # (expired between context build and this write). Emit a WARN and a
    # filter_decision so the failure mode is auditable without being silent.
    if extend_rows_updated == 0:
        from aletheia.storage.sqlite import get_db as _get_db

        opp_row = _get_db().execute(
            "SELECT valid_until, status FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if opp_row is not None:
            opp_valid_until = opp_row["valid_until"] or ""
            if defer_deadline.isoformat() > opp_valid_until:
                from aletheia.storage.models import FilterDecision
                from aletheia.storage.repositories import insert_filter_decision as _ins_fd

                logger.warning(
                    "defer_exceeds_opportunity_ttl",
                    extra={
                        "opportunity_id": opportunity_id,
                        "defer_deadline": defer_deadline.isoformat(),
                        "opportunity_valid_until": opp_valid_until,
                        "opportunity_status": opp_row["status"],
                        "defer_reason": reason,
                        "event_kind": event_kind,
                    },
                )
                _ins_fd(FilterDecision(
                    dedup_key=f"defer_exceeds_ttl:{opportunity_id}:{defer_until}",
                    candidate_key=f"opportunity:{opportunity_id}",
                    candidate_kind="opportunity",
                    opportunity_id=opportunity_id,
                    symbol=str((selected or {}).get("symbol") or "unknown"),
                    market_type=str((selected or {}).get("market_type") or "spot"),
                    direction=(selected or {}).get("direction"),
                    filter_name="defer_ttl_guard",
                    stage="operator_defer",
                    decision="warning",
                    reason_code="defer_exceeds_opportunity_ttl",
                    reason_detail=json.dumps({
                        "defer_deadline": defer_deadline.isoformat(),
                        "opportunity_valid_until": opp_valid_until,
                        "opportunity_status": opp_row["status"],
                    }, sort_keys=True, separators=(",", ":")),
                    filter_version="defer_ttl_guard:v1",
                ))
    # Layer-1 observability: the defer path was silent until this log line.
    # Payload fields are read-only and every one already exists in `snapshot`
    # or `selected`; no new state is introduced. reason_class is derived by
    # aletheia.analytics.defer_classifier so the log, pipeline_progress
    # aggregation, and Blotter tooltip all share one taxonomy.
    try:
        from aletheia.analytics.defer_classifier import classify_defer_reason

        reason_class = classify_defer_reason(reason)
    except Exception:
        reason_class = "unknown"
    logger.info(
        "operator_defer",
        extra={
            "event_kind": event_kind,
            "defer_reason": reason,
            "reason_class": reason_class,
            "opportunity_id": opportunity_id,
            "symbol": (selected or {}).get("symbol"),
            "direction": (selected or {}).get("direction"),
            "defer_until": defer_until,
            "defer_deadline_basis": defer_deadline_basis,
            "policy_epoch_id": policy_epoch_id,
            "extended_valid_until": extended_valid_until,
        },
    )
    return event_row


def _record_skip_validated_observation(
    ctx: dict,
    *,
    opportunity_id: str,
    policy_epoch_id: str,
    decision,
    window_minutes: int = 30,
    trigger: str = "manual",
) -> dict | None:
    """Cache a terminal validated-adverse-skip as a symbol-level reuse record (#321).

    A later cycle that re-emits the SAME symbol+direction at an UNCHANGED material
    fingerprint can reuse this instead of burning the second (counterfactual) LLM
    call in ``_reassess_direct_skip`` (gated by ``reuse_recent_skip_validation``).

    Stores the full validated adverse judgment so the reuse path can reconstruct a
    ``confirmed_raw`` without re-invoking the model. No ``defer_until`` is set, so
    ``active_defer_opportunity_ids`` never counts it — it neither occupies an
    admission slot nor masquerades as an active defer (DL §2.3b). Best-effort:
    callers wrap in try/except so a cache-write failure never blocks the decision.
    """
    from aletheia.storage.models import OperatorEvaluationEvent
    from aletheia.storage.repositories import record_operator_evaluation_event
    from aletheia.agent_shell.trading_context import (
        material_fingerprint_from_candidate_ctx,
        _normalize_direction,
        _normalize_market_type,
    )

    material_fingerprint = material_fingerprint_from_candidate_ctx(ctx, opportunity_id)
    if not material_fingerprint:
        return None
    symbol = decision.symbol
    direction = _normalize_direction(decision.direction)
    market_type = _normalize_market_type(decision.market_type)
    now = datetime.now(timezone.utc)
    window_seconds = max(int(window_minutes), 1) * 60
    window_epoch = int(now.timestamp()) // window_seconds * window_seconds
    window_start = datetime.fromtimestamp(window_epoch, timezone.utc).isoformat()
    dedup_key = hashlib.sha256(
        json.dumps(
            {
                "event_kind": "operator_skip_validated",
                "symbol": symbol,
                "direction": direction,
                "market_type": market_type,
                "material_fingerprint": material_fingerprint,
                "policy_epoch_id": policy_epoch_id,
                "window_start": window_start,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    snapshot = {
        "event_kind": "operator_skip_validated",
        "material_fingerprint": material_fingerprint,
        "symbol": symbol,
        "direction": direction,
        "market_type": market_type,
        "opportunity_id": opportunity_id,
        "validated": True,
        "skip_kind": decision.skip_kind,
        "adverse_reason_codes": list(decision.adverse_reason_codes or []),
        "adverse_evidence": decision.adverse_evidence,
        "validated_adverse_claims": decision.validated_adverse_claims,
        "reasoning": decision.reasoning,
        "conviction": decision.conviction,
        "observation_window_start": window_start,
    }
    return record_operator_evaluation_event(OperatorEvaluationEvent(
        dedup_key=dedup_key,
        event_kind="operator_skip_validated",
        trigger=str(trigger or "manual"),
        pending_unresolved_total=0,
        pending_visible_count=0,
        pending_oldest_created_at=None,
        queue_snapshot_json=json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), default=str
        ),
        data_health_json="{}",
        policy_epoch_id=policy_epoch_id,
    ))


async def evaluate_trading_opportunity(params: dict) -> dict:
    """System Operator evaluates market state and submits a paper proposal if conviction >= 55."""
    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.agent_shell.trading_decision import (
        DecisionUnavailable,
        attach_candidate_entry_mark,
        evaluate_trading_opportunity as _evaluate,
        prepare_decision_as_proposal,
    )
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )

    information_policy_epoch_id = ensure_opportunity_information_policy_epoch()
    params = params or {}
    execution_mode = _configured_paper_execution_mode()

    # Build three-layer enriched context
    # Learning authority must be assigned only after the visible queue has been
    # scoped to one durable carrier. Authorizing the mixed queue creates an
    # unlinked exposure with no candidate outcome key.
    ctx = build_enriched_trading_context(authorize_learning=False)
    from aletheia.agent_shell.skip_policy import (
        ensure_skip_remediation_policy_epoch,
        skip_policy_context,
    )
    from aletheia.agent_shell.b2_evidence_policy import (
        attach_b2_candidate_evidence,
        ensure_b2_candidate_evidence_policy_epoch,
    )
    from aletheia.agent_shell.vergex_policy import ensure_vergex_utilization_policy_epoch

    policy_epoch_id = ensure_skip_remediation_policy_epoch()
    b2_policy_epoch_id = ensure_b2_candidate_evidence_policy_epoch()
    vergex_policy_epoch_id = ensure_vergex_utilization_policy_epoch()
    ctx["skip_policy_epoch_id"] = policy_epoch_id
    ctx["skip_policy"] = skip_policy_context()
    ctx["vergex_policy_epoch_id"] = vergex_policy_epoch_id
    ctx["information_policy_epoch_id"] = information_policy_epoch_id
    attach_b2_candidate_evidence(ctx, policy_epoch_id=b2_policy_epoch_id)

    # A zero-pending heartbeat is an operator observation, not a trade decision.
    # Only short-circuit an explicit queue summary: lightweight unit callers that
    # omit the queue retain their existing decision-path contract.
    queue = ctx.get("layer2", {}).get("pending_queue")
    if isinstance(queue, dict) and queue.get("unresolved_total") == 0:
        event = _record_no_evaluable_candidate_observation(
            queue,
            ctx.get("layer1", {}).get("data_health", {}),
            trigger=str(params.get("_trigger") or "manual"),
            policy_epoch_id=policy_epoch_id,
        )
        return {
            "action": "no_evaluable_candidate",
            "observation_status": "observed",
            "operator_evaluation_event_id": event["id"],
            "policy_epoch_id": policy_epoch_id,
            "b2_evidence_policy_epoch_id": b2_policy_epoch_id,
            "information_policy_epoch_id": information_policy_epoch_id,
            "pending_queue_remaining": 0,
            "pending_queue": queue,
        }

    # Wake context (exit-intent plan M1.5): when the daemon woke us for specific
    # positions, carry it before fingerprinting so a distinct wake cannot be
    # hidden behind an earlier entry-candidate defer observation.
    if isinstance(ctx, dict) and isinstance(params, dict) and params.get("wake_batches"):
        ctx["wake_context"] = params

    from aletheia.agent_shell.trading_context import (
        operator_decision_context_fingerprint,
        material_fingerprint_from_candidate_ctx,
        scope_operator_context_to_candidate,
    )

    defer_window_minutes = int(
        ctx["skip_policy"].get("unchanged_context_defer_window_minutes") or 120
    )
    symbol_level_suppression = bool(
        ctx["skip_policy"].get("symbol_level_defer_suppression")
    )
    selected_opportunity_id = None
    context_fingerprint = None
    selected_candidate: dict | None = None
    deferred_candidates: list[dict] = []
    for candidate in (ctx.get("layer2") or {}).get("pending_signals") or []:
        opportunity_id = str(candidate.get("opportunity_id") or "")
        if not opportunity_id:
            continue
        candidate_ctx = scope_operator_context_to_candidate(ctx, opportunity_id)
        # A skip remains valid when exact entry pricing is unavailable. Bind the
        # mark for model planning now, then let the trade-only validation seam
        # create a typed defer if the model nevertheless asks to enter.
        attach_candidate_entry_mark(candidate_ctx, execution_mode)
        candidate_fingerprint = operator_decision_context_fingerprint(candidate_ctx)
        candidate_material_fp = material_fingerprint_from_candidate_ctx(
            candidate_ctx, opportunity_id
        )
        existing_defer = _existing_deferred_context_observation(
            candidate_fingerprint,
            opportunity_id=opportunity_id,
            policy_epoch_id=policy_epoch_id,
            window_minutes=defer_window_minutes,
            symbol=candidate.get("symbol"),
            direction=candidate.get("direction") or candidate.get("direction_bias"),
            market_type=candidate.get("market_type") or candidate.get("market"),
            material_fingerprint=candidate_material_fp,
            symbol_level=symbol_level_suppression,
        )
        if existing_defer is None:
            ctx = candidate_ctx
            selected_opportunity_id = opportunity_id
            context_fingerprint = candidate_fingerprint
            selected_candidate = candidate
            break
        deferred_candidates.append({
            "opportunity_id": opportunity_id,
            "context_fingerprint": candidate_fingerprint,
            "event": existing_defer,
        })

    if selected_opportunity_id is None and deferred_candidates:
        existing_defer = deferred_candidates[0]["event"]
        return {
            "action": "evaluation_deferred",
            "observation_status": "all_visible_candidates_unchanged",
            "operator_evaluation_event_id": existing_defer["id"],
            "policy_epoch_id": policy_epoch_id,
            "b2_evidence_policy_epoch_id": b2_policy_epoch_id,
            "context_fingerprint": deferred_candidates[0]["context_fingerprint"],
            "model_invoked": False,
            "deferred_opportunity_ids": [row["opportunity_id"] for row in deferred_candidates],
            # All visible candidates are within their defer window — nothing actionable.
            # Return 0 so pi_daemon does not immediately spin.  Its durable deadline
            # query wakes at defer_until; a genuinely new signal may wake it sooner.
            "pending_queue_remaining": 0,
            "pending_queue": queue,
        }

    # Missing exact candidate technical facts are retryable data unavailability,
    # not a reason to ask the model for a judgment over '?' values. The material
    # fingerprint includes the exact feature snapshot id, so a later feature
    # publication reopens evaluation immediately without consuming the carrier.
    from aletheia.agent_shell.decision_facts import candidate_context_unavailable_reason

    unavailable_reason = (
        candidate_context_unavailable_reason(
            ctx, opportunity_id=selected_opportunity_id,
        )
        if selected_opportunity_id else None
    )
    if unavailable_reason:
        event = _record_deferred_context_observation(
            ctx,
            context_fingerprint=context_fingerprint,
            opportunity_id=selected_opportunity_id,
            policy_epoch_id=policy_epoch_id,
            trigger=str(params.get("_trigger") or "manual"),
            reason=unavailable_reason,
            window_minutes=defer_window_minutes,
        )
        return {
            "action": "evaluation_deferred",
            "observation_status": "candidate_context_unavailable",
            "operator_evaluation_event_id": event["id"],
            "policy_epoch_id": policy_epoch_id,
            "b2_evidence_policy_epoch_id": b2_policy_epoch_id,
            "context_fingerprint": context_fingerprint,
            "opportunity_id": selected_opportunity_id,
            "model_invoked": False,
            "defer_reason": unavailable_reason,
            "pending_queue_remaining": int((queue or {}).get("unresolved_total") or 0),
            "pending_queue": queue,
        }

    from aletheia.learning.prompt_bundles import authorize_operator_learning_context

    ctx = authorize_operator_learning_context(ctx)
    if context_fingerprint is not None:
        context_fingerprint = operator_decision_context_fingerprint(ctx)

    # Run System Operator decision loop.
    # ``DecisionUnavailable`` from selected stages is a non-terminal defer, not a
    # skip: the pending carrier stays queued and the same candidate is
    # re-evaluated on the next material_change wake. Two additional stages are
    # recognised here (Phase C, DL-S47/DL-S48):
    #   * ``operator_conviction_gate`` — initial conviction<55 without debate
    #   * ``operator_debate_gate``    — conviction<55 after bull/bear debate
    _DEFERRED_STAGES = {
        "operator_skip_validation",
        "operator_output_validation",
        "operator_conviction_gate",
        "operator_debate_gate",
        "operator_entry_plan",
    }
    _STAGE_TO_OBSERVATION_STATUS = {
        "operator_skip_validation": "invalid_or_neutral_direct_skip",
        "operator_output_validation": "operator_output_unavailable",
        "operator_conviction_gate": "operator_low_conviction",
        "operator_debate_gate": "debate_not_confirmed",
        "operator_entry_plan": "operator_entry_plan_unavailable",
    }
    _STAGE_TO_EVENT_KIND = {
        "operator_skip_validation": "operator_decision_deferred_unchanged_context",
        "operator_output_validation": "operator_decision_deferred_unchanged_context",
        "operator_conviction_gate": "operator_decision_deferred_low_conviction",
        "operator_debate_gate": "operator_decision_deferred_low_conviction",
        "operator_entry_plan": "operator_decision_deferred_unchanged_context",
    }

    def _deferred_evaluation_result(exc: DecisionUnavailable) -> dict:
        event = _record_deferred_context_observation(
            ctx,
            context_fingerprint=context_fingerprint,
            opportunity_id=selected_opportunity_id,
            policy_epoch_id=policy_epoch_id,
            trigger=str(params.get("_trigger") or "manual"),
            reason=exc.reason,
            window_minutes=defer_window_minutes,
            event_kind=_STAGE_TO_EVENT_KIND[exc.stage],
        )
        # DL-INV-7: filter facts must be persisted at the moment they happen.
        if exc.filter_trace and selected_candidate:
            try:
                from aletheia.analytics.filter_evidence import (
                    record_candidate_filter_trace,
                )

                record_candidate_filter_trace(
                    {
                        "opportunity_id": selected_opportunity_id,
                        "symbol": selected_candidate.get("symbol"),
                        "market_type": selected_candidate.get("market_type"),
                        "direction": selected_candidate.get("direction"),
                        "universe_snapshot_id": selected_candidate.get(
                            "universe_snapshot_id"
                        ),
                        "universe_member_id": selected_candidate.get(
                            "universe_member_id"
                        ),
                        "scanner_family": selected_candidate.get("scanner_family"),
                        "filter_trace": exc.filter_trace,
                    },
                    raise_on_error=False,
                )
            except Exception:
                logger.warning(
                    "defer filter_trace persistence failed for %s",
                    selected_opportunity_id,
                    exc_info=True,
                )
        return {
            "action": "evaluation_deferred",
            "observation_status": _STAGE_TO_OBSERVATION_STATUS[exc.stage],
            "operator_evaluation_event_id": event["id"],
            "policy_epoch_id": policy_epoch_id,
            "b2_evidence_policy_epoch_id": b2_policy_epoch_id,
            "context_fingerprint": context_fingerprint,
            "opportunity_id": selected_opportunity_id,
            "model_invoked": True,
            "defer_reason": exc.reason,
            "defer_stage": exc.stage,
            "pending_queue_remaining": int((queue or {}).get("unresolved_total") or 0),
            "pending_queue": queue,
        }

    try:
        # The daemon binds the evaluation identity before this command selects a
        # carrier. Add only the exact durable entities now known at the LLM
        # boundary. Resolve the pending row by its selected source identity
        # outside the model-visible context so telemetry cannot change the prompt.
        # A final PiDecision and Proposal do not exist yet and must not be
        # pre-created or inferred for telemetry.
        from aletheia.agent_runtime.run_context import (
            bind_economic_attribution,
            current_economic_attribution,
        )
        from aletheia.storage.repositories import (
            list_pending_pi_decisions_for_opportunity,
        )

        pending_decision_id = None
        if selected_opportunity_id:
            try:
                pending_decisions = list_pending_pi_decisions_for_opportunity(
                    selected_opportunity_id,
                    str(
                        (selected_candidate or {}).get("signal_source")
                        or "market_observer"
                    ),
                )
            except Exception:
                # Attribution is observation-only. A read failure must not block
                # the business decision or retain database exception text.
                logger.warning("operator inference attribution lookup unavailable")
            else:
                # Duplicate carriers make the decision identity ambiguous. Keep
                # it unavailable instead of choosing one by row order.
                if len(pending_decisions) == 1:
                    pending_decision_id = pending_decisions[0].get("id")
        causal_attribution = current_economic_attribution()
        causal_attribution.update({
            "opportunity_id": selected_opportunity_id,
            "decision_id": pending_decision_id,
        })
        with bind_economic_attribution(**causal_attribution):
            decision = await _evaluate(ctx)
    except DecisionUnavailable as exc:
        if exc.stage not in _DEFERRED_STAGES:
            raise
        return _deferred_evaluation_result(exc)

    trade_like = decision.action == "trade"
    rejected_missing_pending_carrier = False
    pending_authority_failure = (
        None
        if trade_like and _has_durable_pending_carrier(decision)
        else (_pending_carrier_authority_failure(decision) if trade_like else None)
    )
    if trade_like and pending_authority_failure:
        original_action = decision.action
        decision.filter_trace.append({
            "filter_name": "pending_signal_authority",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": pending_authority_failure,
            "inputs": {
                "action": original_action,
                "opportunity_id": decision.opportunity_id,
                "signal_source": decision.signal_source,
            },
            "parameters": {"required_carrier": "pi_decisions.action=signal_pending"},
        })
        decision.action = "skip"
        decision.reasoning = (
            f"{decision.reasoning} [{pending_authority_failure}; no proposal created]"
        )
        trade_like = False
        rejected_missing_pending_carrier = True

    def _attach_pending_queue_state(payload: dict) -> None:
        try:
            from aletheia.agent_shell.trading_context import _build_pending_signal_queue

            _, queue = _build_pending_signal_queue(limit=0)
            payload["pending_queue_remaining"] = queue["unresolved_total"]
            payload["pending_queue"] = queue
        except Exception:
            payload["pending_queue_remaining"] = None

    result: dict = {
        "action": decision.action,
        # Identity belongs to every evaluated terminal decision, not only trades.
        # A skip with a source opportunity must remain explainable later.
        "symbol": decision.symbol,
        "direction": decision.direction,
        "conviction": decision.conviction,
        # PiDecision.reasoning is TEXT and is the durable audit record. Display
        # surfaces may truncate independently; persistence must not.
        "reasoning": decision.reasoning,
        "debate_ran": decision.debate_ran,
        "debate_initial_conviction": decision.debate_initial_conviction,
        "debate_bull": decision.debate_bull_confidence,
        "debate_bear": decision.debate_bear_confidence,
        "debate_adjustment": decision.debate_adjustment,
        # R0/attribution: carry the source opportunity_id through to record_decision
        # so the pi_decisions(action='trade') row links back to its opportunity.
        "opportunity_id": decision.opportunity_id,
        # Phase 2 (Serenity Option A): which table opportunity_id resolves against
        # ('market_observer' → opportunities; 'serenity' → serenity_opportunities).
        "signal_source": decision.signal_source,
        "bear_case": decision.bear_case,
        "key_assumptions": decision.key_assumptions,
        "supporting_signals": decision.supporting_signals,
        "opposing_signals": decision.opposing_signals,
        "risk_factors": decision.risk_factors,
        "skip_kind": decision.skip_kind,
        "adverse_reason_codes": decision.adverse_reason_codes,
        "adverse_evidence": decision.adverse_evidence,
        "validated_adverse_claims": decision.validated_adverse_claims,
        "market_type": decision.market_type,
        "scanner_family": decision.scanner_family,
        "position_size_usdt": decision.position_size_usdt,
        "proposal_status": "not_applicable",
        "proposal_reason": "action_not_trade",
        "filter_trace": decision.filter_trace,
        "b2_evidence_policy_epoch_id": b2_policy_epoch_id,
        # One bundle-level attribution unit for all overlapping Learning prompt channels.
        "learning_manifest": ctx.get("learning_manifest") or {},
    }
    from aletheia.agent_shell.trading_context import build_decision_evidence_manifest
    result["evidence_manifest"] = build_decision_evidence_manifest(ctx, decision)

    if trade_like:
        result["proposal_status"] = "submitting"
        result["proposal_reason"] = None

    # A trade-like model choice without execution authority is an evaluation
    # failure, not a terminal System Operator decision. Persisting it through
    # claim_decision would create an anonymous final (or consume an unqueued
    # Opportunity) and make the real pending queue look abandoned.
    if rejected_missing_pending_carrier:
        result["proposal_reason"] = pending_authority_failure
        result["operator_decision_persisted"] = False
        existing_final = None
        if decision.opportunity_id:
            from aletheia.storage.repositories import (
                find_final_pi_decision_for_opportunity,
            )

            existing_final = find_final_pi_decision_for_opportunity(
                decision.opportunity_id,
                decision.signal_source or "market_observer",
            )
        if existing_final:
            result.update({
                "action": existing_final.get("action"),
                "symbol": existing_final.get("symbol"),
                "direction": existing_final.get("direction"),
                "market_type": existing_final.get("market_type"),
                "conviction": existing_final.get("conviction"),
                "reasoning": existing_final.get("reasoning") or "",
                "proposal_id": existing_final.get("proposal_id"),
                "proposal_status": existing_final.get("proposal_status"),
                "proposal_reason": existing_final.get("proposal_reason"),
                "opportunity_id": existing_final.get("opportunity_id"),
                "signal_source": (
                    existing_final.get("signal_source") or "market_observer"
                ),
                "idempotent_replay": True,
                "operator_decision_persisted": True,
            })
        _attach_pending_queue_state(result)
        return result

    prepared_proposal = None
    if trade_like:
        try:
            prepared_result = await prepare_decision_as_proposal(decision, execution_mode)
        except DecisionUnavailable as exc:
            # Assessment/preflight unavailability is not a business decision.
            # Leave the exact queue carrier untouched so the daemon can retry.
            if exc.stage in _DEFERRED_STAGES:
                return _deferred_evaluation_result(exc)
            raise
        except Exception as exc:
            decision.filter_trace.append({
                "filter_name": "proposal_submission",
                "stage": "proposal_submission",
                "decision": "error",
                "reason_code": "proposal_preparation_failed",
                "inputs": {"action": decision.action},
                "parameters": {},
            })
            raise DecisionUnavailable(
                "proposal_preparation_failed", stage="proposal_preflight"
            ) from exc
        prepared_proposal = prepared_result.get("proposal") if prepared_result else None
        if prepared_proposal is not None:
            # Preparation resolves the executable instrument (including legacy
            # missing market_type).  Persist the exact same identity on the
            # PiDecision that will own this Proposal.
            decision.market_type = prepared_proposal.market_type
            result["market_type"] = prepared_proposal.market_type
            result["proposal_id"] = prepared_proposal.id
            result["proposal_status"] = "submitting"
            result["proposal_reason"] = None
        elif prepared_result and prepared_result.get("status") == "skipped":
            # A deterministic direction-quality rejection is a real terminal
            # business decision. Persist it honestly as skip, not as a trade
            # claim with proposal_id=NULL.
            decision.action = "skip"
            trade_like = False
            result["action"] = "skip"
            result["proposal_id"] = None
            result["proposal_status"] = "not_applicable"
            result["proposal_reason"] = prepared_result.get("reason")
            result["filter_trace"] = decision.filter_trace
        else:
            reason = (
                prepared_result.get("reason")
                if isinstance(prepared_result, dict)
                else "proposal_preparation_unavailable"
            )
            raise DecisionUnavailable(
                reason or "proposal_preparation_unavailable",
                stage="proposal_preflight",
            )
        if trade_like and result.get("proposal_reason"):
            result["proposal_error"] = result["proposal_reason"]

    # The prepared Proposal and final PiDecision become visible together in one
    # BEGIN IMMEDIATE transaction.  Risk/broker work starts only after commit.
    from aletheia.agent_shell.reflection import (
        PendingSourceClaimError,
        claim_decision,
        finalize_decision,
        recover_interrupted_decision,
    )

    # adjust_position: the Operator revises an open position's ExitPlan (stop / target /
    # horizon) WITHOUT closing it — the M3 pivot. _attach_position_context already
    # linked the decision's symbol/direction to the judged position.
    if decision.action == "adjust_position":
        from aletheia.agent_shell.trading_decision import _match_position_for_decision
        from aletheia.portfolio.position_manager import apply_operator_adjustment

        positions = (ctx.get("layer1") or {}).get("positions") or []
        match = _match_position_for_decision(decision, positions)
        if match and match.get("id"):
            result["adjust"] = apply_operator_adjustment(
                match["id"],
                new_stop=decision.stop_price,
                new_target=decision.take_profit,
                new_horizon=decision.expected_horizon_hours,
                reason=decision.reasoning,
            )
        else:
            result["adjust_error"] = "no_target_position"

    # close: the Operator proactively exits a position ahead of its passive stop/
    # target — market now, or a resting limit at a better price (M3.5, D10).
    if decision.action == "close":
        from aletheia.agent_shell.trading_decision import _match_position_for_decision
        from aletheia.portfolio.position_manager import proactive_exit

        positions = (ctx.get("layer1") or {}).get("positions") or []
        match = _match_position_for_decision(decision, positions)
        if match and match.get("id"):
            result["exit"] = proactive_exit(
                match["id"],
                order_type=decision.order_type or "market",
                limit_price=decision.limit_price,
                reason=decision.reasoning,
            )
        else:
            result["exit_error"] = "no_target_position"

    # Record decision for next-cycle reflection loop
    try:
        claimed_final, claim_created = claim_decision(
            result, proposal=prepared_proposal
        )
    except PendingSourceClaimError as exc:
        decision.filter_trace.append({
            "filter_name": "pending_source_claim",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": exc.reason,
            "inputs": {
                "opportunity_id": decision.opportunity_id,
                "signal_source": decision.signal_source,
            },
            "parameters": {"required_status": "queued"},
        })
        result.update({
            "action": "skip",
            "proposal_id": None,
            "proposal_status": "rejected",
            "proposal_reason": exc.reason,
            "proposal_error": exc.reason,
            "operator_decision_persisted": False,
            "filter_trace": decision.filter_trace,
        })
        _attach_pending_queue_state(result)
        return result
    submission_owner = claim_created

    def _apply_claim_to_result(claim: dict, *, replay: bool) -> None:
        result.update({
            "action": claim.get("action"),
            "symbol": claim.get("symbol"),
            "direction": claim.get("direction"),
            "market_type": claim.get("market_type"),
            "conviction": claim.get("conviction"),
            "reasoning": claim.get("reasoning") or "",
            "proposal_id": claim.get("proposal_id"),
            "proposal_status": claim.get("proposal_status"),
            "proposal_reason": claim.get("proposal_reason"),
            "opportunity_id": claim.get("opportunity_id"),
            "signal_source": claim.get("signal_source") or "market_observer",
        })
        if replay:
            result["idempotent_replay"] = True

    def _return_existing_claim() -> dict:
        _apply_claim_to_result(claimed_final, replay=True)
        _attach_pending_queue_state(result)
        return result

    if not claim_created:
        interrupted = (
            claimed_final.get("proposal_status") == "submitting"
            and not claimed_final.get("proposal_id")
        )
        if interrupted and prepared_proposal is not None:
            claimed_final, recovered = recover_interrupted_decision(
                claimed_final, result, prepared_proposal
            )
            submission_owner = recovered or bool(claimed_final.get("proposal_id"))
        elif interrupted:
            # The current assessment did not reproduce the executable intent.
            # It has no authority to settle the older durable trade claim as a
            # skip/failure; preserve that claim for a later strict matching retry.
            _apply_claim_to_result(claimed_final, replay=True)
            result["proposal_status"] = "submitting"
            result["proposal_reason"] = "recovery_deferred"
            result["proposal_error"] = "recovery_deferred"
            result["recovery_deferred"] = True
            result["operator_decision_persisted"] = True
            _attach_pending_queue_state(result)
            return result
        elif (
            claimed_final.get("proposal_status") == "submitting"
            and claimed_final.get("proposal_id")
        ):
            # The atomic claim already owns a durable Proposal. A fresh model
            # assessment is advisory after that commit and cannot replace the
            # original trade with skip/watch. Resume the stored identity and
            # execution mode instead of finalizing it as not_applicable.
            if claimed_final.get("action") != "trade":
                return _return_existing_claim()
            _apply_claim_to_result(claimed_final, replay=True)
            trade_like = True
            submission_owner = True
        else:
            return _return_existing_claim()

    if trade_like and claimed_final.get("proposal_id") and submission_owner:
        from aletheia.execution.gateway import execute_existing_proposal
        from aletheia.storage.repositories import get_trade_proposal

        stored_proposal = get_trade_proposal(claimed_final["proposal_id"])
        proposal_execution_mode = (
            str(stored_proposal.get("execution_mode"))
            if stored_proposal and stored_proposal.get("execution_mode")
            else execution_mode
        )
        proposal_result = execute_existing_proposal(
            claimed_final["proposal_id"], proposal_execution_mode
        )
        if proposal_result.get("status") == "already_claimed":
            return _return_existing_claim()
        result["proposal_id"] = proposal_result.get("proposal_id")
        result["proposal_status"] = _proposal_status_from_execution_result(
            proposal_result
        )
        result["proposal_reason"] = (
            proposal_result.get("reason") or proposal_result.get("reason_code")
        )
        result["filter_trace"] = decision.filter_trace
        if result.get("proposal_reason"):
            result["proposal_error"] = result["proposal_reason"]

    # Settle the durable claim after the Proposal reaches a visible result.
    settled_final = finalize_decision(
        claimed_final["id"],
        result,
        # Candidate skip trace/outcome facts were written atomically with the
        # claim.  Do not turn a later best-effort reflection retry into their
        # only evidence path.
        publish_filter_evidence=(
            claim_created and claimed_final.get("action") != "skip"
        ),
    )
    if claim_created:
        result["proposal_id"] = settled_final.get("proposal_id")
        result["proposal_status"] = settled_final.get("proposal_status")
        result["proposal_reason"] = settled_final.get("proposal_reason")
    else:
        _apply_claim_to_result(settled_final, replay=True)

    # #321: cache a freshly-created, validated adverse skip so a later cycle that
    # re-emits the same symbol at an unchanged material fingerprint can skip the
    # second counterfactual LLM confirmation. Written always (data-harmless);
    # only consumed when reuse_recent_skip_validation is enabled.
    if (
        claim_created
        and result.get("action") == "skip"
        and getattr(decision, "skip_kind", None) == "adverse_candidate_evidence"
        and getattr(decision, "adverse_reason_codes", None)
        and selected_opportunity_id
    ):
        try:
            _record_skip_validated_observation(
                ctx,
                opportunity_id=selected_opportunity_id,
                policy_epoch_id=policy_epoch_id,
                decision=decision,
                window_minutes=int(
                    ctx["skip_policy"].get("reuse_skip_validation_window_minutes") or 30
                ),
                trigger=str(params.get("_trigger") or "manual"),
            )
        except Exception:
            logger.debug(
                "skip_validated cache write failed for %s",
                selected_opportunity_id,
                exc_info=True,
            )

    # Data completeness can trigger a maintenance request. Aggregate signal
    # quality is deliberately observation-only under B1: it must not create an
    # implicit pause or confidence veto after the candidate was evaluated.
    try:
        data_health = ctx.get("layer1", {}).get("data_health", {})
        if not data_health.get("ohlcv_ready"):
            _emit_self_heal_action(
                "request_backfill",
                reason=f"only {data_health.get('members_with_full_coverage', 0)}/{data_health.get('total_members', 0)} members have full OHLCV coverage",
            )
    except Exception:
        pass

    _attach_pending_queue_state(result)

    # A terminal Operator decision releases one admission slot. Wake Ranker
    # after the durable decision is settled so backpressured Opportunities do
    # not wait for the three-minute safety heartbeat.
    try:
        from aletheia.agent_runtime.scheduler import notify_paper_trader

        notify_paper_trader(source="operator_queue_slot_released")
    except Exception:
        logger.debug("Failed to notify Ranker after queue-slot release", exc_info=True)

    return result


async def review_portfolio_state(params: dict) -> dict:
    """System Operator reviews open positions and may hold, adjust, or close one."""
    import json as _json

    from aletheia.agent_shell.context import build_app_context
    from aletheia.agents.helpers import llm_structured

    params = params or {}
    wake_batches = [
        batch
        for batch in (params.get("wake_batches") or [])
        if isinstance(batch, dict) and batch.get("position_id")
    ]
    wake_position_ids = list(dict.fromkeys(
        str(batch["position_id"]) for batch in wake_batches
    ))
    from aletheia.agent_runtime.wake_events import HORIZON_DUE

    horizon_due_position_ids = {
        str(batch["position_id"])
        for batch in wake_batches
        if any(
            isinstance(event, dict) and event.get("kind") == HORIZON_DUE
            for event in (batch.get("events") or [])
        )
    }
    wake_fingerprint = (
        "sha256:"
        + hashlib.sha256(
            _json.dumps(
                wake_batches,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if wake_batches
        else None
    )
    ctx = build_app_context(sections=["risk"])
    # This is execution authority, not a display context. build_app_context()
    # intentionally truncates and projects positions for UI/LLM summaries, so it
    # omits the exact id and market_type required for safe position selection.
    all_open_positions = list_all_open_positions()
    total_unrealized_pnl = 0.0
    unrealized_pnl_available = True
    for position in all_open_positions:
        try:
            unrealized_pnl = float(position.get("unrealized_pnl"))
        except (TypeError, ValueError):
            unrealized_pnl_available = False
            break
        if not math.isfinite(unrealized_pnl):
            unrealized_pnl_available = False
            break
        total_unrealized_pnl += unrealized_pnl
    portfolio_context = {
        "total_open_positions": len(all_open_positions),
        "review_scope_positions": 0,
        "unrealized_pnl_available": unrealized_pnl_available,
        "total_unrealized_pnl_usdt": (
            round(total_unrealized_pnl, 8)
            if unrealized_pnl_available
            else None
        ),
    }
    open_positions = all_open_positions
    if wake_position_ids:
        wake_ids = set(wake_position_ids)
        open_positions = [
            position
            for position in open_positions
            if str(position.get("id") or "") in wake_ids
        ]
    # Stale wake batches are terminal for already-closed Positions and must not
    # change the action contract for a different open Position in the same
    # review.  Only in-scope open horizon wakes require close-or-extend.
    open_scope_ids = {str(position.get("id") or "") for position in open_positions}
    horizon_due_position_ids.intersection_update(open_scope_ids)
    horizon_due_review = bool(horizon_due_position_ids)
    portfolio_context["review_scope_positions"] = len(open_positions)
    risk = ctx.get("risk", {})

    if not open_positions:
        return {
            "action": "none",
            "reason": (
                "wake_positions_not_open" if wake_position_ids else "no_open_positions"
            ),
            # A stale wake whose exact Position is already closed is terminal;
            # retrying it can neither review nor safely mutate that Position.
            "review_completed": bool(wake_position_ids),
        }

    from aletheia.data.price_feed import (
        get_fresh_mark_for_protective_close,
        protective_price_venue,
    )

    configured_data_venue = config_loader.load_config().market_data.ccxt_exchange
    review_marks: dict[str, dict | None] = {}
    review_mark_evidence: list[dict] = []
    review_now = datetime.now(timezone.utc)
    for position in open_positions:
        position_id = str(position.get("id") or "")
        venue = protective_price_venue(
            position.get("execution_mode"),
            configured_data_venue=configured_data_venue,
        )
        mark = (
            get_fresh_mark_for_protective_close(
                str(position.get("symbol") or ""),
                str(position.get("market_type") or ""),
                venue=venue,
            )
            if venue
            else None
        )
        review_marks[position_id] = mark
        evidence = {
            "position_id": position_id,
            "status": "unavailable",
            "requested_venue": venue,
            "requested_symbol": str(position.get("symbol") or ""),
            "requested_market_type": str(position.get("market_type") or ""),
        }
        if mark is not None:
            evidence = {
                "position_id": position_id,
                "status": "available",
                **{
                    key: mark[key]
                    for key in (
                        "price",
                        "observed_at",
                        "age_seconds",
                        "source",
                        "venue",
                        "symbol",
                        "market_type",
                    )
                },
            }
        review_mark_evidence.append(evidence)

    schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": (
                    ["adjust_position", "close_one"]
                    if horizon_due_review
                    else ["hold", "adjust_position", "close_one"]
                ),
            },
            "target_symbol": {"type": "string"},
            "target_market_type": {"type": "string"},
            "target_direction": {
                "type": "string",
                "enum": ["long", "short"],
            },
            "target_position_id": {"type": "string"},
            "new_stop_price": {"type": "number"},
            "new_target_price": {"type": "number"},
            "new_horizon_hours": {"type": "number"},
            "reasoning": {"type": "string"},
        },
        "required": ["action", "reasoning"],
    }

    def _position_review_value(value) -> object:
        return "not available" if value is None else value

    def _position_holding_age_hours(position: dict) -> float | None:
        try:
            opened_at = datetime.fromisoformat(
                str(position.get("opened_at") or "").replace("Z", "+00:00")
            )
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            age = (review_now - opened_at).total_seconds() / 3600.0
        except (TypeError, ValueError):
            return None
        return round(age, 4) if math.isfinite(age) and age >= 0 else None

    def _mark_directional_return_pct(position: dict, mark: dict | None) -> float | None:
        if mark is None:
            return None
        try:
            entry = float(position.get("entry_price"))
            price = float(mark["price"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(entry) or entry <= 0:
            return None
        direction = str(position.get("direction") or "").lower()
        if direction not in {"long", "short"}:
            return None
        signed = price - entry if direction == "long" else entry - price
        return round((signed / entry) * 100.0, 6)

    position_lines = []
    for position in open_positions:
        mark = review_marks.get(str(position.get("id") or ""))
        position_lines.append(
            f"  id={position['id']} {position['symbol']} "
            f"({position.get('market_type', 'spot')}) "
            f"{position['direction']} qty={position['quantity']} "
            f"entry={_position_review_value(position.get('entry_price'))} "
            f"fresh_mark={_position_review_value(mark.get('price') if mark else None)} "
            f"fresh_mark_observed_at="
            f"{_position_review_value(mark.get('observed_at') if mark else None)} "
            f"fresh_mark_age_seconds="
            f"{_position_review_value(mark.get('age_seconds') if mark else None)} "
            f"fresh_mark_source="
            f"{_position_review_value(mark.get('source') if mark else None)} "
            f"mark_directional_return_pct="
            f"{_position_review_value(_mark_directional_return_pct(position, mark))} "
            f"persisted_current={_position_review_value(position.get('current_price'))} "
            f"persisted_upnl={_position_review_value(position.get('unrealized_pnl'))} "
            f"stop={_position_review_value(position.get('stop_price'))} "
            f"target={_position_review_value(position.get('take_profit_price'))} "
            f"opened_at={_position_review_value(position.get('opened_at'))} "
            f"holding_age_hours="
            f"{_position_review_value(_position_holding_age_hours(position))} "
            f"expected_horizon_hours="
            f"{_position_review_value(position.get('expected_horizon_hours'))}"
        )
    pos_summary = "\n".join(position_lines)
    total_unrealized_text = (
        f"{total_unrealized_pnl:.2f} USDT"
        if unrealized_pnl_available
        else "not available"
    )
    wake_section = (
        "\nWake evidence requiring this review:\n"
        + _json.dumps(wake_batches, sort_keys=True, default=str)[:6000]
        if wake_batches
        else ""
    )
    system = "You are a portfolio risk reviewer for an autonomous paper trading system."
    user = f"""Global portfolio context:
  Total open positions: {len(all_open_positions)}
  Persisted global unrealized PnL: {total_unrealized_text}

Positions in this review scope ({len(open_positions)} of {len(all_open_positions)} total open positions):
{pos_summary}
{wake_section}

Risk limits:
  Max open positions (demo): {risk.get('max_open_positions_demo', '?')}
  Kill switch: {risk.get('kill_switch', False)}

Review the portfolio and decide:
- "hold": keep the supplied position unchanged
- "adjust_position": revise its stop, target, or horizon
- "close_one": close one exact position

Be conservative. Global portfolio context is read-only. For adjust_position or
close_one, target_position_id must identify one listed review-scope position. Never
act on a position outside the supplied wake evidence.

For horizon_due wakes, "hold" is not a valid terminal answer. Choose "close_one",
or choose "adjust_position" and set new_horizon_hours to an absolute age from
opened_at that is strictly greater than the current age reported in the wake
evidence. A stop/target-only or empty adjustment does not extend the horizon and
will be retried.

Return JSON: {_json.dumps(schema)}"""

    class _Schema:
        @staticmethod
        def model_json_schema() -> dict:
            return schema

    review, err = await llm_structured(
        system,
        user,
        schema=_Schema(),
        caller="agent_shell.portfolio_review",
        # This bounded JSON choice does not benefit from an internal reasoning
        # transcript. Reserve the output budget for the schema response so a
        # thinking model cannot exhaust every retry before emitting JSON.
        reasoning_effort="none",
    )
    if err or review is None:
        from aletheia.storage.models import PiDecision
        from aletheia.storage.repositories import insert_pi_decision

        target = open_positions[0] if len(open_positions) == 1 else None
        review_error_code = str(err or "empty_review").split(":", 1)[0][:80]
        unavailable = PiDecision(
            symbol=target.get("symbol") if target else None,
            direction=target.get("direction") if target else None,
            market_type=target.get("market_type") if target else None,
            action="review_unavailable",
            conviction=0,
            reasoning="Operator position review unavailable",
            proposal_status="failed",
            proposal_reason="operator_review_unavailable",
            evidence_manifest_json=_json.dumps({
                "position_review": {
                    "trigger": (
                        params.get("trigger")
                        or params.get("_trigger")
                        or "manual"
                    ),
                    "wake_fingerprint": wake_fingerprint,
                    "wake_batches": wake_batches,
                    "target_position_id": target.get("id") if target else None,
                    "portfolio_context": portfolio_context,
                    "review_marks": review_mark_evidence,
                    "error_code": review_error_code,
                    "effect": {"status": "unavailable"},
                }
            }, sort_keys=True, separators=(",", ":"), default=str),
        )
        insert_pi_decision(unavailable)
        return {
            "action": "review_unavailable",
            "reason": "operator_review_unavailable",
            "review_completed": False,
            "review_error_code": review_error_code,
            "position_review_decision_id": unavailable.id,
            "wake_fingerprint": wake_fingerprint,
        }

    raw_action = str(review.get("action") or "hold")
    supported_action = raw_action in set(schema["properties"]["action"]["enum"])
    action = raw_action if supported_action else "hold"
    reasoning = str(review.get("reasoning") or "")[:300]
    if not supported_action:
        reasoning = (
            f"{reasoning} [unsupported_action:{raw_action}; normalized to hold]"
        ).strip()
    result = {
        "action": action,
        "reasoning": reasoning,
        "target_symbol": review.get("target_symbol"),
    }
    if horizon_due_review and raw_action == "hold":
        result["horizon_review_error"] = (
            "explicit_extension_or_close_required"
        )

    sym = review.get("target_symbol") or ""
    mkt = review.get("target_market_type")
    direction = review.get("target_direction")
    target_id = review.get("target_position_id")
    candidates = list_all_open_positions()
    if wake_position_ids:
        wake_ids = set(wake_position_ids)
        candidates = [p for p in candidates if p["id"] in wake_ids]
    mutating_action = action in {"adjust_position", "close_one"}
    if mutating_action and not target_id:
        matching = []
    elif target_id:
        matching = [p for p in candidates if p["id"] == target_id]
    else:
        matching = [p for p in candidates if p["symbol"] == sym]
        if mkt:
            matching = [p for p in matching if p.get("market_type") == mkt]
        if direction:
            matching = [p for p in matching if p["direction"] == direction]

    if mutating_action and not matching:
        result[f"{action}_error"] = (
            "target_position_id_required" if not target_id else "no_target_position"
        )

    target = matching[0] if matching else (
        open_positions[0] if len(open_positions) == 1 else None
    )
    target_mark_available = bool(
        target and review_marks.get(str(target.get("id") or "")) is not None
    )
    time_authorized_horizon_close = bool(
        action == "close_one"
        and target
        and str(target.get("id") or "") in horizon_due_position_ids
    )
    if action == "adjust_position" and matching and not target_mark_available:
        result["adjust_position_error"] = "fresh_review_mark_unavailable"
    if (
        action == "close_one"
        and matching
        and not target_mark_available
        and not time_authorized_horizon_close
    ):
        result["close_one_error"] = "fresh_review_mark_unavailable"

    def _effect(*, pending: bool = False) -> dict:
        if pending:
            return {"status": "pending"}
        return {
            key: value
            for key, value in result.items()
            if key in {
                "adjust",
                "adjust_position_error",
                "close",
                "close_error",
                "close_one_error",
                "positions_closed",
                "horizon_review_error",
            }
        }

    def _evidence(*, pending: bool = False) -> dict:
        return {
            "position_review": {
                "trigger": params.get("trigger") or params.get("_trigger") or "manual",
                "wake_fingerprint": wake_fingerprint,
                "wake_batches": wake_batches,
                "target_position_id": target.get("id") if target else None,
                "portfolio_context": portfolio_context,
                "review_marks": review_mark_evidence,
                "effect": _effect(pending=pending),
            }
        }

    # Persist the Operator's exact intent before any position mutation or close
    # submission. If a later broker/SQLite operation fails, the causal decision
    # remains durable with effect.status=pending instead of leaving an orphaned
    # position side effect with no PiDecision authority record.
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision, update_pi_decision

    mutation_blocked = bool(
        result.get("adjust_position_error") or result.get("close_one_error")
    )
    mutating = mutating_action and bool(matching) and not mutation_blocked
    persisted = PiDecision(
        symbol=target.get("symbol") if target else None,
        direction=target.get("direction") if target else None,
        market_type=target.get("market_type") if target else None,
        action="close" if action == "close_one" else action,
        conviction=0,
        reasoning=reasoning,
        proposal_status="not_applicable",
        proposal_reason="position_review",
        evidence_manifest_json=_json.dumps(
            _evidence(pending=mutating),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )
    insert_pi_decision(persisted)
    result["position_review_decision_id"] = persisted.id
    result["wake_fingerprint"] = wake_fingerprint

    if action == "adjust_position" and matching and not mutation_blocked:
        from aletheia.portfolio.position_manager import apply_operator_adjustment

        target = matching[0]
        if (
            target["id"] in horizon_due_position_ids
            and review.get("new_horizon_hours") is None
        ):
            result["adjust_position_error"] = "horizon_extension_required"
        else:
            result["adjust"] = apply_operator_adjustment(
                target["id"],
                new_stop=review.get("new_stop_price"),
                new_target=review.get("new_target_price"),
                new_horizon=review.get("new_horizon_hours"),
                reason=reasoning,
            )

    closed_count = 0
    if action == "close_one" and matching:
        if not mutation_blocked:
            for pos in matching[:1]:
                try:
                    from aletheia.portfolio.position_manager import close_position_routed

                    close_result = close_position_routed(
                        pos["id"],
                        reason="pi_agent_portfolio_review",
                        fallback_exit_price=None,
                        decision_reasoning=reasoning,
                        decision_actor="system_operator",
                        decision_source="portfolio_review",
                    )
                    result["close"] = close_result
                    if (
                        isinstance(close_result, dict)
                        and close_result.get("closed") is True
                    ):
                        closed_count += 1
                except Exception as exc:
                    result["close_error"] = str(exc)[:100]
        result["positions_closed"] = closed_count

    # A wake is complete only when the requested effect has an authoritative
    # terminal fact.  In particular, a close exception/error is not daemon success.
    # Exchange-demo close acknowledgement is terminal for this review because its
    # durable pending-close claim is subsequently owned by reconciliation; retrying
    # the review must never submit a replacement order.
    if action == "hold":
        review_completed = supported_action and not horizon_due_review
    elif action == "adjust_position":
        adjustment = result.get("adjust")
        review_completed = bool(
            isinstance(adjustment, dict) and adjustment.get("updated") is True
        )
    elif action == "close_one":
        from aletheia.portfolio.position_manager import _OPEN_CLOSE_STATES
        from aletheia.storage.repositories import get_position

        close_state = get_position(matching[0]["id"]) if matching else None
        durable_close_claim = bool(
            close_state
            and close_state.get("pending_close_order_id")
            and close_state.get("pending_close_status") in _OPEN_CLOSE_STATES
        )
        review_completed = bool(closed_count == 1 or durable_close_claim)
    else:
        review_completed = False
    result["review_completed"] = review_completed

    if review_completed and wake_batches:
        from aletheia.portfolio.position_manager import acknowledge_horizon_review

        horizon_position_ids = list(dict.fromkeys(
            str(batch["position_id"])
            for batch in wake_batches
            if str(batch["position_id"]) in horizon_due_position_ids
        ))
        if action in {"adjust_position", "close_one"}:
            effective_target_id = str(matching[0]["id"]) if matching else None
            horizon_position_ids = [
                position_id
                for position_id in horizon_position_ids
                if position_id == effective_target_id
            ]
        if horizon_position_ids:
            result["horizon_reviews_acknowledged"] = acknowledge_horizon_review(
                horizon_position_ids
            )

    update_pi_decision(
        persisted.id,
        evidence_manifest_json=_json.dumps(
            _evidence(),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )
    return result


HANDLERS: dict[str, callable] = {
    "get_system_status": get_system_status,
    "get_operator_status": get_operator_status,
    "get_agent_activity": get_agent_activity,
    "get_market_snapshot": get_market_snapshot,
    "get_positions": get_positions,
    "explain_agent_run": explain_agent_run,
    "explain_proposal": explain_proposal,
    "get_failed_tasks": get_failed_tasks,
    "get_recommendations": get_recommendations,
    "pause_agents": pause_agents,
    "resume_agents": resume_agents,
    "create_research_note": create_research_note,
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
    "reconcile_positions": reconcile_positions,
    "run_market_scan": run_market_scan,
    "rerun_failed_agent_task": rerun_failed_agent_task,
    "set_symbol_observe_only": set_symbol_observe_only,
    "propose_prompt_change": propose_prompt_change,
    "propose_schedule_change": propose_schedule_change,
    "propose_risk_boundary_change": propose_risk_boundary_change,
    "propose_command_center_behavior_change": propose_command_center_behavior_change,
    "propose_extension_enablement": propose_extension_enablement,
    "evaluate_trading_opportunity": evaluate_trading_opportunity,
    "review_portfolio_state": review_portfolio_state,
}
