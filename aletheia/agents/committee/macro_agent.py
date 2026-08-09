"""Macro Agent — macro environment assessment.

Reads Fear & Greed index, BTC ETF flows, on-chain data, and DXY
to assess the overall market regime.
"""

from __future__ import annotations

import logging
from typing import Any

from aletheia.agents.committee.base import AgentOpinion, EvidenceItem

logger = logging.getLogger("aletheia.committee.macro")


def analyze_macro(macro_data: dict[str, Any]) -> AgentOpinion:
    """Produce a Macro Agent opinion.

    ``macro_data`` contains:
      - fear_greed: int | None — 0-100 (0=extreme fear, 100=extreme greed)
      - etf_flow_24h: float | None — USD ETF inflow (+) or outflow (-)
      - dxy_change: float | None — DXY daily % change
      - onchain_mvrv: float | None — MVRV z-score
      - stablecoin_supply_change: float | None — stablecoin supply % change
    """
    evidence: list[EvidenceItem] = []
    score = 0.0

    # --- Fear & Greed (contrarian signal) ---
    fg = macro_data.get("fear_greed")
    if fg is not None:
        if fg <= 20:
            # Extreme fear = contrarian buy
            evidence.append(EvidenceItem("F&G", f"{fg} extreme fear (contrarian buy)", +10))
            score += 10
        elif fg <= 40:
            evidence.append(EvidenceItem("F&G", f"{fg} fear (mild bullish)", +5))
            score += 5
        elif fg >= 80:
            # Extreme greed = contrarian sell
            evidence.append(EvidenceItem("F&G", f"{fg} extreme greed (contrarian sell)", -10))
            score -= 10
        elif fg >= 60:
            evidence.append(EvidenceItem("F&G", f"{fg} greed (mild bearish)", -5))
            score -= 5

    # --- ETF Flow ---
    etf = macro_data.get("etf_flow_24h")
    if etf is not None:
        if etf > 100_000_000:
            evidence.append(EvidenceItem("ETF", f"+${etf/1e6:.0f}M 强劲流入", +12))
            score += 12
        elif etf > 0:
            evidence.append(EvidenceItem("ETF", f"+${etf/1e6:.0f}M 温和流入", +6))
            score += 6
        elif etf < -100_000_000:
            evidence.append(EvidenceItem("ETF", f"-${abs(etf)/1e6:.0f}M 大幅流出", -12))
            score -= 12
        elif etf < 0:
            evidence.append(EvidenceItem("ETF", f"-${abs(etf)/1e6:.0f}M 温和流出", -6))
            score -= 6

    # --- DXY (inverse correlation with crypto) ---
    dxy = macro_data.get("dxy_change")
    if dxy is not None:
        if dxy < -0.5:
            evidence.append(EvidenceItem("DXY", f"{dxy:+.2f}% 美元走弱 (利好)", +8))
            score += 8
        elif dxy > 0.5:
            evidence.append(EvidenceItem("DXY", f"{dxy:+.2f}% 美元走强 (利空)", -8))
            score -= 8

    # --- On-chain MVRV ---
    mvrv = macro_data.get("onchain_mvrv")
    if mvrv is not None:
        if mvrv > 3.0:
            evidence.append(EvidenceItem("MVRV", f"{mvrv:.2f} 历史顶部区域", -10))
            score -= 10
        elif mvrv < 0:
            evidence.append(EvidenceItem("MVRV", f"{mvrv:.2f} 被低估区域", +8))
            score += 8

    # --- Stablecoin supply ---
    sc = macro_data.get("stablecoin_supply_change")
    if sc is not None:
        if sc > 2.0:
            evidence.append(EvidenceItem("Stable", f"+{sc:.1f}% 资金入场", +6))
            score += 6
        elif sc < -2.0:
            evidence.append(EvidenceItem("Stable", f"{sc:.1f}% 资金撤离", -6))
            score -= 6

    # --- Direction & confidence ---
    if score >= 10:
        direction = "long"
        confidence = min(0.5 + score / 80, 0.85)
    elif score <= -10:
        direction = "short"
        confidence = min(0.5 + abs(score) / 80, 0.85)
    else:
        direction = "neutral"
        confidence = 0.5

    reasoning_parts = []
    if direction == "long":
        reasoning_parts.append(f"宏观环境偏多 (得分 {score:.0f})")
    elif direction == "short":
        reasoning_parts.append(f"宏观环境偏空 (得分 {score:.0f})")
    else:
        reasoning_parts.append(f"宏观环境中性 (得分 {score:.0f})")
    if evidence:
        reasoning_parts.append("关键因子: " + "; ".join(e.value for e in evidence[:3]))

    return AgentOpinion(
        agent_name="Macro",
        direction=direction,
        confidence=round(confidence, 3),
        evidence=evidence,
        reasoning=". ".join(reasoning_parts),
        raw_score=score,
    )
