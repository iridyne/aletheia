"""Command Registry — defines all commands the Agent Shell can execute."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDef:
    name: str
    risk_level: str  # "read" | "low_write" | "operational"
    required_permission: str  # "Observe" | "Paper" | "Full"
    description: str


COMMANDS: dict[str, CommandDef] = {
    "get_system_status": CommandDef(
        name="get_system_status",
        risk_level="read",
        required_permission="Observe",
        description="Show gateway health, permission mode, live state, and config summary",
    ),
    "get_agent_activity": CommandDef(
        name="get_agent_activity",
        risk_level="read",
        required_permission="Observe",
        description="Show recent agent runs with status and timing",
    ),
    "get_market_snapshot": CommandDef(
        name="get_market_snapshot",
        risk_level="read",
        required_permission="Observe",
        description="Show latest market prices for observed symbols",
    ),
    "get_positions": CommandDef(
        name="get_positions",
        risk_level="read",
        required_permission="Observe",
        description="Show open positions with PnL",
    ),
    "explain_agent_run": CommandDef(
        name="explain_agent_run",
        risk_level="read",
        required_permission="Observe",
        description="Explain what a specific agent run did (steps, tools, output)",
    ),
    "explain_proposal": CommandDef(
        name="explain_proposal",
        risk_level="read",
        required_permission="Observe",
        description="Explain a trade proposal and its risk decision",
    ),
    "pause_agents": CommandDef(
        name="pause_agents",
        risk_level="operational",
        required_permission="Paper",
        description="Pause all scheduled agent jobs",
    ),
    "resume_agents": CommandDef(
        name="resume_agents",
        risk_level="operational",
        required_permission="Paper",
        description="Resume paused agent jobs",
    ),
    "create_research_note": CommandDef(
        name="create_research_note",
        risk_level="low_write",
        required_permission="Paper",
        description="Create a research note in the knowledge base",
    ),
    "create_diagnostic_todo": CommandDef(
        name="create_diagnostic_todo",
        risk_level="low_write",
        required_permission="Paper",
        description="Create a diagnostic todo item for a system issue",
    ),
    "create_improvement_proposal": CommandDef(
        name="create_improvement_proposal",
        risk_level="low_write",
        required_permission="Paper",
        description="Create a system improvement proposal",
    ),
}

FORBIDDEN_INTENTS = frozenset([
    "enable_live", "modify_credentials", "bypass_risk",
    "place_live_order", "modify_execution_gateway", "delete_knowledge",
    "change_risk_limits",
])


def get_command(name: str) -> CommandDef | None:
    return COMMANDS.get(name)


def list_command_names() -> list[str]:
    return list(COMMANDS.keys())
