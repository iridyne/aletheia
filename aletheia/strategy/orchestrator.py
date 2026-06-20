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

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from aletheia.strategy.backtest import BacktestConfig, load_bars_from_db, run_backtest
from aletheia.strategy.cost_model import CostModel
from aletheia.strategy.dsl import RuleSet
from aletheia.strategy.models import BacktestRun, BacktestTrade
from aletheia.strategy.repositories import insert_backtest_run, insert_backtest_trades, update_backtest_run_equity_curve
from aletheia.strategy.review import StrategyVerdict, Verdict, evaluate_strategy

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
    bar_limit: int = 2000,
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
    from aletheia.storage.repositories import list_strategies, update_strategy
    from aletheia.trading.strategy_lifecycle import promote_strategy

    cm = cost_model or CostModel()
    reports: list[BacktestReport] = []
    killed = kept = review_count = skipped = 0

    # Walk-forward config (read once, not per-strategy)
    from aletheia.config.loader import load_config as _ld_cfg
    _factory_cfg = _ld_cfg().strategy_factory
    _wf_enabled = _factory_cfg.walk_forward_enabled
    _wf_min_bars = _factory_cfg.walk_forward_min_bars

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
                # Track consecutive skip count in strategy parameters
                skip_count = int(params.get("_backtest_skip_count", 0)) + 1
                params["_backtest_skip_count"] = skip_count
                update_strategy(sid, parameters=json.dumps(params))

                # Auto-kill after 3 consecutive skips (symbol is unbacktestable)
                if skip_count >= 3:
                    promote_strategy(sid, "retired")
                    killed += 1
                    reports.append(BacktestReport(
                        strategy_id=sid, strategy_name=name,
                        symbol=symbol, timeframe=timeframe,
                        bar_count=len(bars), trade_count=0,
                        verdict="kill",
                        verdict_reasons=["insufficient_ohlcv_data", f"consecutive_skips={skip_count}"],
                        action_taken="retired",
                    ))
                    logger.info("auto-killed %s: insufficient data after %d consecutive skips", sid, skip_count)
                else:
                    skipped += 1
                    reports.append(BacktestReport(
                        strategy_id=sid, strategy_name=name,
                        symbol=symbol, timeframe=timeframe,
                        bar_count=len(bars), trade_count=0,
                        verdict="review",
                        verdict_reasons=["insufficient_ohlcv_data", f"skip_{skip_count}_of_3"],
                        action_taken="skipped",
                    ))
                    # Trigger async OHLCV backfill for next cycle
                    _request_ohlcv_backfill(symbol, timeframe)
                continue

            # Reset skip counter on successful bar load
            if params.get("_backtest_skip_count"):
                params["_backtest_skip_count"] = 0
                update_strategy(sid, parameters=json.dumps(params))

            cfg = BacktestConfig(
                symbol=symbol, timeframe=timeframe,
                direction=direction, cost_model=cm,
            )
            result = run_backtest(ruleset, bars, cfg)
            m = result.metrics

            # Persist backtest results
            eq_curve = result.equity_curve
            eq_json = json.dumps([round(v, 2) for v in eq_curve]) if eq_curve else None

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
                equity_curve_json=eq_json,
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

            # Walk-forward OOS validation: strategies that pass backtest
            # must also show positive expectancy out-of-sample.
            if (_wf_enabled
                    and verdict.verdict != Verdict.KILL
                    and len(bars) >= _wf_min_bars):
                try:
                    from aletheia.strategy.walk_forward import run_walk_forward
                    wf_result = run_walk_forward(ruleset, bars, cfg)
                    if not wf_result.passed:
                        verdict = StrategyVerdict(
                            verdict=Verdict.KILL,
                            reasons=[
                                "walk_forward_failed",
                                f"oos_expectancy={wf_result.oos_avg_expectancy:.3f}",
                                f"degradation={wf_result.degradation_ratio:.2f}",
                            ],
                            metrics_summary={
                                **verdict.metrics_summary,
                                "wf_oos_expectancy": wf_result.oos_avg_expectancy,
                                "wf_degradation": wf_result.degradation_ratio,
                            },
                        )
                except Exception as wf_err:
                    logger.debug("walk_forward skipped for %s: %s", sid, wf_err)

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

    orch_result = OrchestratorResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        strategies_evaluated=len(active),
        strategies_killed=killed,
        strategies_kept=kept,
        strategies_review=review_count,
        strategies_skipped=skipped,
        reports=reports,
    )

    # Auto lifecycle: promote survivors that pass gates, pause consistent losers
    try:
        run_lifecycle_automation()
    except Exception as e:
        logger.warning("lifecycle_automation failed: %s", e)

    return orch_result


def run_lifecycle_automation() -> dict:
    """Auto-promote strategies that pass promotion gates; auto-pause consistent losers.

    Runs after backtests so evidence is fresh. Does NOT touch live transitions
    (those require explicit human approval via propose_strategy_promotion).
    """
    from aletheia.storage.repositories import list_strategies, update_strategy
    from aletheia.trading.strategy_lifecycle import (
        check_promotion_gates, promote_strategy,
        DEFAULT_PROMOTION_GATES,
    )

    promoted = []
    paused = []

    for strat in list_strategies(limit=200):
        sid = strat["id"]
        state = strat.get("state")

        # Auto-promote paper_running → paper_passed when all gates pass
        if state == "paper_running" and not strat.get("paper_trading_paused") and not strat.get("under_review"):
            gates = check_promotion_gates(sid)
            if gates["passed"]:
                try:
                    promote_strategy(sid, "paper_passed")
                    logger.info("auto_promoted %s to paper_passed", sid)
                    promoted.append(sid)
                except Exception as e:
                    logger.warning("auto_promote failed for %s: %s", sid, e)
                continue

            # Auto-pause if consecutive losses exceed gate threshold
            max_consec = DEFAULT_PROMOTION_GATES["max_consecutive_losses_allowed"]
            consec = gates.get("metrics", {}).get("max_consecutive_losses")
            if consec is not None and consec > max_consec:
                try:
                    from datetime import datetime, timezone
                    update_strategy(
                        sid,
                        paper_trading_paused=True,
                        paper_pause_reason=f"auto_pause:consecutive_losses={consec}>{max_consec}",
                        paper_paused_at=datetime.now(timezone.utc).isoformat(),
                    )
                    logger.info("auto_paused %s (consecutive_losses=%s)", sid, consec)
                    paused.append(sid)
                except Exception as e:
                    logger.warning("auto_pause failed for %s: %s", sid, e)

    return {"promoted": promoted, "paused": paused}


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


def _request_ohlcv_backfill(symbol: str, timeframe: str) -> None:
    """Best-effort async OHLCV fetch to populate data for next backtest cycle."""
    try:
        from aletheia.data.ccxt_provider import get_market_data_provider

        async def _fetch():
            provider = get_market_data_provider()
            try:
                await provider.get_ohlcv(symbol, timeframe=timeframe, limit=200)
            finally:
                await provider.close()

        asyncio.run(_fetch())
        logger.info("ohlcv_backfill completed: %s/%s (200 bars)", symbol, timeframe)
    except Exception as e:
        logger.debug("ohlcv_backfill failed for %s/%s: %s", symbol, timeframe, e)
