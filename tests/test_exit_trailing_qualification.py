"""Archived trailing evidence stays readable without creating new observations."""

import hashlib
import json

import pytest

from aletheia.analytics.exit_trailing_qualification import build_exit_trailing_qualification_report
from aletheia.storage import repositories as repo
from aletheia.storage.models import Position
from tests.position_fixture import force_update_position


OPENED = "2026-08-08T00:00:00+00:00"
CLOSED = "2026-08-08T01:00:00+00:00"


def _archived_position(arm, pnl, *, changed=False, recovery=False, provenance="deterministic_trailing_v2"):
    position_id, digest = {
        "treatment": ("qualification-treatment-0", "a8910def5c41ba11cd6af185b3d841346bff5e70d11751edb705f557f2827d8a"),
        "control": ("qualification-control-2", "62515f2af61d1c16be56e01bb6c3e67744f24bd105ac6eebf61956a7f030b3fb"),
    }[arm]
    repo._insert("positions", Position(
        id=position_id, symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1, entry_price=100, current_price=102, execution_mode="paper_internal",
        status="open", opened_at=OPENED, closed_at=CLOSED, realized_pnl=pnl,
        outcome_eligible=pnl is not None,
        outcome_exclusion_reason="eligible" if pnl is not None else "funding_unavailable",
        producer_commit="archived-sha", closed_producer_commit="archived-sha",
        behavior_fingerprint="archived-behavior", closed_behavior_fingerprint="archived-behavior",
    ).model_dump())
    assignment = {
        "policy_version": "paper-dynamic-profit-protection/v2",
        "treatment_id": "atr1.5-balance1.5-completed1m/v1",
        "assignment_unit": position_id, "assignment_hash": digest, "arm": arm,
        "treatment_allocation_bps": 9000, "control_holdout_bps": 1000,
        "recovery_window_bars": 30, "recovery_threshold_pct": 1.0,
    }
    payload = json.dumps({
        "stop_price": 98,
        "trailing": {
            "qualification": assignment, "cost_model_version": "paper-net-balance-v1",
            "exit_fee_rate": 0.001, "slippage_bps": 25,
            "mode": "atr" if arm == "treatment" else "off", "enabled": arm == "treatment",
        },
    })
    repo._insert("position_exit_plan_versions", {
        "position_id": position_id, "version": 1, "effective_at": OPENED,
        "stop_price": 98, "plan_json": payload, "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
        "provenance": "paper_entry_protection_v2", "created_at": OPENED,
    })
    if changed:
        repo._insert("position_exit_plan_versions", {
            "position_id": position_id, "version": 2, "effective_at": CLOSED,
            "stop_price": 98, "plan_json": payload, "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
            "provenance": provenance, "created_at": CLOSED,
        })
    if recovery:
        observation = {
            "contract_version": "post-exit-recovery/v1", "policy_version": assignment["policy_version"],
            "position_id": position_id, "venue": "bitget", "symbol": "BTC/USDT",
            "market_type": "spot", "timeframe": "1m", "direction": "long",
            "exit_price": 102, "closed_at": CLOSED, "bars_requested": 30,
            "recovery_threshold_pct": 1.0, "status": "available", "mfe_pct": 1.2,
        }
        encoded = json.dumps(observation, sort_keys=True, separators=(",", ":"))
        repo._insert("position_protection_state", {
            "position_id": position_id, "venue": "bitget", "symbol": "BTC/USDT",
            "market_type": "spot", "activated_at": OPENED, "next_bar_open_ms": 0,
            "created_at": OPENED, "updated_at": CLOSED,
            "recovery_observation_json": encoded,
            "recovery_observation_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        })

    force_update_position(position_id, status="closed")


def test_archived_report_preserves_cohort_economics_and_recorded_recovery():
    _archived_position("treatment", 2, changed=True, recovery=True)
    _archived_position("control", -1)
    before = repo.get_db().total_changes

    report = build_exit_trailing_qualification_report()

    assert repo.get_db().total_changes == before
    assert report["enrollment_enabled"] is False
    assert report["enrollment_block_reason"] == "archived"
    assert report["assignment_count"] == 2
    assert report["cohort_count"] == 1
    cohort = report["cohorts"][0]
    assert cohort["code_commit"] == "archived-sha"
    assert cohort["incremental_net_pnl_mean"] == 3
    assert cohort["incremental_net_r_mean"] == 1.5
    assert cohort["treatment"]["recovery_available_count"] == 1
    assert cohort["treatment"]["recovery_truncation_rate"] == 1.0
    assert report["promotion_eligible"] is False


def test_missing_archived_recovery_is_not_recomputed():
    _archived_position("treatment", 2, changed=True)
    before = repo.get_db().total_changes
    report = build_exit_trailing_qualification_report()
    assert repo.get_db().total_changes == before
    treatment = report["cohorts"][0]["treatment"]
    assert treatment["recovery_available_count"] == 0
    assert treatment["recovery_unavailable_reasons"] == {"archived_recovery_not_recorded": 1}


@pytest.mark.parametrize("pnl,provenance,reason", [
    (None, "deterministic_trailing_v2", "funding_unavailable"),
    (1, "system_operator_adjustment_v1", "exit_plan_intervention"),
])
def test_archived_report_keeps_unavailable_economics_unavailable(pnl, provenance, reason):
    _archived_position("treatment", pnl, changed=True, provenance=provenance)
    treatment = build_exit_trailing_qualification_report()["cohorts"][0]["treatment"]
    assert treatment["available_count"] == 0
    assert treatment["net_pnl_mean"] is None
    assert treatment["unavailable_reasons"] == {reason: 1}
