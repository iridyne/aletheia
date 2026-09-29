"""Phase 5: Hyperliquid transport → CCXT publicPostInfo.

Acceptance checks for #3699 Phase 5:
- One CCXT exchange per collect(); publicPostInfo owns transport
- metaAndAssetCtxs provenance preserved (one response per observation)
- predictedFundings uses same exchange, separate call
- exchange/session closes on success and failure
- No raw Hyperliquid URL or httpx_client remains in collector
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_exchange(btc_fr="0.0001", btc_mark="95000", btc_oi="10.5"):
    meta = [
        {"universe": [{"name": "BTC", "szDecimals": 5}]},
        [{"funding": btc_fr, "markPx": btc_mark, "openInterest": btc_oi, "oraclePx": btc_mark}],
    ]
    predicted = [
        ["BTC", [["HlPerp", {"fundingRate": "0.00012", "fundingIntervalHours": 1, "nextFundingTime": 1700010000000}]]],
    ]

    async def _post_info(params):
        t = params.get("type")
        if t == "metaAndAssetCtxs":
            return meta
        if t == "predictedFundings":
            return predicted
        return {}

    exchange = MagicMock()
    exchange.publicPostInfo = AsyncMock(side_effect=_post_info)
    return exchange


async def test_collect_uses_ccxt_publicPostInfo(monkeypatch):
    """collect() calls publicPostInfo for both metaAndAssetCtxs and predictedFundings."""
    import extensions.data.collectors.hyperliquid as mod

    exchange = _make_exchange()
    session = MagicMock()
    session.close = AsyncMock()

    with (
        patch.object(mod, "make_ccxt", return_value=(exchange, session)),
        patch.object(mod, "close_ccxt", new_callable=AsyncMock) as mock_close,
    ):
        from extensions.data.collectors.hyperliquid import HyperliquidCollector
        result = await HyperliquidCollector().collect(symbols=["BTC/USDT"])

    assert exchange.publicPostInfo.call_count == 2
    types_called = {c.args[0]["type"] for c in exchange.publicPostInfo.call_args_list}
    assert types_called == {"metaAndAssetCtxs", "predictedFundings"}
    mock_close.assert_awaited_once_with(exchange, session)
    assert len(result["errors"]) == 0


async def test_collect_closes_exchange_on_step1_error(monkeypatch):
    """Exchange is closed even when metaAndAssetCtxs fails."""
    import extensions.data.collectors.hyperliquid as mod

    exchange = MagicMock()
    exchange.publicPostInfo = AsyncMock(side_effect=RuntimeError("connection refused"))
    session = MagicMock()
    session.close = AsyncMock()

    with (
        patch.object(mod, "make_ccxt", return_value=(exchange, session)),
        patch.object(mod, "close_ccxt", new_callable=AsyncMock) as mock_close,
    ):
        from extensions.data.collectors.hyperliquid import HyperliquidCollector
        result = await HyperliquidCollector().collect()

    mock_close.assert_awaited_once_with(exchange, session)
    assert any("metaAndAssetCtxs" in e for e in result["errors"])


async def test_one_response_per_observation_not_three_calls(monkeypatch):
    """OI, funding, mark price all come from one metaAndAssetCtxs call — not three."""
    import extensions.data.collectors.hyperliquid as mod

    exchange = _make_exchange()
    session = MagicMock()
    session.close = AsyncMock()

    with (
        patch.object(mod, "make_ccxt", return_value=(exchange, session)),
        patch.object(mod, "close_ccxt", new_callable=AsyncMock),
    ):
        from extensions.data.collectors.hyperliquid import HyperliquidCollector
        result = await HyperliquidCollector().collect(symbols=["BTC/USDT"])

    meta_calls = [c for c in exchange.publicPostInfo.call_args_list if c.args[0]["type"] == "metaAndAssetCtxs"]
    assert len(meta_calls) == 1, "exactly one metaAndAssetCtxs request per observation"
    assert len(result["funding"]) >= 1
    assert len(result["open_interest"]) >= 1


async def test_predicted_fundings_uses_same_exchange(monkeypatch):
    """_fetch_predicted_fundings uses the exchange passed from collect()."""
    import extensions.data.collectors.hyperliquid as mod

    exchange = _make_exchange()
    session = MagicMock()
    session.close = AsyncMock()

    with (
        patch.object(mod, "make_ccxt", return_value=(exchange, session)),
        patch.object(mod, "close_ccxt", new_callable=AsyncMock),
    ):
        from extensions.data.collectors.hyperliquid import HyperliquidCollector
        result = await HyperliquidCollector().collect(symbols=["BTC/USDT"])

    predicted = [f for f in result["funding"] if f.exchange == "hyperliquid_predicted"]
    assert len(predicted) >= 1


def test_no_direct_hyperliquid_url_or_httpx():
    """No raw Hyperliquid URL or httpx_client import in production collector."""
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "extensions/data/collectors/hyperliquid.py"
    text = src.read_text()
    tree = ast.parse(text)

    import_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_names.append(node.module)

    assert "httpx_client" not in " ".join(import_names)

    # No hard-coded Hyperliquid endpoint URL (docstring URL is allowed)
    code_lines = [l for l in text.split("\n") if not l.strip().startswith("#") and "hyperliquid.xyz" in l]
    assert all("POST" in l or "api.hyperliquid.xyz" not in l for l in code_lines), \
        "Found non-comment Hyperliquid URL in code"
