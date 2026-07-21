"""Prediction Observer agent — monitors Polymarket prediction markets.

Fetches active markets from Polymarket's public Gamma API (no auth needed),
asks the LLM to identify notable probability trends, and creates
asset_class="prediction" Opportunities for informational tracking.

Design choices:
- asset_class = "prediction" — paper_trader IGNORES these (observation-only).
- direction_bias maps to probability momentum:
    "long" = YES probability rising (bet is gaining)
    "short" = YES probability falling (bet is losing ground)
    "neutral" = stable probability
- symbol = market question slug (truncated to 80 chars for readability)
- market = "prediction"
- Degrades fully: any Polymarket API error → logs and returns empty.
- Each notable market gets its own Opportunity with structured metadata.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import (
    insert_opportunity, list_strategies,
    upsert_prediction_event, upsert_prediction_market,
    insert_probability_snapshot, insert_prediction_claim,
    insert_serenity_evidence, list_serenity_themes,
)
from aletheia.config.loader import load_config
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}
_OPPORTUNITY_SCANNER_FAMILY = "prediction_market"
_OPPORTUNITY_TTL = timedelta(minutes=30)

logger = logging.getLogger("aletheia.agents.prediction_observer")


class PredictionMarketRead(BaseModel):
    """Structured read for a prediction market.

    direction_bias here means probability momentum, not price direction:
    - "long"    = YES probability is rising (the event is becoming more likely)
    - "short"   = YES probability is falling (the event is becoming less likely)
    - "neutral" = probability is stable
    """
    direction_bias: Literal["long", "short", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "speculative"] = "speculative"
    confidence_score: int = Field(
        50, ge=0, le=100,
        description="0=no trend signal, 50=balanced, 100=strong trend signal"
    )
    rationale: str = ""
    key_observations: list[str] = Field(
        default_factory=list,
        description="2-3 bullets citing probability levels and volume"
    )


_SYSTEM_PROMPT = """\
You are a prediction market analyst for Aletheia.

You will receive a table of active Polymarket prediction markets. Each row shows:
- The YES probability (0–100%)
- 24h probability change (positive = rising, negative = falling)
- Trading volume in USD
- Resolution date

Your task: identify the 1-3 most notable markets by probability momentum.
For each notable market, output a structured read.

direction_bias (probability momentum interpretation):
- "long"    = YES probability is rising meaningfully (strong trend toward resolution YES)
- "short"   = YES probability is falling meaningfully (strong trend toward resolution NO)
- "neutral" = probability is stable or changes are noise

confidence:
- "high"    = large 24h probability shift (>10pp) AND high volume (>$100k)
- "medium"  = moderate shift (5-10pp) OR decent volume (>$10k)
- "speculative" = small shift or thin market

key_observations: 2-3 bullets citing probability values and volume.

Focus on markets most relevant to financial/macro themes when possible.
This is informational analysis only — not trading advice."""


def _persist_normalized(markets: list, *, run_id: str | None = None) -> dict[str, str]:
    """Write normalized prediction_events + prediction_markets + snapshots.

    Returns a mapping of market.market_id -> internal prediction_markets.id
    so callers can reference the normalized row.
    """
    from aletheia.storage.models import _utcnow
    market_id_map: dict[str, str] = {}
    for market in markets:
        ext_market_id = market.market_id or market.slug or market.question[:40]
        slug = market.slug or ""
        event_id = upsert_prediction_event({
            "external_event_id": ext_market_id,
            "slug": slug,
            "title": market.question,
            "category": market.category,
            "resolution_at": market.resolution_date or None,
            "liquidity": market.liquidity,
            "volume_24h": market.volume_usd,
            "source": "polymarket",
            "status": "active",
        })
        internal_market_id = upsert_prediction_market({
            "event_id": event_id,
            "external_market_id": ext_market_id,
            "question": market.question,
            "yes_probability": market.yes_probability,
            "no_probability": round(1.0 - market.yes_probability, 4),
            "probability_change_24h": market.prob_change_24h,
            "enable_orderbook": int(market.enable_orderbook),
            "liquidity": market.liquidity,
            "volume_24h": market.volume_usd,
            "source": "polymarket",
            "status": "active",
        })
        market_id_map[ext_market_id] = internal_market_id
        insert_probability_snapshot({
            "market_id": internal_market_id,
            "ts": _utcnow(),
            "yes_probability": market.yes_probability,
            "no_probability": round(1.0 - market.yes_probability, 4),
            "liquidity": market.liquidity,
            "volume_24h": market.volume_usd,
        })
    return market_id_map


def _generate_heuristic_claims(market, *, internal_market_id: str, run_id: str | None = None, median_volume: float = 0) -> None:
    """Write heuristic claims for notable market conditions."""
    if abs(market.prob_change_24h) > 0.10:
        direction = "long" if market.prob_change_24h > 0 else "short"
        insert_prediction_claim({
            "market_id": internal_market_id,
            "run_id": run_id,
            "claim_text": f"Probability moved {market.prob_change_24h*100:+.1f}pp in 24h — strong momentum signal",
            "claim_type": "probability_momentum",
            "direction": direction,
            "confidence": min(0.9, 0.5 + abs(market.prob_change_24h)),
        })
    if median_volume > 0 and market.volume_usd > median_volume * 2:
        insert_prediction_claim({
            "market_id": internal_market_id,
            "run_id": run_id,
            "claim_text": f"Volume ${market.volume_usd:,.0f} is >2x median (${median_volume:,.0f}) — unusual activity",
            "claim_type": "volume_spike",
            "confidence": 0.7,
        })
    if market.liquidity < 5000 and market.liquidity > 0:
        insert_prediction_claim({
            "market_id": internal_market_id,
            "run_id": run_id,
            "claim_text": f"Liquidity only ${market.liquidity:,.0f} — thin market, prices may be unreliable",
            "claim_type": "liquidity_warning",
            "confidence": 0.8,
        })
    if market.resolution_date:
        from datetime import datetime, timezone
        try:
            end = datetime.fromisoformat(market.resolution_date.replace("Z", "+00:00"))
            days_left = (end - datetime.now(timezone.utc)).days
            if 0 < days_left < 7:
                insert_prediction_claim({
                    "market_id": internal_market_id,
                    "run_id": run_id,
                    "claim_text": f"Resolves in {days_left} days — approaching resolution, volatility expected",
                    "claim_type": "resolution_risk",
                    "confidence": 0.6,
                })
        except (ValueError, TypeError):
            pass


def _build_market_metadata(market, *, markets_scanned: int = 0) -> str:
    """Serialize prediction-specific fields into the Opportunity.metadata JSON."""
    return json.dumps({
        "event_title": market.question,
        "market_id": market.market_id,
        "slug": market.slug,
        "category": market.category,
        "yes_probability": market.yes_probability,
        "no_probability": round(1.0 - market.yes_probability, 4),
        "probability_change_24h": market.prob_change_24h,
        "volume_24h": market.volume_usd,
        "liquidity": market.liquidity,
        "resolution_at": market.resolution_date,
        "enable_orderbook": market.enable_orderbook,
        "markets_scanned": markets_scanned,
        "source": "polymarket",
    })


@register_agent("prediction_observer")
async def run_prediction_observer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Fetch Polymarket data and create prediction Opportunities for tracking."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    categories = cfg.market_data.observed_prediction_markets
    if not categories:
        outputs.append("no_prediction_markets_configured")
        return {"outputs": outputs, "errors": errors}

    # Start observer run record
    from aletheia.storage.repositories import (
        insert_prediction_run, update_prediction_run,
    )
    from aletheia.storage.models import new_id as _new_id
    obs_run_id = _new_id()
    insert_prediction_run(obs_run_id, categories)

    # Fetch markets from Polymarket
    from aletheia.data.polymarket_provider import fetch_active_markets, format_prediction_context
    try:
        markets = await fetch_active_markets(categories=categories, limit=50)
        log_tool(run_id, "fetch_prediction_markets", "observe", True,
                 output=f"markets:{len(markets)}")
    except Exception as e:
        errors.append(f"polymarket_fetch_failed:{e}")
        update_prediction_run(obs_run_id, status="failed", errors=errors)
        return {"outputs": outputs, "errors": errors}

    if not markets:
        outputs.append("no_active_markets")
        update_prediction_run(obs_run_id, status="completed", markets_scanned=0)
        return {"outputs": outputs, "errors": errors}

    # Write normalized tables (events + markets + probability snapshots)
    try:
        market_id_map = _persist_normalized(markets, run_id=run_id)
    except Exception as e:
        errors.append(f"normalized_persist_partial:{e}")
        market_id_map = {}

    context = format_prediction_context(markets)
    record_step(
        run_id, "prediction_read_input",
        input_data=f"categories={categories} markets={len(markets)}",
        output_data=context[:1200],
    )

    # Find active strategy
    linked_strategy_id = None
    for s in list_strategies(limit=50):
        if is_active_paper_strategy(s):
            linked_strategy_id = s["id"]
            break

    # Ask LLM to identify notable markets
    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=(
            f"Prediction market data:\n{context}\n\n"
            "Identify the single most notable market by probability momentum "
            "and return the structured read JSON for it."
        ),
        schema=PredictionMarketRead,
        max_tokens=500,
    )
    log_tool(run_id, "llm_structured", "observe", True)

    direction = "neutral"
    confidence = "speculative"
    confidence_score = 0
    rationale = ""
    observations: list[str] = []

    if err or not read:
        errors.append(err or "no_structured_read")
    else:
        d = str(read.get("direction_bias", "")).lower()
        c = str(read.get("confidence", "")).lower()
        if d in _VALID_DIRECTIONS:
            direction = d
        if c in _VALID_CONFIDENCE:
            confidence = c
        rationale = str(read.get("rationale", ""))
        cs = read.get("confidence_score")
        if isinstance(cs, (int, float)) and 0 <= cs <= 100:
            confidence_score = int(cs)
        obs = read.get("key_observations") or []
        if isinstance(obs, list):
            observations = [str(o)[:200] for o in obs[:3] if o]

    record_step(
        run_id, "prediction_read_output",
        input_data=None,
        output_data=f"direction={direction} confidence={confidence} score={confidence_score}",
    )

    if not is_tool_permitted("create_opportunity", permission_mode):
        update_prediction_run(obs_run_id, status="completed",
                             markets_scanned=len(markets), opportunities_created=0)
        return {"outputs": outputs, "errors": errors}

    # Create one Opportunity per fetched market — capture all signals.
    # The LLM read applies to the top market; others get a heuristic bias.
    opps_created = 0
    opportunity_valid_until = (
        datetime.now(timezone.utc) + _OPPORTUNITY_TTL
    ).isoformat()
    for i, market in enumerate(markets):
        q = market.question
        symbol_slug = (q[:77] + "...") if len(q) > 80 else q

        if i == 0:
            # Top market gets the LLM-derived signal
            m_direction = direction
            m_confidence = confidence
            m_score = confidence_score
            thesis_parts = []
            if rationale:
                thesis_parts.append(rationale)
            if observations:
                thesis_parts.append("Observations: " + " | ".join(observations))
            thesis_parts.append(
                f"YES={market.yes_probability * 100:.1f}% "
                f"Δ24h={market.prob_change_24h * 100:+.1f}pp "
                f"vol=${market.volume_usd:,.0f}"
            )
            thesis = " | ".join(thesis_parts)
        else:
            # Heuristic: derive direction from 24h probability change
            change = market.prob_change_24h
            if change > 0.05:
                m_direction = "long"
            elif change < -0.05:
                m_direction = "short"
            else:
                m_direction = "neutral"
            m_confidence = "medium" if abs(change) > 0.03 and market.volume_usd > 10000 else "speculative"
            m_score = min(100, max(0, int(50 + change * 200)))
            thesis = (
                f"YES={market.yes_probability * 100:.1f}% "
                f"Δ24h={market.prob_change_24h * 100:+.1f}pp "
                f"vol=${market.volume_usd:,.0f}"
            )

        opp = Opportunity(
            symbol=symbol_slug,
            market="prediction",
            direction_bias=m_direction,
            thesis=thesis,
            confidence=m_confidence,
            confidence_score=m_score,
            source_agent="prediction_observer",
            linked_strategy_id=linked_strategy_id,
            scanner_family=_OPPORTUNITY_SCANNER_FAMILY,
            valid_until=opportunity_valid_until,
            asset_class="prediction",
            execution_eligible=False,
            metadata=_build_market_metadata(market, markets_scanned=len(markets)),
        )
        insert_opportunity(opp)
        try:
            from aletheia.agent_runtime.scheduler import notify_paper_trader

            notify_paper_trader(source="prediction_observer_opportunity")
        except Exception:
            logger.debug(
                "Failed to notify Ranker after prediction Opportunity commit",
                exc_info=True,
            )
        log_tool(run_id, "create_opportunity", "observe", True, output=opp.id)
        outputs.append(f"opportunity:{opp.id}")
        opps_created += 1

    # Generate claims for each market (heuristic + LLM observations for top market)
    median_vol = sorted(m.volume_usd for m in markets)[len(markets) // 2] if markets else 0
    for i, market in enumerate(markets):
        ext_id = market.market_id or market.slug or market.question[:40]
        internal_id = market_id_map.get(ext_id)
        if not internal_id:
            continue
        _generate_heuristic_claims(market, internal_market_id=internal_id,
                                   run_id=obs_run_id, median_volume=median_vol)
        if i == 0 and observations:
            for obs_text in observations:
                insert_prediction_claim({
                    "market_id": internal_id,
                    "run_id": obs_run_id,
                    "claim_text": obs_text,
                    "claim_type": "observation",
                    "direction": direction,
                    "confidence": (confidence_score or 50) / 100.0,
                    "source": "prediction_observer_llm",
                })

    # Phase 4: Write serenity_evidence for high-signal prediction markets
    # Criteria: |Δ24h| > 10pp AND volume > $50k AND question references crypto/macro
    _CRYPTO_KEYWORDS = {"bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
                        "defi", "fed", "rate", "inflation", "macro", "recession",
                        "tariff", "gdp", "cpi", "fomc", "treasury"}
    try:
        themes = list_serenity_themes(status="active", limit=50)
        for market in markets:
            if abs(market.prob_change_24h) < 0.10 or market.volume_usd < 50000:
                continue
            q_lower = market.question.lower()
            if not any(kw in q_lower for kw in _CRYPTO_KEYWORDS):
                continue
            matched_theme = None
            for theme in themes:
                theme_title = (theme.get("title") or "").lower()
                if any(kw in q_lower and kw in theme_title for kw in _CRYPTO_KEYWORDS):
                    matched_theme = theme
                    break
            if not matched_theme and themes:
                matched_theme = themes[0]
            if matched_theme:
                from aletheia.storage.models import SerenityEvidence
                from aletheia.trading.serenity_evidence import assess_evidence_tier
                ext_id = market.market_id or market.slug or market.question[:40]
                internal_id = market_id_map.get(ext_id, ext_id)
                ev = SerenityEvidence(
                    theme_id=matched_theme["id"],
                    symbol=None,
                    source="prediction_market",
                    source_ref=internal_id,
                    title=f"Prediction signal: {market.question[:80]}",
                    summary=(
                        f"YES={market.yes_probability*100:.1f}% "
                        f"Δ24h={market.prob_change_24h*100:+.1f}pp "
                        f"vol=${market.volume_usd:,.0f}"
                    ),
                    # Self-proposed grade on the canonical ladder; the independent evaluator
                    # caps prediction_market evidence at "speculative" regardless (D2).
                    tier="likely" if abs(market.prob_change_24h) > 0.15 else "speculative",
                    supports=market.prob_change_24h > 0,
                    evidence_type="catalyst",
                    actor="prediction_observer",
                    confidence="likely" if market.volume_usd > 100000 else "speculative",
                )
                ev.assessed_tier, ev.assessed_by = assess_evidence_tier(ev.model_dump())
                insert_serenity_evidence(ev)
                outputs.append(f"serenity_evidence:{ev.id}")
    except Exception as e:
        errors.append(f"serenity_evidence_link:{e}")

    update_prediction_run(obs_run_id, status="completed",
                         markets_scanned=len(markets),
                         opportunities_created=opps_created)
    return {"outputs": outputs, "errors": errors}
