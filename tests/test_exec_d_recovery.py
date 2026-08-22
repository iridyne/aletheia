"""Tests for exec-d recovery path fixes.

#1779 — Bitget: recover orderId via clientOid detail lookup
#1783 — Binance: resolve missing orderId via origClientOrderId lookup
#1785 — Binance: fix userTrades window violations (>7-day, fromId+endTime)
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _binance_fill_row(order_id="ORD-1", trade_id="TRADE-1", client_oid="coid-abc"):
    """Minimal CCXT-normalized Binance fill row."""
    return {
        "id": trade_id,
        "order": order_id,
        "symbol": "BTC/USDT:USDT",
        "price": 50000.0,
        "amount": 0.01,
        "timestamp": 1700000000000,
        "fee": {"cost": 0.3, "currency": "USDT"},
        "info": {
            "orderId": order_id,
            "clientOrderId": client_oid,
            "price": "50000",
            "qty": "0.01",
            "time": "1700000000000",
            "commission": "0.3",
            "commissionAsset": "USDT",
        },
    }


def _make_binance_client_cls(lookup_result=None, fills=None):
    """Return a credential-free BinanceClient drop-in for binance_broker patching."""
    _fills = fills if fills is not None else []
    _lookup = lookup_result

    class _FakeBinanceClient:
        def __init__(self, mode="testnet"):
            self._mode = mode

        async def get_order_by_client_order_id(self, coid, symbol):  # noqa: ARG002
            return _lookup

        def _ensure_exchange(self):
            mock_ex = MagicMock()
            mock_ex.load_markets = AsyncMock()
            mock_ex.fetch_my_trades = AsyncMock(return_value=_fills)
            return mock_ex

        async def close(self):
            pass

    return _FakeBinanceClient


def _make_binance_exchange_cls(mock_exchange):
    """Return a BinanceClient whose _ensure_exchange returns mock_exchange."""

    class _FakeBinanceClient:
        def __init__(self, mode="testnet"):
            self._mode = mode

        def _ensure_exchange(self):
            return mock_exchange

        async def close(self):
            pass

    return _FakeBinanceClient


def _make_bitget_client():
    """Build a BitgetClient with get_secret patched out."""
    from aletheia.exchanges.bitget.client import BitgetClient
    with patch("aletheia.exchanges.bitget.client.get_secret", return_value="dummy"):
        return BitgetClient(mode="demo")


# ─────────────────────────────────────────────────────────────────────────────
# #1779 — Bitget clientOid → orderId recovery
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bitget_get_order_detail_by_client_oid_returns_order():
    """get_order_detail_by_client_oid returns orderId dict on success."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "code": "00000",
        "data": {"orderId": "RECOVERED-99", "clientOid": "my-client-oid", "status": "filled"},
    }
    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_response)

    client = _make_bitget_client()
    with (
        patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_http),
        patch.object(client, "_get_creds", return_value=("k", "s", "p")),
    ):
        result = await client.get_order_detail_by_client_oid("my-client-oid", "BTCUSDT")

    assert result is not None
    assert result["orderId"] == "RECOVERED-99"
    assert result["clientOid"] == "my-client-oid"


@pytest.mark.asyncio
async def test_bitget_get_order_detail_by_client_oid_returns_none_on_not_found():
    """get_order_detail_by_client_oid returns None when exchange reports not-found."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"code": "40009", "msg": "order not found"}
    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_response)

    client = _make_bitget_client()
    with (
        patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_http),
        patch.object(client, "_get_creds", return_value=("k", "s", "p")),
    ):
        result = await client.get_order_detail_by_client_oid("missing-oid", "BTCUSDT")

    assert result is None


@pytest.mark.asyncio
async def test_bitget_recovers_order_id_from_client_oid():
    """get_order_detail_by_client_oid exists on BitgetClient and returns orderId."""
    from aletheia.exchanges.bitget.client import BitgetClient

    detail_result = {
        "orderId": "RECOVERED-FROM-DETAIL",
        "clientOid": "proposal-coid-1",
        "status": "filled",
    }

    async def _fake_detail(self, client_oid, symbol):  # noqa: ARG001
        return detail_result if client_oid == "proposal-coid-1" else None

    client = _make_bitget_client()
    with patch.object(BitgetClient, "get_order_detail_by_client_oid", _fake_detail):
        result = await client.get_order_detail_by_client_oid("proposal-coid-1", "BTCUSDT")

    assert result is not None
    assert result["orderId"] == "RECOVERED-FROM-DETAIL"


# ─────────────────────────────────────────────────────────────────────────────
# #1783 — Binance origClientOrderId → orderId recovery
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_binance_recovers_order_id_from_client_order_id():
    """fetch_private_fill_evidence recovers exchange_order_id via client_order_id lookup."""
    from aletheia.execution import binance_broker
    from aletheia.execution.binance_broker import fetch_private_fill_evidence

    recovered_order_id = "BINANCE-ORD-999"
    client_oid = "client-abc-123"
    fill = _binance_fill_row(order_id=recovered_order_id, client_oid=client_oid)

    FakeCls = _make_binance_client_cls(
        lookup_result={"orderId": recovered_order_id, "clientOrderId": client_oid, "status": "filled"},
        fills=[fill],
    )
    order = {
        "execution_mode": "binance_testnet",
        "market_type": "usdt_futures",
        "symbol": "BTC/USDT",
        "exchange_order_id": "",
        "client_order_id": client_oid,
        "created_at": "2024-01-01T00:00:00+00:00",
    }

    with patch.object(binance_broker, "BinanceClient", FakeCls):
        result = await fetch_private_fill_evidence(order)

    assert isinstance(result, list)
    assert any(r["orderId"] == recovered_order_id for r in result)


@pytest.mark.asyncio
async def test_binance_returns_empty_when_client_order_id_not_found():
    """fetch_private_fill_evidence returns [] when orderId recovery fails (fail-closed)."""
    from aletheia.execution import binance_broker
    from aletheia.execution.binance_broker import fetch_private_fill_evidence

    FakeCls = _make_binance_client_cls(lookup_result=None)
    order = {
        "execution_mode": "binance_testnet",
        "market_type": "usdt_futures",
        "symbol": "BTC/USDT",
        "exchange_order_id": "",
        "client_order_id": "no-such-coid",
        "created_at": "2024-01-01T00:00:00+00:00",
    }

    with patch.object(binance_broker, "BinanceClient", FakeCls):
        result = await fetch_private_fill_evidence(order)

    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# #1785 — Binance window partitioning and pagination rules
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_binance_window_under_7_days_single_request():
    """Recent order (<7 days): single fetch_my_trades call with startTime+endTime, no fromId."""
    from aletheia.execution import binance_broker
    from aletheia.execution.binance_broker import fetch_private_fill_evidence

    calls: list[dict] = []
    order_id = "ORD-RECENT"
    fill = _binance_fill_row(order_id=order_id)

    async def _mock_trades(symbol, limit=1000, params=None):  # noqa: ARG001
        calls.append(dict(params or {}))
        return [fill]

    mock_exchange = MagicMock()
    mock_exchange.load_markets = AsyncMock()
    mock_exchange.fetch_my_trades = _mock_trades

    FakeCls = _make_binance_exchange_cls(mock_exchange)
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    order = {
        "execution_mode": "binance_testnet",
        "market_type": "usdt_futures",
        "symbol": "BTC/USDT",
        "exchange_order_id": order_id,
        "client_order_id": "",
        "created_at": recent_ts,
    }

    with patch.object(binance_broker, "BinanceClient", FakeCls):
        await fetch_private_fill_evidence(order)

    assert len(calls) == 1, f"Expected 1 call for recent order, got {len(calls)}"
    assert "startTime" in calls[0], "First call must have startTime"
    assert "endTime" in calls[0], "First call must have endTime"
    assert "fromId" not in calls[0], "No fromId on first call"


@pytest.mark.asyncio
async def test_binance_window_partitioning_no_endtime_with_from_id():
    """After a full 1000-row page, pagination uses fromId only — never fromId+endTime."""
    from aletheia.execution import binance_broker
    from aletheia.execution.binance_broker import fetch_private_fill_evidence

    calls: list[dict] = []
    order_id = "ORD-PAGINATE"

    def _make_page(start_id: int, count: int) -> list[dict]:
        rows = []
        for i in range(count):
            r = _binance_fill_row(order_id=order_id, trade_id=str(start_id + i))
            r["id"] = str(start_id + i)
            r["info"]["orderId"] = order_id
            rows.append(r)
        return rows

    page1 = _make_page(1, 1000)
    page2 = _make_page(1001, 1)
    call_count = 0

    async def _mock_trades(symbol, limit=1000, params=None):  # noqa: ARG001
        nonlocal call_count
        calls.append(dict(params or {}))
        call_count += 1
        return page1 if call_count == 1 else page2

    mock_exchange = MagicMock()
    mock_exchange.load_markets = AsyncMock()
    mock_exchange.fetch_my_trades = _mock_trades

    FakeCls = _make_binance_exchange_cls(mock_exchange)
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    order = {
        "execution_mode": "binance_testnet",
        "market_type": "usdt_futures",
        "symbol": "BTC/USDT",
        "exchange_order_id": order_id,
        "client_order_id": "",
        "created_at": recent_ts,
    }

    with patch.object(binance_broker, "BinanceClient", FakeCls):
        await fetch_private_fill_evidence(order)

    assert len(calls) >= 2, "Expected at least 2 pages"
    # First call: startTime + endTime, no fromId
    assert "startTime" in calls[0]
    assert "endTime" in calls[0]
    assert "fromId" not in calls[0], "First call must not have fromId"
    # Pagination call: fromId only — endTime and startTime absent
    assert "fromId" in calls[1], "Pagination call must have fromId"
    assert "endTime" not in calls[1], "Pagination call must NOT have endTime"
    assert "startTime" not in calls[1], "Pagination call must NOT have startTime"


@pytest.mark.asyncio
async def test_binance_window_over_7_days_splits_into_chunks():
    """Order older than 7 days results in multiple fetch_my_trades window calls,
    each with startTime+endTime ≤7 days and no fromId."""
    from aletheia.execution import binance_broker
    from aletheia.execution.binance_broker import fetch_private_fill_evidence

    calls: list[dict] = []

    async def _mock_trades(symbol, limit=1000, params=None):  # noqa: ARG001
        calls.append(dict(params or {}))
        return []

    mock_exchange = MagicMock()
    mock_exchange.load_markets = AsyncMock()
    mock_exchange.fetch_my_trades = _mock_trades

    FakeCls = _make_binance_exchange_cls(mock_exchange)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    order = {
        "execution_mode": "binance_testnet",
        "market_type": "usdt_futures",
        "symbol": "BTC/USDT",
        "exchange_order_id": "ORD-OLD",
        "client_order_id": "",
        "created_at": old_ts,
    }

    with patch.object(binance_broker, "BinanceClient", FakeCls):
        await fetch_private_fill_evidence(order)

    assert len(calls) >= 3, f"20-day window should produce >=3 calls, got {len(calls)}"
    max_ms = 7 * 24 * 60 * 60 * 1000
    for i, p in enumerate(calls):
        assert "startTime" in p, f"Call {i} missing startTime"
        assert "endTime" in p, f"Call {i} missing endTime"
        assert "fromId" not in p, f"Call {i} must not have fromId"
        window_ms = p["endTime"] - p["startTime"]
        assert window_ms <= max_ms, f"Call {i} window {window_ms}ms exceeds 7 days"
