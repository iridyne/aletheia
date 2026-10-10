"""Read-only venue capability boundary shared by execution-domain callers."""

from __future__ import annotations

import logging
import threading
import time

from aletheia.async_utils import run_coro_from_sync
from aletheia.config.exchanges import ENTRY_ROUTE_POLICIES

logger = logging.getLogger("aletheia.execution.venue_capabilities")


# Derived from the one route registry, `config.exchanges.ENTRY_ROUTE_POLICIES`.
ENTRY_EXECUTION_MINIMUM_PERMISSION: dict[str, str] = {
    mode: policy.minimum_permission for mode, policy in ENTRY_ROUTE_POLICIES.items()
}


def entry_execution_minimum_permission(execution_mode: str | None) -> str | None:
    """Return the registered Entry Plan permission for one execution identity."""
    return ENTRY_EXECUTION_MINIMUM_PERMISSION.get(str(execution_mode or "").strip())

_REGISTRY_CACHE_TTL = 14400  # 4h — same cadence as the demo capability cache
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



# --- Demo capability cache ---------------------------------------------------
# Execution modes with a sandbox/restricted listing (sandbox flag from the
# CCXT profile) fetch their actual symbol set on every universe sync and cache
# it here, keyed by execution_mode.  Uniform key vocabulary:
#   {"usdt_futures": frozenset, "spot": frozenset}
# A single dict replaces the okx-specific globals that existed here before.

_DEMO_CAPABILITIES_TTL = 14400  # 4 h
_demo_capabilities_cache: dict[str, tuple[dict, float]] = {}
_demo_capabilities_lock = threading.Lock()


def _capability_cache_key(execution_mode: str) -> str:
    """Cache identity: the execution mode **and** the profile that lists its symbols.

    A venue's profile determines which listing it exposes, so keying on
    ``execution_mode`` alone could serve another profile's symbols until the TTL
    expired (#3475).
    The profile class is resolved through the same ``_profile_spec`` the order path
    uses, so the key cannot drift from the venue an order will actually meet; a mode
    whose profile cannot be resolved keeps its bare-mode identity (those modes have
    no restricted listing to confuse).
    """
    mode = str(execution_mode or "").strip().lower()
    try:
        from aletheia.config.loader import load_config
        from extensions.exchanges.composition import _profile_spec

        profile_type = _profile_spec(load_config(), mode)[-1]
        identity = f"{getattr(profile_type, '__name__', '')}:{getattr(profile_type, 'profile_version', '')}"
    except Exception:
        return mode
    return f"{mode}|{identity}"


def refresh_demo_capabilities(execution_mode: str) -> int:
    """Refresh the demo capability cache for *execution_mode*.

    Called by the scheduler every 4 h for each active sandbox execution mode.
    Returns total symbol count (usdt_futures + spot).  Raises on failure so
    the scheduler records the job failure rather than caching stale data.
    """
    async def _load() -> dict | None:
        return await fetch_venue_capabilities(execution_mode)

    cache_key = _capability_cache_key(execution_mode)
    with _demo_capabilities_lock:
        entry = _demo_capabilities_cache.get(cache_key)
        if entry is not None and (time.time() - entry[1]) < _DEMO_CAPABILITIES_TTL:
            return sum(len(v) for v in entry[0].values())

    result = run_coro_from_sync(_load())
    if not isinstance(result, dict):
        raise RuntimeError(f"{execution_mode}_demo_symbol_refresh_unavailable")
    total = sum(len(v) for v in result.values())
    logger.info(
        "%s capabilities refreshed: %d usdt_futures + %d spot",
        execution_mode,
        len(result.get("usdt_futures", frozenset())),
        len(result.get("spot", frozenset())),
    )
    with _demo_capabilities_lock:
        _demo_capabilities_cache[cache_key] = (result, time.time())
    return total


def _is_sandbox_mode(execution_mode: str) -> bool:
    """Return True iff *execution_mode* uses a restricted sandbox listing.

    Asks the CCXT profile resolver — the same source of truth used by
    ``fetch_venue_capabilities`` and ``read_order_constraints``.

    An identity with no external venue (``paper_internal``, the empty default)
    provably has no restricted listing, so it returns False. For an external
    identity a profile-resolution failure is NOT reported as False:
    ``_listing_view`` raises ``ConstraintUnavailable`` and that propagates,
    because an unprovable listing source must never be mistaken for "not
    restricted" — that would silently skip the demo filter entirely.
    """
    from aletheia.config.exchanges import EXTERNAL_EXECUTION_MODES
    from extensions.exchanges.ccxt.order_rules import _listing_view

    mode = str(execution_mode or "").strip().lower()
    if mode not in EXTERNAL_EXECUTION_MODES:
        return False
    sandbox, _, _xn = _listing_view(mode)
    return bool(sandbox)


def load_venue_capabilities(execution_mode: str) -> dict | None:
    """Return normalized ``{market_type: frozenset}`` capability facts.

    Keys use the execution-domain vocabulary: ``"usdt_futures"`` and ``"spot"``.
    Returns ``None`` when non-sandbox data is unavailable or not yet
    synchronized (fail-open — an unknown non-restricted venue does not block).

    Sandbox modes trigger a synchronous refresh when the cache is cold.
    Refresh failures propagate (fail-closed): an unavailable sandbox listing
    must stop pre-order validation rather than silently fall through to the
    live registry, which would leak the wrong venue's listing.

    A mode whose profile cannot be resolved at all returns ``None``: this
    pre-order capability check is fail-open, and the submission path's
    ``read_order_constraints`` is independently fail-closed on the same
    resolution.
    """
    cache_key = _capability_cache_key(execution_mode)
    with _demo_capabilities_lock:
        entry = _demo_capabilities_cache.get(cache_key)
        if entry is not None and (time.time() - entry[1]) < _DEMO_CAPABILITIES_TTL:
            return entry[0]

    from aletheia.execution.order_constraints import ConstraintUnavailable

    try:
        sandbox_mode = _is_sandbox_mode(execution_mode)
    except ConstraintUnavailable:
        logger.warning(
            "Capability listing source unresolvable for execution_mode=%s; "
            "pre-order symbol availability stays unknown",
            execution_mode,
        )
        return None

    if sandbox_mode:
        refresh_demo_capabilities(execution_mode)
        with _demo_capabilities_lock:
            entry = _demo_capabilities_cache.get(cache_key)
        if entry is None:
            raise RuntimeError(f"{execution_mode}_demo_symbol_refresh_unavailable")
        return entry[0]

    from aletheia.config.exchanges import execution_mode_venue

    venue = execution_mode_venue(execution_mode)
    if venue is None:
        return None
    return load_registry_symbols(venue)


async def fetch_venue_capabilities(execution_mode: str) -> dict | None:
    """Fetch a fresh, profile-aware capability snapshot for *execution_mode*.

    Uses the same profile resolution as ``read_order_constraints`` (venue,
    sandbox flag, listing options) so the universe filter and the
    pre-submission constraint check consult the exact same venue listing, via
    the shared ``MarketDataProvider.get_public_market_capabilities`` seam.
    Returns ``{"usdt_futures": frozenset, "spot": frozenset}`` for sandbox
    modes; falls through to the universe registry for non-sandbox modes.
    """
    if not _is_sandbox_mode(execution_mode):
        # Non-sandbox modes use the universe registry as the authority.
        return load_venue_capabilities(execution_mode)

    from aletheia.config.exchanges import execution_mode_venue
    from aletheia.data.providers import get_market_data_provider
    from extensions.exchanges.ccxt.order_rules import _listing_view

    venue = execution_mode_venue(execution_mode)
    if venue is None:
        return None
    sandbox, extra, exchange_name = _listing_view(execution_mode)
    provider = get_market_data_provider(venue, exact_venue=True)
    try:
        read = getattr(provider, "get_public_market_capabilities", None)
        if not callable(read):
            raise RuntimeError("market capabilities unavailable")
        from typing import cast, Callable, Any, Awaitable
        read_fn = cast(Callable[..., Awaitable[Any]], read)
        capabilities = await read_fn(sandbox=sandbox, extra_options=extra, exchange_name=exchange_name or None)
        if not isinstance(capabilities, dict) or any(
            key not in {"spot", "usdt_futures"}
            or not isinstance(symbols, frozenset)
            or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
            for key, symbols in capabilities.items()
        ):
            raise ValueError("market capabilities invalid")
        return capabilities
    finally:
        await provider.close()


def get_supported_symbols(
    execution_mode: str,
    market_type: str = "usdt_futures",
) -> frozenset | None:
    """Return canonical symbols known for ``execution_mode`` and market type."""
    capabilities = load_venue_capabilities(execution_mode)
    if capabilities is None:
        return None
    return capabilities.get(market_type, frozenset())
