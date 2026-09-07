"""Partial reductions remain fail-closed without product authorization."""


import aletheia.portfolio.position_manager as pm


def _insert(db, pid, *, mode, qty=2.0, entry=100.0, entry_fee=0.1):
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,entry_fee) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, "BTC/USDT", "usdt_futures", "long", qty, entry, mode, "open", entry_fee),
    )



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
    _insert(get_db(), "ok2", mode="okx_demo", qty=2.0)
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
