"""Optional observation agents; Core owns their AgentRun execution."""


def register_equity_observer() -> None:
    from aletheia.agents import register_agent
    from extensions.observation.equity_observer import run_equity_observer

    register_agent("equity_observer")(run_equity_observer)
