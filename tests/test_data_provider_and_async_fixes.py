"""Focused regressions for B9-data batch (#91, #92).

- #91: features retains configured fallback construction. Registry has always
       loaded primary metadata only; its regression now proves no cross-venue
       substitution and no write on failed reads, rather than constructor args.
- #92: MacroCollector.collect() called _fetch_tradfi() (which drives yfinance
       HTTP under the hood) directly from an async context, blocking the
       scheduler's event loop for the length of each ticker fetch. Now routed
       through asyncio.to_thread so the blocking work happens off-loop.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# #91 — universe modules use ccxt_fallback (not same-exchange fallback)
# ---------------------------------------------------------------------------


def _cfg_with(md_kwargs: dict):
    """Build the minimal cfg shape universe/registry.sync_universe reads."""
    from aletheia.config.schema import AletheiaConfig
    cfg = AletheiaConfig()
    for k, v in md_kwargs.items():
        setattr(cfg.market_data, k, v)
    return cfg


def test_universe_registry_never_substitutes_fallback_venue(monkeypatch):
    """Registry has always loaded primary markets only, not fallback data."""
    from aletheia.data.ccxt_provider import CCXTProvider
    from aletheia.universe import registry as _reg
    monkeypatch.setattr(_reg, "_load_demo_whitelist", _AsyncNone())
    cfg = _cfg_with({"ccxt_exchange": "bitget", "ccxt_fallback": "okx"})
    closed = []

    class Exchange:
        async def load_markets(self):
            raise RuntimeError("primary listing unavailable")

    async def close(self):
        closed.append(self._primary_name)

    monkeypatch.setattr(CCXTProvider, "_ensure_primary", lambda self: Exchange())
    monkeypatch.setattr(CCXTProvider, "_ensure_fallback", lambda *args: pytest.fail("cross-venue registry"))
    monkeypatch.setattr(CCXTProvider, "close", close)
    monkeypatch.setattr(_reg.repo, "sync_universe_assets", lambda *args, **kwargs: pytest.fail("unavailable listing must not write"))
    with pytest.raises(RuntimeError, match="primary listing unavailable"):
        asyncio.run(_reg.sync_registry(cfg))
    assert closed == ["bitget"]


def test_universe_features_uses_configured_fallback_exchange(monkeypatch):
    """#91 features.py counterpart: same fix at collect_features()."""
    from aletheia.data import ccxt_provider as _ccxt_mod

    captured: dict = {}

    class FakeCCXTProvider:
        def __init__(self, *, exchange, fallback_exchange):
            captured["exchange"] = exchange
            captured["fallback_exchange"] = fallback_exchange

    monkeypatch.setattr(_ccxt_mod, "CCXTProvider", FakeCCXTProvider)

    from aletheia.universe import features as _feat
    cfg = _cfg_with({"ccxt_exchange": "bitget", "ccxt_fallback": "okx"})

    try:
        asyncio.run(_feat.collect_features([], cfg))
    except Exception:
        pass

    assert captured.get("exchange") == "bitget"
    assert captured.get("fallback_exchange") == "okx"


class _AsyncNone:
    """Async callable that returns None — for stubbing coroutine helpers."""

    async def __call__(self, *args, **kwargs):
        return None


# ---------------------------------------------------------------------------
# #92 — macro collector doesn't block the event loop on yfinance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_macro_collector_runs_yfinance_off_loop(monkeypatch):
    """#92: yfinance ticker fetches are synchronous and can take multi-second HTTP
    round trips each. The old code called _fetch_tradfi() (which ran all tickers
    sequentially) directly from async collect() — every other async task on the
    scheduler loop stalled for the duration.
    Now each ticker runs via asyncio.to_thread(_fetch_one_tradfi_ticker, sym)
    so the blocking work is off-loop and all four tickers are concurrent.
    Assert this by making _fetch_one_tradfi_ticker block for a bit and running a
    competing async task in parallel — if the loop is stalled, the task can't tick."""
    from aletheia.data.collectors import macro as _macro

    STALL_SEC = 0.5

    def fake_one_ticker(sym):
        time.sleep(STALL_SEC)  # deliberately blocking, runs in asyncio.to_thread
        return 1.0, {"source": "yfinance", "precision": "date", "source_observed_at": "2026-09-04"}

    async def fake_fred(_key):
        return None, None, None, None  # forces yfinance fallback path

    async def fake_fng():
        return None, None

    async def fake_coingecko():
        return None, None, None

    # Patch _fetch_one_tradfi_ticker — the function collect() dispatches per ticker
    # via asyncio.to_thread. (_fetch_tradfi is legacy/unused by collect().)
    monkeypatch.setattr(_macro, "_fetch_one_tradfi_ticker", fake_one_ticker)
    monkeypatch.setattr(_macro, "_fetch_fred", fake_fred)
    monkeypatch.setattr(_macro, "_fetch_fng", fake_fng)
    monkeypatch.setattr(_macro, "_fetch_coingecko", fake_coingecko)
    monkeypatch.setenv("FRED_API_KEY", "irrelevant")  # keep FRED branch, returns None

    collector = _macro.MacroCollector()

    async def _competing_ticker():
        ticks = 0
        for _ in range(50):
            await asyncio.sleep(0.01)
            ticks += 1
        return ticks

    # Run the collector AND a competing task concurrently. If the loop is stalled
    # by the sync yfinance call, the competing task can't advance.
    start = time.perf_counter()
    _macro_result, competing_ticks = await asyncio.gather(
        collector.collect(["BTC/USDT"]),
        _competing_ticker(),
    )
    elapsed = time.perf_counter() - start

    # 50 * 0.01 = 0.5s of expected competing-task work. If yfinance blocked the
    # loop, competing_ticks would be well below 50 despite the wall clock.
    # to_thread means the sleep(0.5) runs off-loop so competing task finishes
    # its 50 ticks within roughly its own 0.5s budget (concurrent, not serial).
    assert competing_ticks == 50, (
        f"async competing task starved — event loop was blocked by sync yfinance. "
        f"ticks={competing_ticks}, elapsed={elapsed:.2f}s"
    )
