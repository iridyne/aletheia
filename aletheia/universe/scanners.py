"""Scanner Families — score assets by opportunity type.

Each scanner takes a feature dict and returns (score: 0-100, reasons: list[str], direction: str|None).
Direction is intrinsic to the scoring logic — not inferred post-hoc.
run_all_scanners picks the best-scoring family for each asset.
"""

from __future__ import annotations


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
        if direction == "long" and funding > 0.01:
            score -= 15.0
            reasons.append("funding crowded long")
        elif direction == "short" and funding < -0.01:
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

    # === Overextended returns ===
    if ret_4h is not None:
        if ret_4h > 8.0:
            score += 25.0
            if direction is None:
                direction = "short"
            reasons.append(f"4h overextended +{ret_4h:.1f}%")
        elif ret_4h < -8.0:
            score += 25.0
            if direction is None:
                direction = "long"
            reasons.append(f"4h overextended {ret_4h:.1f}%")

    # === 24h extension ===
    if ret_24h is not None:
        if ret_24h > 12.0:
            score += 15.0
            if direction is None:
                direction = "short"
            reasons.append(f"24h extended +{ret_24h:.1f}%")
        elif ret_24h < -12.0:
            score += 15.0
            if direction is None:
                direction = "long"
            reasons.append(f"24h extended {ret_24h:.1f}%")

    # === Volume exhaustion confirmation ===
    if vol_ratio is not None and vol_ratio < 0.7:
        score += 15.0
        reasons.append("volume exhaustion")

    # === Funding confirms crowding (reversion more likely) ===
    if funding is not None and direction is not None:
        if direction == "short" and funding > 0.005:
            score += 15.0
            reasons.append("crowded longs (funding)")
        elif direction == "long" and funding < -0.005:
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

    # === Volume spike (necessary condition) ===
    if vol_ratio is not None and vol_ratio > 2.0:
        score += 25.0
        reasons.append(f"vol {vol_ratio:.1f}x avg")
    elif vol_ratio is not None and vol_ratio > 1.5:
        score += 10.0
    else:
        score = score * 0.5

    # === BB squeeze (relative compression detection) ===
    bb_squeeze = feat.get("bb_squeeze")
    if bb_squeeze:
        score += 15.0
        reasons.append("BB squeeze")
    elif bb_width is not None and bb_width < 2.0:
        score += 10.0
        reasons.append("BB narrow")

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
        score = 0.0

    return max(0.0, min(100.0, score)), reasons, direction


def score_derivatives(feat: dict) -> tuple[float, list[str], str | None]:
    """Score derivatives/funding rate opportunity.

    Returns (score, reasons, direction).
    """
    score = 0.0
    reasons = []
    direction = None

    funding = feat.get("funding_rate")
    oi_delta = feat.get("oi_delta_1h")
    ret_4h = feat.get("return_4h")

    # Extreme funding rate
    if funding is not None:
        if abs(funding) > 0.01:
            score += 40.0
            direction = "long" if funding < 0 else "short"
            reasons.append(f"funding {funding*100:.3f}% extreme")
        elif abs(funding) > 0.005:
            score += 25.0
            direction = "long" if funding < 0 else "short"
            reasons.append(f"funding {funding*100:.3f}% elevated")
        elif abs(funding) > 0.002:
            score += 10.0
            if funding < 0:
                direction = "long"
            else:
                direction = "short"

    # OI surge
    if oi_delta is not None:
        if abs(oi_delta) > 20:
            score += 30.0
            reasons.append(f"OI delta {oi_delta:+.0f}%")
        elif abs(oi_delta) > 10:
            score += 20.0
            reasons.append(f"OI delta {oi_delta:+.0f}%")

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
    """Score event/catalyst opportunity — placeholder.

    Returns (score, reasons, direction).
    """
    return 0.0, [], None


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
    }

    # Prefer scanners with direction
    valid = {k: v for k, v in results.items() if v[2] is not None}

    if valid:
        best_family = max(valid, key=lambda k: valid[k][0])
        best_score, best_reasons, best_dir = valid[best_family]
    else:
        best_family = max(results, key=lambda k: results[k][0])
        best_score, best_reasons, best_dir = results[best_family]

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
        "all_scores": {k: v[0] for k, v in results.items()},
        "is_proxy": is_proxy,
    }
