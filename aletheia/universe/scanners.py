"""Scanner Families — score assets by opportunity type.

Each scanner takes a feature dict and returns (score: 0-100, reasons: list[str]).
run_all_scanners picks the best-scoring family for each asset.
"""

from __future__ import annotations


def score_momentum(feat: dict) -> tuple[float, list[str]]:
    """Score momentum/breakout opportunity based on returns + volume."""
    score = 0.0
    reasons = []

    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")
    atr = feat.get("atr_pct")
    rsi_val = feat.get("rsi_14")

    # Strong 1h return
    if ret_1h is not None:
        if abs(ret_1h) > 3.0:
            score += 30.0
            reasons.append(f"1h return {ret_1h:+.1f}%")
        elif abs(ret_1h) > 1.5:
            score += 20.0
            reasons.append(f"1h return {ret_1h:+.1f}%")
        elif abs(ret_1h) > 0.5:
            score += 10.0

    # 4h momentum
    if ret_4h is not None:
        if abs(ret_4h) > 5.0:
            score += 25.0
            reasons.append(f"4h return {ret_4h:+.1f}%")
        elif abs(ret_4h) > 2.5:
            score += 15.0

    # RSI confirms direction (not extreme enough for MR but directional)
    if rsi_val is not None:
        if 55 <= rsi_val <= 75:
            score += 15.0
            reasons.append(f"RSI {rsi_val:.0f} bullish")
        elif 25 <= rsi_val <= 45:
            score += 15.0
            reasons.append(f"RSI {rsi_val:.0f} bearish")

    # ATR expansion signals breakout
    if atr is not None and atr > 3.0:
        score += 15.0
        reasons.append(f"ATR {atr:.1f}% expanding")

    # 24h trend alignment
    if ret_24h is not None and ret_1h is not None:
        if (ret_24h > 0 and ret_1h > 0) or (ret_24h < 0 and ret_1h < 0):
            score += 10.0

    return min(100.0, score), reasons


def score_mean_reversion(feat: dict) -> tuple[float, list[str]]:
    """Score mean-reversion opportunity based on RSI extremes + overextension."""
    score = 0.0
    reasons = []

    rsi_val = feat.get("rsi_14")
    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")

    # RSI extremes
    if rsi_val is not None:
        if rsi_val >= 80:
            score += 35.0
            reasons.append(f"RSI {rsi_val:.0f} overbought")
        elif rsi_val >= 70:
            score += 25.0
            reasons.append(f"RSI {rsi_val:.0f} overbought zone")
        elif rsi_val <= 20:
            score += 35.0
            reasons.append(f"RSI {rsi_val:.0f} oversold")
        elif rsi_val <= 30:
            score += 25.0
            reasons.append(f"RSI {rsi_val:.0f} oversold zone")

    # Overextended returns (large move likely to revert)
    if ret_4h is not None:
        if abs(ret_4h) > 8.0:
            score += 30.0
            reasons.append(f"4h overextended {ret_4h:+.1f}%")
        elif abs(ret_4h) > 5.0:
            score += 20.0
            reasons.append(f"4h extended {ret_4h:+.1f}%")

    # 24h extension
    if ret_24h is not None and abs(ret_24h) > 12.0:
        score += 20.0
        reasons.append(f"24h extended {ret_24h:+.1f}%")

    # Divergence: 1h reversing while 4h still extended
    if ret_1h is not None and ret_4h is not None:
        if ret_4h > 5.0 and ret_1h < -0.5:
            score += 15.0
            reasons.append("1h reversal starting")
        elif ret_4h < -5.0 and ret_1h > 0.5:
            score += 15.0
            reasons.append("1h reversal starting")

    return min(100.0, score), reasons


def score_volatility(feat: dict) -> tuple[float, list[str]]:
    """Score volatility expansion opportunity."""
    score = 0.0
    reasons = []

    atr = feat.get("atr_pct")
    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")

    # High ATR = volatility expansion
    if atr is not None:
        if atr > 5.0:
            score += 40.0
            reasons.append(f"ATR {atr:.1f}% high vol")
        elif atr > 3.0:
            score += 30.0
            reasons.append(f"ATR {atr:.1f}% expanding")
        elif atr > 2.0:
            score += 15.0

    # Large 1h range
    if ret_1h is not None and abs(ret_1h) > 2.0:
        score += 25.0
        reasons.append(f"1h range {abs(ret_1h):.1f}%")

    # 4h range expansion
    if ret_4h is not None and abs(ret_4h) > 4.0:
        score += 20.0
        reasons.append(f"4h range {abs(ret_4h):.1f}%")

    # Volume spike implies breakout volatility
    vol = feat.get("volume_1h")
    vol_24h = feat.get("quote_volume_24h")
    if vol and vol_24h and vol_24h > 0:
        hourly_avg = vol_24h / 24
        if hourly_avg > 0 and vol > hourly_avg * 3:
            score += 15.0
            reasons.append("volume spike 3x avg")

    return min(100.0, score), reasons


def score_derivatives(feat: dict) -> tuple[float, list[str]]:
    """Score derivatives/funding rate opportunity."""
    score = 0.0
    reasons = []

    funding = feat.get("funding_rate")
    oi = feat.get("open_interest")
    oi_delta = feat.get("oi_delta_1h")

    # Extreme funding rate
    if funding is not None:
        if abs(funding) > 0.01:
            score += 40.0
            reasons.append(f"funding {funding*100:.3f}% extreme")
        elif abs(funding) > 0.005:
            score += 25.0
            reasons.append(f"funding {funding*100:.3f}% elevated")
        elif abs(funding) > 0.002:
            score += 10.0

    # OI surge
    if oi_delta is not None:
        if abs(oi_delta) > 20:
            score += 30.0
            reasons.append(f"OI delta {oi_delta:+.0f}%")
        elif abs(oi_delta) > 10:
            score += 20.0
            reasons.append(f"OI delta {oi_delta:+.0f}%")

    # If no derivatives data available, this scanner scores low (intentional)
    if funding is None and oi_delta is None:
        # Use return/RSI as proxy for crowding
        rsi_val = feat.get("rsi_14")
        ret_4h = feat.get("return_4h")
        if rsi_val is not None and rsi_val > 75 and ret_4h and ret_4h > 5:
            score += 20.0
            reasons.append("likely crowded long (proxy)")
        elif rsi_val is not None and rsi_val < 25 and ret_4h and ret_4h < -5:
            score += 20.0
            reasons.append("likely crowded short (proxy)")

    return min(100.0, score), reasons


def run_all_scanners(feat: dict) -> dict:
    """Run all scanner families and return best result.

    Returns dict with: scanner_family, opportunity_score, reasons.
    """
    results = {
        "momentum": score_momentum(feat),
        "mean_reversion": score_mean_reversion(feat),
        "volatility": score_volatility(feat),
        "derivatives": score_derivatives(feat),
    }

    best_family = max(results, key=lambda k: results[k][0])
    best_score, best_reasons = results[best_family]

    return {
        "scanner_family": best_family,
        "opportunity_score": best_score,
        "reasons": best_reasons,
        "all_scores": {k: v[0] for k, v in results.items()},
    }
