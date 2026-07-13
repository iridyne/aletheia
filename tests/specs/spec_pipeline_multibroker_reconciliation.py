"""OKX and Binance account-explicit private-fill reconciliation contracts."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


BROKERS = (
    pytest.param(
        "aletheia.execution.okx_broker",
        "OKXClient",
        "okx_demo",
        id="okx-demo",
    ),
    pytest.param(
        "aletheia.execution.okx_broker",
        "OKXClient",
        "okx_live_limited",
        id="okx-live-limited",
    ),
    pytest.param(
        "aletheia.execution.binance_broker",
        "BinanceClient",
        "binance_testnet",
        id="binance-testnet",
    ),
    pytest.param(
        "aletheia.execution.binance_broker",
        "BinanceClient",
        "binance_live_limited",
        id="binance-live-limited",
    ),
)

OKX_ACCOUNTS = (
    pytest.param("okx_demo", "demo", id="okx-demo-account"),
    pytest.param("okx_live_limited", "live", id="okx-live-account"),
)

BINANCE_ACCOUNTS = (
    pytest.param("binance_testnet", "testnet", id="binance-testnet-account"),
    pytest.param("binance_live_limited", "live", id="binance-live-account"),
)

CLOSE_BROKERS = (
    pytest.param(
        "aletheia.execution.okx_broker",
        "OKXClient",
        "close_okx_position",
        "okx_demo",
        id="okx-demo-close",
    ),
    pytest.param(
        "aletheia.execution.okx_broker",
        "OKXClient",
        "close_okx_position",
        "okx_live_limited",
        id="okx-live-close",
    ),
    pytest.param(
        "aletheia.execution.binance_broker",
        "BinanceClient",
        "close_testnet_position",
        "binance_testnet",
        id="binance-testnet-close",
    ),
    pytest.param(
        "aletheia.execution.binance_broker",
        "BinanceClient",
        "close_testnet_position",
        "binance_live_limited",
        id="binance-live-close",
    ),
)


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    jsonl._last_audit_hash = None
    run_migrations()
    return conn


def _seed_authority(execution_mode: str, *, suffix: str):
    from tests.execution_authority import seed_order_authority

    return seed_order_authority(
        proposal_id=f"proposal-mbr-{suffix}",
        risk_decision_id=f"risk-mbr-{suffix}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60_000.0,
        execution_mode=execution_mode,
        leverage=5,
    )


def _private_fill(exchange_order_id: str, *, suffix: str) -> dict:
    return {
        "orderId": exchange_order_id,
        "tradeId": f"private-trade-{suffix}",
        "fillPrice": "60123.5",
        "fillQuantity": "0.01",
        "fee": "0.123456",
        "feeCoin": "USDT",
        "cTime": "1784050000000",
    }


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
@spec_claim(
    SpecClaim(
        "PL-MBR-01",
        "pipeline_multibroker_reconciliation",
        "§1 delayed private-fill materialization",
        "L2b",
        "A submitted OKX or Binance entry Order in demo, testnet, or limited-live "
        "mode becomes one durable Fill and Position only from order-matched private "
        "fill economics.",
        status="implemented",
    )
)
def test_delayed_private_fill_materializes_actual_economics(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.execution.exchange_materialization import (
        durable_private_fill_id,
    )
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    proposal, decision = _seed_authority(execution_mode, suffix=execution_mode)
    exchange_order_id = f"EX-MBR-{execution_mode}"
    order = Order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=execution_mode,
        exchange_order_id=exchange_order_id,
        status="submitted",
    )
    repo.insert_order(order)

    broker = importlib.import_module(broker_module)

    async def _evidence(stored_order):
        assert stored_order["id"] == order.id
        return [_private_fill(exchange_order_id, suffix=execution_mode)]

    monkeypatch.setattr(
        broker, "fetch_private_fill_evidence", _evidence, raising=False
    )

    result = reconcile()

    stored_order = repo.get_order(order.id)
    fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()
    position = db.execute(
        "SELECT * FROM positions WHERE order_id=?", (order.id,)
    ).fetchone()
    assert result["checked"] == 1
    assert result["filled"] == 1
    assert stored_order["status"] == "filled"
    assert stored_order["filled_at"] == datetime.fromtimestamp(
        1_784_050_000, tz=timezone.utc
    ).isoformat()
    assert fill is not None
    assert fill["id"] == durable_private_fill_id(
        execution_mode,
        f"private-trade-{execution_mode}",
    )
    assert fill["fill_price"] == pytest.approx(60_123.5)
    assert fill["fill_quantity"] == pytest.approx(0.01)
    assert fill["fee"] == pytest.approx(0.123456)
    assert fill["fee_source"] == "reconciled"
    assert position is not None
    assert position["quantity"] == pytest.approx(0.01)
    assert position["entry_price"] == pytest.approx(60_123.5)
    assert position["entry_fee"] == pytest.approx(0.123456)
    assert position["entry_fill_verified"] == 1
    assert position["entry_price_source"] == "broker_fill"
    assert position["opened_at"] == datetime.fromtimestamp(
        1_784_050_000, tz=timezone.utc
    ).isoformat()
    assert repo.get_trade_proposal(proposal.id)["status"] == "executed"

    second = reconcile()
    assert second["checked"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 1


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
@spec_claim(
    SpecClaim(
        "PL-MBR-02",
        "pipeline_multibroker_reconciliation",
        "§2 synchronous response handoff",
        "L2b",
        "A broker response that reports filled remains materialization_missing until "
        "order-matched private fills provide actual fee and exchange time.",
        status="implemented",
    )
)
def test_reported_filled_waits_for_private_fill_before_finalizing(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    proposal, decision = _seed_authority(
        execution_mode, suffix=f"sync-{execution_mode}"
    )
    broker = importlib.import_module(broker_module)
    exchange_order_id = f"EX-MBR-SYNC-{execution_mode}"

    class _FilledResponseClient:
        def __init__(self, *args, **kwargs):
            pass

        async def place_order(self, order):
            return SimpleNamespace(
                order_id=exchange_order_id,
                client_oid=order.client_oid,
                status="filled",
                # These aggregate response values are not final fee/time evidence.
                fill_price=60_111.0,
                filled_quantity=0.01,
            )

        async def close(self):
            pass

    monkeypatch.setattr(broker, client_name, _FilledResponseClient)
    if execution_mode == "okx_demo":
        monkeypatch.setattr(broker, "_get_demo_symbols", lambda market_type: None)

    opened = broker.execute_live_order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        leverage=proposal.leverage,
        execution_mode=execution_mode,
    )
    order = repo.get_order(opened["order_id"])

    assert opened["order_status"] == "materialization_missing"
    assert opened["fill_id"] is None
    assert opened["position_id"] is None
    assert order["status"] == "materialization_missing"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order["id"],)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (order["id"],)
    ).fetchone()[0] == 0

    async def _evidence(stored_order):
        return [_private_fill(exchange_order_id, suffix=f"sync-{execution_mode}")]

    monkeypatch.setattr(
        broker, "fetch_private_fill_evidence", _evidence, raising=False
    )
    result = reconcile()
    fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (order["id"],)
    ).fetchone()
    position = db.execute(
        "SELECT * FROM positions WHERE order_id=?", (order["id"],)
    ).fetchone()

    assert result["filled"] == 1
    assert repo.get_order(order["id"])["status"] == "filled"
    assert fill["fill_price"] == pytest.approx(60_123.5)
    assert fill["fee"] == pytest.approx(0.123456)
    assert fill["filled_at"] == datetime.fromtimestamp(
        1_784_050_000, tz=timezone.utc
    ).isoformat()
    assert position["entry_fill_verified"] == 1
    assert position["entry_price_source"] == "broker_fill"
    assert position["opened_at"] == datetime.fromtimestamp(
        1_784_050_000, tz=timezone.utc
    ).isoformat()


@pytest.mark.parametrize(
    ("old_verified", "old_source"),
    (
        pytest.param(False, "aggregate_response", id="explicitly-unverified"),
        pytest.param(True, "unavailable", id="inconsistent-legacy-marker"),
    ),
)
@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
@spec_claim(
    SpecClaim(
        "PL-MBR-05",
        "pipeline_multibroker_reconciliation",
        "§5 averaging provenance",
        "L2b",
        "A verified private fill may update an existing Position but must not mark "
        "an older unverified entry leg as verified.",
        status="implemented",
    )
)
def test_verified_averaging_leg_does_not_upgrade_unverified_history(
    tmp_path,
    monkeypatch,
    broker_module,
    client_name,
    execution_mode,
    old_verified,
    old_source,
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Position

    old_position = Position(
        id=f"position-unverified-{execution_mode}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=59_000.0,
        current_price=59_000.0,
        execution_mode=execution_mode,
        entry_fee=0.1,
        entry_fill_verified=old_verified,
        entry_price_source=old_source,
    )
    repo.insert_position(old_position)
    proposal, decision = _seed_authority(
        execution_mode, suffix=f"average-{execution_mode}"
    )
    exchange_order_id = f"EX-MBR-AVERAGE-{execution_mode}"
    order = Order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=execution_mode,
        exchange_order_id=exchange_order_id,
        status="materialization_missing",
    )
    repo.insert_order(order)
    broker = importlib.import_module(broker_module)

    async def _evidence(stored_order):
        return [_private_fill(exchange_order_id, suffix=f"average-{execution_mode}")]

    monkeypatch.setattr(
        broker, "fetch_private_fill_evidence", _evidence, raising=False
    )

    result = reconcile()

    position = db.execute(
        "SELECT * FROM positions WHERE id=?", (old_position.id,)
    ).fetchone()
    assert result["filled"] == 1
    assert position["quantity"] == pytest.approx(0.02)
    assert position["entry_price"] == pytest.approx((59_000.0 + 60_123.5) / 2)
    assert position["entry_fill_verified"] == 0
    assert position["entry_price_source"] == old_source


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
@spec_claim(
    SpecClaim(
        "PL-MBR-06",
        "pipeline_multibroker_reconciliation",
        "§6 legacy estimated-fill conflict",
        "L2c",
        "A local estimated Fill that cannot be matched by the same private fill "
        "identity and economics blocks Position verification.",
        status="implemented",
    )
)
def test_unmatched_estimated_fill_blocks_position_verification(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order

    proposal, decision = _seed_authority(
        execution_mode, suffix=f"estimated-conflict-{execution_mode}"
    )
    exchange_order_id = f"EX-MBR-ESTIMATED-{execution_mode}"
    order = Order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=execution_mode,
        exchange_order_id=exchange_order_id,
        status="materialization_missing",
    )
    repo.insert_order(order)
    repo.insert_fill(Fill(
        id=f"local-estimated-{execution_mode}",
        order_id=order.id,
        fill_price=60_123.5,
        fill_quantity=0.01,
        fee=0.123456,
        fee_currency="USDT",
        fee_source="estimated",
        filled_at=datetime.fromtimestamp(
            1_784_050_000, tz=timezone.utc
        ).isoformat(),
    ))
    broker = importlib.import_module(broker_module)

    async def _evidence(stored_order):
        return [_private_fill(
            exchange_order_id, suffix=f"estimated-conflict-{execution_mode}"
        )]

    monkeypatch.setattr(
        broker, "fetch_private_fill_evidence", _evidence, raising=False
    )

    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "materialization_missing"
    assert db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    local_fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()
    assert local_fill["fee_source"] == "estimated"


@spec_claim(
    SpecClaim(
        "PL-MBR-07",
        "pipeline_multibroker_reconciliation",
        "§7 periodic reconciliation",
        "L2b",
        "The periodic job reconciles unresolved OKX and Binance Orders from every "
        "explicit account mode regardless of the currently selected exchange mode.",
        status="implemented",
    )
)
def test_periodic_job_reconciles_all_explicit_account_orders(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.execution import binance_broker, okx_broker
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    orders: list[Order] = []
    for execution_mode in (
        "okx_demo",
        "okx_live_limited",
        "binance_testnet",
        "binance_live_limited",
    ):
        proposal, decision = _seed_authority(
            execution_mode, suffix=f"scheduled-{execution_mode}"
        )
        order = Order(
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            client_order_id=proposal.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=proposal.quantity,
            price=proposal.price,
            execution_mode=execution_mode,
            exchange_order_id=f"EX-MBR-SCHEDULED-{execution_mode}",
            status="materialization_missing",
        )
        repo.insert_order(order)
        orders.append(order)

    async def _okx_evidence(stored_order):
        return [_private_fill(
            stored_order["exchange_order_id"],
            suffix=f"scheduled-{stored_order['execution_mode']}",
        )]

    async def _binance_evidence(stored_order):
        return [_private_fill(
            stored_order["exchange_order_id"],
            suffix=f"scheduled-{stored_order['execution_mode']}",
        )]

    monkeypatch.setattr(okx_broker, "fetch_private_fill_evidence", _okx_evidence)
    monkeypatch.setattr(
        binance_broker, "fetch_private_fill_evidence", _binance_evidence
    )
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(paused=False),
            exchange=SimpleNamespace(mode="demo"),
        ),
    )

    scheduler._run_reconciliation()

    assert [repo.get_order(order.id)["status"] for order in orders] == [
        "filled", "filled", "filled", "filled"
    ]
    assert db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 4


@spec_claim(
    SpecClaim(
        "PL-MBR-08",
        "pipeline_multibroker_reconciliation",
        "§8 empty periodic reconciliation",
        "L2c",
        "A periodic reconciliation pass with no unresolved Orders constructs no "
        "exchange client.",
        status="implemented",
    )
)
def test_periodic_job_with_no_orders_constructs_no_exchange_client(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.binance import client as binance_client
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.exchanges.okx import client as okx_client

    class _UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("empty reconciliation must not construct a client")

    monkeypatch.setattr(okx_client, "OKXClient", _UnexpectedClient)
    monkeypatch.setattr(binance_client, "BinanceClient", _UnexpectedClient)
    monkeypatch.setattr(bitget_client, "BitgetClient", _UnexpectedClient)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(paused=False),
            exchange=SimpleNamespace(mode="demo"),
        ),
    )

    scheduler._run_reconciliation()


@spec_claim(
    SpecClaim(
        "PL-MBR-09",
        "pipeline_multibroker_reconciliation",
        "§9 account isolation",
        "L2c",
        "Each of the six exchange account modes reads private state only from its "
        "explicitly mapped demo, testnet, or live account.",
        status="implemented",
    )
)
def test_six_account_modes_construct_only_their_mapped_private_client(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import binance_broker, okx_broker
    from aletheia.execution.reconciliation import reconcile
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    constructed: list[tuple[str, str]] = []

    class _BitgetClient:
        def __init__(self, mode):
            constructed.append(("bitget", mode))

        async def get_open_orders(self, **kwargs):
            return []

        async def get_fills(self, **kwargs):
            return []

        async def close(self):
            pass

    class _OKXExchange:
        async def load_markets(self):
            return None

        def market(self, symbol):
            return {"id": "BTC-USDT-SWAP", "contractSize": 0.01}

        async def private_get_trade_fills_history(self, params):
            return {"code": "0", "data": []}

    class _OKXClient:
        def __init__(self, mode):
            constructed.append(("okx", mode))
            self.exchange = _OKXExchange()

        def _ensure_exchange(self):
            return self.exchange

        async def close(self):
            pass

    class _BinanceExchange:
        async def load_markets(self):
            return None

        async def fetch_my_trades(self, *args, **kwargs):
            return []

    class _BinanceClient:
        def __init__(self, mode):
            constructed.append(("binance", mode))
            self.exchange = _BinanceExchange()

        def _ensure_exchange(self):
            return self.exchange

        async def close(self):
            pass

    monkeypatch.setattr(bitget_client, "BitgetClient", _BitgetClient)
    monkeypatch.setattr(okx_broker, "OKXClient", _OKXClient)
    monkeypatch.setattr(binance_broker, "BinanceClient", _BinanceClient)

    account_modes = (
        "bitget_demo",
        "bitget_live_limited",
        "okx_demo",
        "okx_live_limited",
        "binance_testnet",
        "binance_live_limited",
    )
    for execution_mode in account_modes:
        proposal, decision = _seed_authority(
            execution_mode, suffix=f"account-{execution_mode}"
        )
        repo.insert_order(Order(
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            client_order_id=proposal.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=proposal.quantity,
            price=proposal.price,
            execution_mode=execution_mode,
            exchange_order_id=f"EX-MBR-ACCOUNT-{execution_mode}",
            status="submitted",
        ))

    result = reconcile()

    assert result["checked"] == 6
    assert Counter(constructed) == Counter({
        ("bitget", "demo"): 1,
        ("bitget", "live"): 1,
        ("okx", "demo"): 1,
        ("okx", "live"): 1,
        ("binance", "testnet"): 1,
        ("binance", "live"): 1,
    })


@spec_claim(
    SpecClaim(
        "PL-MBR-11",
        "pipeline_multibroker_reconciliation",
        "§11 account-scoped Fill identity",
        "L2c",
        "The same remote trade id returned by all six exchange account modes becomes "
        "six distinct deterministic local Fill ids while preserving the original "
        "remote identity and replay idempotency.",
        status="implemented",
    )
)
def test_same_remote_trade_id_is_isolated_across_six_accounts(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import binance_broker, okx_broker
    from aletheia.execution.exchange_materialization import (
        durable_private_fill_id,
    )
    from aletheia.execution.reconciliation import reconcile
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    shared_remote_fill_id = "REMOTE-TRADE-SHARED-BY-ACCOUNTS"
    exchange_ids: dict[str, str] = {}
    account_modes = (
        "bitget_demo",
        "bitget_live_limited",
        "okx_demo",
        "okx_live_limited",
        "binance_testnet",
        "binance_live_limited",
    )
    for execution_mode in account_modes:
        proposal, decision = _seed_authority(
            execution_mode, suffix=f"collision-{execution_mode}"
        )
        exchange_order_id = f"EX-MBR-COLLISION-{execution_mode}"
        exchange_ids[execution_mode] = exchange_order_id
        repo.insert_order(Order(
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            client_order_id=proposal.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=proposal.quantity,
            price=proposal.price,
            execution_mode=execution_mode,
            exchange_order_id=exchange_order_id,
            status="submitted",
        ))

    def _same_remote_fill(exchange_order_id: str) -> dict:
        row = _private_fill(exchange_order_id, suffix="unused")
        row["tradeId"] = shared_remote_fill_id
        return row

    class _BitgetClient:
        def __init__(self, mode):
            self.execution_mode = (
                "bitget_demo" if mode == "demo" else "bitget_live_limited"
            )

        async def get_open_orders(self, **kwargs):
            return []

        async def get_fills(self, **kwargs):
            return [_same_remote_fill(exchange_ids[self.execution_mode])]

        async def get_order_fills(self, *, order_id, **kwargs):
            assert order_id == exchange_ids[self.execution_mode]
            return [_same_remote_fill(order_id)]

        async def close(self):
            pass

    async def _okx_evidence(order):
        return [_same_remote_fill(order["exchange_order_id"])]

    async def _binance_evidence(order):
        return [_same_remote_fill(order["exchange_order_id"])]

    monkeypatch.setattr(bitget_client, "BitgetClient", _BitgetClient)
    monkeypatch.setattr(okx_broker, "fetch_private_fill_evidence", _okx_evidence)
    monkeypatch.setattr(
        binance_broker,
        "fetch_private_fill_evidence",
        _binance_evidence,
    )

    first = reconcile()
    fills = [
        dict(row)
        for row in db.execute(
            """
            SELECT fills.*, orders.execution_mode
            FROM fills
            JOIN orders ON orders.id = fills.order_id
            ORDER BY orders.execution_mode
            """
        ).fetchall()
    ]

    assert first["filled"] == 6
    assert len(fills) == 6
    assert len({row["id"] for row in fills}) == 6
    assert {
        row["id"] for row in fills
    } == {
        durable_private_fill_id(mode, shared_remote_fill_id)
        for mode in account_modes
    }
    assert all(row["id"].endswith(f":{shared_remote_fill_id}") for row in fills)

    second = reconcile()
    assert second["checked"] == 0
    assert db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 6


def _seed_verified_open_position(execution_mode: str):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position

    proposal, decision = _seed_authority(
        execution_mode, suffix=f"close-{execution_mode}"
    )
    entry_order = Order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=59_000.0,
        execution_mode=execution_mode,
        exchange_order_id=f"EX-MBR-ENTRY-{execution_mode}",
        status="filled",
        filled_at="2026-07-13T00:00:00+00:00",
    )
    repo.insert_order(entry_order)
    repo.insert_fill(Fill(
        id=f"private-entry-{execution_mode}",
        order_id=entry_order.id,
        fill_price=59_000.0,
        fill_quantity=proposal.quantity,
        fee=0.1,
        fee_currency="USDT",
        fee_source="reconciled",
        filled_at="2026-07-13T00:00:00+00:00",
    ))
    position = Position(
        proposal_id=proposal.id,
        order_id=entry_order.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        quantity=proposal.quantity,
        entry_price=59_000.0,
        current_price=59_000.0,
        entry_fee=0.1,
        execution_mode=execution_mode,
        entry_fill_verified=True,
        entry_price_source="broker_fill",
        opened_at="2026-07-13T00:00:00+00:00",
    )
    repo.insert_position(position)
    repo.update_order(entry_order.id, position_id=position.id)
    repo.update_trade_proposal(proposal.id, status="executed")
    return position


@pytest.mark.parametrize(
    "broker_module,client_name,close_function,execution_mode", CLOSE_BROKERS
)
@spec_claim(
    SpecClaim(
        "PL-MBR-10",
        "pipeline_multibroker_reconciliation",
        "§10 private close materialization",
        "L2b",
        "An OKX or Binance aggregate close response never closes locally; exact "
        "account-matched private fills close the linked Position once with actual "
        "fee, price, and exchange time.",
        status="implemented",
    )
)
def test_close_waits_for_private_fill_then_materializes_once(
    tmp_path,
    monkeypatch,
    broker_module,
    client_name,
    close_function,
    execution_mode,
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    position = _seed_verified_open_position(execution_mode)
    broker = importlib.import_module(broker_module)
    exchange_order_id = f"EX-MBR-CLOSE-{execution_mode}"
    expected_account = {
        "okx_demo": "demo",
        "okx_live_limited": "live",
        "binance_testnet": "testnet",
        "binance_live_limited": "live",
    }[execution_mode]

    class _FilledCloseClient:
        def __init__(self, mode):
            assert mode == expected_account

        async def place_order(self, order):
            assert order.reduce_only is True
            return SimpleNamespace(
                order_id=exchange_order_id,
                status="filled",
                fill_price=60_111.0,
                filled_quantity=0.01,
            )

        async def close(self):
            pass

    monkeypatch.setattr(broker, client_name, _FilledCloseClient)
    close_result = getattr(broker, close_function)(
        position.id, reason="take_profit"
    )
    close_order = repo.get_order(close_result["order_id"])

    assert close_result["status"] == "materialization_missing"
    assert close_result["closed"] is False
    assert repo._select_one("positions", position.id)["status"] == "open"
    assert close_order["status"] == "materialization_missing"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 0

    async def _evidence(stored_order):
        return [_private_fill(
            exchange_order_id, suffix=f"close-{execution_mode}"
        )]

    monkeypatch.setattr(
        broker, "fetch_private_fill_evidence", _evidence, raising=False
    )
    result = reconcile()
    stored_position = repo._select_one("positions", position.id)
    stored_order = repo.get_order(close_order["id"])
    close_fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()

    assert result["filled"] == 1
    assert stored_order["status"] == "filled"
    assert stored_position["status"] == "closed"
    assert stored_position["pending_close_order_id"] is None
    assert stored_position["current_price"] == pytest.approx(60_123.5)
    assert stored_position["exit_fee"] == pytest.approx(0.123456)
    assert stored_position["exit_fill_verified"] == 1
    assert stored_position["exit_price_source"] == "broker_fill"
    assert stored_position["closed_at"] == datetime.fromtimestamp(
        1_784_050_000, tz=timezone.utc
    ).isoformat()
    assert close_fill["fee_source"] == "reconciled"
    evidence = json.loads(stored_position["reconciliation_evidence"])
    assert evidence["source"] == f"{execution_mode}_private_close_fills"
    assert evidence["broker_id"] == execution_mode
    assert evidence["execution_mode"] == execution_mode
    assert "bitget" not in evidence["source"]
    raw_fill_id = f"private-trade-close-{execution_mode}"
    assert evidence["fill_ids"] == [raw_fill_id]
    assert evidence["durable_fill_ids"] == [close_fill["id"]]
    assert evidence["fills"][0]["remote_fill_id"] == raw_fill_id
    assert evidence["fills"][0]["durable_fill_id"] == close_fill["id"]

    second = reconcile()
    assert second["filled"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 1


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
@spec_claim(
    SpecClaim(
        "PL-MBR-03",
        "pipeline_multibroker_reconciliation",
        "§3 unavailable private evidence",
        "L2b",
        "When a private fill query is unavailable, reconciliation keeps the Order "
        "unresolved and creates no Fill or Position from requested economics.",
        status="implemented",
    )
)
def test_private_fill_query_failure_stays_unresolved_and_sanitized(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    proposal, decision = _seed_authority(
        execution_mode, suffix=f"failure-{execution_mode}"
    )
    order = Order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=execution_mode,
        exchange_order_id=f"EX-MBR-FAIL-{execution_mode}",
        status="materialization_missing",
    )
    repo.insert_order(order)
    broker = importlib.import_module(broker_module)
    sensitive = "private-api-secret-must-not-persist"

    async def _query_failure(stored_order):
        raise RuntimeError(sensitive)

    monkeypatch.setattr(
        broker, "fetch_private_fill_evidence", _query_failure, raising=False
    )

    result = reconcile()

    assert repo.get_order(order.id)["status"] == "materialization_missing"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert sensitive not in repr(result)
    audit_text = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert sensitive not in audit_text


@spec_claim(
    SpecClaim(
        "PL-MBR-04",
        "pipeline_multibroker_reconciliation",
        "§4 venue evidence normalization",
        "L2a",
        "The broker-owned readers select the mapped demo, testnet, or live account and normalize "
        "only complete, order-matched private fills with actual fee and time.",
        status="implemented",
    )
)
@pytest.mark.parametrize("execution_mode,account_mode", OKX_ACCOUNTS)
def test_okx_private_reader_normalizes_contract_quantity_and_actual_fee(
    monkeypatch, execution_mode, account_mode
):
    from aletheia.execution import okx_broker

    calls: list[dict] = []

    class _Exchange:
        async def load_markets(self):
            return None

        def market(self, symbol):
            assert symbol == "BTC/USDT:USDT"
            return {"id": "BTC-USDT-SWAP", "contractSize": 0.01}

        async def private_get_trade_fills_history(self, params):
            calls.append(params)
            return {
                "code": "0",
                "data": [{
                    "ordId": "OKX-PRIVATE-1",
                    "billId": "OKX-FILL-1",
                    "clOrdId": "proposalmbr",
                    "fillPx": "60123.5",
                    "fillSz": "1",
                    "fee": "-0.123456",
                    "feeCcy": "USDT",
                    "ts": "1784050000000",
                }],
            }

    class _Client:
        def __init__(self, mode):
            assert mode == account_mode
            self.exchange = _Exchange()

        def _ensure_exchange(self):
            return self.exchange

        async def close(self):
            pass

    monkeypatch.setattr(okx_broker, "OKXClient", _Client)
    rows = asyncio.run(okx_broker.fetch_private_fill_evidence({
        "execution_mode": execution_mode,
        "exchange_order_id": "OKX-PRIVATE-1",
        "market_type": "usdt_futures",
        "symbol": "BTC/USDT",
        "created_at": "2026-07-13T00:00:00+00:00",
    }))

    assert rows == [{
        "orderId": "OKX-PRIVATE-1",
        "tradeId": "OKX-FILL-1",
        "clientOid": "proposalmbr",
        "fillPrice": "60123.5",
        "fillQuantity": "0.01",
        "fee": "-0.123456",
        "feeCoin": "USDT",
        "cTime": "1784050000000",
    }]
    assert calls[0]["instType"] == "SWAP"
    assert calls[0]["instId"] == "BTC-USDT-SWAP"


@spec_claim(
    SpecClaim(
        "PL-MBR-12",
        "pipeline_multibroker_reconciliation",
        "§4 venue evidence normalization",
        "L2a",
        "The broker-owned readers select the mapped demo, testnet, or live account and normalize "
        "only complete, order-matched private fills with actual fee and time.",
        status="implemented",
    )
)
@pytest.mark.parametrize("execution_mode,account_mode", BINANCE_ACCOUNTS)
def test_binance_private_reader_normalizes_order_matched_actual_fee(
    monkeypatch, execution_mode, account_mode
):
    from aletheia.execution import binance_broker

    calls: list[dict] = []

    class _Exchange:
        async def load_markets(self):
            return None

        async def fetch_my_trades(self, symbol, since, limit, params):
            calls.append({
                "symbol": symbol,
                "since": since,
                "limit": limit,
                "params": params,
            })
            return [{
                "id": "BINANCE-FILL-1",
                "order": "BINANCE-PRIVATE-1",
                "price": 60_123.5,
                "amount": 0.01,
                "timestamp": 1_784_050_000_000,
                "fee": {"cost": 0.123456, "currency": "USDT"},
                "info": {"clientOrderId": "proposal-mbr"},
            }]

    class _Client:
        def __init__(self, mode):
            assert mode == account_mode
            self.exchange = _Exchange()

        def _ensure_exchange(self):
            return self.exchange

        async def close(self):
            pass

    monkeypatch.setattr(binance_broker, "BinanceClient", _Client)
    rows = asyncio.run(binance_broker.fetch_private_fill_evidence({
        "execution_mode": execution_mode,
        "exchange_order_id": "BINANCE-PRIVATE-1",
        "market_type": "usdt_futures",
        "symbol": "BTC/USDT",
        "created_at": "2026-07-13T00:00:00+00:00",
    }))

    assert rows == [{
        "orderId": "BINANCE-PRIVATE-1",
        "tradeId": "BINANCE-FILL-1",
        "clientOid": "proposal-mbr",
        "fillPrice": "60123.5",
        "fillQuantity": "0.01",
        "fee": "0.123456",
        "feeCoin": "USDT",
        "cTime": "1784050000000",
    }]
    assert calls[0]["symbol"] == "BTC/USDT:USDT"
    assert calls[0]["limit"] == 1000
