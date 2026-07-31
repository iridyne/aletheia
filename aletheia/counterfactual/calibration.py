"""Conviction-gate calibration report from shadow-trade counterfactuals.

Bucket resolved shadow candidates by ``trade_score`` and read off the average
net return per band — the answer to "is the conviction-to-trade gate leaving
money on the table, in this window?". Read-only; no writes to DB, filesystem,
or config.

Interpretation limits are baked into the returned notes: outcomes are shadow /
paper results under a cost model (not live fills), the descriptive
relationship holds only over the queried window and regime, and small-n bands
are inconclusive. Crypto returns are fat-tailed — always read ``avg_net``
alongside ``median_net`` and ``win_rate``.
"""

from __future__ import annotations

import sqlite3
from statistics import mean, median
from typing import Any

from aletheia.counterfactual.advisories import (
    advisory_to_dict,
    build_rotation_advisories,
    render_advisories,
)
from aletheia.counterfactual.trades import list_deduped_shadows

# Band edges (trade_score). Chosen to straddle the gate so the sign flip is
# visible on both sides. The gate itself is drawn in as a marker at runtime.
_BAND_EDGES: list[float] = [40.0, 46.0, 50.0, 55.0, 60.0, 70.0]


def _min_conviction_gate() -> int:
    """Return the live conviction-to-trade gate, or the DL-INV-2 default."""
    try:
        from aletheia.agent_shell.trading_decision import _MIN_CONVICTION_TO_TRADE

        return int(_MIN_CONVICTION_TO_TRADE)
    except Exception:
        return 55


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
    policy_epoch_id: str | None = None,
    scanner_family: str | None = None,
) -> dict[str, Any]:
    """Build the JSON-serializable calibration report. Read-only.

    Passing ``policy_epoch_id`` restricts the window to shadow rows written
    under the same policy epoch, which is the correct partition whenever the
    caller could otherwise straddle an epoch rotation.

    Passing ``scanner_family`` restricts to a single scanner family
    (e.g. ``"momentum"``, ``"short_bias"``).
    """
    import aletheia.storage.sqlite as sqlite_store

    gate = _min_conviction_gate()
    report: dict[str, Any] = {
        "window_days": days,
        "symbols": symbols or "ALL",
        "policy_epoch_id": policy_epoch_id,
        "scanner_family": scanner_family or "ALL",
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
        sqlite_store.get_db()
    except sqlite3.OperationalError as exc:
        report["errors"].append(f"Database could not be opened: {exc}")
        return report

    try:
        rows = list_deduped_shadows(
            days=days,
            symbols=symbols,
            policy_epoch_id=policy_epoch_id,
            scanner_family=scanner_family,
        )
    except sqlite3.OperationalError as exc:
        report["errors"].append(f"Query failed (missing table/column?): {exc}")
        return report

    report["total_candidates"] = len(rows)
    if not rows:
        report["notes"].append("No resolved shadow candidates in this window/scope.")
        return report

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

    for b in bands_out:
        if b["avg_net_pct"] is not None and b["avg_net_pct"] > 0:
            report["crossover_band"] = b["band"]
            break

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

    # L5 back-linkage: attach any rotation advisories derived from this report.
    # Advisories are observe-only (see counterfactual.advisories); attaching
    # them here does not authorize any action.
    report["rotation_advisories"] = [
        advisory_to_dict(a) for a in build_rotation_advisories(calibration_report=report)
    ]
    return report


def _fmt_pct(v: Any) -> str:
    return f"{v:+.3f}" if isinstance(v, (int, float)) else "   n/a"


def render_text(report: dict[str, Any]) -> str:
    if report.get("errors"):
        return "shadow_threshold_calibration: " + "; ".join(report["errors"])

    gate = report["conviction_gate"]
    lines: list[str] = []
    scope = report["symbols"] if report["symbols"] != "ALL" else "all symbols"
    epoch = report.get("policy_epoch_id")
    epoch_note = f", policy_epoch={epoch}" if epoch else ""
    lines.append(
        f"Shadow counterfactual calibration — {report['window_days']}d, {scope}"
        f"{epoch_note}, {report.get('total_candidates', 0)} candidates (deduped)"
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

    advisories_raw = report.get("rotation_advisories") or []
    if advisories_raw:
        from aletheia.counterfactual.advisories import RotationAdvisory

        advisories = [RotationAdvisory(**a) for a in advisories_raw]
        lines.append("")
        lines.append(render_advisories(advisories))
    return "\n".join(lines)
