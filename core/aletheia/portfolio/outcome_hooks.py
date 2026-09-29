"""Durable delivery and replay for side effects of eligible Position outcomes."""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
from datetime import datetime, timezone

from aletheia.analytics.outcome_integrity import (
    assess_reportable_outcome,
    attribution_chain_complete,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent


logger = logging.getLogger("aletheia.portfolio.outcome_hooks")

SERENITY_POST_MORTEM_HOOK = "serenity_post_mortem"
_INVALIDATION_FENCED_HOOKS = {SERENITY_POST_MORTEM_HOOK}

_feedback_executor: Callable[[str, dict], None] | None = None


def configure_outcome_feedback(executor: Callable[[str, dict], None] | None) -> None:
    """Bind optional feedback at bootstrap; Core retains every durable job transition."""
    global _feedback_executor
    _feedback_executor = executor


def enqueue_outcome_hook_jobs(position: dict) -> list[dict]:
    """Persist every applicable hook intent inside the caller's close transaction."""
    if position.get("execution_mode") == "paper_internal":
        return []
    jobs: list[dict] = []
    if position.get("trade_school") == "serenity" and position.get("opportunity_id"):
        jobs.append(repo.enqueue_outcome_hook_job(
            str(position["id"]),
            SERENITY_POST_MORTEM_HOOK,
        ))
    return jobs


def process_outcome_hook_jobs(
    *,
    position_id: str | None = None,
    hook_type: str | None = None,
    limit: int = 50,
    now: str | None = None,
    stale_after_seconds: int = 300,
) -> dict:
    """Lease and deliver due jobs, retaining every failed attempt for replay."""
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    errors: list[dict[str, str]] = []
    summary: dict[str, object] = {
        "claimed": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": errors,
    }

    for _ in range(limit):
        job = repo.claim_outcome_hook_job(
            position_id=position_id,
            hook_type=hook_type,
            now=now,
            stale_after_seconds=stale_after_seconds,
        )
        if job is None:
            break
        summary["claimed"] = int(summary["claimed"]) + 1
        claim_token = str(job.get("claim_token") or "")
        try:
            position, exclusion = _load_eligible_position(str(job["position_id"]))
            if position is None:
                if not repo.mark_outcome_hook_job_skipped(
                    str(job["id"]), claim_token, exclusion, now=now
                ):
                    raise RuntimeError("Outcome hook skip lost its lease")
                summary["skipped"] = int(summary["skipped"]) + 1
                continue

            applicability_reason = _hook_applicability_reason(
                str(job["hook_type"]), position
            )
            if applicability_reason is not None:
                if not repo.mark_outcome_hook_job_skipped(
                    str(job["id"]), claim_token, applicability_reason, now=now
                ):
                    raise RuntimeError("Outcome hook skip lost its lease")
                summary["skipped"] = int(summary["skipped"]) + 1
                continue

            if str(job["hook_type"]) in _INVALIDATION_FENCED_HOOKS:
                hook_error: Exception | None = None
                with repo.transaction(immediate=True):
                    current_job = repo._select_one("outcome_hook_jobs", str(job["id"]))
                    if (
                        current_job is None
                        or current_job.get("status") != "processing"
                        or current_job.get("claim_token") != claim_token
                    ):
                        summary["skipped"] = int(summary["skipped"]) + 1
                        continue
                    current, exclusion = _load_eligible_position(str(job["position_id"]))
                    if current is None:
                        if not repo.mark_outcome_hook_job_skipped(
                            str(job["id"]), claim_token, exclusion, now=now
                        ):
                            raise RuntimeError("Outcome hook skip lost its lease")
                        summary["skipped"] = int(summary["skipped"]) + 1
                        continue
                    try:
                        _execute_hook(str(job["hook_type"]), current)
                    except Exception as exc:
                        # Preserve the existing partial-shadow replay contract while
                        # keeping the writer fence through every derived write.
                        hook_error = exc
                    else:
                        if not repo.mark_outcome_hook_job_completed(
                            str(job["id"]), claim_token, now=now
                        ):
                            raise RuntimeError("Outcome hook completion lost its lease")
                if hook_error is not None:
                    raise hook_error
            else:
                _execute_hook(str(job["hook_type"]), position)
                if not repo.mark_outcome_hook_job_completed(
                    str(job["id"]), claim_token, now=now
                ):
                    raise RuntimeError("Outcome hook completion lost its lease")
            summary["completed"] = int(summary["completed"]) + 1
        except Exception as exc:
            attempts = max(1, int(job.get("attempts") or 1))
            retry_delay = min(3600, 60 * (2 ** min(attempts - 1, 6)))
            repo.mark_outcome_hook_job_failed(
                str(job["id"]),
                claim_token,
                f"{type(exc).__name__}: {exc}",
                now=now,
                retry_delay_seconds=retry_delay,
            )
            summary["failed"] = int(summary["failed"]) + 1
            errors.append({"job_id": str(job["id"]), "error": str(exc)})
            logger.warning(
                "Outcome hook %s failed for Position %s: %s",
                job.get("hook_type"),
                job.get("position_id"),
                exc,
            )
    return summary


def _load_eligible_position(position_id: str) -> tuple[dict | None, str]:
    position = repo._select_one("positions", position_id)
    if position is None:
        return None, "position_not_found"
    if position.get("execution_mode") == "paper_internal":
        return None, "archived_execution_mode"
    pending_attribution_recheck = (
        not bool(position.get("outcome_eligible"))
        and position.get("outcome_exclusion_reason") == "attribution_read_failed"
    )
    if pending_attribution_recheck:
        # This marker means the close-time reader failed; it is not evidence that
        # attribution is absent. Re-read and reclassify under one writer lock so
        # the stored eligibility and the causal backlinks are one audited fact.
        with repo.transaction(immediate=True):
            current = repo._select_one("positions", position_id)
            if current is None:
                return None, "position_not_found"
            if (
                not bool(current.get("outcome_eligible"))
                and current.get("outcome_exclusion_reason")
                == "attribution_read_failed"
            ):
                assessment_input = {
                    **current,
                    "outcome_eligible": True,
                    "outcome_exclusion_reason": "eligible",
                }
                prospective = {
                    **assessment_input,
                    "_attribution_complete": attribution_chain_complete(
                        assessment_input
                    ),
                }
                assessment = assess_reportable_outcome(prospective)
                repo.update_closed_position_settlement(
                    position_id,
                    outcome_eligible=assessment.eligible,
                    outcome_exclusion_reason=assessment.reason,
                )
                repo.insert_audit_event(AuditEvent(
                    actor="outcome_hook_replay",
                    source="outcome_attribution_recheck",
                    event_type="outcome_eligibility_rechecked",
                    entity_type="position",
                    entity_id=position_id,
                    before_state=json.dumps({
                        "outcome_eligible": False,
                        "outcome_exclusion_reason": "attribution_read_failed",
                    }, sort_keys=True),
                    after_state=json.dumps({
                        "outcome_eligible": assessment.eligible,
                        "outcome_exclusion_reason": assessment.reason,
                    }, sort_keys=True),
                    message=(
                        "Retried close-time attribution and persisted the durable "
                        f"Outcome eligibility result: {assessment.reason}"
                    ),
                ))
                current = {
                    **current,
                    "outcome_eligible": assessment.eligible,
                    "outcome_exclusion_reason": assessment.reason,
                }
                if not assessment.eligible:
                    return None, assessment.reason
                return prospective, "eligible"
            position = current

    prospective = {
        **position,
        "_attribution_complete": attribution_chain_complete(position),
    }
    assessment = assess_reportable_outcome(prospective)
    if not assessment.eligible:
        return None, assessment.reason
    return prospective, "eligible"


def _hook_applicability_reason(hook_type: str, position: dict) -> str | None:
    if hook_type == SERENITY_POST_MORTEM_HOOK:
        if (
            position.get("trade_school") != "serenity"
            or not position.get("opportunity_id")
        ):
            return "hook_not_applicable"
        return None
    return "unsupported_hook_type"


def _execute_hook(hook_type: str, position: dict) -> None:
    if _feedback_executor is None:
        raise RuntimeError("outcome_feedback_unavailable")
    _feedback_executor(hook_type, position)
