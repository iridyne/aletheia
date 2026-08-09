"""Trend Agent — technical analysis based on scanner signals.

Reads the deterministic scanner-family scores already computed by Aletheia's
universe layer and translates them into a structured AgentOpinion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aletheia.agents.committee.base import AgentOpinion, EvidenceItem

logger = logging.getLogger("aletheia.committee.trend")


def analyze_trend(features: dict[str, Any]) -> AgentOpinion:
    """Produce a Trend Agent opinion from scanner feature snapshot.

    ``features`` is the enriched feature dict that the universe layer already
    computed for a symbol (SMA/RSI/ATR/Bollinger/Momentum/volume).
    """
    evidence: list[EvidenceItem] = []
    score = 0.0

    # --- SMA trend ---
    sma20 = features.get("sma20")
    sma50 = features.get("sma50")
    price = features.get("last_price")
    if price and sma20 and sma50:
        if price > sma20 > sma50:
            evidence.append(EvidenceItem("SMA", f"price {price:.2f} > SMA20 {sma20:.2f} > SMA50 {sma50:.2f}", +18))
            score += 18
        elif price < sma20 < sma50:
            evidence.append(EvidenceItem("SMA", f"price {price:.2f} < SMA20 {sma20:.2f} < SMA50 {sma50:.2f}", -18))
            score -= 18
        elif price > sma50:
            evidence.append(EvidenceItem("SMA", f"price {price:.2f} > SMA50 {sma50:.2f}", +10))
            score += 10
        else:
            evidence.append(EvidenceItem("SMA", f"price {price:.2f} < SMA50 {sma50:.2f}", -10))
            score -= 10

    # --- RSI ---
    rsi = features.get("rsi14")
    if rsi is not None:
        if rsi > 70:
            evidence.append(EvidenceItem("RSI", f"RSI {rsi:.1f} overbought", -8))
            score -= 8
        elif rsi < 30:
            evidence.append(EvidenceItem("RSI", f"RSI {rsi:.1f} oversold", +8))
            score += 8
        elif rsi > 50:
            evidence.append(EvidenceItem("RSI", f"RSI {rsi:.1f} bullish", +6))
            score += 6
        else:
            evidence.append(EvidenceItem("RSI", f"RSI {rsi:.1f} bearish", -6))
            score -= 6

    # --- Momentum ---
    mom = features.get("mom10")
    if mom is not None:
        if mom > 2.0:
            evidence.append(EvidenceItem("Mom10", f"+{mom:.2f}% strong upward momentum", +12))
            score += 12
        elif mom > 0:
            evidence.append(EvidenceItem("Mom10", f"+{mom:.2f}% positive momentum", +6))
            score += 6
        elif mom > -2.0:
            evidence.append(EvidenceItem("Mom10", f"{mom:.2f}% mild selling", -4))
            score -= 4
        else:
            evidence.append(EvidenceItem("Mom10", f"{mom:.2f}% strong selling pressure", -10))
            score -= 10

    # --- Bollinger Band ---
    bb_pct = features.get("bb_pct")
    if bb_pct is not None:
        if bb_pct > 1.0:
            evidence.append(EvidenceItem("BB%", f"{bb_pct:.2f} above upper band (overbought)", -6))
            score -= 6
        elif bb_pct < 0.0:
            evidence.append(EvidenceItem("BB%", f"{bb_pct:.2f} below lower band (oversold)", +6))
            score += 6
        elif bb_pct > 0.8:
            evidence.append(EvidenceItem("BB%", f"{bb_pct:.2f} near upper band", -2))
            score -= 2

    # --- Volume z-score ---
    vol_z = features.get("volume_z")
    if vol_z is not None:
        if vol_z > 1.5:
            evidence.append(EvidenceItem("VolZ", f"{vol_z:.2f} unusually high volume (confirmation)", +5))
            score += 5
        elif vol_z < -1.0:
            evidence.append(EvidenceItem("VolZ", f"{vol_z:.2f} low volume (weak move)", -3))
            score -= 3

    # --- Direction & confidence ---
    # Neutral threshold: score ∈ (-10, 10) → neutral (reduced from ±15 to
    # reduce unnecessary neutrality in weak but real signals)
    if score >= 10:
        direction = "long"
        confidence = min(0.4 + score / 100, 0.95)
    elif score <= -10:
        direction = "short"
        confidence = min(0.4 + abs(score) / 100, 0.95)
    else:
        direction = "neutral"
        confidence = max(0.3, 0.5 - abs(score) / 100)

    reasoning_parts = []
    if direction == "long":
        reasoning_parts.append(f"多头信号累积得分 {score:.0f}: " + "; ".join(e.value for e in evidence[:3]))
    elif direction == "short":
        reasoning_parts.append(f"空头信号累积得分 {score:.0f}: " + "; ".join(e.value for e in evidence[:3]))
    else:
        reasoning_parts.append(f"信号混杂 (得分 {score:.0f})，无明确方向")

    return AgentOpinion(
        agent_name="Trend",
        direction=direction,
        confidence=round(confidence, 3),
        evidence=evidence,
        reasoning=". ".join(reasoning_parts),
        raw_score=score,
    )
