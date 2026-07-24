#!/usr/bin/env python3
"""shadow_threshold_calibration — read-only counterfactual check of the
conviction-to-trade gate.

Question this answers
---------------------
Every so often an observation flags a symbol (or a whole basket) that produces
many signals but is rarely or never selected for a trade, and asks: *is the
conviction gate too strict — are we leaving money on the table?*

`shadow_trades` is a counterfactual ledger: on every ranking tick it records
each candidate's ``trade_score`` (the seed for the operator's ``conviction``;
``conviction = min(trade_score, 95)``), whether it was selected, and — once the
horizon resolves — its directional net return (``net_pnl_pct``, already net of
the modelled fees/spread/funding). Bucketing resolved candidates by
``trade_score`` and reading off the average net return per band tells us where
forward expectancy actually turns positive, which is exactly what the gate is
supposed to sit at.

This script only READS local SQLite state. It performs no writes against the
database, filesystem, or configuration, calls no agents/LLMs/exchanges, and is
safe to run repeatedly.

Important limits on interpretation
----------------------------------
- Outcomes are shadow/paper results under a cost model, not live fills.
- The relationship is descriptive over the queried window and regime; the
  break-even point can move, so re-run before drawing threshold conclusions.
- Crypto returns are fat-tailed: read ``avg_net`` alongside ``median_net`` and
  ``win_rate`` — a single band average can be swung by one outlier. Treat small
  bands (low ``n``) as inconclusive.
- ``was_selected`` here is the shadow-batch selection, which is broader than the
  live ``conviction >= gate`` trade gate; the selected-vs-unselected section is
  a secondary sanity check, not the gate calibration itself.

Usage
-----
    uv run python scripts/shadow_threshold_calibration.py
    uv run python scripts/shadow_threshold_calibration.py --days 14
    uv run python scripts/shadow_threshold_calibration.py --symbol SOXL/USDT --symbol CHZ/USDT
    uv run python scripts/shadow_threshold_calibration.py --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from statistics import mean, median
from typing import Any

# Band edges (trade_score). Chosen to straddle the gate so the sign flip is
# visible on both sides. The gate itself is drawn in as a marker at runtime.
_BAND_EDGES = [40.0, 46.0, 50.0, 55.0, 60.0, 70.0]


def _min_conviction_gate() -> int:
    """Return the live conviction-to-trade gate, or a documented fallback.

    Imported from the decision module so this report stays aligned with the
    real gate rather than a hard-coded copy.
    """
    try:
        from aletheia.agent_shell.trading_decision import _MIN_CONVICTION_TO_TRADE

        return int(_MIN_CONVICTION_TO_TRADE)
    except Exception:
        return 55  # DL-INV-2 / DL-C01 documented default


def _band_label(score: float, edges: list[float]) -> str:
    lo = None
    for edge in edges:
        if score < edge:
            return f"<{int(edge)}" if lo is None else f"{int(lo)}-{int(edge)}"
        lo = edge
    return f"{int(edges[-1])}+"


def _band_lower_edge(label: str) -> float:
    """Numeric lower edge of a band label, e.g. '55-60' -> 55.0, '70+' -> 70.0."""
    digits = label.lstrip("<").split("-")[0].rstrip("+")
    try:
        return float(digits)
    except ValueError:
        return -1.0


def _band_order_key(label: str) -> float:
    """Sort key: a '<X' bucket sorts just before the '[X-...' bucket."""
    edge = _band_lower_edge(label)
    return edge - 0.5 if label.startswith("<") else edge


def _fetch_deduped_rows(
    db: sqlite3.Connection,
    *,
    days: int,
    symbols: list[str] | None,
) -> list[dict[str, Any]]:
    """One resolved row per candidate (latest tick per outcome_candidate_key).

    A candidate is re-scanned many times, appending a shadow row each tick; the
    raw table over-weights long-lived candidates. Collapsing to the latest row
    per ``outcome_candidate_key`` restores one vote per candidate.
    """
    params: list[Any] = [f"-{int(days)} days"]
    symbol_clause = ""
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        symbol_clause = f"AND symbol IN ({placeholders})"
        params.extend(symbols)

    sql = f"""
        WITH resolved AS (
            SELECT
                outcome_candidate_key,
                symbol,
                direction,
                trade_score,
                net_pnl_pct,
                was_selected,
                ROW_NUMBER() OVER (
                    PARTITION BY outcome_candidate_key
                    ORDER BY created_at DESC
                ) AS rn
            FROM shadow_trades
            WHERE net_pnl_pct IS NOT NULL
              AND outcome_candidate_key IS NOT NULL
              AND created_at >= datetime('now', ?)
              {symbol_clause}
        )
        SELECT symbol, direction, trade_score, net_pnl_pct, was_selected
        FROM resolved
        WHERE rn = 1
    """
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def _summarize_band(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [float(r["net_pnl_pct"]) for r in rows]
    wins = sum(1 for v in nets if v > 0)
    return {
        "n": len(nets),
        "avg_net_pct": round(mean(nets), 3) if nets else None,
        "median_net_pct": round(median(nets), 3) if nets else None,
        "win_rate_pct": round(100.0 * wins / len(nets), 1) if nets else None,
    }


def build_calibration(
    *,
    days: int = 8,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Build the JSON-serializable calibration report. Read-only."""
    import aletheia.storage.sqlite as sqlite_store

    gate = _min_conviction_gate()
    report: dict[str, Any] = {
        "window_days": days,
        "symbols": symbols or "ALL",
        "conviction_gate": gate,
        "bands": [],
        "selection_check": {},
        "crossover_band": None,
        "notes": [],
        "errors": [],
    }

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        report["errors"].append("Database is not initialized.")
        return report
    try:
        db = sqlite_store.get_db()
    except sqlite3.OperationalError as exc:
        report["errors"].append(f"Database could not be opened: {exc}")
        return report

    try:
        rows = _fetch_deduped_rows(db, days=days, symbols=symbols)
    except sqlite3.OperationalError as exc:
        report["errors"].append(f"Query failed (missing table/column?): {exc}")
        return report

    report["total_candidates"] = len(rows)
    if not rows:
        report["notes"].append("No resolved shadow candidates in this window/scope.")
        return report

    # Band aggregation.
    banded: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        label = _band_label(float(r["trade_score"]), _BAND_EDGES)
        banded.setdefault(label, []).append(r)

    bands_out = []
    for label in sorted(banded, key=_band_order_key):
        summary = _summarize_band(banded[label])
        summary["band"] = label
        summary["at_or_above_gate"] = _band_order_key(label) >= gate
        bands_out.append(summary)
    report["bands"] = bands_out

    # First band (by ascending score) whose average net return is positive.
    for b in bands_out:
        if b["avg_net_pct"] is not None and b["avg_net_pct"] > 0:
            report["crossover_band"] = b["band"]
            break

    # Secondary sanity check: selected vs unselected (shadow-batch selection).
    sel = [r for r in rows if int(r["was_selected"] or 0) == 1]
    unsel = [r for r in rows if int(r["was_selected"] or 0) == 0]
    report["selection_check"] = {
        "selected": _summarize_band(sel) if sel else {"n": 0},
        "unselected": _summarize_band(unsel) if unsel else {"n": 0},
    }

    report["notes"].append(
        "Outcomes are shadow/paper results under a cost model, over the queried "
        "window and regime only; re-run before drawing threshold conclusions."
    )
    report["notes"].append(
        "Read avg_net alongside median_net and win_rate; treat low-n bands as inconclusive."
    )
    return report


def _fmt_pct(v: Any) -> str:
    return f"{v:+.3f}" if isinstance(v, (int, float)) else "   n/a"


def _render_text(report: dict[str, Any]) -> str:
    if report.get("errors"):
        return "shadow_threshold_calibration: " + "; ".join(report["errors"])

    gate = report["conviction_gate"]
    lines: list[str] = []
    scope = report["symbols"] if report["symbols"] != "ALL" else "all symbols"
    lines.append(
        f"Shadow counterfactual calibration — {report['window_days']}d, {scope}, "
        f"{report.get('total_candidates', 0)} candidates (deduped)"
    )
    lines.append(f"Conviction-to-trade gate: {gate}  (conviction = min(trade_score, 95))")
    lines.append("")
    header = f"  {'trade_score band':<18}{'n':>6}{'avg_net%':>11}{'median%':>10}{'win%':>8}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for b in report["bands"]:
        marker = "  <-- gate" if _band_lower_edge(b["band"]) == gate else ""
        lines.append(
            f"  {b['band']:<18}{b['n']:>6}{_fmt_pct(b['avg_net_pct']):>11}"
            f"{_fmt_pct(b['median_net_pct']):>10}"
            f"{(str(b['win_rate_pct']) if b['win_rate_pct'] is not None else 'n/a'):>8}{marker}"
        )
    lines.append("")

    if not report["bands"]:
        lines.append("Verdict: no resolved shadow candidates in this window/scope — nothing to assess.")
        return "\n".join(lines)

    cross = report.get("crossover_band")
    if cross is None:
        lines.append(
            "Verdict: no trade_score band had positive average net return in this "
            "window — no evidence the gate is leaving money on the table."
        )
    else:
        cross_lo = _band_order_key(cross)
        if cross_lo >= gate:
            lines.append(
                f"Verdict: average net return first turns positive in band '{cross}', "
                f"at or above the gate ({gate}). Consistent with a well-calibrated gate: "
                f"expectancy below the gate is not positive in this window."
            )
        else:
            lines.append(
                f"Verdict: average net return first turns positive in band '{cross}', "
                f"BELOW the gate ({gate}). This is the only condition under which the gate "
                f"might be too strict — inspect n/median/win% for that band before acting, "
                f"and re-run over a longer window."
            )

    sc = report.get("selection_check", {})
    s, u = sc.get("selected", {}), sc.get("unselected", {})
    if s.get("n") and u.get("n"):
        lines.append("")
        lines.append(
            f"Selection sanity (shadow-batch, broader than the gate): "
            f"selected n={s['n']} avg_net={_fmt_pct(s.get('avg_net_pct'))}% "
            f"win={s.get('win_rate_pct')}%  |  "
            f"unselected n={u['n']} avg_net={_fmt_pct(u.get('avg_net_pct'))}% "
            f"win={u.get('win_rate_pct')}%"
        )

    if report.get("notes"):
        lines.append("")
        for note in report["notes"]:
            lines.append(f"note: {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=8, help="Lookback window in days (default 8).")
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Canonical symbol to scope to (e.g. SOXL/USDT). Repeatable.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args(argv)

    report = build_calibration(days=args.days, symbols=args.symbols)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_render_text(report))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
