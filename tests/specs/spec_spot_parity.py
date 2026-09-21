"""Spec: Demo↔Live Spot Parity (AI chooses spot vs perp)

Executable claims for wiring SPOT trading through the OKX adapter and letting the
System Operator decide market_type per opportunity (no hardcoded perp default).

Source event: docs/research/integrations/DEMO_LIVE_SPOT_PARITY_EVENT_2026-06-28.md (OKX demo
spot support empirically confirmed). Owner principles: demo↗live convergence;
exchange-agnostic core with per-adapter capability; no fixed instrument bias.
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

# ---------------------------------------------------------------------------
# L2a — Structural (adapter capability + symbol routing)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S01", "spot_parity", "§3 adapter capability", "L2a",
              "OKX adapter declares it supports BOTH spot and usdt_futures"),
)
def test_okx_declares_spot_and_futures_capability():
    from tests.uta.test_ccxt_adapter import _profile

    assert "spot" in _profile("okx").supported_market_types
    assert "usdt_futures" in _profile("okx").supported_market_types


@spec_claim(
    SpecClaim("SP-S02", "spot_parity", "§3 symbol routing", "L2a",
              "Core instrument identity maps spot to canonical and perp to a settlement-suffixed venue symbol"),
)
def test_okx_symbol_routing_by_market_type():
    from aletheia.uta.compat import instrument_from_local

    # Spot: plain canonical pair, NO settle suffix
    assert instrument_from_local("BTC/USDT", "spot").venue_symbol == "BTC/USDT"
    assert ":" not in instrument_from_local("BTC/USDT", "spot").venue_symbol
    # Perp/usdt_futures: ccxt perp with settle suffix
    assert instrument_from_local("BTC/USDT", "usdt_futures").venue_symbol == "BTC/USDT:USDT"


@spec_claim(
    SpecClaim("SP-S03", "spot_parity", "§3 spot has no leverage", "L2a",
              "OKX profile sets leverage only for the perpetual market, never spot"),
)
@pytest.mark.parametrize("market_type", ["spot", "usdt_futures"])
async def test_leverage_preparation_depends_on_market_type(market_type):
    from unittest.mock import AsyncMock
    from tests.uta.test_ccxt_adapter import FakeExchange, _profile, _identity, _okx_command

    profile = _profile("okx")
    exchange = FakeExchange()
    exchange.set_leverage = AsyncMock()
    command = _okx_command(_identity(profile), market_type=market_type, leverage="2")
    prepared = await profile.prepare_submit(exchange, command)
    assert prepared.instrument.market_type == market_type
    if market_type == "spot":
        exchange.set_leverage.assert_not_awaited()
    else:
        exchange.set_leverage.assert_awaited_once()


# ---------------------------------------------------------------------------
# L2b — Behavioral (decision chain: AI/opportunity drives market_type)
# ---------------------------------------------------------------------------
