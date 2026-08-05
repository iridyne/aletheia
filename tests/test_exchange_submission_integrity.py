"""Real-path regressions for ambiguous submission and local materialization."""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import ccxt
import httpx
import pytest

from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import reset_db


BROKERS = (
    pytest.param(
        "aletheia.execution.bitget_broker", "BitgetClient", "bitget_demo",
        id="bitget",
    ),
    pytest.param(
        "aletheia.execution.okx_broker", "OKXClient", "okx_demo",
        id="okx",
    ),
    pytest.param(
        "aletheia.execution.binance_broker", "BinanceClient", "binance_testnet",
        id="binance",
    ),
)

CLOSE_BROKERS = (
    pytest.param(
        "aletheia.execution.bitget_broker", "BitgetClient", "bitget_demo",
        "close_demo_position", id="bitget",
    ),
    pytest.param(
        "aletheia.execution.okx_broker", "OKXClient", "okx_demo",
        "close_okx_position", id="okx",
    ),
    pytest.param(
        "aletheia.execution.binance_broker", "BinanceClient", "binance_testnet",
        "close_testnet_position", id="binance",
    ),
)


def _uses_delayed_private_materialization(broker_module: str) -> bool:
    # All exchange brokers treat the aggregate submission response as an
    # acknowledgement only. Durable Fill/Position state waits for private,
    # order-matched fill history.
    return True


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


def _prepare_broker(monkeypatch, broker_module, client_name, place_order):
    broker = importlib.import_module(broker_module)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    original_insert_order = broker.insert_order

    def _insert_with_authority(order):
        # Exercise the production native-authority check instead of bypassing it
        # with synthetic ids. Close Orders consume their direct Position link;
        # entry Orders consume this immutable Proposal/RiskDecision snapshot.
        if order.close_reason is None and repo.get_trade_proposal(order.proposal_id) is None:
            proposal = TradeProposal(
                id=order.proposal_id,
                symbol=order.symbol,
                market_type=order.market_type,
                direction=order.direction,
                order_type=order.order_type,
                quantity=order.quantity,
                price=order.price,
                execution_mode=order.execution_mode,
                status="approved",
            )
            repo.insert_trade_proposal(proposal)
            repo.insert_risk_decision(RiskDecision(
                id=order.risk_decision_id,
                proposal_id=proposal.id,
                approved=True,
                allowed_execution_mode=order.execution_mode,
                proposal_execution_mode=proposal.execution_mode,
                decision_role="authoritative",
                evaluated_quantity=order.quantity,
                proposal_commit_hash=proposal.commit_hash,
            ))
        original_insert_order(order)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def place_order(self, order):
            return await place_order(order)

        async def close(self):
            pass

    monkeypatch.setattr(broker, client_name, _Client)
    monkeypatch.setattr(broker, "insert_order", _insert_with_authority)
    if broker_module.endswith("okx_broker"):
        monkeypatch.setattr(broker, "_get_demo_symbols", lambda market_type: None)
    return broker


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
def test_open_transport_failure_is_unknown_and_direct_retry_is_suppressed(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo

    calls = []

    async def _connection_lost(order):
        calls.append(order.client_oid)
        raise ConnectionResetError("private-transport-detail")

    broker = _prepare_broker(
        monkeypatch, broker_module, client_name, _connection_lost,
    )
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
        broker.execute_live_order(**kwargs)
    first = repo.list_orders(limit=10)
    assert len(first) == 1
    assert first[0]["status"] == "submission_unknown"
    assert first[0]["client_order_id"] == "proposal-transport-unknown"
    assert "private-transport-detail" not in (first[0]["error_reason"] or "")

    with pytest.raises(SubmissionUnknownError):
        broker.execute_live_order(**kwargs)
    assert calls == ["proposal-transport-unknown"]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
def test_explicit_api_rejection_remains_known_error(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    async def _api_rejected(order):
        raise RuntimeError("explicit exchange rejection")

    broker = _prepare_broker(
        monkeypatch, broker_module, client_name, _api_rejected,
    )
    with pytest.raises(RuntimeError, match="explicit exchange rejection"):
        broker.execute_live_order(
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
    assert orders[0]["status"] == "error"
    assert orders[0]["error_reason"] == "order_submission_rejected"
    assert "explicit exchange rejection" not in orders[0]["error_reason"]


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
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
def test_ambiguous_exchange_response_is_unknown_and_retry_suppressed(
    tmp_path,
    monkeypatch,
    broker_module,
    client_name,
    execution_mode,
    error_factory,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo

    calls: list[str] = []

    async def _ambiguous_response(order):
        calls.append(order.client_oid)
        raise error_factory()

    broker = _prepare_broker(
        monkeypatch,
        broker_module,
        client_name,
        _ambiguous_response,
    )
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
        broker.execute_live_order(**kwargs)
    [stored] = repo.list_orders(limit=10)
    assert stored["status"] == "submission_unknown"

    with pytest.raises(SubmissionUnknownError):
        broker.execute_live_order(**kwargs)
    assert calls == [kwargs["proposal_id"]]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize(
    "client_module,client_name,mode,market_type",
    [
        pytest.param(
            "aletheia.exchanges.okx.client",
            "OKXClient",
            "demo",
            "spot",
            id="okx-client",
        ),
        pytest.param(
            "aletheia.exchanges.binance.client",
            "BinanceClient",
            "testnet",
            "usdt_futures",
            id="binance-client",
        ),
    ],
)
@pytest.mark.real_place_order
def test_ccxt_bad_response_wrapped_by_real_client_remains_ambiguous(
    monkeypatch, client_module, client_name, mode, market_type,
):
    """The shared broker classifier must see through each real client wrapper."""
    module = importlib.import_module(client_module)
    client_type = getattr(module, client_name)
    client = object.__new__(client_type)
    client._mode = mode

    class _Exchange:
        async def create_order(self, **_kwargs):
            raise ccxt.BadResponse("create-order response unavailable")

    monkeypatch.setattr(client, "_ensure_exchange", lambda: _Exchange())
    request = module.OrderRequest(
        symbol="BTC/USDT",
        market_type=market_type,
        side="buy",
        order_type="market",
        quantity=0.001,
        client_oid="wrapped-bad-response",
    )

    with pytest.raises(RuntimeError) as captured:
        asyncio.run(client.place_order(request))

    from aletheia.exchanges.submission_errors import is_ambiguous_submission_error

    assert isinstance(captured.value.__cause__, ccxt.BadResponse)
    assert is_ambiguous_submission_error(captured.value) is True


@pytest.mark.parametrize(
    ("status_code", "readable"),
    [
        pytest.param(503, True, id="server-error"),
        pytest.param(200, False, id="unreadable-success-response"),
    ],
)
@pytest.mark.real_place_order
def test_bitget_client_exposes_ambiguous_create_order_response(
    monkeypatch,
    status_code,
    readable,
):
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.exchanges.submission_errors import (
        SubmissionResponseUnknownError,
    )

    class _Response:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            if not readable:
                raise json.JSONDecodeError("bad acknowledgement", "", 0)
            return {"code": "50000", "msg": "temporary server failure"}

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(bitget_client, "httpx_client", lambda **_kwargs: _HTTP())
    monkeypatch.setattr(
        bitget_client.BitgetClient,
        "_headers",
        lambda *_args, **_kwargs: {},
    )
    request = bitget_client.OrderRequest(
        symbol="BTCUSDT",
        market_type="spot",
        side="buy",
        order_type="market",
        quantity=0.001,
        client_oid="bitget-response-unknown",
    )

    with pytest.raises(SubmissionResponseUnknownError):
        asyncio.run(bitget_client.BitgetClient(mode="demo").place_order(request))


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"code": "00000", "data": {"clientOid": "proposal-1"}},
            id="success-without-order-id",
        ),
        pytest.param(
            {"code": "00000", "data": []},
            id="success-with-non-object-data",
        ),
        pytest.param(
            {"code": "00000", "data": {"orderId": []}},
            id="success-with-non-string-order-id",
        ),
        pytest.param(["not-a-bitget-envelope"], id="non-object-envelope"),
        pytest.param({"data": {}}, id="missing-response-code"),
    ],
)
@pytest.mark.real_place_order
def test_bitget_client_treats_unusable_create_order_envelope_as_unknown(
    monkeypatch,
    payload,
):
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.exchanges.submission_errors import (
        SubmissionResponseUnknownError,
    )

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return payload

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(bitget_client, "httpx_client", lambda **_kwargs: _HTTP())
    monkeypatch.setattr(
        bitget_client.BitgetClient,
        "_headers",
        lambda *_args, **_kwargs: {},
    )
    request = bitget_client.OrderRequest(
        symbol="BTCUSDT",
        market_type="spot",
        side="buy",
        order_type="market",
        quantity=0.001,
        client_oid="proposal-1",
    )

    with pytest.raises(SubmissionResponseUnknownError):
        asyncio.run(bitget_client.BitgetClient(mode="demo").place_order(request))


@pytest.mark.real_place_order
def test_bitget_explicit_create_order_rejection_remains_known(monkeypatch):
    from aletheia.exchanges.bitget import client as bitget_client

    class _Response:
        status_code = 400

        @staticmethod
        def json():
            return {
                "code": "40017",
                "msg": "Parameter verification failed",
                "data": None,
            }

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(bitget_client, "httpx_client", lambda **_kwargs: _HTTP())
    monkeypatch.setattr(
        bitget_client.BitgetClient,
        "_headers",
        lambda *_args, **_kwargs: {},
    )
    request = bitget_client.OrderRequest(
        symbol="BTCUSDT",
        market_type="spot",
        side="buy",
        order_type="market",
        quantity=0.001,
        client_oid="proposal-explicit-rejection",
    )

    with pytest.raises(RuntimeError, match="Bitget order error") as exc_info:
        asyncio.run(bitget_client.BitgetClient(mode="demo").place_order(request))
    assert type(exc_info.value) is RuntimeError


@pytest.mark.real_place_order
@pytest.mark.parametrize("response_code", ("22002", "25227"))
def test_bitget_no_position_close_rejection_is_structured(
    monkeypatch, response_code,
):
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.exchanges.submission_errors import ClosePositionAbsentError

    class _Response:
        status_code = 400

        @staticmethod
        def json():
            return {
                "code": response_code,
                "msg": "No position available to close",
                "data": None,
            }

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(bitget_client, "httpx_client", lambda **_kwargs: _HTTP())
    monkeypatch.setattr(
        bitget_client.BitgetClient,
        "_headers",
        lambda *_args, **_kwargs: {},
    )
    request = bitget_client.OrderRequest(
        symbol="BTCUSDT",
        market_type="usdt_futures",
        side="buy",
        order_type="market",
        quantity=0.001,
        client_oid="close-position-1",
        hold_side="long",
    )

    with pytest.raises(ClosePositionAbsentError, match=response_code):
        asyncio.run(bitget_client.BitgetClient(mode="demo").place_order(request))


@pytest.mark.real_place_order
def test_bitget_success_without_order_id_is_unknown_through_gateway_and_not_retried(
    tmp_path,
    monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import bitget_broker, gateway
    from aletheia.exchanges.bitget import client as bitget_client
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

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "code": "00000",
                "msg": "success",
                "data": {"clientOid": proposal.id},
            }

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            post_calls.append(proposal.id)
            return _Response()

    monkeypatch.setattr(bitget_client, "httpx_client", lambda **_kwargs: _HTTP())
    monkeypatch.setattr(
        bitget_client.BitgetClient,
        "_headers",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        gateway,
        "load_config",
        lambda: SimpleNamespace(live=SimpleNamespace(state="live_disabled")),
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
    monkeypatch.setattr(bitget_broker, "write_post_commit_audit", lambda _event: True)

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


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
def test_confirmed_exchange_fill_rolls_back_partial_local_materialization(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo

    calls = []

    async def _filled(order):
        calls.append(order.client_oid)
        return SimpleNamespace(
            order_id="EX-FILLED-LOCAL-FAIL",
            status="filled",
            fill_price=60001.0,
            filled_quantity=0.001,
        )

    broker = _prepare_broker(monkeypatch, broker_module, client_name, _filled)
    monkeypatch.setattr(
        broker,
        "insert_position",
        lambda position: (_ for _ in ()).throw(RuntimeError("private-db-detail")),
        raising=False,
    )
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

    if _uses_delayed_private_materialization(broker_module):
        result = broker.execute_live_order(**kwargs)
        assert result["order_status"] == "materialization_missing"
        assert result["position_id"] is None
    else:
        with pytest.raises(PostSubmitDBError) as exc_info:
            broker.execute_live_order(**kwargs)
        assert exc_info.value.exchange_order_id == "EX-FILLED-LOCAL-FAIL"

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
        broker.execute_live_order(**kwargs)
    assert calls == ["proposal-materialization-missing"]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
def test_filled_status_without_fill_economics_does_not_fabricate_local_rows(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo

    async def _filled_without_economics(order):
        return SimpleNamespace(
            order_id="EX-FILLED-NO-DETAIL",
            status="filled",
            fill_price=None,
            filled_quantity=None,
        )

    broker = _prepare_broker(
        monkeypatch, broker_module, client_name, _filled_without_economics,
    )
    call = lambda: broker.execute_live_order(
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
    if _uses_delayed_private_materialization(broker_module):
        result = call()
        assert result["order_status"] == "materialization_missing"
    else:
        with pytest.raises(PostSubmitDBError):
            call()

    order = repo.list_orders(limit=10)[0]
    assert order["exchange_order_id"] == "EX-FILLED-NO-DETAIL"
    assert order["status"] == "materialization_missing"
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(status="open", limit=10) == []


@pytest.mark.parametrize(
    "broker_module,client_name,execution_mode,close_name", CLOSE_BROKERS,
)
def test_confirmed_close_local_failure_stays_open_and_suppresses_retry(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode, close_name,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls = []

    async def _filled_close(order):
        calls.append(order.client_oid)
        return SimpleNamespace(
            order_id="EX-CLOSE-LOCAL-FAIL",
            status="filled",
            fill_price=101.0,
            filled_quantity=0.01,
        )

    broker = _prepare_broker(
        monkeypatch, broker_module, client_name, _filled_close,
    )
    monkeypatch.setattr(
        broker,
        "insert_fill",
        lambda fill: (_ for _ in ()).throw(RuntimeError("private-close-db-detail")),
        raising=False,
    )
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)

    if _uses_delayed_private_materialization(broker_module):
        result = getattr(broker, close_name)(position.id, reason="stop_loss")
        assert result["closed"] is False
        assert result["status"] == "materialization_missing"
    else:
        with pytest.raises(PostSubmitDBError):
            getattr(broker, close_name)(position.id, reason="stop_loss")

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

    retry = getattr(broker, close_name)(position.id, reason="stop_loss")
    assert retry["already_pending"] is True
    assert calls == [f"close-{position.id}"]
    assert len(repo.list_orders(limit=10)) == 1


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
def test_empty_exchange_order_id_is_unknown_and_suppresses_retry(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo

    calls = []

    async def _missing_id(order):
        calls.append(order.client_oid)
        return SimpleNamespace(
            order_id="", status="submitted", fill_price=None, filled_quantity=None,
        )

    broker = _prepare_broker(monkeypatch, broker_module, client_name, _missing_id)
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
        broker.execute_live_order(**kwargs)
    order = repo.list_orders(limit=10)[0]
    assert order["status"] == "submission_unknown"
    assert not order["exchange_order_id"]

    with pytest.raises(SubmissionUnknownError):
        broker.execute_live_order(**kwargs)
    assert calls == ["proposal-missing-exchange-id"]


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
def test_first_exchange_id_backfill_failure_is_post_submit_unresolved(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
    from aletheia.storage import repositories as repo

    calls = []

    async def _acknowledged(order):
        calls.append(order.client_oid)
        return SimpleNamespace(
            order_id="EX-BACKFILL-FAIL",
            status="submitted",
            fill_price=None,
            filled_quantity=None,
        )

    broker = _prepare_broker(
        monkeypatch, broker_module, client_name, _acknowledged,
    )
    original_update = broker.update_order

    def _fail_exchange_id_backfill(order_id, **updates):
        if updates.get("exchange_order_id"):
            raise RuntimeError("private-backfill-db-detail")
        return original_update(order_id, **updates)

    monkeypatch.setattr(broker, "update_order", _fail_exchange_id_backfill)
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
        broker.execute_live_order(**kwargs)
    assert exc_info.value.exchange_order_id == "EX-BACKFILL-FAIL"
    assert "private-backfill-db-detail" not in str(exc_info.value)
    assert repo.list_orders(limit=10)[0]["status"] == "pending"

    with pytest.raises(SubmissionUnknownError):
        broker.execute_live_order(**kwargs)
    assert calls == ["proposal-backfill-fail"]


@pytest.mark.parametrize("broker_module,client_name,execution_mode", BROKERS)
@pytest.mark.parametrize(
    "exchange_status,reported_quantity,is_valid_partial",
    (
        ("filled", 0.0005, False),
        ("partially_filled", 0.001, False),
        ("partially_filled", 0.0005, True),
    ),
)
def test_entry_fill_status_requires_consistent_exchange_quantity(
    tmp_path,
    monkeypatch,
    broker_module,
    client_name,
    execution_mode,
    exchange_status,
    reported_quantity,
    is_valid_partial,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo

    async def _response(order):
        return SimpleNamespace(
            order_id="EX-QUANTITY-CHECK",
            status=exchange_status,
            fill_price=60001.0,
            filled_quantity=reported_quantity,
        )

    broker = _prepare_broker(monkeypatch, broker_module, client_name, _response)
    call = lambda: broker.execute_live_order(
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
    if _uses_delayed_private_materialization(broker_module):
        result = call()
        order = repo.list_orders(limit=10)[0]
        assert result["order_status"] == "materialization_missing"
        assert order["status"] == "materialization_missing"
        assert order["filled_at"] is None
        assert repo.list_fills(limit=10) == []
        assert repo.list_positions(status="open", limit=10) == []
        return
    if not is_valid_partial:
        with pytest.raises(PostSubmitDBError):
            call()
        order = repo.list_orders(limit=10)[0]
        assert order["status"] == "materialization_missing"
        assert repo.list_fills(limit=10) == []
        return

    call()
    order = repo.list_orders(limit=10)[0]
    position = repo.list_positions(status="open", limit=10)[0]
    assert order["status"] == "partially_filled"
    assert order["filled_at"] is None
    assert order["client_order_id"] == "proposal-quantity-check"
    assert order["position_id"] == position["id"]
    assert len(repo.list_fills(limit=10)) == 1
    assert len(repo.list_positions(status="open", limit=10)) == 1


@pytest.mark.parametrize(
    "broker_module,client_name,execution_mode,close_name", CLOSE_BROKERS,
)
@pytest.mark.parametrize(
    "exchange_status,reported_quantity",
    (("filled", 0.005), ("partially_filled", 0.01)),
)
def test_close_fill_status_quantity_conflict_stays_unresolved(
    tmp_path,
    monkeypatch,
    broker_module,
    client_name,
    execution_mode,
    close_name,
    exchange_status,
    reported_quantity,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    async def _response(order):
        return SimpleNamespace(
            order_id="EX-CLOSE-QUANTITY-CONFLICT",
            status=exchange_status,
            fill_price=101.0,
            filled_quantity=reported_quantity,
        )

    broker = _prepare_broker(monkeypatch, broker_module, client_name, _response)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)

    if _uses_delayed_private_materialization(broker_module):
        result = getattr(broker, close_name)(position.id, reason="stop_loss")
        assert result["closed"] is False
        expected_status = (
            "partially_filled"
            if exchange_status == "partially_filled"
            else "materialization_missing"
        )
        assert result["status"] == expected_status
    else:
        with pytest.raises(PostSubmitDBError):
            getattr(broker, close_name)(position.id, reason="stop_loss")
        expected_status = "materialization_missing"
    order = repo.list_orders(limit=10)[0]
    stored = repo._select_one("positions", position.id)
    assert order["status"] == expected_status
    assert repo.list_fills(limit=10) == []
    assert stored["status"] == "open"
    assert stored["pending_close_status"] == expected_status


@pytest.mark.parametrize(
    "broker_module,client_name,execution_mode,close_name", CLOSE_BROKERS,
)
def test_concurrent_close_claim_reaches_exchange_once(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode, close_name,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    entered_exchange = threading.Event()
    release_exchange = threading.Event()
    calls = []

    async def _delayed_fill(order):
        calls.append(order.client_oid)
        entered_exchange.set()
        assert release_exchange.wait(timeout=5)
        return SimpleNamespace(
            order_id="EX-CONCURRENT-CLOSE",
            status="filled",
            fill_price=101.0,
            filled_quantity=0.01,
        )

    broker = _prepare_broker(
        monkeypatch, broker_module, client_name, _delayed_fill,
    )
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(getattr(broker, close_name), position.id, "stop_loss")
        assert entered_exchange.wait(timeout=5)
        second = pool.submit(getattr(broker, close_name), position.id, "stop_loss")
        second_result = second.result(timeout=5)
        release_exchange.set()
        first_result = first.result(timeout=10)

    assert second_result["already_pending"] is True
    assert first_result["closed"] is (
        not _uses_delayed_private_materialization(broker_module)
    )
    if _uses_delayed_private_materialization(broker_module):
        assert first_result["status"] == "materialization_missing"
    assert calls == [f"close-{position.id}"]
    assert len(repo.list_orders(limit=10)) == 1


def test_bitget_no_position_close_rejection_keeps_single_durable_claim(
    tmp_path, monkeypatch,
):
    """A venue-reported absence must investigate, not resubmit a close."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.exchanges.submission_errors import ClosePositionAbsentError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls = []

    async def _no_position(order):
        calls.append(order.client_oid)
        raise ClosePositionAbsentError(
            "Bitget close rejected: no position available (code=25227)"
        )

    broker = _prepare_broker(
        monkeypatch,
        "aletheia.execution.bitget_broker",
        "BitgetClient",
        _no_position,
    )
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode="bitget_demo",
    )
    repo.insert_position(position)

    with pytest.raises(ClosePositionAbsentError, match="25227"):
        broker.close_demo_position(position.id, reason="stop_loss")
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
    retry = broker.close_demo_position(position.id, reason="stop_loss")

    orders = repo.list_orders(limit=10)
    stored_position = repo._select_one("positions", position.id)
    assert stored_position is not None
    assert retry["already_pending"] is True
    assert retry["status"] == "position_absence_unverified"
    assert calls == [f"close-{position.id}"]
    assert len(orders) == 1
    assert stored_position["pending_close_order_id"] == first_order_id


@pytest.mark.parametrize(
    "broker_module,client_name,execution_mode,close_name", CLOSE_BROKERS,
)
def test_close_ack_without_exchange_id_is_unknown_and_not_retried(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode, close_name,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls = []

    async def _missing_id(order):
        calls.append(order.client_oid)
        return SimpleNamespace(
            order_id="", status="submitted", fill_price=None, filled_quantity=None,
        )

    broker = _prepare_broker(monkeypatch, broker_module, client_name, _missing_id)
    position = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=100.0, current_price=101.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)

    with pytest.raises(SubmissionUnknownError):
        getattr(broker, close_name)(position.id, reason="stop_loss")
    order = repo.list_orders(limit=10)[0]
    stored = repo._select_one("positions", position.id)
    assert order["status"] == "submission_unknown"
    assert order["client_order_id"] == f"close-{position.id}"
    assert order["position_id"] == position.id
    assert order["close_reason"] == "stop_loss"
    assert stored["pending_close_status"] == "submission_unknown"

    retry = getattr(broker, close_name)(position.id, reason="stop_loss")
    assert retry["already_pending"] is True
    assert calls == [f"close-{position.id}"]


@pytest.mark.parametrize(
    "broker_module,client_name,execution_mode,close_name", CLOSE_BROKERS,
)
def test_close_exchange_id_backfill_failure_is_post_submit_unresolved(
    tmp_path, monkeypatch, broker_module, client_name, execution_mode, close_name,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.errors import PostSubmitDBError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls = []

    async def _acknowledged(order):
        calls.append(order.client_oid)
        return SimpleNamespace(
            order_id="EX-CLOSE-BACKFILL-FAIL",
            status="submitted",
            fill_price=None,
            filled_quantity=None,
        )

    broker = _prepare_broker(monkeypatch, broker_module, client_name, _acknowledged)
    original_update = broker.update_order

    def _fail_exchange_id_backfill(order_id, **updates):
        if updates.get("exchange_order_id"):
            raise RuntimeError("private-close-backfill-detail")
        return original_update(order_id, **updates)

    monkeypatch.setattr(broker, "update_order", _fail_exchange_id_backfill)
    position = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=100.0, current_price=101.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)

    with pytest.raises(PostSubmitDBError) as exc_info:
        getattr(broker, close_name)(position.id, reason="stop_loss")
    assert exc_info.value.exchange_order_id == "EX-CLOSE-BACKFILL-FAIL"
    assert "private-close-backfill-detail" not in str(exc_info.value)
    stored = repo._select_one("positions", position.id)
    assert stored["pending_close_status"] == "materialization_missing"

    retry = getattr(broker, close_name)(position.id, reason="stop_loss")
    assert retry["already_pending"] is True
    assert calls == [f"close-{position.id}"]
