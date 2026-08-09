"""LLM-powered Investment Committee Agent.

Replaces the deterministic Trend/Macro/News/Risk agents with a single
LLM call that performs multi-dimensional analysis and returns structured
output with reasoning.

This is the "AI" in AI Investment Committee — the LLM reads market data
and explains WHY it makes a recommendation.

Intelligent routing:
  - Uses complete_with_fallback() so 429/rate-limit errors auto-fallback
    to the next model in the configured chain (flash-lite → openrouter).
  - Tracks per-model health via the fallback module's cooldown mechanism.
  - JSON parsing is robust to raw-string LLM outputs (e.g. "NEUTRAL").
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger("aletheia.committee.llm")


# --- Prompt Templates ---

_SYSTEM_PROMPT = """\
You are an AI Investment Committee analyst for Aletheia crypto paper-trading system.

Your job: analyze ONE trading opportunity and provide a structured recommendation.

Analysis framework:
1. TREND: Price vs moving averages, momentum, RSI — is the trend bullish/bearish?
2. MACRO: Fear & Greed, ETF flows, DXY, on-chain data — does macro support the trade?
3. RISK: Volatility (ATR), portfolio heat, consecutive losses — can we take this risk?
4. EVENTS: Recent news, upcoming catalysts — any event-driven edge?

Output: structured JSON with direction, confidence, reasoning, evidence, risks, and position size.

Be specific — cite actual numbers from the data. Be honest — say "neutral" if signals conflict.

Respond with ONLY the JSON object, no markdown fences, no extra text.
"""


def _build_user_prompt(symbol: str, market_type: str, data: dict[str, Any]) -> str:
    """Build a comprehensive user prompt from market data."""

    sections = [f"# Market Analysis: {symbol} ({market_type})"]

    # Price & Trend
    price_data = {}
    for key in ["last_price", "sma20", "sma50", "rsi14", "rsi4h", "bb_pct",
                "mom10", "return_1h", "return_4h", "return_24h"]:
        if data.get(key) is not None:
            price_data[key] = data[key]
    if price_data:
        sections.append("## Price & Trend")
        for k, v in price_data.items():
            sections.append(f"  {k}: {v}")

    # Volume & Flow
    flow_data = {}
    for key in ["vol_ratio", "volume_z", "taker_buy_ratio", "oi_delta_1h", "oi_price_divergence"]:
        if data.get(key) is not None:
            flow_data[key] = data[key]
    if flow_data:
        sections.append("## Volume & Flow")
        for k, v in flow_data.items():
            sections.append(f"  {k}: {v}")

    # Derivatives
    deriv_data = {}
    for key in ["funding_rate", "open_interest", "oi_delta_1h"]:
        if data.get(key) is not None:
            deriv_data[key] = data[key]
    if deriv_data:
        sections.append("## Derivatives")
        for k, v in deriv_data.items():
            sections.append(f"  {k}: {v}")

    # Macro Environment
    macro_data = {}
    for key in ["fear_greed", "etf_flow_24h", "dxy_change", "onchain_mvrv", "stablecoin_supply_change"]:
        if data.get(key) is not None:
            macro_data[key] = data[key]
    if macro_data:
        sections.append("## Macro Environment")
        for k, v in macro_data.items():
            sections.append(f"  {k}: {v}")

    # News
    headlines = data.get("recent_headlines", [])
    if headlines:
        sections.append("## Recent News")
        for hl in headlines[:5]:
            sections.append(f"  - {hl}")

    # Risk Context
    risk_data = {}
    for key in ["atr_pct", "current_portfolio_heat", "consecutive_losses", "account_drawdown_pct"]:
        if data.get(key) is not None:
            risk_data[key] = data[key]
    if risk_data:
        sections.append("## Risk Context")
        for k, v in risk_data.items():
            sections.append(f"  {k}: {v}")

    # Regime
    if data.get("regime"):
        sections.append(f"## Current Regime: {data['regime']}")

    sections.append("")
    sections.append("Analyze this data and provide your recommendation as JSON.")

    return "\n".join(sections)


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    """Robustly parse JSON from LLM response.

    Handles:
    - Standard JSON objects
    - JSON wrapped in markdown fences (```json ... ```)
    - Raw string responses like "NEUTRAL" or "bullish" (mapped to structured output)
    - Truncated JSON (returns None)
    """
    text = text.strip()

    if not text:
        return None

    # Case 1: Raw string response (no JSON structure at all)
    # Map common directional words to structured output
    raw_lower = text.lower().strip()
    direction_map = {
        "neutral": "neutral",
        "bullish": "long",
        "bearish": "short",
        "long": "long",
        "short": "short",
    }
    if raw_lower in direction_map:
        logger.debug("LLM returned raw string '%s', mapping to %s", text, direction_map[raw_lower])
        return {
            "direction": direction_map[raw_lower],
            "confidence": "low",
            "confidence_score": 30,
            "reasoning": f"LLM returned brief response: {text}",
            "key_evidence": [],
            "position_size_factor": 0.3,
        }

    # Case 2: Markdown fences
    if "```" in text:
        # Extract content between fences
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.lower().startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
        else:
            # No JSON-like content found in fences
            return None

    # Case 3: Try direct JSON parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        # Handle JSON-encoded string (e.g. '"LONG"' → 'LONG')
        if isinstance(result, str):
            str_lower = result.lower().strip()
            if str_lower in direction_map:
                logger.debug("LLM returned JSON string '%s', mapping to %s", result, direction_map[str_lower])
                return {
                    "direction": direction_map[str_lower],
                    "confidence": "low",
                    "confidence_score": 30,
                    "reasoning": f"LLM returned brief response: {result}",
                    "key_evidence": [],
                    "position_size_factor": 0.3,
                }
        return None
    except json.JSONDecodeError:
        pass

    # Case 4: Try to extract JSON object from mixed content
    # Find the first { and try to parse from there
    brace_start = text.find("{")
    if brace_start >= 0:
        # Try progressively shorter substrings to handle truncated JSON
        for end in range(len(text), brace_start, -1):
            try:
                result = json.loads(text[brace_start:end])
                if isinstance(result, dict):
                    logger.debug("Parsed partial JSON (chars %d-%d)", brace_start, end)
                    return result
            except json.JSONDecodeError:
                continue

    # Case 5: Try to fix common JSON issues (trailing commas, single quotes)
    try:
        # Remove trailing commas before } or ]
        fixed = re.sub(r',\s*([}\]])', r'\1', text)
        result = json.loads(fixed)
        if isinstance(result, dict):
            logger.debug("Parsed JSON after trailing-comma fix")
            return result
    except json.JSONDecodeError:
        pass

    return None


# --- Main Analysis Function ---

async def analyze_with_llm(
    symbol: str,
    market_type: str,
    data: dict[str, Any],
    provider: str | None = None,
) -> dict[str, Any] | None:
    """Call LLM to analyze a trading opportunity.

    Uses complete_with_fallback() for intelligent model routing:
    - Primary: sensenova/deepseek-v4-flash
    - Fallback 1: sensenova-6.7-flash-lite (lighter, same provider)
    - Fallback 2: openrouter/nemotron-3-ultra-550b-a55b:free (cross-provider)

    The fallback mechanism handles 429 rate-limit errors automatically
    and tracks per-model health via cooldown.

    Args:
        symbol: trading symbol (e.g. "BTC/USDT")
        market_type: market type (e.g. "perp")
        data: market data dict (price, indicators, macro, news, risk)
        provider: optional LLM provider name (default: sensenova)

    Returns:
        Dict with direction, confidence, reasoning, evidence, risks, etc.
        or None if all LLM attempts failed.
    """
    try:
        from aletheia.model_provider.fallback import complete_with_fallback
        from aletheia.model_provider.base import ModelRequest

        user_prompt = _build_user_prompt(symbol, market_type, data)

        req = ModelRequest(
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            system=_SYSTEM_PROMPT,
            temperature=0.1,  # lower temp for more deterministic JSON output
            max_tokens=1500,
        )

        # Use complete_with_fallback for intelligent routing
        # This will try the primary model first, then fall back through
        # the configured chain on transient errors (429, 502, 503, 504)
        provider_name = provider or "sensenova"
        resp = await complete_with_fallback(
            req,
            provider_name=provider_name,
        )

        # Parse JSON from response
        text = resp.content.strip()
        result = _parse_llm_json(text)

        if result is None:
            # Log FIRST 200 chars of raw response for diagnosis
            logger.warning(
                "LLM JSON parse failed for %s (provider=%s): %.200s",
                symbol, provider_name, text,
            )
            return None

        # Log missing direction (another failure path)
        if "direction" not in result:
            logger.warning(
                "LLM missing direction for %s: keys=%s raw=%.100s",
                symbol, list(result.keys()), text,
            )
            return None

        # Log success with key fields
        logger.info(
            "LLM OK %s: dir=%s conf=%s keys=%s",
            symbol, result.get("direction"), result.get("confidence"), list(result.keys()),
        )

        # Validate required fields
        if "direction" not in result:
            logger.warning("LLM response missing direction for %s", symbol)
            return None

        # Log which model actually served the request (useful for debugging)
        fallback_meta = getattr(resp, 'raw', {})
        model_meta: dict[str, Any] = {}
        if isinstance(fallback_meta, dict):
            fb = fallback_meta.get('aletheia_model_fallback', {})
            model_meta = {
                "requested_model": fb.get('requested_model'),
                "final_model": fb.get('final_model'),
                "fallback_used": bool(fb.get('fallback_used')),
            }
            if fb.get('fallback_used'):
                logger.info(
                    "LLM fallback used for %s: %s → %s",
                    symbol,
                    fb.get('requested_model', '?'),
                    fb.get('final_model', '?'),
                )

        # Underscore-prefixed metadata: routing provenance for persistence,
        # never part of the LLM's own structured output contract.
        result["_model_meta"] = model_meta

        return result

    except asyncio.TimeoutError:
        logger.warning("LLM call timed out for %s", symbol)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("LLM JSON parse error for %s: %s", symbol, exc)
        return None
    except Exception as exc:
        # Catch-all for provider errors (including "all models cooling down")
        logger.warning("LLM call failed for %s: %s: %s", symbol, type(exc).__name__, exc)
        return None
