"""AI Investment Committee — multi-agent explainable trading decisions.

Architecture:
    Market Data → 4 Agents (Trend/Macro/News/Risk) → Decision Core → Rule Engine → Report

Each agent outputs a structured AgentOpinion with direction, confidence, evidence,
and citations. The Decision Core aggregates opinions via weighted voting. The Rule
Engine applies deterministic checks. The result is a complete Decision Report that
explains WHY the decision was made.

Reference design: docs/design/proposals/explainable_ai_quant_design.md
"""

from aletheia.agents.committee.base import (
    AgentOpinion,
    DecisionReport,
    EvidenceItem,
    AgentDirection,
)
from aletheia.agents.committee.committee import InvestmentCommittee

__all__ = [
    "AgentOpinion",
    "DecisionReport",
    "EvidenceItem",
    "AgentDirection",
    "InvestmentCommittee",
]
