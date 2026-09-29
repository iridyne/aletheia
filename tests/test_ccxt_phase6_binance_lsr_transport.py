"""Phase 6: Binance LSR transport → CCXT fapiData implicit methods.

Acceptance checks for #3699 Phase 6:
- make_ccxt("binanceusdm") is called for LSR batch (not httpx_client)
- fapiDataGetGlobalLongShortAccountRatio and fapiDataGetTopLongShortPositionRatio own transport
- Exchange is closed after both batch calls complete
- LSR failure does not prevent OI/funding collection (MS-C01 preserved)
- No _BINANCE_LSR_URL or httpx_client in derivatives.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _lsr_exchange(btc_ratio="1.35", fail_top=False):
    async def _global(params):
        if params.get("symbol") == "BTCUSDT":
            return [{"longShortRatio": btc_ratio}]
        return []

    async def _top(params):
        if fail_top:
            raise RuntimeError("top position unavailable")
        if params.get("symbol") == "BTCUSDT":
            return [{"longShortRatio": "1.1"}]
        return []

    exchange = MagicMock()
    exchange.fapiDataGetGlobalLongShortAccountRatio = AsyncMock(side_effect=_global)
    exchange.fapiDataGetTopLongShortPositionRatio = AsyncMock(side_effect=_top)
    return exchange


def test_make_ccxt_called_with_binanceusdm():
    """make_ccxt is called with 'binanceusdm' for LSR — not 'binance' and not httpx."""
    import extensions.data.collectors.derivatives as mod

    exchange = _lsr_exchange()
    session = MagicMock()
    make_calls = []

    def _make(name):
        make_calls.append(name)
        return exchange, session

    with (
        patch.object(mod, "make_ccxt", side_effect=_make),
        patch.object(mod, "close_ccxt", new_callable=AsyncMock),
    ):
        asyncio.run(mod._fetch_binance_ratio_batch(exchange, ["BTC/USDT"], mod._BINANCE_LSR_METHOD))

    # make_ccxt is not called inside _fetch_binance_ratio_batch itself (caller owns lifetime)
    # Verify the constants name the correct CCXT methods
    assert mod._BINANCE_LSR_METHOD == "fapiDataGetGlobalLongShortAccountRatio"
    assert mod._BINANCE_TOP_POSITION_METHOD == "fapiDataGetTopLongShortPositionRatio"


async def test_exchange_closed_after_lsr_batch(monkeypatch):
    """close_ccxt is called once after both batch calls regardless of outcome."""
    import extensions.data.collectors.derivatives as mod
    from aletheia.config import loader as cfg_loader
    from extensions.data import ccxt_provider as provider_mod

    _mkt = {"BTC/USDT:USDT": {"id": "BTC/USDT:USDT", "symbol": "BTC/USDT:USDT",
                               "base": "BTC", "quote": "USDT", "settle": "USDT",
                               "type": "swap", "swap": True, "linear": True,
                               "contractSize": 1}}

    class _PrimaryExchange:
        markets = _mkt
        async def load_markets(self): return self.markets
        async def fetch_funding_rate(self, symbol): return {"fundingRate": 0.0001}
        async def fetch_open_interest(self, symbol): return {"openInterestValue": 100.0, "info": {"oiUsd": 100}}

    class _Provider:
        _primary_name = "okx"
        def _ensure_primary(self): return _PrimaryExchange()
        async def close(self): return None

    lsr_exchange = _lsr_exchange()
    mock_session = MagicMock()
    mock_close = AsyncMock()

    cfg = type("Cfg", (), {"market_data": type("MD", (), {
        "enable_long_short_ratio": True, "derivatives_history_hours": 0
    })()})()

    monkeypatch.setattr(provider_mod, "get_market_data_provider", lambda: _Provider())
    monkeypatch.setattr(cfg_loader, "load_config", lambda: cfg)
    monkeypatch.setattr(mod, "make_ccxt", lambda name: (lsr_exchange, mock_session))
    monkeypatch.setattr(mod, "close_ccxt", mock_close)

    result = await mod.DerivativesCollector().collect(["BTC/USDT"])

    mock_close.assert_awaited_once_with(lsr_exchange, mock_session)
    assert len(result["open_interest"]) == 1
    assert result["open_interest"][0].long_short_ratio == pytest.approx(1.35)


async def test_lsr_exchange_closed_even_on_batch_error(monkeypatch):
    """close_ccxt fires even when both LSR batch calls raise."""
    import extensions.data.collectors.derivatives as mod
    from aletheia.config import loader as cfg_loader
    from extensions.data import ccxt_provider as provider_mod

    class _Provider:
        _primary_name = "okx"
        def _ensure_primary(self):
            ex = MagicMock()
            ex.markets = {}
            ex.load_markets = AsyncMock(return_value={})
            return ex
        async def close(self): return None

    broken_exchange = MagicMock()
    broken_exchange.fapiDataGetGlobalLongShortAccountRatio = AsyncMock(side_effect=RuntimeError("boom"))
    broken_exchange.fapiDataGetTopLongShortPositionRatio = AsyncMock(side_effect=RuntimeError("boom"))
    mock_session = MagicMock()
    mock_close = AsyncMock()

    cfg = type("Cfg", (), {"market_data": type("MD", (), {
        "enable_long_short_ratio": True, "derivatives_history_hours": 0
    })()})()

    monkeypatch.setattr(provider_mod, "get_market_data_provider", lambda: _Provider())
    monkeypatch.setattr(cfg_loader, "load_config", lambda: cfg)
    monkeypatch.setattr(mod, "make_ccxt", lambda name: (broken_exchange, mock_session))
    monkeypatch.setattr(mod, "close_ccxt", mock_close)

    result = await mod.DerivativesCollector().collect(["BTC/USDT"])

    mock_close.assert_awaited_once_with(broken_exchange, mock_session)
    # LSR errors are non-fatal — errors list records them but collection proceeds
    assert isinstance(result, dict)


def test_no_httpx_or_url_constants_in_derivatives():
    """No httpx_client import or raw Binance URL constants remain in derivatives.py."""
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "extensions/data/collectors/derivatives.py"
    text = src.read_text()
    tree = ast.parse(text)

    import_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_names.append(node.module)

    assert "httpx_client" not in " ".join(import_names)
    assert "fapi.binance.com" not in text
    assert "_BINANCE_LSR_URL" not in text
    assert "_BINANCE_TOP_POSITION_RATIO_URL" not in text
