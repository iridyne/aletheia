"""Gateway-level evidence that legal exchange quantities reach Risk Engine."""

import sqlite3
from types import SimpleNamespace


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()


def _approved():
    return SimpleNamespace(approved=True, allowed_execution_mode="bitget_demo", decision_id="risk-1")


def test_adjusted_exchange_quantity_is_rechecked_by_risk_then_submitted(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway

    seen_by_risk = []
    monkeypatch.setattr(gateway, "evaluate_proposal", lambda p: (seen_by_risk.append(p.quantity) or _approved()))
    monkeypatch.setattr(gateway, "_prepare_exchange_quantity", lambda *args: (0.003, None))
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *args: None)
    import aletheia.execution.bitget_broker as broker
    sent = []
    monkeypatch.setattr(broker, "execute_live_order", lambda **kw: (sent.append(kw["quantity"]) or {"order_id": "o1", "exchange_order_id": "x1"}))

    result = gateway.submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0011, price=2000, execution_mode="bitget_demo",
    )

    assert result["status"] == "executed"
    assert seen_by_risk == [0.0011, 0.003]
    assert sent == [0.003]


def test_unavailable_rules_reject_before_any_exchange_order(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway

    monkeypatch.setattr(gateway, "evaluate_proposal", lambda p: _approved())
    monkeypatch.setattr(gateway, "_prepare_exchange_quantity", lambda *args: (0.001, "Executable venue order rules are unavailable or do not support this order. No order was sent."))
    import aletheia.execution.bitget_broker as broker
    monkeypatch.setattr(broker, "execute_live_order", lambda **kw: (_ for _ in ()).throw(AssertionError("exchange must not be called")))

    result = gateway.submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.001, price=2000, execution_mode="bitget_demo",
    )

    assert result["status"] == "rejected"
    assert "No order was sent" in result["message"]
