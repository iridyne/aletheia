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
