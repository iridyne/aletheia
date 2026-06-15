"""Agent run logging to SQLite + JSONL."""

from datetime import datetime, timezone, timedelta
import json

from aletheia.storage.models import AgentRun
from aletheia.storage.repositories import insert_agent_run, update_agent_run
from aletheia.storage.sqlite import get_db
from aletheia.files.jsonl import write_agent_run_log, write_audit


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
    update_data = {
        "status": status,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "outputs_created": json.dumps(outputs if outputs is not None else []),
        "errors": json.dumps(errors if errors is not None else []),
    }
    update_agent_run(run.id, **update_data)
    write_agent_run_log({"event": "run_ended", "run_id": run.id, "status": status})


def _decode_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return [str(raw)]
    return value if isinstance(value, list) else [value]


def mark_stale_running_runs(
    *,
    max_age_minutes: int | None = None,
    reason: str = "gateway startup reconciled incomplete agent run",
    source: str = "agent_runtime",
) -> int:
    """Mark leftover `running` agent rows as stale.

    A gateway restart can interrupt an agent between `start_run()` and
    `end_run()`. Leaving those rows as `running` makes the dashboard claim work is
    still active when no process owns it. This reconciliation closes only the run
    lifecycle row; it does not change trading, risk, execution, or outputs.
    """
    now = datetime.now(timezone.utc)
    ended_at = now.isoformat()
    where = "status = ?"
    params: list[object] = ["running"]
    if max_age_minutes is not None:
        cutoff = (now - timedelta(minutes=max_age_minutes)).isoformat()
        where += " AND started_at <= ?"
        params.append(cutoff)

    db = get_db()
    rows = db.execute(
        f"SELECT id, agent_name, errors FROM agent_runs WHERE {where}",
        tuple(params),
    ).fetchall()
    if not rows:
        return 0

    was_in_tx = db.in_transaction
    marked = 0
    stale_error = f"marked stale by {source}: {reason}"
    for row in rows:
        errors = _decode_json_list(row["errors"])
        if stale_error not in errors:
            errors.append(stale_error)
        cur = db.execute(
            """
            UPDATE agent_runs
            SET status = ?, ended_at = ?, errors = ?
            WHERE id = ? AND status = ?
            """,
            ("stale", ended_at, json.dumps(errors, ensure_ascii=False), row["id"], "running"),
        )
        if cur.rowcount:
            marked += 1
            write_agent_run_log(
                {
                    "event": "run_marked_stale",
                    "run_id": row["id"],
                    "agent_name": row["agent_name"],
                    "ended_at": ended_at,
                    "reason": reason,
                }
            )
    if not was_in_tx:
        db.commit()

    if marked:
        write_audit(
            {
                "actor": "system",
                "source": source,
                "event_type": "agent_runs_marked_stale",
                "entity_type": "agent_run",
                "entity_id": "multiple",
                "before_state": {"status": "running", "count": marked},
                "after_state": {"status": "stale", "count": marked},
                "message": f"{marked} running agent run(s) marked stale: {reason}",
            }
        )
    return marked
