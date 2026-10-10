"""Optional Research Lesson Reviewer — evidence-gated promotion of lessons.

The dreaming_agent mines proposed lessons nightly (ps_lessons, status='proposed'),
but nothing ever advanced them: `LessonStore.accept()` had no caller and
`applied_policy_version` was never written, so 246 lessons piled up unread
(content.lesson_chain doctor warn).

This agent closes the chain WITHOUT hard-coding low-dimensional symbol/direction
blocks (an explicit owner constraint — see negative-feedback design). Instead it
is a high-dimensional, evidence-based, fully-automatic, *overturnable* reviewer:

  Step A (overturn): re-judge every already-accepted/applied lesson; the LLM may
          demote (→ rejected) one its evidence no longer supports.
  Step B (promote):  judge each proposed lesson against the realized net outcomes
          of its own source trades (evidence_json.supporting_source_ids →
          trade_case_study → position → assess_realized_position). A
          deterministic floor (too few clean source trades → defer) prevents
          rubber-stamping in a data vacuum; the LLM then accepts only what the
          numbers support, rejects what they contradict, defers the ambiguous.
  Step C (apply):    fold the run's freshly-accepted lessons into one immutable
          `lesson_policy` version and stamp applied_policy_version on each, so the
          chain is provably closed (not a status stub).

Applied lessons reach the Entry Planner as advisory, citable context
(agent_shell/context.py::entry_lessons) and risk/engine._check_lesson_constraints,
which only enforces lessons that carry an explicit recommendation action.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from aletheia.agents import register_agent
from aletheia.agents.helpers import elapsed_ms, llm_structured, log_tool
from aletheia.model_provider.router import ProviderNotConfigured, get_provider_for_tier

logger = logging.getLogger("aletheia.agents.lesson_reviewer")

# Deterministic anti-rubber-stamp floor: a lesson needs this many of its own source
# trades with a clean realized net outcome before the judge sees it. Three is the
# smallest sample where one trade cannot decide the majority; on the 2026-10-10
# Fedora demo database 7 of the 142 proposed lessons reach it.
_MIN_SOURCE_TRADES = 3
# Bound LLM cost per run; the backlog drains over successive daily runs (logged).
_MAX_PROPOSED_PER_RUN = 40
# Bound LLM re-judgements of consumed lessons per run. The deterministic demote
# check still covers every consumed lesson each run, as Risk enforces every one.
_MAX_REJUDGED_PER_RUN = 200


class _LessonJudgment(BaseModel):
    decision: Literal["accept", "reject", "defer"]
    reasoning: str = ""


def _fetch_evidence(lesson) -> dict:
    """The lesson's own source trades and their realized net outcomes.

    ``evidence_json.supporting_source_ids`` name the trade_case_study items the
    lesson was extracted from; each names the closed position it reviews. Only
    positions the shared realized-outcome gate accepts count toward the sample.
    """
    from aletheia.analytics.outcome_semantics import assess_realized_position
    from aletheia.files.knowledge_document import read_knowledge_document
    from aletheia.storage import repositories as repo

    try:
        lesson_evidence = json.loads(lesson.evidence_json or "{}")
    except (TypeError, ValueError):
        lesson_evidence = {}
    if not isinstance(lesson_evidence, dict):
        lesson_evidence = {}
    trades: dict[str, dict] = {}
    excluded: dict[str, int] = {}
    for source_id in dict.fromkeys(lesson_evidence.get("supporting_source_ids") or ()):
        item = repo.get_knowledge_item(str(source_id))
        position_id = ""
        if item is not None and item.get("item_type") == "trade_case_study":
            document = read_knowledge_document(item)
            if document.state not in {"integrity_conflict", "unavailable"}:
                position_id = str(document.metadata.get("position_id") or "").strip()
        position = repo.get_position(position_id) if position_id else None
        if position is None:
            excluded["source_position_unavailable"] = excluded.get("source_position_unavailable", 0) + 1
            continue
        assessed = assess_realized_position(position)
        if not assessed.eligible or assessed.net_return_pct is None:
            excluded[assessed.reason] = excluded.get(assessed.reason, 0) + 1
            continue
        trades[position_id] = {
            "symbol": position.get("symbol"),
            "direction": position.get("direction"),
            "close_reason": position.get("close_reason"),
            "net_return_pct": round(assessed.net_return_pct, 3),
        }
    total = len(trades)
    wins = sum(1 for trade in trades.values() if trade["net_return_pct"] > 0)
    return {
        "total": total,
        "wins": wins,
        "win_rate": round(100.0 * wins / total, 1) if total else None,
        "avg_return": (
            round(sum(t["net_return_pct"] for t in trades.values()) / total, 3) if total else None
        ),
        "trades": list(trades.values()),
        "excluded": excluded,
    }


def _evidence_block(evidence: dict) -> str:
    lines = [
        "Realized net outcomes of the lesson's own source trades:",
        f"- total: {evidence['total']}, win_rate: {evidence['win_rate']}%, "
        f"avg_net_return: {evidence['avg_return']}%",
    ]
    lines.extend(
        f"- {t['symbol']} {t['direction']} close={t['close_reason']} net={t['net_return_pct']}%"
        for t in evidence["trades"]
    )
    if evidence["excluded"]:
        lines.append(f"Source trades excluded (no clean realized outcome): {evidence['excluded']}")
    return "\n".join(lines)


async def _judge(lesson, evidence: dict, run_id: str | None) -> tuple[str, str, bool]:
    """Return the decision, bounded reasoning, and whether inference failed.

    decision ∈ {accept, reject, defer}; defaults to 'defer' on any failure.
    """
    try:
        payload, err = await llm_structured(
            system=(
                "You are a skeptical trading-lesson evaluator. Decide whether a proposed "
                "lesson is supported by the realized outcomes of the trades it was drawn from. "
                "Be conservative: default to 'defer' unless the evidence clearly supports "
                "the lesson; choose 'reject' if the evidence contradicts it; choose "
                "'accept' only when the numbers back it. Do NOT accept on plausibility "
                "alone. Return ONLY a JSON object: "
                '{"decision": "accept"|"reject"|"defer", "reasoning": "<=200 chars"}.'
            ),
            user=(
                f"Lesson: {lesson.summary}\n"
                f"Category: {lesson.category}, Severity: {lesson.severity}\n\n"
                f"{_evidence_block(evidence)}\n\n"
                "Is this lesson empirically supported? Return the JSON."
            ),
            max_tokens=200,
            provider=get_provider_for_tier("deep"),
            caller="lesson_reviewer.judge",
            schema=_LessonJudgment,
            reasoning_effort="none",
        )
    except ProviderNotConfigured as exc:
        payload, err = None, f"llm_unavailable: {exc}"
    log_tool(run_id, "llm_judge_lesson", "observe", err is None)
    if err or payload is None:
        return "defer", str(err or "empty_payload")[:200], True
    return str(payload["decision"]), str(payload.get("reasoning") or "")[:200], False


def _rejudge_window(lesson_ids: list[str], now: datetime) -> set[str]:
    """Pick this run's LLM re-judgement slice, rotating daily over every lesson.

    The slice start advances by the cap each UTC day, so with N consumed lessons
    each one is re-judged at least once every ceil(N / cap) daily runs.
    """
    if len(lesson_ids) <= _MAX_REJUDGED_PER_RUN:
        return set(lesson_ids)
    start = (now.date().toordinal() * _MAX_REJUDGED_PER_RUN) % len(lesson_ids)
    rotated = lesson_ids[start:] + lesson_ids[:start]
    return set(rotated[:_MAX_REJUDGED_PER_RUN])


@register_agent("lesson_reviewer")
async def run_lesson_reviewer(
    permission_mode: str = "Observe",
    run_id: str | None = None,
    **kwargs,
) -> dict:
    """Evidence-gated, overturnable review of proposed lessons (daily)."""
    from extensions.learning.control import (
        advance_learning_job_cursor,
        get_learning_job_health,
    )
    from extensions.learning.lessons import LessonStore
    from extensions.policies.store import PolicyStore

    outputs: list[str] = []
    errors: list[str] = []
    artifact_refs: list[dict[str, str]] = []
    store = LessonStore()

    # ── Step A: overturn — re-judge already-consumed lessons against fresh evidence ──
    # #130: 'accepted' is transient (accept + mark_applied happen in the same run), so
    # the consumed stable state is 'applied'. Re-review BOTH, so an applied lesson with
    # contradicting evidence is demoted rather than left active until expiry.
    # #2865: Risk enforces every applied lesson (limit=-1), so the overturn pass
    # must reach every one too, not only the newest 100 of each status.
    demoted = 0
    _to_review = (store.list_all(status="accepted", limit=-1, oldest_first=True)
                  + store.list_all(status="applied", limit=-1, oldest_first=True))
    _judge_ids = _rejudge_window(
        [lesson.lesson_id for lesson in _to_review], datetime.now(timezone.utc),
    )
    for lesson in _to_review:
        try:
            evidence = _fetch_evidence(lesson)
        except Exception as e:  # evidence query must never crash the job
            errors.append(f"evidence_failed:{lesson.lesson_id}:{e}")
            continue
        if evidence["total"] < _MIN_SOURCE_TRADES:
            continue
        if lesson.lesson_id not in _judge_ids:
            continue
        decision, reason, judgment_failed = await _judge(lesson, evidence, run_id)
        if decision == "reject":
            started_at = time.monotonic()
            if not store.reject(
                lesson.lesson_id, expected_status=lesson.status,
            ):
                continue
            demoted += 1
            outputs.append(f"demoted:{lesson.lesson_id}")
            log_tool(
                run_id,
                "demote_lesson",
                "observe",
                True,
                output=f"{lesson.lesson_id}:{reason}",
                duration_ms=elapsed_ms(started_at),
            )
        elif judgment_failed:
            errors.append(f"judgment_failed:{lesson.lesson_id}:{reason}")

    # ── Step B: promote — judge proposed lessons ──
    # A deferred lesson stays proposed, so a fixed oldest-first head would re-read
    # the same batch forever. The durable created_at cursor resumes after the last
    # reviewed lesson and wraps to the oldest, so every proposed lesson is reached
    # within ceil(backlog / cap) runs while newer ones cannot starve older ones.
    proposed = store.list_all(status="proposed", limit=-1, oldest_first=True)
    backlog, _oldest_backlog_at = store.backlog_stats()
    cursor = get_learning_job_health("lesson_reviewer").input_cursor or ""
    batch = (
        [lesson for lesson in proposed if lesson.created_at > cursor]
        + [lesson for lesson in proposed if lesson.created_at <= cursor]
    )[:_MAX_PROPOSED_PER_RUN]
    newly_accepted: list[str] = []
    rejected = deferred = 0
    for lesson in batch:
        try:
            evidence = _fetch_evidence(lesson)
        except Exception as e:
            errors.append(f"evidence_failed:{lesson.lesson_id}:{e}")
            deferred += 1
            continue
        # Deterministic floor: no data → defer (never rubber-stamp in a vacuum).
        if evidence["total"] < _MIN_SOURCE_TRADES:
            deferred += 1
            continue
        decision, reason, judgment_failed = await _judge(lesson, evidence, run_id)
        if decision == "accept":
            started_at = time.monotonic()
            if not store.accept(lesson.lesson_id):
                continue
            newly_accepted.append(lesson.lesson_id)
            outputs.append(f"accepted:{lesson.lesson_id}")
            log_tool(
                run_id,
                "accept_lesson",
                "observe",
                True,
                output=f"{lesson.lesson_id}:{reason}",
                duration_ms=elapsed_ms(started_at),
            )
        elif decision == "reject":
            started_at = time.monotonic()
            if not store.reject(
                lesson.lesson_id, expected_status="proposed",
            ):
                continue
            rejected += 1
            outputs.append(f"rejected:{lesson.lesson_id}")
            log_tool(
                run_id,
                "reject_lesson",
                "observe",
                True,
                output=f"{lesson.lesson_id}:{reason}",
                duration_ms=elapsed_ms(started_at),
            )
        else:
            deferred += 1
            if judgment_failed:
                errors.append(f"judgment_failed:{lesson.lesson_id}:{reason}")

    if batch:
        advance_learning_job_cursor("lesson_reviewer", batch[-1].created_at)

    # ── Step C: apply — fold accepted lessons (this run + any stuck from prior runs) ──
    # GAP-04: a prior create_version failure leaves lessons stuck in 'accepted'; pick
    # them up here so a transient DB error doesn't permanently orphan a batch.
    stuck_accepted = [
        l.lesson_id for l in store.list_all(status="accepted", limit=100)
        if l.lesson_id not in set(newly_accepted)
    ]
    if stuck_accepted:
        outputs.append(f"retrying_stuck_accepted:{len(stuck_accepted)}")
    all_to_apply = newly_accepted + stuck_accepted
    skill_store = None
    if all_to_apply:
        applied_ids: list[str] = []
        try:
            started_at = time.monotonic()
            policy_store = PolicyStore()
            from aletheia.storage import repositories as repo

            with repo.transaction(immediate=True):
                accepted_ids = []
                for lesson_id in all_to_apply:
                    current = store.get(lesson_id)
                    if current is not None and current.status == "accepted":
                        accepted_ids.append(lesson_id)
                pending_ids = set(accepted_ids)
                pv = None
                versions = (
                    policy_store.list_versions("lesson_policy")
                    if accepted_ids else []
                )
                for version in versions:
                    version_ids = set(
                        json.loads(version.policy_json).get("accepted_lesson_ids") or []
                    )
                    if version.status != "active" or not pending_ids.issubset(version_ids):
                        continue
                    current_lessons = [store.get(lesson_id) for lesson_id in version_ids]
                    if all(
                        lesson is not None
                        and lesson.status in {"accepted", "applied"}
                        for lesson in current_lessons
                    ):
                        pv = version
                        break
                if pv is None and accepted_ids:
                    pv = policy_store.create_version(
                        policy_type="lesson_policy",
                        policy={
                            "accepted_lesson_ids": accepted_ids,
                            "reviewed_at": datetime.now(timezone.utc).isoformat(),
                            "basis": "realized net outcomes of each lesson's own source trades",
                        },
                        created_by="lesson_reviewer",
                    )
                if pv is not None:
                    applied_ids = [
                        lesson_id for lesson_id in accepted_ids
                        if store.mark_applied(lesson_id, pv.policy_version_id)
                    ]
            if pv is not None:
                outputs.append(
                    f"policy_version:{pv.policy_version_id}:{len(applied_ids)}_applied"
                )
                artifact_refs.append({
                    "artifact_type": "policy_version",
                    "artifact_id": pv.policy_version_id,
                })
                log_tool(
                    run_id,
                    "apply_lessons_to_policy",
                    "observe",
                    True,
                    output=f"{pv.policy_version_id}:{len(applied_ids)}",
                    duration_ms=elapsed_ms(started_at),
                )
        except Exception as e:
            errors.append(f"policy_version_create_failed:{e}")
            pv = None
        if pv is not None:
            from extensions.learning.skills import SkillStore

            skill_store = SkillStore()
            for lid in applied_ids:
                lesson = store.get(lid)
                if (
                    not lesson
                    or lesson.status != "applied"
                    or lesson.applied_policy_version != pv.policy_version_id
                ):
                    continue
                try:
                    started_at = time.monotonic()
                    skill = skill_store.create_from_lesson(
                        lesson,
                        policy_version_id=pv.policy_version_id,
                    )
                    if not skill:
                        errors.append(f"skill_create_failed:{lid}:missing_skill")
                        continue
                    outputs.append(f"draft_skill:{skill.skill_id}")
                    artifact_refs.append({"artifact_type": "draft_skill", "artifact_id": skill.skill_id})
                    log_tool(
                        run_id,
                        "create_draft_skill",
                        "observe",
                        True,
                        output=skill.skill_id,
                        duration_ms=elapsed_ms(started_at),
                    )
                except Exception as e:
                    errors.append(f"skill_create_failed:{lid}:{e}")
                    continue

    from extensions.learning.skills import SkillStore

    if skill_store is None:
        skill_store = SkillStore()
    recovered = 0
    for lesson in store.list_all(status="applied", limit=100):
        if not lesson.applied_policy_version:
            continue
        if skill_store.get_by_source_lesson_id(lesson.lesson_id):
            continue
        try:
            started_at = time.monotonic()
            skill = skill_store.create_from_lesson(
                lesson,
                policy_version_id=lesson.applied_policy_version,
            )
            if not skill:
                errors.append(f"skill_recovery_failed:{lesson.lesson_id}:missing_skill")
                continue
            recovered += 1
            outputs.append(f"recovered_draft_skill:{skill.skill_id}")
            artifact_refs.append({"artifact_type": "draft_skill", "artifact_id": skill.skill_id})
            log_tool(
                run_id,
                "recover_missing_draft_skill",
                "observe",
                True,
                output=skill.skill_id,
                duration_ms=elapsed_ms(started_at),
            )
        except Exception as e:
            errors.append(f"skill_recovery_failed:{lesson.lesson_id}:{e}")
    if recovered:
        outputs.append(f"recovered_missing_draft_skills:{recovered}")

    remaining = max(0, backlog - len(batch))
    logger.info(
        "lesson_reviewer: accepted=%d rejected=%d deferred=%d demoted=%d "
        "(reviewed %d/%d proposed, %d remaining for next run)",
        len(newly_accepted), rejected, deferred, demoted, len(batch), backlog, remaining,
    )
    outputs.append(
        f"summary:accepted={len(newly_accepted)},rejected={rejected},"
        f"deferred={deferred},demoted={demoted},remaining={remaining}"
    )
    return {"outputs": outputs, "errors": errors, "output_artifacts": artifact_refs}
