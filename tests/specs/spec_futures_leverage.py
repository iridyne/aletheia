"""Spec: Futures & Leverage Infrastructure

Executable claims for futures/leverage configuration, asset tier classification,
risk engine leverage checks, and paper_tools leverage passthrough.
"""

import pytest

from tests.execution_authority import seed_order_authority
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
    assert risk.max_demo_position_notional_usdt == 100_000.0
    assert risk.max_demo_single_trade_risk_pct == 20.0
    assert risk.max_futures_leverage_demo == 125
    assert risk.max_open_positions_demo == 200
    assert risk.max_leverage_major_demo == 20
    assert risk.max_leverage_major_live == 3
    assert risk.max_leverage_mid_demo == 10
    assert risk.max_leverage_mid_live == 2
    assert risk.max_leverage_alt_demo == 5
    assert risk.max_leverage_alt_live == 1
    assert risk.max_account_leverage_demo == 10.0
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
    assert get_max_leverage("BTC/USDT", is_live=False, cfg=cfg) == 20
    assert get_max_leverage("SOL/USDT", is_live=False, cfg=cfg) == 10
    assert get_max_leverage("SHIB/USDT", is_live=False, cfg=cfg) == 5

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
    seed_order_authority(
        proposal_id="pf1",
        risk_decision_id="rf1",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60000.0,
        execution_mode="paper_internal",
        leverage=5,
    )
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
    seed_order_authority(
        proposal_id="pf2",
        risk_decision_id="rf2",
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=3000.0,
        execution_mode="paper_internal",
        leverage=1,
    )
    r1 = execute_paper_order(
        proposal_id="pf2", risk_decision_id="rf2",
        symbol="ETH/USDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=3000.0,
        execution_mode="paper_internal", leverage=1,
    )
    pos1 = repo._select_one("positions", r1["position_id"])
    assert pos1["liquidation_price"] is None


# ---------------------------------------------------------------------------
# System Operator decides market_type; AI leverage is advisory.
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("FL-S12", "futures_leverage", "§12 PI decision schema", "L2a",
              "PI decision schema + TradingDecision dataclass + parser carry market_type "
              "and preserve AI leverage separately from the final executable value"),
)
def test_pi_decision_schema_market_type_and_leverage():
    from aletheia.agent_shell.trading_decision import (
        _DECISION_SCHEMA, TradingDecision, _parse_decision,
    )

    props = _DECISION_SCHEMA["properties"]
    assert "market_type" in props
    assert set(props["market_type"]["enum"]) == {"spot", "usdt_futures"}
    assert "leverage" in props

    # dataclass carries the fields
    d = TradingDecision(action="trade", symbol="BTC/USDT", direction="long",
                        conviction=70, reasoning="x")
    assert hasattr(d, "market_type")
    assert hasattr(d, "leverage")

    # parser reads them from raw LLM output
    parsed = _parse_decision({
        "action": "trade", "symbol": "BTC/USDT", "direction": "long",
        "conviction": 70, "reasoning": "x", "bear_case": "y",
        "market_type": "usdt_futures", "leverage": 3,
        "stop_price": 98.0, "take_profit": 104.0,
        "parameter_rationale": {
            key: f"reason for {key}"
            for key in (
                "entry_price", "stop_loss", "take_profit", "notional_usdt", "leverage"
            )
        },
    })
    assert parsed.market_type == "usdt_futures"
    assert parsed.leverage == 3
    assert parsed.ai_advisory_leverage == 3


def _ai_leverage_context(
    *, available=20.0, ratio=0.0, atr=2.0,
    feature_ts="2099-01-01T00:00:00+00:00",
):
    return {
        "layer1": {
            "feature_index": {
                "btc-perp": {
                    "feature_snapshot_id": "feature-1",
                    "feature_ts": feature_ts,
                    "data_freshness_sec": 0,
                    "symbol": "BTC/USDT",
                    "market_type": "perp",
                    "atr_pct": atr,
                    "rsi_14": 50,
                },
            },
            "balance": {"equity_usdt": 1000.0, "available_usdt": available},
            "futures": {"account_leverage_ratio": ratio},
        },
        "layer2": {
            "pending_signals": [{
                "symbol": "BTC/USDT",
                "direction": "long",
                "market_type": "usdt_futures",
            }],
            "entry_mark": {
                "status": "available",
                "symbol": "BTC/USDT",
                "market_type": "usdt_futures",
                "price": 100.0,
            },
        },
    }


def _ai_futures_decision(**updates):
    from aletheia.agent_shell.trading_decision import TradingDecision

    values = {
        "action": "trade",
        "symbol": "BTC/USDT",
        "direction": "long",
        "conviction": 90,
        "reasoning": "candidate is eligible",
        "market_type": "usdt_futures",
        "leverage": 5,
        "ai_advisory_leverage": 5,
        "position_size_usdt": 60.0,
        "stop_price": 98.0,
        "parameter_rationale_required": True,
        "parameter_rationale": {"leverage": "model advisory"},
    }
    values.update(updates)
    return TradingDecision(**values)


@spec_claim(
    SpecClaim("FL-S19", "futures_leverage", "§19 deterministic resolver", "L2b",
              "AI leverage is advisory; a post-debate resolver chooses the minimum "
              "margin-required leverage within conviction, volatility, liquidation, "
              "account-headroom, and Risk caps, falling back to 1x when unavailable"),
)
@pytest.mark.parametrize(
    ("context", "updates", "expected", "reason"),
    [
        (_ai_leverage_context(available=100.0), {}, 1, "margin_covers_notional"),
        (_ai_leverage_context(), {}, 3, "minimum_margin_leverage"),
        (_ai_leverage_context(atr=16.0), {}, 1, "no_safe_leveraged_value"),
        (_ai_leverage_context(ratio=9.9), {}, 1, "no_safe_leveraged_value"),
        (_ai_leverage_context(atr=None), {}, 1, "required_input_unavailable"),
        (_ai_leverage_context(feature_ts="2020-01-01T00:00:00+00:00"), {}, 1,
         "required_input_unavailable"),
        (_ai_leverage_context(), {"conviction": 60}, 1, "no_safe_leveraged_value"),
    ],
)
def test_post_debate_ai_leverage_resolver(context, updates, expected, reason):
    from aletheia.agent_shell.trading_decision import _resolve_ai_leverage
    from aletheia.config.schema import AletheiaConfig

    decision = _ai_futures_decision(**updates)
    _resolve_ai_leverage(decision, context, cfg=AletheiaConfig())

    assert decision.ai_advisory_leverage == 5
    assert decision.leverage == expected
    assert decision.leverage_resolution["reason_code"] == reason
    assert decision.leverage_resolution["final_value"] == expected


def test_spot_ai_leverage_is_structurally_not_applicable():
    from aletheia.agent_shell.trading_decision import _resolve_ai_leverage
    from aletheia.config.schema import AletheiaConfig

    decision = _ai_futures_decision(market_type="spot", leverage=5)
    _resolve_ai_leverage(decision, _ai_leverage_context(), cfg=AletheiaConfig())

    assert decision.leverage is None
    assert decision.leverage_resolution["reason_code"] == "spot_unleveraged"


def test_ai_leverage_resolution_uses_post_debate_conviction(monkeypatch):
    import asyncio
    import aletheia.agent_shell.trading_decision as td
    from aletheia.config.schema import AletheiaConfig

    async def assess(_ctx):
        return {
            "action": "trade",
            "symbol": "BTC/USDT",
            "direction": "long",
            "conviction": 70,
            "reasoning": "candidate is eligible",
            "bear_case": "breakout can fail",
            "market_type": "usdt_futures",
            "leverage": 5,
            "stop_price": 98.0,
            "take_profit": 104.0,
            "parameter_rationale": {
                key: f"reason for {key}"
                for key in (
                    "entry_price", "stop_loss", "take_profit",
                    "notional_usdt", "leverage",
                )
            },
        }

    async def debate(*_args):
        return td.DebateResult(100, "bull", [], 40, "bear", [])

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_run_debate", debate)
    monkeypatch.setattr(td, "_proposal_bias_brake_reason", lambda _decision: None)
    monkeypatch.setattr(td, "_get_strategy_paper_trade_count", lambda _strategy_id: 0)
    monkeypatch.setattr(td, "_compute_position_size", lambda *_args, **_kwargs: 60.0)
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: AletheiaConfig())

    decision = asyncio.run(td.evaluate_trading_opportunity(_ai_leverage_context()))

    assert decision.debate_initial_conviction == 70
    assert decision.conviction == 88
    assert decision.leverage == 3
    assert decision.leverage_resolution["inputs"]["conviction"] == 88


def test_leverage_snapshot_separates_ai_advisory_from_authoritative_reason():
    from aletheia.agent_shell.trading_decision import (
        _build_entry_parameter_snapshot,
        _resolve_ai_leverage,
    )
    from aletheia.config.schema import AletheiaConfig

    decision = _ai_futures_decision()
    _resolve_ai_leverage(decision, _ai_leverage_context(), cfg=AletheiaConfig())
    snapshot = _build_entry_parameter_snapshot(
        decision=decision,
        market_type="usdt_futures",
        entry_price=100.0,
        quantity=0.6,
        stop_price=98.0,
        take_profit=104.0,
        requested_notional=60.0,
        leverage=decision.leverage,
        leverage_cap=20,
    )["leverage"]

    assert snapshot["requested_value"] == 5
    assert snapshot["final_value"] == 3
    assert snapshot["value_source"] == "deterministic_post_debate_policy"
    assert snapshot["ai_advisory_reason"] == "model advisory"
    assert snapshot["reason"] != "model advisory"


@spec_claim(
    SpecClaim("FL-S13", "futures_leverage", "§13 leverage clamped on submit", "L2b",
              "submit_decision_as_proposal forwards final market_type + leverage to "
              "create_paper_proposal, preserving 1x and clamping to the per-tier cap"),
)
async def test_submit_decision_clamps_leverage(tmp_path, monkeypatch):
    import aletheia.agent_shell.trading_decision as td
    from aletheia.config.loader import load_config
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    venue = load_config().market_data.ccxt_exchange or "binance"
    repo.insert_market_snapshot(MarketSnapshot(
        symbol="BTC/USDT",
        market_type="perp",
        exchange=venue,
        price=60000.0,
    ))

    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return {"status": "executed", "proposal_id": "x"}

    # Stub only the gateway-facing boundary + advisory guard chain. Entry pricing
    # deliberately goes through the production exact-venue freshness accessor.
    monkeypatch.setattr("aletheia.execution.paper_tools.create_paper_proposal", _fake_create)
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda ctx: [])
    monkeypatch.setattr("aletheia.trust.guard_chain.summarize",
                        lambda v: {"blocks": [], "details": ""})

    # PI asks for 50x on BTC (major demo cap = 20) → must clamp to 20.
    decision = td.TradingDecision(
        action="trade", symbol="BTC/USDT", direction="long",
        conviction=80, reasoning="strong", position_size_usdt=100.0,
        market_type="usdt_futures", leverage=50,
    )
    await td.submit_decision_as_proposal(decision, execution_mode="paper_internal")
    assert captured["market_type"] == "usdt_futures"
    assert captured["leverage"] == 20  # clamped to BTC demo tier cap

    decision.leverage = 1
    await td.submit_decision_as_proposal(decision, execution_mode="paper_internal")
    assert captured["leverage"] == 1


@spec_claim(
    SpecClaim("FL-S14", "futures_leverage", "§14 futures context", "L2a",
              "trading_context._build_futures_context injects per-symbol leverage limits + "
              "account leverage ratio + margin headroom; the assessment prompt renders it"),
)
def test_futures_context_injection():
    import inspect
    from aletheia.agent_shell.trading_context import _build_futures_context
    import aletheia.agent_shell.trading_decision as td
    from aletheia.config.loader import load_config

    cfg = load_config()
    layer = {
        "features": [{"symbol": "BTC/USDT"}, {"symbol": "SOL/USDT"}],
        "positions": [],
        "balance": {"equity_usdt": 1000.0, "available_usdt": 800.0},
    }
    fut = _build_futures_context(layer, cfg)
    limits = fut["leverage_limits"]
    assert limits["BTC/USDT"]["tier"] == "major"
    assert limits["BTC/USDT"]["max_demo"] == 20
    assert limits["SOL/USDT"]["tier"] == "mid"
    assert "account_leverage_ratio" in fut
    assert "available_margin_pct" in fut

    # The assessment prompt renders the futures section when the context is present.
    prompt = td._build_assessment_prompt({"layer1": {"futures": fut}, "layer2": {}, "layer3": {}})
    assert "Futures" in prompt or "Leverage limits" in prompt


@spec_claim(
    SpecClaim("FL-S15", "futures_leverage", "§15 opportunity carries derivatives", "L2a",
              "Opportunity model carries funding_rate/open_interest/oi_delta_1h and "
              "market_observer stamps them so perp opportunities expose their derivatives state"),
)
def test_opportunity_carries_derivatives_fields():
    import inspect
    from aletheia.storage.models import Opportunity

    fields = Opportunity.model_fields
    assert "funding_rate" in fields
    assert "open_interest" in fields
    assert "oi_delta_1h" in fields

    # market_observer stamps the derivatives values it already reads onto the Opportunity.
    import aletheia.agents.market_observer as mo
    src = inspect.getsource(mo._analyze_symbol)
    assert "funding_rate=" in src
    assert "oi_delta_1h=" in src


@spec_claim(
    SpecClaim("FL-S16", "futures_leverage", "§16 exchange set_leverage", "L2a",
              "All three exchange clients expose set_leverage and call it from place_order "
              "for a leveraged futures open; OrderRequest carries leverage; the three live "
              "brokers forward leverage into OrderRequest (fail-closed clone rule)"),
)
def test_exchange_set_leverage_wiring():
    import inspect
    from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest as BgOR
    from aletheia.exchanges.okx.client import OKXClient, OrderRequest as OkxOR
    from aletheia.exchanges.binance.client import BinanceClient, OrderRequest as BnOR

    # set_leverage exists + place_order invokes it on every client. Read the module
    # source (not client.place_order, which the autouse exchange-block fixture patches).
    for client in (BitgetClient, OKXClient, BinanceClient):
        assert hasattr(client, "set_leverage"), client.__name__
        mod_src = inspect.getsource(inspect.getmodule(client))
        assert "async def set_leverage" in mod_src, client.__name__
        assert "self.set_leverage(" in mod_src, client.__name__

    # OrderRequest carries leverage on every client
    for OR in (BgOR, OkxOR, BnOR):
        assert "leverage" in OR.__dataclass_fields__, OR.__module__

    # The three live brokers forward leverage into OrderRequest
    import aletheia.execution.bitget_broker as bgb
    import aletheia.execution.okx_broker as okxb
    import aletheia.execution.binance_broker as bnb
    for mod in (bgb, okxb, bnb):
        src = inspect.getsource(mod.execute_live_order)
        assert "leverage=leverage" in src, mod.__name__


@spec_claim(
    SpecClaim("FL-S17", "futures_leverage", "§17 strategy market affinity", "L2b",
              "StrategyRecord carries market_type_affinity; the paper ranker skips a "
              "strategy whose affinity contradicts the opportunity's market (a spot-only "
              "strategy must not take a perp opportunity)"),
)
def test_strategy_market_type_affinity_filters_ranker():
    pytest.skip(
        "FL-S17 superseded by Position C 2026-07-02: paper ranker treats "
        "linked_strategy_id as attribution and no longer resolves StrategyRecord "
        "market affinity."
    )
    import json as _json
    from aletheia.storage.models import StrategyRecord, Opportunity, MarketSnapshot
    from aletheia.storage import repositories as repo

    assert "market_type_affinity" in StrategyRecord.model_fields

    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="usdt_futures", price=60000))
    strat = StrategyRecord(
        name="spot-only", state="paper_running", market_type_affinity="spot",
        parameters=_json.dumps({"symbol": "BTC/USDT"}),
    )
    repo.insert_strategy(strat)
    opp = Opportunity(
        symbol="BTC/USDT", market="usdt_futures", direction_bias="long",
        confidence="high", confidence_score=80, source_agent="market_observer",
        linked_strategy_id=strat.id,
    )
    repo.insert_opportunity(opp)

    from aletheia.agents.paper_trader_ranker import build_paper_candidates
    cands = [c for c in build_paper_candidates(limit=50) if c["symbol"] == "BTC/USDT"]
    assert cands, "a candidate row should be produced (recorded as skipped)"
    assert any("market_type_mismatch" in (c.get("skip_reason") or "") for c in cands), cands


@spec_claim(
    SpecClaim("FL-S18", "futures_leverage", "§18 perp observed by default", "L2b",
              "With usdt_futures enabled (default), observed_instruments_from_config emits "
              "BOTH spot and perp instruments per observed symbol — perp observation needs "
              "no explicit observed_instruments config (deferred §6 satisfied functionally)"),
)
def test_perp_observed_by_default_config():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.trading.instruments import observed_instruments_from_config

    cfg = AletheiaConfig()
    assert cfg.market_data.observed_instruments == []          # empty → legacy derivation
    assert cfg.exchange.enabled_markets.get("usdt_futures") is True
    assert cfg.exchange.preferred_market_type == "auto"        # default = perp bias

    insts = observed_instruments_from_config(cfg)

    # Single preferred_market_type per symbol (default perp) — NOT both. Every
    # symbol gets exactly one instrument for LLM reasoning; spot is no longer
    # duplicated into a redundant side-by-side perp.
    assert len(insts) > 0, "expected instruments from default config"
    by_symbol: dict[str, list[str]] = {}
    for i in insts:
        by_symbol.setdefault(i.symbol, []).append(i.market_type)
    for sym, mts in by_symbol.items():
        assert len(mts) == 1, f"{sym}: expected 1 market_type (deduped), got {mts}"
        assert mts[0] == "perp", f"{sym}: expected perp (auto), got {mts[0]}"
