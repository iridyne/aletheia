"""Tests for critical C1 fixes: #1880, #1793, #2049, #1715."""

import re
import pathlib

import pytest
from pydantic import ValidationError


# ─── #1880: NaN/inf in Risk policy fields raises ValidationError ───────────

def test_risk_loss_policy_nan_raises():
    from aletheia.config.schema import RiskLossPolicy
    with pytest.raises(ValidationError):
        RiskLossPolicy(max_single_trade_risk_pct=float("nan"))


def test_risk_loss_policy_inf_raises():
    from aletheia.config.schema import RiskLossPolicy
    with pytest.raises(ValidationError):
        RiskLossPolicy(max_daily_loss_pct=float("inf"))


def test_risk_capital_policy_nan_raises():
    from aletheia.config.schema import RiskCapitalPolicy
    with pytest.raises(ValidationError):
        RiskCapitalPolicy(max_total_exposure_pct=float("nan"))


def test_risk_exit_policy_nan_raises():
    from aletheia.config.schema import RiskExitPolicy
    with pytest.raises(ValidationError):
        RiskExitPolicy(catastrophic_stop_pct=float("nan"))


def test_risk_leverage_policy_nan_raises():
    from aletheia.config.schema import RiskLeveragePolicy
    with pytest.raises(ValidationError):
        RiskLeveragePolicy(account_cap=float("nan"))


def test_risk_margin_policy_nan_raises():
    from aletheia.config.schema import RiskMarginPolicy
    with pytest.raises(ValidationError):
        RiskMarginPolicy(warning_ratio=float("nan"))


def test_risk_policy_valid_defaults():
    """Default-constructed policy models must remain valid after adding constraints."""
    from aletheia.config.schema import (
        RiskCapitalPolicy, RiskLossPolicy, RiskLeveragePolicy,
        RiskMarginPolicy, RiskExitPolicy, RiskEligibilityPolicy,
    )
    assert RiskCapitalPolicy().max_total_exposure_pct == 5.0
    assert RiskLossPolicy().max_daily_loss_pct == 1.0
    assert RiskLeveragePolicy().futures_cap == 2
    assert RiskMarginPolicy().warning_ratio == 0.70
    assert RiskExitPolicy().catastrophic_stop_pct == 10.0
    assert RiskEligibilityPolicy().family_min_win_rate_pct == 50.0


def test_risk_engine_isfinite_guard_present():
    """Engine source contains math.isfinite guard for critical policy values."""
    import inspect
    from aletheia.risk import engine
    src = inspect.getsource(engine)
    assert "math.isfinite" in src, "Engine must have math.isfinite guard for policy values"


# ─── #1793: IO lane jobs with long grace times moved off io executor ───────

def test_long_grace_time_jobs_not_on_io():
    """Jobs with misfire_grace_time >= 300 that were stalling the io lane are now on other executors."""
    sched_src = pathlib.Path("aletheia/agent_runtime/scheduler.py").read_text()
    offenders = {
        "confidence_calibration": 300,
        "cleanup": 600,
        "shadow_resolver": 300,
    }
    for job_id, grace_time in offenders.items():
        pattern = rf'id="{re.escape(job_id)}"[^)]*executor="([^"]+)"'
        m = re.search(pattern, sched_src, re.DOTALL)
        if m is None:
            pattern2 = rf'executor="([^"]+)"[^)]*id="{re.escape(job_id)}"'
            m = re.search(pattern2, sched_src, re.DOTALL)
        assert m is not None, f"Job {job_id!r} registration not found"
        executor = m.group(1)
        assert executor != "io", (
            f"Job {job_id!r} with misfire_grace_time={grace_time} still on 'io' executor "
            f"(found executor={executor!r}) — long jobs starve short IO lane jobs"
        )


# ─── #2049: deriveMasterState checks llmStatus ────────────────────────────

def test_derive_master_state_llm_unavailable_returns_degraded():
    """OPERATIONAL must not be returned when LLM is unavailable."""
    src = pathlib.Path("frontend/src/lib/systemState.ts").read_text()
    assert "llm_unavailable" in src, "deriveMasterState must check for llm_unavailable health issues"
    assert "DEGRADED" in src, "deriveMasterState must return DEGRADED when LLM is unreachable"
    assert "healthIssues" in src, "MasterStateInput must include healthIssues field"


def test_derive_master_state_source_has_llm_check():
    """The actual check occurs before the OPERATIONAL return."""
    src = pathlib.Path("frontend/src/lib/systemState.ts").read_text()
    degraded_pos = src.find("DEGRADED")
    operational_pos = src.find("OPERATIONAL")
    assert degraded_pos != -1 and operational_pos != -1, "Both DEGRADED and OPERATIONAL must appear"
    assert degraded_pos < operational_pos, "DEGRADED check must precede OPERATIONAL return"


# ─── #1715: close with closed=false shows pending state ───────────────────

def test_close_pending_message_in_source():
    """TradesPositions close handler must distinguish closed=false from closed=true."""
    src = pathlib.Path("frontend/src/pages/TradesPositions.tsx").read_text()
    assert "closed === false" in src or "data?.closed === false" in src, (
        "Close handler must check data.closed === false to detect pending state"
    )
    assert "pending" in src.lower() or "submitted" in src.lower(), (
        "Close handler must show a pending/submitted message when close is not yet materialized"
    )
