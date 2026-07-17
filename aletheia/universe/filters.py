"""Tradability Filters — two-pass filtering of the raw asset pool.

Pass 1 (coarse, pre-scan): apply_coarse_tradability_filters
  Hard-binary gates (stablecoin, blacklist, freshness, volume) plus an extreme
  spread rejection at 3× max_spread_bps. Borderline spread cases (2x–3x) survive
  to be re-evaluated after the scanner assigns families.

Pass 2 (fine, post-scan): apply_fine_tradability_filters
  Applies spread filter with per-family tolerance multipliers (from filter_advisor).
  Records individual rejections via record_filter_decision with scanner_family and
  direction, so filter_candidate_outcomes rows are family-attributed — creating the
  data that filter_advisor reads back for future calibration.

apply_tradability_filters is kept as a backward-compatible single-pass function
(spread threshold 2×, no per-family tolerance) for existing callers and tests.
"""

from __future__ import annotations

import hashlib
import logging

from aletheia.config.schema import UniverseConfig

logger = logging.getLogger("aletheia.universe.filters")

# Max data freshness in seconds before an asset is considered stale
_MAX_FRESHNESS_SEC = 7200  # 2 hours

# Stablecoins and pegged assets — never tradeable for directional signals
_STABLECOIN_BASES = {
    "USDC", "USDT", "DAI", "TUSD", "BUSD", "FDUSD", "USDP",
    "PYUSD", "USD1", "USDD", "FRAX", "LUSD", "GUSD",
}

_TRADABILITY_FILTERS = (
    "tradability_stablecoin",
    "tradability_blacklist",
    "tradability_volume",
    "tradability_spread",
    "tradability_freshness",
)

# Coarse spread multiplier: assets with spread > max_spread_bps * this are hard-rejected
# pre-scan. The fine filter re-applies the standard 2× threshold (with per-family
# tolerance) on what passes through.
_COARSE_SPREAD_MULTIPLIER = 3.0

# Normalized market type aliases used in family tolerance key lookup
_PERP_MARKET_TYPES = {"perp", "futures", "swap", "usdt_futures"}


def _normalize_market_type(market_type: str | None) -> str:
    mt = (market_type or "spot").lower()
    return "perp" if mt in _PERP_MARKET_TYPES else mt


def _compute_tradability_score(feat: dict, cfg: UniverseConfig) -> float:
    """Compute a 0-100 tradability score based on volume, spread, depth, freshness."""
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

    # Orderbook depth: bonus for deep books, penalty for shallow ones.
    # Only scored when depth data is available; missing depth does not penalize
    # the asset (the collector may not have run yet).
    depth = feat.get("depth_10bps_usd")
    min_depth = cfg.min_depth_10bps_usd
    if depth is not None and min_depth and min_depth > 0:
        if depth >= min_depth * 3:
            score += 10.0
        elif depth >= min_depth:
            score += 5.0
        elif depth >= min_depth * 0.25:
            pass  # marginal — no bonus, no penalty
        else:
            score -= 10.0  # critically shallow: significant penalty

    return min(100.0, max(0.0, score))


def _candidate_fact(
    feat: dict,
    *,
    decision: str,
    reason_code: str,
    inputs: dict | None = None,
) -> dict:
    """Build the compact, non-directional fact stored for one filter gate."""
    symbol = str(feat.get("symbol") or "")
    market_type = str(feat.get("market_type") or "spot")
    venue = str(feat.get("venue") or "")
    candidate_key = (
        feat.get("feature_snapshot_id")
        or feat.get("asset_id")
        or f"instrument:{venue}:{market_type}:{symbol}"
    )
    return {
        "candidate_key": str(candidate_key),
        "asset_id": feat.get("asset_id"),
        "feature_snapshot_id": feat.get("feature_snapshot_id"),
        "symbol": symbol,
        "venue": venue,
        "market_type": market_type,
        "decision": decision,
        "reason_code": reason_code,
        "inputs": inputs or {},
    }


def _record_tradability_batches(
    scanner_run_id: str | None,
    facts_by_filter: dict[str, list[dict]],
    cfg: UniverseConfig,
    spread_multiplier: float = 2.0,
) -> None:
    if not scanner_run_id:
        return

    from aletheia.analytics.filter_evidence import record_filter_decision_batch

    blacklist = sorted(str(symbol) for symbol in cfg.blacklist)
    blacklist_hash = hashlib.sha256("\n".join(blacklist).encode("utf-8")).hexdigest()[:16]
    parameters = {
        "tradability_stablecoin": {"excluded_bases": sorted(_STABLECOIN_BASES)},
        "tradability_blacklist": {
            "blacklist_count": len(blacklist),
            "blacklist_hash": blacklist_hash,
        },
        "tradability_volume": {"minimum_quote_volume_24h_usdt": cfg.min_volume_24h_usdt},
        "tradability_spread": {"maximum_rejection_spread_bps": cfg.max_spread_bps * spread_multiplier},
        "tradability_freshness": {"maximum_freshness_sec": _MAX_FRESHNESS_SEC},
    }
    for filter_name in _TRADABILITY_FILTERS:
        record_filter_decision_batch(
            run_id=scanner_run_id,
            candidate_kind="universe_feature",
            filter_name=filter_name,
            stage="tradability_filter",
            candidate_facts=facts_by_filter[filter_name],
            parameters=parameters[filter_name],
        )


def _run_filter_pass(
    features: list[dict],
    cfg: UniverseConfig,
    scanner_run_id: str | None,
    spread_multiplier: float,
) -> list[dict]:
    """Core filter logic shared by apply_tradability_filters and apply_coarse_tradability_filters."""
    passed = []
    facts_by_filter: dict[str, list[dict]] = {
        name: [] for name in _TRADABILITY_FILTERS
    }

    for feat in features:
        symbol = feat.get("symbol", "")
        active = True

        # Stablecoin / pegged-asset exclusion
        base = symbol.split("/")[0]
        if base in _STABLECOIN_BASES:
            facts_by_filter["tradability_stablecoin"].append(_candidate_fact(
                feat,
                decision="rejected",
                reason_code="stablecoin_base_excluded",
                inputs={"base": base},
            ))
            active = False
        else:
            facts_by_filter["tradability_stablecoin"].append(_candidate_fact(
                feat,
                decision="passed",
                reason_code="non_stablecoin_base",
                inputs={"base": base},
            ))

        # Blacklist check
        if not active:
            facts_by_filter["tradability_blacklist"].append(_candidate_fact(
                feat, decision="not_applicable", reason_code="prior_gate_rejected",
            ))
        elif symbol in cfg.blacklist:
            facts_by_filter["tradability_blacklist"].append(_candidate_fact(
                feat,
                decision="rejected",
                reason_code="configured_blacklist",
                inputs={"is_blacklisted": True},
            ))
            active = False
        else:
            facts_by_filter["tradability_blacklist"].append(_candidate_fact(
                feat,
                decision="passed",
                reason_code="not_blacklisted",
                inputs={"is_blacklisted": False},
            ))

        # Volume filter
        vol = feat.get("quote_volume_24h") or 0
        if not active:
            facts_by_filter["tradability_volume"].append(_candidate_fact(
                feat, decision="not_applicable", reason_code="prior_gate_rejected",
            ))
        elif vol < cfg.min_volume_24h_usdt:
            facts_by_filter["tradability_volume"].append(_candidate_fact(
                feat,
                decision="rejected",
                reason_code="quote_volume_below_minimum",
                inputs={"quote_volume_24h": vol},
            ))
            active = False
        else:
            facts_by_filter["tradability_volume"].append(_candidate_fact(
                feat,
                decision="passed",
                reason_code="quote_volume_sufficient",
                inputs={"quote_volume_24h": vol},
            ))

        # Spread filter
        spread = feat.get("spread_bps")
        if not active:
            facts_by_filter["tradability_spread"].append(_candidate_fact(
                feat, decision="not_applicable", reason_code="prior_gate_rejected",
            ))
        elif spread is not None and spread > cfg.max_spread_bps * spread_multiplier:
            facts_by_filter["tradability_spread"].append(_candidate_fact(
                feat,
                decision="rejected",
                reason_code="spread_above_rejection_limit",
                inputs={"spread_bps": spread},
            ))
            active = False
        else:
            facts_by_filter["tradability_spread"].append(_candidate_fact(
                feat,
                decision="passed",
                reason_code="spread_within_limit" if spread is not None else "spread_unavailable_allowed",
                inputs={"spread_bps": spread},
            ))

        # Data freshness: reject if data is too stale
        freshness = feat.get("data_freshness_sec")
        if not active:
            facts_by_filter["tradability_freshness"].append(_candidate_fact(
                feat, decision="not_applicable", reason_code="prior_gate_rejected",
            ))
        elif freshness is not None and freshness > _MAX_FRESHNESS_SEC:
            facts_by_filter["tradability_freshness"].append(_candidate_fact(
                feat,
                decision="rejected",
                reason_code="market_data_stale",
                inputs={"data_freshness_sec": freshness},
            ))
            active = False
        else:
            facts_by_filter["tradability_freshness"].append(_candidate_fact(
                feat,
                decision="passed",
                reason_code="market_data_fresh" if freshness is not None else "freshness_unavailable_allowed",
                inputs={"data_freshness_sec": freshness},
            ))

        # Depth gate: hard reject only when depth is critically low (< 10% of
        # configured minimum). Skipped when depth is unavailable — the collector
        # runs on its own schedule and absence of data should not block assets.
        if active:
            depth = feat.get("depth_10bps_usd")
            min_depth = cfg.min_depth_10bps_usd
            if (
                depth is not None
                and min_depth
                and min_depth > 0
                and depth < min_depth * 0.1
            ):
                logger.debug(
                    "Depth gate rejected %s: depth=%.0f < %.0f (10%% of min)",
                    feat.get("symbol"), depth, min_depth * 0.1,
                )
                active = False

        if not active:
            continue

        feat["tradability_score"] = _compute_tradability_score(feat, cfg)
        passed.append(feat)

    _record_tradability_batches(scanner_run_id, facts_by_filter, cfg, spread_multiplier=spread_multiplier)
    logger.info("Tradability filter (spread_mult=%.1f): %d → %d passed", spread_multiplier, len(features), len(passed))
    return passed


def apply_tradability_filters(
    features: list[dict],
    cfg: UniverseConfig,
    scanner_run_id: str | None = None,
) -> list[dict]:
    """Filter assets by tradability criteria (spread threshold: 2× max_spread_bps).

    Backward-compatible single-pass function used by existing callers and tests.
    Production pipeline uses apply_coarse_tradability_filters + apply_fine_tradability_filters.
    """
    return _run_filter_pass(features, cfg, scanner_run_id, spread_multiplier=2.0)


def apply_coarse_tradability_filters(
    features: list[dict],
    cfg: UniverseConfig,
    scanner_run_id: str | None = None,
) -> list[dict]:
    """Pre-scan filter pass (spread threshold: 3× max_spread_bps).

    Removes definite non-tradables before the scanner runs. Borderline spread
    cases (2x–3x max_spread_bps) pass through for family-aware fine filtering
    via apply_fine_tradability_filters after scanner assigns families.
    """
    return _run_filter_pass(features, cfg, scanner_run_id, spread_multiplier=_COARSE_SPREAD_MULTIPLIER)


def apply_fine_tradability_filters(
    scored_features: list[dict],
    cfg: UniverseConfig,
    family_tolerances: dict[str, float] | None = None,
    scanner_run_id: str | None = None,
) -> list[dict]:
    """Post-scan spread filter with per-family tolerance.

    Receives scanner-scored features (each has scanner_family and scanner_direction).
    Applies spread threshold = max_spread_bps * 2 * tolerance(family). Default
    tolerance is 1.0 (same as current 2× threshold), so initial behavior is
    unchanged until filter_advisor accumulates calibration data.

    Rejections are recorded via record_filter_decision with scanner_family and
    direction → filter_candidate_outcomes rows get family populated → this data
    feeds back into filter_advisor for future tolerance calibration.
    """
    tolerances = family_tolerances or {}
    passed = []
    n_rejected = 0

    for scored in scored_features:
        spread = scored.get("spread_bps")
        if spread is None:
            # No spread data — pass through (same as current behavior)
            passed.append(scored)
            continue

        family = scored.get("scanner_family")
        direction = scored.get("scanner_direction")
        market_type = _normalize_market_type(scored.get("market_type"))

        # Per-family tolerance lookup: exact key first, then family-only fallback
        exact_key = f"{family}:{direction or 'unknown'}:{market_type}"
        tolerance = tolerances.get(exact_key, tolerances.get(family or "", 1.0))
        effective_threshold = cfg.max_spread_bps * 2.0 * tolerance

        if spread > effective_threshold:
            n_rejected += 1
            _record_fine_spread_rejection(scored, cfg, scanner_run_id, tolerance)
        else:
            passed.append(scored)

    if n_rejected:
        logger.info(
            "Fine spread filter: %d/%d rejected (family-aware threshold)",
            n_rejected, len(scored_features),
        )
    return passed


def _record_fine_spread_rejection(
    scored: dict,
    cfg: UniverseConfig,
    scanner_run_id: str | None,
    tolerance: float,
) -> None:
    """Record a fine-filter spread rejection with scanner_family and direction.

    Unlike the coarse batch recording, this creates a filter_candidate_outcomes row
    with scanner_family populated — enabling filter_advisor to aggregate per-family
    missed-alpha statistics.
    """
    if not scanner_run_id:
        return

    from aletheia.analytics.filter_evidence import record_filter_decision

    symbol = str(scored.get("symbol") or "")
    if not symbol:
        return

    venue = str(scored.get("venue") or "")
    market_type = str(scored.get("market_type") or "spot")
    family = scored.get("scanner_family")
    direction = scored.get("scanner_direction")  # None → no filter_candidate_outcomes row
    spread = scored.get("spread_bps")
    price = scored.get("price") or scored.get("last_price") or scored.get("close")
    candidate_key = (
        scored.get("feature_snapshot_id")
        or scored.get("asset_id")
        or f"instrument:{venue}:{market_type}:{symbol}"
    )

    try:
        record_filter_decision(
            candidate_key=str(candidate_key),
            candidate_kind="universe_feature",
            filter_name="tradability_spread",
            stage="fine_tradability_filter",
            decision="rejected",
            reason_code="spread_above_family_threshold",
            symbol=symbol,
            market_type=market_type,
            run_id=scanner_run_id,
            venue=venue or None,
            direction=direction,
            scanner_family=family,
            inputs={
                "spread_bps": spread,
                "effective_threshold_bps": round(cfg.max_spread_bps * 2.0 * tolerance, 2),
                "family_tolerance": round(tolerance, 4),
            },
            parameters={
                "base_rejection_spread_bps": cfg.max_spread_bps * 2.0,
                "family_tolerance": round(tolerance, 4),
            },
            entry_price_usd=float(price) if price else None,
            entry_spread_bps=float(spread) if spread else None,
        )
    except Exception as exc:
        logger.warning(
            "fine filter evidence write failed symbol=%s family=%s: %s",
            symbol, family, exc,
        )
