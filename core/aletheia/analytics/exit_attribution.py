"""Exit attribution — empirical distribution of how positions actually close.

Answers the M0 question of the exit-intent plan ("who is driving exits"): for
every closed position, group by ``close_reason`` and by the dependency-neutral
``ClosureCategory`` from :mod:`aletheia.analytics.outcome_integrity`
(strategy / administrative / maintenance / unknown). This quantifies how much of
current exit behavior is rule-driven (stop_loss / take_profit / max_hold /
thesis_invalidated / market_signal_exit) versus AI/administrative, mirroring the
RuleSet 0/208 analysis that justified removing the RuleSet exit bypass.

Read-only. No behavior change — this is measurement, not control.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.analytics.outcome_integrity import classify_closure


def _reason_of(row: dict[str, Any]) -> str:
    reason = (row.get("close_reason") or "").strip()
    return reason or "unknown"


@dataclass
class ExitAttribution:
    """Aggregated exit-reason distribution over a set of closed positions."""

    total: int
    by_reason: dict[str, int]
    by_category: dict[str, int]
    # reason -> {category -> count}; shows how one reason splits across categories
    by_reason_category: dict[str, dict[str, int]]
    # strategy-only reason counts — the exits that count as trading decisions
    strategy_by_reason: dict[str, int]

    def format_report(self) -> str:
        if self.total == 0:
            return "exit attribution: no closed positions in window"
        lines: list[str] = [f"exit attribution — {self.total} closed positions", ""]

        lines.append("by close_reason:")
        for reason, n in sorted(self.by_reason.items(), key=lambda kv: (-kv[1], kv[0])):
            pct = 100.0 * n / self.total
            lines.append(f"  {reason:<28} {n:>6}  ({pct:5.1f}%)")

        lines.append("")
        lines.append("by category:")
        for cat, n in sorted(self.by_category.items(), key=lambda kv: (-kv[1], kv[0])):
            pct = 100.0 * n / self.total
            lines.append(f"  {cat:<28} {n:>6}  ({pct:5.1f}%)")

        strat_total = sum(self.strategy_by_reason.values())
        lines.append("")
        lines.append(f"strategy-only exits ({strat_total}):")
        if strat_total == 0:
            lines.append("  (none classified as strategy)")
        else:
            for reason, n in sorted(
                self.strategy_by_reason.items(), key=lambda kv: (-kv[1], kv[0])
            ):
                pct = 100.0 * n / strat_total
                lines.append(f"  {reason:<28} {n:>6}  ({pct:5.1f}% of strategy)")
        return "\n".join(lines)


def summarize_closures(rows: list[dict[str, Any]]) -> ExitAttribution:
    """Group closed-position rows by reason and closure category. Pure function."""
    by_reason: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_reason_category: dict[str, Counter[str]] = {}
    strategy_by_reason: Counter[str] = Counter()

    for row in rows:
        reason = _reason_of(row)
        category = classify_closure(row).category
        by_reason[reason] += 1
        by_category[category] += 1
        by_reason_category.setdefault(reason, Counter())[category] += 1
        if category == "strategy":
            strategy_by_reason[reason] += 1

    return ExitAttribution(
        total=len(rows),
        by_reason=dict(by_reason),
        by_category=dict(by_category),
        by_reason_category={r: dict(c) for r, c in by_reason_category.items()},
        strategy_by_reason=dict(strategy_by_reason),
    )


def attribution_report(
    days: int | None = None,
    execution_mode: str | None = None,
    limit: int = 10000,
) -> ExitAttribution:
    """Load closed positions from the DB and summarize. ``days=None`` = all time."""
    from aletheia.storage.repositories import list_closed_positions

    since_iso: str | None = None
    if days is not None:
        since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = list_closed_positions(
        since_iso=since_iso, execution_mode=execution_mode, limit=limit
    )
    return summarize_closures(rows)


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Exit attribution report (M0)")
    parser.add_argument("--days", type=int, default=None, help="window in days (default: all time)")
    parser.add_argument("--mode", type=str, default=None, help="filter by execution_mode")
    args = parser.parse_args(argv)

    report = attribution_report(days=args.days, execution_mode=args.mode)
    print(report.format_report())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
