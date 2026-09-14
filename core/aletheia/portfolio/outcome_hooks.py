"""Durable delivery and replay for side effects of eligible Position outcomes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aletheia.storage import repositories as repo


logger = logging.getLogger("aletheia.portfolio.outcome_hooks")

SHADOW_TRADES_HOOK = "shadow_trades"
SERENITY_POST_MORTEM_HOOK = "serenity_post_mortem"
_INVALIDATION_FENCED_HOOKS = {SHADOW_TRADES_HOOK, SERENITY_POST_MORTEM_HOOK}


def enqueue_outcome_hook_jobs(position: dict) -> list[dict]:
    """Persist every applicable hook intent inside the caller's close transaction."""
    if position.get("execution_mode") == "paper_internal":
        return []
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
    from aletheia.portfolio.pnl import load_eligible_position_for_delivery
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
            position, exclusion = load_eligible_position_for_delivery(str(job["position_id"]))
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
                    current, exclusion = load_eligible_position_for_delivery(str(job["position_id"]))
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
        from aletheia.counterfactual.trades import (
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
