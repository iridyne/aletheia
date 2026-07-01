"""Asset registry sync — fetches tradable pairs from the active exchange via CCXT.

When running in demo mode (e.g. okx_demo), the registry is narrowed to only
symbols actually supported on the demo environment — no wasted scanning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aletheia.config.schema import AletheiaConfig
from aletheia.data.symbols import to_canonical
from aletheia.storage.models import UniverseAsset
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.universe.registry")

_LEVERAGED_SUFFIXES = ("3L", "3S", "5L", "5S", "2L", "2S", "UP", "DOWN", "BULL", "BEAR")
_EXCLUDED_BASES = {"BTTC", "LUNC", "LUNA", "UST", "FTT"}


def _precision_to_decimals(p) -> int | None:
    """CCXT market precision → integer decimal places.

    CCXT reports precision in one of two modes: DECIMAL_PLACES (an int like 3) or
    TICK_SIZE (a float like 0.001). int(0.001) == 0 silently destroyed tick-size
    precision, so downstream orders rounded to whole units and got rejected.
    Convert a sub-1 tick size to its decimal-place count.
    """
    if p is None:
        return None
    try:
        if isinstance(p, float) and 0 < p < 1:
            import math
            return max(0, int(round(-math.log10(p))))
        return int(p)
    except (ValueError, OverflowError):
        return None


def _is_excluded(symbol_info: dict) -> bool:
    """Filter out leveraged tokens, test symbols, and known-bad assets."""
    base = (symbol_info.get("base") or "").upper()
    symbol_id = (symbol_info.get("id") or "").upper()

    if any(base.endswith(s) for s in _LEVERAGED_SUFFIXES):
        return True
    if base in _EXCLUDED_BASES:
        return True
    # US-15: "_" excludes exchange-native underscore ids (e.g. dated futures
    # "BTC_USDT_240628"). On venues whose SPOT ids use "_" (e.g. KuCoin "BTC_USDT")
    # this would over-exclude everything — revisit if adding such a venue. Current
    # venues (OKX/Bitget) use "-"/no separator, so this is safe today.
    if "TEST" in symbol_id or "_" in symbol_id:
        return True
    return False


async def _load_demo_whitelist(paper_execution_mode: str) -> frozenset | None:
    """Load the demo symbol whitelist for the active execution mode.

    Returns None if no demo filtering is needed (live mode or full-access demos).
    """
    if paper_execution_mode == "okx_demo":
        from aletheia.exchanges.okx.client import OKXClient
        return await OKXClient.load_demo_markets()
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

    Uses CCXT load_markets() to get all active USDT-quoted pairs.
    When in demo mode with a restricted symbol set (e.g. OKX demo),
    only symbols actually executable on demo are registered.

    Returns count of assets upserted.
    """
    from aletheia.data.ccxt_provider import CCXTProvider

    venue = cfg.market_data.ccxt_exchange
    provider = CCXTProvider(exchange=venue, fallback_exchange=venue)
    count = 0
    sync_started_at = datetime.now(timezone.utc).isoformat()

    try:
        # Load demo whitelist if applicable
        demo_whitelist = await _load_demo_whitelist(cfg.exchange.paper_execution_mode)
        if demo_whitelist is not None:
            logger.info("Demo whitelist active: %d symbols for %s",
                        len(demo_whitelist), cfg.exchange.paper_execution_mode)

        exchange = provider._ensure_primary()
        # Don't set fetchMarkets — OKX rejects 'linear' (uses 'SWAP' internally).
        # Load all markets and filter in the loop below.
        await exchange.load_markets()
        markets = exchange.markets
        include_futures = cfg.exchange.enabled_markets.get("usdt_futures", False)

        now = datetime.now(timezone.utc).isoformat()

        for symbol_key, info in markets.items():
            if not info.get("active", True):
                continue
            quote = (info.get("quote") or "").upper()
            if quote != "USDT":
                continue
            if _is_excluded(info):
                continue

            base = (info.get("base") or "").upper()
            if not base:
                continue

            canonical = f"{base}/{quote}"

            # Determine market type and filter
            is_perp = info.get("swap") or info.get("future")
            if is_perp and not include_futures:
                continue
            if info.get("option"):
                continue
            market_type = "perp" if is_perp else "spot"

            # Demo whitelist filter: skip symbols not executable on demo
            if demo_whitelist is not None and canonical not in demo_whitelist:
                # US-14: was a silent continue. Debug-log so an unexpectedly empty
                # demo universe (whitelist-format mismatch) is diagnosable.
                logger.debug("demo_whitelist: skip %s (not executable on %s)",
                             canonical, cfg.exchange.paper_execution_mode)
                continue

            limits = info.get("limits") or {}
            cost_limits = limits.get("cost") or {}
            min_notional = cost_limits.get("min")

            precision = info.get("precision") or {}
            price_prec = precision.get("price")
            amount_prec = precision.get("amount")

            blacklisted = canonical in cfg.universe.blacklist

            asset = UniverseAsset(
                venue=venue,
                market_type=market_type,
                symbol=canonical,
                base_asset=base,
                quote_asset=quote,
                status="active",
                min_notional=min_notional,
                price_precision=_precision_to_decimals(price_prec),
                amount_precision=_precision_to_decimals(amount_prec),
                first_seen_at=now,
                last_seen_at=now,
                is_blacklisted=blacklisted,
                blacklist_reason="config_blacklist" if blacklisted else None,
            )
            repo.upsert_universe_asset(asset)
            count += 1

        # Deactivate other venues' assets (exchange was switched)
        deactivated_other = repo.deactivate_universe_assets_not_in_venue(venue)
        if deactivated_other:
            logger.info("Deactivated %d assets from other venues", deactivated_other)

        # Deactivate symbols no longer seen on current venue
        deactivated_stale = repo.deactivate_stale_universe_assets(venue, sync_started_at)
        if deactivated_stale:
            logger.info("Deactivated %d stale assets from %s", deactivated_stale, venue)

        # If registry changed significantly, invalidate stale universe pipeline data
        # so downstream jobs (data_collectors, outcome_recorder) don't operate on
        # symbols that no longer exist on the active exchange.
        if deactivated_other or deactivated_stale:
            _invalidate_stale_pipeline_data()

        logger.info("Registry sync: %d active assets from %s", count, venue)
    finally:
        await provider.close()

    return count
