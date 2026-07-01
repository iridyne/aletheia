"""Dreaming Agent — consolidates daily activity into learning summaries."""

import json
import logging

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.model_provider.router import get_provider_for_tier
from aletheia.knowledge.manager import create_daily_review, create_dreaming_log
from aletheia.storage.repositories import list_agent_runs

logger = logging.getLogger("aletheia.agents.dreaming_agent")

# Lifecycle states surfaced in the ledger, in pipeline order.
_LEDGER_STATES = [
    "draft", "research_note", "strategy_hypothesis", "paper_candidate",
    "paper_running", "paper_passed", "live_candidate", "live_limited",
    "retired", "deprecated",
]
# Knowledge item types that signal learning output.
_LEDGER_KNOWLEDGE = ["research_note", "strategy_memory", "post_trade_review", "dreaming_log"]


def _build_learning_ledger() -> str:
    """A real snapshot of where the self-evolution pipeline stands.

    Two parts:
      1. Counts (volume): are agents producing strategies/knowledge at all?
      2. Quality (signal): is what they produce verifiable hypotheses, or noise?

    The quality block is north-star §7 stage-1 evidence — before closing the
    experience loop end-to-end we need to see learning *converge*, not just *write*.
    """
    from aletheia.storage.repositories import list_strategies, list_knowledge_items

    strategies = list_strategies(limit=500)
    counts = {s: 0 for s in _LEDGER_STATES}
    for row in strategies:
        st = row.get("state") or "draft"
        counts[st] = counts.get(st, 0) + 1
    active_states = [f"{s}:{counts[s]}" for s in _LEDGER_STATES if counts.get(s)]
    strat_line = ", ".join(active_states) if active_states else "none yet"

    know_parts = []
    know_active_total = 0
    for t in _LEDGER_KNOWLEDGE:
        try:
            items = list_knowledge_items(t, limit=1000)
            active_items = [k for k in items if k.get("status", "active") == "active"]
            n = len(active_items)
        except Exception:
            n = 0
        know_active_total += n
        if n:
            know_parts.append(f"{t}:{n}")
    know_line = ", ".join(know_parts) if know_parts else "none yet"

    # --- Quality block ---
    total = len(strategies) or 1  # avoid div-by-zero
    activated = sum(counts.get(s, 0) for s in
                    ("paper_candidate", "paper_running", "paper_passed",
                     "live_candidate", "live_limited"))
    retired = counts.get("retired", 0) + counts.get("deprecated", 0)
    activation_ratio = round(activated / total, 3)
    retire_ratio = round(retired / total, 3)

    # post_trade_review coverage: of strategies that produced trades, how many got reviewed?
    try:
        reviews = list_knowledge_items(item_type="post_trade_review", limit=1000)
        reviewed_strategy_ids = {
            r.get("strategy_id") for r in reviews
            if r.get("strategy_id") and (r.get("status") or "active") == "active"
        }
    except Exception:
        reviewed_strategy_ids = set()
    eligible_strategies = [s for s in strategies if s.get("state") in
                           ("paper_running", "paper_passed", "retired", "deprecated")]
    if eligible_strategies:
        review_coverage = round(
            sum(1 for s in eligible_strategies if s["id"] in reviewed_strategy_ids)
            / len(eligible_strategies), 3
        )
    else:
        review_coverage = None

    # Lessons-consulted: how often the experience loop actually MOVED information
    # in 24h. Counts retrieve_lessons calls that returned >=1 lesson, parsed from
    # the logged "lessons:N" output — NOT the bare call count. retrieve_lessons is
    # logged every run even when the FTS gate is empty and it returns lessons:0, so
    # counting calls read non-zero while zero lessons reached a decision — the
    # ROADMAP "false confidence" mode this very metric exists to expose. Playbook
    # reads are reported separately: load_playbooks logs no count (and reads seeded
    # files, not FTS), so it cannot be hit-filtered and must not borrow the lesson
    # signal's credibility.
    lessons_consulted = 0
    playbook_reads = 0
    try:
        from aletheia.storage.repositories import list_tool_calls
        recent_calls = list_tool_calls(limit=2000)
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        def _fresh(c) -> bool:
            return (c.get("created_at") or "") >= cutoff

        def _lesson_hits(c) -> int:
            out = c.get("output_data") or ""
            if out.startswith("lessons:"):
                try:
                    return int(out.split(":", 1)[1])
                except ValueError:
                    return 0
            return 0

        lessons_consulted = sum(
            1 for c in recent_calls
            if c.get("tool_name") == "retrieve_lessons" and _fresh(c) and _lesson_hits(c) > 0
        )
        playbook_reads = sum(
            1 for c in recent_calls
            if c.get("tool_name") == "load_playbooks" and _fresh(c)
        )
    except Exception:
        lessons_consulted = 0
        playbook_reads = 0

    coverage_line = (f"{review_coverage:.0%}" if review_coverage is not None
                     else "n/a (no strategies past paper yet)")

    # --- Strategy Factory metrics (meta-evaluation) ---
    factory_block = ""
    try:
        from aletheia.strategy.performance_ledger import (
            compute_ledger_trend, load_ledger_history,
        )
        history = load_ledger_history(max_entries=2)
        if history:
            latest = history[-1]
            trend = compute_ledger_trend()
            trend_label = "IMPROVING" if trend.improving else ("STAGNANT" if trend.stagnant else "NEUTRAL")
            factory_block = (
                "\n### Strategy Factory (is the brain getting smarter?)\n"
                f"- Compile rate: {latest.compile_rate_pct:.0f}% "
                f"({latest.strategies_with_ruleset}/{latest.strategies_created} executable)\n"
                f"- Survival rate: {latest.survival_rate_pct:.0f}% "
                f"(positive expectancy after costs)\n"
                f"- Kill rate: {latest.kill_rate_pct:.0f}% "
                f"(system decisiveness)\n"
                f"- Time to verdict: {latest.avg_time_to_verdict_hours:.1f}h avg\n"
                f"- Trend: **{trend_label}**\n"
            )
            if trend.signals:
                for s in trend.signals[:4]:
                    factory_block += f"  - {s}\n"
    except Exception:
        pass

    return (
        "\n\n## Learning Ledger\n"
        f"- Strategies by state: {strat_line}\n"
        f"- Knowledge items: {know_line}\n"
        f"- Total strategies: {len(strategies)}\n"
        "\n### Quality (stage-1 evidence)\n"
        f"- Activation ratio: {activation_ratio:.0%} "
        f"(strategies that reached paper_running or beyond)\n"
        f"- Retire/deprecate ratio: {retire_ratio:.0%} "
        "(noise being filtered — non-zero is healthy)\n"
        f"- Post-trade review coverage: {coverage_line}\n"
        f"- Lessons consulted (24h): {lessons_consulted} "
        "(retrieve_lessons calls returning >=1 lesson — real FTS retrieval, not bare calls)\n"
        f"- Playbook reads (24h): {playbook_reads} (load_playbooks calls)\n"
        + factory_block
    )


async def _extract_lessons(run_id: str | None, outputs: list[str], errors: list[str]) -> None:
    """Extract structured lessons from recent trade_case_study items into ps_lessons.

    C4: reads trade_case_study (real closed-trade PnL) not post_trade_review, which was
    reclassified to medium_confidence and contained LLM-generated narrative, not signal.
    """
    from datetime import datetime, timezone, timedelta
    from aletheia.storage.repositories import list_knowledge_items
    from aletheia.learning.lessons import LessonStore

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    reviews = [
        r for r in list_knowledge_items(item_type="trade_case_study", limit=50)
        if (r.get("created_at") or "") >= cutoff
    ]
    if not reviews:
        return

    # Build rich case lines including PnL metrics so the LLM has something
    # concrete to summarise and the evidence_json is populated for lesson_reviewer.
    case_lines = []
    for r in reviews[:10]:
        from aletheia.knowledge.frontmatter import parse_frontmatter
        from pathlib import Path
        meta: dict = {}
        fpath = r.get("file_path")
        if fpath:
            try:
                meta, _ = parse_frontmatter(Path(fpath))
                meta = meta or {}
            except Exception:
                pass
        outcome = meta.get("outcome", "?")
        pnl = meta.get("realized_pnl_pct", "?")
        factor = meta.get("factor_key", "?")
        scanner = meta.get("scanner_family", "?")
        close = meta.get("close_reason", "?")
        case_lines.append(
            f"- [{r.get('title', 'untitled')}] outcome={outcome} pnl={pnl}% "
            f"factor={factor} scanner={scanner} close={close} strategy={r.get('strategy_id', '?')}"
        )
    summaries = "\n".join(case_lines)

    content, err = await llm_complete(
        system=(
            "You are a trading lesson extractor. Given recent trade case studies with "
            "quantitative metrics (outcome, realized_pnl_pct, factor_key, scanner_family, "
            "close_reason), extract 1-3 actionable lessons grounded in the data. "
            "Return a JSON array where each object has: "
            '"summary" (one sentence referencing specific metrics), '
            '"category" (one of: risk_sizing, entry_timing, exit_timing, symbol_selection, general), '
            '"severity" (info|warning|critical), '
            '"evidence" (object with fields: outcome_counts (dict), avg_pnl_pct (float or null), '
            'factor_keys (list of strings), scanner_families (list of strings)). '
            "Return ONLY the JSON array, no other text."
        ),
        user=f"Recent trade case studies:\n{summaries}\n\nExtract lessons with evidence.",
        max_tokens=600,
        provider=get_provider_for_tier("deep"),
    )
    log_tool(run_id, "llm_complete_lessons", "observe", True)
    if err:
        errors.append(f"lesson_extraction_llm_failed: {err}")
        return

    try:
        lessons = json.loads(content.strip().strip("`").removeprefix("json").strip())
    except (json.JSONDecodeError, ValueError):
        logger.warning("dreaming_agent: lesson extraction LLM returned non-JSON")
        return

    # Build aggregate evidence from the case study metadata directly — this gives
    # lesson_reviewer deterministic numbers even if the LLM skipped the evidence field.
    agg_evidence: dict = {}
    if reviews:
        from pathlib import Path
        from aletheia.knowledge.frontmatter import parse_frontmatter
        outcomes: list[str] = []
        pnls: list[float] = []
        factors: list[str] = []
        scanners: list[str] = []
        for r in reviews[:10]:
            fpath = r.get("file_path")
            if fpath:
                try:
                    meta, _ = parse_frontmatter(Path(fpath))
                    meta = meta or {}
                    if meta.get("outcome"):
                        outcomes.append(meta["outcome"])
                    if meta.get("realized_pnl_pct") is not None:
                        try:
                            pnls.append(float(meta["realized_pnl_pct"]))
                        except (ValueError, TypeError):
                            pass
                    if meta.get("factor_key"):
                        factors.append(meta["factor_key"])
                    if meta.get("scanner_family"):
                        scanners.append(meta["scanner_family"])
                except Exception:
                    pass
        outcome_counts: dict[str, int] = {}
        for o in outcomes:
            outcome_counts[o] = outcome_counts.get(o, 0) + 1
        agg_evidence = {
            "outcome_counts": outcome_counts,
            "avg_pnl_pct": round(sum(pnls) / len(pnls), 3) if pnls else None,
            "factor_keys": list(dict.fromkeys(factors)),
            "scanner_families": list(dict.fromkeys(scanners)),
            "case_count": len(reviews),
        }

    store = LessonStore()
    for lesson in lessons[:3]:
        if not isinstance(lesson, dict) or not lesson.get("summary"):
            continue
        # Merge LLM-supplied evidence with deterministic aggregate; deterministic wins.
        llm_evidence = lesson.get("evidence") or {}
        evidence = {**llm_evidence, **agg_evidence}
        created = store.create(
            summary=lesson["summary"],
            source_type="trade_case_study",
            source_id=reviews[0].get("id", "unknown"),
            category=lesson.get("category", "general"),
            severity=lesson.get("severity", "info"),
            evidence=evidence,
        )
        log_tool(run_id, "create_lesson", "observe", True, output=created.lesson_id)
        outputs.append(f"lesson:{created.lesson_id}")


@register_agent("dreaming_agent")
async def run_dreaming_agent(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Consolidate recent activity into a dreaming/learning log (guide §17.2)."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("write_dreaming_log", permission_mode):
        errors.append("write_dreaming_log not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    recent_runs = [
        r for r in list_agent_runs(limit=200)
        if (r.get("started_at") or "") >= cutoff
    ]
    log_tool(run_id, "get_recent_agent_runs", "observe", True)
    summary_lines = [f"- {r['agent_name']}: {r['status']} ({r.get('started_at', '')[:16]})" for r in recent_runs]
    activity = "\n".join(summary_lines) if summary_lines else "No recent agent activity."

    # Enrich context: recent post-trade post-mortems (serenity_post_mortems)
    post_mortem_ctx = ""
    try:
        from aletheia.storage.repositories import list_serenity_post_mortems
        mortems = list_serenity_post_mortems(limit=5)
        if mortems:
            pm_lines = [
                f"- [{m.get('created_at', '')[:10]}] {m.get('strategy_id', '?')}: "
                f"{m.get('summary', 'no summary')}"
                for m in mortems
            ]
            post_mortem_ctx = "\n\nRecent post-mortems:\n" + "\n".join(pm_lines)
    except Exception:
        pass

    content, err = await llm_complete(
        system="You are the system's nightly consolidation process. Given recent agent activity, "
               "produce a short markdown 'dreaming log': (1) what happened, (2) one insight or pattern, "
               "(3) one suggested focus for tomorrow. Keep it brief.",
        user=f"Recent agent activity:\n{activity}{post_mortem_ctx}\n\nWrite the dreaming log.",
        max_tokens=700,
        provider=get_provider_for_tier("deep"),
    )
    log_tool(run_id, "llm_complete", "observe", True)
    item = None
    if err:
        errors.append(err)
        logger.info("dreaming_agent: LLM unavailable, skipping dreaming_log write")
    else:
        # Append a real learning-progress snapshot. Never let it break the run.
        try:
            content += _build_learning_ledger()
            log_tool(run_id, "build_learning_ledger", "observe", True)
        except Exception as e:
            errors.append(f"learning_ledger_failed: {e}")

        item = create_dreaming_log("Daily Consolidation", content)
        if item:
            log_tool(run_id, "write_dreaming_log", "observe", True, output=item.id)
            outputs.append(f"dreaming_log:{item.id}")
        else:
            logger.info("dreaming_agent: dreaming_log skipped by quality/dedup gate")

    # Only write daily_review on cron-triggered runs (not manual CLI/API triggers)
    # to avoid duplicate content under different item_types.
    trigger = kwargs.get("trigger_type", "")
    if trigger in ("cron", "scheduler", ""):
        from aletheia.storage.repositories import list_knowledge_items
        from datetime import datetime, timezone, timedelta
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        existing_reviews = [
            r for r in list_knowledge_items(item_type="daily_review", limit=10)
            if r.get("created_at", "") >= today_start.isoformat()
        ]
        if not existing_reviews:
            review_body = content or ""
            try:
                review_body += _build_learning_ledger()
            except Exception as e:
                errors.append(f"learning_ledger_in_review_failed: {e}")
            review = create_daily_review("Daily Review", review_body)
            # create_daily_review returns None when the body fails the quality gate
            # (empty / too-short) — don't fabricate a write that didn't happen.
            if review is not None:
                log_tool(run_id, "write_daily_review", "observe", True, output=review.id)
                outputs.append(f"daily_review:{review.id}")
            else:
                log_tool(run_id, "write_daily_review", "observe", False,
                         output="skipped:quality_gate")

    # Lesson extraction: mine actionable lessons from today's post-trade reviews
    try:
        await _extract_lessons(run_id, outputs, errors)
    except Exception as e:
        errors.append(f"lesson_extraction_failed: {e}")

    # Trading-as-Git: snapshot the versioned data dirs (knowledge/playbooks/reports)
    # after the nightly consolidation. No-op when git_enabled is off; never raises.
    try:
        from aletheia.files.git import commit_all
        commit = commit_all(f"daily snapshot ({getattr(item, 'id', 'nightly')})")
        if commit.get("committed"):
            log_tool(run_id, "git_commit_all", "observe", True, output=commit.get("message"))
            outputs.append("git_snapshot:committed")
    except Exception as e:
        errors.append(f"git_snapshot_failed: {e}")

    return {"outputs": outputs, "errors": errors}
