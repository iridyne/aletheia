"""Approved Bitget futures protection travels in the original entry order."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _capture_bitget_http(monkeypatch) -> list[dict]:
    from aletheia.exchanges.bitget import client as bitget_client

    bodies: list[dict] = []

    class _Response:
        def json(self):
            return {
                "code": "00000",
                "data": {"orderId": "EX-PRESET-1", "clientOid": "preset-spec"},
            }

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url, content=None, headers=None):
            bodies.append(json.loads(content))
            return _Response()

    async def _no_detail(self, order_id, symbol):
        return None

    monkeypatch.setattr(bitget_client, "httpx_client", lambda **_kwargs: _HTTP())
    monkeypatch.setattr(
        bitget_client.BitgetClient,
        "_headers",
        lambda self, *_args, **_kwargs: {},
    )
    monkeypatch.setattr(bitget_client.BitgetClient, "_get_order_detail", _no_detail)
    return bodies


@pytest.mark.real_place_order
@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-01",
        "pipeline_bitget_preset_protection",
        "futures-open request body",
        "L2b",
        "A Bitget USDT-futures open carries the approved preset stop-loss and "
        "take-profit in its original place-order request.",
        status="implemented",
    )
)
def test_bitget_futures_open_body_carries_exact_preset_protection(monkeypatch):
    from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest

    bodies = _capture_bitget_http(monkeypatch)

    asyncio.run(BitgetClient(mode="demo").place_order(OrderRequest(
        symbol="BTCUSDT",
        market_type="usdt_futures",
        side="buy",
        order_type="market",
        quantity=0.01,
        client_oid="preset-spec",
        preset_stop_loss_price=59_000.25,
        preset_take_profit_price=62_000.75,
    )))

    assert bodies == [{
        "symbol": "BTCUSDT",
        "productType": "USDT-FUTURES",
        "marginMode": "crossed",
        "marginCoin": "USDT",
        "side": "buy",
        "orderType": "market",
        "size": "0.01",
        "tradeSide": "open",
        "presetStopLossPrice": "59000.25",
        "presetStopSurplusPrice": "62000.75",
        "clientOid": "preset-spec",
    }]


@pytest.mark.real_place_order
@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-02",
        "pipeline_bitget_preset_protection",
        "spot-close isolation",
        "L2b",
        "Spot orders and futures close orders never carry entry preset protection, "
        "even if an upstream caller supplies those optional fields.",
        status="implemented",
    )
)
def test_bitget_spot_and_futures_close_never_send_entry_presets(monkeypatch):
    from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest

    bodies = _capture_bitget_http(monkeypatch)
    client = BitgetClient(mode="demo")
    common = {
        "side": "buy",
        "order_type": "market",
        "quantity": 0.01,
        "preset_stop_loss_price": 90.0,
        "preset_take_profit_price": 120.0,
    }

    asyncio.run(client.place_order(OrderRequest(
        symbol="BTCUSDT",
        market_type="spot",
        **common,
    )))
    asyncio.run(client.place_order(OrderRequest(
        symbol="BTCUSDT",
        market_type="usdt_futures",
        hold_side="long",
        **common,
    )))

    assert len(bodies) == 2
    assert bodies[0]["symbol"] == "BTCUSDT"
    assert bodies[1]["tradeSide"] == "close"
    for body in bodies:
        assert "presetStopLossPrice" not in body
        assert "presetStopSurplusPrice" not in body


@pytest.mark.real_place_order
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preset_stop_loss_price", 0.0),
        ("preset_stop_loss_price", -1.0),
        ("preset_stop_loss_price", float("nan")),
        ("preset_stop_loss_price", float("inf")),
        ("preset_take_profit_price", 0.0),
        ("preset_take_profit_price", -1.0),
        ("preset_take_profit_price", float("nan")),
        ("preset_take_profit_price", float("inf")),
    ],
)
def test_bitget_rejects_invalid_preset_price_before_http(monkeypatch, field, value):
    from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest

    bodies = _capture_bitget_http(monkeypatch)
    request = OrderRequest(
        symbol="BTCUSDT",
        market_type="usdt_futures",
        side="buy",
        order_type="market",
        quantity=0.01,
        **{field: value},
    )

    with pytest.raises(ValueError, match="preset protection price"):
        asyncio.run(BitgetClient(mode="demo").place_order(request))

    assert bodies == []


@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-03",
        "pipeline_bitget_preset_protection",
        "approved broker forwarding",
        "L2b",
        "The Bitget broker forwards only the stop and take-profit committed in the "
        "approved Proposal; a caller cannot replace either value.",
        status="implemented",
    )
)
def test_bitget_broker_forwards_approved_proposal_protection(monkeypatch):
    from aletheia.execution import bitget_broker
    from aletheia.exchanges.bitget.client import OrderResponse
    from tests.execution_authority import seed_order_authority

    proposal, decision = seed_order_authority(
        proposal_id="proposal-bitget-preset",
        risk_decision_id="risk-bitget-preset",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60_000.0,
        execution_mode="bitget_demo",
        stop_price=59_000.0,
        take_profit=62_000.0,
    )
    captured = []

    async def _place(self, request):
        captured.append(request)
        return OrderResponse(order_id="EX-BROKER-PRESET", status="submitted")

    monkeypatch.setattr(bitget_broker, "_ensure_mix_margin", lambda *_args: None)
    monkeypatch.setattr(bitget_broker.BitgetClient, "place_order", _place)

    bitget_broker.execute_live_order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        stop_price=proposal.stop_price,
        take_profit_price=proposal.take_profit,
    )

    assert len(captured) == 1
    assert captured[0].preset_stop_loss_price == proposal.stop_price
    assert captured[0].preset_take_profit_price == proposal.take_profit


def test_bitget_broker_rejects_protection_not_in_approved_proposal(monkeypatch):
    from aletheia.execution import bitget_broker
    from aletheia.exchanges.bitget.client import OrderResponse
    from aletheia.storage import repositories as repo
    from tests.execution_authority import seed_order_authority

    proposal, decision = seed_order_authority(
        proposal_id="proposal-bitget-mutation",
        risk_decision_id="risk-bitget-mutation",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60_000.0,
        execution_mode="bitget_demo",
        stop_price=59_000.0,
        take_profit=62_000.0,
    )
    captured = []

    async def _place(self, request):
        captured.append(request)
        return OrderResponse(order_id="EX-MUTATED-PRESET", status="submitted")

    monkeypatch.setattr(bitget_broker, "_ensure_mix_margin", lambda *_args: None)
    monkeypatch.setattr(bitget_broker.BitgetClient, "place_order", _place)

    with pytest.raises(ValueError, match="approved Proposal"):
        bitget_broker.execute_live_order(
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=proposal.quantity,
            price=proposal.price,
            execution_mode=proposal.execution_mode,
            stop_price=58_000.0,
            take_profit_price=proposal.take_profit,
        )

    assert captured == []
    assert repo.list_orders() == []


def _run_gateway_with_captured_broker(
    monkeypatch,
    *,
    execution_mode: str,
    take_profit: float | None,
    position_id: str | None = None,
) -> tuple[dict, list[dict]]:
    from aletheia.execution import bitget_broker, gateway, okx_broker
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        id=f"proposal-gateway-{execution_mode}-{take_profit}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60_000.0,
        stop_price=59_000.0,
        take_profit=take_profit,
        execution_mode=execution_mode,
    )
    repo.insert_trade_proposal(proposal)
    captured: list[dict] = []

    def _risk_result(execution_proposal, decision_role):
        return SimpleNamespace(
            approved=True,
            allowed_execution_mode=execution_proposal.execution_mode,
            decision_id=f"risk-{decision_role}-{execution_mode}",
            violations=[],
            message="approved",
        )

    def _execute(**kwargs):
        captured.append(kwargs)
        return {
            "order_id": f"order-{execution_mode}",
            "exchange_order_id": f"exchange-{execution_mode}",
            "order_status": "submitted",
            "position_id": position_id,
            "fill_id": None,
            "fill_price": None,
            "fill_quantity": 0,
        }

    cfg = SimpleNamespace(
        live=SimpleNamespace(state="live_limited"),
        trading=SimpleNamespace(default_take_profit_pct=37.0),
    )
    monkeypatch.setattr(gateway, "load_config", lambda: cfg)
    monkeypatch.setattr(gateway, "evaluate_proposal", _risk_result)
    monkeypatch.setattr(
        gateway,
        "_prepare_exchange_quantity",
        lambda *_args: (proposal.quantity, None),
    )
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *_args: None)
    monkeypatch.setattr(bitget_broker, "execute_live_order", _execute)
    monkeypatch.setattr(okx_broker, "execute_live_order", _execute)

    return gateway._execute_proposal(proposal), captured


@pytest.mark.parametrize(
    "execution_mode",
    ["bitget_demo", "bitget_live_limited"],
)
@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-04",
        "pipeline_bitget_preset_protection",
        "gateway approved-value routing",
        "L2c",
        "Bitget demo and limited-live dispatch receive the authoritative execution "
        "Proposal's explicit take-profit; other brokers do not gain this behavior.",
        status="implemented",
    )
)
def test_gateway_passes_explicit_take_profit_only_to_bitget(monkeypatch, execution_mode):
    result, captured = _run_gateway_with_captured_broker(
        monkeypatch,
        execution_mode=execution_mode,
        take_profit=62_000.0,
    )

    assert result["status"] == "submitted"
    assert len(captured) == 1
    assert captured[0]["stop_price"] == 59_000.0
    assert captured[0]["take_profit_price"] == 62_000.0


def test_gateway_does_not_forward_bitget_preset_behavior_to_other_brokers(monkeypatch):
    result, captured = _run_gateway_with_captured_broker(
        monkeypatch,
        execution_mode="okx_demo",
        take_profit=62_000.0,
    )

    assert result["status"] == "submitted"
    assert len(captured) == 1
    assert captured[0]["take_profit_price"] is None


@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-05",
        "pipeline_bitget_preset_protection",
        "no configuration fallback",
        "L2c",
        "A mutable default take-profit percentage is never presented as approved "
        "protection on the asynchronous exchange-entry path.",
        status="implemented",
    )
)
def test_gateway_does_not_invent_take_profit_from_config(monkeypatch):
    from aletheia.execution import gateway

    position_updates = []
    monkeypatch.setattr(
        gateway,
        "update_position",
        lambda *args, **kwargs: position_updates.append((args, kwargs)),
        raising=False,
    )

    result, captured = _run_gateway_with_captured_broker(
        monkeypatch,
        execution_mode="bitget_demo",
        take_profit=None,
        position_id="position-should-not-get-config-tp",
    )

    assert result["status"] == "submitted"
    assert captured[0]["take_profit_price"] is None
    assert position_updates == []
