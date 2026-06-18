"""Strategy feedback loop — surfaces kill reasons to inform next generation.

The core insight: if the system keeps generating strategies that get killed for
the same reasons (excessive drawdown, negative expectancy), it's not learning.
This module closes the loop by injecting recent failures as negative constraints
into strategy_designer's LLM prompt.

This is what makes "compile_rate" and "kill_rate" trend in the right direction
over time — the proof that the AI brain is getting smarter.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("aletheia.strategy.feedback")


def get_recent_kill_reasons(days: int = 7, limit: int = 5) -> list[dict]:
    """Retrieve recent strategy kill verdicts with their reasons.

    Returns a list of dicts with:
    - strategy_name: str
    - symbol: str
    - timeframe: str
    - verdict_reasons: list[str]
    - metrics: dict (win_rate, expectancy, max_drawdown_pct, profit_factor)
    - killed_at: str (ISO timestamp)
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = db.execute(
        "SELECT sr.name, sr.parameters, sr.updated_at, "
        "       br.win_rate, br.expectancy, br.max_drawdown_pct, br.profit_factor, "
        "       br.symbol, br.timeframe "
        "FROM strategy_records sr "
        "LEFT JOIN backtest_runs br ON br.strategy_id = sr.id "
        "WHERE sr.state = 'retired' AND sr.updated_at >= ? "
        "ORDER BY sr.updated_at DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()

    results = []
    for r in rows:
        params_raw = r["parameters"] or "{}"
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else {}
        except (ValueError, TypeError):
            params = {}

        kill_reasons = []
        win_rate = r["win_rate"]
        expectancy = r["expectancy"]
        max_dd = r["max_drawdown_pct"]
        pf = r["profit_factor"]

        if max_dd is not None and max_dd > 8.0:
            kill_reasons.append(f"drawdown {max_dd:.1f}% exceeded 8% limit")
        if win_rate is not None and win_rate < 0.20:
            kill_reasons.append(f"win_rate {win_rate:.1%} below 20% minimum")
        if expectancy is not None and expectancy < 0:
            kill_reasons.append(f"negative expectancy {expectancy:+.2f}")
        if pf is not None and pf < 0.8:
            kill_reasons.append(f"profit_factor {pf:.2f} below 0.8")

        if not kill_reasons:
            kill_reasons.append("killed (no backtest metrics available)")

        results.append({
            "strategy_name": r["name"] or "unnamed",
            "symbol": r["symbol"] or params.get("symbol", "?"),
            "timeframe": r["timeframe"] or params.get("timeframe", "?"),
            "verdict_reasons": kill_reasons,
            "metrics": {
                "win_rate": win_rate,
                "expectancy": expectancy,
                "max_drawdown_pct": max_dd,
                "profit_factor": pf,
            },
            "killed_at": r["updated_at"],
        })

    return results


def get_compile_failures(days: int = 7) -> dict:
    """Get stats on strategies that failed to compile to executable RuleSet.

    Returns:
        {"total": int, "compiled": int, "advisory_only": int, "compile_rate_pct": float}
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = db.execute(
        "SELECT parameters FROM strategy_records WHERE created_at >= ?",
        (cutoff,),
    ).fetchall()

    total = len(rows)
    compiled = 0
    for r in rows:
        params_raw = r["parameters"] or "{}"
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else {}
            if params.get("ruleset"):
                compiled += 1
        except (ValueError, TypeError):
            pass

    return {
        "total": total,
        "compiled": compiled,
        "advisory_only": total - compiled,
        "compile_rate_pct": (compiled / total * 100) if total else 0.0,
    }


def format_kill_feedback_block(kills: list[dict], compile_stats: dict) -> str:
    """Format kill reasons + compile stats as a prompt block for strategy_designer.

    This becomes a negative-constraint section in the LLM prompt, teaching it
    to avoid patterns that repeatedly get killed.
    """
    if not kills and compile_stats.get("total", 0) == 0:
        return ""

    lines = ["\n\n## Recent Strategy Failures (AVOID these patterns)\n"]

    if kills:
        for k in kills[:5]:
            reasons = "; ".join(k["verdict_reasons"][:3])
            lines.append(
                f"- **{k['strategy_name']}** ({k['symbol']} {k['timeframe']}): "
                f"KILLED — {reasons}"
            )
        lines.append("")

    if compile_stats.get("total", 0) > 0:
        rate = compile_stats["compile_rate_pct"]
        lines.append(
            f"Compile rate last 7d: {rate:.0f}% "
            f"({compile_stats['compiled']}/{compile_stats['total']} produced executable rules). "
        )
        if rate < 80:
            lines.append(
                "**Priority: output concrete indicator conditions, not vague text rules.**"
            )

    lines.append(
        "\nDo NOT repeat the above failing patterns. Design strategies that address "
        "the weaknesses shown (use tighter stops if drawdown kills, require higher "
        "win rate setups if killed for low win rate, etc.)."
    )

    return "\n".join(lines)


def get_winning_strategy_templates(days: int = 30, limit: int = 3) -> list[dict]:
    """Retrieve strategies that are alive and performing well (positive templates).

    Returns structural summaries of KEEP/paper_passed/paper_running strategies
    with positive expectancy — the LLM should generate MORE like these.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = db.execute(
        "SELECT sr.name, sr.parameters, sr.state, "
        "       br.win_rate, br.expectancy, br.profit_factor, br.symbol, br.timeframe "
        "FROM strategy_records sr "
        "JOIN backtest_runs br ON br.strategy_id = sr.id "
        "WHERE sr.state IN ('paper_running', 'paper_passed', 'live_candidate', 'live_limited') "
        "  AND br.expectancy > 0 AND br.trade_count >= 5 "
        "  AND br.created_at >= ? "
        "ORDER BY br.expectancy DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()

    results = []
    for r in rows:
        params_raw = r["parameters"] or "{}"
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else {}
        except (ValueError, TypeError):
            params = {}

        ruleset_json = params.get("ruleset")
        direction = params.get("direction_bias", "neutral")
        source = params.get("source", "unknown")

        results.append({
            "strategy_name": r["name"] or "unnamed",
            "symbol": r["symbol"] or params.get("symbol", "?"),
            "timeframe": r["timeframe"] or params.get("timeframe", "?"),
            "direction": direction,
            "source": source,
            "has_ruleset": bool(ruleset_json),
            "metrics": {
                "win_rate": r["win_rate"],
                "expectancy": r["expectancy"],
                "profit_factor": r["profit_factor"],
            },
            "state": r["state"],
        })

    return results


def format_winning_templates_block(templates: list[dict]) -> str:
    """Format winning strategy templates as a positive-guidance prompt block."""
    if not templates:
        return ""

    lines = ["\n\n## Winning Strategy Templates (generate MORE like these)\n"]
    for t in templates:
        m = t["metrics"]
        lines.append(
            f"- **{t['strategy_name']}** ({t['symbol']} {t['timeframe']} {t['direction']}): "
            f"win_rate={m['win_rate']:.0%}, expectancy={m['expectancy']:+.2f}, "
            f"PF={m['profit_factor']:.2f} — state={t['state']}"
        )
    lines.append(
        "\nDesign strategies with SIMILAR structure to the winners above "
        "(same scanner family, direction style, or timeframe) but for DIFFERENT symbols or "
        "with parameter variations that might capture a related edge."
    )
    return "\n".join(lines)
