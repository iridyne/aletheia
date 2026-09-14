"""Optional research-note agent, executed by the Core AgentRun lifecycle."""


def register_research_agent() -> None:
    from aletheia.agents import register_agent
    from extensions.research.research_agent import run_research_agent

    register_agent("research_agent")(run_research_agent)
