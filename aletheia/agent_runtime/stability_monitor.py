"""Stability monitor — quantifies "is the system actually running cleanly?".

Closes the gap between "process is alive" (existing health.py) and "the data the
process produces is internally consistent". Stage-0 evidence (north-star §7) is
"connected stable run for 7-14 days, no crash, no orphan orders, no lost state" —
this module turns those qualitative criteria into numbers + a stability score so
the operator can see at a glance whether the bar has been met.

Read-only against state. Never modifies anything (an inconsistency is REPORTED,
not silently repaired — repair is a deliberate human act, trust §1.1).
"""

from datetime import datetime, timezone, timedelta
from typing import Any

from aletheia.storage.repositories import (
    list_orders, list_fills, list_positions, list_trade_proposals, list_agent_runs,
)


_FAILED_STATUSES = {"failed", "error", "stale"}


def _orphan_counts() -> dict[str, int]:
    """Detect causal-chain breaks (trust §1.1). Each non-zero count is a real break."""
    proposals = {p["id"]: p for p in list_trade_proposals(limit=10000)}
    orders = list_orders(limit=10000)
    fills = list_fills(limit=10000)
    positions = list_positions(limit=10000)

    order_ids = {o["id"] for o in orders}
    proposal_ids = set(proposals.keys())

    return {
        # Fills must reference a real order (FK in schema; counts deviations).
        "orphan_fills": sum(1 for f in fills if f.get("order_id") not in order_ids),
        # Orders should reference a real proposal (nullable, but if set, must resolve).
        "orphan_orders": sum(
            1 for o in orders
            if o.get("proposal_id") and o["proposal_id"] not in proposal_ids
        ),
        # Executed proposals should have a corresponding order row.
        "executed_proposals_without_order": sum(
            1 for p in proposals.values()
            if p.get("status") == "executed"
            and not any(o.get("proposal_id") == p["id"] for o in orders)
        ),
        # Open positions whose proposal_id (new FK from #1) doesn't resolve.
        "orphan_positions": sum(
            1 for pos in positions
            if pos.get("proposal_id") and pos["proposal_id"] not in proposal_ids
        ),
    }


def _strategy_id_drift() -> dict[str, Any]:
    """Fraction of paper trades NOT bound to a strategy — direct signal of the
    causal-chain dead link the experience-loop work fixed. Pre-fix this was 100%."""
    proposals = list_trade_proposals(limit=10000)
    paper = [p for p in proposals if (p.get("execution_mode") or "").startswith("paper")]
    if not paper:
        return {"paper_proposals": 0, "without_strategy_id": 0, "ratio": None}
    without = sum(1 for p in paper if not p.get("strategy_id"))
    return {
        "paper_proposals": len(paper),
        "without_strategy_id": without,
        "ratio": round(without / len(paper), 4),
    }


def _agent_run_failure_window() -> dict[str, Any]:
    """Agent-run health over the last 24h (window-bounded, not just last N rows)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    runs = [r for r in list_agent_runs(limit=2000)
            if (r.get("started_at") or "") >= cutoff]
    if not runs:
        return {"total": 0, "failed": 0, "ratio": None}
    failed = sum(1 for r in runs if (r.get("status") or "") in _FAILED_STATUSES)
    return {"total": len(runs), "failed": failed, "ratio": round(failed / len(runs), 4)}


def _uptime_hours() -> float | None:
    """Hours since the earliest agent_run we still have on file. None if no runs.

    A pragmatic "operational lifetime" signal — north-star §7 stage-0 wants 7-14
    days of clean uptime. Bounded by retention (default agent_runs_days=30).
    """
    runs = list_agent_runs(limit=2000)
    if not runs:
        return None
    starts = [r.get("started_at") for r in runs if r.get("started_at")]
    if not starts:
        return None
    earliest = min(starts)
    try:
        dt = datetime.fromisoformat(earliest.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 2)
    except (ValueError, AttributeError):
        return None


def _score(orphans: dict[str, int], drift: dict, runs: dict) -> tuple[int, list[str]]:
    """Compose a 0-100 stability score with explicit reason codes.

    Score is intentionally simple — it's a quick triage signal, not a black-box.
    Reasons accompany every deduction so it's auditable.
    """
    score = 100
    reasons: list[str] = []
    total_orphans = sum(orphans.values())
    if total_orphans:
        score -= min(40, total_orphans * 10)
        reasons.append(f"orphans:{total_orphans}")
    drift_ratio = drift.get("ratio")
    if drift_ratio is not None and drift_ratio > 0.1:
        score -= int(min(30, drift_ratio * 30))
        reasons.append(f"strategy_id_drift:{drift_ratio:.2f}")
    runs_ratio = runs.get("ratio")
    if runs_ratio is not None and runs_ratio > 0.2:
        score -= int(min(30, (runs_ratio - 0.2) * 100))
        reasons.append(f"agent_failure_ratio_24h:{runs_ratio:.2f}")
    return max(0, score), reasons


def compute_stability() -> dict[str, Any]:
    """Compute the full stability report. Read-only; safe to call from API path."""
    orphans = _orphan_counts()
    drift = _strategy_id_drift()
    runs = _agent_run_failure_window()
    score, reasons = _score(orphans, drift, runs)
    return {
        "score": score,
        "reasons": reasons,
        "orphans": orphans,
        "strategy_id_drift": drift,
        "agent_runs_24h": runs,
        "uptime_hours": _uptime_hours(),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
