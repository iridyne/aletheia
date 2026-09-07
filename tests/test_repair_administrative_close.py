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


# ---------------------------------------------------------------------------
# _query_exchange_fill itself — the three tests above monkeypatch it away, so
# without these its body is never executed by the suite. An earlier migration of
# this function shipped permanently broken (it omitted the required `start_at`,
# so every call returned None) and every test still passed.
# ---------------------------------------------------------------------------


def _projection_double(monkeypatch, *, rows, capture: dict):
    """Replace the UTA projection with a double that records the query it received."""
    from types import SimpleNamespace

    from aletheia.maintenance import repair_administrative_close as repair
    import aletheia.execution.reconciliation as reconciliation
    import aletheia.uta.manager as uta_manager

    identity = SimpleNamespace(execution_mode="bitget_demo")
    monkeypatch.setattr(
        uta_manager,
        "build_uta_registry",
        lambda *a, **k: SimpleNamespace(configured_identities=lambda: (identity,)),
    )

    class _Projection:
        def __init__(self, execution_mode, expected_account):
            capture["execution_mode"] = execution_mode
            capture["expected_account"] = expected_account

        async def get_order_fills(self, *, order_id, symbol, market_type, start_at=None):
            capture["order_id"] = order_id
            capture["symbol"] = symbol
            capture["market_type"] = market_type
            capture["start_at"] = start_at
            return rows

    monkeypatch.setattr(reconciliation, "_UTAReconciliationProjection", _Projection)
    return repair


def test_query_exchange_fill_passes_the_required_history_lower_bound(monkeypatch):
    """`start_at` is mandatory: without it the UTA fills read answers PARTIAL.

    `CCXTUTAAdapter.get_fills` rejects `scope.start_at is None` with
    `private_fill_history_start_required`, so an omitted bound silently disables
    the repair instead of failing loudly. This asserts the bound reaches the query.
    """
    capture: dict = {}
    repair = _projection_double(monkeypatch, rows=_private_rows(), capture=capture)

    result = repair._query_exchange_fill(
        "exchange-close-order",
        "BTC/USDT",
        execution_mode="bitget_demo",
        start_at="2026-06-01T00:00:00+00:00",
    )

    assert result is not None, "complete private evidence should aggregate"
    assert capture["start_at"] == "2026-06-01T00:00:00+00:00"
    assert capture["order_id"] == "exchange-close-order"
    assert capture["market_type"] == "usdt_futures"
    # The account is resolved from the Position's own mode, not hardcoded to demo.
    assert capture["execution_mode"] == "bitget_demo"
    assert capture["expected_account"].execution_mode == "bitget_demo"


def test_query_exchange_fill_fails_closed_without_a_lower_bound(monkeypatch):
    capture: dict = {}
    repair = _projection_double(monkeypatch, rows=_private_rows(), capture=capture)

    assert repair._query_exchange_fill(
        "exchange-close-order", "BTC/USDT",
        execution_mode="bitget_demo", start_at=None,
    ) is None
    assert capture == {}, "no venue query may be issued without a history bound"


def test_query_exchange_fill_fails_closed_when_the_account_is_not_configured(monkeypatch):
    from types import SimpleNamespace

    import aletheia.uta.manager as uta_manager
    from aletheia.maintenance import repair_administrative_close as repair

    monkeypatch.setattr(
        uta_manager, "build_uta_registry",
        lambda *a, **k: SimpleNamespace(configured_identities=lambda: ()),
    )
    assert repair._query_exchange_fill(
        "exchange-close-order", "BTC/USDT",
        execution_mode="bitget_live_limited", start_at="2026-06-01T00:00:00+00:00",
    ) is None


def test_query_exchange_fill_fails_closed_when_the_read_raises(monkeypatch):
    from types import SimpleNamespace

    import aletheia.execution.reconciliation as reconciliation
    import aletheia.uta.manager as uta_manager
    from aletheia.maintenance import repair_administrative_close as repair

    identity = SimpleNamespace(execution_mode="bitget_demo")
    monkeypatch.setattr(
        uta_manager, "build_uta_registry",
        lambda *a, **k: SimpleNamespace(configured_identities=lambda: (identity,)),
    )

    class _Raising:
        def __init__(self, *a, **k):
            pass

        async def get_order_fills(self, **kwargs):
            raise RuntimeError("uta_exact_fills_incomplete")

    monkeypatch.setattr(reconciliation, "_UTAReconciliationProjection", _Raising)
    assert repair._query_exchange_fill(
        "exchange-close-order", "BTC/USDT",
        execution_mode="bitget_demo", start_at="2026-06-01T00:00:00+00:00",
    ) is None


def test_query_exchange_fill_resolves_the_positions_own_account_not_demo(monkeypatch):
    """The identity must come from the Position's execution_mode.

    The pre-migration code constructed `BitgetClient(mode="demo", ...)` with the
    mode HARDCODED, so repairing a `bitget_live_limited` Position queried the demo
    account and would have aggregated another account's fills into a live position's
    realized PnL. Parametrising over demo alone cannot catch that, so this asserts
    the live mode explicitly.
    """
    from types import SimpleNamespace

    import aletheia.execution.reconciliation as reconciliation
    import aletheia.uta.manager as uta_manager
    from aletheia.maintenance import repair_administrative_close as repair

    capture: dict = {}
    identities = (
        SimpleNamespace(execution_mode="bitget_demo"),
        SimpleNamespace(execution_mode="bitget_live_limited"),
    )
    monkeypatch.setattr(
        uta_manager, "build_uta_registry",
        lambda *a, **k: SimpleNamespace(configured_identities=lambda: identities),
    )

    class _Projection:
        def __init__(self, execution_mode, expected_account):
            capture["execution_mode"] = execution_mode
            capture["expected_account"] = expected_account

        async def get_order_fills(self, **kwargs):
            return _private_rows()

    monkeypatch.setattr(reconciliation, "_UTAReconciliationProjection", _Projection)

    repair._query_exchange_fill(
        "exchange-close-order", "BTC/USDT",
        execution_mode="bitget_live_limited",
        start_at="2026-06-01T00:00:00+00:00",
    )

    assert capture["execution_mode"] == "bitget_live_limited"
    assert capture["expected_account"].execution_mode == "bitget_live_limited", (
        "the live Position was repaired against a different account"
    )
