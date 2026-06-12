"""Research Worker — background execution of Serenity Research Runs.

Processes queued research runs by executing the dreaming_agent pipeline,
persisting artifacts stage-by-stage, and updating run status.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

from aletheia.storage import repositories as repo
from aletheia.storage.models import SerenityRunEvent

logger = logging.getLogger("aletheia.research_worker")

_EXECUTOR_LOCK = threading.Lock()


def enqueue_research_run(run_id: str) -> None:
    """Enqueue a research run for background execution.

    Uses a thread to avoid blocking the HTTP request. The thread runs
    asyncio.run() to execute the async pipeline.
    """
    def _execute():
        try:
            asyncio.run(_process_research_run(run_id))
        except Exception as e:
            logger.error("Research run %s crashed: %s", run_id, e)
            try:
                _mark_failed(run_id, "unknown_error", str(e), None)
            except Exception:
                pass

    t = threading.Thread(target=_execute, name=f"research-run-{run_id}", daemon=True)
    t.start()


async def _process_research_run(run_id: str) -> None:
    """Main execution pipeline for a research run."""
    run = repo.get_serenity_research_run(run_id)
    if not run:
        logger.error("Research run %s not found", run_id)
        return

    if run["status"] != "queued":
        logger.warning("Research run %s not in queued state (is %s), skipping", run_id, run["status"])
        return

    now = datetime.now(timezone.utc).isoformat()
    repo.update_serenity_research_run(run_id, status="running", started_at=now, updated_at=now)
    _emit_event(run_id, "run.started", message="Research run started")

    try:
        await _run_pipeline(run_id, run)
    except _RateLimitError as e:
        _handle_rate_limit(run_id, run, str(e))
    except Exception as e:
        logger.error("Research run %s failed at stage: %s", run_id, e)
        stage = run.get("stage")
        _mark_failed(run_id, _classify_error(e), str(e)[:500], stage)


async def _run_pipeline(run_id: str, run: dict) -> None:
    """Execute the research pipeline stages."""
    # Stage 1: Run the dreaming agent
    _update_stage(run_id, "dreaming_agent")
    _emit_event(run_id, "stage.started", stage="dreaming_agent", message="Starting dreaming agent")

    from aletheia.agents import get_agent_fn
    try:
        agent_fn = get_agent_fn("dreaming_agent")
        result = await agent_fn(
            permission_mode="Observe",
            run_id=run_id,
            trigger_type=run.get("trigger_type", "manual"),
        )
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "rate" in error_msg.lower():
            raise _RateLimitError(error_msg)
        raise

    outputs = result.get("outputs", [])
    errors = result.get("errors", [])

    _emit_event(run_id, "stage.completed", stage="dreaming_agent",
                message=f"Dreaming agent completed: {len(outputs)} outputs, {len(errors)} errors")

    if errors and not outputs:
        error_msg = "; ".join(errors[:3])
        if "429" in error_msg or "rate" in error_msg.lower():
            raise _RateLimitError(error_msg)
        _mark_failed(run_id, "provider_error", error_msg, "dreaming_agent")
        return

    # Stage 2: Count artifacts
    _update_stage(run_id, "persist_summary")
    themes = repo.list_serenity_themes(status=None, limit=1000)
    evidence = repo.list_serenity_evidence(limit=1000)

    counts = {
        "themes": len(themes),
        "evidence": len(evidence),
        "claims": 0,
        "opportunities": 0,
    }

    # Finalize
    now = datetime.now(timezone.utc).isoformat()
    final_status = "completed" if counts["themes"] > 0 else "completed_empty"
    repo.update_serenity_research_run(
        run_id,
        status=final_status,
        stage=None,
        finished_at=now,
        updated_at=now,
        artifact_counts=json.dumps(counts),
    )
    _emit_event(run_id, f"run.{final_status}",
                message=f"Run {final_status}: {counts['themes']} themes, {counts['evidence']} evidence")


def _update_stage(run_id: str, stage: str) -> None:
    repo.update_serenity_research_run(run_id, stage=stage,
                                      updated_at=datetime.now(timezone.utc).isoformat())


def _emit_event(run_id: str, event_type: str, stage: str | None = None,
                message: str | None = None) -> None:
    try:
        repo.insert_serenity_run_event(SerenityRunEvent(
            run_id=run_id, event_type=event_type, stage=stage, message=message))
    except Exception as e:
        logger.warning("Failed to emit event for run %s: %s", run_id, e)


def _mark_failed(run_id: str, error_type: str, error_message: str, error_stage: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    repo.update_serenity_research_run(
        run_id,
        status="failed",
        error_type=error_type,
        error_message=error_message[:500] if error_message else None,
        error_stage=error_stage,
        finished_at=now,
        updated_at=now,
    )
    _emit_event(run_id, "run.failed", stage=error_stage,
                message=f"{error_type}: {error_message[:200] if error_message else ''}")


def _handle_rate_limit(run_id: str, run: dict, error_message: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    retry_count = (run.get("retry_count") or 0)
    max_retries = 3 if run.get("trigger_type") == "manual" else 2

    if retry_count < max_retries:
        repo.update_serenity_research_run(
            run_id,
            status="rate_limited",
            error_type="provider_rate_limit",
            error_message=error_message[:500],
            error_stage=run.get("stage"),
            finished_at=now,
            updated_at=now,
        )
        _emit_event(run_id, "run.rate_limited",
                    message=f"Rate limited: {error_message[:200]}")
    else:
        repo.update_serenity_research_run(
            run_id,
            status="rate_limited",
            error_type="provider_rate_limit",
            error_message=f"Rate limited after {retry_count} retries: {error_message[:300]}",
            error_stage=run.get("stage"),
            finished_at=now,
            updated_at=now,
        )
        _emit_event(run_id, "run.rate_limited",
                    message=f"Rate limited, retries exhausted")


def _classify_error(e: Exception) -> str:
    msg = str(e).lower()
    if "429" in msg or "rate" in msg:
        return "provider_rate_limit"
    if "timeout" in msg:
        return "provider_timeout"
    if "auth" in msg or "401" in msg or "403" in msg:
        return "provider_auth_error"
    if "network" in msg or "connection" in msg:
        return "network_error"
    return "unknown_error"


class _RateLimitError(Exception):
    pass
