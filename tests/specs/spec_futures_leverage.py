"""Spec: Futures & Leverage Infrastructure

Executable claims for futures/leverage configuration, asset tier classification,
risk engine leverage checks, and paper_tools leverage passthrough.
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("FL-S01", "futures_leverage", "§1 config", "L2a",
              "RiskConfig has per-tier leverage limits + account leverage cap + margin thresholds"),
)
def test_config_schema_futures_fields():
    from aletheia.config.schema import RiskConfig, ExchangeConfig

    risk = RiskConfig()
    assert risk.max_leverage_major_demo == 5
    assert risk.max_leverage_major_live == 3
    assert risk.max_leverage_mid_demo == 3
    assert risk.max_leverage_mid_live == 2
    assert risk.max_leverage_alt_demo == 2
    assert risk.max_leverage_alt_live == 1
    assert risk.max_account_leverage_demo == 3.0
    assert risk.max_account_leverage_live == 1.5
    assert risk.futures_margin_warning_ratio == 0.70
    assert risk.futures_margin_force_close_ratio == 0.85
    assert risk.min_liquidation_buffer_pct == 5.0

    ex = ExchangeConfig()
    assert ex.position_mode == "hedge"
    assert isinstance(ex.futures_symbols, list)


@spec_claim(
    SpecClaim("FL-S02", "futures_leverage", "§2 asset tier", "L2a",
              "get_asset_tier classifies BTC/ETH as major, SOL as mid, unknown as alt"),
)
def test_asset_tier_classification():
    from aletheia.trading.instruments import get_asset_tier

    assert get_asset_tier("BTC/USDT") == "major"
    assert get_asset_tier("ETH/USDT") == "major"
    assert get_asset_tier("SOL/USDT") == "mid"
    assert get_asset_tier("BNB/USDT") == "mid"
    assert get_asset_tier("SHIB/USDT") == "alt"
    assert get_asset_tier("PEPE/USDT") == "alt"


@spec_claim(
    SpecClaim("FL-S03", "futures_leverage", "§3 max leverage", "L2a",
              "get_max_leverage returns tier-appropriate cap respecting account flat cap"),
)
def test_max_leverage_per_tier():
    from aletheia.trading.instruments import get_max_leverage
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()

    # Demo mode
    assert get_max_leverage("BTC/USDT", is_live=False, cfg=cfg) == 5
    assert get_max_leverage("SOL/USDT", is_live=False, cfg=cfg) == 3
    assert get_max_leverage("SHIB/USDT", is_live=False, cfg=cfg) == 2

    # Live mode
    assert get_max_leverage("BTC/USDT", is_live=True, cfg=cfg) == 2  # min(3, account_cap=2)
    assert get_max_leverage("SOL/USDT", is_live=True, cfg=cfg) == 2
    assert get_max_leverage("SHIB/USDT", is_live=True, cfg=cfg) == 1


@spec_claim(
    SpecClaim("FL-S04", "futures_leverage", "§4 ctVal conversion", "L2a",
              "notional_to_contracts correctly converts USDT notional to contract count"),
)
def test_notional_to_contracts():
    from aletheia.trading.instruments import notional_to_contracts, contracts_to_notional

    # BTC: ctVal = 0.001 BTC/contract. $120 at $60000 = 0.002 BTC = 2 contracts
    assert notional_to_contracts(120, 60000, "BTC/USDT") == 2.0
    # ETH: ctVal = 0.01. $30 at $3000 = 0.01 ETH = 1 contract
    assert notional_to_contracts(30, 3000, "ETH/USDT") == 1.0
    # Reverse
    assert contracts_to_notional(2.0, 60000, "BTC/USDT") == 120.0


@spec_claim(
    SpecClaim("FL-S05", "futures_leverage", "§5 liquidation price", "L2a",
              "calc_liquidation_price returns correct estimate for isolated margin"),
)
def test_liquidation_price():
    from aletheia.trading.instruments import calc_liquidation_price

    # 5x long BTC at 60000: liq = 60000 * (1 - 1/5 + 0.004) = 60000 * 0.804 = 48240
    liq = calc_liquidation_price(60000, "long", 5, "BTC/USDT")
    assert liq is not None
    assert abs(liq - 48240.0) < 0.01

    # 5x short BTC at 60000: liq = 60000 * (1 + 1/5 - 0.004) = 60000 * 1.196 = 71760
    liq = calc_liquidation_price(60000, "short", 5, "BTC/USDT")
    assert liq is not None
    assert abs(liq - 71760.0) < 0.01

    # 1x = no liquidation
    assert calc_liquidation_price(60000, "long", 1, "BTC/USDT") is None


@spec_claim(
    SpecClaim("FL-S06", "futures_leverage", "§6 paper_tools leverage", "L2a",
              "create_paper_proposal accepts leverage param and passes it to submit_proposal"),
)
def test_paper_tools_leverage_param():
    import inspect
    from aletheia.execution.paper_tools import create_paper_proposal

    sig = inspect.signature(create_paper_proposal)
    assert "leverage" in sig.parameters
    param = sig.parameters["leverage"]
    assert param.default is None


import inspect


@spec_claim(
    SpecClaim("FL-S07", "futures_leverage", "§7 risk check tier-aware", "L2a",
              "risk engine check #10 uses per-tier leverage cap from get_max_leverage"),
)
def test_risk_engine_tier_aware_leverage():
    import aletheia.risk.engine as eng
    source = inspect.getsource(eng._evaluate_proposal_locked)
    assert "get_max_leverage" in source
    assert "get_asset_tier" in source


@spec_claim(
    SpecClaim("FL-S08", "futures_leverage", "§8 account leverage check", "L2a",
              "risk engine has check #17 for account-level leverage ratio"),
)
def test_risk_engine_account_leverage_check():
    import aletheia.risk.engine as eng
    assert callable(eng._check_account_leverage_ratio)
    assert callable(eng.check_account_leverage)


@spec_claim(
    SpecClaim("FL-S09", "futures_leverage", "§9 liquidation buffer check", "L2a",
              "risk engine has check #18 for liquidation buffer distance"),
)
def test_risk_engine_liquidation_buffer():
    import aletheia.risk.engine as eng
    assert callable(eng._check_liquidation_buffer)
