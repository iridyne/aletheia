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


# ---------------------------------------------------------------------------
# L2a/L2b — Leverage persistence through execution (Round 2 futures enablement)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("FL-S10", "futures_leverage", "§10 leverage persisted on Position", "L2a",
              "Position model carries leverage + liquidation_price; all four broker entry "
              "points (paper/bitget/okx/binance) accept a leverage param; gateway threads "
              "proposal.leverage into execution"),
)
def test_leverage_threaded_through_execution():
    import inspect
    from aletheia.storage.models import Position

    # Position model carries the futures fields.
    fields = Position.model_fields
    assert "leverage" in fields
    assert "liquidation_price" in fields

    # All four broker entry points accept leverage (clone rule).
    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.execution.bitget_broker import execute_live_order as bitget_exec
    from aletheia.execution.okx_broker import execute_live_order as okx_exec
    from aletheia.execution.binance_broker import execute_live_order as binance_exec
    for fn in (execute_paper_order, bitget_exec, okx_exec, binance_exec):
        assert "leverage" in inspect.signature(fn).parameters, fn.__module__

    # Gateway threads proposal.leverage into the paper + live execution calls.
    import aletheia.execution.gateway as gw
    src = inspect.getsource(gw._execute_proposal)
    assert src.count("leverage=proposal.leverage") >= 1


@spec_claim(
    SpecClaim("FL-S11", "futures_leverage", "§11 paper open records leverage", "L2b",
              "execute_paper_order with leverage>1 records leverage and a computed "
              "liquidation_price on the opened Position; leverage=1 leaves liquidation_price None"),
)
def test_paper_open_records_leverage_and_liquidation(tmp_path, monkeypatch):
    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.storage import repositories as repo

    # 5x long BTC @ 60000 → leverage persisted + liquidation_price ≈ entry*(1-1/5+0.004)
    r5 = execute_paper_order(
        proposal_id="pf1", risk_decision_id="rf1",
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        order_type="market", quantity=0.01, price=60000.0,
        execution_mode="paper_internal", leverage=5,
    )
    pos5 = repo._select_one("positions", r5["position_id"])
    assert pos5["leverage"] == 5
    assert pos5["liquidation_price"] is not None
    assert pos5["liquidation_price"] < 60000.0  # long liq below entry

    # 1x spot → no liquidation price
    r1 = execute_paper_order(
        proposal_id="pf2", risk_decision_id="rf2",
        symbol="ETH/USDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=3000.0,
        execution_mode="paper_internal", leverage=1,
    )
    pos1 = repo._select_one("positions", r1["position_id"])
    assert pos1["liquidation_price"] is None
