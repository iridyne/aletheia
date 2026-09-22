"""Stable, additive role taxonomy for LLM-harness attribution.

The mapping classifies existing logical-agent names for telemetry only. It does
not grant permissions, change routing, or alter decision authority.
"""
from __future__ import annotations

from enum import StrEnum


class AgentRole(StrEnum):
    WORKER = "worker"
    EVALUATOR = "evaluator"
    COORDINATOR = "coordinator"
    SYSTEM_OPERATOR = "system_operator"
    UNKNOWN = "unknown"


_SYSTEM_OPERATOR_NAMES = frozenset({"system_operator", "pi_trading_decision"})
_EVALUATOR_NAMES = frozenset({"risk_critic", "lesson_reviewer", "post_trade_reviewer"})
_COORDINATOR_NAMES = frozenset({"orchestrator", "agent_runtime", "agent_shell"})
_WORKER_NAMES = frozenset({
    "market_observer", "equity_observer", "stock_observer", "divergence_scanner",
    "dreaming_agent", "paper_trader", "portfolio_agent", "prediction_observer",
    "research_agent", "research_worker", "news_analyzer", "insight_synthesizer",
})


def role_for_logical_agent(logical_agent: str | None) -> AgentRole:
    """Classify known attribution names; unknown names stay visibly unknown."""
    if logical_agent in _SYSTEM_OPERATOR_NAMES:
        return AgentRole.SYSTEM_OPERATOR
    if logical_agent in _EVALUATOR_NAMES:
        return AgentRole.EVALUATOR
    if logical_agent in _COORDINATOR_NAMES:
        return AgentRole.COORDINATOR
    if logical_agent in _WORKER_NAMES:
        return AgentRole.WORKER
    return AgentRole.UNKNOWN
