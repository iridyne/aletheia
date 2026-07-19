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
  Step B (promote):  judge each proposed lesson against clean, cost-aware settled
          outcomes matching every populated symbol/direction/scanner dimension.
          System-wide aggregates are context only and never satisfy the gate.
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
_LONG_RE = re.compile(r"\blongs?\b", re.IGNORECASE)
_SHORT_RE = re.compile(r"\bshorts?\b", re.IGNORECASE)


def _fetch_evidence(lesson) -> dict:
    """Build the settled-outcome evidence pack for a lesson.

    Global figures are context only. Eligibility is decided exclusively by the
    clean, cost-aware 24h outcomes matching every populated scope dimension that
    can be recovered deterministically from the lesson.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()

    def _agg(where_extra: str, params: tuple) -> dict:
        from aletheia.learning.outcomes import assess_candidate_outcome

        rows = db.execute(
            "SELECT id,outcome_candidate_key,status,horizon,entry_ts,direction,"
            "net_return_pct,min_tradable_edge_pct,cost_model_version "
            "FROM universe_forward_outcomes "
            "WHERE status='settled' AND horizon='24h' "
            "AND settled_at >= datetime('now','-30 days') " + where_extra,
            params,
        ).fetchall()
        eligible_by_outcome: dict[str, tuple[dict, float]] = {}
        for raw in rows:
            row = dict(raw)
            assessed = assess_candidate_outcome(row)
            if (
                not assessed.eligible
                or assessed.net_return_pct is None
                or not assessed.outcome_key
            ):
                continue
            eligible_by_outcome.setdefault(
                assessed.outcome_key, (row, assessed.net_return_pct),
            )
        eligible = list(eligible_by_outcome.values())
        total = len(eligible)
        wins = sum(
            1 for row, net in eligible
            if net > float(row.get("min_tradable_edge_pct") or 0.0)
        )
        avg_return = (
            sum(net for _, net in eligible) / total if total else None
        )
        return {
            "total": total,
            "wins": wins,
            "win_rate": round(100.0 * wins / total, 1) if total else None,
            "avg_return": round(avg_return, 3) if avg_return is not None else None,
        }

    evidence = {"aggregate": _agg("", ())}

    def _json_dict(raw: str | None) -> dict:
        try:
            value = json.loads(raw or "{}")
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}

    rec = _json_dict(lesson.recommendation_json)
    ev = _json_dict(lesson.evidence_json)

    # Recover a symbol if the lesson carries one.
    symbol = None
    symbol = rec.get("symbol") or ev.get("symbol")
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
    scanner = rec.get("scanner") or rec.get("scanner_family")
    scanner = scanner or ev.get("scanner") or ev.get("scanner_family")
    if not scanner:
        families = ev.get("scanner_families")
        if isinstance(families, list) and len(families) == 1:
            scanner = families[0]
    evidence["scanner_family"] = scanner
    if scanner:
        evidence["scanner_family_stats"] = _agg("AND scanner_family = ?", (scanner,))

    direction = rec.get("direction") or ev.get("direction")
    if not direction and lesson.summary:
        if _LONG_RE.search(lesson.summary):
            direction = "long"
        elif _SHORT_RE.search(lesson.summary):
            direction = "short"
    direction = str(direction).lower() if direction else None
    if direction not in {"long", "short"}:
        direction = None
    evidence["direction"] = direction
    if direction:
        evidence["direction_stats"] = _agg("AND direction = ?", (direction,))

    scope_clauses: list[str] = []
    scope_params: list[str] = []
    if symbol:
        scope_clauses.append("AND symbol = ?")
        scope_params.append(symbol)
    if direction:
        scope_clauses.append("AND direction = ?")
        scope_params.append(direction)
    if scanner:
        scope_clauses.append("AND scanner_family = ?")
        scope_params.append(scanner)
    evidence["exact_scope_dimensions"] = {
        key: value for key, value in {
            "symbol": symbol, "direction": direction, "scanner_family": scanner,
        }.items() if value
    }
    evidence["exact_scope"] = (
        _agg(" ".join(scope_clauses), tuple(scope_params))
        if scope_clauses else {"total": 0, "wins": 0, "win_rate": None, "avg_return": None}
    )
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
    if evidence.get("direction"):
        ds = evidence.get("direction_stats") or {}
        lines.append(
            f"Direction {evidence['direction']}: total: {ds.get('total')}, "
            f"win_rate: {ds.get('win_rate')}%, avg_return: {ds.get('avg_return')}%"
        )
    exact = evidence.get("exact_scope") or {}
    lines.append(
        f"Exact populated scope {evidence.get('exact_scope_dimensions') or {}}: "
        f"total: {exact.get('total')}, win_rate: {exact.get('win_rate')}%, "
        f"avg_return: {exact.get('avg_return')}%"
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
    """A clearly failed exact-scope basis demotes regardless of LLM."""
    s = evidence.get("exact_scope")
    if not s or not s.get("total"):
        return False
    win_rate = s.get("win_rate")
    return (
        s["total"] >= _DEMOTE_MIN_SAMPLES
        and win_rate is not None
        and win_rate < _DEMOTE_WIN_RATE
    )


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
        if evidence["exact_scope"]["total"] < _MIN_SETTLED_EVIDENCE:
            continue
        decision, reason = await _judge(lesson, evidence, run_id)
        if decision == "reject":
            store.reject(lesson.lesson_id)
            demoted += 1
            outputs.append(f"demoted:{lesson.lesson_id}")
            log_tool(run_id, "demote_lesson", "observe", True, output=f"{lesson.lesson_id}:{reason}")

    # ── Step B: promote — judge proposed lessons ──
    # Oldest-first prevents a busy producer from starving long-waiting lessons.
    proposed = store.list_all(status="proposed", limit=200, oldest_first=True)
    backlog, _oldest_backlog_at = store.backlog_stats()
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
        if evidence["exact_scope"]["total"] < _MIN_SETTLED_EVIDENCE:
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
    if all_to_apply:
        try:
            policy_store = PolicyStore()
            pv = policy_store.create_version(
                policy_type="lesson_policy",
                policy={
                    "accepted_lesson_ids": all_to_apply,
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                    "basis": "settled outcomes, last 30d, 24h horizon",
                },
                created_by="lesson_reviewer",
            )
            outputs.append(f"policy_version:{pv.policy_version_id}:{len(all_to_apply)}_applied")
            log_tool(run_id, "apply_lessons_to_policy", "observe", True,
                     output=f"{pv.policy_version_id}:{len(all_to_apply)}")
        except Exception as e:
            errors.append(f"policy_version_create_failed:{e}")
            pv = None
        if pv is not None:
            skill_store = None
            for lid in all_to_apply:
                try:
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
                except Exception as e:
                    errors.append(f"mark_applied_failed:{lid}:{e}")

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
