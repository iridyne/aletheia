"""Liquidation-price helper for exchange position materialization."""

from __future__ import annotations


def liquidation_price_for(
    entry_price: float | None,
    direction: str,
    leverage: int | None,
    symbol: str,
) -> float | None:
    """Estimated isolated-margin liquidation price for a futures open, or None.

    Returns None for spot / 1x (no liquidation) and on any computation failure —
    a missing estimate must never break order recording.
    """
    if not leverage or leverage <= 1 or not entry_price:
        return None
    try:
        from aletheia.trading.instruments import calc_liquidation_price
        return calc_liquidation_price(float(entry_price), direction, int(leverage), symbol)
    except Exception:
        return None
