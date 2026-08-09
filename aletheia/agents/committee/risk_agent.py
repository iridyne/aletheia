"""Risk Agent — risk assessment and position sizing.

Reads ATR, current portfolio concentration, and volatility to
determine maximum acceptable position size.  Can veto trades.
"""

from __future__ import annotations

import logging
from typing import Any

from aletheia.agents.committee.base import AgentOpinion, EvidenceItem, RiskLevel

logger = logging.getLogger("aletheia.committee.risk")


def analyze_risk(risk_data: dict[str, Any]) -> AgentOpinion:
    """Produce a Risk Agent opinion.

    ``risk_data`` contains:
      - atr_pct: float | None — ATR as % of price (volatility)
      - current_portfolio_heat: float | None — current portfolio risk (0-1)
      - symbol_correlation: float | None — correlation with existing positions (0-1)
      - consecutive_losses: int | None — recent consecutive losing trades
      - account_drawdown_pct: float | None — current drawdown from peak (%)
    """
    evidence: list[EvidenceItem] = []
    score = 0.0  # positive = safe, negative = risky
    risk_level = RiskLevel.LOW
    position_factor = 1.0  # 1.0 = full size

    # --- ATR (volatility) ---
    atr = risk_data.get("atr_pct")
    if atr is not None:
        if atr > 5.0:
            evidence.append(EvidenceItem("ATR", f"{atr:.1f}% very high volatility", -12))
            score -= 12
            position_factor *= 0.3
        elif atr > 3.0:
            evidence.append(EvidenceItem("ATR", f"{atr:.1f}% high volatility", -6))
            score -= 6
            position_factor *= 0.5
        elif atr > 1.5:
            evidence.append(EvidenceItem("ATR", f"{atr:.1f}% moderate volatility", -2))
            score -= 2
            position_factor *= 0.8
        else:
            evidence.append(EvidenceItem("ATR", f"{atr:.1f}% low volatility", +3))
            score += 3

    # --- Portfolio heat ---
    heat = risk_data.get("current_portfolio_heat")
    if heat is not None:
        if heat > 0.8:
            evidence.append(EvidenceItem("Heat", f"{heat:.0%} portfolio nearly full", -10))
            score -= 10
            position_factor *= 0.3
        elif heat > 0.5:
            evidence.append(EvidenceItem("Heat", f"{heat:.0%} portfolio moderate", -4))
            score -= 4
            position_factor *= 0.7

    # --- Correlation with existing positions ---
    corr = risk_data.get("symbol_correlation")
    if corr is not None:
        if corr > 0.7:
            evidence.append(EvidenceItem("Corr", f"{corr:.0%} highly correlated (concentration risk)", -8))
            score -= 8
            position_factor *= 0.5

    # --- Consecutive losses ---
    losses = risk_data.get("consecutive_losses") or 0
    if losses >= 5:
        evidence.append(EvidenceItem("Streak", f"{losses} consecutive losses (cool down)", -15))
        score -= 15
        position_factor *= 0.2
    elif losses >= 3:
        evidence.append(EvidenceItem("Streak", f"{losses} consecutive losses (caution)", -6))
        score -= 6
        position_factor *= 0.6

    # --- Drawdown ---
    dd = risk_data.get("account_drawdown_pct") or 0
    if dd > 10:
        evidence.append(EvidenceItem("Drawdown", f"{dd:.1f}% account drawdown (reduce size)", -10))
        score -= 10
        position_factor *= 0.3
    elif dd > 5:
        evidence.append(EvidenceItem("Drawdown", f"{dd:.1f}% account drawdown", -4))
        score -= 4
        position_factor *= 0.6

    # --- Risk level ---
    if score <= -15:
        risk_level = RiskLevel.HIGH
    elif score <= -5:
        risk_level = RiskLevel.MEDIUM
    else:
        risk_level = RiskLevel.LOW

    # --- Direction (Risk Agent says "go" or "caution") ---
    if risk_level == RiskLevel.HIGH:
        direction = "neutral"  # effectively a veto
        confidence = 0.7
    elif risk_level == RiskLevel.MEDIUM:
        direction = "neutral"
        confidence = 0.5
    else:
        direction = "long"  # doesn't predict direction, just says "safe to trade"
        confidence = 0.6

    position_factor = max(0.1, min(position_factor, 1.0))

    reasoning_parts = []
    if risk_level == RiskLevel.HIGH:
        reasoning_parts.append(f"风险等级高 (得分 {score:.0f})，建议暂停交易")
    elif risk_level == RiskLevel.MEDIUM:
        reasoning_parts.append(f"风险等级中 (得分 {score:.0f})，降低仓位")
    else:
        reasoning_parts.append(f"风险等级低 (得分 {score:.0f})，可正常交易")
    reasoning_parts.append(f"建议仓位系数: {position_factor:.0%}")
    if evidence:
        reasoning_parts.append("风险因子: " + "; ".join(e.value for e in evidence[:3]))

    return AgentOpinion(
        agent_name="Risk",
        direction=direction,
        confidence=round(confidence, 3),
        evidence=evidence,
        reasoning=". ".join(reasoning_parts),
        raw_score=score,
    )
