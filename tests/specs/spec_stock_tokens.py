"""Spec: tokenized-equity config and Serenity instrument fields (EQ-* claims).

All claims attach to data_utilization_contract (observation-only data domain,
not a new trust boundary).

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
Without that registration the conformance matrix collects zero EQ-* cases.
"""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_CONTRACT = "data_utilization_contract"


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
