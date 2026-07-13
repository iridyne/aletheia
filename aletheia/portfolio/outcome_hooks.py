"""Durable delivery and replay for side effects of eligible Position outcomes."""

from __future__ import annotations

import logging

from aletheia.analytics.outcome_integrity import (
    assess_reportable_outcome,
    attribution_chain_complete,
)
from aletheia.storage import repositories as repo


logger = logging.getLogger("aletheia.portfolio.outcome_hooks")

SHADOW_TRADES_HOOK = "shadow_trades"
SERENITY_POST_MORTEM_HOOK = "serenity_post_mortem"


def enqueue_outcome_hook_jobs(position: dict) -> list[dict]:
    """Persist every applicable hook intent inside the caller's close transaction."""
    jobs = [
        repo.enqueue_outcome_hook_job(str(position["id"]), SHADOW_TRADES_HOOK),
    ]
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
    prospective = {
        **position,
        "_attribution_complete": attribution_chain_complete(position),
    }
    assessment = assess_reportable_outcome(prospective)
    if not assessment.eligible:
        return None, assessment.reason
    return prospective, "eligible"


def _hook_applicability_reason(hook_type: str, position: dict) -> str | None:
    if hook_type == SHADOW_TRADES_HOOK:
        return None
    if hook_type == SERENITY_POST_MORTEM_HOOK:
        if (
            position.get("trade_school") != "serenity"
            or not position.get("opportunity_id")
        ):
            return "hook_not_applicable"
        return None
    return "unsupported_hook_type"


def _execute_hook(hook_type: str, position: dict) -> None:
    if hook_type == SHADOW_TRADES_HOOK:
        from aletheia.analytics.shadow_trades import (
            resolve_shadow_trades_for_position,
        )

        resolve_shadow_trades_for_position(position)
        return
    if hook_type == SERENITY_POST_MORTEM_HOOK:
        from aletheia.portfolio.pnl import _close_serenity_feedback

        realized = position.get("realized_pnl")
        if realized is None:
            raise ValueError("Serenity feedback requires realized economics")
        _close_serenity_feedback(
            position,
            float(realized),
            str(position.get("close_reason") or "verified_exit"),
        )
        return
    raise ValueError(f"unsupported Outcome hook type: {hook_type}")
