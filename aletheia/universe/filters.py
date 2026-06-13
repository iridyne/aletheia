"""Tradability Filters — layer 1 coarse filtering of the raw asset pool."""

from __future__ import annotations

import logging

from aletheia.config.schema import UniverseConfig

logger = logging.getLogger("aletheia.universe.filters")

# Max data freshness in seconds before an asset is considered stale
_MAX_FRESHNESS_SEC = 7200  # 2 hours


def _compute_tradability_score(feat: dict, cfg: UniverseConfig) -> float:
    """Compute a 0-100 tradability score based on volume, spread, depth."""
    score = 0.0

    vol = feat.get("quote_volume_24h") or 0
    if vol >= cfg.min_volume_24h_usdt * 5:
        score += 40.0
    elif vol >= cfg.min_volume_24h_usdt * 2:
        score += 30.0
    elif vol >= cfg.min_volume_24h_usdt:
        score += 20.0
    else:
        score += max(0, (vol / cfg.min_volume_24h_usdt) * 20.0)

    spread = feat.get("spread_bps")
    if spread is not None:
        if spread <= 5:
            score += 35.0
        elif spread <= 10:
            score += 28.0
        elif spread <= cfg.max_spread_bps:
            score += 20.0
        else:
            score += max(0, 20.0 - (spread - cfg.max_spread_bps))
    else:
        score += 15.0  # no spread data, neutral

    freshness = feat.get("data_freshness_sec")
    if freshness is not None:
        if freshness <= 300:
            score += 25.0
        elif freshness <= 3600:
            score += 20.0
        elif freshness <= _MAX_FRESHNESS_SEC:
            score += 10.0
        else:
            score += 0.0
    else:
        score += 10.0

    return min(100.0, max(0.0, score))


def apply_tradability_filters(features: list[dict], cfg: UniverseConfig) -> list[dict]:
    """Filter assets by tradability criteria and compute tradability scores.

    Applies:
    - Minimum 24h volume
    - Maximum spread
    - Data freshness
    - Blacklist exclusion

    Returns filtered list with tradability_score added.
    """
    passed = []

    for feat in features:
        symbol = feat.get("symbol", "")

        # Blacklist check
        if symbol in cfg.blacklist:
            continue

        # Volume filter
        vol = feat.get("quote_volume_24h") or 0
        if vol < cfg.min_volume_24h_usdt:
            continue

        # Spread filter (skip if no spread data — we still include it)
        spread = feat.get("spread_bps")
        if spread is not None and spread > cfg.max_spread_bps * 2:
            continue

        # Data freshness: reject if data is too stale
        freshness = feat.get("data_freshness_sec")
        if freshness is not None and freshness > _MAX_FRESHNESS_SEC:
            continue

        # Compute tradability score
        tradability = _compute_tradability_score(feat, cfg)
        feat["tradability_score"] = tradability
        passed.append(feat)

    logger.info("Tradability filter: %d → %d passed", len(features), len(passed))
    return passed
