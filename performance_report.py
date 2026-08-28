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

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from aletheia.analytics.streaks import FLAT, LOSS, WIN


def _error(section: str, exc: Exception) -> tuple[str, str]:
    return f"{section}_error", f"{type(exc).__name__}: {exc}"


def _add_section(result: dict, name: str, value) -> None:
    result[name] = value
    if name not in result["sections"]:
        result["sections"].append(name)


def _finalize(result: dict) -> dict:
    if result.get("execution_mode_status") == "unavailable":
        result["completeness"] = "unavailable"
        result["complete"] = False
        return result
    errors = [key for key in result if key.endswith("_error")]
    result["completeness"] = "partial" if errors else "complete"
    result["complete"] = not errors
    return result


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


_DEFAULT_REPORT_DAYS = 7
_MAX_REPORT_DAYS = 3650


def _days_arg(value: str) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("days must be an integer") from exc
    if not 1 <= days <= _MAX_REPORT_DAYS:
        raise argparse.ArgumentTypeError(
            f"days must be between 1 and {_MAX_REPORT_DAYS}"
        )
    return days


def _collect(days: int | None = None, execution_mode: str | None = None) -> dict:
    """Pull each analytics section, isolating failures so one bad source does
    not sink the whole report.

    Scoped to the resolved selected ``execution_mode`` (#1939).  ``--execution-mode``
    on the CLI (or ``REPORT_EXECUTION_MODE`` env) overrides the resolved default.
    An unresolved config fails-closed: the report emits
    ``execution_mode_status='unavailable'`` and no metrics.
    """
    from aletheia.analytics.execution_mode import (
        resolve_selected_execution_mode,
    )

    result: dict = {"sections": []}
    if days is None:
        raw_days = os.environ.get("REPORT_DAYS", str(_DEFAULT_REPORT_DAYS))
        try:
            days = _days_arg(raw_days)
        except argparse.ArgumentTypeError as exc:
            raise ValueError(f"REPORT_DAYS: {exc}") from exc
    result["window"] = {"days": days}

    override = execution_mode or os.environ.get("REPORT_EXECUTION_MODE") or _cli_execution_mode()
    execution_mode = override or resolve_selected_execution_mode()
    result["execution_mode"] = execution_mode
    result["execution_mode_status"] = (
        "resolved" if execution_mode else "unavailable"
    )
    if not execution_mode:
        result["unavailable_reason"] = "execution_mode_unresolved"
        return _finalize(result)

    # Rolling + full trade stats + order stats (all DB-backed).
    from aletheia.analytics.trade_stats import (
        build_trade_stats,
        compute_order_stats,
        compute_rolling_trade_stats,
    )

    for name, producer in (
        (
            "rolling",
            lambda: compute_rolling_trade_stats(days=days, execution_mode=execution_mode),
        ),
        ("trade_stats", lambda: build_trade_stats(execution_mode=execution_mode)),
        (
            "order_stats",
            lambda: compute_order_stats(days=days, execution_mode=execution_mode),
        ),
    ):
        try:
            _add_section(result, name, producer())
        except Exception as e:  # pragma: no cover - depends on live DB/runtime
            key, value = _error(name, e)
            result[key] = value

    # Equity curve + risk-adjusted metrics from verified outcomes.
    try:
        from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
        from aletheia.analytics.performance_summary import (
            consolidate_report,
            equity_curve,
            summarize,
        )

        outcomes = eligible_strategy_outcomes(execution_mode=execution_mode)
    except Exception as e:  # pragma: no cover - depends on live DB/runtime
        key, value = _error("outcomes", e)
        result[key] = value
        return _finalize(result)

    if outcomes:
        outcomes = sorted(
            outcomes,
            key=lambda o: (o.get("closed_at") or ""),
        )
        pnls = [float(o.get("realized_pnl") or 0.0) for o in outcomes]

        try:
            eq = equity_curve(pnls)
            merged = consolidate_report(equity=eq)
            merged["risk_adjusted"] = summarize(pnls)
            merged["outcome_count"] = len(outcomes)
            result.update({k: v for k, v in merged.items() if k != "sections"})
            _add_section(result, "equity", result["equity_metrics"])
        except Exception as e:  # pragma: no cover - depends on live DB/runtime
            key, value = _error("equity", e)
            result[key] = value

        # Attribution: where does PnL come from (symbol / strategy / reason).
        try:
            from aletheia.analytics.attribution import attribute

            _add_section(result, "attribution", attribute(outcomes))
        except Exception as e:  # pragma: no cover - depends on live DB/runtime
            key, value = _error("attribution", e)
            result[key] = value

        # Streaks: longest / current consecutive win & loss runs.
        try:
            from aletheia.analytics.streaks import summarize_streaks

            _add_section(result, "streaks", summarize_streaks(pnls))
        except Exception as e:  # pragma: no cover - depends on live DB/runtime
            key, value = _error("streaks", e)
            result[key] = value

        # Temporal: when do wins/losses cluster (hour-of-day / weekday).
        try:
            from aletheia.analytics.temporal import temporal_summary

            _add_section(result, "temporal", temporal_summary(outcomes))
        except Exception as e:  # pragma: no cover - depends on live DB/runtime
            key, value = _error("temporal", e)
            result[key] = value
    else:
        result["equity_note"] = "no eligible closed outcomes yet"

    return _finalize(result)


def _render_human(rep: dict) -> str:
    out = []
    out.append("=" * 64)
    out.append("aletheia bot 交易绩效报告 (只读)")
    out.append("生成时间(UTC): %s" % _now())
    out.append("execution_mode: %s (%s)" % (
        rep.get("execution_mode") or "—",
        rep.get("execution_mode_status") or "unavailable",
    ))
    out.append("window_days: %s" % ((rep.get("window") or {}).get("days")))
    if rep.get("execution_mode_status") == "unavailable":
        out.append(
            "[fail-closed] execution_mode 无法解析,"
            " 不展示混合口径 (#1939): %s"
            % (rep.get("unavailable_reason") or "unknown")
        )
    out.append("可用分块: %s" % (rep.get("sections") or []))
    out.append("完整性: %s" % (rep.get("completeness") or "unknown"))
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
        out.append(
            "  已解析成交率: %s%% (%s/%s; observed=%s; "
            "in_flight=%s; unresolved_or_integrity=%s; delivery_status=%s)"
            % (
                o.get("resolved_fill_rate_pct", o.get("fill_rate_pct")),
                o.get("resolved_fill_numerator", o.get("filled")),
                o.get("resolved_fill_denominator"),
                o.get("observed_order_count", o.get("total")),
                o.get("in_flight_count"),
                o.get("unresolved_or_integrity_count"),
                o.get("delivery_status", "unknown"),
            )
        )

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

    error_keys = sorted(key for key in rep if key.endswith("_error"))
    if error_keys:
        out.append("")
        out.append("[注意] 部分数据不可用:")
        for key in error_keys:
            out.append("  %s: %s" % (key.removesuffix("_error"), rep[key]))

    out.append("=" * 64)
    return "\n".join(out)


def _cli_execution_mode() -> str | None:
    """Return ``--execution-mode <value>`` from argv, if present."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--execution-mode" and i + 1 < len(args):
            return args[i + 1].strip() or None
        if arg.startswith("--execution-mode="):
            return arg.split("=", 1)[1].strip() or None
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=_days_arg,
        default=None,
        help=f"Rolling window in days (1-{_MAX_REPORT_DAYS}; default: {_DEFAULT_REPORT_DAYS}).",
    )
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    parser.add_argument("--execution-mode", default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rep = _collect(days=args.days, execution_mode=args.execution_mode)
    if args.json:
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
