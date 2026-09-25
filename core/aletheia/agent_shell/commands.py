"""Command handlers for the Agent Shell control plane."""

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone

import aletheia.config.loader as config_loader
from aletheia.agent_runtime.orchestrator import run_agent
from aletheia.storage.repositories import (
    get_agent_run,
    get_trade_proposal,
    list_agent_runs,
    list_all_open_positions,
    list_fills,
    list_market_snapshots,
    list_orders,
    list_positions,
    list_risk_decisions,
    list_run_steps,
    list_tool_calls,
    list_trade_proposals,
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
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS, get_command

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
    from aletheia.agent_runtime.pause_ownership import MANUAL_OWNER, acquire_pause, pause_acknowledgement

    acquire_pause(
        MANUAL_OWNER,
        reason="agent_shell_pause",
        actor="agent_shell",
        source="command_center",
    )
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "agent_shell", "source": "command_center",
                 "event_type": "gateway_paused", "message": "Agents paused via Agent Shell"})
    return pause_acknowledgement()


async def resume_agents(params: dict) -> dict:
    from aletheia.agent_runtime import resume_gate
    from aletheia.agent_runtime.pause_ownership import pause_acknowledgement

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
    return pause_acknowledgement(paused=False)


async def create_research_note(params: dict) -> dict:
    title = params.get("title", "Untitled Research Note")
    content = params.get("content", "")
    # PCI-G07: reject docs/ prefix path — PI must not overwrite constitution docs via low-risk commands
    if str(params.get("path") or "").startswith("docs/") or str(title).startswith("docs/"):
        return {"error": "Path rejected: docs/ prefix is reserved for human-authored documents (PCI-G07)."}
    if not content:
        return {"error": "Cannot create a research note without content."}

    try:
        from extensions.knowledge.manager import create_research_note as _create
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.knowledge", "extensions.knowledge.manager"}:
            raise
        return {"error": "knowledge_extension_unavailable"}
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

    try:
        from extensions.knowledge.manager import create_dreaming_log as _create
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.knowledge", "extensions.knowledge.manager"}:
            raise
        return {"error": "knowledge_extension_unavailable"}

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
    try:
        from extensions.knowledge.manager import create_research_note as _create
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.knowledge", "extensions.knowledge.manager"}:
            raise
        return {"error": "knowledge_extension_unavailable"}
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

    try:
        from extensions.knowledge.manager import create_research_note as _create
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.knowledge", "extensions.knowledge.manager"}:
            raise
        return {"error": "knowledge_extension_unavailable"}
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

    try:
        from extensions.knowledge.manager import create_daily_review as _create
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.knowledge", "extensions.knowledge.manager"}:
            raise
        return {"error": "knowledge_extension_unavailable"}

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

    from aletheia.agents import get_agent_fn

    try:
        get_agent_fn(str(target_run["agent_name"]))
    except ValueError as exc:
        return {"error": str(exc)}

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
    from aletheia.data.symbols import MARKET_SPOT, InstrumentId, to_canonical
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


def _horizon_due_allow_hold(execution_mode: str | None) -> bool:
    """Safer default is True: expiry is not a forced flatten."""
    from aletheia.risk.policy import UnknownRiskExecutionMode, resolve_risk_policy

    mode = str(execution_mode or "")
    try:
        resolved = resolve_risk_policy(config_loader.load_config(), mode)
    except UnknownRiskExecutionMode:
        return True
    return bool(getattr(resolved.policy.exits, "horizon_due_allow_hold", True))


def _horizon_hold_roll_hours(position: dict) -> float | None:
    """One more authored horizon period from current age. None if not rollable."""
    from aletheia.risk.policy import UnknownRiskExecutionMode, resolve_risk_policy

    try:
        opened_at = datetime.fromisoformat(
            str(position.get("opened_at") or "").replace("Z", "+00:00")
        )
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        age_hours = (
            datetime.now(timezone.utc) - opened_at
        ).total_seconds() / 3600.0
        horizon = float(position.get("expected_horizon_hours"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(age_hours) or age_hours < 0 or not math.isfinite(horizon) or horizon <= 0:
        return None
    try:
        resolved = resolve_risk_policy(
            config_loader.load_config(),
            str(position.get("execution_mode") or ""),
        )
        backstop = float(resolved.policy.exits.horizon_hard_backstop_mult or 3.0)
    except (UnknownRiskExecutionMode, TypeError, ValueError):
        backstop = 3.0
    if backstop > 0 and age_hours >= horizon * backstop:
        return None
    rolled = round(age_hours + horizon, 4)
    if rolled <= age_hours:
        return None
    return rolled


async def review_portfolio_state(params: dict) -> dict:
    """System Operator reviews open positions and may hold, adjust, or close one."""
    import json as _json

    from aletheia.agent_shell.context import build_app_context
    from aletheia.agents.helpers import llm_structured
    from aletheia.uta.manager import UTA_EXECUTION_MODES

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
    all_open_positions = [
        row for row in list_all_open_positions()
        if row.get("execution_mode") in UTA_EXECUTION_MODES
    ]
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
    sample_horizon = next(
        (
            position
            for position in open_positions
            if str(position.get("id") or "") in horizon_due_position_ids
        ),
        open_positions[0] if open_positions else None,
    )
    allow_horizon_hold = bool(
        horizon_due_review
        and _horizon_due_allow_hold(
            sample_horizon.get("execution_mode") if sample_horizon else ""
        )
    )
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

    review_marks: dict[str, dict | None] = {}
    review_mark_evidence: list[dict] = []
    review_now = datetime.now(timezone.utc)
    for position in open_positions:
        position_id = str(position.get("id") or "")
        venue = protective_price_venue(position.get('execution_mode'))
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
                    if horizon_due_review and not allow_horizon_hold
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
    system = "You are a portfolio risk reviewer for an automated trading system using external exchange accounts."
    if not horizon_due_review:
        horizon_hold_contract = ""
    elif allow_horizon_hold:
        horizon_hold_contract = (
            "For horizon_due wakes, \"hold\" is allowed when there is no clear close thesis. "
            "Hold rolls expected_horizon_hours forward by one original plan period from the "
            "current age and does not flatten. Choose \"close_one\" only with a clear close "
            "thesis. Prefer \"adjust_position\" with new_horizon_hours when you want an explicit "
            "different duration. A stop/target-only or empty adjustment does not extend the "
            "horizon and will be retried."
        )
    else:
        horizon_hold_contract = (
            "For horizon_due wakes, \"hold\" is not a valid terminal answer. Choose "
            "\"close_one\", or choose \"adjust_position\" and set new_horizon_hours to an "
            "absolute age from opened_at that is strictly greater than the current age "
            "reported in the wake evidence. A stop/target-only or empty adjustment does "
            "not extend the horizon and will be retried."
        )
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

{horizon_hold_contract}

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
    if horizon_due_review and raw_action == "hold" and not allow_horizon_hold:
        result["horizon_review_error"] = (
            "explicit_extension_or_close_required"
        )

    sym = review.get("target_symbol") or ""
    mkt = review.get("target_market_type")
    direction = review.get("target_direction")
    target_id = review.get("target_position_id")
    candidates = [
        row for row in list_all_open_positions()
        if row.get("execution_mode") in UTA_EXECUTION_MODES
    ]
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
                "horizon_hold_error",
                "horizon_hold_extended_ids",
                "horizon_hold_extended_hours",
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

    if (
        action == "hold"
        and horizon_due_review
        and allow_horizon_hold
        and not result.get("horizon_review_error")
    ):
        from aletheia.portfolio.position_manager import apply_operator_adjustment

        hold_targets = matching if matching else [
            position
            for position in open_positions
            if str(position.get("id") or "") in horizon_due_position_ids
        ]
        extended_ids: list[str] = []
        rolled_hours: list[float] = []
        for position in hold_targets:
            rolled = _horizon_hold_roll_hours(position)
            if rolled is None:
                result["horizon_hold_error"] = "hold_extension_unavailable"
                break
            adjustment = apply_operator_adjustment(
                position["id"],
                new_horizon=rolled,
                reason=reasoning or "horizon_due_hold",
            )
            if not (isinstance(adjustment, dict) and adjustment.get("updated") is True):
                result["horizon_hold_error"] = (
                    str(adjustment.get("error") or "hold_extension_unavailable")
                    if isinstance(adjustment, dict)
                    else "hold_extension_unavailable"
                )
                result["adjust"] = adjustment
                break
            result["adjust"] = adjustment
            extended_ids.append(str(position["id"]))
            rolled_hours.append(rolled)
        if extended_ids and "horizon_hold_error" not in result:
            result["horizon_hold_extended_ids"] = extended_ids
            result["horizon_hold_extended_hours"] = rolled_hours

    closed_count = 0
    if action == "close_one" and matching:
        if not mutation_blocked:
            for pos in matching[:1]:
                try:
                    from aletheia.portfolio.position_manager import close_position_routed

                    close_result = close_position_routed(
                        pos["id"],
                        reason="pi_agent_portfolio_review",
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
        if horizon_due_review and allow_horizon_hold:
            review_completed = bool(
                result.get("horizon_hold_extended_ids")
                and not result.get("horizon_hold_error")
                and not result.get("horizon_review_error")
            )
        else:
            review_completed = supported_action and not horizon_due_review
    elif action == "adjust_position":
        adjustment = result.get("adjust")
        review_completed = bool(
            isinstance(adjustment, dict) and adjustment.get("updated") is True
        )
    elif action == "close_one":
        from aletheia.execution.close_convergence import has_unresolved_close_claim
        from aletheia.storage.repositories import get_position

        close_state = get_position(matching[0]["id"]) if matching else None
        durable_close_claim = bool(
            close_state and has_unresolved_close_claim(close_state)
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
        elif action == "hold":
            horizon_position_ids = [
                position_id
                for position_id in (result.get("horizon_hold_extended_ids") or [])
                if position_id in horizon_due_position_ids
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
    "rerun_failed_agent_task": rerun_failed_agent_task,
    "set_symbol_observe_only": set_symbol_observe_only,
    "propose_prompt_change": propose_prompt_change,
    "propose_schedule_change": propose_schedule_change,
    "propose_risk_boundary_change": propose_risk_boundary_change,
    "propose_command_center_behavior_change": propose_command_center_behavior_change,
    "propose_extension_enablement": propose_extension_enablement,
    "review_portfolio_state": review_portfolio_state,
}
