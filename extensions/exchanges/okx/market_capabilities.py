"""On-demand public sandbox symbol restrictions for the frozen OKX Extension."""

import logging
import threading
import time

from aletheia.async_utils import run_coro_from_sync
from extensions.exchanges.ccxt.market_capabilities import load_public_market_capabilities

logger = logging.getLogger("aletheia.okx.market_capabilities")


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
    """Refresh the OKX demo symbol cache on demand.

    Returns the total number of supported symbols loaded (swap + spot).
    """
    global _okx_demo_symbols, _okx_demo_symbols_loaded_at

    async def _load():
        return await load_public_market_capabilities("okx", sandbox=True)

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
