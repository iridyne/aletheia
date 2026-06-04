"""Tool registry — registers all agent tools with their permission classes."""

from aletheia.agent_runtime.permissions import register_tool

# Read tools
READ_TOOLS = [
    "get_market_snapshot",
    "get_positions",
    "get_account_summary",
    "get_risk_boundaries",
    "get_strategy_status",
    "get_recent_agent_runs",
]

# Research tools
RESEARCH_TOOLS = [
    "create_research_note",
    "create_opportunity",
    "create_strategy_hypothesis",
    "write_dreaming_log",
    "write_daily_review",
    "create_post_trade_review",
]

# Paper tools
PAPER_TOOLS = [
    "create_paper_proposal",
    "run_paper_order",
    "update_paper_strategy_config",
]

# Editing tools
EDITING_TOOLS = [
    "create_playbook_draft",
    "create_prompt_draft",
    "create_code_change_proposal",
]

# Restricted tools — never callable by agent
RESTRICTED_TOOLS = [
    "unlock_live",
    "place_live_order",
    "change_hard_risk_limits",
    "modify_credentials",
    "modify_execution_gateway",
]


def init_tool_registry() -> None:
    """Register all tools with their permission classes."""
    for t in READ_TOOLS:
        register_tool(t, "read_tool")
    for t in RESEARCH_TOOLS:
        register_tool(t, "research_tool")
    for t in PAPER_TOOLS:
        register_tool(t, "paper_tool")
    for t in EDITING_TOOLS:
        register_tool(t, "editing_tool")
    for t in RESTRICTED_TOOLS:
        register_tool(t, "restricted_tool")
