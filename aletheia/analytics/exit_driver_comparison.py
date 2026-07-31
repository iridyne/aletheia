"""Exit-driver realized-outcome comparison.

Answers "AI-discretionary exits do at least as well as rule exits" from
realized data. Groups CLOSED positions by what DROVE the exit and reports
outcome quality (count, win rate, avg / total realized PnL, avg hold) per
driver:

  • operator — System Operator proactively exited (operator_close / operator_limit_exit)
  • rule     — deterministic strategy rule fired (stop_loss / take_profit / max_hold /
               thesis_invalidated / market_signal_exit)
  • safety   — Layer-0 hard control fired (catastrophic_stop / liquidation_proximity)
  • other    — administrative / maintenance / unclassified

If a rule-driven category consistently beats the operator, that category
should stay in Layer 1 as pure execution. Read-only; no behavior change.

This is realized-outcome attribution, NOT a counterfactual — it compares
actual closes, never "what would have happened if a different driver had
fired." Complements ``exit_attribution.py`` (closure-category distribution);
the two modules are candidates for a future merge.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

OPERATOR_DRIVEN = frozenset({"operator_close", "operator_limit_exit"})
RULE_DRIVEN = frozenset({
    "stop_loss", "take_profit", "max_hold", "thesis_invalidated", "market_signal_exit",
})
SAFETY_DRIVEN = frozenset({"catastrophic_stop", "liquidation_proximity"})


def exit_driver(reason: str | None) -> str:
    r = (reason or "").strip()
    if r in OPERATOR_DRIVEN:
        return "operator"
    if r in RULE_DRIVEN:
        return "rule"
    if r in SAFETY_DRIVEN:
        return "safety"
    return "other"


def _hold_hours(row: dict[str, Any]) -> float | None:
    o, c = row.get("opened_at"), row.get("closed_at")
    if not o or not c:
        return None
    try:
        od = datetime.fromisoformat(str(o).replace("Z", "+00:00"))
        cd = datetime.fromisoformat(str(c).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if od.tzinfo is None:
        od = od.replace(tzinfo=timezone.utc)
    if cd.tzinfo is None:
        cd = cd.replace(tzinfo=timezone.utc)
    return (cd - od).total_seconds() / 3600.0


@dataclass
class ExitOutcomeStats:
    driver: str
    count: int
    wins: int
    total_pnl: float
    avg_pnl: float
    win_rate: float          # 0..1
    avg_hold_hours: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "driver": self.driver, "count": self.count, "wins": self.wins,
            "total_pnl": round(self.total_pnl, 6), "avg_pnl": round(self.avg_pnl, 6),
            "win_rate": round(self.win_rate, 4),
            "avg_hold_hours": round(self.avg_hold_hours, 2) if self.avg_hold_hours is not None else None,
        }


def compare_exit_outcomes(rows: list[dict[str, Any]]) -> dict[str, ExitOutcomeStats]:
    """Group closed positions by exit driver and compute realized-outcome stats. Pure."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[exit_driver(row.get("close_reason"))].append(row)

    out: dict[str, ExitOutcomeStats] = {}
    for driver, group in buckets.items():
        pnls = [float(r.get("realized_pnl") or 0.0) for r in group]
        holds = [h for h in (_hold_hours(r) for r in group) if h is not None]
        wins = sum(1 for p in pnls if p > 0)
        total = sum(pnls)
        out[driver] = ExitOutcomeStats(
            driver=driver,
            count=len(group),
            wins=wins,
            total_pnl=total,
            avg_pnl=(total / len(group)) if group else 0.0,
            win_rate=(wins / len(group)) if group else 0.0,
            avg_hold_hours=(sum(holds) / len(holds)) if holds else None,
        )
    return out


def format_comparison(stats: dict[str, ExitOutcomeStats]) -> str:
    if not stats:
        return "exit shadow: no closed positions in window"
    lines = ["exit-outcome comparison by driver", ""]
    header = f"  {'driver':<10}{'n':>5}{'win%':>7}{'avg_pnl':>11}{'total_pnl':>12}{'avg_hold_h':>12}"
    lines.append(header)
    for driver in ("operator", "rule", "safety", "other"):
        s = stats.get(driver)
        if not s:
            continue
        hold = f"{s.avg_hold_hours:.1f}" if s.avg_hold_hours is not None else "-"
        lines.append(
            f"  {driver:<10}{s.count:>5}{s.win_rate * 100:>6.1f}%"
            f"{s.avg_pnl:>11.4f}{s.total_pnl:>12.4f}{hold:>12}"
        )
    return "\n".join(lines)


def shadow_report(days: int | None = None, execution_mode: str | None = None, limit: int = 10000):
    """Load closed positions and compare exit outcomes by driver."""
    from datetime import timedelta

    from aletheia.storage.repositories import list_closed_positions

    since_iso = None
    if days is not None:
        since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = list_closed_positions(since_iso=since_iso, execution_mode=execution_mode, limit=limit)
    return compare_exit_outcomes(rows)


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Exit-outcome shadow comparison (M7)")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--mode", type=str, default=None)
    args = parser.parse_args(argv)
    print(format_comparison(shadow_report(days=args.days, execution_mode=args.mode)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
