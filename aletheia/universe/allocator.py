"""Universe Allocator — scoring, quota enforcement, TTL assignment, explore/exploit."""

from __future__ import annotations

import hashlib
import json
import random
import logging

from aletheia.config.schema import UniverseConfig
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

# TTL by scanner family (seconds)
_FAMILY_TTL = {
    "momentum": 1800,       # 30 min — fast signals
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
        stats = repo.calibration_stats()
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
        if n < _MIN_CALIBRATION_SAMPLES:
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
                "eligible_outcome_n": int(n),
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


def compute_total_score(
    feat: dict,
    scanner_result: dict,
    cfg: UniverseConfig,
    calibration: dict[str, float] | None = None,
    scoring_cohort_key: str | None = None,
    *,
    calibration_evidence: dict[str, dict] | None = None,
) -> float:
    """Compute the final total_score for universe ranking.

    Linear combination: opportunity + tradability + liquidity - costs - penalties.
    When calibration multipliers are provided, opportunity_score is scaled by
    the family's empirical hit_rate performance.
    """
    opportunity = scanner_result.get("opportunity_score", 0)
    family = scanner_result.get("scanner_family", "momentum")
    if calibration:
        exact_key = _calibration_key(
            family,
            scanner_result.get("scanner_direction") or scanner_result.get("direction"),
            feat.get("market_type", "spot"),
        )
        evidence_key = exact_key if exact_key in calibration else family
        raw_multiplier = float(calibration.get(evidence_key, 1.0))
        # Historical calibration can propose only a bounded direction. It does
        # not receive score authority until a prospective treatment/control
        # verdict passes the shared experiment gate.
        proposed_multiplier = 1.1 if raw_multiplier > 1.0 else 0.9 if raw_multiplier < 1.0 else 1.0
        if proposed_multiplier != 1.0:
            from aletheia.learning.experiments import apply_numeric_treatment

            direction = scanner_result.get("scanner_direction") or scanner_result.get("direction") or "unknown"
            market_type = feat.get("market_type", "spot")
            feature_id = feat.get("feature_snapshot_id")
            # A feature snapshot may be intentionally reused for up to 30 minutes,
            # while each scanner run creates a distinct prospective allocation
            # decision and entry-price window.  Include both identities so later
            # runs cannot upsert into one canonical outcome or accumulate multiple
            # Universe mirrors under the same candidate key.
            candidate_key = (
                (
                    f"universe:{scoring_cohort_key}:{feature_id}:"
                    f"{family}:{direction}:{market_type}"
                )
                if feature_id and scoring_cohort_key
                else (
                    f"universe:{feature_id}:{family}:{direction}:{market_type}"
                    if feature_id else None
                )
            )
            cluster_key = (
                f"universe:cluster:{scoring_cohort_key}:{family}:{direction}:{market_type}"
                if scoring_cohort_key else None
            )
            horizon = _FAMILY_CALIBRATION_HORIZON.get(family, _DEFAULT_CALIBRATION_HORIZON)
            influence = apply_numeric_treatment(
                path_id="universe_family_calibration",
                scope={"family": family, "direction": direction, "market_type": market_type},
                action={"type": "opportunity_multiplier", "multiplier": proposed_multiplier},
                source_cohort="universe_forward_outcomes:clean_net:v2",
                target_horizon=horizon,
                candidate_key=candidate_key,
                baseline=float(opportunity),
                treatment=float(opportunity) * proposed_multiplier,
                consumer="universe_allocator",
                consumer_stage="opportunity_score",
                outcome_candidate_key=(f"{candidate_key}:{horizon}" if candidate_key else None),
                manifest={
                    "raw_multiplier": raw_multiplier,
                    "proposed_multiplier": proposed_multiplier,
                    "source_evidence": dict(
                        (calibration_evidence or {}).get(evidence_key) or {}
                    ),
                },
                assignment_key=cluster_key,
            )
            opportunity = influence.value
            if candidate_key:
                feat["learning_candidate_key"] = candidate_key
                feat["learning_exposure_id"] = influence.exposure_id
                feat["learning_target_horizon"] = horizon
    # Operator-adjustable per-family weight. The PI Operator writes
    # cfg.scanner_weights via _action_adjust_scanner_weight (OL-C09) and the UI
    # shows it, but scoring never consumed it — the manual bias was inert. Empty
    # dict (default) → 1.0 for every family, preserving current behavior.
    opportunity *= cfg.scanner_weights.get(family, 1.0)
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


def resolve_family_quota(cfg: UniverseConfig, regime_state: str | None) -> dict[str, float]:
    """Return the family-quota ratios to apply for the current regime (2A+.9).

    If the regime state has a per-regime override in cfg.family_quota_by_regime,
    it REPLACES the static cfg.family_quota for this allocation. Otherwise the
    static quota is used (empty override map → fully backward compatible).
    """
    if regime_state:
        override = (cfg.family_quota_by_regime or {}).get(regime_state)
        if override:
            return override
    return cfg.family_quota


def allocate_universe(
    scored: list[dict], cfg: UniverseConfig, regime_state: str | None = None,
) -> list[dict]:
    """Select final universe candidates with quota enforcement and explore/exploit.

    Steps:
    1. Sort by total_score descending
    2. Apply family quotas (regime-aware — see resolve_family_quota) so no single
       family exceeds its share
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

    # Family quota limits (absolute counts) — regime-aware (2A+.9)
    quota = resolve_family_quota(cfg, regime_state)
    family_limits: dict[str, int] = {}
    for family, ratio in quota.items():
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
        default_limit = max(1, int(max_total * 0.3))
        # US-13: don't let explore promote 0/low-score candidates into the executable
        # universe. Keep exploration, but require minimal viability first.
        # US-17: and only from families that have NOT already hit their quota.
        viable = [
            r for r in remainder
            if r.get("total_score", 0) >= _EXPLORE_MIN_SCORE
            and family_counts.get(r.get("scanner_family", "momentum"), 0)
            < family_limits.get(r.get("scanner_family", "momentum"), default_limit)
        ]
        if viable:
            sample_size = min(explore_count, len(viable))
            explore_selected = random.sample(viable, sample_size)
            for r in explore_selected:
                fam = r.get("scanner_family", "momentum")
                family_counts[fam] = family_counts.get(fam, 0) + 1

    # Combine and assign TTL + rank metadata
    final = exploit_selected + explore_selected
    final = final[:max_total]

    for item in final:
        family = item.get("scanner_family", "momentum")
        item["ttl_sec"] = _FAMILY_TTL.get(family, 3600)

        # Guard: only mark execution_eligible if there is live price data within the
        # last 2 hours. Symbols with zero snapshots (e.g. NATGAS/USDT) would otherwise
        # reach PI Agent as tradeable but fail silently at proposal submission.
        _sym = item.get("symbol", "")
        _has_live_price = has_fresh_market_snapshot(_sym, item.get("market_type", "spot"))

        if not _has_live_price:
            logger.warning(
                "no_live_price_for_symbol: %s — execution_eligible set to False", _sym
            )
        item["execution_eligible"] = _has_live_price

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
        "Allocation: %d scored → %d exploit + %d explore = %d final (regime=%s)",
        len(scored), len(exploit_selected), len(explore_selected), len(final),
        regime_state or "static",
    )
    return final
