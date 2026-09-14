"""Asset registry policy and publication from a normalized market catalog.

When running in demo mode (e.g. okx_demo), the registry is narrowed to only
symbols actually supported on the demo environment — no wasted scanning.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from aletheia.config.schema import AletheiaConfig
from aletheia.data.symbols import (
    instrument_background_rejection_reason,
)
from aletheia.storage.models import UniverseAsset
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.universe.registry")


async def _load_demo_whitelist(paper_execution_mode: str) -> frozenset | None:
    """Load the demo symbol whitelist for the active execution mode.

    Returns None if no demo filtering is needed (live mode or full-access demos).

    The shared execution capability seam returns {"usdt_futures": frozenset,
    "spot": frozenset}. Collapse to a single frozenset so callers can do
    `symbol in whitelist`.
    """
    from aletheia.execution.venue_capabilities import fetch_venue_capabilities

    markets = await fetch_venue_capabilities(paper_execution_mode)
    if markets is None:
        return None
    return markets.get("usdt_futures", frozenset()) | markets.get("spot", frozenset())


def _invalidate_stale_pipeline_data() -> None:
    """Flush stale universe pipeline data after a venue/strategy change.

    Expires active snapshots and cancels all pending forward outcomes so
    downstream jobs (data_collectors, outcome_recorder) don't operate on
    symbols that no longer exist on the active exchange.
    """
    repo.expire_universe_snapshots(market_type="crypto")
    cancelled = repo.cancel_all_pending_forward_outcomes()
    logger.info(
        "Pipeline invalidated: snapshots superseded, %d pending outcomes cancelled",
        cancelled,
    )


async def sync_registry(cfg: AletheiaConfig) -> int:
    """Sync the universe_registry from the configured exchange.

    Consumes normalized active USDT-quoted markets from the bound adapter.
    When in demo mode with a restricted symbol set (e.g. OKX demo),
    only symbols actually executable on demo are registered.

    Returns count of assets upserted.
    """
    from aletheia.data.market_catalog import fetch_market_catalog

    md = cfg.market_data
    venue = md.ccxt_exchange
    fallback_venue = md.ccxt_fallback or venue
    assets: list[UniverseAsset] = []
    sync_started_at = datetime.now(timezone.utc).isoformat()

    # Load demo whitelist if applicable
    demo_whitelist = await _load_demo_whitelist(cfg.exchange.paper_execution_mode)
    if demo_whitelist is not None:
        logger.info("Demo whitelist active: %d symbols for %s",
                    len(demo_whitelist), cfg.exchange.paper_execution_mode)

    catalog = await fetch_market_catalog(venue, fallback_venue)
    include_futures = cfg.exchange.enabled_markets.get("usdt_futures", False)
    now = datetime.now(timezone.utc).isoformat()
    for entry in catalog:
        canonical = entry.symbol
        market_type = entry.market_type
        if market_type == "perp" and not include_futures:
            continue
        background = entry.background
        if entry.excluded_crypto and background["asset_class"] == "crypto":
            continue

        # Demo whitelist filter: skip symbols not executable on demo
        if demo_whitelist is not None and canonical not in demo_whitelist:
            # US-14: was a silent continue. Debug-log so an unexpectedly empty
            # demo universe (whitelist-format mismatch) is diagnosable.
            logger.debug("demo_whitelist: skip %s (not executable on %s)",
                         canonical, cfg.exchange.paper_execution_mode)
            continue

        config_blacklisted = canonical in cfg.universe.blacklist
        support_reason = instrument_background_rejection_reason(
            background,
            symbol=canonical,
            market_type=market_type,
            venue=venue,
        )
        blacklisted = config_blacklisted or support_reason is not None
        blacklist_reason = None
        if config_blacklisted:
            blacklist_reason = "config_blacklist"
        elif support_reason:
            blacklist_reason = support_reason

        asset = UniverseAsset(
            venue=venue,
            market_type=market_type,
            symbol=canonical,
            base_asset=entry.base,
            quote_asset=entry.quote,
            status="active",
            min_notional=entry.min_notional,
            price_precision=entry.price_precision,
            amount_precision=entry.amount_precision,
            first_seen_at=now,
            last_seen_at=now,
            is_blacklisted=blacklisted,
            blacklist_reason=blacklist_reason,
            metadata=json.dumps(
                {"instrument_background": background}, sort_keys=True
            ),
        )
        assets.append(asset)

    count, deactivated_other, deactivated_stale = repo.sync_universe_assets(
        assets,
        active_venue=venue,
        stale_cutoff_iso=sync_started_at,
    )
    # Deactivate other venues' assets (exchange was switched)
    if deactivated_other:
        logger.info("Deactivated %d assets from other venues", deactivated_other)

    # Deactivate symbols no longer seen on current venue
    if deactivated_stale:
        logger.info("Deactivated %d stale assets from %s", deactivated_stale, venue)

    # If registry changed significantly, invalidate stale universe pipeline data
    # so downstream jobs (data_collectors, outcome_recorder) don't operate on
    # symbols that no longer exist on the active exchange.
    if deactivated_other or deactivated_stale:
        _invalidate_stale_pipeline_data()

    # Always invalidate the venue listing cache after a registry sync so that
    # the next market_data / price_feed cycle re-fetches the current whitelist.
    # This covers both exchange switches (deactivated_other) and routine
    # delistings (deactivated_stale), as well as a first-time sync where
    # neither counter is non-zero but the cache may be cold.
    try:
        from aletheia.data.venue_listing_cache import invalidate_venue_listing
        invalidate_venue_listing(venue)
    except Exception:  # noqa: BLE001 — cache invalidation is best-effort
        pass

    logger.info("Registry sync: %d active assets from %s", count, venue)
    return count
