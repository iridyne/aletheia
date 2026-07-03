"""Intent classification — LLM structured output with keyword fallback."""

import logging
from pydantic import BaseModel, Field

from aletheia.agent_shell.registry import list_command_names, FORBIDDEN_INTENTS

logger = logging.getLogger("aletheia.agent_shell.intent")

INTENT_SYSTEM_PROMPT = """\
You are the Aletheia Trading OS command interpreter.
Classify the user's natural-language input into exactly ONE command from the list below,
or "query" if it's a general question that doesn't map to a specific command.

Available commands:
- archive_low_value_notification: user wants to archive or dismiss a low-value operator recommendation
- get_system_status: user asks about system health, status, what's running
- get_operator_status: user asks about operator heartbeat, mode, or what the operator is doing
- get_agent_activity: user asks about agent runs, what agents did
- get_market_snapshot: user asks about prices, market data
- get_positions: user asks about open trades, positions, PnL
- explain_agent_run: user asks why an agent did something, explain a run
- explain_proposal: user asks about a trade proposal or why it was rejected
- get_failed_tasks: user asks what failed, which tasks failed, or what errors need review
- get_recommendations: user asks what the operator recommends or suggests doing next
- pause_agents: user wants to pause/stop/halt agents
- resume_agents: user wants to resume/start/continue agents
- create_research_note: user wants to create a research note
- create_dreaming_log: user wants to create or write a dreaming log
- create_diagnostic_todo: user wants to log a diagnostic issue or todo
- mark_task_done: user wants to mark an operator task done
- create_improvement_proposal: user wants to suggest an improvement
- draft_extension_manifest: user wants to draft a new extension manifest
- test_extension_sandbox: user wants to validate a drafted extension in a sandbox
- create_daily_review: user wants to create or write a daily review
- create_review_report: user wants to create a non-trading review report
- create_diagnostic_report: user wants to create a diagnostic or system health report
- run_market_scan: user wants to run a manual market scan
- rerun_failed_agent_task: user wants to retry or rerun a failed task
- set_symbol_observe_only: user wants a symbol observed but not tradable
- propose_prompt_change: user wants to propose changing a prompt or playbook
- propose_schedule_change: user wants to propose a scheduler cadence change
- propose_risk_boundary_change: user wants to propose a risk boundary change
- propose_command_center_behavior_change: user wants to propose a Command Center behavior change
- propose_extension_enablement: user wants to propose human approval for enabling an extension

Output the command name and any parameters you can extract (run_id, proposal_id, title, content).
If the user asks about something dangerous (enable live, change credentials, bypass risk), classify as "forbidden".
"""


class CommandIntent(BaseModel):
    command: str = Field(description="One of the registered commands, 'query', or 'forbidden'")
    parameters: dict = Field(default_factory=dict, description="Extracted parameters")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


# Keyword mapping — fallback when LLM is unavailable.
_KEYWORD_MAP: dict[str, list[str]] = {
    # Operational commands first — their keywords are high-specificity and should
    # take priority over broad read-command keywords like "agent" or "open".
    "pause_agents": ["pause", "stop", "halt"],
    "resume_agents": ["resume", "unpause", "continue"],
    "run_market_scan": ["run market scan", "scan the market", "run scanner", "trigger market scan"],
    "rerun_failed_agent_task": ["rerun failed task", "retry failed task", "rerun failed agent", "retry last failure"],
    "set_symbol_observe_only": ["observe only", "mark symbol observe only", "set observe only"],
    "propose_prompt_change": ["propose prompt change", "prompt change proposal", "propose playbook change"],
    "propose_schedule_change": ["propose schedule change", "schedule change proposal", "propose cadence change"],
    "propose_risk_boundary_change": [
        "propose risk boundary change",
        "risk boundary proposal",
        "propose risk change",
    ],
    "propose_command_center_behavior_change": [
        "propose command center behavior change",
        "command center behavior proposal",
        "propose command center improvement",
    ],
    "propose_extension_enablement": [
        "propose extension enablement",
        "extension enablement proposal",
        "propose enabling extension",
    ],
    "create_review_report": [
        "review report",
        "risk review report",
        "trade review report",
        "agent performance report",
    ],
    "create_diagnostic_report": [
        "diagnostic report",
        "system health report",
        "system health diagnostic report",
    ],
    "create_research_note": ["research", "note", "write note"],
    "create_dreaming_log": ["dreaming log", "write dreaming log", "dream log"],
    "create_diagnostic_todo": ["diagnostic", "todo", "issue", "bug"],
    "mark_task_done": ["mark task done", "complete task", "task done"],
    "archive_low_value_notification": [
        "archive low value notification",
        "archive recommendation",
        "archive this recommendation",
        "archive notification",
        "dismiss recommendation",
    ],
    "create_improvement_proposal": [
        "improvement proposal",
        "create improvement proposal",
        "propose improvement",
        "suggest improvement",
        "improve this",
    ],
    "draft_extension_manifest": [
        "draft extension",
        "create extension manifest",
        "draft extension manifest",
    ],
    "test_extension_sandbox": [
        "test extension sandbox",
        "run extension sandbox",
        "validate extension manifest",
    ],
    "create_daily_review": ["daily review", "write review"],
    # Read commands — specific intents first, broad system-status fallback last.
    "get_operator_status": [
        "operator heartbeat",
        "operator status",
        "operator health",
        "operator mode",
        "what is the operator doing",
    ],
    "get_failed_tasks": ["failed task", "failed tasks", "what failed", "operator task", "errors to review"],
    "get_recommendations": ["recommendations", "recommendation", "suggestion", "suggestions", "what should i do"],
    "get_agent_activity": ["agent activity", "agent run", "agents", "runs", "activity", "jobs"],
    "get_market_snapshot": ["market", "price", "prices", "btc", "eth", "sol", "snapshot"],
    "get_positions": ["position", "positions", "pnl"],
    "explain_proposal": ["proposal", "proposal fail", "rejected", "approved", "risk decision", "why did this proposal"],
    "explain_agent_run": ["explain", "why did", "what did"],
    "get_system_status": ["status", "health", "running", "state", "doing", "system"],
}

_FORBIDDEN_KEYWORDS = [
    "enable live",
    "unlock live",
    "start live trading",
    "place live order",
    "modify credentials",
    "change credentials",
    "api key",
    "secret key",
    "password",
    "bypass risk",
    "disable risk",
    "change risk limits",
    "modify execution gateway",
]


def _classify_keyword(text: str) -> CommandIntent:
    """Rule-based fallback classification."""
    text_lower = text.lower().strip()

    for kw in _FORBIDDEN_KEYWORDS:
        if kw in text_lower:
            return CommandIntent(command="forbidden", confidence=0.6)

    for cmd, keywords in _KEYWORD_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                return CommandIntent(command=cmd, confidence=0.4)

    return CommandIntent(command="query", confidence=0.3)


async def classify_intent(user_input: str, context_summary: str) -> CommandIntent:
    """Classify user input into a command intent. LLM first, keyword fallback."""
    try:
        from aletheia.agents.helpers import llm_structured
        user_prompt = (
            f"Context: {context_summary}\n\n"
            f"User input: \"{user_input}\"\n\n"
            f"Classify this into a command."
        )
        result, error = await llm_structured(
            INTENT_SYSTEM_PROMPT, user_prompt, schema=CommandIntent, max_tokens=200
        )
        if result and not error:
            intent = CommandIntent(**result)
            if intent.command in list_command_names() or intent.command in ("query", "forbidden"):
                return intent
            logger.debug("LLM returned unknown command '%s', falling back", intent.command)
    except Exception as e:
        logger.debug("LLM intent classification failed: %s", e)

    return _classify_keyword(user_input)
