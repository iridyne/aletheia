"""Scanner Families — score assets by opportunity type.

Each scanner takes a feature dict and returns (score: 0-100, reasons: list[str], direction: str|None).
Direction is intrinsic to the scoring logic — not inferred post-hoc.
run_all_scanners picks the best-scoring family for each asset.
"""

from __future__ import annotations

from aletheia.universe.funding_units import (
    CROWDED_FUNDING_RATE,
    ELEVATED_FUNDING_RATE,
    MILD_FUNDING_RATE,
)

_DERIVATIVES_MARKET_TYPES = frozenset({"perp", "swap", "futures", "usdt_futures"})

# Frozen identity for the current piecewise/step scoring behavior.  This is a
# measurement contract, not a runtime policy switch: changing it does not alter
# scores.  Any future scorer implementation must receive a new version only
# after its golden baseline is reviewed and approved.
UNIVERSE_SCORER_BEHAVIOR_VERSION = "universe-step-v1"


def score_momentum(feat: dict) -> tuple[float, list[str], str | None]:
    """Score trend continuation: relative strength + volume confirmation.

    Returns (score, reasons, direction).
    Direction is intrinsic — None means no clear directional edge.
    """
    score = 0.0
    reasons = []
    direction = None

    # Prefer beta-adjusted RS if available, fall back to simple RS
    rs_24h = feat.get("rs_beta_adj_24h") or feat.get("relative_strength_24h")
    rs_4h = feat.get("relative_strength_4h")
    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")
    vol_ratio = feat.get("vol_ratio")
    rsi_val = feat.get("rsi_14")
    oi_delta = feat.get("oi_delta_1h")
    funding = feat.get("funding_rate")

    # === Relative strength (core signal) ===
    if rs_24h is not None:
        if rs_24h > 5.0:
            score += 30.0
            direction = "long"
            reasons.append(f"RS24h +{rs_24h:.1f}% vs BTC")
        elif rs_24h > 2.5:
            score += 20.0
            direction = "long"
            reasons.append(f"RS24h +{rs_24h:.1f}% vs BTC")
        elif rs_24h < -5.0:
            score += 30.0
            direction = "short"
            reasons.append(f"RS24h {rs_24h:.1f}% vs BTC")
        elif rs_24h < -2.5:
            score += 20.0
            direction = "short"
            reasons.append(f"RS24h {rs_24h:.1f}% vs BTC")

    # Fallback: if no relative strength, use absolute 4h return
    if direction is None and ret_4h is not None:
        if ret_4h > 3.0:
            score += 15.0
            direction = "long"
            reasons.append(f"4h +{ret_4h:.1f}%")
        elif ret_4h < -3.0:
            score += 15.0
            direction = "short"
            reasons.append(f"4h {ret_4h:.1f}%")

    # === 4h relative strength confirmation ===
    if rs_4h is not None and direction is not None:
        if (direction == "long" and rs_4h > 1.0) or \
           (direction == "short" and rs_4h < -1.0):
            score += 15.0
            reasons.append("4h RS confirms")

    # === RSI directional confirmation (1h + 4h MTF) ===
    rsi_4h = feat.get("rsi_4h")
    if rsi_val is not None and direction is not None:
        if direction == "long" and 55 <= rsi_val <= 75:
            score += 10.0
            reasons.append(f"RSI {rsi_val:.0f} bullish")
        elif direction == "short" and 25 <= rsi_val <= 45:
            score += 10.0
            reasons.append(f"RSI {rsi_val:.0f} bearish")
    # 4h RSI alignment bonus
    if rsi_4h is not None and direction is not None:
        if direction == "long" and 55 <= rsi_4h <= 75:
            score += 5.0
        elif direction == "short" and 25 <= rsi_4h <= 45:
            score += 5.0

    # === Volume confirmation ===
    if vol_ratio is not None:
        if vol_ratio > 3.0:
            score += 20.0
            reasons.append(f"vol spike {vol_ratio:.1f}x")
        elif vol_ratio > 1.5:
            score += 10.0
            reasons.append(f"vol elevated {vol_ratio:.1f}x")

    # === Taker buy/sell directional confirmation (P2) ===
    taker_ratio = feat.get("taker_buy_ratio")
    if taker_ratio is not None and direction is not None:
        if direction == "long" and taker_ratio > 1.2:
            score += 10.0
            reasons.append(f"taker buy {taker_ratio:.2f}")
        elif direction == "short" and taker_ratio < 0.8:
            score += 10.0
            reasons.append(f"taker sell {1/taker_ratio:.2f}")

    # === OI confirmation (new money entering) ===
    if oi_delta is not None and direction is not None:
        if direction == "long" and oi_delta > 5:
            score += 10.0
            reasons.append(f"OI +{oi_delta:.0f}%")
        elif direction == "short" and oi_delta < -5:
            score += 10.0
            reasons.append(f"OI {oi_delta:.0f}%")

    # === Funding conflict (penalty) ===
    if funding is not None and direction is not None:
        if direction == "long" and funding > CROWDED_FUNDING_RATE:
            score -= 15.0
            reasons.append("funding crowded long")
        elif direction == "short" and funding < -CROWDED_FUNDING_RATE:
            score -= 15.0
            reasons.append("funding crowded short")

    # === Overextension guard ===
    if ret_24h is not None and abs(ret_24h) > 10.0:
        score -= 20.0
        reasons.append("24h overextended")

    if direction is None:
        score = 0.0

    return max(0.0, min(100.0, score)), reasons, direction


def score_mean_reversion(feat: dict) -> tuple[float, list[str], str | None]:
    """Score mean-reversion: RSI extreme + volume exhaustion + overextension.

    Returns (score, reasons, direction).
    """
    score = 0.0
    reasons = []
    direction = None

    rsi_val = feat.get("rsi_14")
    rsi_4h = feat.get("rsi_4h")
    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")
    vol_ratio = feat.get("vol_ratio")
    funding = feat.get("funding_rate")

    # === RSI extremes (tightened thresholds) ===
    if rsi_val is not None:
        if rsi_val >= 80:
            score += 35.0
            direction = "short"
            reasons.append(f"RSI {rsi_val:.0f} extreme overbought")
        elif rsi_val >= 75:
            score += 20.0
            direction = "short"
            reasons.append(f"RSI {rsi_val:.0f} overbought")
        elif rsi_val <= 20:
            score += 35.0
            direction = "long"
            reasons.append(f"RSI {rsi_val:.0f} extreme oversold")
        elif rsi_val <= 25:
            score += 20.0
            direction = "long"
            reasons.append(f"RSI {rsi_val:.0f} oversold")

    # === 4h RSI confirmation: higher-timeframe extreme is a stronger signal ===
    # 1h RSI extremes can be single-candle noise; 4h RSI extremes carry more weight.
    if rsi_4h is not None:
        if rsi_4h >= 75:
            evidence_direction = "short"
            if direction is None:
                direction = evidence_direction
            if direction == evidence_direction:
                score += 15.0
                reasons.append(f"4h RSI {rsi_4h:.0f} overbought")
        elif rsi_4h <= 25:
            evidence_direction = "long"
            if direction is None:
                direction = evidence_direction
            if direction == evidence_direction:
                score += 15.0
                reasons.append(f"4h RSI {rsi_4h:.0f} oversold")

    # === Overextended returns ===
    if ret_4h is not None:
        if ret_4h > 8.0:
            evidence_direction = "short"
            if direction is None:
                direction = evidence_direction
            if direction == evidence_direction:
                score += 25.0
                reasons.append(f"4h overextended +{ret_4h:.1f}%")
        elif ret_4h < -8.0:
            evidence_direction = "long"
            if direction is None:
                direction = evidence_direction
            if direction == evidence_direction:
                score += 25.0
                reasons.append(f"4h overextended {ret_4h:.1f}%")

    # === 24h extension ===
    if ret_24h is not None:
        if ret_24h > 12.0:
            evidence_direction = "short"
            if direction is None:
                direction = evidence_direction
            if direction == evidence_direction:
                score += 15.0
                reasons.append(f"24h extended +{ret_24h:.1f}%")
        elif ret_24h < -12.0:
            evidence_direction = "long"
            if direction is None:
                direction = evidence_direction
            if direction == evidence_direction:
                score += 15.0
                reasons.append(f"24h extended {ret_24h:.1f}%")

    # === Volume exhaustion confirmation ===
    if vol_ratio is not None and vol_ratio < 0.7:
        score += 15.0
        reasons.append("volume exhaustion")

    # === Funding confirms crowding (reversion more likely) ===
    if funding is not None and direction is not None:
        if direction == "short" and funding > ELEVATED_FUNDING_RATE:
            score += 15.0
            reasons.append("crowded longs (funding)")
        elif direction == "long" and funding < -ELEVATED_FUNDING_RATE:
            score += 15.0
            reasons.append("crowded shorts (funding)")

    if direction is None:
        score = 0.0

    return max(0.0, min(100.0, score)), reasons, direction


def score_volatility(feat: dict) -> tuple[float, list[str], str | None]:
    """Score volatility breakout: squeeze->expansion + volume + direction.

    Returns (score, reasons, direction).
    """
    score = 0.0
    reasons = []
    direction = None

    atr = feat.get("atr_pct")
    ret_4h = feat.get("return_4h")
    vol_ratio = feat.get("vol_ratio")
    bb_width = feat.get("bb_width_pct")

    # === ATR expansion ===
    if atr is not None:
        if atr > 5.0:
            score += 30.0
            reasons.append(f"ATR {atr:.1f}% breakout")
        elif atr > 3.0:
            score += 20.0
            reasons.append(f"ATR {atr:.1f}% expanding")

    # === BB squeeze (relative compression detection) ===
    bb_squeeze = feat.get("bb_squeeze")
    if bb_squeeze:
        score += 15.0
        reasons.append("BB squeeze")
    elif bb_width is not None and bb_width < 2.0:
        score += 10.0
        reasons.append("BB narrow")

    # === 4h timeframe confirmation (2A+.11: multi-TF, not 1h noise) ===
    # 1h ATR/BB spike on a single candle; the true 4h reading distinguishes a
    # sustained volatility regime from noise. A high 4h ATR confirms the
    # expansion is real; a narrow 4h Bollinger band is a higher-timeframe
    # squeeze (more energy coiled → stronger breakout), so it earns a slightly
    # larger bonus than the 1h squeeze above.
    atr_4h = feat.get("atr_4h")
    bb_width_4h = feat.get("bb_width_4h")
    if atr_4h is not None and atr_4h > 4.0:
        score += 12.0
        reasons.append(f"4h ATR {atr_4h:.1f}% sustained")
    if bb_width_4h is not None and bb_width_4h < 3.0:
        score += 12.0
        reasons.append(f"4h BB squeeze {bb_width_4h:.1f}%")

    # === Direction: from 4h trend ===
    if ret_4h is not None and abs(ret_4h) > 2.0:
        direction = "long" if ret_4h > 0 else "short"

    # === Taker buy/sell confirms breakout direction (P2) ===
    taker_ratio = feat.get("taker_buy_ratio")
    if taker_ratio is not None and direction is not None:
        if (direction == "long" and taker_ratio > 1.2) or \
           (direction == "short" and taker_ratio < 0.8):
            score += 10.0
            reasons.append(f"taker confirms {direction}")

    if direction is None:
        return 0.0, [], None

    # === Volume: contribution for high vol, penalty for low vol applied to
    # full accumulated score. Moved after all signals so the penalty correctly
    # covers ATR + BB + 4h + taker, not just the ATR portion. ===
    if vol_ratio is not None:
        if vol_ratio > 2.0:
            score += 25.0
            reasons.append(f"vol {vol_ratio:.1f}x avg")
        elif vol_ratio > 1.5:
            score += 10.0
        else:
            score *= 0.5
    else:
        score *= 0.7  # unknown volume: moderate penalty

    return max(0.0, min(100.0, score)), reasons, direction


def score_derivatives(feat: dict) -> tuple[float, list[str], str | None]:
    """Score derivatives/funding rate opportunity.

    Returns (score, reasons, direction).
    """
    if str(feat.get("market_type") or "").lower() not in _DERIVATIVES_MARKET_TYPES:
        return 0.0, [], None

    score = 0.0
    reasons = []
    direction = None

    funding = feat.get("funding_rate")
    oi_delta = feat.get("oi_delta_1h")
    ret_4h = feat.get("return_4h")
    ret = feat.get("return_1h")
    if ret is None:
        ret = ret_4h

    # Extreme funding rate
    if funding is not None:
        if abs(funding) > CROWDED_FUNDING_RATE:
            score += 40.0
            direction = "long" if funding < 0 else "short"
            reasons.append(f"funding {funding*100:.3f}% extreme")
        elif abs(funding) > ELEVATED_FUNDING_RATE:
            score += 25.0
            direction = "long" if funding < 0 else "short"
            reasons.append(f"funding {funding*100:.3f}% elevated")
        elif abs(funding) > MILD_FUNDING_RATE:
            score += 10.0
            if funding < 0:
                direction = "long"
            else:
                direction = "short"

    # OI surge. A rising-OI move is itself a derivatives signal and may set the
    # direction when funding hasn't. Previously OI only added score and never
    # set direction, so a pure OI surge with neutral funding was zeroed below
    # (direction stayed None) — that, plus the ~2-min delta that rarely cleared
    # 10%, is why OI never contributed to this family in practice. Direction
    # reads rising OI against price: OI up + price up = new longs; OI up + price
    # down = new shorts. Funding-implied positioning still takes precedence; a
    # falling-OI (deleveraging) move stays direction-ambiguous and only scores.
    if oi_delta is not None:
        oi_direction = None
        if oi_delta > 10 and ret is not None:
            if ret > 0.5:
                oi_direction = "long"
            elif ret < -0.5:
                oi_direction = "short"
        if direction is None and oi_direction:
            direction = oi_direction
        oi_score = 30.0 if abs(oi_delta) > 20 else 20.0 if abs(oi_delta) > 10 else 0.0
        if oi_direction is None or direction == oi_direction:
            score += oi_score
            if oi_score:
                reasons.append(f"OI delta {oi_delta:+.0f}%")
        if oi_direction and direction == oi_direction:
            reasons.append(
                "OI build-up, price up" if oi_direction == "long"
                else "OI build-up, price down"
            )

    # OI/price divergence: hidden accumulation or weak-rally distribution signal.
    # oi_price_divergence = +1: OI rising while price falling → absorption/accumulation
    # (smart money absorbing sells); fades the apparent weakness → long.
    # oi_price_divergence = -1: OI falling while price rising → weak rally on
    # dwindling conviction; likely to reverse → short.
    oi_divergence = feat.get("oi_price_divergence")
    if oi_divergence is not None:
        divergence_direction = (
            "long" if oi_divergence == 1
            else "short" if oi_divergence == -1
            else None
        )
        if oi_divergence == 1:
            if direction is None:
                direction = divergence_direction
            if direction == divergence_direction:
                score += 15.0
                reasons.append("OI↑/price↓ accumulation divergence")
        elif oi_divergence == -1:
            if direction is None:
                direction = divergence_direction
            if direction == divergence_direction:
                score += 15.0
                reasons.append("OI↓/price↑ weak-rally divergence")

    # Fallback when no derivatives data
    if funding is None and oi_delta is None:
        rsi_val = feat.get("rsi_14")
        if rsi_val is not None and rsi_val > 75 and ret_4h and ret_4h > 5:
            score += 20.0
            direction = "short"
            reasons.append("likely crowded long (proxy)")
        elif rsi_val is not None and rsi_val < 25 and ret_4h and ret_4h < -5:
            score += 20.0
            direction = "long"
            reasons.append("likely crowded short (proxy)")

    if direction is None:
        score = 0.0

    return max(0.0, min(100.0, score)), reasons, direction


def score_event(feat: dict) -> tuple[float, list[str], str | None]:
    """Score event/catalyst opportunity: sharp price jump + volume spike + taker confirmation.

    Detects short-horizon shock events (liquidation cascades, news catalysts, large-order
    impact) using available feat fields. A qualifying event requires both price displacement
    AND volume confirmation; a pure vol spike without displacement is not an event signal.

    Returns (score, reasons, direction).
    """
    score = 0.0
    reasons = []
    direction = None

    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")
    vol_ratio = feat.get("vol_ratio")
    taker_ratio = feat.get("taker_buy_ratio")
    oi_delta = feat.get("oi_delta_1h")
    rsi_val = feat.get("rsi_14")

    # === Price jump (core event signal) ===
    # A large 1h return is the primary indicator of a catalyst event.
    if ret_1h is not None:
        if ret_1h > 8.0:
            score += 45.0
            direction = "long"
            reasons.append(f"sharp +{ret_1h:.1f}% 1h jump")
        elif ret_1h < -8.0:
            score += 45.0
            direction = "short"
            reasons.append(f"sharp {ret_1h:.1f}% 1h dump")
        elif ret_1h > 4.0:
            score += 25.0
            direction = "long"
            reasons.append(f"+{ret_1h:.1f}% 1h move")
        elif ret_1h < -4.0:
            score += 25.0
            direction = "short"
            reasons.append(f"{ret_1h:.1f}% 1h move")

    # No directional displacement → not an event
    if direction is None:
        return 0.0, [], None

    # === Taker directional confirmation ===
    if taker_ratio is not None:
        if direction == "long" and taker_ratio > 1.5:
            score += 15.0
            reasons.append(f"taker buy dominated ({taker_ratio:.2f})")
        elif direction == "short" and taker_ratio < 0.65:
            score += 15.0
            reasons.append(f"taker sell dominated ({taker_ratio:.2f})")

    # === OI surge co-signals event impact (not direction-reversing) ===
    if oi_delta is not None and abs(oi_delta) > 15:
        score += 10.0
        reasons.append(f"OI delta {oi_delta:+.0f}%")

    # === RSI extension cap: extreme RSI reduces late-entry score ===
    # An already-extended RSI at event time means worse risk/reward for chasing.
    if rsi_val is not None:
        if direction == "long" and rsi_val > 80:
            score *= 0.7
            reasons.append(f"rsi {rsi_val:.0f} extended (late)")
        elif direction == "short" and rsi_val < 20:
            score *= 0.7
            reasons.append(f"rsi {rsi_val:.0f} extended (late)")

    # === 4h context: event in trend direction is stronger ===
    if ret_4h is not None:
        if direction == "long" and ret_4h > 3.0:
            score += 5.0
            reasons.append("4h trend aligned")
        elif direction == "short" and ret_4h < -3.0:
            score += 5.0
            reasons.append("4h trend aligned")

    # === Volume confirmation applied to full accumulated score ===
    # Moved after all confirmations so the low-volume penalty applies to the
    # entire event thesis (price jump + taker + OI + 4h), not just the price
    # jump portion. Without volume the whole event signal is weaker.
    if vol_ratio is not None:
        if vol_ratio >= 4.0:
            score += 30.0
            reasons.append(f"vol x{vol_ratio:.1f} vs 72h avg")
        elif vol_ratio >= 2.0:
            score += 20.0
            reasons.append(f"vol x{vol_ratio:.1f} vs 72h avg")
        elif vol_ratio >= 1.5:
            score += 10.0
            reasons.append(f"vol x{vol_ratio:.1f} slightly elevated")
        else:
            score *= 0.5
    else:
        score *= 0.7  # unknown volume: moderate penalty

    return max(0.0, min(100.0, score)), reasons, direction


def score_short_bias(feat: dict) -> tuple[float, list[str], str | None]:
    """Score dedicated bearish setups for bear/volatile regimes (Phase 2A+.8).

    Fires ONLY "short". Looks for confluence of sustained downward momentum
    (4h + 24h aligned), non-crowded short positioning (funding not deeply
    negative = shorts not already piled in), and RSI with room to fall (not
    already oversold). Complements the generic momentum/derivatives families
    with a short-specific edge; the regime gate in signal_emitter filters it
    out in bull markets, and short_enabled=False kills it globally.

    Returns (score, reasons, direction). direction is "short" or None.
    """
    score = 0.0
    reasons: list[str] = []

    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")
    rsi_val = feat.get("rsi_14")
    rsi_4h = feat.get("rsi_4h")
    funding = feat.get("funding_rate")
    oi_delta = feat.get("oi_delta_1h")
    rs_24h = feat.get("rs_beta_adj_24h") or feat.get("relative_strength_24h")

    # === Core gate: meaningful downward momentum on 4h (not noise) ===
    if ret_4h is None or ret_4h >= -1.0:
        return 0.0, [], None
    if ret_4h <= -5.0:
        score += 30.0
        reasons.append(f"4h {ret_4h:.1f}% sharp decline")
    elif ret_4h <= -2.5:
        score += 22.0
        reasons.append(f"4h {ret_4h:.1f}% decline")
    else:
        score += 14.0
        reasons.append(f"4h {ret_4h:.1f}% soft decline")

    # === 24h trend confirmation (required) — don't fade a dip in an uptrend ===
    if ret_24h is not None and ret_24h >= 0:
        return 0.0, [], None
    if ret_24h is not None and ret_24h < -3.0:
        score += 15.0
        reasons.append(f"24h {ret_24h:.1f}% aligned")

    # === Relative weakness vs BTC strengthens the short ===
    if rs_24h is not None and rs_24h < -3.0:
        score += 15.0
        reasons.append(f"RS24h {rs_24h:.1f}% weak vs BTC")

    # === Funding gate: avoid crowded shorts (squeeze risk) ===
    # Negative funding means shorts pay longs, so deeply negative funding is the
    # crowded-short/squeeze-risk state. Positive funding means longs are paying
    # and can support an already-valid short thesis; it never creates one alone.
    if funding is not None and funding < -CROWDED_FUNDING_RATE:
        return 0.0, [], None
    if funding is not None and funding > ELEVATED_FUNDING_RATE:
        score += 10.0
        reasons.append(f"funding {funding:.3f} (longs paying)")

    # === RSI 1h room-to-fall gate: reject if already oversold (poor entry) ===
    if rsi_val is not None and rsi_val < 25:
        return 0.0, [], None
    if rsi_val is not None and rsi_val < 45:
        score += 8.0
        reasons.append(f"rsi {rsi_val:.0f} room to fall")

    # === 4h RSI: higher-timeframe confirmation of room to fall ===
    if rsi_4h is not None:
        if rsi_4h < 25:
            # Already oversold on 4h — risk of snap-back is too high
            return 0.0, [], None
        elif rsi_4h < 50:
            score += 10.0
            reasons.append(f"4h rsi {rsi_4h:.0f} room to fall")

    # === OI flow: confirms the short thesis ===
    # Declining OI + price decline = long liquidation cascade (bearish confirmation)
    # Rising OI + price decline = new shorts entering = directional conviction
    if oi_delta is not None:
        if oi_delta < -10:
            score += 12.0
            reasons.append(f"OI liquidation {oi_delta:.0f}%")
        elif oi_delta > 10:
            score += 10.0
            reasons.append(f"new shorts OI +{oi_delta:.0f}%")

    return max(0.0, min(100.0, score)), reasons, "short"


def run_all_scanners(feat: dict) -> dict:
    """Run all scanner families. Returns best result with direction.

    Scanners return (score, reasons, direction). Direction is intrinsic to scoring.
    Prefers scanners that produce a direction; falls back to highest score if none do.
    """
    results = {
        "momentum": score_momentum(feat),
        "mean_reversion": score_mean_reversion(feat),
        "volatility": score_volatility(feat),
        "derivatives": score_derivatives(feat),
        "event": score_event(feat),
        "short_bias": score_short_bias(feat),
    }

    # Prefer scanners with direction
    valid = {k: v for k, v in results.items() if v[2] is not None}

    if valid:
        best_family = max(valid, key=lambda k: valid[k][0])
        best_score, best_reasons, best_dir = valid[best_family]
    else:
        best_family = max(results, key=lambda k: results[k][0])
        best_score, best_reasons, best_dir = results[best_family]

    # Preserve the scanner's proposed side for observation-only outcome identity.
    # The no-chase gate below still clears scanner_direction, so this cannot make
    # the candidate actionable or restore allocation authority.
    proposed_direction = best_dir

    # VergeX-derived entry discipline: a candidate is not actionable when the
    # current 15m candle already travelled more than 1.5 ATR in the proposed
    # direction. The underlying family score remains in all_scores for shadow
    # analysis, while the emitted candidate is directionless and cannot be chased.
    range_atr = feat.get("last_candle_range_atr_15m")
    ret_15m = feat.get("return_15m")
    chase_aligned = (
        best_dir == "long" and ret_15m is not None and ret_15m > 0
    ) or (
        best_dir == "short" and ret_15m is not None and ret_15m < 0
    )
    if (
        best_dir is not None
        and range_atr is not None
        and float(range_atr) > 1.5
        and chase_aligned
    ):
        best_score = 0.0
        best_reasons = [*best_reasons, "15m candle >1.5 ATR; no chase"]
        best_dir = None

    # Derivatives proxy: fired when funding_rate and oi_delta are both None —
    # RSI fallback produces a weaker signal that shouldn't trigger high-conf paths.
    is_proxy = (
        best_family == "derivatives"
        and feat.get("funding_rate") is None
        and feat.get("oi_delta_1h") is None
    )

    return {
        "scanner_family": best_family,
        "opportunity_score": best_score,
        "reasons": best_reasons,
        "scanner_direction": best_dir,
        "proposed_direction": proposed_direction,
        "all_scores": {k: v[0] for k, v in results.items()},
        "is_proxy": is_proxy,
    }
