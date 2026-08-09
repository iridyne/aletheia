"""Report Generator — produces the final human-readable decision report.

Combines Decision Core output + Rule Engine results into a structured
DecisionReport that is persisted to the audit log and knowledge base.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aletheia.agents.committee.base import (
    AgentOpinion,
    DecisionReport,
    RuleCheckResult,
)
from aletheia.agents.committee.decision_core import DecisionResult

logger = logging.getLogger("aletheia.committee.report")


def generate_report(
    symbol: str,
    market_type: str,
    decision: DecisionResult,
    agent_opinions: list[AgentOpinion],
    rule_results: list[RuleCheckResult],
    decision_id: str = "",
) -> DecisionReport:
    """Generate the complete Decision Report."""

    # Build human-readable explanation
    explanation_parts = []

    # Direction summary
    dir_label = {"long": "做多", "short": "做空", "neutral": "观望"}
    explanation_parts.append(
        f"决策: {dir_label.get(decision.direction, decision.direction)} | "
        f"置信度: {decision.confidence:.0%} | "
        f"仓位: {decision.position_size_factor:.0%}"
    )

    # Agent summary
    for op in agent_opinions:
        emoji = {"long": "🟢", "short": "🔴", "neutral": "⚪"}[op.direction]
        explanation_parts.append(
            f"  {emoji} {op.agent_name}: {op.direction} ({op.confidence:.0%}) "
            f"[{len(op.evidence)} 证据]"
        )

    # Key evidence (top 3 across all agents)
    all_evidence = []
    for op in agent_opinions:
        all_evidence.extend(op.evidence)
    all_evidence.sort(key=lambda e: abs(e.contribution), reverse=True)
    if all_evidence:
        explanation_parts.append("关键证据:")
        for e in all_evidence[:5]:
            sign = "+" if e.contribution > 0 else ""
            explanation_parts.append(f"  {e.indicator}: {e.value} ({sign}{e.contribution:.0f})")

    # Blocked rules
    blocked = [r for r in rule_results if not r.passed and r.severity == "block"]
    warnings = [r for r in rule_results if not r.passed and r.severity == "warning"]
    if blocked:
        explanation_parts.append(f"⛔ 被拦截 ({len(blocked)} 条规则):")
        for r in blocked:
            explanation_parts.append(f"  - {r.message}")
    if warnings:
        explanation_parts.append(f"⚠️ 警告 ({len(warnings)} 条):")
        for r in warnings:
            explanation_parts.append(f"  - {r.message}")

    # Conflict
    if decision.conflict_detected:
        explanation_parts.append(f"⚡ 冲突: {decision.conflict_description}")

    report = DecisionReport(
        symbol=symbol,
        market_type=market_type,
        timestamp=datetime.now(timezone.utc).isoformat(),
        final_direction=decision.direction,
        final_confidence=decision.confidence,
        position_size_factor=decision.position_size_factor,
        agent_opinions=agent_opinions,
        rule_checks=rule_results,
        decision_id=decision_id,
        explanation="\n".join(explanation_parts),
    )

    return report
