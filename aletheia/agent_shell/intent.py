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
- get_system_status: user asks about system health, status, what's running
- get_agent_activity: user asks about agent runs, what agents did
- get_market_snapshot: user asks about prices, market data
- get_positions: user asks about open trades, positions, PnL
- explain_agent_run: user asks why an agent did something, explain a run
- explain_proposal: user asks about a trade proposal or why it was rejected
- pause_agents: user wants to pause/stop/halt agents
- resume_agents: user wants to resume/start/continue agents
- create_research_note: user wants to create a research note
- create_diagnostic_todo: user wants to log a diagnostic issue or todo
- create_improvement_proposal: user wants to suggest an improvement

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
    "create_research_note": ["research", "note", "write note"],
    "create_diagnostic_todo": ["diagnostic", "todo", "issue", "bug"],
    "create_improvement_proposal": ["improvement", "improve", "suggest"],
    # Read commands — broader keywords, lower priority.
    "get_system_status": ["status", "health", "running", "state", "doing", "system"],
    "get_agent_activity": ["agent activity", "agent run", "agents", "runs", "activity", "jobs"],
    "get_market_snapshot": ["market", "price", "prices", "btc", "eth", "sol", "snapshot"],
    "get_positions": ["position", "positions", "pnl"],
    "explain_agent_run": ["explain", "why did", "what did"],
    "explain_proposal": ["proposal", "rejected", "approved", "risk decision"],
}

_FORBIDDEN_KEYWORDS = ["live", "credential", "password", "api key", "secret", "bypass risk"]


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
