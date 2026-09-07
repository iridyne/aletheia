"""Agent run logging to SQLite + JSONL."""

from datetime import datetime, timezone, timedelta
import json
import logging

from aletheia.storage.models import (
    AgentArtifactRef,
    AgentRun,
    AgentRunArtifact,
    AgentRunStatus,
)
from aletheia.storage.repositories import (
    begin_agent_run,
    claim_agent_run,
    finish_agent_run,
    insert_agent_run_artifacts,
    mark_agent_run_stale,
    transaction,
)
from aletheia.storage.sqlite import get_db
from aletheia.files.jsonl import write_agent_run_log, write_audit

_logger = logging.getLogger("aletheia.agent_runtime.run_logger")


class AgentRunAlreadyActive(RuntimeError):
    def __init__(self, run_id: str, agent_name: str) -> None:
        self.run_id = run_id
        self.agent_name = agent_name
        super().__init__(f"agent '{agent_name}' already has an active run: {run_id}")


class AgentRunTransitionError(RuntimeError):
    pass


def _write_agent_run_log_best_effort(record: dict) -> bool:
    """Mirror lifecycle evidence without making the diagnostic sink authoritative."""
    try:
        write_agent_run_log(record)
    except Exception as exc:  # noqa: BLE001 - a secondary sink must not control lifecycle
        _logger.warning(
            "agent_run_diagnostic_mirror_failed event=%s error_type=%s",
            record.get("event", "unknown"),
            type(exc).__name__,
        )
        return False
    return True


def _create_run(
    agent_name: str,
    trigger_type: str,
    permission_mode: str,
    model_provider: str | None,
    *,
    status: AgentRunStatus,
    event: str,
) -> AgentRun:
    run = AgentRun(
        agent_name=agent_name,
        trigger_type=trigger_type,
        permission_mode=permission_mode,
        model_provider=model_provider,
        status=status,
    )
    existing_run_id = claim_agent_run(run)
    if existing_run_id is not None:
        raise AgentRunAlreadyActive(existing_run_id, agent_name)
    _write_agent_run_log_best_effort({"event": event, **run.model_dump()})
    return run


def start_run(agent_name: str, trigger_type: str, permission_mode: str, model_provider: str | None = None) -> AgentRun:
    try:
        from aletheia.economics.tracing import initialize_runtime_tracing

        initialize_runtime_tracing()
    except Exception:
        # Measurement initialization must never block an AgentRun. The missing
        # economics facts will make the later audit invalid rather than altering
        # runtime behavior.
        pass
    return _create_run(
        agent_name,
        trigger_type,
        permission_mode,
        model_provider,
        status="running",
        event="run_started",
    )


def queue_run(agent_name: str, trigger_type: str, permission_mode: str, model_provider: str | None = None) -> AgentRun:
    return _create_run(
        agent_name,
        trigger_type,
        permission_mode,
        model_provider,
        status="queued",
        event="run_queued",
    )


def begin_queued_run(run: AgentRun, *, permission_mode: str, model_provider: str | None) -> None:
    """Transition a pre-created manual job from queued to running."""

    if not begin_agent_run(
        run.id,
        permission_mode=permission_mode,
        model_provider=model_provider,
    ):
        raise AgentRunTransitionError(f"agent_run_begin_conflict:{run.id}")
    run.permission_mode = permission_mode
    run.model_provider = model_provider
    run.status = "running"
    _write_agent_run_log_best_effort({"event": "run_started", **run.model_dump()})


def end_run(
    run: AgentRun,
    status: AgentRunStatus = "completed",
    outputs: list[str] | None = None,
    errors: list[str] | None = None,
    artifact_refs: list[AgentArtifactRef] | None = None,
) -> None:
    try:
        from aletheia.agents.helpers import flush_run_telemetry

        flush_run_telemetry(run.id)
    except Exception:
        pass
    tools_called = _aggregate_tools_called(run.id)
    cost_summary = _aggregate_cost(run.id)
    ended_at = datetime.now(timezone.utc).isoformat()
    with transaction():
        if not finish_agent_run(
            run.id,
            expected_status=run.status,
            status=status,
            ended_at=ended_at,
            outputs_created=json.dumps(outputs if outputs is not None else []),
            errors=json.dumps(errors if errors is not None else []),
            tools_called=json.dumps(tools_called),
            # AgentRun.cost_estimate is populated only when every recorded event
            # is complete; the event ledger retains the known subtotal otherwise.
            cost_estimate=cost_summary.get("cost_estimate_usd"),
            model_name=cost_summary.get("final_model"),
        ):
            raise AgentRunTransitionError(f"agent_run_finish_conflict:{run.id}")
        insert_agent_run_artifacts([
            AgentRunArtifact(
                run_id=run.id,
                artifact_type=artifact.artifact_type,
                artifact_id=artifact.artifact_id,
            )
            for artifact in artifact_refs or []
        ])
    run.status = status
    run.ended_at = ended_at
    _write_agent_run_log_best_effort({
        "event": "run_ended",
        "run_id": run.id,
        "status": status,
        "cost_estimate_usd": cost_summary.get("cost_estimate_usd"),
        "known_cost_subtotal_usd": cost_summary.get("known_cost_subtotal_usd", 0.0),
        "cost_complete": cost_summary.get("cost_complete", False),
        "cost_event_count": cost_summary.get("event_count", 0),
    })


def _aggregate_cost(run_id: str) -> dict:
    try:
        from aletheia.economics.tracing import aggregate_agent_run_cost

        return aggregate_agent_run_cost(run_id)
    except Exception:
        return {
            "run_id": run_id,
            "event_count": 0,
            "known_cost_subtotal_usd": 0.0,
            "cost_estimate_usd": None,
            "cost_complete": False,
            "final_model": None,
        }


def _aggregate_tools_called(run_id: str) -> list[str]:
    """Return deduplicated tool names used during this run."""
    try:
        from aletheia.storage.repositories import list_tool_calls
        calls = list_tool_calls(run_id=run_id, limit=500)
        seen: dict[str, None] = {}
        for c in calls:
            name = c.get("tool_name") or ""
            if name and name not in seen:
                seen[name] = None
        return list(seen)
    except Exception:
        return []


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
    """Mark leftover `running` or `queued` agent rows as stale.

    A gateway restart can interrupt an agent between `start_run()` and
    `end_run()`. Leaving those rows as `running` makes the dashboard claim work is
    still active when no process owns it. This reconciliation closes only the run
    lifecycle row; it does not change trading, risk, execution, or outputs.
    """
    now = datetime.now(timezone.utc)
    ended_at = now.isoformat()
    where = "status IN (?, ?)"
    params: list[object] = ["running", "queued"]
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

    already_in_tx = db.in_transaction
    if not already_in_tx:
        db.execute("BEGIN")
    marked = 0
    mirror_records: list[dict] = []
    stale_error = f"marked stale by {source}: {reason}"
    try:
        for row in rows:
            errors = _decode_json_list(row["errors"])
            if stale_error not in errors:
                errors.append(stale_error)
            if mark_agent_run_stale(
                str(row["id"]),
                ended_at=ended_at,
                errors=json.dumps(errors, ensure_ascii=False),
            ):
                marked += 1
                mirror_records.append(
                    {
                        "event": "run_marked_stale",
                        "run_id": row["id"],
                        "agent_name": row["agent_name"],
                        "ended_at": ended_at,
                        "reason": reason,
                    }
                )
        if not already_in_tx:
            db.commit()
    except Exception:
        if not already_in_tx:
            db.rollback()
        raise

    for record in mirror_records:
        _write_agent_run_log_best_effort(record)

    if marked:
        write_audit(
            {
                "actor": "system",
                "source": source,
                "event_type": "agent_runs_marked_stale",
                "entity_type": "agent_run",
                "entity_id": "multiple",
        "before_state": {"status": "running_or_queued", "count": marked},
                "after_state": {"status": "stale", "count": marked},
                "message": (
                    f"{marked} running or queued agent run(s) marked stale: {reason}"
                ),
            }
        )
    return marked
