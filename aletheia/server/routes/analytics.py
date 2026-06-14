"""Read-only analytics API routes."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/strategy-evidence")
def get_strategy_evidence():
    """Per-strategy evidence snapshot with recommended actions."""
    from aletheia.analytics.strategy_evidence import build_strategy_evidence_snapshot
    return build_strategy_evidence_snapshot(limit=100)


@router.get("/backtest-paper-drift")
def get_backtest_paper_drift():
    """Backtest vs paper drift analysis per strategy."""
    from aletheia.analytics.backtest_paper_drift import build_backtest_paper_drift
    return build_backtest_paper_drift(limit=100)


@router.get("/exit-policy-advice")
def get_exit_policy_advice():
    """Exit policy diagnosis and suggestions."""
    from aletheia.analytics.exit_policy_advisor import build_exit_policy_advice
    return build_exit_policy_advice(limit=500)


@router.get("/trade-paths")
def get_trade_paths():
    """MFE/MAE trade path analysis for closed trades."""
    from aletheia.analytics.trade_path import build_trade_path_rows
    return build_trade_path_rows(limit=200)
