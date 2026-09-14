"""Public listing reads do not import a concrete adapter from Core."""

from pathlib import Path
import subprocess
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest


def test_core_listing_absence_does_not_import_adapter():
    script = '''
import asyncio
import sys
attempted = []
class MissingAdapter:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.data.ccxt_provider":
            attempted.append(fullname)
            raise ModuleNotFoundError("absent adapter", name=fullname)
sys.meta_path.insert(0, MissingAdapter())
from aletheia.trading.market_data import _listed_symbols_on_exchange
from aletheia.data.venue_listing_cache import get_venue_listing
async def check():
    assert await _listed_symbols_on_exchange("bitget") is None
    assert await get_venue_listing("bitget") is None
asyncio.run(check())
assert not attempted, attempted
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("spot_only", [False, True])
async def test_adapter_preserves_listing_filters_and_closes(monkeypatch, spot_only):
    from aletheia.data import ccxt_provider

    exchange = SimpleNamespace(options={}, load_markets=AsyncMock(return_value={
        "BTC/USDT": {"spot": True, "active": True},
        "ETH/USDT": {"spot": True, "active": None},
        "SOL/USDT": {"spot": True, "active": False},
        "AAPLON/USDT": {"active": True},
        "ZERO/USDT": {"spot": True, "active": 0},
        "BAD/USDT": None,
    }))
    provider = SimpleNamespace(_ensure_primary=lambda: exchange, close=AsyncMock())
    factory = Mock(return_value=provider)
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", factory)
    listed = await ccxt_provider.read_market_listing("bitget", spot_only)
    assert listed == frozenset({"BTC/USDT", "ETH/USDT", "ZERO/USDT" if spot_only else "AAPLON/USDT"})
    assert exchange.options == ({"defaultType": "spot"} if spot_only else {})
    factory.assert_called_once_with(exchange="bitget", native_ccxt=True, fallback_exchange="bitget")
    provider.close.assert_awaited_once()


@pytest.mark.parametrize("markets", [None, {}, {"BTC/USDT": {"active": False}}, RuntimeError("offline")])
async def test_adapter_failure_closes(monkeypatch, markets):
    from aletheia.data import ccxt_provider

    load = AsyncMock(side_effect=markets) if isinstance(markets, Exception) else AsyncMock(return_value=markets)
    exchange = SimpleNamespace(options={}, load_markets=load)
    provider = SimpleNamespace(_ensure_primary=lambda: exchange, close=AsyncMock())
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", lambda **kwargs: provider)
    with pytest.raises((ValueError, RuntimeError)):
        await ccxt_provider.read_market_listing("bitget", False)
    provider.close.assert_awaited_once()


@pytest.mark.parametrize("listed", [None, frozenset(), frozenset({""}), frozenset({1}), {"BTC/USDT"}])
async def test_invalid_bound_result_remains_unknown(monkeypatch, listed):
    from aletheia.data import providers, venue_listing_cache
    from aletheia.trading.market_data import _listed_symbols_on_exchange

    reader = AsyncMock(return_value=listed)
    monkeypatch.setattr(providers, "_listing_reader", reader)
    venue_listing_cache.invalidate_all()
    assert await _listed_symbols_on_exchange("bitget") is None
    assert await venue_listing_cache.get_venue_listing("bitget") is None
    assert venue_listing_cache.get_cached_listing("bitget") is None
    assert reader.await_args_list == [call("bitget", True), call("bitget", False)]


async def test_cache_deadline_does_not_drain_or_cache_late_result(monkeypatch):
    from aletheia.data import providers, venue_listing_cache

    cancelled, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def reader(venue, spot_only):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return frozenset({"BTC/USDT"})
        finally:
            closed.set()

    monkeypatch.setattr(providers, "_listing_reader", reader)
    monkeypatch.setattr(venue_listing_cache, "_LISTING_LOAD_TIMEOUT_SEC", 0.01)
    venue_listing_cache.invalidate_all()
    try:
        assert await asyncio.wait_for(venue_listing_cache.get_venue_listing("bitget"), 0.2) is None
        await asyncio.wait_for(cancelled.wait(), 0.2)
        assert not closed.is_set()
    finally:
        release.set()
        await asyncio.wait_for(closed.wait(), 1)
    assert venue_listing_cache.get_cached_listing("bitget") is None


@pytest.mark.parametrize("entrypoint", ["cli", "web", "collector"])
@pytest.mark.parametrize("missing", [None, "aletheia.data.ccxt_provider", "broken_dependency"])
def test_listing_composition(entrypoint, missing):
    script = f"entrypoint = {entrypoint!r}\nmissing = {missing!r}\n" + '''
import asyncio
import importlib
import socket
import sys
from aletheia.data import providers
from aletheia.config import secrets, loader
import aletheia.logging_config
def forbidden(*args, **kwargs):
    raise AssertionError("composition must not query clients, credentials, or old bindings")
secrets.get_secret = forbidden
socket.socket.connect = forbidden
providers.configure_listing_reader(forbidden)
attempted = []
if missing:
    class MissingAdapter:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "aletheia.data.ccxt_provider":
                attempted.append(fullname)
                raise ModuleNotFoundError("isolated missing dependency", name=missing)
    sys.meta_path.insert(0, MissingAdapter())
else:
    from aletheia.data import ccxt_provider
    ccxt_provider.get_market_data_provider = forbidden
class StopBeforeRuntime(Exception):
    pass
def stop_before_runtime():
    raise StopBeforeRuntime
aletheia.logging_config.configure_logging = lambda: None
loader.bootstrap = stop_before_runtime
try:
    if entrypoint == "cli":
        from aletheia import main
        main._configure_runtime()
    elif entrypoint == "web":
        importlib.import_module("aletheia.server.app")
    else:
        from aletheia.data import collector_runner
        try:
            collector_runner.main()
        except StopBeforeRuntime:
            pass
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing, exc
else:
    assert missing != "broken_dependency", "transitive errors must propagate"
    if missing:
        assert providers._listing_reader is None
    else:
        assert providers._listing_reader is ccxt_provider.read_market_listing
if missing:
    assert attempted
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
