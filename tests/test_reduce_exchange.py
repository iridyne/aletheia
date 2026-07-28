"""Tests for the isolated OKX partial-reduce reference and fail-closed router.

The exchange client is mocked (no keys/network). The helper does not constitute
an authorized product path.
"""

from types import SimpleNamespace

import aletheia.portfolio.position_manager as pm


def _insert(db, pid, *, mode, qty=2.0, entry=100.0, entry_fee=0.1):
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,entry_fee) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, "BTC/USDT", "usdt_futures", "long", qty, entry, mode, "open", entry_fee),
    )


# ── OKX reduce-only reference (mock the client) ──────────────────────────────

def test_reduce_okx_places_reduce_only_and_records(monkeypatch):
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import _select_one, list_position_close_events
    import aletheia.execution.okx_broker as okxb

    db = get_db()
    _insert(db, "ok1", mode="okx_demo", qty=2.0, entry=100.0, entry_fee=0.1)

    captured = []

    class _FakeOKX:
        def __init__(self, mode="demo"):
            pass

        async def place_order(self, req):
            captured.append(req)
            return SimpleNamespace(
                order_id="ex-1", client_oid=getattr(req, "client_oid", None),
                status="filled", fill_price=110.0, filled_quantity=req.quantity,
            )

        async def close(self):
            pass

    monkeypatch.setattr(okxb, "OKXClient", _FakeOKX)

    res = okxb.reduce_okx_position("ok1", 0.5, reason="operator_reduce")

    assert res["reduced"] is True and res["fill_quantity"] == 0.5
    # the order was reduce-only for the PARTIAL size
    assert captured[0].reduce_only is True and captured[0].quantity == 0.5
    # reduce-in-place accounting recorded: quantity shrunk + one partial close event
    assert _select_one("positions", "ok1")["quantity"] == 1.5
    events = list_position_close_events(position_id="ok1")
    assert len(events) == 1 and events[0]["is_partial"] == 1
    # okx fee 0.0005: gross 5 - entry_fee_portion 0.025 - exit_fee 0.0275 = 4.9475
    assert events[0]["realized_pnl"] == 4.9475


# ── reduce_position_routed dispatch + guards ─────────────────────────────────

def test_router_rejects_paper_internal():
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "pi", mode="paper_internal")
    res = pm.reduce_position_routed("pi", 0.5)
    assert res.get("error") == "partial_reduce_not_production_authorized"
    assert "Proposal" in res.get("hint", "")


def test_router_full_exit_delegates_to_close(monkeypatch):
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "b1", mode="bitget_demo", qty=1.0)
    calls = []
    monkeypatch.setattr(pm, "close_position_routed",
                        lambda pid, reason=None, **kw: calls.append((pid, reason)) or {"closed": True})
    res = pm.reduce_position_routed("b1", 1.0, reason="operator_reduce")  # >= qty → full exit
    assert res == {"closed": True}
    assert calls == [("b1", "operator_reduce")]


def test_router_rejects_okx_reference_path(monkeypatch):
    from aletheia.storage.sqlite import get_db
    import aletheia.execution.okx_broker as okxb
    _insert(get_db(), "ok2", mode="okx_demo", qty=2.0)
    monkeypatch.setattr(okxb, "reduce_okx_position",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("router must not dispatch to isolated helper")
                        ))
    res = pm.reduce_position_routed("ok2", 0.5, reason="scale out")
    assert res.get("error") == "partial_reduce_not_production_authorized"


def test_router_bitget_binance_fail_closed():
    from aletheia.storage.sqlite import get_db
    db = get_db()
    _insert(db, "b2", mode="bitget_demo", qty=2.0)
    _insert(db, "bn1", mode="binance_testnet", qty=2.0)
    assert pm.reduce_position_routed("b2", 0.5).get("error") == (
        "partial_reduce_not_production_authorized"
    )
    assert pm.reduce_position_routed("bn1", 0.5).get("error") == (
        "partial_reduce_not_production_authorized"
    )
