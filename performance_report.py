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

from aletheia.analytics.streaks import FLAT, LOSS, WIN


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

            # Attribution: where does PnL come from (symbol / strategy / reason).
            from aletheia.analytics.attribution import attribute

            attr = attribute(outcomes)
            result["attribution"] = attr
            result["sections"].append("attribution")

            # Streaks: longest / current consecutive win & loss runs.
            from aletheia.analytics.streaks import summarize_streaks

            result["streaks"] = summarize_streaks(pnls)
            result["sections"].append("streaks")

            # Temporal: when do wins/losses cluster (hour-of-day / weekday).
            from aletheia.analytics.temporal import temporal_summary

            result["temporal"] = temporal_summary(outcomes)
            result["sections"].append("temporal")
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

    if "attribution" in rep:
        attr = rep["attribution"]
        out.append("")
        out.append("[归因 attribution]  样本数: %s" % attr.get("outcome_count"))
        for dim, label in (
            ("by_symbol", "按标的"),
            ("by_strategy", "按策略"),
            ("by_close_reason", "按平仓原因"),
        ):
            seg = attr.get(dim) or {}
            if not seg:
                continue
            out.append("  %s:" % label)
            for name, st in seg.items():
                out.append(
                    "    %s: 笔数=%s 胜率=%s%% 总PnL=%s PF=%s"
                    % (
                        name,
                        st.get("trade_count"),
                        st.get("win_rate_pct"),
                        st.get("total_pnl"),
                        st.get("profit_factor"),
                    )
                )
        tc = attr.get("top_contributors") or {}
        if tc.get("best"):
            best = ", ".join(
                "%s(%s)" % (n, round(p, 2)) for n, p in tc["best"][:3]
            )
            out.append("  最大贡献标的: %s" % best)

    if "streaks" in rep:
        s = rep["streaks"]
        cur_sign = s.get("current_streak_sign")
        cur_label = {WIN: "连胜", LOSS: "连亏", FLAT: "持平", None: "无"}.get(cur_sign, str(cur_sign))
        out.append("")
        out.append("[连胜连败 streaks]")
        out.append("  最长连胜: %s 笔 | 最长连亏: %s 笔" % (
            s.get("max_consecutive_wins"), s.get("max_consecutive_losses")))
        out.append("  当前连: %s (%s 笔)" % (cur_label, s.get("current_streak_length")))

    if "temporal" in rep:
        t = rep["temporal"]
        out.append("")
        out.append("[时间序列 temporal]  样本=%s 可定时=%s" % (
            t.get("outcome_count"), t.get("bucketed_count")))
        bh, wh = t.get("best_hour", {}), t.get("worst_hour", {})
        if bh.get("hour") is not None:
            out.append("  最佳时段: %02d:00 (PnL=%s) | 最差时段: %02d:00 (PnL=%s)" % (
                bh["hour"], round(bh.get("total_pnl", 0.0), 2),
                wh.get("hour", 0), round(wh.get("total_pnl", 0.0), 2)))
        bd, wd = t.get("best_weekday", {}), t.get("worst_weekday", {})
        if bd.get("weekday") is not None:
            _wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            out.append("  最佳星期: %s (PnL=%s) | 最差星期: %s (PnL=%s)" % (
                _wd[bd["weekday"]], round(bd.get("total_pnl", 0.0), 2),
                _wd[wd.get("weekday", 0)], round(wd.get("total_pnl", 0.0), 2)))

    if (
        rep.get("trade_stats_error")
        or rep.get("equity_error")
        or rep.get("attribution_error")
    ):
        out.append("")
        out.append("[注意] 部分数据不可用:")
        if rep.get("trade_stats_error"):
            out.append("  trade_stats: %s" % rep["trade_stats_error"])
        if rep.get("equity_error"):
            out.append("  equity: %s" % rep["equity_error"])
        if rep.get("attribution_error"):
            out.append("  attribution: %s" % rep["attribution_error"])

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
