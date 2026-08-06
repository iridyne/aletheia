"""Risk Critic agent — reviews recent proposals/strategies and writes risk lessons."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.model_provider.router import get_provider_for_tier
from aletheia.knowledge.manager import create_research_note
from aletheia.storage.repositories import list_trade_proposals, list_risk_decisions


@register_agent("risk_critic")
async def run_risk_critic(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Critique recent risk decisions and record a risk-lesson note."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_research_note", permission_mode):
        errors.append("create_research_note not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    proposals = list_trade_proposals(limit=20)
    decisions = list_risk_decisions(limit=20)
    log_tool(run_id, "get_risk_boundaries", "observe", True)

    # C2: include per-rejection violation details so the LLM critique is actionable
    rejected = [d for d in decisions if not d.get("approved")]
    rejection_lines = []
    for d in rejected[:10]:
        try:
            import json as _json
            viols = _json.loads(d.get("violations") or "[]")
        except Exception:
            viols = [d.get("violations") or "unknown"]
        rejection_lines.append(f"  - proposal={d.get('proposal_id', '?')}: {', '.join(str(v) for v in viols)}")
    rejection_detail = "\n".join(rejection_lines) if rejection_lines else "  (none)"

    ctx = (
        f"Recent proposals: {len(proposals)}; recent risk decisions: {len(decisions)}. "
        f"Rejections: {len(rejected)}.\n"
        f"Rejection details:\n{rejection_detail}"
    )

    content, err = await llm_complete(
        system="You are a risk critic. Given recent proposal/risk-decision stats, identify risk "
               "patterns or lessons and recommend guardrails. You cannot change hard limits — only advise. "
               "Keep under 200 words.",
        user=f"{ctx}\n\nWrite a risk lesson note.",
        max_tokens=600,
        provider=get_provider_for_tier("deep"),
        caller="risk_critic.review",
    )
    log_tool(run_id, "llm_complete", "observe", True)
    if err:
        errors.append(err)
        return {"outputs": outputs, "errors": errors}

    # risk_critic writes are always medium_confidence — never entering the FTS
    # retrieval pool that feeds trading decisions. Reason (2026-07-18, follow-up
    # to the Phase C proposal-flow / FTS cleanup): the input is aggregate stats
    # ("Recent proposals: N; risk decisions: M") and the output is a macro
    # observation, not candidate-specific evidence a future trade decision could
    # apply. The prior gate ("promote when there are any proposals or decisions")
    # produced 39 vacuous "1 proposal reviewed, 1 approved (100%)" stubs at
    # high_confidence, polluting the experience loop. Real trading lessons
    # travel through `trade_case_study` (factor_key/holding_hours/pnl) instead;
    # this agent stays advisory. Kept as medium_confidence so the audit trail
    # exists but retrieve_lessons() cannot serve it to a decision prompt.
    confidence = "medium_confidence"
    item = create_research_note("Risk Lesson", content, confidence=confidence,
                                metadata={"source": "risk_critic", "subtype": "risk_lesson"})
    if item:
        log_tool(run_id, "create_research_note", "observe", True, output=item.id)
        outputs.append(f"research_note:{item.id}")

    return {"outputs": outputs, "errors": errors}
