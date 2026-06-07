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

    Answers "is learning producing validated strategies, or only piling up
    notes?" using existing repo queries — no new tables, no new page.
    """
    from aletheia.storage.repositories import list_strategies, list_knowledge_items

    strategies = list_strategies(limit=500)
    counts = {s: 0 for s in _LEDGER_STATES}
    for row in strategies:
        st = row.get("state") or "draft"
        counts[st] = counts.get(st, 0) + 1
    active = [f"{s}:{counts[s]}" for s in _LEDGER_STATES if counts.get(s)]
    strat_line = ", ".join(active) if active else "none yet"

    know_parts = []
    for t in _LEDGER_KNOWLEDGE:
        try:
            items = list_knowledge_items(t, limit=1000)
            active = [k for k in items if k.get("status", "active") == "active"]
            n = len(active)
        except Exception:
            n = 0
        if n:
            know_parts.append(f"{t}:{n}")
    know_line = ", ".join(know_parts) if know_parts else "none yet"

    return (
        "\n\n## Learning Ledger\n"
        f"- Strategies by state: {strat_line}\n"
        f"- Knowledge items: {know_line}\n"
        f"- Total strategies: {len(strategies)}\n"
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

    return {"outputs": outputs, "errors": errors}
