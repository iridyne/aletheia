"""Causal checks for the Risk Engine (Project Chimera inspired).

Traditional risk engine: "Is this trade within limits?" (passive)
Causal risk engine: "WHY should we take this trade?" (active)

Adds a layer of explainability checks that validate whether the trade
has fundamental/technical substance beyond pattern matching.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aletheia.risk.causal")


def _safe_observations_list(raw: Any) -> list[str]:
    """Parse a stored key_observations JSON string without ever raising.

    The durable Opportunity stores observations as a JSON-encoded string; any
    malformed/non-list payload degrades to an empty list instead of aborting
    the causal gate.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def resolve_causal_facts(proposal) -> dict[str, Any] | None:
    """Resolve causal input facts from the proposal's linked Opportunity.

    TradeProposal carries no thesis/confidence/observations of its own; the
    durable facts live on the Opportunity model. Mirrors the dual-table,
    exactly-one-source identity rule used by the paper opportunity identity
    check: the opportunity_id must resolve in exactly one of ``opportunities``
    or ``serenity_opportunities``. Any ambiguity, missing source, or read
    error returns None (the engine then skips the gate instead of guessing).
    """
    opportunity_id = str(getattr(proposal, "opportunity_id", "") or "").strip()
    if not opportunity_id:
        return None
    try:
        from aletheia.storage.repositories import (
            get_opportunity,
            get_serenity_opportunity,
        )

        normal = get_opportunity(opportunity_id)
        serenity = get_serenity_opportunity(opportunity_id)
    except Exception as exc:
        logger.warning(
            "causal facts unavailable for opportunity %s (%s)",
            opportunity_id,
            type(exc).__name__,
        )
        return None
    sources = [row for row in (normal, serenity) if row is not None]
    if len(sources) != 1:
        return None
    source = serenity if normal is None else normal
    is_serenity = normal is None
    # Committee report is read-only advisory input: the latest persisted LLM
    # committee evaluation for this opportunity (append-only committee_reports).
    # Any read/parse problem degrades to None — the gate then behaves exactly as
    # before the committee existed (fail-soft, advisory-first).
    committee_report = _load_latest_committee_report(opportunity_id)
    return {
        "direction": getattr(proposal, "direction", None),
        "symbol": getattr(proposal, "symbol", None),
        "thesis": source.get("thesis") or "",
        "confidence": (
            source.get("evidence_tier") if is_serenity else source.get("confidence")
        ),
        "confidence_score": source.get("confidence_score"),
        "scanner_family": None if is_serenity else source.get("scanner_family"),
        "key_observations": _safe_observations_list(source.get("key_observations")),
        "committee_report": committee_report,
    }


def _load_latest_committee_report(opportunity_id: str) -> dict[str, Any] | None:
    """Latest persisted committee evaluation for an opportunity (or None).

    Reshapes the committee_reports row into the dict shape
    check_trade_causality consumes (agent_opinions + explainability_score).
    Never raises: unreadable/missing data simply yields None.
    """
    try:
        from aletheia.storage.repositories import (
            get_latest_committee_report_for_opportunity,
        )

        row = get_latest_committee_report_for_opportunity(opportunity_id)
        if not row:
            return None
        try:
            opinions = json.loads(row.get("agent_opinions_json") or "[]")
        except (TypeError, ValueError):
            opinions = []
        if not isinstance(opinions, list):
            opinions = []
        return {
            "agent_opinions": opinions,
            "explainability_score": row.get("explainability_score") or 0.0,
            "direction": row.get("direction"),
            "confidence": row.get("confidence"),
        }
    except Exception as exc:
        logger.debug("committee report lookup failed for %s: %s", opportunity_id, exc)
        return None


@dataclass
class CausalCheckResult:
    """Result of causal validation."""

    passed: bool
    confidence: float  # 0-1, how confident are we in the cause
    cause_type: str  | None = None  # "fundamental", "technical", "sentiment", "mixed", "unknown"
    reasoning: str = ""
    evidence: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "confidence": round(self.confidence, 3),
            "cause_type": self.cause_type,
            "reasoning": self.reasoning[:300],
            "evidence": self.evidence[:5],
            "concerns": self.concerns[:5],
        }


def check_trade_causality(
    proposal_data: dict[str, Any],
    market_context: dict[str, Any] | None = None,
) -> CausalCheckResult:
    """Validate whether a trade proposal has a substantive cause.

    Questions asked:
    1. Is there a clear catalyst (news, earnings, protocol upgrade)?
    2. Does the technical setup have multi-timeframe confirmation?
    3. Is the move supported by on-chain/fundamental data?
    4. Or is this just noise / FOMO / thin-data guesswork?

    Args:
        proposal_data: dict with:
            - direction: "long" | "short"
            - symbol: str
            - thesis: str — the stated reason for the trade
            - confidence: str — "high" | "medium" | "speculative"
            - confidence_score: int — 0-100
            - scanner_family: str — which scanner family detected this
            - key_observations: list[str] — cited evidence
            - committee_report: dict | None — optional committee output
        market_context: dict with:
            - atr_pct: float — current volatility
            - fear_greed: int — 0-100
            - etf_flow_24h: float — USD flow
            - recent_headlines: list[str]
            - onchain_mvrv: float | None

    Returns:
        CausalCheckResult with pass/fail + reasoning.
    """
    evidence: list[str] = []
    concerns: list[str] = []
    cause_scores: dict[str, float] = {
        "fundamental": 0.0,
        "technical": 0.0,
        "sentiment": 0.0,
    }

    direction = proposal_data.get("direction", "neutral")
    confidence = proposal_data.get("confidence", "speculative")
    confidence_score = proposal_data.get("confidence_score", 50) or 50
    thesis = proposal_data.get("thesis", "") or ""
    observations = proposal_data.get("key_observations", []) or []
    scanner_family = proposal_data.get("scanner_family", "")
    committee_report = proposal_data.get("committee_report")

    market = market_context or {}

    # --- 1. Committee-based causality (strongest signal) ---
    if committee_report:
        agents = committee_report.get("agent_opinions", [])
        for agent in agents:
            agent_name = agent.get("agent", "")
            agent_evidence = agent.get("evidence", [])
            if agent_name == "Trend" and len(agent_evidence) >= 2:
                cause_scores["technical"] += 0.3
                evidence.append(f"Trend Agent: {len(agent_evidence)} 个技术证据")
            elif agent_name == "Macro" and len(agent_evidence) >= 2:
                cause_scores["fundamental"] += 0.3
                evidence.append(f"Macro Agent: {len(agent_evidence)} 个宏观证据")
            elif agent_name == "News" and len(agent_evidence) >= 1:
                cause_scores["sentiment"] += 0.2
                evidence.append(f"News Agent: {len(agent_evidence)} 个新闻证据")

        # High explainability score = more causal substance
        es = committee_report.get("explainability_score", 0)
        if es >= 0.7:
            evidence.append(f"高可解释性评分 ({es:.2f})")
        elif es < 0.4:
            concerns.append(f"低可解释性评分 ({es:.2f})")

    # --- 2. Observation-based causality ---
    if len(observations) >= 3:
        evidence.append(f"引用 {len(observations)} 个具体指标")
        cause_scores["technical"] += 0.2
    elif len(observations) >= 1:
        evidence.append(f"引用 {len(observations)} 个指标")
        cause_scores["technical"] += 0.1
    else:
        concerns.append("无具体指标引用")

    # --- 3. Market context validation ---
    fg = market.get("fear_greed")
    if fg is not None:
        if direction == "long" and fg <= 25:
            evidence.append(f"F&G={fg} 极端恐惧 (contrarian 做多合理)")
            cause_scores["sentiment"] += 0.2
        elif direction == "short" and fg >= 75:
            evidence.append(f"F&G={fg} 极端贪婪 (contrarian 做空合理)")
            cause_scores["sentiment"] += 0.2
        elif direction == "long" and fg >= 80:
            concerns.append(f"F&G={fg} 极度贪婪中做多 (FOMO 风险)")
            cause_scores["sentiment"] -= 0.1
        elif direction == "short" and fg <= 20:
            concerns.append(f"F&G={fg} 极度恐惧中做空 (catch falling knife)")
            cause_scores["sentiment"] -= 0.1

    etf = market.get("etf_flow_24h")
    if etf is not None:
        if direction == "long" and etf > 50_000_000:
            evidence.append(f"ETF 净流入 +${etf/1e6:.0f}M (资金支撑)")
            cause_scores["fundamental"] += 0.2
        elif direction == "short" and etf < -50_000_000:
            evidence.append(f"ETF 净流出 -${abs(etf)/1e6:.0f}M (资金撤离)")
            cause_scores["fundamental"] += 0.2
        elif direction == "long" and etf < -50_000_000:
            concerns.append(f"ETF 净流出但做多 (逆势风险)")
            cause_scores["fundamental"] -= 0.1

    headlines = market.get("recent_headlines", [])
    if headlines:
        evidence.append(f"有 {len(headlines)} 条相关新闻")
        cause_scores["sentiment"] += 0.1

    # --- 4. Volatility sanity ---
    atr = market.get("atr_pct")
    if atr is not None and atr > 5.0:
        concerns.append(f"高波动率 ({atr:.1f}%) — 信号可能不可靠")

    # --- 5. Confidence sanity ---
    if confidence == "speculative" and confidence_score < 40:
        concerns.append("低置信度 + speculative 标签 = 猜测成分大")

    # --- Determine cause type and overall confidence ---
    max_cause = max(cause_scores, key=cause_scores.get)
    max_score = cause_scores[max_cause]

    if max_score >= 0.4:
        cause_type = max_cause
        causal_confidence = min(max_score, 1.0)
    elif max_score >= 0.2:
        cause_type = "mixed"
        causal_confidence = max_score
    else:
        cause_type = "unknown"
        causal_confidence = 0.1

    # --- Pass/fail ---
    # Require at least some causal substance
    passed = (
        causal_confidence >= 0.2
        and len(concerns) < 3
        and cause_type != "unknown"
    )

    # Build reasoning
    reasoning_parts = []
    if cause_type and cause_type != "unknown":
        reasoning_parts.append(f"归因类型: {cause_type}")
    if evidence:
        reasoning_parts.append("证据: " + "; ".join(evidence[:3]))
    if concerns:
        reasoning_parts.append("顾虑: " + "; ".join(concerns[:3]))

    return CausalCheckResult(
        passed=passed,
        confidence=round(causal_confidence, 3),
        cause_type=cause_type,
        reasoning=". ".join(reasoning_parts),
        evidence=evidence,
        concerns=concerns,
    )
