"""Research Agent — creates research notes and strategy hypotheses."""

from datetime import datetime, timedelta, timezone

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.knowledge.manager import create_research_note

_PREDICTION_CONTEXT_MAX_AGE_HOURS = 6


def _prediction_context_block() -> str:
    """Gather recent high-signal prediction market observations for LLM context."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.storage.repositories import list_prediction_claims
        cfg = load_config()
        if not cfg.market_data.prediction_enabled:
            return ""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=_PREDICTION_CONTEXT_MAX_AGE_HOURS)
        ).isoformat()
        claims = list_prediction_claims(
            claim_type="probability_momentum",
            since=cutoff,
            limit=5,
        )
        if not claims:
            return ""
        lines = ["## Prediction Market Catalyst Signals (observation-only, fresh)"]
        for c in claims[:5]:
            direction_str = f" [{c.get('direction', '')}]" if c.get("direction") else ""
            lines.append(f"- {c['claim_text']}{direction_str}")
        lines.append("")
        return "\n".join(lines)
    except Exception:
        return ""


@register_agent("research_agent")
async def run_research_agent(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Generate a research note using the model provider (graceful if unavailable)."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_research_note", permission_mode):
        errors.append("create_research_note not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    pred_context = _prediction_context_block()

    title = "Market Structure Analysis"
    system_prompt = (
        "You are a trading research analyst. Write a concise research note (markdown) on "
        "current crypto market structure for BTC/ETH/SOL: key concepts, a trading hypothesis, "
        "counterarguments, and a confidence level. Keep it under 300 words."
    )
    user_prompt = "Write a research note on current crypto market structure."
    if pred_context:
        user_prompt += f"\n\n{pred_context}\nIncorporate any relevant prediction market signals as catalysts in your analysis."

    content, err = await llm_complete(
        system=system_prompt,
        user=user_prompt,
        max_tokens=900,
    )
    log_tool(run_id, "llm_complete", "observe", True)
    if err:
        errors.append(err)
        content = (
            "Research note: model provider offline. Configure a provider in Settings to "
            "enable real analysis. This stub is recorded at speculative confidence and "
            "will not enter the retrieval gate until a substantive note replaces it."
        )

    item = create_research_note(title, content, confidence="speculative",
                                metadata={"source": "research_agent",
                                          "prediction_context_included": bool(pred_context)})
    log_tool(run_id, "create_research_note", "observe", item is not None,
             output=item.id if item else "quality_gate_rejected")
    if item is None:
        errors.append("create_research_note:quality_gate_rejected")
        return {"outputs": outputs, "errors": errors}
    outputs.append(f"research_note:{item.id}")

    return {"outputs": outputs, "errors": errors}
