"""Real-path regressions for ambiguous submission and local materialization."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import ccxt
import httpx
import pytest

from datetime import datetime, timezone
from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import reset_db
from tests.uta.test_execution_coordinator import _insert_bound_position
from aletheia.uta.models import SubmissionReport, CommandEvidence, OrderStatusReport, OrderLifecycle
from tests.fill_reports import reports


def _report(command, *, venue_order_id, state="open", filled_quantity=0, evidence=CommandEvidence.ACKNOWLEDGED, reason_code=None):
    return SubmissionReport(identity=command.expected_account, command_id=command.command_id,
        evidence=evidence, client_order_id=command.client_order_id, venue_order_id=venue_order_id or None,
        reason_code=reason_code, received_at=datetime.now(timezone.utc), profile_version="1",
        order=OrderStatusReport(identity=command.expected_account, instrument=command.instrument,
            side=command.side, order_type=command.order_type, original_quantity=command.quantity,
            state=OrderLifecycle(state), filled_quantity=str(filled_quantity),
            received_at=datetime.now(timezone.utc)))

MODES = ("bitget_demo", "okx_demo", "binance_testnet")


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    conn = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def _prepare_broker(monkeypatch, place_order):
    from aletheia.execution import coordinator as broker
    from tests.uta.test_execution_coordinator import _Adapter
    monkeypatch.setattr(broker, "create_uta_adapter", lambda mode: _Adapter(place_order, mode))
    monkeypatch.setattr("aletheia.execution.order_constraints.prepare_exchange_quantity", lambda mode, symbol, market, qty, price: qty)
    return broker


def _execute(**kwargs):
    from aletheia.storage import repositories as repo
    from tests.execution_authority import seed_order_authority
    from aletheia.execution.coordinator import execute_order

    if repo.get_trade_proposal(kwargs["proposal_id"]) is None:
        seed_order_authority(**kwargs)
    return execute_order(**kwargs)


def _private_fill(
    exchange_order_id: str,
    client_order_id: str,
    *,
    quantity: float,
    suffix: str,
):
    return reports([{
        "orderId": exchange_order_id,
        "clientOid": client_order_id,
        "tradeId": f"private-trade-{suffix}",
        "fillPrice": "101.5",
        "fillQuantity": str(quantity),
        "fee": "0.01",
        "feeCoin": "USDT",
        "cTime": "1784050000000",
    }], symbol="BTC/USDT", market_type="spot")[0]


@pytest.mark.parametrize("execution_mode", MODES)
@pytest.mark.parametrize(
    "fill_quantity,expected_status,expected_proposal_status,late_outcome",
    [
        pytest.param(0.01, "filled", "executed", "ack", id="full"),
        pytest.param(
            0.004,
            "partially_filled",
            "partially_filled",
            "ack",
            id="partial",
        ),
        pytest.param(
            0.01, "filled", "executed", "ambiguous", id="ambiguous-finalizer",
        ),
        pytest.param(
            0.01, "filled", "executed", "rejected", id="rejection-finalizer",
        ),
        pytest.param(
            0.01, "filled", "executed", "missing-id", id="missing-id-finalizer",
        ),
        pytest.param(
            0.01, "filled", "executed", "terminal", id="terminal-finalizer",
        ),
    ],
)
def test_late_entry_ack_preserves_private_materialization_winner(tmp_path,
monkeypatch, execution_mode,
fill_quantity,
expected_status,
expected_proposal_status,
late_outcome,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_entry
    from aletheia.storage import repositories as repo

    entered_exchange = threading.Event()
    release_exchange = threading.Event()
    case_id = expected_status if late_outcome == "ack" else late_outcome
    proposal_id = f"proposal-{execution_mode}-late-entry-{case_id}"
    exchange_order_id = f"EX-LATE-ENTRY-{execution_mode}-{case_id}"

    async def _late_ack(order):
        entered_exchange.set()
        assert release_exchange.wait(timeout=5)
        if late_outcome == "ambiguous":
            raise ConnectionResetError("late transport failure")
        if late_outcome == "rejected":
            raise RuntimeError("late definitive rejection")
        return _report(order, venue_order_id=exchange_order_id, state="canceled" if late_outcome == "terminal" else "accepted", filled_quantity=0,
                       evidence=CommandEvidence.AMBIGUOUS if late_outcome == "missing-id" else CommandEvidence.ACKNOWLEDGED)

    broker = _prepare_broker(monkeypatch, _late_ack)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _execute,
            proposal_id=proposal_id,
            risk_decision_id=f"risk-{execution_mode}-{case_id}",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=100.0,
            execution_mode=execution_mode,
        )
        assert entered_exchange.wait(timeout=5)
        [pending] = repo.list_orders(limit=10)
        winner = materialize_bitget_entry(
            pending["id"],
            [_private_fill(
                exchange_order_id,
                proposal_id,
                quantity=fill_quantity,
                suffix=f"entry-{execution_mode}-{case_id}",
            )],
        )
        assert winner.state == expected_status
        release_exchange.set()
        result = future.result(timeout=10)

    stored_order = repo.get_order(pending["id"])
    stored_position = repo.get_position(stored_order["position_id"])
    [stored_fill] = repo.list_fills(limit=10)
    assert stored_order["status"] == expected_status
    assert stored_order["exchange_order_id"] == exchange_order_id
    assert stored_position["status"] == "open"
    assert repo.get_trade_proposal(proposal_id)["status"] == expected_proposal_status
    assert result["order_status"] == expected_status
    assert result["exchange_order_id"] == exchange_order_id
    assert result["fill_id"] == stored_fill["id"]
    assert result["position_id"] == stored_position["id"]


@pytest.mark.parametrize("execution_mode", MODES)
@pytest.mark.parametrize(
    "late_outcome",
    ["ack", "ambiguous", "rejected", "missing-id"],
    ids=[
        "ack",
        "ambiguous-finalizer",
        "rejection-finalizer",
        "missing-id-finalizer",
    ],
)
def test_late_close_ack_preserves_private_materialization_winner(tmp_path,
monkeypatch, execution_mode, late_outcome,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    entered_exchange = threading.Event()
    release_exchange = threading.Event()
    exchange_order_id = f"EX-LATE-CLOSE-{execution_mode}"

    async def _late_ack(order):
        entered_exchange.set()
        assert release_exchange.wait(timeout=5)
        if late_outcome == "ambiguous":
            raise ConnectionResetError("late close transport failure")
        if late_outcome == "rejected":
            raise RuntimeError("late definitive close rejection")
        return _report(order, venue_order_id=exchange_order_id, state="accepted", filled_quantity=0,
                       evidence=CommandEvidence.AMBIGUOUS if late_outcome == "missing-id" else CommandEvidence.ACKNOWLEDGED)

    broker = _prepare_broker(monkeypatch, _late_ack)
    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    _insert_bound_position(position)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            broker.close_position, position.id, "take_profit",
        )
        assert entered_exchange.wait(timeout=5)
        [pending] = [row for row in repo.list_orders(limit=10) if row["close_reason"]]
        winner = materialize_bitget_close(
            pending["id"],
            [_private_fill(
                exchange_order_id,
                f"close-{position.id}",
                quantity=0.01,
                suffix=f"close-{execution_mode}",
            )],
        )
        assert winner.state == "filled"
        release_exchange.set()
        result = future.result(timeout=10)

    stored_order = repo.get_order(pending["id"])
    stored_position = repo.get_position(position.id)
    assert stored_order["status"] == "filled"
    assert stored_order["exchange_order_id"] == exchange_order_id
    assert stored_position["status"] == "closed"
    assert stored_position["pending_close_order_id"] is None
    assert stored_position["pending_close_status"] is None
    assert result["status"] == "filled"
    assert result["closed"] is True
    assert len(repo.list_fills(limit=10)) == 1


def test_exchange_ack_cas_accepts_pending_and_rejects_conflicting_identity(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    pending = Order(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=100.0,
        execution_mode="bitget_demo",
        source="adopted",
    )
    repo.insert_order(pending)
    applied = repo.finalize_exchange_acknowledgement(
        pending.id,
        exchange_order_id="EX-NORMAL-ACK",
        status="submitted",
        error_reason=None,
    )
    assert applied["applied"] is True
    assert applied["order_status"] == "submitted"
    assert applied["exchange_order_id"] == "EX-NORMAL-ACK"

    repo.transition_order(pending.id, status="filled")
    with pytest.raises(ValueError, match="identity conflicts"):
        repo.finalize_exchange_acknowledgement(
            pending.id,
            exchange_order_id="EX-CONFLICTING-ACK",
            status="submitted",
            error_reason=None,
        )
    assert repo.get_order(pending.id)["status"] == "filled"
    assert repo.get_order(pending.id)["exchange_order_id"] == "EX-NORMAL-ACK"


@pytest.mark.parametrize("execution_mode", MODES)
def test_open_transport_failure_is_unknown_and_direct_retry_is_suppressed(tmp_path, monkeypatch, execution_mode,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo

    calls = []

    async def _connection_lost(order):
        calls.append(order.client_order_id)
        raise ConnectionResetError("private-transport-detail")

    broker = _prepare_broker(monkeypatch, _connection_lost)
    kwargs = dict(
        proposal_id="proposal-transport-unknown",
        risk_decision_id="risk-1",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode=execution_mode,
    )

    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    first = repo.list_orders(limit=10)
    assert len(first) == 1
    assert first[0]["status"] == "submission_unknown"
    assert first[0]["client_order_id"] == "proposal-transport-unknown"
    assert "private-transport-detail" not in (first[0]["error_reason"] or "")

    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    assert calls == ["proposal-transport-unknown"]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize("execution_mode", MODES)
def test_explicit_api_rejection_remains_known_error(tmp_path, monkeypatch, execution_mode,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    async def _api_rejected(order):
        return _report(order, venue_order_id=None, evidence=CommandEvidence.VENUE_REJECTED, reason_code="venue_rejected")

    broker = _prepare_broker(monkeypatch, _api_rejected)
    with pytest.raises(RuntimeError, match="venue_rejected"):
        _execute(
            proposal_id="proposal-api-rejected",
            risk_decision_id="risk-1",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60000.0,
            execution_mode=execution_mode,
        )

    orders = repo.list_orders(limit=10)
    assert len(orders) == 1
    assert orders[0]["status"] == "submission_rejected"
    assert orders[0]["error_reason"] == "venue_rejected"
    assert "explicit exchange rejection" not in orders[0]["error_reason"]


@pytest.mark.parametrize("execution_mode", MODES[1:])
@pytest.mark.parametrize("exchange_status", ("accepted", "open"))
def test_okx_binance_known_open_ack_statuses_remain_submitted(tmp_path, monkeypatch, execution_mode, exchange_status,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    async def _acknowledged_open(order):
        return _report(order, venue_order_id="EX-KNOWN-OPEN-ACK", state=exchange_status)

    broker = _prepare_broker(monkeypatch, _acknowledged_open)
    result = _execute(
        proposal_id="proposal-known-open-ack",
        risk_decision_id="risk-known-open-ack",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode=execution_mode,
    )

    [order] = repo.list_orders(limit=10)
    assert result["order_status"] == "submitted"
    assert order["status"] == "submitted"
    assert order["exchange_order_id"] == "EX-KNOWN-OPEN-ACK"
    assert order["error_reason"] is None
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(status="open", limit=10) == []


@pytest.mark.parametrize("execution_mode", MODES[1:])
@pytest.mark.parametrize("exchange_status", ("canceled", "rejected", "expired"))
def test_okx_binance_terminal_ack_statuses_remain_cancelled(tmp_path, monkeypatch, execution_mode, exchange_status,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    audits = []

    async def _terminal_ack(order):
        return _report(order, venue_order_id="EX-TERMINAL-ACK", state=exchange_status)

    broker = _prepare_broker(monkeypatch, _terminal_ack)
    monkeypatch.setattr(broker, "write_post_commit_audit", audits.append)
    result = _execute(
        proposal_id="proposal-terminal-ack",
        risk_decision_id="risk-terminal-ack",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode=execution_mode,
    )

    [order] = repo.list_orders(limit=10)
    assert result["order_status"] == "cancelled"
    assert order["status"] == "cancelled"
    assert order["exchange_order_id"] == "EX-TERMINAL-ACK"
    assert order["error_reason"] == "explicit_venue_rejection"
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(status="open", limit=10) == []


@pytest.mark.parametrize("execution_mode", MODES[1:])
def test_okx_binance_unknown_ack_status_is_submission_unknown_and_not_retried(tmp_path, monkeypatch, execution_mode,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo

    audits = []
    calls = []

    async def _unknown_ack(order):
        calls.append(order.client_order_id)
        return _report(order, venue_order_id="EX-UNKNOWN-ACK", state="unknown")

    broker = _prepare_broker(monkeypatch, _unknown_ack)
    monkeypatch.setattr(broker, "write_post_commit_audit", audits.append)
    kwargs = dict(
        proposal_id="proposal-unknown-ack",
        risk_decision_id="risk-unknown-ack",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode=execution_mode,
    )

    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    [order] = repo.list_orders(limit=10)
    assert order["status"] == "submission_unknown"
    assert order["exchange_order_id"] == "EX-UNKNOWN-ACK"
    assert order["error_reason"] == "unrecognized_venue_order_status"
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(status="open", limit=10) == []

    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    assert calls == ["proposal-unknown-ack"]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize("execution_mode", MODES)
@pytest.mark.parametrize(
    "error_factory",
    [
        pytest.param(
            lambda: ccxt.ExchangeNotAvailable("HTTP 503 after create-order"),
            id="ccxt-exchange-unavailable",
        ),
        pytest.param(
            lambda: ccxt.DDoSProtection("venue response unavailable"),
            id="ccxt-network-subclass",
        ),
        pytest.param(
            lambda: ccxt.BadResponse("create-order acknowledgement unusable"),
            id="ccxt-bad-response",
        ),
        pytest.param(
            lambda: ccxt.NullResponse("create-order acknowledgement empty"),
            id="ccxt-null-response",
        ),
        pytest.param(
            lambda: httpx.HTTPStatusError(
                "server error after submission",
                request=httpx.Request("POST", "https://exchange.invalid/order"),
                response=httpx.Response(
                    503,
                    request=httpx.Request(
                        "POST", "https://exchange.invalid/order"
                    ),
                ),
            ),
            id="http-5xx",
        ),
        pytest.param(
            lambda: json.JSONDecodeError("unreadable acknowledgement", "", 0),
            id="response-unreadable",
        ),
    ],
)
def test_ambiguous_exchange_response_is_unknown_and_retry_suppressed(tmp_path,
monkeypatch, execution_mode,
error_factory,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo

    calls: list[str] = []

    async def _ambiguous_response(order):
        calls.append(order.client_order_id)
        raise error_factory()

    broker = _prepare_broker(monkeypatch, _ambiguous_response)
    kwargs = dict(
        proposal_id=f"proposal-{execution_mode}-ambiguous-response",
        risk_decision_id=f"risk-{execution_mode}-ambiguous-response",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode=execution_mode,
    )

    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    [stored] = repo.list_orders(limit=10)
    assert stored["status"] == "submission_unknown"

    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    assert calls == [kwargs["proposal_id"]]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize("venue", ["okx", "binance"])
@pytest.mark.real_place_order
@pytest.mark.asyncio
async def test_ccxt_bad_response_from_uta_remains_ambiguous(monkeypatch, venue):
    from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
    from tests.uta.test_ccxt_adapter import (
        FakeExchange, _identity, _install, _profile, _okx_command, _binance_command,
    )

    profile = _profile(venue)
    exchange = FakeExchange()
    exchange.create_response = ccxt.BadResponse("create-order response unavailable")
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)
    command = _okx_command(identity, market_type="spot") if venue == "okx" else _binance_command(identity)
    try:
        report = await adapter.submit_order(command)
        assert report.evidence is CommandEvidence.AMBIGUOUS
        assert report.order is None
        assert [event[0] for event in exchange.events].count("create_order") == 1
    finally:
        await adapter.close()


@pytest.mark.parametrize("kind", ["classic_v2", "uta_v3"])
@pytest.mark.parametrize("payload", [
    {"code": "00000", "data": {"clientOid": "proposal-1"}},
    {"code": "00000", "data": []},
    {"code": "00000", "data": {"orderId": []}},
    ["not-a-bitget-envelope"],
    {"data": {}},
    ccxt.ExchangeNotAvailable("synthetic server failure"),
    ccxt.BadResponse("synthetic unreadable acknowledgement"),
])
async def test_bitget_client_treats_unusable_create_order_envelope_as_unknown(
    monkeypatch, kind, payload,
):
    from tests.test_bitget_adapter import _bitget_adapter
    from tests.uta.test_ccxt_adapter import _bitget_command

    adapter, exchange = _bitget_adapter(monkeypatch, kind)
    calls = []

    async def response(request):
        calls.append(request)
        if isinstance(payload, Exception):
            raise payload
        return payload

    exchange.privateMixPostV2MixOrderPlaceOrder = response
    exchange.privateUtaPostV3TradePlaceOrder = response
    try:
        report = await adapter.submit_order(_bitget_command(adapter.identity, leverage=None, quantity="1"))
        assert len(calls) == 1
        assert report.evidence is CommandEvidence.AMBIGUOUS
        assert report.order is None
        assert report.venue_order_id is None
    finally:
        await adapter.close()


@pytest.mark.parametrize("kind", ["classic_v2", "uta_v3"])
@pytest.mark.parametrize("code,purpose", [
    ("40017", "entry"), ("22002", "exit"), ("25227", "exit"),
])
async def test_bitget_explicit_create_order_rejection_remains_known(monkeypatch, kind, code, purpose):
    from tests.test_bitget_adapter import _bitget_adapter
    from tests.uta.test_ccxt_adapter import _bitget_command

    adapter, exchange = _bitget_adapter(monkeypatch, kind)

    async def rejected(request):
        response = {"code": code, "msg": "synthetic rejection", "data": None}
        exchange.handle_errors(400, "Bad Request", "mock", "POST", {},
                               json.dumps(response), response, {}, None)
        raise AssertionError("expected CCXT rejection")

    exchange.privateMixPostV2MixOrderPlaceOrder = rejected
    exchange.privateUtaPostV3TradePlaceOrder = rejected
    try:
        report = await adapter.submit_order(_bitget_command(
            adapter.identity, leverage=None, quantity="1", purpose=purpose, reduce_only=purpose == "exit",
        ))
        assert report.evidence is CommandEvidence.VENUE_REJECTED
        if purpose == "exit":
            assert report.reason_code == "close_position_absence_unverified"
        assert report.order is None
        assert report.venue_order_id is None
    finally:
        await adapter.close()


def test_bitget_success_without_order_id_is_unknown_through_gateway_and_not_retried(
    tmp_path,
    monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    proposal = TradeProposal(
        id="proposal-bitget-success-without-order-id",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        id="risk-bitget-success-without-order-id",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode="bitget_demo",
        proposal_execution_mode="bitget_demo",
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)

    post_calls: list[str] = []

    async def _missing_id(command):
        post_calls.append(command.client_order_id)
        return _report(command, venue_order_id=None, state="unknown", evidence=CommandEvidence.AMBIGUOUS)

    _prepare_broker(monkeypatch, _missing_id)
    monkeypatch.setattr(
        gateway,
        "load_config",
        lambda: SimpleNamespace(
            live=SimpleNamespace(state="live_disabled"),
            trading=SimpleNamespace(exchange_tpsl_enabled=False),
        ),
    )
    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda *_args, **_kwargs: SimpleNamespace(
            approved=True,
            allowed_execution_mode="bitget_demo",
            decision_id=decision.id,
        ),
    )
    monkeypatch.setattr(
        gateway,
        "_prepare_exchange_quantity",
        lambda _mode, _symbol, _market_type, quantity, _price: (quantity, None),
    )
    monkeypatch.setattr(
        gateway,
        "_check_symbol_availability",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)

    first = gateway.execute_existing_proposal(proposal.id, "bitget_demo")

    assert first["status"] == "submission_unknown"
    [order] = repo.list_orders(limit=10)
    assert order["proposal_id"] == proposal.id
    assert order["status"] == "submission_unknown"
    assert order["exchange_order_id"] is None
    assert repo.get_trade_proposal(proposal.id)["status"] == "submission_unknown"

    replay = gateway.execute_existing_proposal(proposal.id, "bitget_demo")
    assert replay["status"] == "submission_unknown"
    assert post_calls == [proposal.id]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize("execution_mode", MODES)
def test_confirmed_exchange_fill_rolls_back_partial_local_materialization(tmp_path, monkeypatch, execution_mode,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo

    calls = []

    async def _filled(order):
        calls.append(order.client_order_id)
        return _report(order, venue_order_id="EX-FILLED-LOCAL-FAIL", state="filled", filled_quantity=0.001)

    broker = _prepare_broker(monkeypatch, _filled)
    repo.get_db().execute("CREATE TEMP TRIGGER fail_position_insert BEFORE INSERT ON positions "
                          "BEGIN SELECT RAISE(ABORT, 'private-db-detail'); END")
    kwargs = dict(
        proposal_id="proposal-materialization-missing",
        risk_decision_id="risk-1",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode=execution_mode,
    )

    result = _execute(**kwargs)
    assert result["order_status"] == "materialization_missing"
    assert result["position_id"] is None
    from aletheia.execution.exchange_materialization import materialize_bitget_entry
    with pytest.raises(sqlite3.IntegrityError, match="private-db-detail"):
        materialize_bitget_entry(result["order_id"], [_private_fill(
            "EX-FILLED-LOCAL-FAIL", kwargs["proposal_id"], quantity=0.001, suffix="rollback-entry")])

    orders = repo.list_orders(limit=10)
    assert len(orders) == 1
    assert orders[0]["exchange_order_id"] == "EX-FILLED-LOCAL-FAIL"
    assert orders[0]["status"] == "materialization_missing"
    assert orders[0]["client_order_id"] == "proposal-materialization-missing"
    assert orders[0]["position_id"] is None
    assert "private-db-detail" not in (orders[0]["error_reason"] or "")
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(status="open", limit=10) == []

    with pytest.raises(PostSubmitDBError):
        _execute(**kwargs)
    assert calls == ["proposal-materialization-missing"]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize("execution_mode", MODES)
def test_filled_status_without_fill_economics_does_not_fabricate_local_rows(tmp_path, monkeypatch, execution_mode,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    async def _filled_without_economics(order):
        return _report(order, venue_order_id="EX-FILLED-NO-DETAIL", state="filled")

    broker = _prepare_broker(monkeypatch, _filled_without_economics)
    call = lambda: _execute(
            proposal_id="proposal-fill-economics-missing",
            risk_decision_id="risk-1",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60000.0,
            execution_mode=execution_mode,
        )
    result = call()
    assert result["order_status"] == "materialization_missing"

    order = repo.list_orders(limit=10)[0]
    assert order["exchange_order_id"] == "EX-FILLED-NO-DETAIL"
    assert order["status"] == "materialization_missing"
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(status="open", limit=10) == []


@pytest.mark.parametrize("execution_mode", MODES)
def test_confirmed_close_local_failure_stays_open_and_suppresses_retry(tmp_path, monkeypatch, execution_mode, ):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls = []

    async def _filled_close(order):
        calls.append(order.client_order_id)
        return _report(order, venue_order_id="EX-CLOSE-LOCAL-FAIL", state="filled", filled_quantity=0.01)

    broker = _prepare_broker(monkeypatch, _filled_close)
    repo.get_db().execute("CREATE TEMP TRIGGER fail_close_event BEFORE INSERT ON position_close_events "
                          "BEGIN SELECT RAISE(ABORT, 'private-close-db-detail'); END")
    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    _insert_bound_position(position)

    result = broker.close_position(position.id, reason="stop_loss")
    assert result["closed"] is False
    assert result["status"] == "materialization_missing"
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    with pytest.raises(sqlite3.IntegrityError, match="private-close-db-detail"):
        materialize_bitget_close(result["order_id"], [_private_fill(
            "EX-CLOSE-LOCAL-FAIL", f"close-{position.id}", quantity=0.01, suffix="rollback-close")])

    order = repo.list_orders(limit=10)[0]
    stored_position = repo._select_one("positions", position.id)
    assert order["exchange_order_id"] == "EX-CLOSE-LOCAL-FAIL"
    assert order["status"] == "materialization_missing"
    assert order["client_order_id"] == f"close-{position.id}"
    assert order["position_id"] == position.id
    assert order["close_reason"] == "stop_loss"
    assert "private-close-db-detail" not in (order["error_reason"] or "")
    assert repo.list_fills(limit=10) == []
    assert stored_position["status"] == "open"
    assert stored_position["pending_close_status"] == "materialization_missing"

    retry = broker.close_position(position.id, reason="stop_loss")
    assert retry["already_pending"] is True
    assert calls == [f"close-{position.id}"]
    assert [row["id"] for row in repo.list_orders(limit=10) if row["close_reason"]] == [order["id"]]


@pytest.mark.parametrize("execution_mode", MODES)
def test_empty_exchange_order_id_is_unknown_and_suppresses_retry(tmp_path, monkeypatch, execution_mode,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo

    calls = []

    async def _missing_id(order):
        calls.append(order.client_order_id)
        return _report(order, venue_order_id=None, state="unknown", evidence=CommandEvidence.AMBIGUOUS)

    broker = _prepare_broker(monkeypatch, _missing_id)
    kwargs = dict(
        proposal_id="proposal-missing-exchange-id",
        risk_decision_id="risk-1",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode=execution_mode,
    )
    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    order = repo.list_orders(limit=10)[0]
    assert order["status"] == "submission_unknown"
    assert not order["exchange_order_id"]

    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    assert calls == ["proposal-missing-exchange-id"]


@pytest.mark.parametrize("execution_mode", MODES)
def test_first_exchange_id_backfill_failure_is_post_submit_unresolved(tmp_path, monkeypatch, execution_mode,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
    from aletheia.storage import repositories as repo

    calls = []

    async def _acknowledged(order):
        calls.append(order.client_order_id)
        return _report(order, venue_order_id="EX-BACKFILL-FAIL", state="accepted")

    broker = _prepare_broker(monkeypatch, _acknowledged)
    original_finalize = broker.finalize_exchange_acknowledgement

    def _fail_exchange_id_backfill(order_id, **acknowledgement):
        if acknowledgement.get("exchange_order_id"):
            raise RuntimeError("private-backfill-db-detail")
        return original_finalize(order_id, **acknowledgement)

    monkeypatch.setattr(
        broker, "finalize_exchange_acknowledgement", _fail_exchange_id_backfill,
    )
    kwargs = dict(
        proposal_id="proposal-backfill-fail",
        risk_decision_id="risk-1",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode=execution_mode,
    )
    with pytest.raises(PostSubmitDBError) as exc_info:
        _execute(**kwargs)
    assert exc_info.value.exchange_order_id == "EX-BACKFILL-FAIL"
    assert "private-backfill-db-detail" not in str(exc_info.value)
    assert repo.list_orders(limit=10)[0]["status"] == "pending"

    with pytest.raises(SubmissionUnknownError):
        _execute(**kwargs)
    assert calls == ["proposal-backfill-fail"]


@pytest.mark.parametrize("execution_mode", MODES[1:])
def test_explicit_rejection_write_failure_is_post_submit_unresolved(tmp_path, monkeypatch, execution_mode,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo

    async def _rejected(order):
        return _report(order, venue_order_id="EX-REJECTION-WRITE-FAIL", state="rejected")

    broker = _prepare_broker(monkeypatch, _rejected)
    audits = []
    monkeypatch.setattr(broker, "write_post_commit_audit", audits.append)
    monkeypatch.setattr(
        broker,
        "finalize_exchange_acknowledgement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private-rejection-db-detail")
        ),
    )

    with pytest.raises(PostSubmitDBError) as exc_info:
        _execute(
            proposal_id="proposal-rejection-write-fail",
            risk_decision_id="risk-1",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60000.0,
            execution_mode=execution_mode,
        )

    assert exc_info.value.exchange_order_id == "EX-REJECTION-WRITE-FAIL"
    assert "private-rejection-db-detail" not in str(exc_info.value)
    assert repo.list_orders(limit=10)[0]["status"] == "pending"
    assert audits == []


@pytest.mark.parametrize("execution_mode", MODES)
@pytest.mark.parametrize(
    "exchange_status,reported_quantity",
    (
        ("filled", 0.0005),
        ("partially_filled", 0.001),
        ("partially_filled", 0.0005),
    ),
)
def test_aggregate_fill_quantity_never_materializes_private_economics(
    tmp_path, monkeypatch, execution_mode, exchange_status, reported_quantity,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    async def _response(order):
        return _report(order, venue_order_id="EX-QUANTITY-CHECK", state=exchange_status, filled_quantity=reported_quantity)

    broker = _prepare_broker(monkeypatch, _response)
    call = lambda: _execute(
        proposal_id="proposal-quantity-check",
        risk_decision_id="risk-1",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode=execution_mode,
    )
    result = call()
    order = repo.list_orders(limit=10)[0]
    expected_status = "partially_filled" if exchange_status == "partially_filled" else "materialization_missing"
    assert result["order_status"] == expected_status
    assert order["status"] == expected_status
    assert order["filled_at"] is None
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(status="open", limit=10) == []


@pytest.mark.parametrize("execution_mode", MODES)
@pytest.mark.parametrize(
    "exchange_status,reported_quantity",
    (("filled", 0.005), ("partially_filled", 0.01)),
)
def test_close_fill_status_quantity_conflict_stays_unresolved(tmp_path,
monkeypatch, execution_mode, exchange_status,
reported_quantity,):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    async def _response(order):
        return _report(order, venue_order_id="EX-CLOSE-QUANTITY-CONFLICT", state=exchange_status, filled_quantity=reported_quantity)

    broker = _prepare_broker(monkeypatch, _response)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    _insert_bound_position(position)

    result = broker.close_position(position.id, reason="stop_loss")
    assert result["closed"] is False
    expected_status = (
        "partially_filled"
        if exchange_status == "partially_filled"
        else "materialization_missing"
    )
    assert result["status"] == expected_status
    order = repo.list_orders(limit=10)[0]
    stored = repo._select_one("positions", position.id)
    assert order["status"] == expected_status
    assert repo.list_fills(limit=10) == []
    assert stored["status"] == "open"
    assert stored["pending_close_status"] == expected_status


@pytest.mark.parametrize("execution_mode", MODES)
def test_concurrent_close_claim_reaches_exchange_once(tmp_path, monkeypatch, execution_mode, ):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    entered_exchange = threading.Event()
    release_exchange = threading.Event()
    calls = []

    async def _delayed_fill(order):
        calls.append(order.client_order_id)
        entered_exchange.set()
        assert release_exchange.wait(timeout=5)
        return _report(order, venue_order_id="EX-CONCURRENT-CLOSE", state="filled", filled_quantity=0.01)

    broker = _prepare_broker(monkeypatch, _delayed_fill)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    _insert_bound_position(position)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(broker.close_position, position.id, "stop_loss")
        assert entered_exchange.wait(timeout=5)
        second = pool.submit(broker.close_position, position.id, "stop_loss")
        second_result = second.result(timeout=5)
        release_exchange.set()
        first_result = first.result(timeout=10)

    assert second_result["already_pending"] is True
    assert first_result["closed"] is False
    assert first_result["status"] == "materialization_missing"
    assert calls == [f"close-{position.id}"]
    assert len([row for row in repo.list_orders(limit=10) if row["close_reason"]]) == 1


@pytest.mark.parametrize("execution_mode", MODES)
def test_no_position_close_rejection_keeps_single_durable_claim(tmp_path,
monkeypatch, execution_mode, ):
    """A venue-reported absence must investigate, not resubmit a close."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls = []

    async def _no_position(order):
        calls.append(order.client_order_id)
        return _report(order, venue_order_id=None, evidence=CommandEvidence.VENUE_REJECTED,
                       reason_code="close_position_absence_unverified")

    broker = _prepare_broker(monkeypatch, _no_position)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    _insert_bound_position(position)

    with pytest.raises(RuntimeError, match="close_position_absence_unverified"):
        broker.close_position(position.id, reason="stop_loss")
    first_order = repo.list_orders(limit=10)[0]
    first_order_id = first_order["id"]
    first_position = repo._select_one("positions", position.id)
    assert first_position is not None
    assert first_order["status"] == "error"
    assert first_order["error_reason"] == "close_position_absence_unverified"
    assert first_position["status"] == "open"
    assert first_position["pending_close_status"] == "position_absence_unverified"

    # Simulate the next Position Manager cycle reaching the same broker boundary.
    # What must not recur is the exchange side effect or a replacement durable
    # Close Order while disappearance remains unverified.
    retry = broker.close_position(position.id, reason="stop_loss")

    orders = [row for row in repo.list_orders(limit=10) if row["close_reason"]]
    stored_position = repo._select_one("positions", position.id)
    assert stored_position is not None
    assert retry["already_pending"] is True
    assert retry["status"] == "position_absence_unverified"
    assert calls == [f"close-{position.id}"]
    assert len(orders) == 1
    assert stored_position["pending_close_order_id"] == first_order_id
    assert stored_position["status"] == "open"
    assert stored_position["closed_at"] is None
    assert repo.list_fills(limit=10) == []

    audit_events = [
        json.loads(line)
        for line in (tmp_path / "logs" / "audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    absence_events = [
        event
        for event in audit_events
        if event.get("event_type") == "uta_command_applied"
    ]
    assert len(absence_events) == 1
    assert absence_events[0]["entity_id"] == first_order_id
    assert absence_events[0]["reason"] == "close_position_absence_unverified"


@pytest.mark.parametrize("execution_mode", MODES)
def test_close_ack_without_exchange_id_is_unknown_and_not_retried(tmp_path, monkeypatch, execution_mode, ):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls = []

    async def _missing_id(order):
        calls.append(order.client_order_id)
        return _report(order, venue_order_id=None, state="unknown", evidence=CommandEvidence.AMBIGUOUS)

    broker = _prepare_broker(monkeypatch, _missing_id)
    position = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=100.0, current_price=101.0,
        execution_mode=execution_mode,
    )
    _insert_bound_position(position)

    with pytest.raises(SubmissionUnknownError):
        broker.close_position(position.id, reason="stop_loss")
    order = repo.list_orders(limit=10)[0]
    stored = repo._select_one("positions", position.id)
    assert order["status"] == "submission_unknown"
    assert order["client_order_id"] == f"close-{position.id}"
    assert order["position_id"] == position.id
    assert order["close_reason"] == "stop_loss"
    assert stored["pending_close_status"] == "submission_unknown"

    retry = broker.close_position(position.id, reason="stop_loss")
    assert retry["already_pending"] is True
    assert calls == [f"close-{position.id}"]


@pytest.mark.parametrize("execution_mode", MODES)
def test_close_exchange_id_backfill_failure_is_post_submit_unresolved(tmp_path, monkeypatch, execution_mode, ):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls = []

    async def _acknowledged(order):
        calls.append(order.client_order_id)
        return _report(order, venue_order_id="EX-CLOSE-BACKFILL-FAIL", state="accepted")

    broker = _prepare_broker(monkeypatch, _acknowledged)
    original_finalize = broker.finalize_exchange_acknowledgement

    def _fail_exchange_id_backfill(order_id, **acknowledgement):
        if acknowledgement.get("exchange_order_id"):
            raise RuntimeError("private-close-backfill-detail")
        return original_finalize(order_id, **acknowledgement)

    monkeypatch.setattr(
        broker, "finalize_exchange_acknowledgement", _fail_exchange_id_backfill,
    )
    position = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=100.0, current_price=101.0,
        execution_mode=execution_mode,
    )
    _insert_bound_position(position)

    with pytest.raises(PostSubmitDBError) as exc_info:
        broker.close_position(position.id, reason="stop_loss")
    assert exc_info.value.exchange_order_id == "EX-CLOSE-BACKFILL-FAIL"
    assert "private-close-backfill-detail" not in str(exc_info.value)
    stored = repo._select_one("positions", position.id)
    assert stored["pending_close_status"] == "pending"

    retry = broker.close_position(position.id, reason="stop_loss")
    assert retry["already_pending"] is True
    assert calls == [f"close-{position.id}"]
