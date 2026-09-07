"""Spec: Demo↔Live Spot Parity (AI chooses spot vs perp)

Executable claims for wiring SPOT trading through the OKX adapter and letting the
System Operator decide market_type per opportunity (no hardcoded perp default).

Source event: docs/research/integrations/DEMO_LIVE_SPOT_PARITY_EVENT_2026-06-28.md (OKX demo
spot support empirically confirmed). Owner principles: demo↗live convergence;
exchange-agnostic core with per-adapter capability; no fixed instrument bias.
"""

from tests.specs.spec_meta import SpecClaim, spec_claim

# ---------------------------------------------------------------------------
# L2a — Structural (adapter capability + symbol routing)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S01", "spot_parity", "§3 adapter capability", "L2a",
              "OKX adapter declares it supports BOTH spot and usdt_futures"),
)
def test_okx_declares_spot_and_futures_capability():
    from aletheia.exchanges.okx.client import SUPPORTED_MARKET_TYPES

    assert "spot" in SUPPORTED_MARKET_TYPES
    assert "usdt_futures" in SUPPORTED_MARKET_TYPES


@spec_claim(
    SpecClaim("SP-S02", "spot_parity", "§3 symbol routing", "L2a",
              "okx_ccxt_symbol maps spot→canonical (no :USDT), perp→ccxt perp suffix"),
)
def test_okx_symbol_routing_by_market_type():
    from aletheia.exchanges.okx.client import okx_ccxt_symbol

    # Spot: plain canonical pair, NO settle suffix
    assert okx_ccxt_symbol("spot", "BTC/USDT") == "BTC/USDT"
    assert ":" not in okx_ccxt_symbol("spot", "BTC/USDT")
    # Perp/usdt_futures: ccxt perp with settle suffix
    assert okx_ccxt_symbol("usdt_futures", "BTC/USDT") == "BTC/USDT:USDT"


@spec_claim(
    SpecClaim("SP-S03", "spot_parity", "§3 spot has no leverage", "L2a",
              "is_leveraged_market is False for spot, True for usdt_futures"),
)
def test_spot_is_not_leveraged():
    from aletheia.exchanges.okx.client import is_leveraged_market

    assert is_leveraged_market("spot") is False
    assert is_leveraged_market("usdt_futures") is True


# ---------------------------------------------------------------------------
# L2b — Behavioral (decision chain: AI/opportunity drives market_type)
# ---------------------------------------------------------------------------
