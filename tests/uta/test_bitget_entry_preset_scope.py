"""PL-BG-PRESET-02 applies to the current UTA owner, not the retired client."""

import pytest

from aletheia.uta.models import CommandEvidence, InstrumentIdentity
from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
from tests.uta.test_ccxt_adapter import FakeExchange, _bitget_command, _identity, _install, _profile


@pytest.mark.parametrize("kind", ["uta_v3"])
@pytest.mark.parametrize("market", ["spot", "usdt_futures"])
@pytest.mark.parametrize("purpose", ["entry", "exit"])
async def test_bitget_entry_presets_are_futures_entry_only(monkeypatch, kind, market, purpose):
    profile = _profile(kind)
    exchange = FakeExchange()
    exchange.create_response = {"id": "submitted"}
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    instrument = InstrumentIdentity(
        canonical_symbol="BTC/USDT", venue_symbol="BTC/USDT" if market == "spot" else "BTC/USDT:USDT",
        market_type=market, base_currency="BTC", quote_currency="USDT", settlement_currency="USDT",
    )
    try:
        report = await adapter.submit_order(_bitget_command(
            adapter.identity, instrument=instrument, purpose=purpose,
            reduce_only=purpose == "exit", leverage=None, order_type="limit",
            limit_price="100", stop_loss_price="90", take_profit_price="120",
        ))
        assert report.evidence is CommandEvidence.ACKNOWLEDGED
        requests = [event[1] for event in exchange.events if event[0] == "create_order"]
        assert len(requests) == 1
        params = requests[0][-1]
        if market == "usdt_futures" and purpose == "entry":
            assert str(params["stopLoss"]["triggerPrice"]) == "90"
            assert str(params["takeProfit"]["triggerPrice"]) == "120"
        else:
            assert "stopLoss" not in params
            assert "takeProfit" not in params
        return report
    finally:
        await adapter.close()


@pytest.mark.parametrize("kind", ["uta_v3"])
def test_approved_spot_proposal_keeps_local_stop_without_venue_entry_presets(
    tmp_path, monkeypatch, kind,
):
    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo
    from tests.helpers.execution_authority import seed_order_authority
    from tests.uta.test_execution_coordinator import _setup_db

    _setup_db(tmp_path, monkeypatch)
    proposal, decision = seed_order_authority(
        proposal_id="spot-entry", risk_decision_id="spot-risk",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="limit", quantity=1, price=100, execution_mode="bitget_demo",
        stop_price=90, take_profit=120,
    )
    profile = _profile(kind)
    exchange = FakeExchange()
    exchange.create_response = {"id": "spot-order"}
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: adapter)
    coordinator.execute_order(
        proposal_id=proposal.id, risk_decision_id=decision.id,
        symbol=proposal.symbol, market_type=proposal.market_type,
        direction=proposal.direction, order_type=proposal.order_type,
        quantity=proposal.quantity, price=proposal.price,
        execution_mode=proposal.execution_mode, stop_price=proposal.stop_price,
        take_profit_price=proposal.take_profit,
    )
    requests = [event[1] for event in exchange.events if event[0] == "create_order"]
    assert len(requests) == 1
    assert "stopLoss" not in requests[0][-1]
    assert "takeProfit" not in requests[0][-1]
    stored = repo.get_trade_proposal(proposal.id)
    assert stored["stop_price"] == 90
    assert stored["take_profit"] == 120
    assert exchange.closed
