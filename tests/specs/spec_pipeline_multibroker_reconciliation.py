"""OKX and Binance account-explicit private-fill reconciliation contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


MODES = ("okx_demo", "okx_live_limited", "binance_testnet", "binance_live_limited")

OKX_ACCOUNTS = (
    pytest.param("okx_demo", "demo", id="okx-demo-account"),
    pytest.param("okx_live_limited", "live", id="okx-live-account"),
)

BINANCE_ACCOUNTS = (
    pytest.param("binance_testnet", "testnet", id="binance-testnet-account"),
    pytest.param("binance_live_limited", "live", id="binance-live-account"),
)

from tests.uta.test_execution_coordinator import _identity
from tests.test_exchange_submission_integrity import _prepare_broker, _report


def _patch_private_evidence(monkeypatch, evidence):
    from aletheia.execution import reconciliation
    from aletheia.storage import repositories as repo
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import (
        FillListReport, FillReport, OrderListReport, OrderQueryReport, FundingLedgerReport, ReadCompleteness,
    )

    modes = (*MODES, "bitget_demo", "bitget_live_limited")
    monkeypatch.setattr(reconciliation, "build_uta_registry", lambda: SimpleNamespace(
        configured_identities=lambda: tuple(_identity(mode) for mode in modes)))
    async def query(mode, method, request, *, expected_account):
        assert expected_account == _identity(mode)
        now = datetime.now(timezone.utc)
        envelope = dict(identity=expected_account, completeness=ReadCompleteness.COMPLETE,
                        scope=request.scope, received_at=now, profile_version="1")
        if method == "get_open_orders":
            return OrderListReport(**envelope)
        if method == "get_order":
            return OrderQueryReport(**envelope)
        if method == "get_funding_ledger":
            return FundingLedgerReport(**{**envelope, "completeness": ReadCompleteness.UNAVAILABLE,
                                         "reason_code": "funding_not_in_fixture"})
        assert method == "get_fills"
        fills = []
        for order in repo.list_orders(limit=1000):
            if order["execution_mode"] != mode or order["status"] == "filled":
                continue
            if request.venue_order_id and request.venue_order_id != order["exchange_order_id"]:
                continue
            if request.client_order_id and request.client_order_id != order["client_order_id"]:
                continue
            instrument = instrument_from_local(order["symbol"], order["market_type"])
            if request.scope.instrument and request.scope.instrument != instrument:
                continue
            for row in await evidence(order):
                fills.append(FillReport(identity=expected_account, instrument=instrument,
                    venue_order_id=row["orderId"], client_order_id=order["client_order_id"],
                    venue_fill_id=row["tradeId"], side="sell" if order["close_reason"] else "buy",
                    quantity=row["fillQuantity"], price=row["fillPrice"], fee=row["fee"],
                    fee_currency=row["feeCoin"],
                    executed_at=datetime.fromtimestamp(int(row["cTime"]) / 1000, tz=timezone.utc), received_at=now))
        return FillListReport(**envelope, fills=tuple(fills))

    monkeypatch.setattr(reconciliation, "query_uta", query)


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


@pytest.mark.parametrize("execution_mode", MODES)
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
def test_delayed_private_fill_materializes_actual_economics(tmp_path, monkeypatch, execution_mode):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.execution.exchange_materialization import (
        durable_private_fill_id,
    )
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    proposal, decision = _seed_authority(execution_mode, suffix=execution_mode)
    exchange_order_id = f"EX-MBR-{execution_mode}"
    order = Order(proposal_id=proposal.id,
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
    status="submitted", uta_account_identity_json=_identity(execution_mode).model_dump_json())
    repo.insert_order(order)

    async def _evidence(stored_order):
        assert stored_order["id"] == order.id
        return [_private_fill(exchange_order_id, suffix=execution_mode)]

    _patch_private_evidence(monkeypatch, _evidence)

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


@pytest.mark.parametrize("execution_mode", MODES)
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
def test_reported_filled_waits_for_private_fill_before_finalizing(tmp_path, monkeypatch, execution_mode):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    proposal, decision = _seed_authority(
        execution_mode, suffix=f"sync-{execution_mode}"
    )
    exchange_order_id = f"EX-MBR-SYNC-{execution_mode}"

    async def _filled(command):
        assert command.expected_account == _identity(execution_mode)
        return _report(command, venue_order_id=exchange_order_id, state="filled", filled_quantity="0.01")

    broker = _prepare_broker(monkeypatch, _filled)

    opened = broker.execute_order(
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

    _patch_private_evidence(monkeypatch, _evidence)
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
@pytest.mark.parametrize("execution_mode", MODES)
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
def test_verified_averaging_leg_does_not_upgrade_unverified_history(tmp_path,
monkeypatch, execution_mode,
old_verified,
old_source,):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Position

    # Known account identity is independent of private-fill verification. Keep
    # the old economics unverified, but prove which account owns that exposure.
    old_proposal, old_decision = _seed_authority(execution_mode, suffix=f"old-average-{execution_mode}")
    old_order = Order(
        proposal_id=old_proposal.id,
        risk_decision_id=old_decision.id,
        client_order_id=old_proposal.id,
        symbol=old_proposal.symbol,
        market_type=old_proposal.market_type,
        direction=old_proposal.direction,
        order_type=old_proposal.order_type,
        quantity=old_proposal.quantity,
        price=old_proposal.price,
        execution_mode=execution_mode,
        status="filled",
        uta_account_identity_json=_identity(execution_mode).model_dump_json(),
    )
    repo.insert_order(old_order)
    old_position = Position(
        id=f"position-unverified-{execution_mode}",
        order_id=old_order.id,
        proposal_id=old_proposal.id,
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
    order = Order(proposal_id=proposal.id,
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
    status="materialization_missing", uta_account_identity_json=_identity(execution_mode).model_dump_json())
    repo.insert_order(order)
    async def _evidence(stored_order):
        return [_private_fill(exchange_order_id, suffix=f"average-{execution_mode}")]

    _patch_private_evidence(monkeypatch, _evidence)

    result = reconcile()

    position = db.execute(
        "SELECT * FROM positions WHERE id=?", (old_position.id,)
    ).fetchone()
    assert result["filled"] == 1
    assert position["quantity"] == pytest.approx(0.02)
    assert position["entry_price"] == pytest.approx((59_000.0 + 60_123.5) / 2)
    assert position["entry_fill_verified"] == 0
    assert position["entry_price_source"] == old_source


@pytest.mark.parametrize("execution_mode", MODES)
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
def test_unmatched_estimated_fill_blocks_position_verification(tmp_path, monkeypatch, execution_mode):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order

    proposal, decision = _seed_authority(
        execution_mode, suffix=f"estimated-conflict-{execution_mode}"
    )
    exchange_order_id = f"EX-MBR-ESTIMATED-{execution_mode}"
    order = Order(proposal_id=proposal.id,
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
    status="materialization_missing", uta_account_identity_json=_identity(execution_mode).model_dump_json())
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
    async def _evidence(stored_order):
        return [_private_fill(
            exchange_order_id, suffix=f"estimated-conflict-{execution_mode}"
        )]

    _patch_private_evidence(monkeypatch, _evidence)

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
        order = Order(proposal_id=proposal.id,
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
        status="materialization_missing", uta_account_identity_json=_identity(execution_mode).model_dump_json())
        repo.insert_order(order)
        orders.append(order)
    async def _evidence(stored_order):
        return [_private_fill(stored_order["exchange_order_id"],
                              suffix=f"scheduled-{stored_order['execution_mode']}")]
    _patch_private_evidence(monkeypatch, _evidence)
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
    from aletheia.execution import reconciliation

    async def _unexpected_query(*args, **kwargs):
        pytest.fail("empty reconciliation must not query an account")

    monkeypatch.setattr(reconciliation, "query_uta", _unexpected_query)
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
def test_six_account_modes_query_only_their_mapped_uta_account(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation as recon
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    queried: list[str] = []

    async def _empty(order):
        queried.append(order["execution_mode"])
        return []

    _patch_private_evidence(monkeypatch, _empty)

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
        repo.insert_order(Order(proposal_id=proposal.id,
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
        status="submitted", uta_account_identity_json=_identity(execution_mode).model_dump_json()))

    result = recon.reconcile()

    assert result["checked"] == 6
    assert set(queried) == set(account_modes)


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
    from aletheia.execution import reconciliation as recon
    from aletheia.execution.exchange_materialization import (
        durable_private_fill_id,
    )
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
        repo.insert_order(Order(proposal_id=proposal.id,
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
        status="submitted", uta_account_identity_json=_identity(execution_mode).model_dump_json()))

    def _same_remote_fill(exchange_order_id: str) -> dict:
        row = _private_fill(exchange_order_id, suffix="unused")
        row["tradeId"] = shared_remote_fill_id
        return row

    async def _fills(order):
        return [_same_remote_fill(exchange_ids[order["execution_mode"]])]

    _patch_private_evidence(monkeypatch, _fills)

    first = recon.reconcile()
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

    second = recon.reconcile()
    assert second["checked"] == 0
    assert db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 6


def _seed_verified_open_position(execution_mode: str):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position

    proposal, decision = _seed_authority(
        execution_mode, suffix=f"close-{execution_mode}"
    )
    entry_order = Order(proposal_id=proposal.id,
    risk_decision_id=decision.id,
    client_order_id=proposal.id,
    symbol=proposal.symbol,
    market_type=proposal.market_type,
    direction=proposal.direction,
    order_type=proposal.order_type,
    quantity=proposal.quantity,
    # Exchange Order keeps the submitted Proposal price; the private Fill
    # below records the venue's different execution price.
    price=proposal.price,
    execution_mode=execution_mode,
    exchange_order_id=f"EX-MBR-ENTRY-{execution_mode}",
    status="filled",
    filled_at="2026-07-13T00:00:00+00:00", uta_account_identity_json=_identity(execution_mode).model_dump_json())
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
    repo.update_order_metadata(entry_order.id, position_id=position.id)
    repo.update_trade_proposal(proposal.id, status="executed")
    return position


@pytest.mark.parametrize("execution_mode", MODES)
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
def test_close_waits_for_private_fill_then_materializes_once(tmp_path,
monkeypatch, execution_mode,):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.portfolio.position_manager import close_position_routed
    from aletheia.storage import repositories as repo

    position = _seed_verified_open_position(execution_mode)
    exchange_order_id = f"EX-MBR-CLOSE-{execution_mode}"
    async def _filled_close(command):
        assert command.expected_account == _identity(execution_mode)
        assert command.reduce_only is True
        return _report(command, venue_order_id=exchange_order_id, state="filled", filled_quantity="0.01")

    _prepare_broker(monkeypatch, _filled_close)
    decision_reasoning = "Momentum reversed while breadth weakened."
    close_result = close_position_routed(
        position.id,
        reason="take_profit",
        decision_reasoning=decision_reasoning,
        decision_actor="system_operator",
        decision_source="portfolio_review",
        allow_live=execution_mode.endswith("_live_limited"),
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

    _patch_private_evidence(monkeypatch, _evidence)
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
    exit_reasoning = json.loads(stored_position["exit_reasoning"])
    assert exit_reasoning["actor"] == "system_operator"
    assert exit_reasoning["source"] == "portfolio_review"
    assert exit_reasoning["reason"] == decision_reasoning
    assert exit_reasoning["trigger"]["exit_price"] == pytest.approx(60_123.5)
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
    audit = db.execute(
        "SELECT actor, source FROM audit_events "
        "WHERE event_type='position_closed' AND entity_id=?",
        (position.id,),
    ).fetchone()
    assert tuple(audit) == ("system_operator", "portfolio_review")
    assert evidence["fills"][0]["durable_fill_id"] == close_fill["id"]
    exit_reasoning_before_replay = stored_position["exit_reasoning"]

    second = reconcile()
    assert second["filled"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 1
    assert repo._select_one("positions", position.id)["exit_reasoning"] == (
        exit_reasoning_before_replay
    )


@pytest.mark.parametrize("execution_mode", MODES)
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
def test_private_fill_query_failure_stays_unresolved_and_sanitized(tmp_path, monkeypatch, execution_mode):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    proposal, decision = _seed_authority(
        execution_mode, suffix=f"failure-{execution_mode}"
    )
    order = Order(proposal_id=proposal.id,
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
    status="materialization_missing", uta_account_identity_json=_identity(execution_mode).model_dump_json())
    repo.insert_order(order)
    sensitive = "private-api-secret-must-not-persist"

    async def _query_failure(stored_order):
        raise RuntimeError(sensitive)

    _patch_private_evidence(monkeypatch, _query_failure)

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

def _read_profile_fills(monkeypatch, execution_mode, profile, payload, venue_order_id, contract_size):
    import ccxt.async_support as ccxt
    from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import FillQuery, QueryScope
    from tests.uta.test_ccxt_adapter import _install

    exchange = getattr(ccxt, profile.exchange_name)()
    exchange.set_markets([{
        "id": "BTC-USDT-SWAP" if profile.expected_venue == "okx" else "BTCUSDT",
        "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT", "settle": "USDT",
        "type": "swap", "spot": False, "swap": True, "future": False, "option": False,
        "contract": True, "linear": True, "inverse": False, "contractSize": contract_size,
    }])
    calls = []

    async def load(*args, **kwargs):
        return exchange.markets

    async def request(path, api, method, params, *args, **kwargs):
        calls.append((path, params))
        return payload

    exchange.load_markets = load
    exchange.request = request
    _install(monkeypatch, exchange)
    identity = _identity(execution_mode).model_copy(update={"profile": profile.expected_profile})

    async def run():
        adapter = CCXTUTAAdapter(identity, profile)
        monkeypatch.setattr(adapter, "_now", lambda: datetime.fromtimestamp(1784050060, tz=timezone.utc))
        try:
            return await adapter.get_fills(FillQuery(
                scope=QueryScope(instrument=instrument_from_local("BTC/USDT", "usdt_futures"),
                    start_at=datetime.fromtimestamp(1784049900, tz=timezone.utc),
                    end_at=datetime.fromtimestamp(1784050060, tz=timezone.utc)),
                venue_order_id=venue_order_id))
        finally:
            await adapter.close()

    return asyncio.run(run()), calls

@spec_claim(
    SpecClaim(
        "PL-MBR-04",
        "pipeline_multibroker_reconciliation",
        "§4 venue evidence normalization",
        "L2a",
        "UTA profiles select the mapped demo, testnet, or live account and normalize "
        "only complete, order-matched private fills with actual fee and time.",
        status="implemented",
    )
)
@pytest.mark.parametrize("execution_mode,account_mode", OKX_ACCOUNTS)
def test_okx_private_reader_normalizes_contract_quantity_and_actual_fee(
    monkeypatch, execution_mode, account_mode
):
    from decimal import Decimal
    from extensions.exchanges.okx.profile import OKXDemoProfile, OKXLiveProfile
    from aletheia.uta.models import ReadCompleteness

    profile_type = OKXDemoProfile if account_mode == "demo" else OKXLiveProfile
    profile = profile_type(api_key="isolated", secret="isolated", passphrase="isolated")
    report, calls = _read_profile_fills(monkeypatch, execution_mode, profile, {
        "code": "0", "data": [{
            "instId": "BTC-USDT-SWAP", "instType": "SWAP", "side": "buy",
            "ordId": "OKX-PRIVATE-1", "billId": "OKX-FILL-1", "tradeId": "OKX-FILL-1",
            "clOrdId": "proposalmbr", "fillPx": "60123.5", "fillSz": "1",
            "fee": "-0.123456", "feeCcy": "USDT", "ts": "1784050000000",
        }]}, "OKX-PRIVATE-1", 0.01)
    assert report.completeness is ReadCompleteness.COMPLETE
    assert report.identity.execution_mode == execution_mode
    [fill] = report.fills
    assert fill.venue_order_id == "OKX-PRIVATE-1"
    assert fill.quantity == Decimal("0.01")
    assert fill.price == Decimal("60123.5")
    assert fill.fee == Decimal("0.123456")
    assert fill.fee_currency == "USDT"
    assert fill.executed_at == datetime.fromtimestamp(1784050000, tz=timezone.utc)
    assert calls[0][1]["instId"] == "BTC-USDT-SWAP"


@spec_claim(
    SpecClaim(
        "PL-MBR-12",
        "pipeline_multibroker_reconciliation",
        "§4 venue evidence normalization",
        "L2a",
        "UTA profiles select the mapped demo, testnet, or live account and normalize "
        "only complete, order-matched private fills with actual fee and time.",
        status="implemented",
    )
)
@pytest.mark.parametrize("execution_mode,account_mode", BINANCE_ACCOUNTS)
def test_binance_private_reader_normalizes_order_matched_actual_fee(
    monkeypatch, execution_mode, account_mode
):
    from decimal import Decimal
    from extensions.exchanges.binance.profile import BinanceTestnetProfile, BinanceLiveProfile
    from aletheia.uta.models import ReadCompleteness

    profile_type = BinanceTestnetProfile if account_mode == "testnet" else BinanceLiveProfile
    profile = profile_type(api_key="isolated", secret="isolated")
    report, calls = _read_profile_fills(monkeypatch, execution_mode, profile, [{
        "id": "42", "orderId": "BINANCE-PRIVATE-1", "symbol": "BTCUSDT", "side": "BUY",
        "price": "60123.5", "qty": "0.01", "time": 1784050000000,
        "commission": "0.123456", "commissionAsset": "USDT", "buyer": True, "maker": False,
    }], "BINANCE-PRIVATE-1", 1)
    assert report.completeness is ReadCompleteness.COMPLETE
    assert report.identity.execution_mode == execution_mode
    [fill] = report.fills
    assert fill.venue_order_id == "BINANCE-PRIVATE-1"
    assert fill.venue_fill_id == "42"
    assert fill.quantity == Decimal("0.01")
    assert fill.price == Decimal("60123.5")
    assert fill.fee == Decimal("0.123456")
    assert fill.fee_currency == "USDT"
    assert fill.executed_at == datetime.fromtimestamp(1784050000, tz=timezone.utc)
    assert calls[0][1]["symbol"] == "BTCUSDT"
