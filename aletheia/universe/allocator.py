"""Universe Allocator — scoring, quota enforcement, TTL assignment, explore/exploit."""

from __future__ import annotations

import random
import logging

from aletheia.config.schema import UniverseConfig

logger = logging.getLogger("aletheia.universe.allocator")

# TTL by scanner family (seconds)
_FAMILY_TTL = {
    "momentum": 1800,       # 30 min — fast signals
    "mean_reversion": 3600, # 1 hour
    "volatility": 7200,     # 2 hours
    "derivatives": 14400,   # 4 hours
    "event": 43200,         # 12 hours
    "core": 86400,          # 24 hours
}


def compute_late_entry_penalty(feat: dict) -> float:
    """Compute penalty for chasing a move that's already extended.

    Returns 0-30 (subtracted from total score). Higher = more penalty.
    """
    penalty = 0.0

    ret_1h = feat.get("return_1h") or 0
    ret_4h = feat.get("return_4h") or 0
    rsi_val = feat.get("rsi_14")

    # Already moved significantly in 1h
    if abs(ret_1h) > 5.0:
        penalty += 15.0
    elif abs(ret_1h) > 3.0:
        penalty += 8.0

    # Extended 4h move
    if abs(ret_4h) > 10.0:
        penalty += 10.0
    elif abs(ret_4h) > 7.0:
        penalty += 5.0

    # Extreme RSI indicates move is mature
    if rsi_val is not None:
        if rsi_val > 85 or rsi_val < 15:
            penalty += 10.0
        elif rsi_val > 78 or rsi_val < 22:
            penalty += 5.0

    return min(30.0, penalty)


def compute_total_score(feat: dict, scanner_result: dict, cfg: UniverseConfig) -> float:
    """Compute the final total_score for universe ranking.

    Linear combination: opportunity + tradability + liquidity - costs - penalties.
    """
    opportunity = scanner_result.get("opportunity_score", 0)
    tradability = feat.get("tradability_score", 50.0)

    # Liquidity score from volume (simple: high volume = good liquidity)
    vol = feat.get("quote_volume_24h") or 0
    if vol >= cfg.min_volume_24h_usdt * 10:
        liquidity = 90.0
    elif vol >= cfg.min_volume_24h_usdt * 5:
        liquidity = 75.0
    elif vol >= cfg.min_volume_24h_usdt * 2:
        liquidity = 60.0
    else:
        liquidity = 40.0

    # Estimated cost from spread
    spread = feat.get("spread_bps") or 10
    estimated_cost = min(20.0, spread * 0.5)

    # Crowding risk (proxy: extreme RSI + large returns = crowded)
    crowding = 0.0
    rsi_val = feat.get("rsi_14")
    ret_4h = feat.get("return_4h") or 0
    if rsi_val is not None:
        if (rsi_val > 80 and ret_4h > 5) or (rsi_val < 20 and ret_4h < -5):
            crowding = 15.0
        elif (rsi_val > 70 and ret_4h > 3) or (rsi_val < 30 and ret_4h < -3):
            crowding = 8.0

    late_penalty = compute_late_entry_penalty(feat)

    # Data staleness penalty
    freshness = feat.get("data_freshness_sec") or 0
    staleness_penalty = 0.0
    if freshness > 3600:
        staleness_penalty = 10.0
    elif freshness > 1800:
        staleness_penalty = 5.0

    # Weighted combination
    total = (
        0.35 * opportunity
        + 0.25 * tradability
        + 0.15 * liquidity
        - estimated_cost
        - crowding
        - late_penalty
        - staleness_penalty
    )

    # Store sub-scores on feat for downstream use
    feat["liquidity_score"] = liquidity
    feat["estimated_cost"] = estimated_cost
    feat["crowding_risk"] = crowding
    feat["late_entry_penalty"] = late_penalty

    return max(0.0, min(100.0, total))


def allocate_universe(scored: list[dict], cfg: UniverseConfig) -> list[dict]:
    """Select final universe candidates with quota enforcement and explore/exploit.

    Steps:
    1. Sort by total_score descending
    2. Apply family quotas (no single family exceeds its share)
    3. Split into exploit (by score) and explore (random from remainder)
    4. Assign TTL per family
    5. Cap at max_candidates
    """
    if not scored:
        return []

    # Sort by total score
    scored.sort(key=lambda x: x.get("total_score", 0), reverse=True)

    max_total = cfg.max_candidates
    exploit_count = int(max_total * (1 - cfg.explore_ratio))
    explore_count = max_total - exploit_count

    # Family quota limits (absolute counts)
    family_limits: dict[str, int] = {}
    for family, ratio in cfg.family_quota.items():
        family_limits[family] = max(1, int(max_total * ratio))

    # Exploit selection: top by score, respecting quotas
    exploit_selected: list[dict] = []
    family_counts: dict[str, int] = {}
    remainder: list[dict] = []

    for item in scored:
        family = item.get("scanner_family", "momentum")
        current = family_counts.get(family, 0)
        limit = family_limits.get(family, max(1, int(max_total * 0.3)))

        if len(exploit_selected) >= exploit_count:
            remainder.append(item)
        elif current >= limit:
            remainder.append(item)
        else:
            exploit_selected.append(item)
            family_counts[family] = current + 1

    # Explore selection: random sample from remainder
    explore_selected: list[dict] = []
    if remainder and explore_count > 0:
        sample_size = min(explore_count, len(remainder))
        explore_selected = random.sample(remainder, sample_size)

    # Combine and assign TTL + rank metadata
    final = exploit_selected + explore_selected
    final = final[:max_total]

    for item in final:
        family = item.get("scanner_family", "momentum")
        item["ttl_sec"] = _FAMILY_TTL.get(family, 3600)
        item["execution_eligible"] = True

        # Risk flags
        risk_flags = []
        if item.get("late_entry_penalty", 0) > 15:
            risk_flags.append("late_entry")
        if item.get("crowding_risk", 0) > 10:
            risk_flags.append("crowded")
        if (item.get("spread_bps") or 0) > 20:
            risk_flags.append("wide_spread")
        item["risk_flags"] = risk_flags

    logger.info(
        "Allocation: %d scored → %d exploit + %d explore = %d final",
        len(scored), len(exploit_selected), len(explore_selected), len(final),
    )
    return final
