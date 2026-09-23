"""Read-only venue capability boundary shared by execution-domain callers."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("aletheia.execution.venue_capabilities")


def venue_execution_mode(venue: str, configured_exchange_mode: str) -> str:
    """Resolve the durable execution mode for a venue under the configured mode.

    Looks the answer up in UTA's authoritative mode list rather than composing
    `f"{venue}_demo"`. Two reasons string composition is wrong here:

    * the non-live suffix is NOT uniform — Binance's is `binance_testnet`, not
      `binance_demo` — so composition yields a mode no registry, profile, or Risk
      pool recognizes;
    * `config.exchanges.EXECUTION_MODE_VENUES` is the single registered identity
      list.
    """
    from aletheia.config.exchanges import execution_mode_environment, execution_mode_venue
    from aletheia.uta.manager import UTA_EXECUTION_MODES

    live = configured_exchange_mode == "live"
    candidates = sorted(
        mode for mode in UTA_EXECUTION_MODES
        if execution_mode_venue(mode) == venue
        and (execution_mode_environment(mode) == "live") is live
    )
    if not candidates:
        raise ValueError(
            f"no {'live' if live else 'non-live'} execution mode registered for venue {venue!r}"
        )
    if len(candidates) > 1:
        raise ValueError(f"ambiguous execution modes for venue {venue!r}: {candidates}")
    return candidates[0]


# Entry execution currently has one proven exchange-Demo handoff. Extend this
# registry only with matching Planner, Risk/Gateway, and execution tests.
ENTRY_EXECUTION_MINIMUM_PERMISSION: dict[str, str] = {
    "bitget_demo": "Full",
}


def entry_execution_minimum_permission(execution_mode: str | None) -> str | None:
    """Return the registered Entry Plan permission for one execution identity."""
    return ENTRY_EXECUTION_MINIMUM_PERMISSION.get(str(execution_mode or "").strip())

_REGISTRY_CACHE_TTL = 14400  # 4h
# universe_registry.market_type is free TEXT and is populated with the universe
# vocabulary ("perp"/"spot"); every consumer of this cache indexes it with the
# execution vocabulary ("usdt_futures"/"spot") that Proposals and Orders carry.
# Without translating, a synced venue reports "no usdt_futures products" for
# every futures symbol it actually lists.
_REGISTRY_MARKET_TYPE_ALIASES = {"perp": "usdt_futures", "swap": "usdt_futures"}
_registry_symbol_cache: dict[str, tuple[dict, float]] = {}
_registry_cache_lock = threading.Lock()


def load_registry_symbols(venue: str) -> dict | None:
    """Return cached ``{market_type: frozenset}`` symbols for one venue.

    ``None`` means the registry is unavailable or has not been synchronized yet;
    callers preserve the existing fail-open pre-order behavior in that case.
    """
    with _registry_cache_lock:
        cached = _registry_symbol_cache.get(venue)
        if cached is not None and (time.time() - cached[1]) < _REGISTRY_CACHE_TTL:
            return cached[0]

    try:
        from aletheia.storage.repositories import list_universe_assets

        # This cache is execution authority, not a paginated UI feed. A bounded
        # page would turn every valid symbol after the cutoff into a false
        # "unavailable" rejection.
        assets = list_universe_assets(venue=venue, active_only=True, limit=None)
        if not assets:
            return None  # registry not yet synced — preserve fail-open semantics
        by_type: dict[str, set] = {}
        for row in assets:
            market_type = row.get("market_type") or "usdt_futures"
            market_type = _REGISTRY_MARKET_TYPE_ALIASES.get(market_type, market_type)
            by_type.setdefault(market_type, set()).add(row["symbol"])
        result = {key: frozenset(value) for key, value in by_type.items()}
        with _registry_cache_lock:
            _registry_symbol_cache[venue] = (result, time.time())
        total = sum(len(symbols) for symbols in result.values())
        logger.debug(
            "Symbol cache loaded: %d symbols (%s) for venue=%s",
            total,
            list(result.keys()),
            venue,
        )
        return result
    except Exception as exc:
        logger.warning("Failed to load registry symbols for venue=%s: %s", venue, exc)
        return None


def load_venue_capabilities(execution_mode: str) -> dict | None:
    """Return normalized registry facts for an execution mode's venue."""
    from aletheia.config.exchanges import execution_mode_venue

    venue = execution_mode_venue(execution_mode)
    if venue is None:
        return None
    return load_registry_symbols(venue)


def get_supported_symbols(
    execution_mode: str,
    market_type: str = "usdt_futures",
) -> frozenset | None:
    """Return canonical symbols known for ``execution_mode`` and market type."""
    capabilities = load_venue_capabilities(execution_mode)
    if capabilities is None:
        return None
    return capabilities.get(market_type, frozenset())
