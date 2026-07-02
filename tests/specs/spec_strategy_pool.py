"""Spec: Strategy Pool exposure (RED, written 2026-06-24 during freeze).

These claims describe how the Strategies subsystem MUST expose per-strategy
KPIs (win_rate / total_pnl / trade_count) to the PI Agent decision chain
after Round 2 解冻. They are recorded with status="missing" because the
production code path (trading_context.py's Layer 2 strategies builder) is
inside the EXPERIMENT FREEZE clearance and cannot be modified during Round 1.

The fix is small (Round 2 第一周): JOIN strategy_records with
build_strategy_kpis() so PI sees real numbers instead of None/?.

Note: an earlier audit incorrectly named the helper as
`per_strategy_kpi_summary` — the real name is `build_strategy_kpis()` in
`aletheia/analytics/trade_stats.py:227`. Verified 2026-06-24.
"""

from __future__ import annotations

import ast

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_TRADING_CONTEXT = "aletheia/agent_shell/trading_context.py"


def _project_root() -> str:
    import pathlib
    return str(pathlib.Path(__file__).resolve().parents[2])


# ---------------------------------------------------------------------------
# SP-S01 — strategy_records DDL lacks PI-visible KPI columns
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S01", "strategy_pool", "§schema vs PI", "L2a",
              "strategy_records DDL does NOT include win_rate/total_pnl/trade_count",
              status="implemented"),  # this is the observation, not the fix
)
def test_strategy_records_ddl_missing_kpi_columns():
    """Structural fact: strategy_records has no KPI columns; PI prompt 必然拿到 None.

    This test passes (status=implemented) as a documentation anchor — it
    asserts the current schema reality so that if someone adds these columns
    in Round 2 the test must be updated (forcing a deliberate change).
    """
    import sqlite3
    conn = sqlite3.connect(f"{_project_root()}/data/app.db")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(strategy_records)")]
    conn.close()
    assert "win_rate" not in cols
    assert "total_pnl" not in cols
    assert "trade_count" not in cols


# ---------------------------------------------------------------------------
# SP-S02 — build_strategy_kpis helper must exist and return the expected shape
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S02", "strategy_pool", "§KPI source", "L2a",
              "build_strategy_kpis() returns {strategy_id: {trade_count, win_rate, total_pnl}}"),
)
def test_build_strategy_kpis_shape():
    """Round 2 修复依赖的 helper 必须存在,且字段名稳定."""
    from aletheia.analytics.trade_stats import build_strategy_kpis
    r = build_strategy_kpis()
    assert isinstance(r, dict)
    if r:
        sid = next(iter(r))
        kpi = r[sid]
        assert "trade_count" in kpi
        assert "win_rate" in kpi
        assert "total_pnl" in kpi


# ---------------------------------------------------------------------------
# SP-S03 — trading_context.py MUST use build_strategy_kpis to enrich strategies
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S03", "strategy_pool", "§KPI exposure to PI", "L2a",
              "trading_context.py must call build_strategy_kpis() so PI sees real KPIs"),
)
def test_trading_context_uses_strategy_kpis():
    """trading_context.py 源码必须 reference build_strategy_kpis."""
    import pathlib
    src = (pathlib.Path(_project_root()) / _TRADING_CONTEXT).read_text(encoding="utf-8")
    assert "build_strategy_kpis" in src, (
        "trading_context.py never references build_strategy_kpis — PI prompt 仍然拿到 "
        "win_rate=None total_pnl=None trade_count=None. "
        "See round2_week1_decision_chain_fixes.md Fix S-Bug-C"
    )


# ---------------------------------------------------------------------------
# SP-S04 — strategy_designer must write direction_affinity (Phase 2A)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S04", "strategy_pool", "§direction_affinity + applicable_regimes written", "L2b",
              "strategy_designer derives BOTH direction_affinity AND applicable_regimes "
              "from direction_bias; applicable_regimes is a JSON-array the ranker can "
              "parse (a comma string would be silently dropped → regime gate inert)"),
)
def test_strategy_designer_writes_regime_targeting():
    """行为可达性(非源码字符串):designer 的方向→regime 映射必须

      1. long/short/neutral 各产出正确的 direction_affinity
      2. applicable_regimes 是 ranker 真能解析的 **JSON array**(json.loads→list),
         排除硬相反 regime;neutral→"all"
      3. ranker._check_regime_fit 真的用它过滤(long 策略在 bear 高置信 regime 被挡)

    反向验证:若 applicable_regimes 写成逗号串,第 2 步 json.loads 会失败 → 立即挂。
    """
    import json
    from aletheia.agents.strategy_designer import _derive_regime_targeting

    # 1 + 2. 方向 → (affinity, regimes) 映射正确且 regimes 可 JSON 解析
    aff_long, reg_long = _derive_regime_targeting("long")
    assert aff_long == "long"
    parsed_long = json.loads(reg_long)  # 逗号串会在此炸 → 保证 ranker 能消费
    assert isinstance(parsed_long, list) and "bear" not in parsed_long, (
        f"long 策略 applicable_regimes 应排除 bear: {parsed_long}"
    )

    aff_short, reg_short = _derive_regime_targeting("short")
    assert aff_short == "short"
    parsed_short = json.loads(reg_short)
    assert isinstance(parsed_short, list) and "bull" not in parsed_short, (
        f"short 策略 applicable_regimes 应排除 bull: {parsed_short}"
    )

    aff_neutral, reg_neutral = _derive_regime_targeting("neutral")
    assert aff_neutral == "both" and reg_neutral == "all"

    # 3. ranker 真的用它过滤:long 策略在高置信 bear regime 应被挡
    from aletheia.agents.paper_trader_ranker import _check_strategy_regime_fit

    class _Regime:
        state = "bear"
        confidence = 0.9

    strat = {"direction_affinity": aff_long, "applicable_regimes": reg_long, "parameters": "{}"}
    verdict = _check_strategy_regime_fit(strat, "long", _Regime())
    assert verdict is not None, "long 策略在 bear regime 应被 ranker regime 门挡下"


# ---------------------------------------------------------------------------
# SP-S05 — short_bias scanner family is REACHABLE (Phase 2A+.8)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S05", "strategy_pool", "§short scanner family", "L2b",
              "short_bias scanner family is REACHABLE: score_short_bias fires 'short' on bearish setups AND is registered in run_all_scanners"),
)
def test_short_bias_scanner_family_reachable():
    """专用 short_bias scanner family 必须**可达**，不能是死码。

    2026-07-01 教训（假绿陷阱）：先前的 SP-S05 只 `assert 'family == "short_bias"' in src`
    —— 字符串写了就 GREEN，但那段分支在 run_all_scanners 里从未注册，运行时永不可达，
    是死码。本 spec 改为**行为可达性**检查，任何"写了但没接通"的回归都会被抓住：
      1. score_short_bias 在 bearish setup 上真的产出 direction="short" 且 score>0
      2. score_short_bias 在 bullish setup 上返回 None（不误发）
      3. short_bias 已注册进 run_all_scanners（出现在 all_scores 里 → 可被选中）
    """
    from aletheia.universe.scanners import score_short_bias, run_all_scanners

    # 1. 干净 bearish setup → 产出 short 信号
    bearish = {
        "return_4h": -4.0, "return_24h": -6.0,
        "funding_rate": -0.006, "rsi_14": 40.0,
        "relative_strength_24h": -5.0,
    }
    score, reasons, direction = score_short_bias(bearish)
    assert direction == "short", f"score_short_bias 未在 bearish setup 产出 short: {direction}"
    assert score > 0, f"score_short_bias 产出 short 但 score={score}"

    # 2. bullish setup → 不误发
    bullish = {"return_4h": 3.0, "return_24h": 5.0, "rsi_14": 60.0}
    assert score_short_bias(bullish)[2] is None, "score_short_bias 在 bullish setup 误发信号"

    # 3. 已注册进 run_all_scanners（可达性核心断言）— all_scores 包含 short_bias
    result = run_all_scanners(bearish)
    assert "short_bias" in result.get("all_scores", {}), (
        "short_bias 未注册进 run_all_scanners — scanner_family 永远不会是 'short_bias'，"
        "该 family 是死码。必须在 run_all_scanners 的 results dict 中注册 score_short_bias。"
    )


# ---------------------------------------------------------------------------
# SP-S06 — regime scoring is config-driven scanner-family signal (P1b, no-op)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S06", "strategy_pool", "§scanner-family regime scoring config", "L2a",
              "The ranker's regime score deltas are config-driven "
              "(cfg.universe.scanner_regime_scoring), defaults reproduce the prior "
              "hardcoded values EXACTLY (verified no-op). Rehomes regime affinity to "
              "the scanner-family config layer (Position C insight) as a SOFT, tunable "
              "signal — NOT a hard block (owner directive: no low-dim hard interception)."),
)
def test_regime_scoring_is_config_driven_noop():
    import pathlib
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    srs = cfg.universe.scanner_regime_scoring
    # Defaults MUST equal the prior hardcoded ranker values (no behavior change).
    assert srs["bear_short"] == 20.0 and srs["bear_long"] == -25.0, srs
    assert srs["bull_long"] == 20.0 and srs["bull_short"] == -20.0, srs
    assert srs["volatile_penalty"] == 10.0, srs
    assert srs["sideways_trend_penalty"] == 25.0 and srs["sideways_reversion_bonus"] == 15.0, srs

    # Ranker must READ the config (not re-hardcode) — else the config is inert.
    ranker_src = (pathlib.Path(_project_root()) / "aletheia" / "agents" / "paper_trader_ranker.py").read_text(encoding="utf-8")
    assert "scanner_regime_scoring" in ranker_src, (
        "paper_trader_ranker must read cfg.universe.scanner_regime_scoring"
    )


# ---------------------------------------------------------------------------
# SP-S07 — regime scoring applies to strategy-LESS opportunities (the 70% gap)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("SP-S07", "strategy_pool", "§regime scoring strategy-independent", "L2b",
              "The regime score delta is applied to a candidate with NO linked strategy "
              "(strategy_evidence=None, strategy_has_ruleset=False) — proving regime "
              "affinity is a scanner-family/candidate-level signal, not a strategy-record "
              "property. Closes the orphan-opportunity gap (~70% of opps have no strategy)."),
)
def test_regime_scoring_applies_without_strategy(monkeypatch):
    import aletheia.agents.paper_trader_ranker as r
    from aletheia.regime.detector import MarketRegime

    # Bear regime at full confidence → weight = 1.0, deltas apply undiluted.
    bear = MarketRegime(state="bear", confidence=1.0, long_bias=0.2, short_bias=0.8)
    monkeypatch.setattr(r, "get_current_regime", lambda: bear)
    # Isolate the regime term: zero out the direction-dependent lesson penalty.
    monkeypatch.setattr(r, "_lesson_penalty", lambda *a, **k: 0.0)

    opp = {"symbol": "X/USDT", "confidence_score": 50, "scanner_family": "momentum"}
    factor = {"status": "watch", "win_rate": None, "trade_count": 0}

    # NO strategy passed (strategy_evidence=None, strategy_has_ruleset=False).
    s_short = r._compute_trade_score(opp, None, factor, "medium",
                                     None, strategy_has_ruleset=False, direction="short")
    s_long = r._compute_trade_score(opp, None, factor, "medium",
                                    None, strategy_has_ruleset=False, direction="long")

    # bear_short(+20) − bear_long(−25) = +45, applied with no strategy present.
    assert abs((s_short - s_long) - 45.0) < 0.01, (
        f"regime delta not applied strategy-independently: short={s_short} long={s_long} "
        f"(expected diff 45.0 from bear_short − bear_long)"
    )
