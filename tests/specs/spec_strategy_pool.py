
from __future__ import annotations



from tests.specs.spec_meta import SpecClaim, spec_claim




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
    """Structural fact: strategy_records has no KPI columns; System Operator prompt 必然拿到 None.

    This test passes (status=implemented) as a documentation anchor — it
    asserts the current schema reality so that if someone adds these columns
    in Round 2 the test must be updated (forcing a deliberate change).
    """
    import sqlite3
    from aletheia.storage.sqlite import db_path

    conn = sqlite3.connect(db_path())
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




# ---------------------------------------------------------------------------
# SP-S04 — REMOVED (Position C 2026-07-02): tested strategy_designer's
# _derive_regime_targeting + ranker._check_strategy_regime_fit, both deleted with
# the rules engine. Regime affinity now lives in the scanner-family layer — see
# SP-S06/S07 below (config-driven, strategy-independent) and the universe pipeline.
# ---------------------------------------------------------------------------


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
        "funding_rate": 0.006, "rsi_14": 40.0,
        "relative_strength_24h": -5.0,
    }
    score, reasons, direction = score_short_bias(bearish)
    assert direction == "short", f"score_short_bias 未在 bearish setup 产出 short: {direction}"
    assert score > 0, f"score_short_bias 产出 short 但 score={score}"

    # Deeply negative funding means shorts are paying/crowded. A dedicated
    # continuation-short scanner must reject that squeeze-prone setup.
    crowded_short = {**bearish, "funding_rate": -0.012}
    assert score_short_bias(crowded_short)[2] is None

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




# ---------------------------------------------------------------------------
# SP-S07 — regime scoring applies to strategy-LESS opportunities (the 70% gap)
# ---------------------------------------------------------------------------
