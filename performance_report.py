#!/usr/bin/env python3
"""aletheia performance_report — read-only consolidated performance snapshot.

Aggregates the existing analytics outputs (rolling stats, full trade stats,
order fill-rate, equity curve + risk-adjusted metrics) into one operator-facing
report. It performs NO writes against the database, the filesystem, or any
configuration. Safe to run repeatedly.

Requires a configured runtime home (the deployed bot sets this up). When the
database is unavailable the script degrades gracefully and reports which
sections could not be computed.

Usage:
    python3 performance_report.py              # human-readable report
    python3 performance_report.py --json        # machine-readable JSON
    python3 performance_report.py --days 30     # 30-day rolling window
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _collect() -> dict:
    """Pull each analytics section, isolating failures so one bad source does
    not sink the whole report."""
    result: dict = {"sections": []}
    days = int(os.environ.get("REPORT_DAYS", os.environ.get("SNAPSHOT_WINDOW_HOURS", "7")))

    # Rolling + full trade stats + order stats (all DB-backed).
    try:
        from aletheia.analytics.trade_stats import (
            build_trade_stats,
            compute_order_stats,
            compute_rolling_trade_stats,
        )
        result["rolling"] = compute_rolling_trade_stats(days=days)
        result["trade_stats"] = build_trade_stats()
        result["order_stats"] = compute_order_stats(days=days)
        result["sections"] += ["rolling", "trade_stats", "order_stats"]
    except Exception as e:  # pragma: no cover - depends on live DB/runtime
        result["trade_stats_error"] = f"{type(e).__name__}: {e}"

    # Equity curve + risk-adjusted metrics from verified outcomes.
    try:
        from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
        from aletheia.analytics.performance_summary import (
            consolidate_report,
            equity_curve,
            summarize,
        )

        outcomes = eligible_strategy_outcomes()
        if outcomes:
            outcomes = sorted(
                outcomes,
                key=lambda o: (o.get("closed_at") or ""),
            )
            pnls = [float(o.get("realized_pnl") or 0.0) for o in outcomes]
            eq = equity_curve(pnls)
            merged = consolidate_report(
                equity=eq,
                rolling_stats=result.get("rolling"),
                trade_stats=result.get("trade_stats"),
                order_stats=result.get("order_stats"),
            )
            merged["risk_adjusted"] = summarize(pnls)
            merged["outcome_count"] = len(outcomes)
            result.update(merged)
        else:
            result["equity_note"] = "no eligible closed outcomes yet"
    except Exception as e:  # pragma: no cover - depends on live DB/runtime
        result["equity_error"] = f"{type(e).__name__}: {e}"

    return result


def _render_human(rep: dict) -> str:
    out = []
    out.append("=" * 64)
    out.append("aletheia bot 交易绩效报告 (只读)")
    out.append("生成时间(UTC): %s" % _now())
    out.append("可用分块: %s" % (rep.get("sections") or []))
    out.append("=" * 64)

    if "trade_stats" in rep:
        ts = rep["trade_stats"]
        totals = ts.get("totals", {})
        out.append("")
        out.append("[总体 trade_stats]")
        out.append("  交易数: %s" % totals.get("trade_count"))
        out.append("  胜率: %s%%" % totals.get("win_rate"))
        out.append("  总PnL: %s" % totals.get("total_pnl"))
        out.append("  平均持仓(h): %s" % totals.get("avg_hold_hours"))

    if "rolling" in rep:
        r = rep["rolling"]
        out.append("")
        out.append("[滚动 rolling]")
        out.append("  交易数: %s | 胜率: %s%% | 总PnL: %s" % (
            r.get("total_trades"), r.get("win_rate"), r.get("total_pnl")))

    if "order_stats" in rep:
        o = rep["order_stats"]
        out.append("")
        out.append("[订单 order_stats]")
        out.append("  成交率: %s%% (filled=%s, errors=%s)" % (
            o.get("fill_rate_pct"), o.get("filled"), o.get("errors")))

    if "risk_adjusted" in rep:
        ra = rep["risk_adjusted"]
        out.append("")
        out.append("[风险调整 risk_adjusted]")
        out.append("  交易数: %s | 胜率: %s%%" % (ra.get("trade_count"), ra.get("win_rate_pct")))
        out.append("  盈亏比(profit factor): %s" % ra.get("profit_factor"))
        out.append("  单笔期望(expectancy): %s" % ra.get("expectancy"))
        out.append("  最大回撤(max drawdown): %s" % ra.get("max_drawdown"))
        out.append("  Calmar: %s" % ra.get("calmar"))

    if rep.get("trade_stats_error") or rep.get("equity_error"):
        out.append("")
        out.append("[注意] 部分数据不可用:")
        if rep.get("trade_stats_error"):
            out.append("  trade_stats: %s" % rep["trade_stats_error"])
        if rep.get("equity_error"):
            out.append("  equity: %s" % rep["equity_error"])

    out.append("=" * 64)
    return "\n".join(out)


def main() -> int:
    as_json = "--json" in sys.argv[1:]
    rep = _collect()
    if as_json:
        # Profit factor may be inf; JSON cannot represent it -> stringify.
        def _clean(o):
            if isinstance(o, float) and (o == float("inf") or o == float("-inf") or o != o):
                return "inf" if o > 0 else ("-inf" if o < 0 else None)
            if isinstance(o, dict):
                return {k: _clean(v) for k, v in o.items()}
            if isinstance(o, list):
                return [_clean(v) for v in o]
            return o
        print(json.dumps(_clean(rep), ensure_ascii=False, indent=2))
    else:
        print(_render_human(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
