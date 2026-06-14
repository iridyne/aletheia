"""Knowledge Reference Router — builds advisory context blocks for AI agents.

Each agent gets tailored knowledge: relevant case studies, guard advisories,
and prior lessons. This module unifies retrieval into a single advisory block
that agents inject into their LLM prompts.

All output is ADVISORY — explicitly framed as suggestions, never instructions.
Cannot override risk limits or unlock live trading.
"""
from __future__ import annotations

import logging
from typing import Literal

from aletheia.knowledge.guards import evaluate_guards, format_guard_block, GuardEvaluation
from aletheia.knowledge.retrieval import retrieve_lessons, format_memory_block, Lesson

logger = logging.getLogger("aletheia.knowledge.router")

AgentRole = Literal["strategy_designer", "market_observer", "paper_trader"]

_ITEM_TYPES_BY_ROLE: dict[str, list[str]] = {
    "strategy_designer": ["trade_case_study", "research_note", "post_trade_review", "strategy_memory"],
    "market_observer": ["trade_case_study", "research_note", "post_trade_review", "dreaming_log"],
    "paper_trader": ["trade_case_study", "post_trade_review"],
}


def build_advisory_context(
    role: str,
    query: str,
    *,
    guard_context: dict | None = None,
    k: int = 3,
    max_chars: int = 2000,
) -> str:
    """Build a combined advisory block for an agent's LLM prompt.

    Components:
    1. Relevant lessons/case studies (FTS retrieval)
    2. Guard advisory block (if guard_context provided and guards match)

    Returns empty string when nothing relevant found (agent runs unaugmented).
    """
    parts: list[str] = []

    # Lesson retrieval
    lessons_block = _retrieve_lessons_safe(role, query, k)
    if lessons_block:
        parts.append(lessons_block)

    # Guard advisory (only advisory-level info for LLM context, not blocking)
    if guard_context:
        guard_block = _build_guard_advisory(guard_context)
        if guard_block:
            parts.append(guard_block)

    if not parts:
        return ""

    combined = "\n".join(parts)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n[...truncated]"
    return combined


def retrieve_for_role(role: str, query: str, k: int = 3) -> list[Lesson]:
    """Retrieve lessons relevant to a specific agent role."""
    item_types = _ITEM_TYPES_BY_ROLE.get(role, ["trade_case_study", "post_trade_review"])
    try:
        return retrieve_lessons(query, k=k, item_types=item_types)
    except Exception as e:
        logger.warning("retrieve_for_role(%s) failed: %s", role, e)
        return []


def _retrieve_lessons_safe(role: str, query: str, k: int) -> str:
    """Retrieve and format lessons. Returns empty string on failure."""
    try:
        lessons = retrieve_for_role(role, query, k)
        return format_memory_block(lessons)
    except Exception as e:
        logger.warning("Lesson retrieval failed for %s: %s", role, e)
        return ""


def _build_guard_advisory(context: dict) -> str:
    """Build advisory guard block for LLM prompt injection."""
    try:
        result = evaluate_guards(context, scope="paper_entry")
        if not result.hits:
            return ""
        header = (
            "\n## Active guard warnings (ADVISORY — scoring/informational only; "
            "hard blocks are applied in ranker, not here)\n"
        )
        return header + format_guard_block(result, max_chars=800)
    except Exception as e:
        logger.warning("Guard advisory build failed: %s", e)
        return ""
