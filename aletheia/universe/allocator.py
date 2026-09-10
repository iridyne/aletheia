"""Current Candidate execution decoration and historical allocation replay.

Only historical Twin/Relative-Rank replay uses quota/explore allocation. Current
Candidate selection belongs to Candidate Selection Utility; calibration summaries
remain read-only diagnostics for the operator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random

from aletheia.config.schema import UniverseConfig
from aletheia.universe.outcome_economics import COST_MODEL_VERSION
from aletheia.universe.snapshot_freshness import has_fresh_market_snapshot

logger = logging.getLogger("aletheia.universe.allocator")

# Calibration: minimum samples before adjusting a family's weight
_MIN_CALIBRATION_SAMPLES = 50
# P1-3: calibrate on cost-aware NET EXPECTANCY, not win rate. A family can win often
# yet bleed money (small wins, occasional large losses); win rate would boost it, net
# expectancy correctly penalizes it.
#  - shrink the sample mean toward neutral for small n (empirical Bayes)
#  - REF net return earns the full ±max_step of adjustment
#  - a catastrophic worst-case single outcome forbids any BOOST (downside harm)
_CALIBRATION_SHRINKAGE = 50
_CALIBRATION_REF_NET_PCT = 1.0
_CALIBRATION_HARM_FLOOR_PCT = -15.0
# US-13: explore candidates below this total_score are too weak to promote into the
# executable universe (they would be flagged execution_eligible with a near-zero score).
_EXPLORE_MIN_SCORE = 20.0
# Per-family calibration horizon (matches typical holding period)
_FAMILY_CALIBRATION_HORIZON = {
    "momentum": "4h",
    "mean_reversion": "1h",
    "volatility": "1h",
    # 1h chosen over 4h: derivatives 4h win_rate=39.5% (n=43, likely proxy noise),
    # 1h win_rate=52.9% (n=51) better matches derivatives signal quality.
    "derivatives": "1h",
}
_DEFAULT_CALIBRATION_HORIZON = "1h"

# Historical per-family TTL values (seconds), exposed by analytics readers.
# Current entry TTL comes from config.universe.entry_ttl_sec.
_FAMILY_TTL = {
    "momentum": 1800,       # 30 min
    "mean_reversion": 3600, # 1 hour
    "volatility": 7200,     # 2 hours
    "derivatives": 14400,   # 4 hours
    "event": 43200,         # 12 hours
    "core": 86400,          # 24 hours
}


def _calibration_key(family: str, direction: str | None, market_type: str | None) -> str:
    return f"{family}:{direction or 'unknown'}:{market_type or 'spot'}"


def get_family_multipliers(
    max_step: float = 0.10,
    *,
    evidence_out: dict[str, dict] | None = None,
) -> dict[str, float]:
    """Compute stratified opportunity-score multipliers from net outcomes.

    Returns {family:direction:market_type: multiplier}. Legacy test/adaptor rows
    without direction/market_type retain a family-only compatibility key, but
    production rows are never folded across economically opposite cohorts.

    Each family is calibrated on its natural horizon (momentum → 4h,
    derivatives/mean_reversion/volatility → 1h).
    """
    from aletheia.storage import repositories as repo

    try:
        stats = repo.calibration_stats(cost_model_version=COST_MODEL_VERSION)
    except Exception:
        return {}

    multipliers: dict[str, float] = {}
    window_metadata = None
    if evidence_out is not None:
        from aletheia.analytics.learning_window import outcome_window_metadata

        window_metadata = outcome_window_metadata()
    for row in stats:
        family = row.get("scanner_family")
        if not family:
            continue
        expected_horizon = _FAMILY_CALIBRATION_HORIZON.get(family, _DEFAULT_CALIBRATION_HORIZON)
        if row.get("horizon") != expected_horizon:
            continue
        n = row.get("n", 0)
        independent_n = row.get("independent_n") if row.get("independent_n") is not None else n
        if independent_n < _MIN_CALIBRATION_SAMPLES:
            continue
        avg_net = row.get("avg_return")
        if avg_net is None:
            continue
        # Shrink the cost-aware mean net return toward neutral for small samples, then
        # map to a bounded score adjustment. A negative net expectancy penalizes the
        # family even if its hit rate is high.
        shrunk = float(avg_net) * n / (n + _CALIBRATION_SHRINKAGE)
        adjustment = shrunk / _CALIBRATION_REF_NET_PCT * max_step
        adjustment = max(-max_step, min(max_step, adjustment))
        # Downside harm: never BOOST a family whose worst settled outcome was
        # catastrophic (a fat left tail is not alpha). Penalties are still allowed.
        min_return = row.get("min_return")
        if adjustment > 0 and min_return is not None and float(min_return) < _CALIBRATION_HARM_FLOOR_PCT:
            adjustment = 0.0
        direction = row.get("direction")
        market_type = row.get("market_type")
        key = (
            _calibration_key(family, direction, market_type)
            if direction or market_type
            else family
        )
        multipliers[key] = 1.0 + adjustment
        if evidence_out is not None:
            evidence = {
                "contract": "universe-calibration-source-evidence/v1",
                "source": "universe_forward_outcomes",
                "window": window_metadata,
                "scanner_family": family,
                "direction": direction,
                "market_type": market_type,
                "horizon": row.get("horizon"),
                "cost_model_version": row.get("cost_model_version"),
                "eligible_outcome_n": int(n),
                "independent_outcome_n": int(independent_n),
                "distinct_symbols": int(row.get("distinct_symbols") or 0),
                "source_outcome_fingerprint": row.get(
                    "source_outcome_fingerprint"
                ),
                "source_window_start": row.get("source_window_start"),
                "win_count": int(row.get("win_count") or 0),
                "avg_net_return_pct": float(avg_net),
                "min_net_return_pct": (
                    None if row.get("min_return") is None
                    else float(row["min_return"])
                ),
                "max_net_return_pct": (
                    None if row.get("max_return") is None
                    else float(row["max_return"])
                ),
                "proposed_multiplier": 1.0 + adjustment,
            }
            canonical = json.dumps(
                evidence, sort_keys=True, separators=(",", ":"),
            )
            evidence_out[key] = {
                **evidence,
                "evidence_fingerprint": hashlib.sha256(
                    canonical.encode("utf-8")
                ).hexdigest(),
            }

    return multipliers


_DERIVATIVE_MARKET_TYPES = frozenset({"perp", "swap", "futures", "usdt_futures"})


def resolve_family_quota(cfg: UniverseConfig, regime_state: str | None) -> dict[str, float]:
    """Return the family-quota ratios to apply for the current regime (2A+.9).

    If the regime state has a per-regime override in cfg.family_quota_by_regime,
    it REPLACES the static cfg.family_quota for this allocation. Otherwise the
    static quota is used (empty override map → fully backward compatible).
    """
    import math as _math
    if regime_state:
        override = (cfg.family_quota_by_regime or {}).get(regime_state)
        if override:
            quota = override
        else:
            quota = cfg.family_quota
    else:
        quota = cfg.family_quota
    for k, val in (quota or {}).items():
        if not isinstance(val, (int, float)) or not _math.isfinite(float(val)):
            raise ValueError(f"not finite: family_quota['{k}'] = {val!r}")
    return quota


def decorate_universe_candidates(
    candidates: list[dict],
    cfg: UniverseConfig,
) -> list[dict]:
    """Apply the shared execution and risk projection to selected candidates."""

    for item in candidates:
        item["ttl_sec"] = cfg.entry_ttl_sec
        symbol = item.get("symbol", "")
        has_live_price = has_fresh_market_snapshot(
            symbol,
            item.get("market_type", "spot"),
            exchange=item.get("venue"),
        )
        if not has_live_price:
            logger.warning(
                "no_live_price_for_symbol: %s — execution_eligible set to False",
                symbol,
            )
        item["execution_eligible"] = has_live_price
        risk_flags = []
        if item.get("late_entry_penalty", 0) > 15:
            risk_flags.append("late_entry")
        if item.get("crowding_risk", 0) > 10:
            risk_flags.append("crowded")
        if (item.get("spread_bps") or 0) > 20:
            risk_flags.append("wide_spread")
        item["risk_flags"] = risk_flags
    return candidates


def allocate_universe(
    scored: list[dict],
    cfg: UniverseConfig,
    regime_state: str | None = None,
    *,
    explore_sampler=None,
    decorate_selected: bool = True,
    log_result: bool = True,
) -> list[dict]:
    """Select final universe candidates with quota enforcement and explore/exploit.

    Steps:
    1. Sort by total_score descending
    2. Apply family quotas (regime-aware — see resolve_family_quota) so no single
       family exceeds its share
    3. Split into exploit (by score) and explore (random from remainder)
    4. Assign entry TTL from config (unified across all families, #1325)
    5. Cap at max_candidates
    """
    if not scored:
        return []

    # Sort by total score
    scored.sort(key=lambda x: x.get("total_score", 0), reverse=True)

    max_total = cfg.max_candidates
    exploit_count = int(max_total * (1 - cfg.explore_ratio))
    explore_count = max_total - exploit_count

    # Family quota limits (absolute counts) — regime-aware (2A+.9)
    regime_quota = resolve_family_quota(cfg, regime_state)
    static_quota = resolve_family_quota(cfg, None)

    def quota_key(item: dict) -> tuple[str, str]:
        context = (item.get("analysis_context") or {}).get("kind")
        return (str(context or "legacy_crypto"), item.get("scanner_family", "momentum"))

    def family_limit(item: dict) -> int:
        context, family = quota_key(item)
        quota = static_quota if context == "stock_linked_perp" else regime_quota
        return max(1, int(max_total * quota.get(family, 0.3)))

    # Exploit selection: top by score, respecting quotas
    exploit_selected: list[dict] = []
    family_counts: dict[tuple[str, str], int] = {}
    remainder: list[dict] = []

    for item in scored:
        key = quota_key(item)
        current = family_counts.get(key, 0)
        limit = family_limit(item)

        if len(exploit_selected) >= exploit_count:
            remainder.append(item)
        elif current >= limit:
            remainder.append(item)
        else:
            exploit_selected.append(item)
            family_counts[key] = current + 1

    # Explore selection: random sample from remainder, but only families still
    # UNDER their quota (US-17). Historically explore random-sampled the whole
    # remainder — and because run_all_scanners is winner-take-all per symbol and
    # momentum wins ~43% of symbols, that remainder is dominated by OVER-quota
    # momentum. So explore silently re-flooded the very family the exploit phase
    # had just capped, producing 84% momentum in the live pool despite a 30%
    # momentum quota (diagnosis 2026-07-03). Restricting explore to under-quota
    # families means exploration adds DIVERSITY, not more of the dominant family.
    explore_selected: list[dict] = []
    if remainder and explore_count > 0:
        # US-13: don't let explore promote 0/low-score candidates into the executable
        # universe. Keep exploration, but require minimal viability first.
        # US-17: and only from families that have NOT already hit their quota.
        viable = [
            r for r in remainder
            if r.get("total_score", 0) >= _EXPLORE_MIN_SCORE
            and family_counts.get(quota_key(r), 0) < family_limit(r)
        ]
        if viable:
            # Randomize the eligible remainder, then admit candidates one by one.
            # Sampling ``explore_count`` items in one shot is not capacity-safe:
            # when a family has one slot left, the sample can contain several
            # members from that family because every item was checked against the
            # same pre-sample count. Re-checking after each admission keeps the
            # documented quota invariant across exploit *and* explore.
            sampler = explore_sampler or random.sample
            randomized = sampler(viable, len(viable))
            for r in randomized:
                if len(explore_selected) >= explore_count:
                    break
                key = quota_key(r)
                limit = family_limit(r)
                current = family_counts.get(key, 0)
                if current >= limit:
                    continue
                explore_selected.append(r)
                family_counts[key] = current + 1

    # Combine and assign TTL + rank metadata
    final = exploit_selected + explore_selected
    final = final[:max_total]

    if decorate_selected:
        decorate_universe_candidates(final, cfg)

    if log_result:
        logger.info(
            "Allocation: %d scored → %d exploit + %d explore = %d final (regime=%s)",
            len(scored), len(exploit_selected), len(explore_selected), len(final),
            regime_state or "static",
        )
    return final
