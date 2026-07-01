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

AgentRole = Literal["strategy_designer", "market_observer", "equity_observer", "paper_trader"]

# post_trade_review is deliberately EXCLUDED from every role's advisory retrieval.
# It is the hallucination-prone source identified in LEARNING_LOOP_AUDIT_2026-06-26
# (reviews of paper fills graded "execution quality" with no real execution to grade);
# B1 already removed it from the ranker's _lesson_penalty, and C1 downgraded new reviews
# to medium_confidence. This list closes the symmetric gap on the *advisory* path:
# build_advisory_context (live in market_observer + strategy_designer) must not inject
# post_trade_review lessons into agent prompts. trade_case_study (full-chain, has the R0
# attribution) is the trustworthy lesson source. Re-adding post_trade_review here is a
# regression (locked by test_knowledge_router::test_no_role_retrieves_post_trade_review).
_ITEM_TYPES_BY_ROLE: dict[str, list[str]] = {
    "strategy_designer": ["trade_case_study", "research_note", "strategy_memory"],
    "market_observer": ["trade_case_study", "research_note", "dreaming_log", "trading_guard"],
    # equity_observer uses equity-context knowledge: sector rotation, earnings events,
    # relative strength patterns. Query terms should use equity vocabulary (not crypto).
    "equity_observer": ["research_note", "trade_case_study"],
    "paper_trader": ["trade_case_study", "trading_guard"],
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
    item_types = _ITEM_TYPES_BY_ROLE.get(role, ["trade_case_study"])
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


def build_guard_advisory(guard_context: dict) -> str:
    """Public: guard-only advisory block for one (symbol, scanner_family[, direction]).

    market_observer must build this PER-SYMBOL — guards match on symbol/
    scanner_family/direction, which the run-level lesson block can't carry (it was
    built once from a symbol LIST with no family, so guards never matched — 断口F).
    Returns "" when no guard applies.
    """
    return _build_guard_advisory(guard_context)


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
