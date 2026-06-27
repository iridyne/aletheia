"""Post-trade Reviewer agent — reviews recent fills and writes a post-trade review.

Also triggers structured case study generation and guard candidate detection
from the trade_case_builder module (deterministic, no LLM).
"""

import logging

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.knowledge.manager import create_post_trade_review
from aletheia.storage.repositories import get_trade_proposal, get_order, list_fills, list_orders

logger = logging.getLogger("aletheia.agents.post_trade_reviewer")


@register_agent("post_trade_reviewer")
async def run_post_trade_reviewer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Review recent fills grouped by strategy and record per-strategy reviews."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_post_trade_review", permission_mode):
        errors.append("create_post_trade_review not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    fills = list_fills(limit=200)
    orders = list_orders(limit=200)
    log_tool(run_id, "get_positions", "observe", True)

    # Group fills by strategy_id
    strategy_fills: dict[str | None, list] = {}
    for fill in fills:
        order_id = fill.get("order_id")
        if not order_id:
            continue
        linked_order = next((o for o in orders if o["id"] == order_id), None)
        if not linked_order:
            continue
        proposal_id = linked_order.get("proposal_id")
        if not proposal_id:
            strategy_fills.setdefault(None, []).append(fill)
            continue
        proposal = get_trade_proposal(proposal_id)
        sid = proposal.get("strategy_id") if proposal else None
        strategy_fills.setdefault(sid, []).append(fill)

    if not strategy_fills:
        strategy_fills[None] = []

    for strategy_id, strat_fills in strategy_fills.items():
        # Enrich context with position entry/exit data and market context
        position_details = []
        for f in strat_fills[:10]:
            order = get_order(f.get("order_id")) if f.get("order_id") else None
            pos_id = order.get("position_id") if order else None
            if pos_id:
                from aletheia.storage.repositories import _select_one
                pos = _select_one("positions", pos_id)
                if pos:
                    detail = f"{pos.get('symbol', '?')} {pos.get('direction', '?')} entry={pos.get('entry_price')} exit={pos.get('current_price')} pnl={pos.get('realized_pnl', 'open')} regime={pos.get('regime_tag', '?')}"
                    entry_ctx = pos.get("entry_market_summary") or ""
                    if entry_ctx:
                        detail += f" entry_context={entry_ctx[:200]}"
                    position_details.append(detail)
        positions_block = "\n".join(position_details) if position_details else "No position details available."
        ctx = f"Strategy: {strategy_id or 'global'}. Fills: {len(strat_fills)}; orders: {len(orders)}.\n\nPosition Details:\n{positions_block}"

        content, err = await llm_complete(
            system=(
                "You are a post-trade reviewer for an autonomous crypto paper-trading system. "
                "You are given recent fills and their position outcomes (entry/exit price, realized "
                "PnL, market regime, entry context). Write a short review focused on SIGNAL QUALITY "
                "and DECISION-MAKING: was the direction correct, was the entry timing good, did the "
                "thesis play out, which pattern should be repeated or avoided next time. "
                "Judge the TRADE DECISION, not execution microstructure — this is a market-order "
                "system with no VWAP/smart-routing/slippage desk, so do NOT invent execution-quality "
                "commentary. If PnL is 'open', say the trade is unresolved rather than guessing. "
                "Under 200 words."
            ),
            user=f"{ctx}\n\nWrite a post-trade review.",
            max_tokens=600,
        )
        log_tool(run_id, "llm_complete", "observe", True)
        if err:
            errors.append(err)
            logger.info("post_trade_reviewer: LLM unavailable for strategy=%s, skipping write", strategy_id)
            continue

        # C1 (LEARNING_LOOP_AUDIT §R1): a post_trade_review is a SOFT LLM reflection,
        # not an attributed hard case. Keep it medium_confidence so the retrieval
        # quality gate (high_confidence) is led by trade_case_study — real closed-trade
        # cases that earn high via has_full_chain (now reachable after the R0
        # opportunity_id fix). Auto-upgrading every review to high flooded the pool
        # ~118:1 with templated reflections and starved real cases.
        confidence = "medium_confidence"
        item = create_post_trade_review(
            "Post-Trade Review",
            content,
            metadata={"source": "post_trade_reviewer"},
            strategy_id=strategy_id,
            confidence=confidence,
        )
        if item:
            log_tool(run_id, "create_post_trade_review", "observe", True, output=item.id)
            outputs.append(f"post_trade_review:{item.id}")

    # Phase 4: Structured case study generation + guard candidate detection
    case_studies, guard_candidates = _run_case_builder_safe(run_id)
    if case_studies:
        outputs.append(f"case_studies_created:{len(case_studies)}")
    if guard_candidates:
        outputs.append(f"guard_candidates_created:{len(guard_candidates)}")

    # P3 event chain: sufficient new lessons → trigger strategy_designer to adapt
    new_knowledge_count = len(case_studies) + len(guard_candidates)
    if new_knowledge_count >= 2:
        _trigger_strategy_adaptation(new_knowledge_count, run_id)
        outputs.append(f"strategy_designer_triggered:lessons={new_knowledge_count}")

    return {"outputs": outputs, "errors": errors}


def _trigger_strategy_adaptation(lesson_count: int, run_id: str | None) -> None:
    """Trigger strategy_designer to incorporate new lessons. Best-effort."""
    try:
        from aletheia.agent_runtime.scheduler import trigger_strategy_designer_on_demand
        fired = trigger_strategy_designer_on_demand()
        if fired:
            logger.info(
                "Strategy designer triggered after %d new lessons (review→design chain)",
                lesson_count,
            )
        else:
            logger.debug("Strategy designer trigger debounced (cooldown active)")
    except Exception as e:
        logger.debug("Strategy adaptation trigger failed (non-fatal): %s", e)


def _run_case_builder_safe(run_id: str | None) -> tuple[list, list]:
    """Run trade_case_builder deterministically. Never crashes the agent."""
    try:
        from aletheia.analytics.trade_case_builder import build_case_studies, detect_guard_candidates

        cases = build_case_studies(since_hours=168, skip_existing=True)
        log_tool(run_id, "build_case_studies", "observe", True, output=f"{len(cases)} created")

        candidates = detect_guard_candidates()
        log_tool(run_id, "detect_guard_candidates", "observe", True, output=f"{len(candidates)} created")

        return cases, candidates
    except Exception as e:
        logger.warning("Case builder failed (non-fatal): %s", e)
        return [], []
