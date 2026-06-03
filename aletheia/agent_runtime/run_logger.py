"""Agent run logging to SQLite + JSONL."""

from datetime import datetime

from aletheia.storage.models import AgentRun
from aletheia.storage.repositories import insert_agent_run, update_agent_run
from aletheia.files.jsonl import write_agent_run_log


def start_run(agent_name: str, trigger_type: str, permission_mode: str, model_provider: str | None = None) -> AgentRun:
    run = AgentRun(
        agent_name=agent_name,
        trigger_type=trigger_type,
        permission_mode=permission_mode,
        model_provider=model_provider,
    )
    insert_agent_run(run)
    write_agent_run_log({"event": "run_started", **run.model_dump()})
    return run


def end_run(run: AgentRun, status: str = "completed", outputs: list[str] | None = None, errors: list[str] | None = None) -> None:
    import json
    update_data = {
        "status": status,
        "ended_at": datetime.utcnow().isoformat(),
    }
    if outputs:
        update_data["outputs_created"] = json.dumps(outputs)
    if errors:
        update_data["errors"] = json.dumps(errors)
    update_agent_run(run.id, **update_data)
    write_agent_run_log({"event": "run_ended", "run_id": run.id, "status": status})
