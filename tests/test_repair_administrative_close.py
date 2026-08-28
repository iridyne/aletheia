"""Focused regressions for administrative-close repair (#2156/#2157)."""

from __future__ import annotations

import sqlite3

import pytest

from aletheia.maintenance import repair_administrative_close as repair
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Order, Position
from aletheia.storage.sqlite import reset_db


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite_store

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sqlite_store._db_conn = None
    connection = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    sqlite_store._db_conn = connection
    run_migrations()


def _seed_administrative_close(monkeypatch) -> Position:
    position = Position(
        id="repair-position",
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        entry_fee=0.1,
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        execution_mode="bitget_demo",
        status="closed",
        close_category="administrative",
        close_reason="disappearance",
        realized_pnl=None,
        opened_at="2026-08-26T00:00:00+00:00",
        closed_at="2026-08-27T00:00:00+00:00",
    )
    repo.insert_position(position)
    monkeypatch.setattr(repo, "validate_native_order_authority", lambda _order: None)
    repo.insert_order(
        Order(
            id="repair-close-order",
            symbol=position.symbol,
            market_type=position.market_type,
            direction="sell",
            order_type="market",
            quantity=position.quantity,
            price=100.0,
            execution_mode=position.execution_mode,
            exchange_order_id="exchange-close-order",
            position_id=position.id,
            close_reason="take_profit",
            status="filled",
            source="native",
            filled_at="2026-08-27T00:00:00+00:00",
        )
    )
    return position


def _private_rows():
    return [
        {
            "tradeId": "remote-fill-1",
            "orderId": "exchange-close-order",
            "fillPrice": "101.0",
            "fillQuantity": "0.4",
            "fee": "0.04",
            "feeCoin": "USDT",
            "cTime": "1787788800000",
        },
        {
            "tradeId": "remote-fill-2",
            "orderId": "exchange-close-order",
            "fillPrice": "102.0",
            "fillQuantity": "0.6",
            "fee": "0.06",
            "feeCoin": "USDT",
            "cTime": "1787788801000",
        },
    ]


def test_aggregate_exchange_fills_uses_all_private_rows():
    result = repair._aggregate_exchange_fills(
        _private_rows(),
        exchange_order_id="exchange-close-order",
        symbol="ETH/USDT",
        execution_mode="bitget_demo",
    )

    assert result is not None
    assert result["fill_ids"] == ["remote-fill-1", "remote-fill-2"]
    assert result["fill_quantity"] == pytest.approx(1.0)
    assert result["price"] == pytest.approx(101.6)
    assert result["fee"] == pytest.approx(0.1)
    assert result["fee_status"] == "complete"
    assert result["filled_at"] == "2026-08-27T00:00:01+00:00"


def test_repair_requires_complete_private_evidence(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    position = _seed_administrative_close(monkeypatch)
    monkeypatch.setattr(repair, "_query_exchange_fill", lambda *args, **kwargs: None)

    with pytest.raises(ValueError, match="administrative close preserved"):
        repair.repair_position(position.id)

    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "closed"
    assert stored["close_category"] == "administrative"
    assert stored["realized_pnl"] is None


def test_repair_rolls_back_intermediate_reopen_on_close_failure(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    position = _seed_administrative_close(monkeypatch)
    evidence = repair._aggregate_exchange_fills(
        _private_rows(),
        exchange_order_id="exchange-close-order",
        symbol=position.symbol,
        execution_mode=position.execution_mode,
    )
    monkeypatch.setattr(repair, "_query_exchange_fill", lambda *args, **kwargs: evidence)

    def fail_close(*args, **kwargs):
        raise RuntimeError("close path failed")

    monkeypatch.setattr("aletheia.portfolio.pnl.close_position", fail_close)

    with pytest.raises(RuntimeError, match="close path failed"):
        repair.repair_position(position.id)

    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "closed"
    assert stored["close_category"] == "administrative"
    assert stored["close_reason"] == "disappearance"
    assert repo.list_audit_events(limit=100) == []


def test_repair_uses_vwap_and_aggregated_fee(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    position = _seed_administrative_close(monkeypatch)
    evidence = repair._aggregate_exchange_fills(
        _private_rows(),
        exchange_order_id="exchange-close-order",
        symbol=position.symbol,
        execution_mode=position.execution_mode,
    )
    monkeypatch.setattr(repair, "_query_exchange_fill", lambda *args, **kwargs: evidence)
    monkeypatch.setattr("aletheia.portfolio.pnl._btc_price_at_close", lambda: None)

    result = repair.repair_position(position.id)

    assert result["exit_price"] == pytest.approx(101.6)
    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "closed"
    assert stored["current_price"] == pytest.approx(101.6)
    assert stored["exit_price_source"] == "broker_fill"
    assert stored["exit_fill_verified"] == 1
    assert stored["exit_fee"] == pytest.approx(0.1)
    assert stored["realized_pnl"] == pytest.approx(1.4)
    assert stored["closed_at"] == "2026-08-27T00:00:01+00:00"
