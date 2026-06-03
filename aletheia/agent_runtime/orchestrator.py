"""Agent orchestrator — runs agents with permission enforcement."""

from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.agent_runtime.run_logger import start_run, end_run
from aletheia.config.loader import load_config
from aletheia.storage.migrations import run_migrations
from aletheia.files.jsonl import append_jsonl


async def run_agent(agent_name: str, trigger_type: str = "scheduled") -> dict:
    """Run a named agent with full lifecycle logging."""
    run_migrations()
    cfg = load_config()
    permission_mode = cfg.agent.permission_mode
    provider_name = cfg.models.default_provider

    run = start_run(agent_name, trigger_type, permission_mode, provider_name)
    outputs = []
    errors = []

    try:
        from aletheia.agents import get_agent_fn
        agent_fn = get_agent_fn(agent_name)
        result = await agent_fn(permission_mode=permission_mode)
        if isinstance(result, dict):
            outputs = result.get("outputs", [])
            errors = result.get("errors", [])
    except Exception as e:
        errors.append(str(e))

    status = "completed" if not errors else "completed_with_errors"
    end_run(run, status=status, outputs=outputs, errors=errors)
    return {"run_id": run.id, "status": status, "outputs": outputs, "errors": errors}
