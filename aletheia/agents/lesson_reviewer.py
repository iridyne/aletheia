"""Lesson Reviewer — evidence-gated, overturnable promotion of proposed lessons.

The dreaming_agent mines proposed lessons nightly (ps_lessons, status='proposed'),
but nothing ever advanced them: `LessonStore.accept()` had no caller and
`applied_policy_version` was never written, so 246 lessons piled up unread
(content.lesson_chain doctor warn).

This agent closes the chain WITHOUT hard-coding low-dimensional symbol/direction
blocks (an explicit owner constraint — see negative-feedback design). Instead it
is a high-dimensional, evidence-based, fully-automatic, *overturnable* reviewer:

  Step A (overturn): re-judge every already-accepted lesson against fresh settled
          evidence; demote (→ rejected) any the current evidence no longer
          supports. Later evidence can always overturn an earlier acceptance.
  Step B (promote):  judge each proposed lesson against the settled outcome record
          (system-wide aggregate + symbol-specific when the lesson carries one).
          A deterministic floor (insufficient settled data → defer) prevents
          rubber-stamping in a data vacuum; the LLM then accepts only what the
          numbers support, rejects what they contradict, defers the ambiguous.
  Step C (apply):    fold the run's freshly-accepted lessons into one immutable
          `lesson_policy` version and stamp applied_policy_version on each, so the
          chain is provably closed (not a status stub).

Accepted lessons flow to the PI decision context as higher-confidence advisory
(trading_context surfaces them) and to risk/engine._check_lesson_constraints —
which only enforces lessons that carry an explicit recommendation action, so a
plain accepted lesson is advisory, never an autonomous hard block.
"""

import json
import logging
import re
from datetime import datetime, timezone

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.model_provider.router import get_provider_for_tier

logger = logging.getLogger("aletheia.agents.lesson_reviewer")

# Deterministic anti-rubber-stamp floor: never promote a lesson when there is too
# little settled evidence to judge it against at all.
_MIN_SETTLED_EVIDENCE = 20
# Bound LLM cost per run; the backlog drains over successive daily runs (logged).
_MAX_PROPOSED_PER_RUN = 40
# Overturn guard: a clearly-failing symbol-specific basis demotes deterministically
# even if the LLM would keep it (belt-and-suspenders for the overturn requirement).
_DEMOTE_MIN_SAMPLES = 10
_DEMOTE_WIN_RATE = 35.0

_SYMBOL_RE = re.compile(r"\b([A-Z0-9]{2,12}/USDT)\b")


def _fetch_evidence(lesson) -> dict:
    """Build the settled-outcome evidence pack for a lesson.

    System-wide aggregate over the last 30 days at the 24h horizon, plus a
    symbol-specific slice when a symbol can be recovered from the lesson's
    recommendation/evidence JSON or its summary text. All counts come from
    universe_forward_outcomes (status='settled').
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()

    def _agg(where_extra: str, params: tuple) -> dict:
        row = db.execute(
            "SELECT COUNT(*) AS total, "
            "  SUM(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0) "
            "          THEN 1 ELSE 0 END) AS wins, "
            "  AVG(net_return_pct) AS avg_return "
            "FROM universe_forward_outcomes "
            "WHERE status='settled' AND horizon='24h' "
            "  AND net_return_pct IS NOT NULL "
            "  AND settled_at >= datetime('now','-30 days') " + where_extra,
            params,
        ).fetchone()
        total = row["total"] or 0
        wins = row["wins"] or 0
        return {
            "total": total,
            "wins": wins,
            "win_rate": round(100.0 * wins / total, 1) if total else None,
            "avg_return": round(row["avg_return"], 3) if row["avg_return"] is not None else None,
        }

    evidence = {"aggregate": _agg("", ())}

    # Recover a symbol if the lesson carries one.
    symbol = None
    try:
        rec = json.loads(lesson.recommendation_json or "{}")
        symbol = rec.get("symbol")
    except (ValueError, TypeError):
        rec = {}
    if not symbol:
        try:
            ev = json.loads(lesson.evidence_json or "{}")
            symbol = ev.get("symbol")
        except (ValueError, TypeError):
            pass
    if not symbol and lesson.summary:
        m = _SYMBOL_RE.search(lesson.summary)
        if m:
            symbol = m.group(1)

    evidence["symbol"] = symbol
    if symbol:
        evidence["symbol_stats"] = _agg("AND symbol = ?", (symbol,))

    # Scanner-family slice (#99): a block_scanner lesson must be judged against the
    # scanner-family's OWN settled outcomes, not just the global/symbol aggregates —
    # otherwise a family-specific block is approved on evidence that never looked at
    # that family. universe_forward_outcomes carries scanner_family.
    scanner = (rec or {}).get("scanner")
    if not scanner:
        try:
            ev = json.loads(lesson.evidence_json or "{}")
            scanner = ev.get("scanner") or ev.get("scanner_family")
        except (ValueError, TypeError):
            pass  # intentional: missing/malformed evidence_json -> no scanner slice
    evidence["scanner_family"] = scanner
    if scanner:
        evidence["scanner_family_stats"] = _agg("AND scanner_family = ?", (scanner,))
    return evidence


def _evidence_block(evidence: dict) -> str:
    agg = evidence["aggregate"]
    lines = [
        "System-wide settled outcomes (last 30d, 24h horizon):",
        f"- total: {agg['total']}, win_rate: {agg['win_rate']}%, avg_return: {agg['avg_return']}%",
    ]
    if evidence.get("symbol"):
        s = evidence.get("symbol_stats") or {}
        lines.append(
            f"Symbol {evidence['symbol']}: total: {s.get('total')}, "
            f"win_rate: {s.get('win_rate')}%, avg_return: {s.get('avg_return')}%"
        )
    if evidence.get("scanner_family"):
        sf = evidence.get("scanner_family_stats") or {}
        lines.append(
            f"Scanner family {evidence['scanner_family']}: total: {sf.get('total')}, "
            f"win_rate: {sf.get('win_rate')}%, avg_return: {sf.get('avg_return')}%"
        )
    return "\n".join(lines)


async def _judge(lesson, evidence: dict, run_id: str | None) -> tuple[str, str]:
    """Ask the LLM to judge a lesson against its evidence. Returns (decision, reasoning).

    decision ∈ {accept, reject, defer}; defaults to 'defer' on any failure.
    """
    content, err = await llm_complete(
        system=(
            "You are a skeptical trading-lesson evaluator. Decide whether a proposed "
            "lesson is supported by the recent settled-outcome evidence provided. "
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
    )
    log_tool(run_id, "llm_judge_lesson", "observe", err is None)
    if err:
        return "defer", f"llm_error:{err}"
    try:
        content_str = (content or "").strip().strip("`").removeprefix("json").strip()
        parsed = json.loads(content_str)
    except (json.JSONDecodeError, ValueError):
        return "defer", "unparseable_llm_response"
    decision = str(parsed.get("decision", "defer")).lower()
    if decision not in ("accept", "reject", "defer"):
        decision = "defer"
    return decision, str(parsed.get("reasoning", ""))[:200]


def _deterministic_demote(evidence: dict) -> bool:
    """A symbol-specific basis that has clearly failed demotes regardless of LLM."""
    s = evidence.get("symbol_stats")
    if not s or not s.get("total"):
        return False
    return s["total"] >= _DEMOTE_MIN_SAMPLES and (s.get("win_rate") or 100.0) < _DEMOTE_WIN_RATE


@register_agent("lesson_reviewer")
async def run_lesson_reviewer(
    permission_mode: str = "Observe",
    run_id: str | None = None,
    **kwargs,
) -> dict:
    """Evidence-gated, overturnable review of proposed lessons (daily)."""
    from aletheia.learning.lessons import LessonStore
    from aletheia.policies.store import PolicyStore

    outputs: list[str] = []
    errors: list[str] = []
    store = LessonStore()

    # ── Step A: overturn — re-judge already-consumed lessons against fresh evidence ──
    # #130: 'accepted' is transient (accept + mark_applied happen in the same run), so
    # the consumed stable state is 'applied'. Re-review BOTH, so an applied lesson with
    # contradictory settled evidence is demoted rather than left active until expiry.
    demoted = 0
    _to_review = (store.list_all(status="accepted", limit=100)
                  + store.list_all(status="applied", limit=100))
    for lesson in _to_review:
        try:
            evidence = _fetch_evidence(lesson)
        except Exception as e:  # evidence query must never crash the job
            errors.append(f"evidence_failed:{lesson.lesson_id}:{e}")
            continue
        if _deterministic_demote(evidence):
            store.reject(lesson.lesson_id)
            demoted += 1
            outputs.append(f"demoted:{lesson.lesson_id}")
            log_tool(run_id, "demote_lesson", "observe", True, output=lesson.lesson_id)
            continue
        decision, reason = await _judge(lesson, evidence, run_id)
        if decision == "reject":
            store.reject(lesson.lesson_id)
            demoted += 1
            outputs.append(f"demoted:{lesson.lesson_id}")
            log_tool(run_id, "demote_lesson", "observe", True, output=f"{lesson.lesson_id}:{reason}")

    # ── Step B: promote — judge proposed lessons ──
    proposed = store.list_all(status="proposed", limit=200)
    backlog = len(proposed)
    batch = proposed[:_MAX_PROPOSED_PER_RUN]
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
        if evidence["aggregate"]["total"] < _MIN_SETTLED_EVIDENCE:
            deferred += 1
            continue
        decision, reason = await _judge(lesson, evidence, run_id)
        if decision == "accept":
            store.accept(lesson.lesson_id)
            newly_accepted.append(lesson.lesson_id)
            outputs.append(f"accepted:{lesson.lesson_id}")
            log_tool(run_id, "accept_lesson", "observe", True, output=f"{lesson.lesson_id}:{reason}")
        elif decision == "reject":
            store.reject(lesson.lesson_id)
            rejected += 1
            outputs.append(f"rejected:{lesson.lesson_id}")
            log_tool(run_id, "reject_lesson", "observe", True, output=f"{lesson.lesson_id}:{reason}")
        else:
            deferred += 1

    # ── Step C: apply — fold this run's accepted lessons into one policy version ──
    if newly_accepted:
        try:
            policy_store = PolicyStore()
            skill_store = None
            pv = policy_store.create_version(
                policy_type="lesson_policy",
                policy={
                    "accepted_lesson_ids": newly_accepted,
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                    "basis": "settled outcomes, last 30d, 24h horizon",
                },
                created_by="lesson_reviewer",
            )
            for lid in newly_accepted:
                store.mark_applied(lid, pv.policy_version_id)
                try:
                    from aletheia.learning.skills import SkillStore

                    if skill_store is None:
                        skill_store = SkillStore()
                    lesson = store.get(lid)
                    if lesson:
                        skill = skill_store.create_from_lesson(
                            lesson,
                            policy_version_id=pv.policy_version_id,
                        )
                        if skill:
                            outputs.append(f"draft_skill:{skill.skill_id}")
                            log_tool(run_id, "create_draft_skill", "observe", True, output=skill.skill_id)
                except Exception as e:
                    errors.append(f"skill_create_failed:{lid}:{e}")
            outputs.append(f"policy_version:{pv.policy_version_id}:{len(newly_accepted)}_applied")
            log_tool(run_id, "apply_lessons_to_policy", "observe", True,
                     output=f"{pv.policy_version_id}:{len(newly_accepted)}")
        except Exception as e:
            errors.append(f"policy_apply_failed:{e}")

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
    return {"outputs": outputs, "errors": errors}
