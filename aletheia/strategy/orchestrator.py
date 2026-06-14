"""Backtest orchestrator — batch-run backtests and apply review verdicts.

This is the "evaluation engine" that proves the AI is getting smarter over time.
It runs backtests for all active strategies with executable RuleSets, evaluates
them, and takes action (retire killed strategies, keep survivors).

Designed to be called from:
- Scheduler (daily cron job)
- CLI (`aletheia backtest`)
- Agent (strategy review agent)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from aletheia.strategy.backtest import BacktestConfig, load_bars_from_db, run_backtest
from aletheia.strategy.cost_model import CostModel
from aletheia.strategy.dsl import RuleSet
from aletheia.strategy.models import BacktestRun, BacktestTrade
from aletheia.strategy.repositories import insert_backtest_run, insert_backtest_trades
from aletheia.strategy.review import Verdict, evaluate_strategy

logger = logging.getLogger("aletheia.strategy.orchestrator")


@dataclass
class BacktestReport:
    """Result of one strategy's backtest + review cycle."""
    strategy_id: str
    strategy_name: str
    symbol: str
    timeframe: str
    bar_count: int
    trade_count: int
    verdict: str              # "kill" | "keep" | "review"
    verdict_reasons: list[str]
    action_taken: str         # "retired" | "kept" | "flagged_for_review" | "skipped"
    metrics: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class OrchestratorResult:
    """Result of a full orchestration run."""
    timestamp: str
    strategies_evaluated: int
    strategies_killed: int
    strategies_kept: int
    strategies_review: int
    strategies_skipped: int
    reports: list[BacktestReport]


def run_all_backtests(
    retire_killed: bool = True,
    cost_model: CostModel | None = None,
    bar_limit: int = 500,
) -> OrchestratorResult:
    """Run backtests for all active strategies with executable RuleSets.

    For each paper_running strategy:
    1. Parse RuleSet from parameters["ruleset"]
    2. Load OHLCV bars from SQLite
    3. Run backtest
    4. Persist results (BacktestRun + BacktestTrade records)
    5. Evaluate via structured review (kill/keep/review)
    6. If retire_killed=True and verdict=KILL → retire the strategy

    Returns OrchestratorResult with per-strategy reports.
    """
    from aletheia.storage.repositories import list_strategies
    from aletheia.trading.strategy_lifecycle import promote_strategy

    cm = cost_model or CostModel()
    reports: list[BacktestReport] = []
    killed = kept = review_count = skipped = 0

    strategies = list_strategies(limit=200)
    active = [s for s in strategies if s.get("state") in ("paper_running", "paper_candidate")]

    for strat in active:
        sid = strat["id"]
        name = strat.get("name") or "unnamed"
        params_raw = strat.get("parameters") or "{}"
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
        except (ValueError, TypeError):
            params = {}

        ruleset_json = params.get("ruleset")
        if not ruleset_json:
            skipped += 1
            reports.append(BacktestReport(
                strategy_id=sid, strategy_name=name,
                symbol=params.get("symbol", "?"), timeframe=params.get("timeframe", "?"),
                bar_count=0, trade_count=0,
                verdict="review", verdict_reasons=["no_executable_ruleset"],
                action_taken="skipped",
            ))
            continue

        symbol = params.get("symbol", "BTC/USDT")
        timeframe = params.get("timeframe", "1h")
        direction = params.get("direction_bias", "long")
        if direction == "neutral":
            direction = "long"

        try:
            ruleset = RuleSet.from_json(ruleset_json)
            bars = load_bars_from_db(symbol, timeframe, limit=bar_limit)

            if len(bars) < 30:
                skipped += 1
                reports.append(BacktestReport(
                    strategy_id=sid, strategy_name=name,
                    symbol=symbol, timeframe=timeframe,
                    bar_count=len(bars), trade_count=0,
                    verdict="review", verdict_reasons=["insufficient_ohlcv_data"],
                    action_taken="skipped",
                ))
                continue

            cfg = BacktestConfig(
                symbol=symbol, timeframe=timeframe,
                direction=direction, cost_model=cm,
            )
            result = run_backtest(ruleset, bars, cfg)
            m = result.metrics

            # Persist backtest results
            run_record = BacktestRun(
                strategy_id=sid,
                symbol=symbol, timeframe=timeframe, direction=direction,
                bar_count=len(bars), initial_capital=cfg.initial_capital,
                cost_model_json=json.dumps(asdict(cm)),
                ruleset_json=ruleset_json,
                trade_count=m.trade_count, win_rate=m.win_rate,
                total_return_pct=m.total_return_pct, expectancy=m.expectancy,
                profit_factor=m.profit_factor if m.profit_factor != float('inf') else 999.0,
                max_drawdown_pct=m.max_drawdown_pct, sharpe_approx=m.sharpe_approx,
                avg_trade_bars=m.avg_trade_bars,
            )
            insert_backtest_run(run_record)

            trade_records = [
                BacktestTrade(
                    run_id=run_record.id, trade_index=i, direction=t.direction,
                    entry_ts=t.entry_ts, exit_ts=t.exit_ts,
                    entry_bar_index=t.entry_bar_index, exit_bar_index=t.exit_bar_index,
                    entry_price=t.entry_price, exit_price=t.exit_price,
                    quantity=t.quantity, entry_fee=t.entry_fee, exit_fee=t.exit_fee,
                    entry_slippage=t.entry_slippage, exit_slippage=t.exit_slippage,
                    pnl=t.pnl, return_pct=t.return_pct, exit_reason=t.exit_reason,
                )
                for i, t in enumerate(result.trades)
            ]
            insert_backtest_trades(trade_records)

            # Evaluate verdict
            pf = m.profit_factor if m.profit_factor != float('inf') else 999.0
            verdict = evaluate_strategy(
                trade_count=m.trade_count, win_rate=m.win_rate,
                expectancy=m.expectancy, profit_factor=pf,
                max_drawdown_pct=m.max_drawdown_pct,
                total_return_pct=m.total_return_pct,
            )

            action = "flagged_for_review"
            if verdict.verdict == Verdict.KILL and retire_killed:
                promote_strategy(sid, "retired")
                action = "retired"
                killed += 1
            elif verdict.verdict == Verdict.KEEP:
                action = "kept"
                kept += 1
            else:
                review_count += 1

            reports.append(BacktestReport(
                strategy_id=sid, strategy_name=name,
                symbol=symbol, timeframe=timeframe,
                bar_count=len(bars), trade_count=m.trade_count,
                verdict=verdict.verdict.value,
                verdict_reasons=verdict.reasons,
                action_taken=action,
                metrics=verdict.metrics_summary,
            ))

        except Exception as e:
            logger.warning("Backtest failed for strategy %s: %s", sid, e)
            skipped += 1
            reports.append(BacktestReport(
                strategy_id=sid, strategy_name=name,
                symbol=symbol, timeframe=timeframe,
                bar_count=0, trade_count=0,
                verdict="review", verdict_reasons=[],
                action_taken="skipped", error=str(e),
            ))

    return OrchestratorResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        strategies_evaluated=len(active),
        strategies_killed=killed,
        strategies_kept=kept,
        strategies_review=review_count,
        strategies_skipped=skipped,
        reports=reports,
    )


def generate_daily_report(result: OrchestratorResult) -> str:
    """Generate a markdown daily review report from orchestrator results."""
    lines = [
        f"# Strategy Backtest Report — {result.timestamp[:10]}",
        "",
        f"**Evaluated**: {result.strategies_evaluated} | "
        f"**Killed**: {result.strategies_killed} | "
        f"**Kept**: {result.strategies_kept} | "
        f"**Review**: {result.strategies_review} | "
        f"**Skipped**: {result.strategies_skipped}",
        "",
    ]

    for r in result.reports:
        icon = {"kill": "💀", "keep": "✅", "review": "⚠️"}.get(r.verdict, "?")
        lines.append(f"## {icon} {r.strategy_name} ({r.strategy_id})")
        lines.append(f"- Symbol: {r.symbol} | Timeframe: {r.timeframe} | Bars: {r.bar_count}")
        lines.append(f"- Trades: {r.trade_count} | Verdict: **{r.verdict.upper()}** → {r.action_taken}")
        if r.metrics:
            lines.append(
                f"- Win rate: {r.metrics.get('win_rate', 0):.1%} | "
                f"Expectancy: {r.metrics.get('expectancy', 0):+.2f} | "
                f"PF: {r.metrics.get('profit_factor', 0):.2f} | "
                f"MaxDD: {r.metrics.get('max_drawdown_pct', 0):.1f}%"
            )
        if r.verdict_reasons:
            lines.append(f"- Reasons: {'; '.join(r.verdict_reasons[:3])}")
        if r.error:
            lines.append(f"- Error: {r.error}")
        lines.append("")

    if not result.reports:
        lines.append("*No active strategies with executable rules found.*")

    return "\n".join(lines)
