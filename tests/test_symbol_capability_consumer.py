"""Gateway consumes normalized availability, not venue-specific source rules."""

import pytest


@pytest.fixture(autouse=True)
def forbid_source_bypass(monkeypatch):
    from aletheia.execution import venue_capabilities

    def forbidden(*args):
        pytest.fail("Gateway bypassed the normalized symbol capability reader")

    for name in ("load_registry_symbols", "load_venue_capabilities"):
        monkeypatch.setattr(venue_capabilities, name, forbidden)


@pytest.mark.parametrize("execution_mode", ["bitget_demo", "okx_demo", "contract_account"])
@pytest.mark.parametrize("market_type", ["spot", "usdt_futures"])
@pytest.mark.parametrize("supported", [None, frozenset(), frozenset({"BTC/USDT"})])
def test_gateway_uses_exact_normalized_symbol_facts(monkeypatch, execution_mode, market_type, supported):
    from aletheia.execution import gateway, venue_capabilities

    calls = []

    def read(mode, market):
        calls.append((mode, market))
        return supported

    monkeypatch.setattr(venue_capabilities, "get_supported_symbols", read)
    reason = gateway._check_symbol_availability("BTCUSDT", execution_mode, market_type)

    assert calls == [(execution_mode, market_type)]
    assert (reason is not None) is (supported == frozenset())
    if reason:
        assert all(value in reason for value in ("BTCUSDT", execution_mode, market_type))


def test_gateway_does_not_turn_capability_failure_into_unknown(monkeypatch):
    from aletheia.execution import gateway, venue_capabilities

    def unavailable(*args):
        raise RuntimeError("capability acquisition unavailable")

    monkeypatch.setattr(venue_capabilities, "get_supported_symbols", unavailable)
    with pytest.raises(RuntimeError, match="capability acquisition unavailable"):
        gateway._check_symbol_availability("BTC/USDT", "bitget_demo", "usdt_futures")
