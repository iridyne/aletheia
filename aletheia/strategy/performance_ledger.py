"""Performance Ledger — tracks whether the AI is getting smarter over time.

This is the meta-evaluation layer. It doesn't judge individual strategies;
it judges the SYSTEM's ability to generate, validate, and filter strategies.

Key metrics (from the strategic document):
- Hypothesis Throughput: strategies generated per day
- Compile Rate: % of strategies with executable RuleSet (vs advisory text)
- Backtest Survival: % of backtested strategies that survive cost model
- Kill Rate: % of strategies the system dares to retire
- Time to Falsification: hours from strategy creation to verdict
- Review Quality: % of KILL verdicts that have specific reasons (not "market bad")

Each metric is computed from the existing SQLite tables and emitted as
a JSONL event for time-series analysis.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("aletheia.strategy.performance_ledger")


@dataclass
class LedgerSnapshot:
    """Point-in-time performance snapshot of the strategy factory."""
    timestamp: str
    period_days: int  # lookback window for this snapshot

    # Hypothesis Throughput
    strategies_created: int
    strategies_per_day: float

    # Compile Rate
    strategies_with_ruleset: int
    compile_rate_pct: float      # 0-100

    # Backtest Coverage
    strategies_backtested: int
    backtest_coverage_pct: float  # 0-100

    # Backtest Survival
    strategies_surviving: int    # expectancy > 0 after costs
    survival_rate_pct: float     # 0-100

    # Kill Rate
    strategies_killed: int
    kill_rate_pct: float         # 0-100

    # Time to Falsification
    avg_time_to_verdict_hours: float  # mean hours from creation to first verdict
    median_time_to_verdict_hours: float

    # Overall Health
    active_strategies: int       # currently paper_running
    total_backtest_trades: int


def compute_ledger_snapshot(period_days: int = 7) -> LedgerSnapshot:
    """Compute a performance ledger snapshot for the last N days.

    Pulls data from strategy_records, backtest_runs, and audit events.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=period_days)).isoformat()

    # Strategies created in period
    rows = db.execute(
        "SELECT id, parameters, state, created_at FROM strategy_records WHERE created_at >= ?",
        (cutoff,)
    ).fetchall()
    strategies_created = len(rows)
    strategies_per_day = strategies_created / max(period_days, 1)

    # Compile rate: how many have a "ruleset" key in parameters
    with_ruleset = 0
    for r in rows:
        params_raw = r["parameters"] if "parameters" in r.keys() else "{}"
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else {}
            if params.get("ruleset"):
                with_ruleset += 1
        except (ValueError, TypeError):
            pass
    compile_rate = (with_ruleset / strategies_created * 100) if strategies_created else 0.0

    # Backtest coverage (of period strategies)
    period_ids = [r["id"] for r in rows]
    backtested = set()
    if period_ids:
        placeholders = ",".join("?" * len(period_ids))
        bt_rows = db.execute(
            f"SELECT DISTINCT strategy_id FROM backtest_runs WHERE strategy_id IN ({placeholders})",
            period_ids,
        ).fetchall()
        backtested = {r["strategy_id"] for r in bt_rows}
    backtest_coverage = (len(backtested) / strategies_created * 100) if strategies_created else 0.0

    # Survival rate (among backtested)
    surviving = 0
    if backtested:
        placeholders = ",".join("?" * len(backtested))
        surv_rows = db.execute(
            f"SELECT strategy_id, expectancy FROM backtest_runs "
            f"WHERE strategy_id IN ({placeholders}) ORDER BY created_at DESC",
            list(backtested),
        ).fetchall()
        # Take latest backtest per strategy
        seen: set[str] = set()
        for r in surv_rows:
            sid = r["strategy_id"]
            if sid not in seen:
                seen.add(sid)
                if r["expectancy"] > 0:
                    surviving += 1
    survival_rate = (surviving / len(backtested) * 100) if backtested else 0.0

    # Kill rate (retired in period)
    retired_rows = db.execute(
        "SELECT COUNT(*) as cnt FROM strategy_records WHERE state = 'retired' AND updated_at >= ?",
        (cutoff,)
    ).fetchone()
    killed = retired_rows["cnt"] if retired_rows else 0
    kill_rate = (killed / strategies_created * 100) if strategies_created else 0.0

    # Time to falsification: hours from created_at to first backtest_runs.created_at
    ttf_hours: list[float] = []
    if period_ids:
        for sid in period_ids:
            strat_row = next((r for r in rows if r["id"] == sid), None)
            if not strat_row:
                continue
            bt_row = db.execute(
                "SELECT created_at FROM backtest_runs WHERE strategy_id = ? ORDER BY created_at ASC LIMIT 1",
                (sid,)
            ).fetchone()
            if bt_row:
                try:
                    created = datetime.fromisoformat(strat_row["created_at"].replace("Z", "+00:00"))
                    backtested_at = datetime.fromisoformat(bt_row["created_at"].replace("Z", "+00:00"))
                    delta_h = (backtested_at - created).total_seconds() / 3600
                    ttf_hours.append(delta_h)
                except (ValueError, TypeError):
                    pass

    avg_ttf = sum(ttf_hours) / len(ttf_hours) if ttf_hours else 0.0
    sorted_ttf = sorted(ttf_hours)
    median_ttf = sorted_ttf[len(sorted_ttf) // 2] if sorted_ttf else 0.0

    # Active strategies
    active_row = db.execute(
        "SELECT COUNT(*) as cnt FROM strategy_records WHERE state = 'paper_running'"
    ).fetchone()
    active = active_row["cnt"] if active_row else 0

    # Total backtest trades in period
    bt_trades_row = db.execute(
        "SELECT COUNT(*) as cnt FROM backtest_trades bt "
        "JOIN backtest_runs br ON bt.run_id = br.id "
        "WHERE br.created_at >= ?",
        (cutoff,)
    ).fetchone()
    total_bt_trades = bt_trades_row["cnt"] if bt_trades_row else 0

    return LedgerSnapshot(
        timestamp=now.isoformat(),
        period_days=period_days,
        strategies_created=strategies_created,
        strategies_per_day=strategies_per_day,
        strategies_with_ruleset=with_ruleset,
        compile_rate_pct=round(compile_rate, 1),
        strategies_backtested=len(backtested),
        backtest_coverage_pct=round(backtest_coverage, 1),
        strategies_surviving=surviving,
        survival_rate_pct=round(survival_rate, 1),
        strategies_killed=killed,
        kill_rate_pct=round(kill_rate, 1),
        avg_time_to_verdict_hours=round(avg_ttf, 1),
        median_time_to_verdict_hours=round(median_ttf, 1),
        active_strategies=active,
        total_backtest_trades=total_bt_trades,
    )


def append_ledger_event(snapshot: LedgerSnapshot) -> None:
    """Append a ledger snapshot to the JSONL event stream."""
    from aletheia.files.jsonl import append_jsonl
    append_jsonl("performance_ledger", asdict(snapshot))


def load_ledger_history(max_entries: int = 14) -> list[LedgerSnapshot]:
    """Load recent ledger snapshots from the JSONL event stream."""
    from aletheia.files.jsonl import _log_dir

    path = _log_dir() / "performance_ledger"
    if not path.exists():
        return []

    entries: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except (OSError, ValueError):
        return []

    entries = entries[-max_entries:]
    fields = {f for f in LedgerSnapshot.__dataclass_fields__}
    snapshots = []
    for e in entries:
        try:
            snapshots.append(LedgerSnapshot(**{k: e[k] for k in fields if k in e}))
        except (TypeError, KeyError):
            continue
    return snapshots


@dataclass
class LedgerTrend:
    """Directional signal: is the system improving?"""
    improving: bool
    stagnant: bool
    signals: list[str]


def compute_ledger_trend() -> LedgerTrend:
    """Compare the two most recent ledger snapshots to detect improvement.

    Improvement signals (any 2+ = improving):
    - compile_rate_pct went up
    - survival_rate_pct went up
    - kill_rate_pct went up (system is more decisive)
    - avg_time_to_verdict_hours went down (faster falsification)
    - strategies_per_day went up (higher throughput)

    Stagnation = no improvement signals over 2+ consecutive snapshots.
    """
    history = load_ledger_history(max_entries=4)
    if len(history) < 2:
        return LedgerTrend(
            improving=False, stagnant=False,
            signals=["insufficient_data: need 2+ ledger snapshots"],
        )

    latest = history[-1]
    prev = history[-2]
    signals: list[str] = []

    if latest.compile_rate_pct > prev.compile_rate_pct:
        signals.append(f"compile_rate: {prev.compile_rate_pct:.0f}% -> {latest.compile_rate_pct:.0f}%")
    if latest.survival_rate_pct > prev.survival_rate_pct:
        signals.append(f"survival_rate: {prev.survival_rate_pct:.0f}% -> {latest.survival_rate_pct:.0f}%")
    if latest.kill_rate_pct > prev.kill_rate_pct:
        signals.append(f"kill_decisiveness: {prev.kill_rate_pct:.0f}% -> {latest.kill_rate_pct:.0f}%")
    if prev.avg_time_to_verdict_hours > 0 and latest.avg_time_to_verdict_hours < prev.avg_time_to_verdict_hours:
        signals.append(f"time_to_verdict: {prev.avg_time_to_verdict_hours:.1f}h -> {latest.avg_time_to_verdict_hours:.1f}h")
    if latest.strategies_per_day > prev.strategies_per_day:
        signals.append(f"throughput: {prev.strategies_per_day:.1f}/d -> {latest.strategies_per_day:.1f}/d")

    improving = len(signals) >= 2
    stagnant = len(signals) == 0

    if stagnant:
        signals.append("no improvement detected between last two snapshots")

    return LedgerTrend(improving=improving, stagnant=stagnant, signals=signals)


def format_ledger_report(snapshot: LedgerSnapshot) -> str:
    """Format a ledger snapshot as a human-readable markdown block."""
    return (
        f"## Performance Ledger — {snapshot.timestamp[:10]} ({snapshot.period_days}d window)\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Strategies Created | {snapshot.strategies_created} ({snapshot.strategies_per_day:.1f}/day) |\n"
        f"| Compile Rate | {snapshot.compile_rate_pct:.0f}% ({snapshot.strategies_with_ruleset} with RuleSet) |\n"
        f"| Backtest Coverage | {snapshot.backtest_coverage_pct:.0f}% ({snapshot.strategies_backtested} tested) |\n"
        f"| Survival Rate | {snapshot.survival_rate_pct:.0f}% ({snapshot.strategies_surviving} positive expectancy) |\n"
        f"| Kill Rate | {snapshot.kill_rate_pct:.0f}% ({snapshot.strategies_killed} retired) |\n"
        f"| Time to Verdict | {snapshot.avg_time_to_verdict_hours:.1f}h avg / {snapshot.median_time_to_verdict_hours:.1f}h median |\n"
        f"| Active Paper Strategies | {snapshot.active_strategies} |\n"
        f"| Backtest Trades (period) | {snapshot.total_backtest_trades} |\n"
    )
