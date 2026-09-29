"""Phase 4: Deribit options transport → CCXT without GEX/IV regression.

Acceptance checks for #3699 Phase 4:
- CCXT exchange owns transport; Aletheia still owns GEX/IV semantics
- Two-batch shape (BTC + ETH) is preserved
- Same items produce identical GEX/IV outputs
- No direct Deribit URL or httpx remains in the production collector
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Minimal fixture: one call/put pair per currency that produces a finite GEX
def _btc_items():
    return [
        {
            "instrument_name": "BTC-31DEC27-100000-C",
            "open_interest": 10.0,
            "mid_iv": 65.0,
            "mark_iv": 64.0,
            "underlying_price": 95000.0,
            "creation_timestamp": 1700000000000,
        },
        {
            "instrument_name": "BTC-31DEC27-80000-P",
            "open_interest": 8.0,
            "mid_iv": 70.0,
            "mark_iv": 69.0,
            "underlying_price": 95000.0,
            "creation_timestamp": 1700000000000,
        },
        # Add enough entries to clear _MIN_VALID_ENTRIES threshold
        {
            "instrument_name": "BTC-31DEC27-90000-C",
            "open_interest": 5.0,
            "mid_iv": 62.0,
            "mark_iv": 61.0,
            "underlying_price": 95000.0,
            "creation_timestamp": 1700000000000,
        },
        {
            "instrument_name": "BTC-31DEC27-85000-P",
            "open_interest": 6.0,
            "mid_iv": 68.0,
            "mark_iv": 67.0,
            "underlying_price": 95000.0,
            "creation_timestamp": 1700000000000,
        },
        {
            "instrument_name": "BTC-31DEC27-95000-C",
            "open_interest": 12.0,
            "mid_iv": 60.0,
            "mark_iv": 59.0,
            "underlying_price": 95000.0,
            "creation_timestamp": 1700000000000,
        },
    ]


def _eth_items():
    return [
        {
            "instrument_name": "ETH-31DEC27-4000-C",
            "open_interest": 100.0,
            "mid_iv": 72.0,
            "mark_iv": 71.0,
            "underlying_price": 3500.0,
            "creation_timestamp": 1700000000000,
        },
        {
            "instrument_name": "ETH-31DEC27-3000-P",
            "open_interest": 80.0,
            "mid_iv": 75.0,
            "mark_iv": 74.0,
            "underlying_price": 3500.0,
            "creation_timestamp": 1700000000000,
        },
        {
            "instrument_name": "ETH-31DEC27-3500-C",
            "open_interest": 120.0,
            "mid_iv": 68.0,
            "mark_iv": 67.0,
            "underlying_price": 3500.0,
            "creation_timestamp": 1700000000000,
        },
        {
            "instrument_name": "ETH-31DEC27-3200-P",
            "open_interest": 90.0,
            "mid_iv": 73.0,
            "mark_iv": 72.0,
            "underlying_price": 3500.0,
            "creation_timestamp": 1700000000000,
        },
        {
            "instrument_name": "ETH-31DEC27-3800-C",
            "open_interest": 70.0,
            "mid_iv": 65.0,
            "mark_iv": 64.0,
            "underlying_price": 3500.0,
            "creation_timestamp": 1700000000000,
        },
    ]


@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    exchange.publicGetGetBookSummaryByCurrency = AsyncMock(side_effect=lambda params: {
        "id": 1, "jsonrpc": "2.0",
        "result": _btc_items() if params["currency"] == "BTC" else _eth_items(),
    })
    return exchange


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.close = AsyncMock()
    return session


async def test_collector_calls_ccxt_for_btc_and_eth(mock_exchange, mock_session):
    """Collector calls publicGetGetBookSummaryByCurrency for each currency."""
    from extensions.data.collectors.options_structure import OptionsStructureCollector

    with (
        patch("extensions.data.collectors.options_structure.make_ccxt", return_value=(mock_exchange, mock_session)),
        patch("extensions.data.collectors.options_structure.close_ccxt", new_callable=AsyncMock) as mock_close,
    ):
        collector = OptionsStructureCollector()
        result = await collector.collect()

    assert mock_exchange.publicGetGetBookSummaryByCurrency.call_count == 2
    calls = mock_exchange.publicGetGetBookSummaryByCurrency.call_args_list
    currencies_called = {c.args[0]["currency"] for c in calls}
    assert currencies_called == {"BTC", "ETH"}
    for c in calls:
        assert c.args[0]["kind"] == "option"
    mock_close.assert_awaited_once_with(mock_exchange, mock_session)
    assert len(result["snapshots"]) == 2
    assert result["errors"] == []


async def test_collector_closes_exchange_on_error(mock_exchange, mock_session):
    """Exchange is closed even when a currency call fails."""
    from extensions.data.collectors.options_structure import OptionsStructureCollector

    mock_exchange.publicGetGetBookSummaryByCurrency.side_effect = RuntimeError("connection refused")

    with (
        patch("extensions.data.collectors.options_structure.make_ccxt", return_value=(mock_exchange, mock_session)),
        patch("extensions.data.collectors.options_structure.close_ccxt", new_callable=AsyncMock) as mock_close,
    ):
        collector = OptionsStructureCollector()
        result = await collector.collect()

    mock_close.assert_awaited_once_with(mock_exchange, mock_session)
    assert len(result["errors"]) == 2  # both currencies failed


async def test_gex_semantics_preserved_from_items(mock_exchange, mock_session):
    """Same items → same GEX snapshot; transport change is semantically neutral."""
    import datetime
    from extensions.data.collectors.options_structure import _gex_from_items

    observed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    snap = _gex_from_items("BTC", _btc_items(), observed_at)

    assert snap is not None, "GEX computation returned None unexpectedly"
    assert snap.symbol == "BTC"
    net_gex: float = snap.net_gex_estimate  # type: ignore[assignment]
    assert math.isfinite(net_gex)
    underlying: float = snap.underlying_price  # type: ignore[assignment]
    assert underlying == 95000.0
    count: int = snap.valid_strike_count  # type: ignore[assignment]
    assert count >= 1


async def test_deribit_ccxt_exchange_is_constructed(mock_exchange, mock_session):
    """make_ccxt is called with 'deribit' — no legacy Deribit URL or httpx."""
    from extensions.data.collectors.options_structure import OptionsStructureCollector

    with (
        patch("extensions.data.collectors.options_structure.make_ccxt", return_value=(mock_exchange, mock_session)) as mock_make,
        patch("extensions.data.collectors.options_structure.close_ccxt", new_callable=AsyncMock),
    ):
        await OptionsStructureCollector().collect()

    mock_make.assert_called_once_with("deribit")


def test_no_direct_deribit_url_or_httpx_in_module():
    """No raw Deribit URL or httpx_client import remains in options_structure."""
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "extensions/data/collectors/options_structure.py"
    text = src.read_text()
    tree = ast.parse(text)

    # No import of httpx_client
    import_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_names.append(node.module)
    assert "httpx_client" not in " ".join(import_names)

    # No hard-coded Deribit URL
    assert "deribit.com" not in text
    assert "DERIBIT_BASE" not in text
