"""Normalizer — compute composite scores from raw collector data."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aletheia.data.models import LeverageState, TradabilityScore

logger = logging.getLogger("aletheia.data.normalizer")


def compute_tradability(
    symbol: str,
    exchange: str,
    spread_bps: float,
    depth_10bps_usd: float,
    volume_24h_usd: float,
) -> TradabilityScore:
    """Compute a 0-100 tradability score from microstructure + volume data."""
    # Scoring: lower spread is better, higher depth is better, higher volume is better.
    # Each factor contributes a sub-score (0-33), summed to 0-100.
    spread_score = max(0, 33 - spread_bps * 2)  # 0 bps → 33; >16.5 bps → 0
    depth_score = min(33, depth_10bps_usd / 10_000)  # $330k → full 33
    vol_score = min(34, volume_24h_usd / 5_000_000)  # $170M → full 34

    score = round(spread_score + depth_score + vol_score, 1)
    score = max(0, min(100, score))

    # Execution cost estimate: spread / 2 + impact (simplified)
    impact_bps = 1.0 if depth_10bps_usd > 50_000 else 3.0
    exec_cost = spread_bps / 2 + impact_bps

    return TradabilityScore(
        symbol=symbol,
        exchange=exchange,
        spread_bps=spread_bps,
        depth_10bps_usd=depth_10bps_usd,
        volume_24h_usd=volume_24h_usd,
        score=score,
        execution_cost_estimate_bps=round(exec_cost, 2),
        is_tradable=score >= 30 and spread_bps < 20,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def compute_leverage_state(
    symbol: str,
    funding_rate: float | None = None,
    funding_annualized: float | None = None,
    open_interest_usd: float | None = None,
    oi_delta_1h_pct: float | None = None,
    long_short_ratio: float | None = None,
    liquidation_1h_usd: float | None = None,
) -> LeverageState:
    """Compute crowding score and funding opportunity flag from derivatives data."""
    crowding = 0.0
    factors = 0

    # High absolute funding → crowded
    if funding_rate is not None:
        fr_abs = abs(funding_rate)
        if fr_abs > 0.001:  # >0.1% per 8h
            crowding += min(30, fr_abs * 30_000)
        factors += 1

    # High OI relative to typical
    if oi_delta_1h_pct is not None and abs(oi_delta_1h_pct) > 5:
        crowding += min(25, abs(oi_delta_1h_pct) * 2)
        factors += 1

    # Skewed long/short ratio
    if long_short_ratio is not None:
        skew = abs(long_short_ratio - 1.0)
        if skew > 0.3:
            crowding += min(25, skew * 30)
        factors += 1

    # Heavy liquidations
    if liquidation_1h_usd is not None and liquidation_1h_usd > 100_000:
        crowding += min(20, liquidation_1h_usd / 500_000 * 20)
        factors += 1

    crowding = round(min(100, crowding), 1)

    # Funding opportunity: extreme funding + direction suggests mean-reversion
    funding_opp = False
    if funding_rate is not None and abs(funding_rate) > 0.0005:
        funding_opp = True

    return LeverageState(
        symbol=symbol,
        funding_rate=funding_rate,
        funding_annualized=funding_annualized,
        open_interest_usd=open_interest_usd,
        oi_delta_1h_pct=oi_delta_1h_pct,
        long_short_ratio=long_short_ratio,
        liquidation_1h_usd=liquidation_1h_usd,
        crowding_score=crowding,
        funding_opportunity=funding_opp,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
