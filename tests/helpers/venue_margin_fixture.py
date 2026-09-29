"""Venue margin facts for entry-path tests (#3729).

The canonical sizing path reads a persisted `VenueMarginEnvelope` instead of a
static maintenance-margin default, so tests that compile an Entry Plan must seed
that fact row. This helper is the one place that does it.

The default tier mirrors the live bitget `fetch_market_leverage_tiers` payload
for `BTC/USDT:USDT` (public endpoint, probed 2026-09-29): tier 1 covers
0..200_000 USDT notional at 150x with a 0.4% maintenance-margin rate. Tests that
need a different envelope — an 18x-safe-cap case, a tier boundary, a missing
instrument — pass their own `tiers`.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from aletheia.execution.margin_mode_policy import MarginMode

# (min_notional, max_notional, max_leverage, maintenance_margin_rate)
DEFAULT_BITGET_BTC_TIERS = ((0.0, None, 150.0, 0.004),)


def seed_venue_margin_envelope(
    *,
    venue: str = "bitget",
    symbol: str = "BTC/USDT",
    market_type: str = "perp",
    margin_mode: str = "isolated",
    tiers=DEFAULT_BITGET_BTC_TIERS,
    fetched_at_ms: int | None = None,
    source: str = "test-fixture/bitget-market-leverage-tiers",
    store: bool = True,
):
    """Build (and by default persist) one venue margin envelope for tests."""
    from aletheia.trading.venue_margin import (
        MarginTier,
        build_venue_margin_envelope,
        store_venue_margin_envelope,
    )

    resolved_tiers = tuple(
        MarginTier(
            tier=index + 1,
            min_notional=None if lower is None else float(lower),
            max_notional=None if upper is None else float(upper),
            max_leverage=float(leverage),
            maintenance_margin_rate=float(rate),
        )
        for index, (lower, upper, leverage, rate) in enumerate(tiers)
    )
    envelope = build_venue_margin_envelope(
        venue=venue,
        symbol=symbol,
        market_type=market_type,
        # VenueMarginEnvelope validates the allowed modes at runtime.
        margin_mode=cast("MarginMode", margin_mode),
        tiers=resolved_tiers,
        source=source,
        fetched_at_ms=fetched_at_ms if fetched_at_ms is not None else int(time.time() * 1000),
    )
    if store:
        store_venue_margin_envelope(envelope)
    return envelope
