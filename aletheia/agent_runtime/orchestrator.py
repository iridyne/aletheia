"""Agent orchestrator — runs agents with permission enforcement."""

import traceback

from aletheia.agent_runtime.run_logger import begin_queued_run, start_run, end_run
from aletheia.storage.models import AgentRun
from aletheia.config.loader import load_config
from aletheia.files.jsonl import write_audit


async def run_agent(
    agent_name: str,
    trigger_type: str = "scheduled",
    *,
    existing_run: AgentRun | None = None,
) -> dict:
    """Run a named agent with full lifecycle logging."""
    # Migrations run once at gateway startup (aletheia/server/app.py:44); scheduler
    # jobs MUST NOT call run_migrations() — concurrent executescript() across threads
    # was the dominant `database is locked` source.
    # Ensure tool permissions are registered even if the app lifespan didn't run
    # (e.g. tests using ASGITransport, or a direct orchestrator call).
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()
    cfg = load_config()
    permission_mode = cfg.agent.permission_mode
    raw_provider_name = getattr(getattr(cfg, "models", None), "default_provider", None)
    provider_name = raw_provider_name if isinstance(raw_provider_name, str) else None

    if existing_run is None:
        run = start_run(agent_name, trigger_type, permission_mode, provider_name)
    else:
        run = existing_run
        begin_queued_run(
            run,
            permission_mode=permission_mode,
            model_provider=provider_name,
        )
    outputs = []
    errors = []

    try:
        from aletheia.agents import get_agent_fn
        from aletheia.model_provider.broker import inference_priority, priority_for_agent

        agent_fn = get_agent_fn(agent_name)
        with inference_priority(priority_for_agent(agent_name, trigger_type)):
            result = await agent_fn(permission_mode=permission_mode, run_id=run.id)
            if isinstance(result, dict):
                outputs = result.get("outputs", [])
                errors = result.get("errors", [])
    except Exception as e:
        # #70 fix: persist only the exception type + first line (not the full
        # traceback which leaks internal paths and possibly credentials from
        # error messages of HTTP/exchange clients). Full traceback goes to the
        # rotating log file (logger.exception below) which is never API-exposed.
        import logging
        _orch_logger = logging.getLogger("aletheia.orchestrator")
        _orch_logger.exception("Agent %s failed", agent_name)
        safe_error = f"{type(e).__name__}: {str(e)[:200]}"
        errors.append(safe_error)
        write_audit({
            "actor": "system", "source": "orchestrator",
            "event_type": "agent_failed", "entity_type": "agent_run",
            "entity_id": run.id,
            "message": f"Agent {agent_name} failed: {type(e).__name__}",
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
