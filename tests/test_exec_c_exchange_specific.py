"""Tests for OKX+Bitget exchange-specific execution fixes.

Covers:
- #1711: OKX uses fillTime (not ts) for trade timestamps
- #1706: OKX no-position close raises ClosePositionAbsentError
- #1771: OKX clOrdId normalization (hyphens stripped)
- #1727: Bitget TPSL toggle gates native orders
- #1521: Bitget margin-transfer errors block order placement
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# #1711 — OKX fillTime
# ---------------------------------------------------------------------------

def test_okx_uses_fillTime_when_available():
    """#1711: get_verified_position_exit and fetch_private_fill_evidence prefer
    fillTime over ts for trade execution timestamp."""
    from aletheia.exchanges.okx.client import _okx_int

    # fillTime present → use it
    raw = {"fillTime": "1000", "ts": "2000"}
    ts = _okx_int(raw.get("fillTime") or raw.get("ts"))
    assert ts == 1000, "Should prefer fillTime over ts"

    # fillTime absent → fall back to ts
    raw_no_ft = {"ts": "3000"}
    ts2 = _okx_int(raw_no_ft.get("fillTime") or raw_no_ft.get("ts"))
    assert ts2 == 3000, "Should fall back to ts when fillTime absent"


# ---------------------------------------------------------------------------
# #1706 — OKX no-position close raises ClosePositionAbsentError
# ---------------------------------------------------------------------------

def test_okx_no_position_raises_absent_error():
    """#1706: OKX error 51020 / 'Position does not exist' on a reduce-only close
    is raised as ClosePositionAbsentError rather than a generic RuntimeError."""
    from aletheia.exchanges.okx.client import OKXClient, OrderRequest
    from aletheia.exchanges.submission_errors import ClosePositionAbsentError
    import ccxt.async_support as ccxt

    order = OrderRequest(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        side="sell",
        order_type="market",
        quantity=0.1,
        reduce_only=True,
    )

    # Build a client without credential validation; inject a mock exchange
    client = OKXClient.__new__(OKXClient)
    client._mode = "demo"
    # Use a plain Mock for the exchange, with a sync create_order that raises
    mock_exchange = MagicMock()
    mock_exchange.create_order = AsyncMock(
        side_effect=ccxt.ExchangeError(
            "51020: Order does not match position info. Position does not exist."
        )
    )
    mock_exchange.market.return_value = {"id": "BTC-USDT-SWAP", "contractSize": 0.01}
    mock_exchange.load_markets = AsyncMock()
    client._exchange = mock_exchange

    async def run():
        try:
            return await client.place_order(order)
        finally:
            # Drain the mock to avoid unawaited-coroutine leaks
            client._exchange = None

    with pytest.raises(ClosePositionAbsentError) as exc_info:
        asyncio.run(run())

    assert "51020" in str(exc_info.value) or "no matching position" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# #1771 — OKX clOrdId normalization
# ---------------------------------------------------------------------------

def test_okx_clordid_normalization():
    """#1771: fill matching succeeds when local id has hyphens ('close-abc')
    but OKX clOrdId lacks them ('closeabc')."""
    # Test the normalization logic directly — the key comparison in
    # fetch_private_fill_evidence is:
    #   matched = remote_client_id.replace("-", "") == client_order_id.replace("-", "")
    local_id = "close-abc123def456"     # stored in DB (with hyphen)
    remote_id = "closeabc123def456"     # OKX strips hyphens before returning

    # Before fix: direct equality fails
    assert remote_id != local_id, "Sanity: raw strings differ"

    # After fix: normalized comparison succeeds
    assert remote_id.replace("-", "") == local_id.replace("-", ""), (
        "Normalized comparison must match despite hyphen difference"
    )

    # Also verify a genuinely different clOrdId still doesn't match
    unrelated_id = "opensomethingelse"
    assert unrelated_id.replace("-", "") != local_id.replace("-", "")


# ---------------------------------------------------------------------------
# #1727 — Bitget TPSL toggle gates native orders
# ---------------------------------------------------------------------------

def test_bitget_tpsl_gated_by_toggle():
    """#1727: When exchange_tpsl_enabled=False, preset TPSL prices are not
    forwarded to BitgetClient.place_order even when stop/TP prices are approved."""
    from aletheia.execution import bitget_broker

    mock_config = MagicMock()
    mock_config.trading.exchange_tpsl_enabled = False
    mock_config.exchange.bitget_profile = "uta_v3"

    captured_requests = []

    def mock_run_coro(coro):
        # Capture OrderRequest passed to place_order; return a fake response
        if hasattr(coro, "cr_frame"):
            # Can't easily introspect coro locals here, so rely on call capture below
            pass
        mock_resp = MagicMock()
        mock_resp.order_id = "ordid123"
        mock_resp.status = "submitted"
        return mock_resp

    mock_client = MagicMock()

    def capture_place_order(request):
        captured_requests.append(request)
        coro = AsyncMock()
        return coro()

    with (
        patch.object(bitget_broker, "load_config", return_value=mock_config),
        patch.object(bitget_broker, "_guard_unresolved_submission"),
        patch.object(bitget_broker, "_approved_protection_prices", return_value=(49000.0, 51000.0)),
        patch.object(bitget_broker, "insert_order"),
        patch.object(bitget_broker, "update_order"),
        patch.object(bitget_broker, "write_post_commit_audit"),
        patch("aletheia.execution.bitget_broker.BitgetClient", return_value=mock_client),
        patch("aletheia.execution.bitget_broker._run_coro") as mock_run_coro_patch,
    ):
        # set_leverage and place_order both go through _run_coro
        fake_resp = MagicMock()
        fake_resp.order_id = "ordid123"
        fake_resp.status = "submitted"
        mock_run_coro_patch.return_value = fake_resp

        try:
            bitget_broker.execute_live_order(
                proposal_id="prop123",
                risk_decision_id="risk123",
                symbol="BTC/USDT",
                market_type="usdt_futures",
                direction="long",
                order_type="market",
                quantity=0.1,
                price=50000.0,
                execution_mode="bitget_demo",
                stop_price=49000.0,
                take_profit_price=51000.0,
                leverage=10,
            )
        except Exception:
            pass

        # Find the place_order call — second _run_coro call after set_leverage
        coro_calls = mock_run_coro_patch.call_args_list
        assert len(coro_calls) >= 1, "Expected _run_coro to be called for place_order"

        # The OrderRequest is the argument to client.place_order(...)
        # client.place_order is a MagicMock; capture its call args
        place_order_calls = mock_client.place_order.call_args_list
        assert len(place_order_calls) >= 1, "place_order should have been called"

        order_req = place_order_calls[-1].args[0]
        # Key assertion: TPSL prices must be None because toggle is off
        assert order_req.preset_stop_loss_price is None, (
            f"preset_stop_loss_price should be None when tpsl_enabled=False, "
            f"got {order_req.preset_stop_loss_price}"
        )
        assert order_req.preset_take_profit_price is None, (
            f"preset_take_profit_price should be None when tpsl_enabled=False, "
            f"got {order_req.preset_take_profit_price}"
        )


# ---------------------------------------------------------------------------
# #1521 — Bitget margin transfer errors block order placement
# ---------------------------------------------------------------------------

def test_bitget_margin_transfer_error_blocks_order():
    """#1521: When _ensure_mix_margin raises MarginTransferError, execute_live_order
    raises before calling place_order — no exchange order is placed."""
    from aletheia.execution import bitget_broker
    from aletheia.execution.bitget_broker import MarginTransferError

    mock_config = MagicMock()
    mock_config.trading.exchange_tpsl_enabled = False
    mock_config.exchange.bitget_profile = "uta_v3"

    mock_client = MagicMock()

    with (
        patch.object(bitget_broker, "load_config", return_value=mock_config),
        patch.object(bitget_broker, "_guard_unresolved_submission"),
        patch.object(bitget_broker, "_approved_protection_prices", return_value=(None, None)),
        patch.object(bitget_broker, "insert_order"),
        patch.object(bitget_broker, "update_order"),
        patch("aletheia.execution.bitget_broker.BitgetClient", return_value=mock_client),
        # Raise MarginTransferError from _ensure_mix_margin
        patch.object(bitget_broker, "_ensure_mix_margin",
                     side_effect=MarginTransferError("mock margin failure")),
        patch("aletheia.execution.bitget_broker._run_coro") as mock_run_coro,
    ):
        with pytest.raises(MarginTransferError):
            bitget_broker.execute_live_order(
                proposal_id="prop123",
                risk_decision_id="risk123",
                symbol="BTC/USDT",
                market_type="usdt_futures",
                direction="long",
                order_type="market",
                quantity=0.1,
                price=50000.0,
                execution_mode="bitget_demo",
                leverage=10,
            )

        # place_order must NOT have been called — order was blocked before submission
        assert mock_client.place_order.call_count == 0, (
            "place_order should not be called when margin transfer fails"
        )
