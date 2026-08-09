"""InvestmentCommittee — main orchestrator for the AI investment committee.

Usage:

    committee = InvestmentCommittee()
    report = await committee.evaluate(
        symbol="BTC/USDT",
        market_type="perp",
        features={...},        # scanner features
        news_data={...},       # headlines + calendar
        macro_data={...},      # F&G, ETF, DXY, on-chain
        risk_data={...},       # ATR, portfolio heat, etc.
    )

    if report.blocked:
        print("决策被拦截:", report.explanation)
    else:
        print(f"方向: {report.final_direction}, 仓位: {report.position_size_factor}")
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from aletheia.agents.committee.base import (
    AgentOpinion,
    DecisionReport,
    EvidenceItem,
)
from aletheia.agents.committee.decision_core import decide, DecisionResult
from aletheia.agents.committee.macro_agent import analyze_macro
from aletheia.agents.committee.news_agent import analyze_news
from aletheia.agents.committee.report_generator import generate_report
from aletheia.agents.committee.risk_agent import analyze_risk
from aletheia.agents.committee.rule_engine import run_rules
from aletheia.agents.committee.trend_agent import analyze_trend

logger = logging.getLogger("aletheia.committee")


class InvestmentCommittee:
    """AI Investment Committee — multi-agent explainable decisions.

    Two modes:
      - evaluate(): deterministic rule-based (fast, no LLM)
      - evaluate_with_llm(): LLM-powered (uses tokens, provides reasoning)
    """

    def __init__(
        self,
        enable_trend: bool = True,
        enable_macro: bool = True,
        enable_news: bool = True,
        enable_risk: bool = True,
    ):
        self._agents: dict[str, bool] = {
            "Trend": enable_trend,
            "Macro": enable_macro,
            "News": enable_news,
            "Risk": enable_risk,
        }

    async def evaluate(
        self,
        symbol: str,
        market_type: str,
        features: dict[str, Any] | None = None,
        news_data: dict[str, Any] | None = None,
        macro_data: dict[str, Any] | None = None,
        risk_data: dict[str, Any] | None = None,
    ) -> DecisionReport:
        """Run the deterministic committee evaluation (no LLM).

        Uses rule-based Trend/Macro/News/Risk agents for fast scoring.
        """
        opinions: list[AgentOpinion] = []
        decision_id = uuid.uuid4().hex[:16]

        # --- Phase 1: Each agent produces an opinion ---
        if self._agents.get("Trend") and features:
            try:
                opinions.append(analyze_trend(features or {}))
            except Exception as exc:
                logger.warning("Trend Agent failed for %s: %s", symbol, exc)

        if self._agents.get("Macro") and macro_data:
            try:
                opinions.append(analyze_macro(macro_data or {}))
            except Exception as exc:
                logger.warning("Macro Agent failed for %s: %s", symbol, exc)

        if self._agents.get("News") and news_data:
            try:
                opinions.append(analyze_news(news_data or {}))
            except Exception as exc:
                logger.warning("News Agent failed for %s: %s", symbol, exc)

        if self._agents.get("Risk") and risk_data:
            try:
                opinions.append(analyze_risk(risk_data or {}))
            except Exception as exc:
                logger.warning("Risk Agent failed for %s: %s", symbol, exc)

        # --- Phase 2: Decision Core aggregates ---
        risk_position_factor = 1.0
        risk_opinion = next((o for o in opinions if o.agent_name == "Risk"), None)
        if risk_opinion:
            if risk_opinion.raw_score <= -15:
                risk_position_factor = 0.2
            elif risk_opinion.raw_score <= -5:
                risk_position_factor = 0.5
            else:
                risk_position_factor = 1.0

        decision_result: DecisionResult = decide(
            opinions, risk_position_factor=risk_position_factor
        )

        # --- Phase 3: Rule Engine checks ---
        preliminary_report = DecisionReport(
            symbol=symbol,
            market_type=market_type,
            timestamp="",
            final_direction=decision_result.direction,
            final_confidence=decision_result.confidence,
            position_size_factor=decision_result.position_size_factor,
            agent_opinions=opinions,
            rule_checks=[],
        )
        rule_results = run_rules(preliminary_report)

        # --- Phase 4: Generate final report ---
        report = generate_report(
            symbol=symbol,
            market_type=market_type,
            decision=decision_result,
            agent_opinions=opinions,
            rule_results=rule_results,
            decision_id=decision_id,
        )

        return report

    async def evaluate_with_llm(
        self,
        symbol: str,
        market_type: str,
        features: dict[str, Any] | None = None,
        news_data: dict[str, Any] | None = None,
        macro_data: dict[str, Any] | None = None,
        risk_data: dict[str, Any] | None = None,
        provider: str | None = None,
    ) -> DecisionReport:
        """Run LLM-powered committee evaluation (single call).

        This is the "full LLM" mode — the LLM reads all market data
        and produces a structured recommendation with reasoning.

        Args:
            symbol: trading symbol
            market_type: market type
            features: scanner features (SMA, RSI, etc.)
            news_data: headlines + calendar
            macro_data: F&G, ETF, DXY, etc.
            risk_data: ATR, portfolio heat, etc.
            provider: optional LLM provider name

        Returns:
            DecisionReport with LLM analysis.
        """
        from aletheia.agents.committee.llm_agent import analyze_with_llm

        # Merge all data into a single dict for the LLM
        merged_data: dict[str, Any] = {}
        if features:
            merged_data.update(features)
        if news_data:
            if news_data.get("headlines"):
                merged_data["recent_headlines"] = news_data["headlines"]
            if news_data.get("macro_calendar"):
                merged_data["macro_calendar"] = news_data["macro_calendar"]
        if macro_data:
            merged_data.update(macro_data)
        if risk_data:
            merged_data.update(risk_data)

        decision_id = uuid.uuid4().hex[:16]

        # Call LLM for analysis
        llm_result = await analyze_with_llm(symbol, market_type, merged_data, provider=provider)

        if llm_result is None:
            # LLM failed — fall back to deterministic
            logger.warning("LLM analysis failed for %s, falling back to deterministic", symbol)
            return await self.evaluate(
                symbol, market_type, features, news_data, macro_data, risk_data,
            )

        # Build opinion from LLM output
        # Normalize direction to lowercase (LLM may return "LONG", "NEUTRAL", etc.)
        raw_direction = str(llm_result.get("direction", "neutral")).lower().strip()
        # Validate direction; default to neutral if unexpected value
        if raw_direction not in ("long", "short", "neutral"):
            logger.warning("LLM returned unexpected direction '%s', defaulting to neutral", raw_direction)
            raw_direction = "neutral"

        # --- Evidence extraction (accept both key spellings) ---
        # Production LLMs overwhelmingly return "evidence", while the original
        # contract asked for "key_evidence"; reading only the latter silently
        # emptied the evidence list and depressed the explainability score.
        raw_evidence = llm_result.get("key_evidence") or llm_result.get("evidence") or []
        if not isinstance(raw_evidence, list):
            raw_evidence = [raw_evidence]
        evidence_items = [
            EvidenceItem("LLM", str(e)[:300], 10.0)
            for e in raw_evidence[:8]
        ]

        # --- Confidence parsing (handles multiple LLM output formats) ---
        # LLM may return confidence as:
        #   - numeric 0-1: {"confidence": 0.45}
        #   - numeric 0-100: {"confidence": 45} or {"confidence_score": 45}
        #   - categorical: {"confidence": "low"/"medium"/"high"/"speculative"}
        confidence_map = {"high": 0.8, "medium": 0.6, "speculative": 0.4, "low": 0.3}
        raw_confidence_val = llm_result.get("confidence", None)

        # Try numeric parse first (handles 0.45, 45, "0.45", "45")
        numeric_from_confidence = None
        if raw_confidence_val is not None:
            try:
                num = float(raw_confidence_val)
                # Heuristic: if >1, assume 0-100 scale; otherwise 0-1
                if num > 1.0:
                    numeric_from_confidence = num / 100.0
                else:
                    numeric_from_confidence = num
                numeric_from_confidence = max(0.0, min(numeric_from_confidence, 1.0))
            except (TypeError, ValueError):
                pass

        # Also check confidence_score field (0-100 scale)
        numeric_from_score = None
        raw_score_val = llm_result.get("confidence_score", None)
        if raw_score_val is not None:
            try:
                numeric_from_score = float(raw_score_val) / 100.0
                numeric_from_score = max(0.0, min(numeric_from_score, 1.0))
            except (TypeError, ValueError):
                pass

        # Categorical fallback
        raw_confidence_str = str(raw_confidence_val or "medium").lower().strip()
        mapped_confidence = confidence_map.get(raw_confidence_str, 0.5)

        # Blend: prefer numeric from confidence field, then confidence_score,
        # then categorical. Weights: 70% primary numeric + 30% categorical.
        if numeric_from_confidence is not None:
            confidence = 0.7 * numeric_from_confidence + 0.3 * mapped_confidence
        elif numeric_from_score is not None:
            confidence = 0.7 * numeric_from_score + 0.3 * mapped_confidence
        else:
            confidence = mapped_confidence

        confidence = round(max(0.05, min(confidence, 0.95)), 3)
        confidence_score = int(confidence * 100)

        # DEBUG: Log final confidence for diagnosis
        logger.info(
            "LLM confidence result for %s: raw=%s → final=%.3f (%.0f%%)",
            symbol, raw_confidence_val, confidence, confidence * 100,
        )

        opinion = AgentOpinion(
            agent_name="LLM",
            direction=raw_direction,
            confidence=confidence,
            evidence=evidence_items,
            reasoning=llm_result.get("reasoning", ""),
            raw_score=confidence_score,
        )

        # Build decision result
        raw_position_size = llm_result.get("position_size_factor", 0.5) or 0.5
        # Short quality penalty: historical shorts lose -134.54 vs longs +545.88.
        # Halve short exposure to reduce the dominant loss source while keeping
        # the option to short when conviction is high.
        SHORT_SIZE_MULTIPLIER = 0.5
        # UNI variance control: UNI produced the biggest win (+423.72) AND
        # biggest loss (-232.40) — extreme single-symbol variance.  Cap UNI
        # position size to 60% of what the LLM recommends to keep the symbol
        # from dominating the portfolio.
        UNI_SIZE_MULTIPLIER = 0.6
        position_size = raw_position_size
        if raw_direction == "short":
            position_size *= SHORT_SIZE_MULTIPLIER
        if symbol.upper().startswith("UNI"):
            position_size *= UNI_SIZE_MULTIPLIER
        vote = confidence if raw_direction == "long" else (-confidence if raw_direction == "short" else 0.0)
        decision_result = DecisionResult(
            direction=raw_direction,
            confidence=confidence,
            position_size_factor=position_size,
            conflict_detected=False,
            conflict_description="",
            agent_votes={"LLM": {
                "direction": raw_direction,
                "confidence": confidence,
                "weight": 1.0,
                "vote": vote,
            }},
        )

        # Rule checks
        preliminary_report = DecisionReport(
            symbol=symbol,
            market_type=market_type,
            timestamp="",
            final_direction=decision_result.direction,
            final_confidence=decision_result.confidence,
            position_size_factor=decision_result.position_size_factor,
            agent_opinions=[opinion],
            rule_checks=[],
        )
        rule_results = run_rules(preliminary_report)

        # Generate final report
        report = generate_report(
            symbol=symbol,
            market_type=market_type,
            decision=decision_result,
            agent_opinions=[opinion],
            rule_results=rule_results,
            decision_id=decision_id,
        )
        # Attach LLM routing provenance (which model served the request) so the
        # persisted committee record can attribute the opinion to a real model.
        report.model_meta = llm_result.get("_model_meta") or {}

        # Capture LLM-authored risk warnings (风险提示) for the persisted record.
        # Production models use the "risks" key; accept both spellings.
        raw_risks = llm_result.get("risks") or llm_result.get("key_risks") or []
        if not isinstance(raw_risks, list):
            raw_risks = [raw_risks]
        report.risk_warnings = [str(r)[:300] for r in raw_risks][:8]

        # DEBUG: Log final report confidence
        logger.info(
            "LLM evaluate_with_llm DONE for %s: report.conf=%.3f es=%.2f",
            symbol, report.final_confidence, report.explainability_score,
        )

        return report
