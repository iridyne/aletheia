"""Base types for the AI Investment Committee.

Every agent outputs an AgentOpinion — a structured, auditable decision
that cites specific data points.  The Decision Core aggregates these
into a final Decision Report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

AgentDirection = Literal["long", "short", "neutral"]


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence cited by an agent."""

    indicator: str  # e.g. "RSI", "MACD", "Fear&Greed"
    value: str  # e.g. "RSI 64.7 bullish"
    contribution: float  # signed score contribution, e.g. +15 or -8

    def to_dict(self) -> dict:
        return {
            "indicator": self.indicator,
            "value": self.value,
            "contribution": self.contribution,
        }


@dataclass
class AgentOpinion:
    """Structured output from a single committee agent."""

    agent_name: str
    direction: AgentDirection
    confidence: float  # 0.0 - 1.0
    evidence: list[EvidenceItem] = field(default_factory=list)
    reasoning: str = ""
    raw_score: float = 0.0  # weighted contribution to final score

    @property
    def weighted_contribution(self) -> float:
        return self.confidence * self.raw_score

    def to_dict(self) -> dict:
        return {
            "agent": self.agent_name,
            "direction": self.direction,
            "confidence": round(self.confidence, 3),
            "weighted_contribution": round(self.weighted_contribution, 4),
            "evidence": [e.to_dict() for e in self.evidence],
            "reasoning": self.reasoning[:500],
        }


@dataclass
class RuleCheckResult:
    """Result of a single rule check."""

    rule_name: str
    passed: bool
    severity: Literal["info", "warning", "block"]
    message: str
    metadata: dict = field(default_factory=dict)


@dataclass
class DecisionReport:
    """Complete auditable decision report."""

    symbol: str
    market_type: str
    timestamp: str
    final_direction: AgentDirection
    final_confidence: float  # 0.0 - 1.0
    position_size_factor: float  # 0.0 - 1.0 (1.0 = full size)
    agent_opinions: list[AgentOpinion] = field(default_factory=list)
    rule_checks: list[RuleCheckResult] = field(default_factory=list)
    decision_id: str = ""
    explanation: str = ""  # human-readable summary
    # LLM routing metadata (which model served the request, whether fallback was
    # used). Advisory provenance only; excluded from to_dict() to keep the
    # external report contract stable.
    model_meta: dict = field(default_factory=dict)
    # LLM-authored risk warnings (风险提示). Advisory; excluded from to_dict()
    # for the same contract-stability reason, persisted via committee_reports.
    risk_warnings: list = field(default_factory=list)

    @property
    def explainability_score(self) -> float:
        """0.0 - 1.0, how explainable is this decision?"""
        if not self.agent_opinions:
            return 0.0
        scores: list[float] = []
        # Evidence count (>=3 per agent is ideal)
        for op in self.agent_opinions:
            n = len(op.evidence)
            scores.append(min(n / 3.0, 1.0))
        # Agent agreement
        directions = [o.direction for o in self.agent_opinions if o.direction != "neutral"]
        if directions:
            dominant = max(set(directions), key=directions.count)
            agreement = directions.count(dominant) / len(directions)
            scores.append(agreement)
        # Rule compliance
        if self.rule_checks:
            passed = sum(1 for r in self.rule_checks if r.passed)
            scores.append(passed / len(self.rule_checks))
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def blocked(self) -> bool:
        return any(not r.passed and r.severity == "block" for r in self.rule_checks)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "market_type": self.market_type,
            "timestamp": self.timestamp,
            "final_direction": self.final_direction,
            "final_confidence": round(self.final_confidence, 3),
            "position_size_factor": round(self.position_size_factor, 3),
            "explainability_score": round(self.explainability_score, 3),
            "blocked": self.blocked,
            "agents": [o.to_dict() for o in self.agent_opinions],
            "rules": [
                {
                    "rule": r.rule_name,
                    "passed": r.passed,
                    "severity": r.severity,
                    "message": r.message,
                }
                for r in self.rule_checks
            ],
            "explanation": self.explanation,
        }
