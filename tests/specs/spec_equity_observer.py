"""Spec: Equity Observer + stock token abstraction (EQ-* claims).

Guards the generic stock-token registry and the equity_observer's
exchange-agnostic token lookup introduced in the Bitget r-series adaptation
(2026-07-01).  All claims attach to data_utilization_contract (observation-only
data domain, not a new trust boundary).

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
Without that registration the conformance matrix collects zero EQ-* cases.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_CONTRACT = "data_utilization_contract"


# ---------------------------------------------------------------------------
# EQ-R01 — generic stock-token registry + r-series rule exist in symbols.py
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "EQ-R01", _CONTRACT, "§stock-token-registry", "L2a",
        "symbols.py exposes is_stock_token(), stock_token_to_ticker(), and "
        "ticker_to_stock_tokens(); is_stock_token('rNVDA/USDT') returns True.",
    ),
)
def test_stock_token_functions_exist():
    from aletheia.data.symbols import (
        is_stock_token,
        stock_token_to_ticker,
        ticker_to_stock_tokens,
    )
    # r-series token recognised
    assert is_stock_token("rNVDA/USDT"), "rNVDA/USDT must be a stock token"
    assert is_stock_token("rTSLA/USDT"), "rTSLA/USDT must be a stock token"
    assert is_stock_token("rAAPL/USDT"), "rAAPL/USDT must be a stock token"

    # ON-series token recognised (registry-based)
    assert is_stock_token("AAPLON/USDT"), "AAPLON/USDT must be a stock token"
    assert is_stock_token("TSLAON/USDT"), "TSLAON/USDT must be a stock token"

    # Non-token NOT recognised
    assert not is_stock_token("BTC/USDT"), "BTC/USDT is not a stock token"
    assert not is_stock_token("ETH/USDT"), "ETH/USDT is not a stock token"

    # ticker lookup works for both series
    assert stock_token_to_ticker("rNVDA/USDT") == "NVDA"
    assert stock_token_to_ticker("AAPLON/USDT") == "AAPL"
    assert stock_token_to_ticker("rTSLA/USDT") == "TSLA"

    # ticker → tokens: r-series first, then ON-series
    tokens = ticker_to_stock_tokens("AAPL")
    assert len(tokens) >= 1, "AAPL must have at least one token"
    assert tokens[0] == "rAAPL/USDT", "r-series must be first (higher liquidity)"
    # AAPLON should also appear if present in registry
    assert "AAPLON/USDT" in tokens, "AAPLON/USDT must be in AAPL token list"


# ---------------------------------------------------------------------------
# EQ-R02 — equity_observer uses ticker_to_stock_tokens (r-series priority)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "EQ-R02", _CONTRACT, "§equity-observer-r-series", "L2a",
        "equity_observer imports ticker_to_stock_tokens and iterates candidate "
        "tokens in priority order (r-series before ON-series).",
    ),
)
def test_equity_observer_uses_priority_token_lookup():
    src = (_ROOT / "aletheia" / "agents" / "equity_observer.py").read_text(encoding="utf-8")
    assert "ticker_to_stock_tokens" in src, (
        "equity_observer must import/call ticker_to_stock_tokens"
    )
    assert "stock_token_exchange" in src, (
        "equity_observer must call stock_token_exchange for exchange-agnostic routing"
    )
    # The iteration over candidate_tokens and break on first successful price
    assert "for token_sym in candidate_tokens" in src, (
        "equity_observer must iterate candidate_tokens in priority order"
    )


# ---------------------------------------------------------------------------
# EQ-R03 — research_worker has equity_chain lens
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "EQ-R03", _CONTRACT, "§serenity-equity-chain-lens", "L2a",
        "research_worker.py defines _EQUITY_CHAIN_LENS and appends it to "
        "active_lenses when equities are configured.",
    ),
)
def test_research_worker_has_equity_chain_lens():
    src = (_ROOT / "aletheia" / "agents" / "research_worker.py").read_text(encoding="utf-8")
    assert "_EQUITY_CHAIN_LENS" in src, (
        "research_worker must define _EQUITY_CHAIN_LENS"
    )
    assert "equity_chain" in src, (
        "research_worker must register 'equity_chain' lens key"
    )
    assert "_get_equity_context_block" in src, (
        "research_worker must define _get_equity_context_block() for context injection"
    )
    assert "active_lenses" in src, (
        "research_worker must build active_lenses list (conditionally includes equity_chain)"
    )


# ---------------------------------------------------------------------------
# EQ-R04 — data_utilization.md §3 has r-series row (R5 compliance)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "EQ-R04", _CONTRACT, "§data-utilization-r-series-row", "L2a",
        "data_utilization.md §3 has a row for r-series tokens (R5: new source "
        "must be registered before collection code is written).",
    ),
)
def test_data_utilization_doc_has_rstock_row():
    doc = (_ROOT / "docs" / "design" / "data" / "data_utilization.md").read_text(encoding="utf-8")
    assert "r-series" in doc and "113" in doc, (
        "data_utilization.md §3 must contain the r-series row with 113-token count"
    )
    assert "equity_chain" in doc or "_fetch_stock_token_price" in doc, (
        "data_utilization.md must reference the consumption path (equity_chain or "
        "_fetch_stock_token_price)"
    )


# ---------------------------------------------------------------------------
# EQ-R05 — schema has observed_stock_tokens field
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "EQ-R05", _CONTRACT, "§schema-observed-stock-tokens", "L2a",
        "MarketDataConfig exposes observed_stock_tokens (list[str]) as the "
        "primary field for tokenized equity/ETF symbols.",
    ),
)
def test_schema_has_observed_stock_tokens():
    from aletheia.config.schema import MarketDataConfig
    cfg = MarketDataConfig()
    assert hasattr(cfg, "observed_stock_tokens"), (
        "MarketDataConfig must have observed_stock_tokens field"
    )
    tokens = cfg.observed_stock_tokens
    assert isinstance(tokens, list), "observed_stock_tokens must be a list"
    # Default should include at least one r-series entry
    r_series = [t for t in tokens if t.startswith("r") and "/" in t]
    assert r_series, "Default observed_stock_tokens must include at least one r-series entry"


# ---------------------------------------------------------------------------
# EQ-R06 — stock_token_market_type() returns correct types (spot / swap)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "EQ-R06", _CONTRACT, "§stock-token-market-type", "L2a",
        "stock_token_market_type() returns 'spot' for r-series / ON-series tokens "
        "and respects _STOCK_TOKEN_NON_SPOT overrides for future swap instruments.",
    ),
)
def test_stock_token_market_type():
    from aletheia.data.symbols import stock_token_market_type, MARKET_SPOT

    # r-series are spot by default
    assert stock_token_market_type("rNVDA/USDT") == MARKET_SPOT
    assert stock_token_market_type("rTSLA/USDT") == MARKET_SPOT

    # ON-series are spot by default
    assert stock_token_market_type("AAPLON/USDT") == MARKET_SPOT

    # _STOCK_TOKEN_NON_SPOT extension point exists in symbols.py
    src = (_ROOT / "aletheia" / "data" / "symbols.py").read_text(encoding="utf-8")
    assert "_STOCK_TOKEN_NON_SPOT" in src, (
        "symbols.py must define _STOCK_TOKEN_NON_SPOT dict for swap overrides"
    )


# ---------------------------------------------------------------------------
# EQ-R07 — SerenityOpportunity model carries asset_class + market_type fields
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "EQ-R07", _CONTRACT, "§serenity-opp-instrument-classification", "L2a",
        "SerenityOpportunity has asset_class and market_type fields so the "
        "frontend can call fmtSymbol(symbol, market_type) correctly for equity "
        "spot tokens (slash kept) vs future swap tokens (slash stripped).",
    ),
)
def test_serenity_opportunity_has_instrument_fields():
    from aletheia.storage.models import SerenityOpportunity

    opp = SerenityOpportunity(
        theme_id="test-theme",
        symbol="rNVDA/USDT",
        direction="long",
    )
    assert hasattr(opp, "asset_class"), "SerenityOpportunity must have asset_class field"
    assert hasattr(opp, "market_type"), "SerenityOpportunity must have market_type field"
    assert opp.asset_class == "crypto", "Default asset_class must be 'crypto'"
    assert opp.market_type == "spot", "Default market_type must be 'spot'"

    # Equity override works
    opp_eq = SerenityOpportunity(
        theme_id="test-theme",
        symbol="rNVDA/USDT",
        direction="long",
        asset_class="equity",
        market_type="spot",
    )
    assert opp_eq.asset_class == "equity"
    assert opp_eq.market_type == "spot"
