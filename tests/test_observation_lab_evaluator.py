"""Focused proof that Lab evaluates canonical exposures without legacy writes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from aletheia.universe.outcome_economics import COST_MODEL_VERSION


def _outcome(db, candidate: str, *, net: float) -> None:
    entry = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    due = entry + timedelta(hours=4)
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "allocation_selected, entry_price_usd, entry_ts, due_at, settled_at, "
        "exit_price_usd, directional_return_pct, net_return_pct, min_tradable_edge_pct, "
        "estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, mae_pct, "
        "settlement_price_source, settlement_lag_sec, cost_model_version, status) "
        "VALUES (?, ?, 'test', 'BTC/USDT', 'perp', 'long', '4h', 1, 100, ?, ?, ?, "
        "?, ?, ?, 0.05, 0.1, 0.1, 0.0, -0.1, 'horizon_ohlcv', 0.0, '"
        f"{COST_MODEL_VERSION}', 'settled')",
        (
            f"outcome-{candidate}", candidate, entry.isoformat(), due.isoformat(),
            (due + timedelta(minutes=1)).isoformat(), 100.0 * (1.0 + (net + 0.2) / 100.0),
            net + 0.2, net,
        ),
    )


def test_lab_evaluator_records_immutable_result_from_lab_exposures():
    from aletheia.observation_lab.canonical import (
        consume_inference_plan,
        freeze_inference_plan,
        register_inference_plan,
        register_observation_spec,
    )
    from aletheia.observation_lab.db import get_lab_db, reset_lab_db
    from aletheia.observation_lab.evaluator import evaluate_observation
    from aletheia.storage.sqlite import get_db

    reset_lab_db()
    spec = {
        "observation_id": "lab-evaluator-test",
        "path_id": "universe_family_calibration",
        "filter_name": "test-filter",
        "stage": "shadow",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "4h",
        "control": {"value": 1.0},
        "treatment": {"value": 2.0},
        "behavior_policy_epoch_ref": "epoch-lab",
        "gates": {"min_samples_per_arm": 1, "min_market_days": 0},
    }
    root = register_observation_spec(spec)
    lab = get_lab_db()
    contract = lab.execute(
        "SELECT * FROM observation_generation_contracts WHERE observation_id=? AND generation=1",
        (root["observation_id"],),
    ).fetchone()
    _outcome(get_db(), "control-key", net=0.0)
    _outcome(get_db(), "treatment-key", net=1.0)
    common = {
        "observation_id": root["observation_id"],
        "generation": 1,
        "manifest_id": None,
        "manifest_hash": None,
        "assignment_unit_key": None,
        "candidate_key": None,
        "applied": 1,
        "behavior_changed": 0,
        "baseline_payload_hash": "baseline",
        "applied_payload_hash": "baseline",
        "baseline_payload_json": json.dumps({"value": 1.0}),
        "applied_payload_json": json.dumps({"value": 1.0}),
        "outcome_source": "fixed_horizon_candidate",
        "resolution_reason": "observe_only:treatment",
        "behavior_contract_hash": contract["behavior_contract_hash"],
        "behavior_policy_epoch_ref": "epoch-lab",
        "active_manifest_snapshot_json": "[]",
        "manifest_set_fingerprint": None,
        "revocation_snapshot_fingerprint": None,
        "authority_source": "lab",
        "authority_epoch": "lab:test",
        "runtime_session_id": "session:test",
        "producer_commit": "commit:test",
        "exposed_at": "2026-08-01T12:00:00+00:00",
        "imported_at": "2026-08-01T12:01:00+00:00",
    }
    for receipt, assignment, candidate in (
        ("receipt-control", "control", "control-key"),
        ("receipt-treatment", "treatment", "treatment-key"),
    ):
        row = dict(common)
        row.update({
            "exposure_id": receipt,
            "source_receipt_id": receipt,
            "receipt_sequence": 1 if assignment == "control" else 2,
            "assignment": assignment,
            "assignment_unit_key": candidate,
            "candidate_key": candidate,
            "outcome_candidate_key": candidate,
        })
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        lab.execute(
            f"INSERT INTO observation_exposures ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )

    result = evaluate_observation(root["observation_id"], 1)
    assert result["available"] is True
    assert result["verdict"] == "insufficient_data"
    assert result["evaluations"][0]["reason"] == "no_actual_intervention"
    repeated = evaluate_observation(root["observation_id"], 1)
    assert repeated["evaluations"][0]["evaluation_id"] == result["evaluations"][0]["evaluation_id"]
    assert evaluate_observation(
        root["observation_id"], 1, evaluation_kind="confirmatory",
    )["reason"] == "inference_plan_not_frozen"
    plan_id = register_inference_plan(
        observation_id=root["observation_id"],
        generation=1,
        inference_contract_version="observation-fixed-look/v1",
        plan={"authority_alpha": 0.025},
    )
    freeze_inference_plan(
        plan_id,
        cutoff={"assignment_unit_keys": ["control-key", "treatment-key"]},
    )
    confirmatory = evaluate_observation(
        root["observation_id"], 1, evaluation_kind="confirmatory",
    )
    assert confirmatory["available"] is True
    assert confirmatory["evaluations"][0]["harm_metrics"]["inference_role"] == "confirmatory"
    consume_inference_plan(
        plan_id, evaluation_id=confirmatory["evaluations"][0]["evaluation_id"],
    )
    consumed = evaluate_observation(
        root["observation_id"], 1, evaluation_kind="confirmatory",
    )
    assert consumed["evaluations"][0]["evaluation_id"] == confirmatory["evaluations"][0]["evaluation_id"]
    assert lab.execute(
        "SELECT COUNT(*) FROM observation_evaluations WHERE observation_id=? AND generation=1",
        (root["observation_id"],),
    ).fetchone()[0] == 2
    # Lab evaluation must not materialize a legacy Experiment/Hypothesis row.
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 0


def test_canonical_runner_keeps_window_active_when_evaluation_is_unavailable():
    from aletheia.observation_lab.canonical import register_observation_spec
    from aletheia.observation_lab.db import get_lab_db, reset_lab_db
    from aletheia.observation_lab.kernel import advance_windows
    from aletheia.observation_lab.runner import register_builtin_handlers

    reset_lab_db()
    register_builtin_handlers()
    root = register_observation_spec(
        {
            "observation_id": "lab-evaluator-unavailable",
            "path_id": "universe_family_calibration",
            "filter_name": "test-filter",
            "stage": "shadow",
            "scanner_family": "momentum",
            "direction": "long",
            "market_type": "perp",
            "horizon": "4h",
            "control": {"value": 1.0},
            "treatment": {"value": 2.0},
        },
        started_at="2026-08-01T00:00:00+00:00",
        collect_until="2026-08-02T00:00:00+00:00",
        finalize_not_before="2026-08-02T00:00:00+00:00",
    )

    advanced = advance_windows(
        now="2026-08-03T00:00:00+00:00",
    )

    result = advanced["results"][f"{root['observation_id']}:1"]
    assert result["status"] == "advanced"
    summary = result["summary"]
    assert summary["evaluation"]["available"] is False
    assert get_lab_db().execute(
        "SELECT status FROM observation_windows WHERE observation_id=? AND generation=1",
        (root["observation_id"],),
    ).fetchone()[0] == "active"
