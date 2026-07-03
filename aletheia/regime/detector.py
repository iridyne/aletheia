"""Market regime detection — deterministic, no LLM.

Consumes:
  - BTC 1d OHLCV (for MA200 + 30d momentum)
  - MacroSnapshot (VIX)
  - Market-wide average funding rate (from universe features)

Produces:
  MarketRegime dataclass → written to DB by caller, consumed by signal_emitter,
  paper_trader_ranker, and risk_engine.

Detection runs every universe_refresh cycle (~10 min). State changes are
debounced: a new state must hold for `min_hold_periods` consecutive detections
before the published regime switches (prevents flicker during transitions).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("aletheia.regime")

REGIME_BULL = "bull"
REGIME_BEAR = "bear"
REGIME_SIDEWAYS = "sideways"
REGIME_VOLATILE = "volatile"

_VALID_REGIMES = {REGIME_BULL, REGIME_BEAR, REGIME_SIDEWAYS, REGIME_VOLATILE}

# Bias presets per regime state
_BIAS_TABLE: dict[str, tuple[float, float]] = {
    REGIME_BULL: (0.75, 0.25),
    REGIME_BEAR: (0.25, 0.75),
    REGIME_SIDEWAYS: (0.50, 0.50),
    REGIME_VOLATILE: (0.35, 0.35),
}


@dataclass
class MarketRegime:
    state: str = REGIME_SIDEWAYS
    confidence: float = 0.5
    long_bias: float = 0.50
    short_bias: float = 0.50
    signals: dict = field(default_factory=dict)
    detected_at: str = ""

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()


@dataclass
class _RegimeVote:
    bull: float = 0.0
    bear: float = 0.0
    sideways: float = 0.0
    volatile: float = 0.0

    def winner(self) -> tuple[str, float]:
        scores = {
            REGIME_BULL: self.bull,
            REGIME_BEAR: self.bear,
            REGIME_SIDEWAYS: self.sideways,
            REGIME_VOLATILE: self.volatile,
        }
        best = max(scores, key=lambda k: scores[k])
        total = sum(scores.values()) or 1.0
        confidence = scores[best] / total
        return best, round(confidence, 3)


def detect_regime(
    *,
    btc_closes_1d: list[float] | None = None,
    vix: float | None = None,
    avg_funding_rate: float | None = None,
    total_market_cap_change_7d: float | None = None,
    atr_7d: float | None = None,
    atr_30d: float | None = None,
) -> MarketRegime:
    """Detect current market regime from available signals.

    All inputs are optional — the detector works with whatever is available,
    producing lower confidence when data is sparse.

    Args:
        btc_closes_1d: BTC/USDT daily closes, oldest→newest. Need ≥200 for MA200.
        vix: Current VIX close value.
        avg_funding_rate: Average funding rate across observed perpetual contracts.
        total_market_cap_change_7d: Total crypto market cap 7d change (%).
        atr_7d: BTC 7-day ATR as % of price.
        atr_30d: BTC 30-day ATR as % of price.

    Returns:
        MarketRegime with state, confidence, and directional bias.
    """
    vote = _RegimeVote()
    signals: dict = {}

    # Signal 1: BTC price vs MA200
    if btc_closes_1d and len(btc_closes_1d) >= 200:
        ma200 = sum(btc_closes_1d[-200:]) / 200
        current = btc_closes_1d[-1]
        pct_above_ma200 = (current - ma200) / ma200 * 100
        signals["btc_vs_ma200_pct"] = round(pct_above_ma200, 2)

        if pct_above_ma200 > 10:
            vote.bull += 2.0
        elif pct_above_ma200 > 0:
            vote.bull += 1.0
        elif pct_above_ma200 < -10:
            vote.bear += 2.0
        elif pct_above_ma200 < 0:
            vote.bear += 1.0

    # Signal 2: BTC 30-day momentum
    if btc_closes_1d and len(btc_closes_1d) >= 30:
        price_30d_ago = btc_closes_1d[-30]
        current = btc_closes_1d[-1]
        if price_30d_ago > 0:
            momentum_30d = (current - price_30d_ago) / price_30d_ago * 100
            signals["btc_momentum_30d_pct"] = round(momentum_30d, 2)

            if momentum_30d > 15:
                vote.bull += 1.5
            elif momentum_30d > 5:
                vote.bull += 1.0
            elif momentum_30d < -15:
                vote.bear += 1.5
            elif momentum_30d < -5:
                vote.bear += 1.0
            else:
                vote.sideways += 1.5

    # Signal 3: VIX level
    if vix is not None:
        signals["vix"] = vix
        if vix > 30:
            vote.volatile += 1.5
            vote.bear += 1.0
        elif vix > 25:
            vote.volatile += 1.0
            vote.bear += 0.5
        elif vix < 15:
            vote.bull += 1.0
        else:
            vote.sideways += 0.5

    # Signal 4: Average funding rate (crowding signal)
    if avg_funding_rate is not None:
        signals["avg_funding_rate"] = avg_funding_rate
        if avg_funding_rate > 0.0008:
            # Extremely positive funding → longs crowded → bearish contrarian
            vote.bear += 1.0
        elif avg_funding_rate > 0.0005:
            vote.bear += 0.5
        elif avg_funding_rate < -0.0003:
            # Negative funding → shorts crowded → bullish contrarian
            vote.bull += 0.5
            vote.volatile += 0.5

    # Signal 5: Total crypto market cap 7d change
    if total_market_cap_change_7d is not None:
        signals["market_cap_change_7d_pct"] = total_market_cap_change_7d
        if total_market_cap_change_7d > 8:
            vote.bull += 1.0
        elif total_market_cap_change_7d > 3:
            vote.bull += 0.5
        elif total_market_cap_change_7d < -8:
            vote.bear += 1.0
        elif total_market_cap_change_7d < -3:
            vote.bear += 0.5
        else:
            vote.sideways += 0.5

    # Signal 6: ATR expansion (volatility regime)
    if atr_7d is not None and atr_30d is not None and atr_30d > 0:
        atr_ratio = atr_7d / atr_30d
        signals["atr_expansion_ratio"] = round(atr_ratio, 2)
        if atr_ratio > 2.0:
            vote.volatile += 2.0
        elif atr_ratio > 1.5:
            vote.volatile += 1.0
        elif atr_ratio < 0.6:
            vote.sideways += 1.0

    # Determine winner
    state, confidence = vote.winner()

    # If no meaningful signals, default sideways with low confidence
    if not signals:
        state = REGIME_SIDEWAYS
        confidence = 0.3

    long_bias, short_bias = _BIAS_TABLE[state]

    return MarketRegime(
        state=state,
        confidence=confidence,
        long_bias=long_bias,
        short_bias=short_bias,
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Debounce logic (called by the scheduler integration layer)
# ---------------------------------------------------------------------------

_consecutive_state: list[str] = []
_published_regime: MarketRegime | None = None


def debounced_detect(
    min_hold_periods: int = 3,
    **detect_kwargs,
) -> MarketRegime:
    """Detect regime with debounce — state must hold N consecutive times to switch.

    Returns the currently published (debounced) regime, which may lag the raw
    detection by up to `min_hold_periods` cycles during transitions.
    """
    global _published_regime

    raw = detect_regime(**detect_kwargs)

    _consecutive_state.append(raw.state)
    # Keep only the last N+1 entries
    while len(_consecutive_state) > min_hold_periods + 1:
        _consecutive_state.pop(0)

    if _published_regime is None:
        _published_regime = raw
        return _published_regime

    # Check if last N detections are all the same new state
    if len(_consecutive_state) >= min_hold_periods:
        recent = _consecutive_state[-min_hold_periods:]
        if all(s == recent[0] for s in recent):
            if recent[0] != _published_regime.state:
                logger.info(
                    "Regime transition: %s → %s (held %d periods)",
                    _published_regime.state, recent[0], min_hold_periods,
                )
                _published_regime = raw

    # Update confidence/signals even if state didn't change
    if _published_regime.state == raw.state:
        _published_regime = raw

    return _published_regime


def get_current_regime() -> MarketRegime:
    """Return the last published regime, recovering from DB if process restarted."""
    global _published_regime
    if _published_regime is not None:
        return _published_regime
    try:
        from aletheia.storage.repositories import get_latest_market_regime
        row = get_latest_market_regime()
        if row:
            # A11 (LEARNING_LOOP_AUDIT): on restart-recovery also restore long/short
            # bias — the old code left them at the 0.50/0.50 default, so a recovered
            # bear/bull regime fed NEUTRAL bias to consumers (ranker / risk / PI) until
            # the next detection cycle. Derive from _BIAS_TABLE so recovered bias is
            # consistent with the recovered state.
            state = row.get("state", REGIME_SIDEWAYS)
            lb, sb = _BIAS_TABLE.get(state, (0.50, 0.50))
            _published_regime = MarketRegime(
                state=state,
                confidence=float(row.get("confidence", 0.5)),
                long_bias=lb,
                short_bias=sb,
            )
            return _published_regime
    except Exception:
        pass
    return MarketRegime()


def reset_state() -> None:
    """Reset module-level state (for testing)."""
    global _published_regime
    _consecutive_state.clear()
    _published_regime = None
