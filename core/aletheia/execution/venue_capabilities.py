"""Read-only venue capability boundary shared by execution-domain callers."""

from __future__ import annotations

import logging
import threading
import time

from aletheia.async_utils import run_coro_from_sync

logger = logging.getLogger("aletheia.execution.venue_capabilities")


def venue_execution_mode(venue: str, configured_exchange_mode: str) -> str:
    """Resolve the durable execution mode for a venue under the configured mode.

    Looks the answer up in UTA's authoritative mode list rather than composing
    `f"{venue}_demo"`. Two reasons string composition is wrong here:

    * the non-live suffix is NOT uniform — Binance's is `binance_testnet`, not
      `binance_demo` — so composition yields a mode no registry, profile, or Risk
      pool recognizes;
    * `config.exchanges.EXECUTION_MODE_VENUES` is the single registered identity
      list. `okx_demo` still uses the dedicated CCXT sandbox capability seam
      below, but it shares the same physical venue identity.
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

_REGISTRY_CACHE_TTL = 14400  # 4h — same cadence as the OKX demo capability cache
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


_okx_demo_symbols: dict | None = None  # {"swap": frozenset, "spot": frozenset}
_okx_demo_symbols_loaded_at: float = 0.0
_OKX_SYMBOLS_TTL = 14400  # 4h
_symbols_lock = threading.Lock()


def _get_demo_symbols(market_type: str = "usdt_futures") -> frozenset:
    """Return cached OKX demo symbols for the given market_type, loading lazily.

    market_type "usdt_futures" / "perp" → swap frozenset
    market_type "spot"                  → spot frozenset

    Refresh failures propagate so an unavailable whitelist fails closed before order
    submission. An empty frozenset means the venue loaded successfully but has no
    products of the requested market type.
    """
    global _okx_demo_symbols, _okx_demo_symbols_loaded_at
    key = "spot" if market_type == "spot" else "swap"
    if _okx_demo_symbols is not None and (time.time() - _okx_demo_symbols_loaded_at) < _OKX_SYMBOLS_TTL:
        return _okx_demo_symbols.get(key, frozenset())
    # Cache missing or stale — trigger a synchronous refresh
    refresh_okx_demo_symbols()
    if _okx_demo_symbols is None:
        raise RuntimeError("okx_demo_symbol_refresh_unavailable")
    return _okx_demo_symbols.get(key, frozenset())


def refresh_okx_demo_symbols() -> int:
    """Refresh the OKX demo symbol cache. Called by scheduler every 4h.

    Returns the total number of supported symbols loaded (swap + spot).
    """
    global _okx_demo_symbols, _okx_demo_symbols_loaded_at

    async def _load():
        return await fetch_venue_capabilities("okx_demo")

    with _symbols_lock:
        if (
            _okx_demo_symbols is not None
            and (time.time() - _okx_demo_symbols_loaded_at) < _OKX_SYMBOLS_TTL
        ):
            return sum(len(_okx_demo_symbols.get(key, frozenset())) for key in ("swap", "spot"))
        result = run_coro_from_sync(_load())
        if not isinstance(result, dict):
            raise RuntimeError("okx_demo_symbol_refresh_unavailable")
        _okx_demo_symbols = result
        _okx_demo_symbols_loaded_at = time.time()
        total = len(result.get("swap", frozenset())) + len(result.get("spot", frozenset()))
        logger.info("OKX demo symbols refreshed: %d swap + %d spot",
                    len(result.get("swap", frozenset())), len(result.get("spot", frozenset())))
        return total


def load_venue_capabilities(execution_mode: str) -> dict | None:
    """Return normalized read-only capability facts for an execution mode.

    OKX demo keeps the existing cache/source semantics. Other exchange
    modes use the existing ``universe_registry`` truth. ``None`` is unknown or
    unavailable; an empty set inside a loaded mapping remains known unavailable.
    """
    if execution_mode == "okx_demo":
        swap = _get_demo_symbols("usdt_futures")
        spot = _get_demo_symbols("spot")
        if swap is None or spot is None:
            return None
        return {"swap": swap, "spot": spot}

    from aletheia.config.exchanges import execution_mode_venue

    venue = execution_mode_venue(execution_mode)
    if venue is None:
        return None
    return load_registry_symbols(venue)


async def fetch_venue_capabilities(execution_mode: str) -> dict | None:
    """Fetch fresh read-only capability facts when a caller owns a refresh.

    Universe synchronization historically fetched the OKX sandbox list on every
    sync. Keep that freshness contract while routing the concrete client through
    this same boundary; cached execution checks continue using
    :func:`load_venue_capabilities`.
    """
    if execution_mode == "okx_demo":
        from aletheia.data.providers import get_market_data_provider

        provider = get_market_data_provider("okx", exact_venue=True)
        try:
            read = getattr(provider, "get_public_market_capabilities", None)
            if not callable(read):
                raise RuntimeError("market capabilities unavailable")
            capabilities = await read(sandbox=True)
            if not isinstance(capabilities, dict) or any(
                key not in {"spot", "swap"}
                or not isinstance(symbols, frozenset)
                or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
                for key, symbols in capabilities.items()
            ):
                raise ValueError("market capabilities invalid")
            return capabilities
        finally:
            await provider.close()
    return load_venue_capabilities(execution_mode)


def get_supported_symbols(
    execution_mode: str,
    market_type: str = "usdt_futures",
) -> frozenset | None:
    """Return canonical symbols known for ``execution_mode`` and market type."""
    capabilities = load_venue_capabilities(execution_mode)
    if capabilities is None:
        return None
    if execution_mode == "okx_demo":
        key = "spot" if market_type == "spot" else "swap"
    else:
        key = market_type
    return capabilities.get(key, frozenset())
