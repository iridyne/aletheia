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
async def test_bitget_futures_open_body_carries_exact_preset_protection(monkeypatch):
    from aletheia.uta.models import CommandEvidence
    from tests.test_bitget_adapter import _bitget_adapter
    from tests.uta.test_ccxt_adapter import _bitget_command

    adapter, exchange = _bitget_adapter(monkeypatch)
    exchange.markets["BTC/USDT:USDT"]["precision"]["price"] = 0.01
    requests = []

    async def uta(request):
        requests.append(("uta_v3", request))
        return {"code": "00000", "data": {"orderId": "EX-PRESET-1"}}

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
        assert family == "uta_v3"
        assert body["symbol"] == "BTCUSDT"
        assert body["side"] == "buy"
        assert body["orderType"] == "market"
        assert body["clientOid"] == "preset-spec"
        assert body["stopLoss"] == "59000.25"
        assert body["takeProfit"] == "62000.75"
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
@pytest.mark.parametrize("kind", ["uta_v3"])
@pytest.mark.parametrize("market", ["spot", "usdt_futures"])
@pytest.mark.parametrize("purpose", ["entry", "exit"])
async def test_bitget_spot_and_futures_close_never_send_entry_presets(monkeypatch, kind, market, purpose):
    from tests.uta.test_bitget_entry_preset_scope import test_bitget_entry_presets_are_futures_entry_only

    result = await test_bitget_entry_presets_are_futures_entry_only(monkeypatch, kind, market, purpose)
    assert result is not None, "bitget preset-scope delegation must return its evidence report"


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
        _bitget_command(_identity(_profile("uta_v3")), **{field: value})
    assert calls == []


@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-03",
        "pipeline_bitget_preset_protection",
        "approved broker forwarding",
        "L2b",
        "The Bitget broker forwards Proposal protection only when native TPSL is "
        "enabled at execution time; a caller cannot replace either value.",
        status="implemented",
    )
)
@pytest.mark.parametrize("native_tpsl_enabled", [False, True])
def test_bitget_broker_gates_approved_proposal_protection(monkeypatch, native_tpsl_enabled):
    from aletheia.execution import coordinator
    from tests.uta.test_execution_coordinator import _Adapter, _ack
    from aletheia.uta.models import UTACapabilities, CapabilitySupport
    from tests.helpers.execution_authority import seed_order_authority

    proposal, decision = seed_order_authority(
        proposal_id=f"proposal-bitget-preset-{native_tpsl_enabled}",
        risk_decision_id=f"risk-bitget-preset-{native_tpsl_enabled}",
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
    audits = []

    async def submit(command):
        captured.append(command)
        return _ack(command)

    adapter = _Adapter(submit, "bitget_demo")
    adapter.capabilities = UTACapabilities(
        native_tpsl=CapabilitySupport.SUPPORTED, source="isolated_test"
    )
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: adapter)
    monkeypatch.setattr(coordinator, "write_post_commit_audit", audits.append)
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
        native_tpsl_enabled=native_tpsl_enabled,
        leverage=10,
    )

    assert len(captured) == 1
    expected_stop = proposal.stop_price if native_tpsl_enabled else None
    expected_take = proposal.take_profit if native_tpsl_enabled else None
    assert captured[0].stop_loss_price == expected_stop
    assert captured[0].take_profit_price == expected_take
    assert audits[0]["after"]["native_tpsl_enabled"] is native_tpsl_enabled
    # The accepted entry that carried the presets is the protection confirmation.
    assert audits[0]["after"].get("protection_source") == (
        "entry_preset_ack" if native_tpsl_enabled else None
    )


@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-08",
        "pipeline_bitget_preset_protection",
        "§4 pre-send preset guard",
        "L2b",
        "A futures entry whose approved command carries SL/TP is sent only when the "
        "built venue request carries both as the exact approved strings; otherwise "
        "it is refused before the venue call (native_tpsl_params_missing).",
        status="implemented",
    )
)
@pytest.mark.parametrize("fault", ["stop_dropped", "decimal_trigger", "take_profit_changed"])
async def test_entry_whose_built_body_lost_its_presets_is_never_sent(monkeypatch, fault):
    from decimal import Decimal

    from aletheia.uta.models import CommandEvidence
    from tests.test_bitget_adapter import _bitget_adapter
    from tests.uta.test_ccxt_adapter import _bitget_command

    adapter, exchange = _bitget_adapter(monkeypatch)
    assert exchange.markets is not None
    exchange.markets["BTC/USDT:USDT"]["precision"]["price"] = 0.01
    requests = []

    async def uta(request):
        requests.append(request)
        return {"code": "00000", "data": {"orderId": "EX-PRESET-LOST"}}

    monkeypatch.setattr(exchange, "privateUtaPostV3TradePlaceOrder", uta, raising=False)
    built = adapter._profile.submit_request

    def faulty(command):
        args = built(command)
        params = dict(args[-1])
        if fault == "stop_dropped":
            params.pop("stopLoss")
        elif fault == "decimal_trigger":
            # #3783: ccxt reads a Decimal trigger as None and the venue still ACKs.
            params["stopLoss"] = {"triggerPrice": Decimal("59000.25")}
        else:
            params["takeProfit"] = {"triggerPrice": "62000.70"}
        return (*args[:-1], params)

    monkeypatch.setattr(adapter._profile, "submit_request", faulty)
    try:
        report = await adapter.submit_order(_bitget_command(
            adapter.identity, quantity="1", leverage=None,
            client_order_id="preset-guard", stop_loss_price="59000.25",
            take_profit_price="62000.75",
        ))
        assert report.evidence is CommandEvidence.NOT_SENT
        assert report.reason_code == "native_tpsl_params_missing"
        assert requests == []
    finally:
        await adapter.close()

def test_bitget_broker_rejects_protection_not_in_approved_proposal(monkeypatch):
    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo
    from tests.helpers.execution_authority import seed_order_authority

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
    native_tpsl_enabled: bool | None = False,
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
        trading=SimpleNamespace(
            default_take_profit_pct=37.0,
            exchange_tpsl_enabled=native_tpsl_enabled,
        ),
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
        "Gateway snapshots the native-TPSL toggle with the authoritative Proposal "
        "values for Bitget demo and limited-live dispatch; unset, it is on for "
        "bitget_demo only (#3848).",
        status="implemented",
    )
)
@pytest.mark.parametrize("native_tpsl_enabled", [False, True, None])
def test_gateway_passes_native_tpsl_policy(
    monkeypatch, execution_mode, native_tpsl_enabled
):
    result, captured = _run_gateway_with_captured_broker(
        monkeypatch,
        execution_mode=execution_mode,
        take_profit=62_000.0,
        native_tpsl_enabled=native_tpsl_enabled,
    )

    assert result["status"] == "submitted"
    assert len(captured) == 1
    assert captured[0]["stop_price"] == 59_000.0
    assert captured[0]["take_profit_price"] == 62_000.0
    expected = (execution_mode == "bitget_demo") if native_tpsl_enabled is None else native_tpsl_enabled
    assert captured[0]["native_tpsl_enabled"] is expected




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


@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-06",
        "pipeline_bitget_preset_protection",
        "§4 entry-attached protection read-back",
        "L2b",
        "The venue ids of an entry's attached SL/TP are read back from the open "
        "strategy orders and persisted as {sl, tp, origin=entry_preset}, with audit.",
        status="implemented",
    )
)
def test_entry_preset_ids_are_read_back_and_persisted(tmp_path, monkeypatch):
    from aletheia.portfolio.native_protection import verify_native_protection
    from tests.test_bitget_uta_v3_switch import _strategy_row
    from tests.test_tpsl_native_protection_check import (
        audit_types, install_venue, record_closes, seed_unverified_position, stored_ids,
    )

    position_id = seed_unverified_position(tmp_path, monkeypatch)
    # Bitget returns one strategy order carrying both preset legs.
    exchange = install_venue(monkeypatch, {"code": "00000", "data": [_strategy_row(orderId="PRESET-1")]})
    closes = record_closes(monkeypatch)

    result = verify_native_protection()

    assert exchange.calls == [{"category": "USDT-FUTURES", "symbol": "BTCUSDT"}]
    assert result == {"verified": [position_id], "errors": []}
    assert stored_ids(position_id) == {"origin": "entry_preset", "sl": "PRESET-1", "tp": "PRESET-1"}
    assert audit_types(position_id) == ["native_protection_verified"]
    assert closes == []
    # Persisted ids leave the read-back scan: the next cycle reads nothing.
    exchange.calls.clear()
    assert verify_native_protection() == {"verified": [], "errors": []}
    assert exchange.calls == []


@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-07",
        "pipeline_bitget_preset_protection",
        "§4 read-back is bookkeeping only",
        "L2b",
        "The read-back never closes a Position (owner D1 2026-10-09): a complete read "
        "showing no stop past the grace window writes native_protection_absent and "
        "reports an error; an incomplete read writes nothing. Neither persists ids.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    "response",
    [
        {"code": "00000", "data": []},
        {"code": "00000", "data": "TP_ONLY"},
        {"code": "40001", "data": []},
        {"code": "00000", "data": None},
        {"code": "00000", "data": "FULL_PAGE"},
    ],
    ids=["complete_empty", "complete_tp_only", "rejected", "malformed", "full_page"],
)
def test_read_back_that_finds_no_stop_audits_and_never_closes(tmp_path, monkeypatch, response):
    from aletheia.portfolio.native_protection import verify_native_protection
    from tests.test_bitget_uta_v3_switch import _strategy_row
    from tests.test_tpsl_native_protection_check import (
        audit_types, install_venue, record_closes, seed_unverified_position, stored_ids,
    )

    position_id = seed_unverified_position(tmp_path, monkeypatch)
    if response["data"] == "TP_ONLY":
        response = {"code": "00000", "data": [_strategy_row(stopLoss="")]}
    elif response["data"] == "FULL_PAGE":
        response = {"code": "00000", "data": [_strategy_row(orderId=str(i)) for i in range(100)]}
    install_venue(monkeypatch, response)
    closes = record_closes(monkeypatch)

    result = verify_native_protection()

    assert closes == []
    assert "closed" not in result
    assert len(result["errors"]) == 1
    complete = response["code"] == "00000" and isinstance(response["data"], list) and len(response["data"]) < 100
    if complete:
        assert audit_types(position_id) == ["native_protection_absent"]
        assert result["errors"][0]["error_type"] == "native_protection_absent"
    else:
        assert audit_types(position_id) == []
    assert stored_ids(position_id) == {}


def _amend_command(identity, trigger_price, target="PRESET-1"):
    from aletheia.uta.models import ModifyOrderCommand
    from tests.uta.test_ccxt_adapter import _bitget_command

    return ModifyOrderCommand(
        command_id="amend-1", expected_account=identity,
        instrument=_bitget_command(identity).instrument, purpose="protective_stop",
        position_id="position-1", target_venue_order_id=target, trigger_price=trigger_price,
    )


def _install_strategy_venue(exchange, rows):
    """Real ccxt codec; only the two UTA strategy endpoints are answered locally."""
    calls = []

    async def unfilled(params):
        calls.append(("unfilled", params))
        return {"code": "00000", "data": rows}

    async def modify(request):
        calls.append(("modify", request))
        return {"code": "00000", "data": {"orderId": request["orderId"], "clientOid": ""}}

    async def forbidden(*args, **kwargs):
        calls.append(("forbidden", args))
        raise AssertionError("an amend must never cancel or place a stop")

    exchange.privateUtaGetV3TradeUnfilledStrategyOrders = unfilled
    exchange.privateUtaPostV3TradeModifyStrategyOrder = modify
    for name in ("privateUtaPostV3TradeCancelStrategyOrder", "privateUtaPostV3TradePlaceStrategyOrder",
                 "privateUtaPostV3TradePlaceOrder", "privateUtaPostV3TradeCancelOrder"):
        setattr(exchange, name, forbidden)
    return calls


@spec_claim(
    SpecClaim(
        "PL-BG-PRESET-09",
        "pipeline_bitget_preset_protection",
        "§4 venue stop amended in place",
        "L2b",
        "A tightened stop amends the stored Bitget UTA strategy order in place "
        "(modify-strategy-order, never cancel-then-place). A take-profit carried by the "
        "same strategy order is resent unchanged, so the amend cannot drop it.",
        status="implemented",
    )
)
@pytest.mark.parametrize("shared_take_profit", [True, False], ids=["shared_sl_tp_order", "stop_only_order"])
async def test_tightened_stop_amends_the_venue_strategy_order_in_place(monkeypatch, shared_take_profit):
    from aletheia.uta.models import CommandEvidence
    from tests.test_bitget_adapter import _bitget_adapter
    from tests.test_bitget_uta_v3_switch import _strategy_row

    adapter, exchange = _bitget_adapter(monkeypatch)
    assert exchange.markets is not None
    exchange.markets["BTC/USDT:USDT"]["precision"]["price"] = 0.01
    row = _strategy_row(orderId="PRESET-1") if shared_take_profit else _strategy_row(
        orderId="PRESET-1", takeProfit="", tpTriggerBy="", tpOrderType="",
    )
    calls = _install_strategy_venue(exchange, [_strategy_row(orderId="OTHER-9", posSide="short"), row])
    try:
        report = await adapter.modify_order(_amend_command(adapter.identity, "59500.5"))
    finally:
        await adapter.close()

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    assert report.venue_order_id == "PRESET-1"
    assert calls[0] == ("unfilled", {"category": "USDT-FUTURES", "symbol": "BTCUSDT"})
    expected = {
        "orderId": "PRESET-1", "qty": "0.01",
        "stopLoss": "59500.5", "slTriggerBy": "mark", "slOrderType": "market",
    }
    if shared_take_profit:
        expected |= {"takeProfit": "62000.75", "tpTriggerBy": "market", "tpOrderType": "market"}
    assert calls[1:] == [("modify", expected)]


@pytest.mark.parametrize(
    ("pos_side", "venue_stop", "new_stop"),
    [("long", "59000.25", "58900"), ("short", "61000.5", "61100")],
)
async def test_stop_amend_that_would_loosen_the_venue_stop_is_never_sent(
    monkeypatch, pos_side, venue_stop, new_stop,
):
    from aletheia.uta.models import CommandEvidence
    from tests.test_bitget_adapter import _bitget_adapter
    from tests.test_bitget_uta_v3_switch import _strategy_row

    adapter, exchange = _bitget_adapter(monkeypatch)
    assert exchange.markets is not None
    exchange.markets["BTC/USDT:USDT"]["precision"]["price"] = 0.01
    calls = _install_strategy_venue(
        exchange, [_strategy_row(orderId="PRESET-1", posSide=pos_side, stopLoss=venue_stop)],
    )
    try:
        report = await adapter.modify_order(_amend_command(adapter.identity, new_stop))
        missing = await adapter.modify_order(_amend_command(adapter.identity, new_stop, target="GONE-1"))
    finally:
        await adapter.close()

    assert report.evidence is CommandEvidence.NOT_SENT
    assert report.reason_code == "protection_amend_loosens"
    assert missing.evidence is CommandEvidence.NOT_SENT
    assert missing.reason_code == "modify_target_unavailable"
    assert [name for name, _ in calls] == ["unfilled", "unfilled"]
