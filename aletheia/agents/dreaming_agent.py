"""Dreaming Agent — consolidates daily activity into learning summaries."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.knowledge.manager import create_daily_review, create_dreaming_log
from aletheia.storage.repositories import list_agent_runs

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

    # Lessons-consulted: how often the experience loop actually fired in 24h.
    # Signal that knowledge is being read back (not just piled up).
    try:
        from aletheia.storage.repositories import list_tool_calls
        recent_calls = list_tool_calls(limit=2000)
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        retrieval_calls = sum(
            1 for c in recent_calls
            if c.get("tool_name") in ("retrieve_lessons", "load_playbooks")
            and (c.get("created_at") or "") >= cutoff
        )
    except Exception:
        retrieval_calls = 0

    coverage_line = (f"{review_coverage:.0%}" if review_coverage is not None
                     else "n/a (no strategies past paper yet)")

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
        f"- Lessons consulted (24h): {retrieval_calls} "
        "(retrieve_lessons + load_playbooks calls; non-zero means the loop is closing)\n"
    )


@register_agent("dreaming_agent")
async def run_dreaming_agent(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
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
    log_tool(run_id, "get_recent_agent_runs", "read_tool", True)
    summary_lines = [f"- {r['agent_name']}: {r['status']} ({r.get('started_at', '')[:16]})" for r in recent_runs]
    activity = "\n".join(summary_lines) if summary_lines else "No recent agent activity."

    content, err = await llm_complete(
        system="You are the system's nightly consolidation process. Given recent agent activity, "
               "produce a short markdown 'dreaming log': (1) what happened, (2) one insight or pattern, "
               "(3) one suggested focus for tomorrow. Keep it brief.",
        user=f"Recent agent activity:\n{activity}\n\nWrite the dreaming log.",
        max_tokens=700,
    )
    log_tool(run_id, "llm_complete", "research_tool", True)
    if err:
        errors.append(err)
        content = f"## Daily Consolidation\n\nRecent activity:\n{activity}\n\n(LLM unavailable for synthesis.)"

    # Append a real learning-progress snapshot. Never let it break the run.
    try:
        content += _build_learning_ledger()
        log_tool(run_id, "build_learning_ledger", "read_tool", True)
    except Exception as e:
        errors.append(f"learning_ledger_failed: {e}")

    item = create_dreaming_log("Daily Consolidation", content)
    log_tool(run_id, "write_dreaming_log", "research_tool", True, output=item.id)
    outputs.append(f"dreaming_log:{item.id}")

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
            review = create_daily_review("Daily Review", content)
            log_tool(run_id, "write_daily_review", "research_tool", True, output=review.id)
            outputs.append(f"daily_review:{review.id}")

    # Trading-as-Git: snapshot the versioned data dirs (knowledge/playbooks/reports)
    # after the nightly consolidation. No-op when git_enabled is off; never raises.
    try:
        from aletheia.files.git import commit_all
        commit = commit_all(f"daily snapshot ({item.id})")
        if commit.get("committed"):
            log_tool(run_id, "git_commit_all", "read_tool", True, output=commit.get("message"))
            outputs.append("git_snapshot:committed")
    except Exception as e:
        errors.append(f"git_snapshot_failed: {e}")

    return {"outputs": outputs, "errors": errors}
