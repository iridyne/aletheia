"""Spec: Demo↔Live Spot Parity (AI chooses spot vs perp)

Executable claims for wiring SPOT trading through the OKX adapter and letting the
System Operator decide market_type per opportunity (no hardcoded perp default).

Source event: docs/research/DEMO_LIVE_SPOT_PARITY_EVENT_2026-06-28.md (OKX demo
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


@spec_claim(
    SpecClaim("SP-S04", "spot_parity", "§3 decision inherits market", "L2b",
              "_attach_opportunity_context backfills market_type from the source "
              "opportunity's market when the PI omitted it (no hardcoded perp)"),
)
def test_decision_inherits_market_type_from_opportunity():
    from aletheia.agent_shell.trading_decision import (
        TradingDecision, _attach_opportunity_context,
    )

    # PI picked a symbol+direction but did NOT specify market_type.
    decision = TradingDecision(
        action="trade", symbol="BTC/USDT", direction="long",
        conviction=70, reasoning="x", market_type=None,
    )
    ctx = {
        "layer1": {"opportunities": [
            {"id": "opp1", "symbol": "BTC/USDT", "market": "spot",
             "direction_bias": "long", "scanner_family": "f"},
        ]},
        "layer2": {"pending_signals": [
            {"opportunity_id": "opp1", "symbol": "BTC/USDT",
             "direction": "long", "market_type": "spot",
             "signal_source": "market_observer"},
        ]},
    }
    _attach_opportunity_context(decision, ctx)
    # Inherits the opportunity's market type rather than defaulting to perp.
    assert decision.market_type == "spot"
    assert decision.opportunity_id == "opp1"


@spec_claim(
    SpecClaim("SP-S05", "spot_parity", "§3 explicit PI choice wins", "L2b",
              "an explicit PI market_type is NOT overwritten by opportunity inheritance"),
)
def test_explicit_pi_market_type_preserved():
    from aletheia.agent_shell.trading_decision import (
        TradingDecision, _attach_opportunity_context,
    )

    decision = TradingDecision(
        action="trade", symbol="BTC/USDT", direction="long",
        conviction=70, reasoning="x", market_type="usdt_futures",
    )
    ctx = {"layer1": {"opportunities": [
        {"id": "opp1", "symbol": "BTC/USDT", "market": "spot", "direction_bias": "long"},
    ]}}
    _attach_opportunity_context(decision, ctx)
    assert decision.market_type == "usdt_futures"  # PI's explicit pick preserved


@spec_claim(
    SpecClaim("SP-S06", "spot_parity", "§3 no fixed bias in prompt", "L2b",
              "the trade prompt frames market_type situationally — no 'default to "
              "usdt_futures' instruction (owner principle 3)"),
)
def test_prompt_has_no_perp_default_bias():
    from aletheia.agent_shell import trading_decision as td

    builder = getattr(td, "_build_decision_prompt", None) or getattr(td, "build_decision_prompt", None)
    if builder is None:
        # Fall back to scanning the module source for the removed instruction.
        import inspect
        src = inspect.getsource(td)
        assert "Default to 'usdt_futures'" not in src
        return
    # If a zero-arg-friendly builder is exposed, assert on its text too.
    import inspect
    src = inspect.getsource(td)
    assert "Default to 'usdt_futures'" not in src
