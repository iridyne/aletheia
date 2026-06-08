"""Agent orchestrator — runs agents with permission enforcement."""

import traceback

from aletheia.agent_runtime.run_logger import start_run, end_run
from aletheia.config.loader import load_config
from aletheia.files.jsonl import write_audit
from aletheia.storage.migrations import run_migrations


async def run_agent(agent_name: str, trigger_type: str = "scheduled") -> dict:
    """Run a named agent with full lifecycle logging."""
    run_migrations()
    # Ensure tool permissions are registered even if the app lifespan didn't run
    # (e.g. tests using ASGITransport, or a direct orchestrator call).
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()
    cfg = load_config()
    permission_mode = cfg.agent.permission_mode
    provider_name = cfg.models.default_provider

    run = start_run(agent_name, trigger_type, permission_mode, provider_name)
    outputs = []
    errors = []

    try:
        from aletheia.agents import get_agent_fn
        agent_fn = get_agent_fn(agent_name)
        result = await agent_fn(permission_mode=permission_mode, run_id=run.id)
        if isinstance(result, dict):
            outputs = result.get("outputs", [])
            errors = result.get("errors", [])
    except Exception as e:
        errors.append(traceback.format_exc())
        write_audit({
            "actor": "system", "source": "orchestrator",
            "event_type": "agent_failed", "entity_type": "agent_run",
            "entity_id": run.id,
            "message": f"Agent {agent_name} raised: {e}",
        })

    status = "completed" if not errors else "completed_with_errors"
    end_run(run, status=status, outputs=outputs, errors=errors)
    # Release the per-run step counter (helpers.log_tool / record_step keep one
    # per run_id to assign monotonic step_numbers without SELECT-MAX).
    try:
        from aletheia.agents.helpers import _release_run_counter
        _release_run_counter(run.id)
    except Exception:
        pass
    return {"run_id": run.id, "status": status, "outputs": outputs, "errors": errors}
