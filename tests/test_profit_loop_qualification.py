"""Tests for profit_loop_qualification — #988 whole-policy profitability verdict."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aletheia.analytics.outcome_integrity as oi_module
import aletheia.analytics.profit_loop_qualification as qual_module
from aletheia.analytics.profit_loop_qualification import (
    QUALIFICATION_CONTRACT_VERSION,
    compile_qualification_verdict,
)
from aletheia.cli.inspect_cmd import inspect_app
from typer.testing import CliRunner


runner = CliRunner()

_REGISTRATION = {
    "deploy_sha": "abc123" * 10,
    "runtime_session_id": "session-001",
    "execution_mode": "paper_internal",
    "policy_epoch_id": "epoch-001",
    "improvement_id": "breakeven_after_1r/v1",
    "registration_at": "2026-01-01T00:00:00+00:00",
    "stopping_rule": "30_days_or_50_genuine_closes",
    "cost_model_id": "default/v1",
    "max_drawdown_cap": 0.20,
}

_CUTOFF = "2026-02-01T00:00:00+00:00"

_BASE_DATE = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)


def _make_outcomes(pnls: list[float], opened_at: str = "2026-01-02T00:00:00+00:00") -> list[dict]:
    return [
        {
            "realized_pnl": pnl,
            "opened_at": opened_at,
            "closed_at": (_BASE_DATE + timedelta(days=i)).isoformat(),
            "execution_mode": "paper_internal",
            "status": "closed",
        }
        for i, pnl in enumerate(pnls)
    ]


def _patch_outcomes(monkeypatch, pnls: list[float]) -> None:
    outcomes = _make_outcomes(pnls)
    monkeypatch.setattr(qual_module, "eligible_strategy_outcomes", lambda **_: outcomes)
    monkeypatch.setattr(
        qual_module,
        "build_outcome_cohort_summary",
        lambda **_: {
            "raw_event_count": len(outcomes),
            "eligible_outcome_count": len(outcomes),
            "excluded_outcome_count": 0,
            "outcome_exclusion_reasons": {},
        },
    )


def test_proven_verdict_all_gates_pass(monkeypatch):
    # 15 uniform wins + 5 uniform losses → net > 0, PF > 1, top1 ≈ 5% < 25%, top5 ≈ 25% < 50%
    pnls = [1.0] * 15 + [-0.5] * 5
    _patch_outcomes(monkeypatch, pnls)
    result = compile_qualification_verdict(registration=_REGISTRATION, cutoff_at=_CUTOFF)
    assert result["contract_version"] == QUALIFICATION_CONTRACT_VERSION
    assert result["verdict"] == "minimum_profitable_loop_proven"
    assert result["metrics"]["net_realized_pnl"] > 0
    assert result["metrics"]["profit_factor"] > 1.0
    assert result["content_hash"]


def test_rejected_verdict_negative_pnl(monkeypatch):
    _patch_outcomes(monkeypatch, [-5.0, -3.0, 1.0, -2.0])
    result = compile_qualification_verdict(registration=_REGISTRATION, cutoff_at=_CUTOFF)
    assert result["verdict"] == "minimum_profitable_loop_rejected"
    assert "net_pnl_positive" in result["gates"]["failed_gates"]


def test_rejected_verdict_concentration_breach(monkeypatch):
    # One trade is 90% of all absolute PnL — breaches top-1 cap
    _patch_outcomes(monkeypatch, [90.0, 1.0, 1.0, 1.0, 1.0, -5.0])
    result = compile_qualification_verdict(registration=_REGISTRATION, cutoff_at=_CUTOFF)
    assert result["verdict"] == "minimum_profitable_loop_rejected"
    assert "concentration_top1" in result["gates"]["failed_gates"]


def test_insufficient_data_no_outcomes(monkeypatch):
    _patch_outcomes(monkeypatch, [])
    result = compile_qualification_verdict(registration=_REGISTRATION, cutoff_at=_CUTOFF)
    assert result["verdict"] == "insufficient_data"


def test_drawdown_gate_enforced_when_cap_registered(monkeypatch):
    # 100 loss then recovery — large drawdown > 20% cap
    _patch_outcomes(monkeypatch, [1.0, -50.0, 60.0, 1.0, 1.0, 1.0, 1.0])
    reg = {**_REGISTRATION, "max_drawdown_cap": 0.05}
    result = compile_qualification_verdict(registration=reg, cutoff_at=_CUTOFF)
    assert result["gates"]["max_drawdown_bounded"]["pass"] is False
    assert result["verdict"] == "minimum_profitable_loop_rejected"


def test_drawdown_gate_recorded_only_without_cap(monkeypatch):
    _patch_outcomes(monkeypatch, [10.0, 5.0, -2.0, 8.0, 3.0])
    reg = {k: v for k, v in _REGISTRATION.items() if k != "max_drawdown_cap"}
    result = compile_qualification_verdict(registration=reg, cutoff_at=_CUTOFF)
    # Gate is recorded but not enforced
    assert result["gates"]["max_drawdown_bounded"]["pass"] is None


def test_artifact_is_hashed_and_reproducible(monkeypatch):
    _patch_outcomes(monkeypatch, [5.0, -1.0, 3.0, 2.0])
    r1 = compile_qualification_verdict(registration=_REGISTRATION, cutoff_at=_CUTOFF)
    r2 = compile_qualification_verdict(registration=_REGISTRATION, cutoff_at=_CUTOFF)
    # Content hash must be deterministic (generated_at differs, so hash differs — that's expected)
    assert r1["content_hash"]
    assert len(r1["content_hash"]) == 64  # SHA-256 hex


def test_cli_command_emits_versioned_json(monkeypatch, tmp_path):
    outcomes = _make_outcomes([1.0] * 15 + [-0.5] * 5)
    monkeypatch.setattr(qual_module, "eligible_strategy_outcomes", lambda **_: outcomes)
    monkeypatch.setattr(
        qual_module,
        "build_outcome_cohort_summary",
        lambda **_: {
            "raw_event_count": len(outcomes),
            "eligible_outcome_count": len(outcomes),
            "excluded_outcome_count": 0,
            "outcome_exclusion_reasons": {},
        },
    )

    reg_file = tmp_path / "registration.json"
    reg_file.write_text(json.dumps(_REGISTRATION), encoding="utf-8")

    result = runner.invoke(
        inspect_app,
        [
            "profit-loop-qualification",
            "--registration", str(reg_file),
            "--cutoff", _CUTOFF,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["contract_version"] == QUALIFICATION_CONTRACT_VERSION
    assert data["verdict"] in {
        "minimum_profitable_loop_proven",
        "minimum_profitable_loop_rejected",
        "insufficient_data",
    }


def test_identity_filter_prevents_epoch_pooling(monkeypatch):
    """Two profitable epochs in the same window: only the registered one contributes."""
    # epoch-A: highly profitable but NOT the registered runtime session
    epoch_a = [
        {
            "realized_pnl": 10.0,
            "opened_at": "2026-01-02T00:00:00+00:00",
            "closed_at": f"2026-01-{2 + i:02d}T12:00:00+00:00",
            "execution_mode": "paper_internal",
            "runtime_session_id": "session-other",
        }
        for i in range(6)
    ]
    # epoch-B: unprofitable, IS the registered session (session-001)
    epoch_b = [
        {
            "realized_pnl": -3.0,
            "opened_at": "2026-01-02T00:00:00+00:00",
            "closed_at": f"2026-01-{10 + i:02d}T12:00:00+00:00",
            "execution_mode": "paper_internal",
            "runtime_session_id": "session-001",
        }
        for i in range(4)
    ]

    def _mock_eligible(*, runtime_session_id=None, **_):
        pool = epoch_a + epoch_b
        if runtime_session_id is not None:
            pool = [r for r in pool if r.get("runtime_session_id") == runtime_session_id]
        return pool

    monkeypatch.setattr(qual_module, "eligible_strategy_outcomes", _mock_eligible)
    monkeypatch.setattr(
        qual_module,
        "build_outcome_cohort_summary",
        lambda **kw: {
            "raw_event_count": len(_mock_eligible(**kw)),
            "eligible_outcome_count": len(_mock_eligible(**kw)),
            "excluded_outcome_count": 0,
            "outcome_exclusion_reasons": {},
        },
    )

    result = compile_qualification_verdict(registration=_REGISTRATION, cutoff_at=_CUTOFF)
    # Without identity filter the pooled epochs would net 60 - 12 = +48 (proven).
    # With identity filter only epoch-B contributes: net = -12 → rejected.
    assert result["verdict"] == "minimum_profitable_loop_rejected"
    assert result["metrics"]["total_eligible_closes"] == 4
    assert result["metrics"]["net_realized_pnl"] < 0
    assert result["funnel"]["identity_mismatched_count"] == 6


def test_missing_runtime_session_id_raises():
    reg = {k: v for k, v in _REGISTRATION.items() if k != "runtime_session_id"}
    import pytest
    with pytest.raises(ValueError, match="runtime_session_id"):
        compile_qualification_verdict(registration=reg, cutoff_at=_CUTOFF)
