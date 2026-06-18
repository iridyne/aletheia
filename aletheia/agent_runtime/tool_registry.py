"""Tool registry — registers all agent tools with their permission classes."""

from aletheia.agent_runtime.permissions import register_tool

# Observe tools — read market data + write analysis/research
OBSERVE_TOOLS = [
    "get_market_snapshot",
    "get_positions",
    "get_account_summary",
    "get_risk_boundaries",
    "get_strategy_status",
    "get_recent_agent_runs",
    "create_research_note",
    "create_opportunity",
    "create_strategy_hypothesis",
    "write_dreaming_log",
    "write_daily_review",
    "create_post_trade_review",
]

# Paper tools — submit simulated orders
PAPER_TOOLS = [
    "create_paper_proposal",
    "run_paper_order",
    "update_paper_strategy_config",
]

# System tools — modify playbooks/prompts/strategy config
# DEPRECATED: no handler implementation exists for these tools.
# They remain registered so permission lookups don't KeyError.
# Use is_tool_implemented() to guard before attempting execution.
SYSTEM_TOOLS = [
    "create_playbook_draft",
    "create_prompt_draft",
    "create_code_change_proposal",
]

_UNIMPLEMENTED_TOOLS = set(SYSTEM_TOOLS)

# Restricted tools — never callable by agent
RESTRICTED_TOOLS = [
    "unlock_live",
    "place_live_order",
    "change_hard_risk_limits",
    "modify_credentials",
    "modify_execution_gateway",
]


def is_tool_implemented(tool_name: str) -> bool:
    """Return False for tools registered but lacking a handler."""
    return tool_name not in _UNIMPLEMENTED_TOOLS


def init_tool_registry() -> None:
    """Register all tools with their permission classes."""
    for t in OBSERVE_TOOLS:
        register_tool(t, "observe")
    for t in PAPER_TOOLS:
        register_tool(t, "paper")
    for t in SYSTEM_TOOLS:
        register_tool(t, "system")
    for t in RESTRICTED_TOOLS:
        register_tool(t, "restricted")
