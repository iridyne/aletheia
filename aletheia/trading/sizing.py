"""Conviction-based position sizing.

Fractional Kelly formula adapted for conviction scores:
  size = base_notional * kelly_fraction * conviction_ratio * drawdown_scale

Used by PI agent (evaluate_trading_opportunity) and can be called from
paper_trader for RuleSet-driven entries. Does NOT touch risk engine limits
— all proposals still pass through Risk Engine gates after sizing.
"""
from __future__ import annotations


_DEFAULT_KELLY_FRACTION = 0.25   # quarter-Kelly: conservative, reduces variance
_MIN_MULTIPLIER = 0.3            # floor: $25 base → $7.50 minimum at lowest conviction
_MAX_MULTIPLIER = 2.5            # cap: $25 base → $62.50 maximum at peak conviction
_CONVICTION_MIDPOINT = 70.0      # conviction at which multiplier = 1.0 (base notional)


def compute_position_size(
    conviction: int,
    base_notional: float,
    *,
    drawdown_pct: float = 0.0,
    kelly_fraction: float = _DEFAULT_KELLY_FRACTION,
    min_multiplier: float = _MIN_MULTIPLIER,
    max_multiplier: float = _MAX_MULTIPLIER,
) -> float:
    """Compute notional position size from conviction score.

    Args:
        conviction: 0-100 int representing trade confidence
        base_notional: base USDT amount (e.g. 25.0 from config)
        drawdown_pct: current portfolio drawdown % (0-100); scales size down
        kelly_fraction: fraction of full Kelly to use (default 0.25 = quarter-Kelly)
        min_multiplier: floor on the size multiplier
        max_multiplier: cap on the size multiplier

    Returns:
        Target notional size in USDT, rounded to 2 decimal places
    """
    if base_notional <= 0:
        return 0.0
    conviction_clamped = max(0, min(100, conviction))

    # Linear scaling: 0% conviction → min_multiplier, midpoint → 1.0, 100% → max_multiplier
    if conviction_clamped <= _CONVICTION_MIDPOINT:
        t = conviction_clamped / _CONVICTION_MIDPOINT
        multiplier = min_multiplier + t * (1.0 - min_multiplier)
    else:
        t = (conviction_clamped - _CONVICTION_MIDPOINT) / (100.0 - _CONVICTION_MIDPOINT)
        multiplier = 1.0 + t * (max_multiplier - 1.0)

    # Apply Kelly fraction
    multiplier *= kelly_fraction * 4  # 4× so that at kelly_fraction=0.25 the midpoint stays at 1.0

    # Drawdown scale: linear from 1.0 at 0% DD to 0.5 at 10%+ DD
    if drawdown_pct > 0:
        dd_scale = max(0.5, 1.0 - (drawdown_pct / 20.0))
        multiplier *= dd_scale

    # Clamp to configured bounds
    multiplier = max(min_multiplier, min(max_multiplier, multiplier))

    return round(base_notional * multiplier, 2)


def conviction_to_multiplier(conviction: int) -> float:
    """Quick multiplier lookup without Kelly/drawdown adjustments. Used for display."""
    result = compute_position_size(conviction, 1.0)
    return round(result, 3)
