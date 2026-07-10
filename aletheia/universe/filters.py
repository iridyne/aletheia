"""Tradability Filters — layer 1 coarse filtering of the raw asset pool."""

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
        "tradability_spread": {"maximum_rejection_spread_bps": cfg.max_spread_bps * 2},
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


def apply_tradability_filters(
    features: list[dict],
    cfg: UniverseConfig,
    scanner_run_id: str | None = None,
) -> list[dict]:
    """Filter assets by tradability criteria and compute tradability scores.

    Applies:
    - Minimum 24h volume
    - Maximum spread
    - Data freshness
    - Blacklist exclusion

    Returns filtered list with tradability_score added.
    """
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

        # Spread filter (skip if no spread data — we still include it)
        spread = feat.get("spread_bps")
        if not active:
            facts_by_filter["tradability_spread"].append(_candidate_fact(
                feat, decision="not_applicable", reason_code="prior_gate_rejected",
            ))
        elif spread is not None and spread > cfg.max_spread_bps * 2:
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

        if not active:
            continue

        # Compute tradability score
        tradability = _compute_tradability_score(feat, cfg)
        feat["tradability_score"] = tradability
        passed.append(feat)

    _record_tradability_batches(scanner_run_id, facts_by_filter, cfg)
    logger.info("Tradability filter: %d → %d passed", len(features), len(passed))
    return passed
