"""Guard chain — composable pre-risk observability layer.

Each Guard is a simple function: (proposal_dict) -> GuardResult.
The chain runs all guards, collects verdicts, and returns a summary.

Guards are *advisory* — they don't block execution (Risk Engine is the gate).
Their purpose: structured observability of WHY a proposal looks good or risky
BEFORE it enters the formal risk evaluation, enabling audit trail enrichment
and early-warning signals for the operator.

Usage:
    from aletheia.trust.guard_chain import run_guards, GuardResult
    verdicts = run_guards(proposal_dict)
    # verdicts: list[GuardResult] — attach to audit or log
"""

from __future__ import annotations

from aletheia.analytics.outcome_integrity import (
    eligible_strategy_outcomes,
)

from dataclasses import dataclass, field
from typing import Callable

from aletheia.config.loader import load_config
from aletheia.storage import repositories as repo


@dataclass
class GuardResult:
    guard: str
    verdict: str  # "pass" | "warn" | "block"
    reason: str
    meta: dict = field(default_factory=dict)


GuardFn = Callable[[dict], GuardResult]

_guards: list[tuple[str, GuardFn]] = []


def register_guard(name: str):
    """Decorator to register a guard function."""
    def decorator(fn: GuardFn) -> GuardFn:
        _guards.append((name, fn))
        return fn
    return decorator


def run_guards(proposal: dict) -> list[GuardResult]:
    """Run all registered guards against a proposal dict. Never raises."""
    results = []
    for name, fn in _guards:
        try:
            result = fn(proposal)
            results.append(result)
        except Exception as e:
            results.append(GuardResult(
                guard=name, verdict="warn",
                reason=f"guard error: {e}",
            ))
    return results


def summarize(results: list[GuardResult]) -> dict:
    """Compact summary for audit trail attachment."""
    blocks = [r for r in results if r.verdict == "block"]
    warns = [r for r in results if r.verdict == "warn"]
    return {
        "guards_run": len(results),
        "blocks": len(blocks),
        "warnings": len(warns),
        "details": [
            {"guard": r.guard, "verdict": r.verdict, "reason": r.reason}
            for r in results if r.verdict != "pass"
        ],
    }


# --- Built-in Guards ---


@register_guard("exposure_concentration")
def _guard_exposure_concentration(proposal: dict) -> GuardResult:
    """Warn if adding to a symbol that already has open positions."""
    symbol = proposal.get("symbol", "")
    positions = repo.list_all_open_positions()
    same_symbol = [p for p in positions if p.get("symbol") == symbol]
    if len(same_symbol) >= 2:
        return GuardResult(
            guard="exposure_concentration",
            verdict="warn",
            reason=f"Already {len(same_symbol)} open positions on {symbol}",
            meta={"existing_count": len(same_symbol)},
        )
    return GuardResult(guard="exposure_concentration", verdict="pass", reason="ok")


@register_guard("recent_loss_streak")
def _guard_recent_loss_streak(proposal: dict) -> GuardResult:
    """Warn if the strategy has consecutive recent losses."""
    strategy_id = proposal.get("strategy_id")
    if not strategy_id:
        return GuardResult(guard="recent_loss_streak", verdict="pass", reason="no strategy")
    strategy_positions = sorted(
        [p for p in eligible_strategy_outcomes() if p.get("strategy_id") == strategy_id],
        key=lambda p: p.get("closed_at", ""),
        reverse=True,
    )[:5]
    losses = 0
    for p in strategy_positions:
        pnl = p.get("realized_pnl") or p.get("pnl_usd") or 0
        if pnl < 0:
            losses += 1
        else:
            break
    if losses >= 3:
        return GuardResult(
            guard="recent_loss_streak",
            verdict="warn",
            reason=f"Strategy has {losses} consecutive losses",
            meta={"consecutive_losses": losses},
        )
    return GuardResult(guard="recent_loss_streak", verdict="pass", reason="ok")


@register_guard("correlation_cluster")
def _guard_correlation_cluster(proposal: dict) -> GuardResult:
    """Warn if opening a correlated asset when similar exposure exists."""
    symbol = proposal.get("symbol", "")
    base = symbol.split("/")[0] if "/" in symbol else symbol

    correlated_groups = {
        "BTC": {"BTC"},
        "ETH": {"ETH"},
        "SOL": {"SOL", "JUP", "RAY", "BONK"},
        "DOGE": {"DOGE", "SHIB", "PEPE", "FLOKI"},
    }

    my_group = None
    for _key, members in correlated_groups.items():
        if base in members:
            my_group = members
            break

    if not my_group:
        return GuardResult(guard="correlation_cluster", verdict="pass", reason="ok")

    positions = repo.list_all_open_positions()
    direction = proposal.get("direction", "long")
    conflicting = []
    for p in positions:
        p_symbol = p.get("symbol", "")
        p_base = p_symbol.split("/")[0] if "/" in p_symbol else p_symbol
        if p_base in my_group and p_base != base and p.get("direction") == direction:
            conflicting.append(p_symbol)

    if conflicting:
        return GuardResult(
            guard="correlation_cluster",
            verdict="warn",
            reason=f"Correlated exposure: already {direction} on {', '.join(conflicting)}",
            meta={"conflicting": conflicting},
        )
    return GuardResult(guard="correlation_cluster", verdict="pass", reason="ok")


@register_guard("drawdown_brake")
def _guard_drawdown_brake(proposal: dict) -> GuardResult:
    """Block if daily drawdown exceeds configured threshold."""
    cfg = load_config()
    max_daily_loss = getattr(cfg.risk, "max_daily_loss_pct", 5.0)
    snapshots = repo.list_portfolio_snapshots(limit=2)
    if len(snapshots) < 2:
        return GuardResult(guard="drawdown_brake", verdict="pass", reason="insufficient data")

    latest = snapshots[0]
    prev = snapshots[1]
    prev_value = prev.get("total_value") or prev.get("total_equity") or 0
    latest_value = latest.get("total_value") or latest.get("total_equity") or 0
    if prev_value <= 0:
        return GuardResult(guard="drawdown_brake", verdict="pass", reason="no baseline")

    dd_pct = ((prev_value - latest_value) / prev_value) * 100
    if dd_pct >= max_daily_loss:
        return GuardResult(
            guard="drawdown_brake",
            verdict="block",
            reason=f"Daily drawdown {dd_pct:.1f}% >= limit {max_daily_loss}%",
            meta={"drawdown_pct": round(dd_pct, 2)},
        )
    if dd_pct >= max_daily_loss * 0.7:
        return GuardResult(
            guard="drawdown_brake",
            verdict="warn",
            reason=f"Daily drawdown {dd_pct:.1f}% approaching limit {max_daily_loss}%",
            meta={"drawdown_pct": round(dd_pct, 2)},
        )
    return GuardResult(guard="drawdown_brake", verdict="pass", reason="ok")
