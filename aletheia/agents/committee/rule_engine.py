"""Rule Engine — deterministic post-decision checks.

Runs a configurable set of rules against the committee decision.
Rules can: warn, reduce position, or block entirely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from aletheia.agents.committee.base import (
    AgentDirection,
    DecisionReport,
    RuleCheckResult,
)

logger = logging.getLogger("aletheia.committee.rule_engine")


@dataclass
class Rule:
    """A deterministic rule that checks a condition."""

    name: str
    check: Callable[[dict[str, Any]], bool]
    severity: str = "warning"  # "info", "warning", "block"
    message: str = ""
    detail_fn: Callable[[dict[str, Any]], str] | None = None

    def evaluate(self, context: dict[str, Any]) -> RuleCheckResult:
        passed = self.check(context)
        detail = ""
        if self.detail_fn and not passed:
            try:
                detail = self.detail_fn(context)
            except Exception:
                detail = ""
        return RuleCheckResult(
            rule_name=self.name,
            passed=passed,
            severity=self.severity if not passed else "info",
            message=self.message if not passed else f"{self.name}: OK",
            metadata={"detail": detail},
        )


# --- Default rule set ---

def _build_default_rules() -> list[Rule]:
    """Build the default rule set.  These reproduce the existing guard
    semantics plus the new committee-level checks.
    """
    return [
        Rule(
            name="min_confidence",
            check=lambda ctx: ctx.get("final_confidence", 0) >= 0.3,
            severity="block",
            message="置信度过低 (<0.3)",
            detail_fn=lambda ctx: f"confidence={ctx.get('final_confidence', 0):.2f}",
        ),
        Rule(
            name="agent_disagreement",
            check=lambda ctx: not ctx.get("conflict_detected", False),
            severity="warning",
            message="Agent 分歧: 降低仓位",
        ),
        Rule(
            name="min_evidence_total",
            # LLM committee emits one focused evidence item per opinion (the
            # "evidence" key in its JSON output).  The old threshold of 3 was
            # designed for the Trend+Macro+News+Risk multi-agent system and
            # silently blocked ~93% of LLM evaluations that had exactly 1
            # evidence.  Lower to 1 so the gate matches the LLM contract.
            check=lambda ctx: ctx.get("total_evidence_count", 0) >= 1,
            severity="block",
            message="无证据支持",
            detail_fn=lambda ctx: f"evidence={ctx.get('total_evidence_count', 0)}",
        ),
        Rule(
            name="explainability_floor",
            check=lambda ctx: ctx.get("explainability_score", 0) >= 0.4,
            severity="block",
            message="可解释性评分过低 (<0.4)",
            detail_fn=lambda ctx: f"ES={ctx.get('explainability_score', 0):.2f}",
        ),
        Rule(
            name="position_size_cap",
            check=lambda ctx: ctx.get("position_size_factor", 0) <= 0.5,
            severity="block",
            message="仓位过大 (>50%)",
            detail_fn=lambda ctx: f"size={ctx.get('position_size_factor', 0):.0%}",
        ),
        Rule(
            name="position_size_floor",
            check=lambda ctx: ctx.get("position_size_factor", 0) >= 0.05 or ctx.get("final_direction") == "neutral",
            severity="warning",
            message="仓位过小 (<5%), 可能不值得执行",
        ),
        Rule(
            name="high_risk_veto",
            check=lambda ctx: ctx.get("risk_level", "low") != "high" or ctx.get("final_direction") == "neutral",
            severity="block",
            message="Risk Agent 高风险否决",
        ),
        Rule(
            name="max_daily_trades",
            check=lambda ctx: ctx.get("daily_trade_count", 0) < 20,
            severity="block",
            message="日交易次数超限 (>=20)",
            detail_fn=lambda ctx: f"count={ctx.get('daily_trade_count', 0)}",
        ),
    ]


def run_rules(
    decision_report: DecisionReport,
    extra_context: dict[str, Any] | None = None,
    rules: list[Rule] | None = None,
) -> list[RuleCheckResult]:
    """Run all rules against the decision context.

    Args:
        decision_report: the preliminary decision report.
        extra_context: additional context (e.g. daily trade count).
        rules: custom rule set, or None for defaults.

    Returns:
        List of RuleCheckResult, one per rule.
    """
    if rules is None:
        rules = _build_default_rules()

    context: dict[str, Any] = {
        "final_direction": decision_report.final_direction,
        "final_confidence": decision_report.final_confidence,
        "position_size_factor": decision_report.position_size_factor,
        "conflict_detected": any(
            o.agent_name == "Risk" and o.direction == "neutral" and o.confidence > 0.6
            for o in decision_report.agent_opinions
        ),
        "total_evidence_count": sum(len(o.evidence) for o in decision_report.agent_opinions),
        "explainability_score": decision_report.explainability_score,
        "risk_level": "high" if any(
            o.agent_name == "Risk" and o.direction == "neutral" and o.confidence > 0.6
            for o in decision_report.agent_opinions
        ) else "low",
    }
    if extra_context:
        context.update(extra_context)

    results = []
    for rule in rules:
        result = rule.evaluate(context)
        results.append(result)
        if not result.passed:
            logger.debug("Rule %s FAILED: %s", rule.name, result.message)

    return results
