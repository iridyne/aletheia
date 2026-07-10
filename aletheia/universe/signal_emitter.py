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
from aletheia.storage.repositories import insert_opportunity, find_open_opportunity_by_symbol_snapshot
from aletheia.trading.instruments import instrument_market_to_db_market
from aletheia.analytics.factor_feedback import lookup_factor_signal
from aletheia.universe.snapshot_freshness import has_fresh_market_snapshot

logger = logging.getLogger("aletheia.universe.signal_emitter")

# Only emit for members that scored high enough to be worth trading
_MIN_SCORE = 45.0
# total_score threshold for "high" vs "medium" confidence label.
# The allocator's score ceiling is ~68.5 (opportunity 0.35×100 + tradability
# 0.25×100 + liquidity 0.15×90 − min cost 5.0), so a 70 threshold made "high"
# unreachable — every emitted opportunity was forced to "medium". 60 keeps a
# meaningful split: medium = [45, 60), high = [60, ceiling].
_HIGH_CONF_THRESHOLD = 60.0

# Regime bias threshold: if the direction's bias < this value, skip signal
# unless score compensates (higher score can override weak regime alignment).
_REGIME_BIAS_THRESHOLD = 0.30


def emit_deterministic_opportunities(
    members: list[dict],
    snapshot_id: str,
    scored_features: list[dict],
    linked_strategy_id: str | None = None,
    regime=None,
    short_enabled: bool = True,
) -> int:
    """Generate one deterministic Opportunity per eligible high-scoring universe member.

    Args:
        members: inserted_members list from service.refresh() — each has id, symbol,
                 snapshot_id, scanner_family, total_score, expires_at.
        snapshot_id: active universe snapshot id.
        scored_features: the `scored` list from refresh — carries scanner reasons +
                         raw feature values (return_1h, rsi_14, funding_rate, …).
        linked_strategy_id: optional strategy to attach.
        regime: MarketRegime instance (or None). When provided, signals whose
                direction conflicts with regime bias are filtered out.

    Returns:
        Number of opportunities created.
    """
    # Key by (symbol, market_type): the same symbol can appear as both spot and
    # perp in one refresh. Keying by symbol alone let one overwrite the other, so
    # an emitted opportunity could inherit funding/direction from the wrong market.
    feat_by_key: dict[tuple, dict] = {
        (f["symbol"], f.get("market_type", "spot")): f for f in scored_features
    }
    created = 0

    for m in members:
        symbol = m.get("symbol", "")
        member_id = m.get("id")
        family = m.get("scanner_family")
        total_score = float(m.get("total_score") or 0)
        market_type = m.get("market_type", "spot") or "spot"

        if not symbol or not member_id or not family:
            continue
        if total_score < _MIN_SCORE:
            continue

        feat = feat_by_key.get((symbol, market_type))
        if not feat:
            continue

        # Prefer scanner-intrinsic direction; fallback to _infer_direction
        direction = feat.get("scanner_direction") or _infer_direction(family, feat)
        if direction is None:
            logger.warning(
                "no direction for %s family=%s score=%.0f — skipped",
                symbol, family, total_score,
            )
            continue

        # Factor feedback gate: block signals from proven-bad factor combinations
        try:
            factor_sig = lookup_factor_signal({
                "scanner_family": family,
                "direction": direction,
                "symbol": symbol,
            })
            if factor_sig.get("status") == "blocked_candidate":
                logger.debug(
                    "factor-blocked %s %s %s (wr=%.0f%% exp=%.4f)",
                    family, direction, symbol,
                    (factor_sig.get("win_rate") or 0) * 100,
                    factor_sig.get("expectancy") or 0,
                )
                continue
        except Exception:
            pass

        # Regime bias gate: filter signals that conflict with current regime
        if regime is not None and not _passes_regime_gate(direction, total_score, regime, short_enabled):
            logger.debug(
                "regime-filtered %s %s %s (regime=%s, bias=%.2f)",
                family, direction, symbol, regime.state,
                regime.short_bias if direction == "short" else regime.long_bias,
            )
            continue

        # De-duplicate: same symbol + market_type already has an open opportunity from
        # this snapshot.  Same read is a different market (e.g. spot vs perp — same
        # price, same signal).  Dedup OD-S02 locks: only the first market's opp is a hit.
        existing = find_open_opportunity_by_symbol_snapshot(
            symbol, snapshot_id,
            market=instrument_market_to_db_market(market_type),
        )
        if existing:
            continue

        confidence = "high" if total_score >= _HIGH_CONF_THRESHOLD else "medium"
        # Proxy derivatives signals (RSI fallback, no funding/OI data) are weaker —
        # cap at medium to avoid triggering strategy_designer high-conf events.
        if family == "derivatives" and feat.get("is_proxy"):
            confidence = "medium"
        reasons: list[str] = feat.get("reasons") or []
        thesis = (
            f"[{family}] {'; '.join(reasons)}" if reasons
            else f"[{family}] score={total_score:.0f}"
        )

        # Guard: only set execution_eligible if there is live price data within the
        # last 2 hours. Symbols with zero snapshots would otherwise reach PI Agent as
        # tradeable but fail silently at proposal submission (price=0 → proposal_id=None).
        _exec_eligible = has_fresh_market_snapshot(symbol, market_type)
        if not _exec_eligible:
            logger.warning(
                "no_live_price_for_symbol: %s — execution_eligible set to False", symbol
            )

        opp = Opportunity(
            symbol=symbol,
            market=instrument_market_to_db_market(market_type),
            direction_bias=direction,
            thesis=thesis,
            confidence=confidence,
            confidence_score=int(round(total_score)),
            source_agent="universe_scanner",
            linked_strategy_id=linked_strategy_id,
            asset_class="crypto",
            execution_eligible=_exec_eligible,
            universe_snapshot_id=snapshot_id,
            universe_member_id=member_id,
            scanner_family=family,
            valid_until=m.get("expires_at"),
            regime_tag=regime.state if regime else None,
            funding_rate=feat.get("funding_rate"),
            open_interest=feat.get("open_interest"),
            oi_delta_1h=feat.get("oi_delta_1h"),
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
        try:
            from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
            notify_pi_daemon(source="signal_emitter")
        except Exception:
            pass

    return created


def _passes_regime_gate(direction: str, total_score: float, regime, short_enabled: bool = True) -> bool:
    """Check if a signal's direction passes the regime bias gate.

    Logic:
      - Get the bias for this direction (long_bias or short_bias)
      - If bias >= threshold, always pass
      - If bias < threshold, only pass if score is exceptionally high (top 20% above _HIGH_CONF_THRESHOLD)
        This allows strong contrarian signals to survive even in hostile regimes.

    US-9: short_enabled=False is a HARD operator switch — no score may override it.
    Previously a high-score short slipped through the score-override branch below,
    silently violating the operator's global short disable.
    """
    if direction == "short" and not short_enabled:
        return False

    bias = regime.long_bias if direction == "long" else regime.short_bias

    if bias >= _REGIME_BIAS_THRESHOLD:
        return True

    # Strong signals can override weak regime alignment
    if total_score >= _HIGH_CONF_THRESHOLD * 1.1:
        return True

    return False


def _funding_conflicts(direction: str, funding: float | None) -> bool:
    """Check if funding rate conflicts with proposed direction.

    Crowded longs (high positive funding) conflict with long direction;
    crowded shorts (extreme negative funding) conflict with short direction.
    """
    if funding is None:
        return False
    if direction == "long" and funding > 0.01:
        return True
    if direction == "short" and funding < -0.01:
        return True
    return False


def _infer_direction(family: str | None, feat: dict) -> str | None:
    """Deterministically infer trade direction from scanner family + feature values.

    Returns "long", "short", or None (no clear signal — caller must skip).

    Phase 0 improvements:
      - 0.1: Momentum rejects when 24h already overextended (mean-reversion zone)
      - 0.2: Rejects when 1h and 4h directions conflict
      - 0.3: Funding rate confirmation across all families
      - 0.4: Volatility uses 4h direction instead of noisy 1h
    """
    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")
    rsi = feat.get("rsi_14")
    funding = feat.get("funding_rate")

    if family == "momentum":
        # 0.1: 24h already overextended → mean-reversion zone, don't chase
        if ret_24h is not None and abs(ret_24h) > 10.0:
            return None

        # 0.2: 1h vs 4h direction conflict → no clear trend
        if ret_1h is not None and ret_4h is not None and ret_1h * ret_4h < 0:
            return None

        # Direction from 4h (more stable than 1h) with RSI confirmation
        direction = None
        if ret_4h is not None and ret_4h > 0:
            if rsi is not None and rsi > 50:
                direction = "long"
        elif ret_4h is not None and ret_4h < 0:
            if rsi is not None and rsi < 50:
                direction = "short"

        # 0.3: Funding conflict → reject
        if direction and _funding_conflicts(direction, funding):
            return None
        return direction

    if family == "mean_reversion":
        # Mean reversion: fade extreme moves — oversold → long, overbought → short
        direction = None
        if rsi is not None:
            if rsi <= 30:
                direction = "long"
            elif rsi >= 70:
                direction = "short"
        if direction is None and ret_4h is not None:
            if ret_4h < -5.0:
                direction = "long"
            elif ret_4h > 5.0:
                direction = "short"

        # For MR, funding CONFIRMS (crowded side = reversion more likely)
        # so we don't filter on funding conflict here
        return direction

    if family == "volatility":
        # 0.4: Use 4h direction (more autocorrelation than 1h noise)
        if ret_4h is not None and abs(ret_4h) > 1.0:
            direction = "long" if ret_4h > 0 else "short"
            # 0.3: Funding conflict → reject
            if _funding_conflicts(direction, funding):
                return None
            return direction
        return None

    if family == "derivatives":
        # Derivatives: fade crowded positions via funding rate signal
        if funding is not None:
            if funding < -0.005:
                return "long"
            if funding > 0.005:
                return "short"
        # Fall back to 4h momentum if no funding data
        if ret_4h is not None and ret_4h != 0:
            direction = "long" if ret_4h > 0 else "short"
            return direction
        return None

    # NOTE: family == "short_bias" is NOT handled here. It is a first-class
    # scanner family whose direction is emitted intrinsically by
    # scanners.score_short_bias() → run_all_scanners() sets scanner_direction,
    # so _infer_direction is never the source for short_bias members. Keeping a
    # duplicate threshold branch here would risk drift (2026-07-01: the previous
    # duplicate was dead code + a fake-GREEN spec; removed once the scorer landed).

    if family == "event":
        # Event catalyst: follow the sharp 1h displacement. Thresholds mirror
        # score_event (>4% long / <-4% short) so a fallback here agrees with the
        # scanner's intrinsic direction. Only used when scanner_direction is
        # absent — the primary path prefers the scanner's own direction.
        if ret_1h is not None:
            if ret_1h > 4.0:
                return "long"
            if ret_1h < -4.0:
                return "short"
        return None

    return None
