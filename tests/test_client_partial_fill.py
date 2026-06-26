"""Unit tests for OKX/Binance CCXT client partial-fill recording.

Regression lock for the sibling of the EC-stop bug: the two CCXT clients
(okx/binance) mapped a CCXT partial fill (unified status "open" with a non-zero
`filled`) to "submitted" and extracted fill price/qty ONLY on a full fill. The
broker then treated a real partial position as unfilled and never recorded it —
a silent exchange↔local desync. Bitget's client already emitted
"partially_filled"; these tests lock the OKX/Binance clients to the same contract:

- partial fill            -> status "partially_filled" + real fill_price/qty
- full fill               -> status "filled" + fill_price/qty
- accepted-but-unfilled   -> status "submitted" + NO fabricated fill_price/qty

The exchange round-trip is mocked at the CCXT boundary; no network.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# asyncio.sleep is patched to a no-op so the 0.3s confirm-poll doesn't slow tests.
@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("asyncio.sleep", new_callable=AsyncMock):
        yield


# ---------------------------------------------------------------------------
# OKX client
# ---------------------------------------------------------------------------


def _okx_client():
    from aletheia.exchanges.okx.client import OKXClient
    with patch("aletheia.exchanges.okx.client.get_secret", return_value="x"):
        return OKXClient(mode="demo")


def _okx_order(order_type="market"):
    from aletheia.exchanges.okx.client import OrderRequest
    return OrderRequest(
        symbol="BTC/USDT", market_type="usdt_futures", side="buy",
        order_type=order_type, quantity=1.0,
        price=60000.0 if order_type == "limit" else None,
    )


async def _run_okx(create_resp, fetch_resp=None):
    """Drive OKXClient.place_order with a mocked ccxt exchange + contract_size=1."""
    from aletheia.exchanges.okx.client import OKXClient

    client = _okx_client()
    fake_ex = MagicMock()
    fake_ex.create_order = AsyncMock(return_value=create_resp)
    fake_ex.fetch_order = AsyncMock(return_value=fetch_resp if fetch_resp is not None else create_resp)
    with patch.object(OKXClient, "_ensure_exchange", return_value=fake_ex), \
            patch.object(OKXClient, "_contract_size", new=AsyncMock(return_value=1.0)):
        return await client.place_order(_okx_order())


@pytest.mark.asyncio
async def test_okx_partial_fill_recorded():
    # CCXT reports a partial as status "open" with filled>0.
    resp = await _run_okx({"id": "O1", "status": "open", "filled": 0.3, "average": 60000.0})
    assert resp.status == "partially_filled", "OKX partial must NOT collapse to submitted"
    assert resp.fill_price == 60000.0
    assert resp.filled_quantity == pytest.approx(0.3), "partial qty (×contract_size) must be recorded"


@pytest.mark.asyncio
async def test_okx_full_fill_recorded():
    resp = await _run_okx({"id": "O2", "status": "closed", "filled": 1.0, "average": 60000.0})
    assert resp.status == "filled"
    assert resp.fill_price == 60000.0
    assert resp.filled_quantity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_okx_unfilled_does_not_fabricate():
    # status "open" with NO fill → submitted, and NO fabricated price/qty.
    resp = await _run_okx({"id": "O3", "status": "open", "filled": 0, "average": None})
    assert resp.status == "submitted"
    assert resp.fill_price is None and resp.filled_quantity is None


# ---------------------------------------------------------------------------
# Binance client
# ---------------------------------------------------------------------------


def _binance_client():
    from aletheia.exchanges.binance.client import BinanceClient
    with patch("aletheia.exchanges.binance.client.get_secret", return_value="x"):
        return BinanceClient(mode="testnet")


def _binance_order(order_type="market"):
    from aletheia.exchanges.binance.client import OrderRequest
    return OrderRequest(
        symbol="BTC/USDT", market_type="usdt_futures", side="buy",
        order_type=order_type, quantity=1.0,
        price=60000.0 if order_type == "limit" else None,
    )


async def _run_binance(create_resp, fetch_resp=None):
    client = _binance_client()
    fake_ex = MagicMock()
    fake_ex.create_order = AsyncMock(return_value=create_resp)
    fake_ex.fetch_order = AsyncMock(return_value=fetch_resp if fetch_resp is not None else create_resp)
    client._exchange = fake_ex
    return await client.place_order(_binance_order())


@pytest.mark.asyncio
async def test_binance_partial_fill_recorded():
    resp = await _run_binance({"id": "B1", "status": "open", "filled": 0.3, "average": 60000.0})
    assert resp.status == "partially_filled", "Binance partial must NOT collapse to submitted"
    assert resp.fill_price == 60000.0
    assert resp.filled_quantity == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_binance_poll_confirms_late_fill():
    # create_order returns "open" (not yet filled); the new confirm-poll sees it filled.
    resp = await _run_binance(
        {"id": "B2", "status": "open", "filled": 0, "average": None},
        fetch_resp={"id": "B2", "status": "closed", "filled": 1.0, "average": 60000.0},
    )
    assert resp.status == "filled", "Binance must poll once and pick up the late fill"
    assert resp.fill_price == 60000.0
    assert resp.filled_quantity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_binance_unfilled_does_not_fabricate():
    resp = await _run_binance({"id": "B3", "status": "open", "filled": 0, "average": None})
    assert resp.status == "submitted"
    assert resp.fill_price is None and resp.filled_quantity is None
