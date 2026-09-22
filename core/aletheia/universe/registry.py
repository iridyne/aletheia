"""Asset registry admission and persistence from normalized venue listing facts.

When running in demo mode (e.g. okx_demo), the registry is narrowed to only
symbols actually supported on the demo environment — no wasted scanning.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from aletheia.config.schema import AletheiaConfig
from aletheia.data.providers import MarketInstrument, get_market_data_provider
from aletheia.data.symbols import instrument_background_rejection_reason, to_canonical
from aletheia.storage.models import UniverseAsset
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.universe.registry")

_LEVERAGED_SUFFIXES = ("3L", "3S", "5L", "5S", "2L", "2S", "UP", "DOWN", "BULL", "BEAR")
_EXCLUDED_BASES = {"BTTC", "LUNC", "LUNA", "UST", "FTT"}


def _is_excluded(instrument: MarketInstrument) -> bool:
    """Filter out unsupported asset classes, leveraged tokens, and test symbols."""
    base = instrument.base_asset

    # OKX category 3 is tokenized stocks/ETFs. The equity observer handles these
    # as observation-only until asset-class metadata and execution capability are
    # explicitly connected; treating them as crypto makes them silently executable.
    if instrument.is_tokenized_equity:
        return True

    if any(base.endswith(s) for s in _LEVERAGED_SUFFIXES):
        return True
    if base in _EXCLUDED_BASES:
        return True
    # US-15: "_" excludes exchange-native underscore ids (e.g. dated futures
    # "BTC_USDT_240628"). On venues whose SPOT ids use "_" (e.g. KuCoin "BTC_USDT")
    # this would over-exclude everything — revisit if adding such a venue. Current
    # venues (OKX/Bitget) use "-"/no separator, so this is safe today.
    if instrument.is_test_or_dated:
        return True
    return False


async def _load_demo_whitelist(paper_execution_mode: str) -> frozenset | None:
    """Load the demo symbol whitelist for the active execution mode.

    Returns None if no demo filtering is needed (live mode or full-access demos).

    The shared execution capability seam returns {"swap": frozenset,
    "spot": frozenset}. Collapse to a single frozenset so callers can do
    `symbol in whitelist`.
    """
    if paper_execution_mode == "okx_demo":
        from aletheia.execution.venue_capabilities import fetch_venue_capabilities

        markets = await fetch_venue_capabilities(paper_execution_mode)
        if markets is None:
            return None
        return markets.get("swap", frozenset()) | markets.get("spot", frozenset())
    # bitget_demo and binance_testnet support their full perp list — no extra filter
    return None


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

    Reads normalized active USDT-quoted pairs from the configured venue.
    When in demo mode with a restricted symbol set (e.g. OKX demo),
    only symbols actually executable on demo are registered.

    Returns count of assets upserted.
    """
    md = cfg.market_data
    venue = md.ccxt_exchange
    provider = get_market_data_provider(venue, exact_venue=True)
    assets: list[UniverseAsset] = []
    sync_started_at = datetime.now(timezone.utc).isoformat()

    try:
        # Load demo whitelist if applicable
        demo_whitelist = await _load_demo_whitelist(cfg.exchange.paper_execution_mode)
        if demo_whitelist is not None:
            logger.info("Demo whitelist active: %d symbols for %s",
                        len(demo_whitelist), cfg.exchange.paper_execution_mode)

        read_instruments = getattr(provider, "get_market_instruments", None)
        if not callable(read_instruments):
            raise RuntimeError("market_instruments_unavailable")
        instruments = await read_instruments()
        if not isinstance(instruments, list) or any(
            not isinstance(item, MarketInstrument) or item.venue != venue
            or item.market_type not in {"spot", "perp"}
            or not isinstance(item.base_asset, str) or not item.base_asset
            or item.quote_asset != "USDT"
            or item.symbol != f"{item.base_asset}/{item.quote_asset}"
            or item.symbol != to_canonical(item.symbol)
            or not isinstance(item.background, dict)
            or any(item.background.get(field) != expected for field, expected in (
                ("venue", item.venue), ("symbol", item.symbol),
                ("market_type", item.market_type), ("physical_market_type", item.market_type),
            ))
            for item in instruments
        ):
            raise ValueError("market_instruments_identity_invalid")
        include_futures = cfg.exchange.enabled_markets.get("usdt_futures", False)

        now = datetime.now(timezone.utc).isoformat()

        for instrument in instruments:
            canonical = instrument.symbol
            market_type = instrument.market_type
            if market_type == "perp" and not include_futures:
                continue
            background = instrument.background
            asset_class = background["asset_class"]
            # Keep unsupported products visible with an honest background.
            # Ordinary crypto exclusions remain absent altogether.
            if _is_excluded(instrument) and asset_class == "crypto":
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
                base_asset=instrument.base_asset,
                quote_asset=instrument.quote_asset,
                status="active",
                min_notional=instrument.min_notional,
                price_precision=instrument.price_precision,
                amount_precision=instrument.amount_precision,
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
    finally:
        await provider.close()

    return count
