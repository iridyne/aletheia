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
        "symbols.py exposes ticker_to_stock_tokens() for the equity observer, "
        "r-series before ON-series.",
    ),
)
def test_stock_token_functions_exist():
    from aletheia.data.symbols import ticker_to_stock_tokens

    tokens = ticker_to_stock_tokens("AAPL")
    assert tokens[0] == "rAAPL/USDT", "r-series must be first (higher liquidity)"
    assert "AAPLON/USDT" in tokens, "AAPLON/USDT must be in AAPL token list"
    # Single-letter NYSE tickers are real r-series listings (rV = Visa).
    assert ticker_to_stock_tokens("V")[0] == "rV/USDT"


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
    src = (_ROOT / "extensions" / "research" / "equity_observer.py").read_text(encoding="utf-8")
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
    assert opp.market_type == "perp", "Default market_type must be 'perp' (perp-only product)"

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
