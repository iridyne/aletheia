"""Test clipped close partial reduction (#2145)."""
from aletheia.execution.exchange_materialization import materialize_bitget_close
from aletheia.storage import repositories as repo
from aletheia.storage.models import Order, Position, new_id


def _fills(quantities, base_price=50000.0, client_oid="", ex_order_id="ex-order-A"):
    """Mock Bitget private fills response rows (all fills share the same orderId)."""
    return [
        {
            "symbol": "BTCUSDT",
            "clientOid": client_oid,
            "orderId": ex_order_id,
            "tradeId": f"fill-{i}",
            "side": "sell",
            "price": str(base_price + i * 10),
            "size": str(qty),
            "feeDetail": {"feeCoin": "USDT", "totalFee": str(round(qty * 0.0005, 8))},
            "cTime": str(1704067200000 + i * 1000),
        }
        for i, qty in enumerate(quantities)
    ]


def _make_position(proposal_id, pos_id, quantity=424.0):
    repo.insert_position(Position(
        id=pos_id,
        symbol="BTCUSDT",
        market_type="usdt_futures",
        direction="long",
        quantity=quantity,
        entry_price=49000.0,
        execution_mode="bitget_demo",
        proposal_id=proposal_id,
        status="open",
    ))


def _make_close_order(proposal_id, pos_id, order_id, client_oid, quantity=212.0):
    repo.insert_order(Order(
        id=order_id,
        proposal_id=proposal_id,
        position_id=pos_id,
        symbol="BTCUSDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="bitget_demo",
        quantity=quantity,
        price=50000.0,
        order_type="market",
        status="submitted",
        client_order_id=client_oid,
        close_reason="exit",
    ))
    repo.update_position(pos_id, pending_close_order_id=order_id)


def test_clipped_close_exact_fill_reduces_position_and_releases_claim():
    """Position 424, Order 212, Fill 212 → Position 212, state=filled_clip_partial_position."""
    proposal_id = new_id()
    pos_id = new_id()
    order_id = new_id()
    client_oid = f"close-{pos_id}"

    _make_position(proposal_id, pos_id, quantity=424.0)
    _make_close_order(proposal_id, pos_id, order_id, client_oid, quantity=212.0)

    result = materialize_bitget_close(order_id, _fills([212.0], client_oid=client_oid))

    assert result.state == "filled_clip_partial_position"
    assert result.new_fill_count == 1
    position = repo.get_position(pos_id)
    assert position is not None and position["quantity"] == 212.0
    assert position["pending_close_order_id"] is None
    order = repo.get_order(order_id)
    assert order is not None and order["status"] == "filled"


def test_clipped_close_idempotent_replay():
    """Replay same fill 10 times → Position stays at 212, no overshoot."""
    proposal_id = new_id()
    pos_id = new_id()
    order_id = new_id()
    client_oid = f"close-{pos_id}"

    _make_position(proposal_id, pos_id, quantity=424.0)
    _make_close_order(proposal_id, pos_id, order_id, client_oid, quantity=212.0)

    fills_data = _fills([212.0], client_oid=client_oid)
    for _ in range(10):
        materialize_bitget_close(order_id, fills_data)

    position = repo.get_position(pos_id)
    assert position is not None and position["quantity"] == 212.0
    order = repo.get_order(order_id)
    assert order is not None and order["status"] == "filled"


def test_clipped_close_two_batches_converges():
    """Partial fill batch then complete fill → Position 212."""
    proposal_id = new_id()
    pos_id = new_id()
    order_id = new_id()
    client_oid = f"close-{pos_id}"

    _make_position(proposal_id, pos_id, quantity=424.0)
    _make_close_order(proposal_id, pos_id, order_id, client_oid, quantity=212.0)

    result1 = materialize_bitget_close(order_id, _fills([100.0], client_oid=client_oid))
    assert result1.state == "partially_filled"
    position = repo.get_position(pos_id)
    assert position is not None and position["quantity"] == 424.0  # Not yet reduced

    result2 = materialize_bitget_close(
        order_id, _fills([100.0, 112.0], client_oid=client_oid)
    )
    assert result2.state == "filled_clip_partial_position"
    position = repo.get_position(pos_id)
    assert position is not None and position["quantity"] == 212.0


def test_full_close_still_uses_complete_path():
    """Position 212, Order 212, Fill 212 → full close (state=filled, position closed)."""
    proposal_id = new_id()
    pos_id = new_id()
    order_id = new_id()
    client_oid = f"close-{pos_id}"

    _make_position(proposal_id, pos_id, quantity=212.0)
    _make_close_order(proposal_id, pos_id, order_id, client_oid, quantity=212.0)

    result = materialize_bitget_close(order_id, _fills([212.0], client_oid=client_oid))

    assert result.state == "filled"
    position = repo.get_position(pos_id)
    assert position is not None and position["status"] == "closed"


def test_clipped_close_crash_restart_convergence():
    """Crash after fill insert, replay converges → Position 212, Order filled."""
    proposal_id = new_id()
    pos_id = new_id()
    order_id = new_id()
    client_oid = f"close-{pos_id}"

    _make_position(proposal_id, pos_id, quantity=424.0)
    _make_close_order(proposal_id, pos_id, order_id, client_oid, quantity=212.0)

    fills_data = _fills([212.0], client_oid=client_oid)
    original_update_order = repo.update_order

    def crash_on_status(oid, **kwargs):
        if oid == order_id and "status" in kwargs:
            raise RuntimeError("simulated crash")
        return original_update_order(oid, **kwargs)

    repo.update_order = crash_on_status
    try:
        materialize_bitget_close(order_id, fills_data)
    except RuntimeError:
        pass
    finally:
        repo.update_order = original_update_order

    # Replay after crash — assert final converged state only
    result = materialize_bitget_close(order_id, fills_data)
    assert result.state == "filled_clip_partial_position"
    position = repo.get_position(pos_id)
    assert position is not None and position["quantity"] == 212.0
    assert position["pending_close_order_id"] is None
    order = repo.get_order(order_id)
    assert order is not None and order["status"] == "filled"
