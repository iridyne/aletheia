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
_DOCTOR = _ROOT / "aletheia" / "diagnostics" / "doctor.py"
_LOCAL_FUNDING = _ROOT / "aletheia" / "portfolio" / "local_funding.py"
_PAPER_ACCOUNT = _ROOT / "aletheia" / "portfolio" / "paper_account.py"
_VERGEX_STRUCTURE = _ROOT / "aletheia" / "data" / "collectors" / "vergex_structure.py"
_POSITION_X = _ROOT / "aletheia" / "data" / "position_x.py"
_DATA_UTILIZATION_DOC = _ROOT / "docs" / "design" / "data" / "data_utilization.md"
_API_REFERENCE_DOC = _ROOT / "docs" / "design" / "api" / "api_reference.md"

_CONTRACT = "data_utilization_contract"


@spec_claim(
    SpecClaim(
        "DU-S23", _CONTRACT, "§3 primary funding delayed recovery", "L2c",
        "Persisted primary-venue funding history is explicitly consumed by the "
        "local-paper historical-ledger settler without being represented as a "
        "broker account ledger.",
        status="implemented",
    ),
)
def test_primary_funding_history_is_registered_for_local_paper_recovery():
    doc = _DATA_UTILIZATION_DOC.read_text(encoding="utf-8")
    funding = _LOCAL_FUNDING.read_text(encoding="utf-8")
    account = _PAPER_ACCOUNT.read_text(encoding="utf-8")
    assert "historical_ledger" in doc
    assert "不冒充 broker ledger" in doc
    assert "allow_historical_ledger" in funding
    assert "allow_historical_ledger=True" in account


@spec_claim(
    SpecClaim(
        "DU-S20", _CONTRACT, "§3 System Operator no-candidate observation", "L2a",
        "operator_evaluation_events is explicitly observation-only and is consumed by the "
        "read-only skip-halt doctor probe rather than an entry-decision path.",
        status="implemented",
    ),
)
def test_operator_evaluation_observation_is_registered_and_consumed_read_only():
    doc = _DATA_UTILIZATION_DOC.read_text(encoding="utf-8")
    doctor = _DOCTOR.read_text(encoding="utf-8")
    assert "operator_evaluation_events" in doc
    assert "observation-only" in doc
    assert "operator_evaluation_events" in doctor
    assert "def _probe_skip_halt" in doctor


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


@spec_claim(
    SpecClaim(
        "DU-S21", _CONTRACT, "§cross-venue Hyperliquid OI", "L2c",
        "Hyperliquid OI is freshness-qualified, rendered with source time, and its exact row id "
        "is eligible for the System Operator evidence manifest.",
        status="implemented",
    ),
)
def test_hyperliquid_oi_is_consumed_with_provenance():
    obs = _MARKET_OBS.read_text(encoding="utf-8")
    ctx = _CONTEXT_BUILDER.read_text(encoding="utf-8")
    trading_ctx = (_ROOT / "aletheia" / "agent_shell" / "trading_context.py").read_text(
        encoding="utf-8"
    )
    assert "batch_open_interest" in obs
    assert "cross_venue_open_interest" in obs
    assert "hl_open_interest_usd" in ctx and "hl_observed_at" in ctx
    assert "open_interest_id" in trading_ctx and "evidence_manifest" in trading_ctx


@spec_claim(
    SpecClaim(
        "DU-S22", _CONTRACT, "§owner aggregate Position X shadow", "L2c",
        "Owner-authorized aggregate imports preserve source provenance and seed only fixed-horizon "
        "shadow outcomes; source status remains observation-only with a 14-day review floor.",
        status="implemented",
    ),
)
def test_owner_aggregate_structure_path_is_shadow_only():
    collector = _VERGEX_STRUCTURE.read_text(encoding="utf-8")
    shadow = (_ROOT / "aletheia" / "data" / "perp_structure_shadow.py").read_text(
        encoding="utf-8"
    )
    doc = _DATA_UTILIZATION_DOC.read_text(encoding="utf-8")
    assert "ingest_owner_aggregate" in collector
    assert "source_hash" in collector and "decision_authority" in collector
    assert "minimum_shadow_window_days" in collector
    assert "decision_influence" in shadow and '"disabled"' in shadow
    assert "perp_structure_shadow_outcomes" in doc and "14" in doc


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


@spec_claim(
    SpecClaim(
        "DU-S16", _CONTRACT, "§decision-context freshness", "L2a",
        "Orderbook, funding, open-interest, macro, on-chain, and cross-venue "
        "context consumers share one fail-closed freshness policy; stale rows "
        "cannot reach Observer or Universe decisions through a side path.",
    ),
)
def test_enriched_decision_consumers_share_freshness_policy():
    freshness = (_ROOT / "aletheia" / "data" / "freshness.py").read_text(encoding="utf-8")
    observer = _MARKET_OBS.read_text(encoding="utf-8")
    context = _CONTEXT_BUILDER.read_text(encoding="utf-8")
    features = (_ROOT / "aletheia" / "universe" / "features.py").read_text(encoding="utf-8")

    for domain in ("orderbook", "funding", "open_interest", "macro", "onchain"):
        assert f'"{domain}"' in freshness
    assert "build_market_context_from_db" in observer
    assert "fresh_row" in context
    assert "fresh_row" in features


@spec_claim(
    SpecClaim(
        "DU-S17", _CONTRACT, "§market-data operator visibility", "L2a",
        "Collector execution health and persisted data coverage are separate API/UI "
        "signals, while the OHLCV array endpoint exposes cache/provider/database "
        "provenance through a backward-compatible response header.",
    ),
)
def test_market_data_visibility_surfaces_are_wired():
    routes = (_ROOT / "aletheia" / "server" / "routes" / "data.py").read_text(encoding="utf-8")
    client = (_ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    overview = (_ROOT / "frontend" / "src" / "pages" / "Overview.tsx").read_text(encoding="utf-8")
    api_doc = _API_REFERENCE_DOC.read_text(encoding="utf-8")

    assert '@router.get("/market/data-coverage")' in routes
    assert "X-Aletheia-Data-Source" in routes
    assert "getDataCoverage" in client and "data-coverage" in client
    assert "Data coverage" in overview
    assert "/market/data-coverage" in api_doc


@spec_claim(
    SpecClaim(
        "DU-S18", _CONTRACT, "§execution/data exchange alignment", "L2a",
        "External execution and primary market-data exchanges share one alignment "
        "model; divergence requires explicit confirmation in both mutation paths and "
        "remains visible in status, doctor, Settings, and Overview.",
    ),
)
def test_exchange_source_divergence_is_guarded_and_visible():
    alignment = (_ROOT / "aletheia" / "config" / "exchange_alignment.py").read_text(encoding="utf-8")
    settings_route = (_ROOT / "aletheia" / "server" / "routes" / "settings.py").read_text(encoding="utf-8")
    health_route = (_ROOT / "aletheia" / "server" / "routes" / "health.py").read_text(encoding="utf-8")
    overview = (_ROOT / "frontend" / "src" / "pages" / "Overview.tsx").read_text(encoding="utf-8")
    settings_ui = (_ROOT / "frontend" / "src" / "pages" / "Settings.tsx").read_text(encoding="utf-8")

    assert "paper_internal" in alignment and "okx_demo" in alignment
    assert settings_route.count("confirm_exchange_divergence") >= 2
    assert "exchange_alignment" in health_route
    assert "exchange_diverged" in overview
    assert "confirmExchangeDivergence" in settings_ui


@spec_claim(
    SpecClaim(
        "DU-S19", _CONTRACT, "§verified exit private history", "L2a",
        "Bitget, OKX, and Binance private position/fill history is registered and "
        "consumed only through the fail-closed VerifiedExitResult contract.",
    ),
)
def test_verified_exit_private_history_is_registered_and_bounded():
    doc = _DATA_UTILIZATION_DOC.read_text(
        encoding="utf-8"
    )
    protocol = (_ROOT / "aletheia" / "execution" / "verified_exit.py").read_text(
        encoding="utf-8"
    )
    assert "Broker 私有仓位/成交历史" in doc
    for state in (
        "verified_fill", "confirmed_absent_without_fill", "inconclusive",
    ):
        assert state in protocol
    for relative in (
        "aletheia/exchanges/bitget/client.py",
        "aletheia/exchanges/okx/client.py",
        "aletheia/exchanges/binance/client.py",
    ):
        source = (_ROOT / relative).read_text(encoding="utf-8")
        assert "get_verified_position_exit" in source
        assert "inconclusive_verified_exit" in source


# ---------------------------------------------------------------------------
# DU-S13 — perp structure rows are registered and consumed as observation data
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S13", _CONTRACT, "§perp structure observation path", "L2a",
        "perp_structure_snapshots is registered in data_utilization.md, has a "
        "whitelisted fixture collector, and reaches market_observer via "
        "render_enriched_context_md(perp_structure=...), while doctor exposes "
        "freshness. It stays observation-only until a real feed is added.",
    ),
)
def test_perp_structure_registered_and_consumed():
    doc = _DATA_UTILIZATION_DOC.read_text(encoding="utf-8")
    assert "perp_structure_snapshots" in doc, (
        "data_utilization.md §3 must register the perp structure observation path"
    )
    assert "market_observer" in doc and "doctor" in doc, (
        "perp structure registration must declare the prompt consumer and freshness check"
    )

    collector = _VERGEX_STRUCTURE.read_text(encoding="utf-8")
    ctx = _CONTEXT_BUILDER.read_text(encoding="utf-8")
    obs = _MARKET_OBS.read_text(encoding="utf-8")
    doctor = _DOCTOR.read_text(encoding="utf-8")
    assert "VergexStructureCollector" in collector and "_ALLOWED_FIELDS" in collector, (
        "perp structure must have a whitelisted fixture collector"
    )
    assert "perp_structure" in ctx, "context_builder must render perp structure"
    assert "latest_perp_structure" in obs and "perp_structure=" in obs, (
        "market_observer must read and pass perp structure into render"
    )
    assert "data.perp_structure" in doctor, "doctor must expose perp structure freshness"


# ---------------------------------------------------------------------------
# DU-S14 — Position X safe-material parser + compact advisory consumer path
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S14", _CONTRACT, "§position x advisory path", "L2a",
        "Position X safe-material parsing lives in aletheia.data.position_x, "
        "extracts compact knowledge cards / snapshots from authorized offline "
        "materials only, and the resulting advisory-only structure block is "
        "consumed by both market_observer and the System Operator context.",
    ),
)
def test_position_x_parser_and_consumers_exist():
    doc = _DATA_UTILIZATION_DOC.read_text(encoding="utf-8")
    px = _POSITION_X.read_text(encoding="utf-8")
    obs = _MARKET_OBS.read_text(encoding="utf-8")
    tc = (_ROOT / "aletheia" / "agent_shell" / "trading_context.py").read_text(encoding="utf-8")

    assert "Position X" in doc, (
        "data_utilization.md must register the Position X offline-material path"
    )
    assert (
        "extract_position_x_knowledge_cards" in px
        and "extract_position_x_snapshot" in px
        and "extract_position_x_material_bundle" in px
    ), (
        "aletheia.data.position_x must expose the offline bundle/card/snapshot extractors"
    )
    assert "build_position_x_agent_context" in px, (
        "Position X module must expose a compact advisory context builder"
    )
    assert "Position X" in obs or "build_position_x_agent_context" in obs, (
        "market_observer must consume compact Position X structure evidence"
    )
    assert "build_position_x_agent_context" in tc or "position_x" in tc, (
        "System Operator context must consume compact Position X structure evidence"
    )


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

    doc = _DATA_UTILIZATION_DOC.read_text(encoding="utf-8")
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
        "CoinalyzeCollector 已被 scheduler.py 注册到 _async_data_collector_context job "
        "（仿 HyperliquidCollector 模式），且 config/schema.py 定义了 "
        "enable_coinalyze（bool）和 coinalyze_api_key（str|None）字段。"
        "两个结构断言共同保证：采集器不仅存在，还被实际调度，且可通过配置启停。",
        status="implemented",
    ),
)
def test_coinalyze_registered_in_scheduler_and_config():
    import inspect
    from aletheia.config import schema as schema_mod

    # 1) scheduler.py 含 CoinalyzeCollector 字符串（已注册到 context lane）
    scheduler_py = _ROOT / "aletheia" / "agent_runtime" / "scheduler.py"
    scheduler_src = scheduler_py.read_text(encoding="utf-8")
    assert "CoinalyzeCollector" in scheduler_src, (
        "scheduler.py must reference CoinalyzeCollector "
        "(it should be registered in _async_data_collector_context, see DU-S06)"
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
# DU-S12 — official RSS news feeds are registered and consumed
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S12", _CONTRACT, "§official RSS feeds", "L2a",
        "Default news feeds include SEC/CFTC/Ethereum Foundation official RSS "
        "sources, and data_utilization.md §3 registers them as NewsCollector -> "
        "news_items -> market_observer/research_worker consumers.",
    ),
)
def test_official_news_feeds_registered_and_consumed():
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    feed_sources = {feed.source for feed in cfg.news.feeds}
    for source in ("sec-press", "cftc-press", "ethereum-foundation"):
        assert source in feed_sources, (
            f"default news feeds must include official source {source!r} (DU-S12)"
        )

    doc = _DATA_UTILIZATION_DOC.read_text(encoding="utf-8")
    assert "官方公告/监管 RSS" in doc, (
        "data_utilization.md §3 must register official RSS feeds (DU-S12)"
    )
    assert "NewsCollector" in doc and "news_items" in doc, (
        "official RSS registration must declare collector and storage (DU-S12)"
    )
    assert "market_observer" in doc and "research_worker" in doc, (
        "official RSS registration must declare consumers (DU-S12)"
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
# DU-S10 / DU-S11 — forward-outcome thesis evidence reaches the PI decision
#   (Position B, P1a 2026-07-02). The universe_forward_outcomes store already
#   accumulates per-(scanner_family, direction) live edge and drives Tier-1
#   self-evolution, but the System Operator only saw a universe-wide aggregate
#   win-rate — it could not weigh a candidate against ITS family+direction
#   track record. These claims lock: (S10) the aggregation groups by
#   (scanner_family, direction); (S11) it is routed into the PI context +
#   prompt, closing the collected-but-not-consumed-at-decision-time gap.
# ---------------------------------------------------------------------------


def _setup_du_db(tmp_path, monkeypatch):
    import sqlite3
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    return conn


def _seed_forward_outcome(conn, family, direction, forward_return, horizon="24h"):
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    directional = (
        forward_return if direction == "long"
        else -forward_return if direction == "short"
        else None
    )
    net = directional - 0.2 if directional is not None else None
    conn.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, "
        " entry_price_usd, entry_ts, due_at, settled_at, exit_price_usd, "
        " forward_return_pct, gross_return_pct, directional_return_pct, net_return_pct, "
        " min_tradable_edge_pct, market_type, cost_model_version, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), "X/USDT",
         family, direction, horizon, 100.0, now, now, now, 101.0,
         forward_return, forward_return, directional, net, 0.05, "spot", "v1", "settled"),
    )
    conn.commit()


@spec_claim(
    SpecClaim(
        "DU-S10", _CONTRACT, "§thesis evidence aggregation", "L2b",
        "repositories.family_direction_edge groups settled net outcomes by "
        "(scanner_family, direction, market_type) — momentum:long:spot and "
        "momentum:short:perp are separate theses, not folded together.",
    ),
)
def test_family_direction_edge_groups_by_direction(tmp_path, monkeypatch):
    conn = _setup_du_db(tmp_path, monkeypatch)
    from aletheia.storage.repositories import family_direction_edge

    # momentum:long — 3 outcomes, 2 wins (positive return = long win)
    _seed_forward_outcome(conn, "momentum", "long", 2.0)
    _seed_forward_outcome(conn, "momentum", "long", 1.0)
    _seed_forward_outcome(conn, "momentum", "long", -1.0)
    # momentum:short — 2 outcomes, 2 wins (negative return = short win)
    _seed_forward_outcome(conn, "momentum", "short", -2.0)
    _seed_forward_outcome(conn, "momentum", "short", -3.0)
    # Unknown-direction legacy row must not be folded into momentum:long.
    _seed_forward_outcome(conn, "momentum", None, 9.0)

    rows = family_direction_edge(horizon="24h", within_days=7)
    by_key = {(r["scanner_family"], r["direction"], r["market_type"]): r for r in rows}

    assert ("momentum", "long", "spot") in by_key, "momentum:long:spot thesis missing"
    assert ("momentum", "short", "spot") in by_key, "momentum:short:spot thesis missing"
    long_edge = by_key[("momentum", "long", "spot")]
    short_edge = by_key[("momentum", "short", "spot")]
    assert long_edge["n"] == 3 and abs(long_edge["win_rate"] - 66.7) < 0.5, (
        f"momentum:long win_rate wrong: {long_edge}"
    )
    assert short_edge["n"] == 2 and abs(short_edge["win_rate"] - 100.0) < 0.5, (
        f"momentum:short win_rate wrong (short wins on negative return): {short_edge}"
    )


@spec_claim(
    SpecClaim(
        "DU-S11", _CONTRACT, "§no-orphan: forward-outcome edge → PI", "L2a",
        "trading_context attaches family_edge to each Layer-1 opportunity (from "
        "family_direction_edge) AND trading_decision renders it in the opportunity "
        "line — the per-family+direction track record reaches the PI at decision "
        "time, not just the universe-wide aggregate.",
    ),
)
def test_forward_outcome_edge_routed_to_pi():
    tc = (_ROOT / "aletheia" / "agent_shell" / "trading_context.py").read_text(encoding="utf-8")
    td = (_ROOT / "aletheia" / "agent_shell" / "trading_decision.py").read_text(encoding="utf-8")
    assert "family_direction_edge" in tc, (
        "trading_context must call family_direction_edge to build the thesis-evidence map"
    )
    assert '"family_edge"' in tc or "'family_edge'" in tc, (
        "trading_context must attach family_edge to each opportunity"
    )
    assert "_fmt_family_edge" in td, (
        "trading_decision must render family_edge in the System Operator prompt (opportunity line)"
    )


# ---------------------------------------------------------------------------
# DU-T01 — scheduler persists CoinalyzeCollector output
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-T01", _CONTRACT, "§coinalyze db write path", "L2b",
        "data_collector_context persists CoinalyzeCollector open_interest and liquidation "
        "outputs into open_interest_snapshots/liquidation_events. The test injects "
        "collector output offline, so it proves scheduler wiring without requiring "
        "a real Coinalyze API key.",
        status="implemented",
    ),
)
def test_scheduler_persists_coinalyze_outputs(monkeypatch):
    import asyncio

    from aletheia.agent_runtime import scheduler as sch
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data.models import LiquidationEvent, OpenInterestSnapshot
    from aletheia.storage import repositories as repo

    oi_rows: list[dict] = []
    liq_rows: list[dict] = []

    from aletheia.data.symbols import InstrumentId

    monkeypatch.setattr(
        sch,
        "_get_observed_instruments",
        lambda cfg: [InstrumentId.from_parts("binance", "BTC/USDT", "perp")],
    )
    monkeypatch.setattr(repo, "insert_open_interest_snapshot", lambda row: oi_rows.append(row))
    monkeypatch.setattr(repo, "insert_liquidation_event", lambda row: liq_rows.append(row))
    monkeypatch.setattr(repo, "insert_orderbook_snapshot", lambda row: None)
    monkeypatch.setattr(repo, "insert_funding_rate_snapshot", lambda row: None)
    monkeypatch.setattr(repo, "insert_macro_snapshot", lambda row: None)
    monkeypatch.setattr(repo, "insert_coingecko_top_rows", lambda rows: None)
    monkeypatch.setattr(repo, "save_volume_ranking_snapshot", lambda symbols, observed_at: None)

    async def fake_bounded(coro, label, timeout, deadline):
        if hasattr(coro, "close"):
            coro.close()
        if label == "coinalyze":
            return {
                "open_interest": [
                    OpenInterestSnapshot(
                        symbol="BTC/USDT",
                        exchange="coinalyze",
                        open_interest_usd=12_500_000.0,
                        oi_change_1h_pct=1.7,
                        observed_at="2026-01-01T00:00:00+00:00",
                    )
                ],
                "liquidations": [
                    LiquidationEvent(
                        symbol="BTC/USDT",
                        exchange="coinalyze",
                        side="long",
                        quantity_usd=250_000.0,
                        price=0.0,
                        event_time="2026-01-01T00:00:00+00:00",
                        observed_at="2026-01-01T00:00:00+00:00",
                    )
                ],
                "errors": [],
            }
        return {}

    monkeypatch.setattr(sch, "_bounded", fake_bounded)

    cfg = AletheiaConfig()
    cfg.market_data.enable_coinalyze = True
    cfg.market_data.derivatives_history_hours = 0
    asyncio.run(sch._async_data_collector_context(cfg, tick=1))

    assert len(oi_rows) == 1, "Coinalyze OI output was not persisted"
    assert oi_rows[0]["exchange"] == "coinalyze"
    assert oi_rows[0]["open_interest_usd"] == 12_500_000.0
    assert len(liq_rows) == 1, "Coinalyze liquidation output was not persisted"
    assert liq_rows[0]["exchange"] == "coinalyze"
    assert liq_rows[0]["quantity_usd"] == 250_000.0


@spec_claim(
    SpecClaim(
        "DU-S15", _CONTRACT, "§orderbook instrument identity", "L2c",
        "Orderbook snapshots persist market_type and critical consumers query by "
        "exchange + symbol + market_type rather than symbol alone.",
        status="implemented",
    )
)
def test_orderbook_identity_reaches_storage_and_consumers():
    root = _ROOT
    model = (root / "aletheia/data/models.py").read_text(encoding="utf-8")
    collector = (root / "aletheia/data/collectors/microstructure.py").read_text(encoding="utf-8")
    repo = (root / "aletheia/storage/repositories.py").read_text(encoding="utf-8")
    observer = (root / "aletheia/agents/market_observer.py").read_text(encoding="utf-8")
    features = (root / "aletheia/universe/features.py").read_text(encoding="utf-8")

    assert "market_type: str" in model
    assert "market_type=market_type" in collector
    assert "def latest_orderbook(" in repo and "market_type: str" in repo
    assert "build_market_context_from_db" in observer
    assert "latest_orderbook(" in _CONTEXT_BUILDER.read_text(encoding="utf-8")
    assert "exchange=venue" in observer and "market_type=market_type" in observer
    assert "latest_orderbook(" in features
    assert 'venue = str(asset.get("venue")' in features
    assert 'market_type = str(asset.get("market_type")' in features
    assert "exchange=venue, market_type=market_type" in features
