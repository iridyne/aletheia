"""Universe Signal Emitter — deterministic opportunity generation from universe members.

Converts high-scoring universe members into Opportunity rows without requiring LLM.
This decouples market sensing from LLM availability:
  - LLM up:   market_observer enriches the existing opportunity with language/context
  - LLM down: universe_scanner opportunities still flow to paper_trader ranker

Called at the end of every universe refresh cycle (source_agent="universe_scanner").
"""

from __future__ import annotations

import logging

from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import (
    insert_opportunity,
    find_open_opportunity_by_symbol_snapshot,
)

logger = logging.getLogger("aletheia.universe.signal_emitter")

# Only emit for members that scored high enough to be worth trading
_MIN_SCORE = 45.0
# total_score threshold for "high" vs "medium" confidence label
_HIGH_CONF_THRESHOLD = 70.0


def emit_deterministic_opportunities(
    members: list[dict],
    snapshot_id: str,
    scored_features: list[dict],
    linked_strategy_id: str | None = None,
) -> int:
    """Generate one deterministic Opportunity per eligible high-scoring universe member.

    Args:
        members: inserted_members list from service.refresh() — each has id, symbol,
                 snapshot_id, scanner_family, total_score, expires_at.
        snapshot_id: active universe snapshot id.
        scored_features: the `scored` list from refresh — carries scanner reasons +
                         raw feature values (return_1h, rsi_14, funding_rate, …).
        linked_strategy_id: optional strategy to attach.

    Returns:
        Number of opportunities created.
    """
    feat_by_symbol: dict[str, dict] = {f["symbol"]: f for f in scored_features}
    created = 0

    for m in members:
        symbol = m.get("symbol", "")
        member_id = m.get("id")
        family = m.get("scanner_family")
        total_score = float(m.get("total_score") or 0)

        if not symbol or not member_id or not family:
            continue
        if total_score < _MIN_SCORE:
            continue

        feat = feat_by_symbol.get(symbol)
        if not feat:
            continue

        direction = _infer_direction(family, feat)
        if direction is None:
            continue  # ambiguous — do not produce neutral noise

        # De-duplicate: same symbol already has an open opportunity from this snapshot
        existing = find_open_opportunity_by_symbol_snapshot(symbol, snapshot_id)
        if existing:
            continue

        confidence = "high" if total_score >= _HIGH_CONF_THRESHOLD else "medium"
        reasons: list[str] = feat.get("reasons") or []
        thesis = (
            f"[{family}] {'; '.join(reasons)}" if reasons
            else f"[{family}] score={total_score:.0f}"
        )

        opp = Opportunity(
            symbol=symbol,
            market="crypto",
            direction_bias=direction,
            thesis=thesis,
            confidence=confidence,
            confidence_score=int(round(total_score)),
            source_agent="universe_scanner",
            linked_strategy_id=linked_strategy_id,
            asset_class="crypto",
            execution_eligible=True,
            universe_snapshot_id=snapshot_id,
            universe_member_id=member_id,
            scanner_family=family,
            valid_until=m.get("expires_at"),
        )
        try:
            insert_opportunity(opp)
            created += 1
            logger.debug("emitted %s %s %s score=%.0f", family, direction, symbol, total_score)
        except Exception as e:
            logger.warning("Failed to emit opportunity for %s: %s", symbol, e)

    if created:
        logger.info(
            "Universe signal emitter: %d deterministic opportunities (snapshot=%s)",
            created, snapshot_id[:8],
        )
    return created


def _infer_direction(family: str | None, feat: dict) -> str | None:
    """Deterministically infer trade direction from scanner family + feature values.

    Returns "long", "short", or None (no clear signal — caller must skip).
    """
    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")
    rsi = feat.get("rsi_14")
    funding = feat.get("funding_rate")

    if family == "momentum":
        # Momentum: follow the dominant short-term trend with multi-timeframe agreement
        if ret_1h is not None and ret_1h > 0:
            if (ret_4h is not None and ret_4h > 0) or (rsi is not None and rsi > 50):
                return "long"
        if ret_1h is not None and ret_1h < 0:
            if (ret_4h is not None and ret_4h < 0) or (rsi is not None and rsi < 50):
                return "short"
        return None

    if family == "mean_reversion":
        # Mean reversion: fade extreme moves — oversold → long, overbought → short
        if rsi is not None:
            if rsi <= 30:
                return "long"
            if rsi >= 70:
                return "short"
        if ret_4h is not None:
            if ret_4h < -5.0:
                return "long"   # overextended down → expect bounce
            if ret_4h > 5.0:
                return "short"  # overextended up → expect pullback
        return None

    if family == "volatility":
        # Volatility expansion: follow the direction of the breakout
        if ret_1h is not None:
            return "long" if ret_1h > 0 else "short"
        return None

    if family == "derivatives":
        # Derivatives: fade crowded positions via funding rate signal
        # Negative funding → shorts paying → crowded short → fade → long
        # Positive funding → longs paying → crowded long → fade → short
        if funding is not None:
            if funding < -0.005:
                return "long"
            if funding > 0.005:
                return "short"
        # Fall back to 4h momentum if no funding data
        if ret_4h is not None:
            return "long" if ret_4h > 0 else "short"
        return None

    return None
