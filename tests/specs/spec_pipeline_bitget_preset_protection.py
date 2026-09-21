"""Approved Bitget futures protection travels in the original entry order."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


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
@pytest.mark.parametrize("kind", ["classic_v2", "uta_v3"])
async def test_bitget_futures_open_body_carries_exact_preset_protection(monkeypatch, kind):
    from aletheia.uta.models import CommandEvidence
    from tests.test_bitget_adapter import _bitget_adapter
    from tests.uta.test_ccxt_adapter import _bitget_command

    adapter, exchange = _bitget_adapter(monkeypatch, kind)
    exchange.markets["BTC/USDT:USDT"]["precision"]["price"] = 0.01
    requests = []

    async def classic(request):
        requests.append(("classic_v2", request))
        return {"code": "00000", "data": {"orderId": "EX-PRESET-1"}}

    async def uta(request):
        requests.append(("uta_v3", request))
        return {"code": "00000", "data": {"orderId": "EX-PRESET-1"}}

    exchange.privateMixPostV2MixOrderPlaceOrder = classic
    exchange.privateUtaPostV3TradePlaceOrder = uta
    try:
        report = await adapter.submit_order(_bitget_command(
            adapter.identity, quantity="1", leverage=None,
            client_order_id="preset-spec", stop_loss_price="59000.25",
            take_profit_price="62000.75",
        ))
        assert report.evidence is CommandEvidence.ACKNOWLEDGED
        assert len(requests) == 1
        family, body = requests[0]
        assert family == kind
        assert body["symbol"] == "BTCUSDT"
        assert body["side"] == "buy"
        assert body["orderType"] == "market"
        assert body["clientOid"] == "preset-spec"
        stop_key = "presetStopLossPrice" if kind == "classic_v2" else "stopLoss"
        take_key = "presetStopSurplusPrice" if kind == "classic_v2" else "takeProfit"
        assert body[stop_key] == "59000.25"
        assert body[take_key] == "62000.75"
    finally:
        await adapter.close()


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
@pytest.mark.parametrize("kind", ["classic_v2", "uta_v3"])
@pytest.mark.parametrize("market", ["spot", "usdt_futures"])
@pytest.mark.parametrize("purpose", ["entry", "exit"])
async def test_bitget_spot_and_futures_close_never_send_entry_presets(monkeypatch, kind, market, purpose):
    from tests.uta.test_bitget_entry_preset_scope import test_bitget_entry_presets_are_futures_entry_only

    await test_bitget_entry_presets_are_futures_entry_only(monkeypatch, kind, market, purpose)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stop_loss_price", 0.0),
        ("stop_loss_price", -1.0),
        ("stop_loss_price", float("nan")),
        ("stop_loss_price", float("inf")),
        ("take_profit_price", 0.0),
        ("take_profit_price", -1.0),
        ("take_profit_price", float("nan")),
        ("take_profit_price", float("inf")),
    ],
)
def test_bitget_rejects_invalid_preset_price_before_http(monkeypatch, field, value):
    from tests.uta.test_ccxt_adapter import FakeExchange, _bitget_command, _identity, _install, _profile

    _session, calls = _install(monkeypatch, FakeExchange())
    with pytest.raises(ValueError):
        _bitget_command(_identity(_profile("classic_v2")), **{field: value})
    assert calls == []


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
    from aletheia.execution import coordinator
    from tests.uta.test_execution_coordinator import _Adapter, _ack
    from aletheia.uta.models import UTACapabilities, CapabilitySupport
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

    async def submit(command):
        captured.append(command)
        return _ack(command)
    adapter = _Adapter(submit, "bitget_demo")
    adapter.capabilities = UTACapabilities(native_tpsl=CapabilitySupport.SUPPORTED, source="isolated_test")
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: adapter)
    coordinator.execute_order(
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
        leverage=10,
    )

    assert len(captured) == 1
    assert captured[0].stop_loss_price == proposal.stop_price
    assert captured[0].take_profit_price == proposal.take_profit


def test_bitget_broker_rejects_protection_not_in_approved_proposal(monkeypatch):
    from aletheia.execution import coordinator
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

    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: captured.append(_mode) or pytest.fail("mutated protection reached adapter"))

    with pytest.raises(ValueError, match="approved Proposal"):
        coordinator.execute_order(
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
            leverage=10,
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
    from aletheia.execution import coordinator, gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, RiskDecision, TradeProposal

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
    authoritative_decision = RiskDecision(
        id=f"risk-authoritative-{execution_mode}",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(authoritative_decision)
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
        repo.insert_order(
            Order(
                id=f"order-{execution_mode}",
                proposal_id=proposal.id,
                risk_decision_id=authoritative_decision.id,
                client_order_id=proposal.id,
                symbol=proposal.symbol,
                market_type=proposal.market_type,
                direction=proposal.direction,
                order_type=proposal.order_type,
                quantity=proposal.quantity or 0.0,
                price=proposal.price,
                execution_mode=proposal.execution_mode,
                exchange_order_id=f"exchange-{execution_mode}",
                status="submitted",
            )
        )
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
    monkeypatch.setattr(coordinator, "execute_order", _execute)

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
