"""Spec: Data Utilization Contract — every acquired data domain must be routed
to a consuming module (物尽其用 / no orphan data).

First principle: data exists to inform a decision. A persisted datum with no
consumer is pure cost (I/O + storage + audit noise) AND a hazard — it fabricates
a sense of richness ("we have OI data") that can mislead. So every data domain
must EITHER have a declared decision-chain consumer OR be explicitly marked
observation-only. These claims lock the consumers that close known gaps and
guard against regression back to collected-but-unused.

Claims attach to data_utilization_contract.

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
Without that registration the conformance matrix collects zero DU-* cases.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_MARKET_OBS = _ROOT / "aletheia" / "agents" / "market_observer.py"
_CONTEXT_BUILDER = _ROOT / "aletheia" / "data" / "context_builder.py"

_CONTRACT = "data_utilization_contract"


# ---------------------------------------------------------------------------
# DU-S01 — render hook for the cross-venue (Hyperliquid) funding signal exists
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S01", _CONTRACT, "§cross-venue funding", "L2a",
        "render_enriched_context_md accepts an xvenue_funding parameter — the "
        "render hook that routes the Hyperliquid feed into the LLM context.",
    ),
)
def test_render_has_xvenue_funding_param():
    import inspect
    from aletheia.data.context_builder import render_enriched_context_md
    sig = inspect.signature(render_enriched_context_md)
    assert "xvenue_funding" in sig.parameters, (
        "render_enriched_context_md must expose xvenue_funding to consume Hyperliquid"
    )


# ---------------------------------------------------------------------------
# DU-S02 — Hyperliquid feed is ROUTED to the decision context (no longer orphan)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S02", _CONTRACT, "§no-orphan: hyperliquid", "L2c",
        "market_observer reads the hyperliquid-tagged funding rows AND passes "
        "xvenue_funding into the rendered LLM context — i.e. the acquired "
        "Hyperliquid data reaches a decision consumer, not just collector/doctor.",
    ),
)
def test_hyperliquid_routed_to_decision_context():
    src = _MARKET_OBS.read_text(encoding="utf-8")
    # the cross-venue read (explicit exchange filter) ...
    assert 'exchange="hyperliquid"' in src or "exchange='hyperliquid'" in src, (
        "market_observer must read hyperliquid funding via explicit exchange filter"
    )
    # ... and it must actually flow into the rendered context
    assert "xvenue_funding" in src, (
        "market_observer must pass xvenue_funding into render_enriched_context_md"
    )


# ---------------------------------------------------------------------------
# DU-S03 — DefiLlama on-chain feed is ROUTED to the decision context
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S03", _CONTRACT, "§no-orphan: onchain", "L2c",
        "market_observer reads latest_onchain_snapshots AND passes onchain= into "
        "the rendered LLM context — DefiLlama on-chain is consumed, not orphan.",
    ),
)
def test_onchain_routed_to_decision_context():
    src = _MARKET_OBS.read_text(encoding="utf-8")
    assert "latest_onchain_snapshots" in src, "market_observer must read onchain snapshots"
    assert "onchain=" in src, "market_observer must pass onchain= into render"


# ---------------------------------------------------------------------------
# DU-B01 — cross-venue funding renders an annualized divergence (behavioral)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-B01", _CONTRACT, "§cross-venue funding", "L2b",
        "Given Hyperliquid + primary annualized funding, render emits a "
        "'Cross-venue funding' section comparing ANNUALIZED rates (HL 1h vs "
        "primary 8h interval are not comparable raw) with the divergence in pp.",
    ),
)
def test_cross_venue_funding_renders_annualized_divergence():
    from aletheia.data.context_builder import render_enriched_context_md
    xv = {"BTC/USDT": {"hl_ann": 0.109, "hl_predicted_ann": 0.120, "primary_ann": 0.055}}
    out = render_enriched_context_md(["BTC/USDT"], {}, {}, None, xvenue_funding=xv)
    assert "Cross-venue funding" in out, "cross-venue section missing"
    assert "Hyperliquid" in out and "primary" in out, "must compare HL vs primary"
    # 0.109 - 0.055 = 0.054 → +5.4pp divergence
    assert "+5.4pp" in out, f"annualized divergence (pp) wrong in: {out!r}"
    assert "predicted" in out, "predicted funding should be surfaced when present"


# ---------------------------------------------------------------------------
# DU-B02 — no Hyperliquid data for a symbol ⇒ section omitted (no noise)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-B02", _CONTRACT, "§cross-venue funding", "L2b",
        "When a symbol has no Hyperliquid funding, the cross-venue section is "
        "omitted entirely (no empty/garbage rows leak into the prompt).",
    ),
)
def test_cross_venue_funding_omitted_when_no_hl_data():
    from aletheia.data.context_builder import render_enriched_context_md
    out = render_enriched_context_md(
        ["SOL/USDT"], {}, {}, None, xvenue_funding={"SOL/USDT": {"hl_ann": None}}
    )
    assert "Cross-venue funding" not in out, "must not render an empty cross-venue section"


# ---------------------------------------------------------------------------
# DU-C01 — CoinalyzeCollector file exists (structural entry point for
#           liquidation + cross-venue OI collection)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-C01", _CONTRACT, "§coinalyze collector", "L2a",
        "CoinalyzeCollector exists at aletheia/data/collectors/coinalyze.py and "
        "exposes a collect() method — the structural entry point that routes "
        "Coinalyze liquidation and cross-venue OI data into liquidation_events "
        "and open_interest_snapshots so existing downstream consumers "
        "(context_builder liquidation_1h_usd, market_observer crowding_score) "
        "receive real data instead of None/empty.",
    ),
)
def test_coinalyze_collector_exists():
    mod = importlib.import_module("aletheia.data.collectors.coinalyze")
    assert hasattr(mod, "CoinalyzeCollector"), (
        "CoinalyzeCollector class must exist in aletheia.data.collectors.coinalyze"
    )
    collector = mod.CoinalyzeCollector()
    assert hasattr(collector, "collect"), "CoinalyzeCollector must have a collect() method"
    assert hasattr(collector, "domain"), "CoinalyzeCollector must declare domain attribute"


# ---------------------------------------------------------------------------
# DU-C02 — CCXT liquidation dead code removed from derivatives.py
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-C02", _CONTRACT, "§ccxt liquidation dead code", "L2a",
        "The _fetch_liquidations_ccxt call block and _CCXTLiquidationsUnsupported "
        "exception class have been removed from derivatives.py — the inline comment "
        "already admitted 'liquidation data has no downstream consumer'; keeping the "
        "code violates R4 (honest degradation) and misleads future sessions. "
        "Removal is a prerequisite for the clean CoinalyzeCollector integration.",
    ),
)
def test_ccxt_liquidation_dead_code_removed():
    _DERIVATIVES = _ROOT / "aletheia" / "data" / "collectors" / "derivatives.py"
    src = _DERIVATIVES.read_text(encoding="utf-8")
    assert "_fetch_liquidations_ccxt" not in src, (
        "derivatives.py still contains _fetch_liquidations_ccxt dead code — "
        "delete it as part of the Coinalyze integration (GAP-DU-C02)"
    )
    assert "_CCXTLiquidationsUnsupported" not in src, (
        "derivatives.py still contains _CCXTLiquidationsUnsupported exception class — "
        "delete it along with _fetch_liquidations_ccxt (GAP-DU-C02)"
    )


# ---------------------------------------------------------------------------
# DU-S04 — yfinance macro path is declared in §3 (GAP-DU-02 / R5 compliance)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S04", _CONTRACT, "§yfinance macro path R5", "L2a",
        "yfinance_provider.py is an independent data-source path (S&P500/VIX/DXY/"
        "Gold -> LLM context) gated by enable_yfinance_macro. Per R5 it must be "
        "registered in data_utilization.md §3. This claim locks that registration.",
        status="implemented",
    ),
)
def test_yfinance_provider_declared_in_utilization_doc():
    """yfinance_provider.py exists, is gated in market_context, and doc §3 lists it."""
    yf_provider = _ROOT / "aletheia" / "data" / "yfinance_provider.py"
    assert yf_provider.exists(), "yfinance_provider.py must exist"

    market_ctx = _ROOT / "aletheia" / "trading" / "market_context.py"
    src = market_ctx.read_text(encoding="utf-8")
    assert "yfinance_provider" in src, (
        "market_context.py must import yfinance_provider (consumer linkage)"
    )
    assert "enable_yfinance_macro" in src, (
        "yfinance path must be gated by enable_yfinance_macro config flag"
    )

    doc = (_ROOT / "docs" / "design" / "data_utilization.md").read_text(encoding="utf-8")
    assert "yfinance" in doc, (
        "data_utilization.md §3 must register the yfinance macro path (R5)"
    )


# ---------------------------------------------------------------------------
# DU-S05 — regime is inline-derived; no standalone RegimeCollector (GAP-DU-03)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S05", _CONTRACT, "§regime inline derivation", "L2a",
        "§3 lists 'RegimeCollector' as the regime collector, but no such class "
        "exists. Regime is MacroCollector._classify_risk_regime() writing "
        "risk_regime into macro_snapshots. Guards against ghost RegimeCollector "
        "class creation and locks the truthful naming.",
        status="implemented",
    ),
)
def test_regime_is_inline_not_separate_collector():
    """Regime lives inside MacroCollector; no standalone RegimeCollector class."""
    collectors_dir = _ROOT / "aletheia" / "data" / "collectors"
    regime_files = list(collectors_dir.glob("*regime*"))
    assert not regime_files, (
        f"Unexpected RegimeCollector file(s): {regime_files}. "
        "Regime must stay an inline derivation inside MacroCollector."
    )

    macro_py = collectors_dir / "macro.py"
    macro_src = macro_py.read_text(encoding="utf-8")
    assert "_classify_risk_regime" in macro_src, (
        "MacroCollector must contain _classify_risk_regime (inline regime derivation)"
    )

    for py_file in (_ROOT / "aletheia").rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        assert "class RegimeCollector" not in text, (
            f"{py_file}: RegimeCollector must not exist as a class "
            "(ghost code -- regime is inline in MacroCollector)"
        )


# ---------------------------------------------------------------------------
# DU-S06 — CoinalyzeCollector is registered in scheduler + config fields exist
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S06", _CONTRACT, "§coinalyze scheduler + config", "L2a",
        "CoinalyzeCollector 已被 scheduler.py 注册到 _async_data_collectors job "
        "（仿 HyperliquidCollector 模式），且 config/schema.py 定义了 "
        "enable_coinalyze（bool）和 coinalyze_api_key（str|None）字段。"
        "两个结构断言共同保证：采集器不仅存在，还被实际调度，且可通过配置启停。",
        status="implemented",
    ),
)
def test_coinalyze_registered_in_scheduler_and_config():
    import inspect
    from aletheia.config import schema as schema_mod

    # 1) scheduler.py 含 CoinalyzeCollector 字符串（已注册到 _async_data_collectors）
    scheduler_py = _ROOT / "aletheia" / "agent_runtime" / "scheduler.py"
    scheduler_src = scheduler_py.read_text(encoding="utf-8")
    assert "CoinalyzeCollector" in scheduler_src, (
        "scheduler.py must reference CoinalyzeCollector "
        "(it should be registered in _async_data_collectors, see DU-S06)"
    )

    # 2) config schema 定义 enable_coinalyze + coinalyze_api_key
    schema_src = inspect.getsource(schema_mod)
    assert "enable_coinalyze" in schema_src, (
        "config/schema.py must define enable_coinalyze field (DU-S06)"
    )
    assert "coinalyze_api_key" in schema_src, (
        "config/schema.py must define coinalyze_api_key field (DU-S06)"
    )


# ---------------------------------------------------------------------------
# DU-S07 — market_context._TIMEFRAMES covers ['1h', '4h', '1d']
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S07", _CONTRACT, "§ohlcv multi-timeframe", "L2a",
        "market_context.py 的 _TIMEFRAMES 常量覆盖 ['1h', '4h', '1d'] 三个周期"
        "（产品决策：OHLCV 多周期全量采集）。防止有人在不知情的情况下缩减周期数，"
        "导致 LLM 上下文丢失中期或日线视角。",
    ),
)
def test_market_context_timeframes_cover_all_three_periods():
    market_ctx_py = _ROOT / "aletheia" / "trading" / "market_context.py"
    src = market_ctx_py.read_text(encoding="utf-8")
    assert "_TIMEFRAMES" in src, (
        "market_context.py must define _TIMEFRAMES constant (DU-S07)"
    )
    assert "'1h'" in src or '"1h"' in src, (
        "_TIMEFRAMES must include '1h' (DU-S07)"
    )
    assert "'4h'" in src or '"4h"' in src, (
        "_TIMEFRAMES must include '4h' (DU-S07)"
    )
    assert "'1d'" in src or '"1d"' in src, (
        "_TIMEFRAMES must include '1d' (DU-S07)"
    )


# ---------------------------------------------------------------------------
# DU-S08 / DU-S09 — Serenity research pipeline data injection
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DU-S08", _CONTRACT, "§yfinance fundamentals in research_worker", "L2a",
        "research_worker._get_equity_context_block injects yfinance fundamentals "
        "(PE, PS, MCap, RevGrowth, Margin) into the research LLM prompt when "
        "stock tokens are configured — grounding equity_chain lens in real data",
    ),
)
def test_research_worker_injects_yfinance():
    rw = _ROOT / "aletheia" / "agents" / "research_worker.py"
    src = rw.read_text(encoding="utf-8")
    assert "yfinance" in src, (
        "research_worker does not reference yfinance — equity_chain lens has no "
        "fundamentals grounding and relies purely on LLM hallucination"
    )
    assert "trailingPE" in src or "forwardPE" in src, (
        "research_worker does not extract PE ratio from yfinance"
    )
    assert "marketCap" in src, (
        "research_worker does not extract market cap from yfinance"
    )


@spec_claim(
    SpecClaim(
        "DU-S09", _CONTRACT, "§coinalyze flow data in research_worker", "L2a",
        "research_worker._get_flow_context_block injects real OI and liquidation "
        "data from the Coinalyze-populated DB tables into the research LLM prompt — "
        "grounding derivatives flow analysis in actual market data",
    ),
)
def test_research_worker_injects_coinalyze_flow():
    rw = _ROOT / "aletheia" / "agents" / "research_worker.py"
    src = rw.read_text(encoding="utf-8")
    assert "_get_flow_context_block" in src, (
        "research_worker has no _get_flow_context_block — Coinalyze data collected "
        "but never injected into research prompt (orphan data)"
    )
    assert "open_interest_snapshots" in src, (
        "research_worker does not read open_interest_snapshots table"
    )
    assert "liquidation_events" in src, (
        "research_worker does not read liquidation_events table"
    )
    assert "open_interest_usd" in src, (
        "research_worker uses wrong column name for OI value"
    )
    assert "flow_block" in src, (
        "research_worker does not wire _get_flow_context_block into research_context"
    )


# ---------------------------------------------------------------------------
# DU-T01 — tripwire: CoinalyzeCollector actually writes to open_interest_snapshots
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason=(
        "NOT YET IMPLEMENTED — scheduler 待接入 tripwire: "
        "CoinalyzeCollector 尚未被 scheduler 调度，DB 里不存在 exchange='coinalyze' 行。"
        "scheduler 接入完成后去掉 xfail，升级为可执行 L2b 测试。"
    ),
)
@spec_claim(
    SpecClaim(
        "DU-T01", _CONTRACT, "§coinalyze db write tripwire", "L2a",
        "missing_tripwire 占位：CoinalyzeCollector 实际写入 open_interest_snapshots "
        "（exchange='coinalyze' 行存在），即 Coinalyze OI 数据真正流入 DB，"
        "crowding_score 有实际数据可读而非永远 None。"
        "当前状态 '🟠 scheduler 待接入' 使本 claim status=missing，"
        "一旦 scheduler 接入完成须升级为可执行 L2b 测试。",
        status="missing",
    ),
)
def test_coinalyze_data_written_to_db():
    pytest.fail(
        "NOT IMPLEMENTED: CoinalyzeCollector 尚未被 scheduler 调度，"
        "open_interest_snapshots 中无 exchange='coinalyze' 行 (DU-T01 tripwire)"
    )
