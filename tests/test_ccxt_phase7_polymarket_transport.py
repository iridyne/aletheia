"""Phase 7: Polymarket transport → CCXT Prediction (gammaPublicGetMarkets / clobPublicGetPricesHistory).

Acceptance checks for #3699 Phase 7:
- make_ccxt_prediction("polymarket") used for Gamma listing
- gammaPublicGetMarkets owns active-market fetch
- clobPublicGetPricesHistory owns price-history fetch
- Exchange closed after each call
- PolymarketMarket semantic parity: yes_probability as 0..1, volume, category filter, fallback
- No raw Gamma/CLOB URL or httpx_client in polymarket_provider.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _gamma_market(question="Will BTC hit 200k?", yes_price="0.62", volume="50000",
                  change="0.05", end="2026-12-31", mid="123", slug="btc-200k",
                  category="crypto", liquidity="10000"):
    return {
        "question": question,
        "outcomePrices": f'["{yes_price}","0.38"]',
        "volume24hr": volume,
        "oneDayPriceChange": change,
        "endDate": end,
        "id": mid,
        "slug": slug,
        "category": category,
        "liquidity": liquidity,
        "enable_order_book": True,
        "active": True,
        "closed": False,
    }


def _make_exchange(markets=None, history=None):
    """Mock ccxt.prediction.polymarket exchange."""
    if markets is None:
        markets = [_gamma_market()]
    if history is None:
        history = {"history": [{"t": "1700000000", "p": 0.62}]}

    exchange = MagicMock()
    exchange.gammaPublicGetMarkets = AsyncMock(return_value=markets)
    exchange.clobPublicGetPricesHistory = AsyncMock(return_value=history)
    return exchange


async def test_fetch_active_markets_uses_gamma_endpoint():
    """fetch_active_markets calls gammaPublicGetMarkets, not httpx."""
    import extensions.data.polymarket_provider as mod

    exchange = _make_exchange()
    session = MagicMock()

    with (
        patch.object(mod, "make_ccxt_prediction", return_value=(exchange, session)) as mock_make,
        patch.object(mod, "close_ccxt", new_callable=AsyncMock) as mock_close,
    ):
        result = await mod.fetch_active_markets(categories=["crypto"], limit=5)

    mock_make.assert_called_once_with("polymarket")
    mock_close.assert_awaited_once_with(exchange, session)
    exchange.gammaPublicGetMarkets.assert_awaited_once()
    assert len(result) == 1
    assert result[0].question == "Will BTC hit 200k?"
    assert result[0].yes_probability == pytest.approx(0.62)
    assert result[0].volume_usd == pytest.approx(50000.0)


async def test_yes_probability_is_fraction_not_percent():
    """yes_probability is 0..1, not percentage."""
    import extensions.data.polymarket_provider as mod

    exchange = _make_exchange(markets=[_gamma_market(yes_price="0.75")])
    with (
        patch.object(mod, "make_ccxt_prediction", return_value=(exchange, MagicMock())),
        patch.object(mod, "close_ccxt", new_callable=AsyncMock),
    ):
        result = await mod.fetch_active_markets(categories=["crypto"])

    assert result[0].yes_probability == pytest.approx(0.75)
    assert result[0].yes_probability < 1.0


async def test_category_filter_and_top_volume_fallback():
    """Category filter works; when no match, top-volume fallback returns all markets."""
    import extensions.data.polymarket_provider as mod

    markets = [
        _gamma_market(question="Crypto Q", category="crypto", volume="90000", mid="1"),
        _gamma_market(question="Politics Q", category="politics", volume="50000", mid="2"),
    ]
    exchange = _make_exchange(markets=markets)

    # Match only crypto
    with (
        patch.object(mod, "make_ccxt_prediction", return_value=(exchange, MagicMock())),
        patch.object(mod, "close_ccxt", new_callable=AsyncMock),
    ):
        matched = await mod.fetch_active_markets(categories=["crypto"])
    assert len(matched) == 1
    assert matched[0].question == "Crypto Q"

    # No match → top-volume fallback returns both, sorted by volume desc
    with (
        patch.object(mod, "make_ccxt_prediction", return_value=(exchange, MagicMock())),
        patch.object(mod, "close_ccxt", new_callable=AsyncMock),
    ):
        fallback = await mod.fetch_active_markets(categories=["unicorn"])
    assert len(fallback) == 2
    assert fallback[0].volume_usd > fallback[1].volume_usd


async def test_fetch_market_history_uses_clob_endpoint():
    """fetch_market_history uses clobPublicGetPricesHistory."""
    import extensions.data.polymarket_provider as mod

    history_payload = {"history": [
        {"t": "1700000000", "p": 0.60},
        {"t": "1700003600", "p": 0.65},
    ]}
    exchange = _make_exchange(history=history_payload)
    session = MagicMock()

    with (
        patch.object(mod, "make_ccxt_prediction", return_value=(exchange, session)) as mock_make,
        patch.object(mod, "close_ccxt", new_callable=AsyncMock) as mock_close,
    ):
        result = await mod.fetch_market_history("token-xyz")

    mock_make.assert_called_once_with("polymarket")
    mock_close.assert_awaited_once_with(exchange, session)
    call_params = exchange.clobPublicGetPricesHistory.call_args.args[0]
    assert call_params["market"] == "token-xyz"
    assert call_params["interval"] == "all"
    assert result == [
        {"ts": "1700000000", "yes_probability": 0.60},
        {"ts": "1700003600", "yes_probability": 0.65},
    ]


def test_no_direct_urls_or_httpx_in_polymarket_provider():
    """No raw Gamma/CLOB URL or httpx_client import in polymarket_provider.py."""
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "extensions/data/polymarket_provider.py"
    text = src.read_text()
    tree = ast.parse(text)

    import_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_names.append(node.module)

    assert "httpx_client" not in " ".join(import_names)
    assert "gamma-api.polymarket.com" not in text
    assert "clob.polymarket.com" not in text
    assert "_GAMMA_URL" not in text
