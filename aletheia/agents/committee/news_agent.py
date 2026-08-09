"""News Agent — news sentiment analysis.

Reads recent news headlines and macro calendar events to assess
sentiment impact on the asset.
"""

from __future__ import annotations

import logging
from typing import Any

from aletheia.agents.committee.base import AgentOpinion, EvidenceItem

logger = logging.getLogger("aletheia.committee.news")

# Keyword-based sentiment scoring (simple, auditable, no LLM needed)
POSITIVE_KEYWORDS = {
    "etf": 3, "adoption": 4, "inflow": 3, "upgrade": 3, "partnership": 2,
    "bullish": 3, "rally": 2, "surge": 2, "buy": 1, "accumulation": 2,
    "rate cut": 3, "dovish": 3, "approval": 4, "breakout": 2,
}
NEGATIVE_KEYWORDS = {
    "hack": -5, "sec": -3, "lawsuit": -4, "crash": -4, "ban": -5,
    "outflow": -3, "bearish": -3, "sell": -2, "dump": -3, "fraud": -5,
    "rate hike": -3, "hawkish": -3, "inflation": -2, "default": -4,
    "regulation": -2, "investigation": -3,
}

# High-impact events that override normal scoring
BLACKLIST_EVENTS = ["exchange hack", "major lawsuit", "ban", "sec enforcement"]


def analyze_news(news_data: dict[str, Any]) -> AgentOpinion:
    """Produce a News Agent opinion.

    ``news_data`` contains:
      - headlines: list[str] — recent news headlines
      - macro_calendar: list[str] — upcoming macro events (CPI, FOMC, etc.)
      - sentiment_score: float | None — optional pre-computed sentiment
    """
    evidence: list[EvidenceItem] = []
    score = 0.0

    headlines = news_data.get("headlines", []) or []
    calendar = news_data.get("macro_calendar", []) or []

    # --- Headline keyword scoring ---
    headline_score = 0.0
    for hl in headlines[:10]:
        hl_lower = hl.lower()
        for kw, val in POSITIVE_KEYWORDS.items():
            if kw in hl_lower:
                headline_score += val
                evidence.append(EvidenceItem("News", f"[+] \"{hl[:60]}\"", val))
        for kw, val in NEGATIVE_KEYWORDS.items():
            if kw in hl_lower:
                headline_score += val
                evidence.append(EvidenceItem("News", f"[-] \"{hl[:60]}\"", val))
    score += max(min(headline_score, 20), -20)  # cap at +/-20

    # --- Macro calendar risk ---
    calendar_risk = 0
    for event in calendar[:5]:
        evt_lower = event.lower()
        if any(b in evt_lower for b in ["cpi", "fomc", "nfp", "fed"]):
            calendar_risk += 5
            evidence.append(EvidenceItem("Calendar", f"⚠ {event} (high impact)", -3))
    score -= min(calendar_risk, 10)

    # --- Direction & confidence ---
    if score >= 8:
        direction = "long"
        confidence = min(0.5 + score / 50, 0.85)
    elif score <= -8:
        direction = "short"
        confidence = min(0.5 + abs(score) / 50, 0.85)
    else:
        direction = "neutral"
        confidence = 0.5

    reasoning_parts = []
    if direction == "long":
        reasoning_parts.append(f"新闻情绪偏多 (得分 {score:.0f})")
    elif direction == "short":
        reasoning_parts.append(f"新闻情绪偏空 (得分 {score:.0f})")
    else:
        reasoning_parts.append(f"新闻情绪中性 (得分 {score:.0f})")
    if evidence:
        reasoning_parts.append("关键事件: " + "; ".join(e.value for e in evidence[:3]))

    return AgentOpinion(
        agent_name="News",
        direction=direction,
        confidence=round(confidence, 3),
        evidence=evidence,
        reasoning=". ".join(reasoning_parts),
        raw_score=score,
    )
