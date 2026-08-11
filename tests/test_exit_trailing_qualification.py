from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aletheia.analytics.exit_trailing_qualification import (
    build_exit_trailing_qualification_report,
    materialize_exit_trailing_recovery,
)
from aletheia.portfolio.exit_plan import load_exit_plan
from aletheia.portfolio.position_manager import _resolve_trailing
from aletheia.portfolio.protective_barrier import (
    _trailing_qualification_assignment,
    activate_new_paper_position,
    persist_position_exit_plan,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import Position
from tests.test_position_manager import setup_env


def _position_id_for(arm: str) -> str:
    for index in range(1000):
        position_id = f"qualification-{arm}-{index}"
        if _trailing_qualification_assignment(position_id)["arm"] == arm:
            return position_id
    raise AssertionError(f"no deterministic {arm} assignment found")


def _activate(arm: str, *, symbol: str = "BTC/USDT") -> str:
    position_id = _position_id_for(arm)
    repo.insert_position(Position(
        id=position_id,
        symbol=symbol,
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        realized_pnl=0.0,
        execution_mode="paper_internal",
        stop_price=98.0,
        take_profit_price=110.0,
        entry_fee=0.06,
        funding_cost=0.0,
        funding_source="not_applicable",
        source="native",
        trade_school="short_term",
        producer_commit="qualification-sha",
        behavior_fingerprint="behavior-v1",
        runtime_session_id="session-open",
    ))
    assert activate_new_paper_position(position_id)["activated"] is True
    return position_id


def _close(
    position_id: str,
    realized_pnl: float | None,
    *,
    eligible: bool,
    closed_at: str | None = None,
) -> None:
    repo.update_position(
        position_id,
        status="closed",
        closed_at=closed_at or datetime.now(timezone.utc).isoformat(),
        realized_pnl=realized_pnl,
        outcome_eligible=eligible,
        outcome_exclusion_reason=("eligible" if eligible else "funding_unavailable"),
        closed_runtime_session_id="session-close",
        closed_producer_commit="qualification-sha",
        closed_behavior_fingerprint="behavior-v1",
    )


def test_new_positions_keep_a_deterministic_static_control_holdout(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.trail_enabled = True
    save_config(cfg)

    treatment_id = _activate("treatment")
    control_id = _activate("control")

    treatment = json.loads(repo.get_position(treatment_id)["trailing_json"])
    control = json.loads(repo.get_position(control_id)["trailing_json"])
    assert treatment["qualification"]["arm"] == "treatment"
    assert treatment["mode"] == "atr"
    assert treatment["enabled"] is True
    assert control["qualification"]["arm"] == "control"
    assert control["mode"] == "off"
    assert control["enabled"] is False
    assert control["cost_model_version"] == treatment["cost_model_version"]
    assert control["exit_fee_rate"] == treatment["exit_fee_rate"]
    assert control["slippage_bps"] == treatment["slippage_bps"]
    assert treatment["qualification"]["recovery_window_bars"] == 30
    assert treatment["qualification"]["recovery_threshold_pct"] == 1.0
    assert _resolve_trailing(repo.get_position(control_id), load_config()) is None


def test_report_keeps_exact_build_cohort_and_cost_complete_arm_economics(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.trail_enabled = True
    save_config(cfg)
    treatment_id = _activate("treatment", symbol="BTCUSDT")
    control_id = _activate("control")

    treatment_position = repo.get_position(treatment_id)
    treatment_plan = load_exit_plan(treatment_position)
    treatment_plan.stop_price = 101.0
    persist_position_exit_plan(
        treatment_id,
        treatment_plan,
        provenance="deterministic_trailing_v2",
    )
    observed_rows = []

    def available_recovery(row, **_kwargs):
        observed_rows.append(row)
        closed_ms = int(
            datetime.fromisoformat(row["closed_at"]).timestamp() * 1000
        )
        start_ms = ((closed_ms + 59_999) // 60_000) * 60_000
        return {
            "status": "available",
            "reason": "complete_post_exit_1m_bars",
            "bars_observed": 30,
            "window_start_ts_ms": start_ms,
            "window_end_ts_ms": start_ms + 1_800_000,
            "mfe_pct": 1.2,
            "mae_pct": -0.4,
        }

    monkeypatch.setattr(
        "aletheia.analytics.exit_trailing_qualification.build_post_exit_path_observation",
        available_recovery,
    )
    closed_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _close(treatment_id, 2.0, eligible=True, closed_at=closed_at)
    _close(control_id, -1.0, eligible=True, closed_at=closed_at)

    from aletheia.portfolio import outcome_hooks

    jobs = outcome_hooks.enqueue_outcome_hook_jobs(repo.get_position(treatment_id))
    recovery_job = next(
        job
        for job in jobs
        if job["hook_type"] == outcome_hooks.EXIT_TRAILING_RECOVERY_HOOK
    )
    assert datetime.fromisoformat(recovery_job["next_attempt_at"]) > (
        datetime.fromisoformat(closed_at) + timedelta(minutes=30)
    )
    monkeypatch.setattr(
        outcome_hooks,
        "_load_eligible_position",
        lambda _position_id: (repo.get_position(treatment_id), "eligible"),
    )
    delivery = outcome_hooks.process_outcome_hook_jobs(
        position_id=treatment_id,
        hook_type=outcome_hooks.EXIT_TRAILING_RECOVERY_HOOK,
    )
    assert delivery["completed"] == 1
    stored = json.loads(
        repo.get_position_protection_state(treatment_id)["recovery_observation_json"]
    )
    assert stored["status"] == "available"
    assert observed_rows[0]["symbol"] == "BTC/USDT"

    monkeypatch.setattr(
        "aletheia.analytics.exit_trailing_qualification.build_post_exit_path_observation",
        lambda *_args, **_kwargs: {
            "status": "unavailable",
            "reason": "post_exit_coverage_unavailable",
            "mfe_pct": None,
        },
    )

    report = build_exit_trailing_qualification_report()

    assert report["assignment_count"] == 2
    assert report["cohort_count"] == 1
    cohort = report["cohorts"][0]
    assert cohort["code_commit"] == "qualification-sha"
    assert cohort["treatment"]["assignment_count"] == 1
    assert cohort["treatment"]["available_count"] == 1
    assert cohort["treatment"]["changed_count"] == 1
    assert cohort["treatment"]["recovery_available_count"] == 1
    assert cohort["treatment"]["recovery_truncation_count"] == 1
    assert cohort["treatment"]["recovery_truncation_rate"] == 1.0
    assert cohort["control"]["assignment_count"] == 1
    assert cohort["control"]["available_count"] == 1
    assert cohort["incremental_net_pnl_mean"] == 3.0
    assert cohort["incremental_net_r_mean"] == 1.5
    assert cohort["promotion_eligible"] is False
    assert report["recovery_contract"] == {
        "contract_version": "post-exit-recovery/v1",
        "observation": "complete_post_exit_1m_bars",
        "window_bars": 30,
        "threshold_pct": 1.0,
        "authority": "diagnostic_only",
    }
    assert report["recovery_available_count"] == 1
    assert report["recovery_truncation_count"] == 1
    assert report["recovery_truncation_rate"] is None
    assert report["recovery_truncation_reason"] == "cohort_scoped_only"

    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit

    projected = build_alpha_attribution_audit(days=30)[
        "exit_trailing_qualification"
    ]
    assert projected["measurement_status"] == "prospective_randomized_holdout"
    assert projected["assignment_count"] == 2

    retained = materialize_exit_trailing_recovery(repo.get_position(treatment_id))
    assert retained["materialized"] is False
    assert retained["status"] == "available"


def test_report_preserves_unavailable_outcomes_instead_of_zero(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.trail_enabled = True
    save_config(cfg)
    control_id = _activate("control")
    _close(control_id, None, eligible=False)

    report = build_exit_trailing_qualification_report()

    assert report["unavailable_count"] == 1
    cohort = report["cohorts"][0]
    assert cohort["control"]["available_count"] == 0
    assert cohort["control"]["net_pnl_mean"] is None
    assert cohort["incremental_net_pnl_mean"] is None
    assert report["promotion_eligible"] is False


def test_report_rejects_operator_plan_intervention_from_arm_economics(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.trail_enabled = True
    save_config(cfg)
    treatment_id = _activate("treatment")
    plan = load_exit_plan(repo.get_position(treatment_id))
    plan.expected_horizon_hours = 24.0
    persist_position_exit_plan(
        treatment_id,
        plan,
        provenance="system_operator_adjustment_v1",
    )
    _close(treatment_id, 1.0, eligible=True)

    report = build_exit_trailing_qualification_report()

    cohort = report["cohorts"][0]
    assert cohort["treatment"]["available_count"] == 0
    assert cohort["treatment"]["unavailable_reasons"] == {
        "exit_plan_intervention": 1,
    }
    assert cohort["treatment"]["net_pnl_mean"] is None
