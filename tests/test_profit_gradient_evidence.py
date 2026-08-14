from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from typer.testing import CliRunner

import aletheia.analytics.profit_gradient_evidence as evidence
from aletheia.cli.inspect_cmd import inspect_app


runner = CliRunner()


def test_compiler_emits_versioned_read_only_artifact_and_stable_hashes():
    first = evidence.build_profit_gradient_evidence(
        evidence_epoch_id="missing-epoch",
        cutoff_at="2026-08-14T00:00:00Z",
        cost_model_id="missing-cost-model",
    )
    second = evidence.build_profit_gradient_evidence(
        evidence_epoch_id="missing-epoch",
        cutoff_at="2026-08-14T00:00:00+00:00",
        cost_model_id="missing-cost-model",
    )

    assert first == second
    assert first["contract_version"] == "profit_gradient_evidence/v1"
    assert first["read_only"] is True
    assert first["behavior_changed"] is False
    assert set(first["layer_samples"]) == set(evidence.LAYERS)
    assert first["registration"]["status"] == "unavailable"
    assert first["content_hash"]
    assert len(first["content_hash"]) == 64
    assert first["source_hashes"]
    assert first["exclusion_waterfall"]["order"]


def test_compiler_does_not_write_database():
    from aletheia.storage.sqlite import get_db

    db = get_db()
    before = db.total_changes

    report = evidence.build_profit_gradient_evidence(
        evidence_epoch_id="missing-epoch",
        cutoff_at="2026-08-14T00:00:00+00:00",
        cost_model_id="missing-cost-model",
    )

    assert db.total_changes == before
    assert report["read_only"] is True


def test_cost_model_source_does_not_admit_future_settlement():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE filter_candidate_outcomes ("
        "id TEXT, cost_model_version TEXT, entry_ts TEXT, settled_at TEXT, status TEXT)"
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes VALUES (?, ?, ?, ?, ?)",
        ("future", "v1", "2026-08-14T00:00:00+00:00", "2026-08-15T00:00:00+00:00", "settled"),
    )

    resolved = evidence._resolve_cost_model(
        db,
        "v1",
        datetime(2026, 8, 14, tzinfo=timezone.utc),
    )

    assert resolved["status"] == "unavailable"
    assert resolved["reason"] == "cost_model_not_recorded"


def test_incomplete_source_cannot_emit_numeric_coverage():
    cutoff = datetime(2026, 8, 14, tzinfo=timezone.utc)
    context = evidence._coverage_context(
        registration={},
        candidate_facts=[
            {
                "event_at": "2026-08-13T00:00:00+00:00",
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "settled_at": "2026-08-14T00:00:00+00:00",
                    "cost_model_version": "v1",
                    "horizon": "24h",
                    "net_return_pct": 1.0,
                },
            }
        ],
        position_facts=[],
        epoch_start=datetime(2026, 8, 12, tzinfo=timezone.utc),
        cutoff=cutoff,
        candidate_source={"status": "available", "unavailable_count": 1},
        position_source={"status": "available", "error_count": 1},
        cost_source={"status": "available"},
        evidence_epoch_id="epoch-1",
        cost_model_id="v1",
    )

    assert context["candidate_outcome_coverage"] is None
    assert context["position_causal_link_completeness"] is None


def test_pairing_rejects_unpaired_units_and_emits_one_contrast_per_arm():
    paired, excluded = evidence._pair_confirmatory_samples(
        [
            {
                "unit_id": "treatment-unit",
                "pair_id": "pair-1",
                "arm": "treatment",
                "notional_quote": 200.0,
                "arm_net_delta_bp": 120.0,
                "arm_net_delta_quote": 2.4,
            },
            {
                "unit_id": "control-unit",
                "pair_id": "pair-1",
                "arm": "control",
                "notional_quote": 200.0,
                "arm_net_delta_bp": 20.0,
                "arm_net_delta_quote": 0.4,
            },
            {
                "unit_id": "unpaired",
                "pair_id": None,
                "arm": "treatment",
                "notional_quote": 200.0,
                "arm_net_delta_bp": 10.0,
                "arm_net_delta_quote": 0.2,
            },
        ]
    )

    assert excluded == [("unpaired", "paired_evidence_unavailable")]
    assert len(paired) == 1
    assert paired[0]["arm"] == "paired"
    assert paired[0]["arm_counts"] == {"treatment": 1, "control": 1}
    assert paired[0]["paired_net_delta_bp"] == 100.0
    assert paired[0]["paired_net_delta_quote"] == 2.0


def test_candidate_policy_value_requires_registered_assignment_and_uses_fixed_horizon_net_once():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE trade_proposals (id TEXT PRIMARY KEY, quantity REAL, price REAL);
        CREATE TABLE learning_exposures (
            exposure_id TEXT PRIMARY KEY, assignment TEXT, assignment_unit_key TEXT,
            concurrent_policy_epoch TEXT, candidate_key TEXT, exposed_at TEXT
        );
        """
    )
    db.execute("INSERT INTO trade_proposals VALUES ('proposal-1', 2, 100)")
    db.execute(
        "INSERT INTO learning_exposures VALUES (?, ?, ?, ?, ?, ?)",
        ("exposure-1", "treatment", "cluster-1", "epoch-1", "candidate-1", "2026-08-13T00:00:00+00:00"),
    )
    fact = {
        "fact_id": "candidate-1",
        "event_at": "2026-08-13T00:00:00+00:00",
        "identity": {"proposal_id": "proposal-1"},
        "terminal_state": "risk_reject",
        "fixed_horizon_outcome": {
            "status": "settled",
            "direction": "long",
            "horizon": "24h",
            "outcome_candidate_key": "candidate-1",
            "entry_ts": "2026-08-13T00:00:00+00:00",
            "settled_at": "2026-08-14T00:00:00+00:00",
            "net_return_pct": -1.0,
            "cost_model_version": "v1",
        },
    }

    sample, reason = evidence._candidate_sample(
        db,
        fact,
        layer="Risk",
        cutoff=datetime(2026, 8, 14, tzinfo=timezone.utc),
        epoch_start=datetime(2026, 8, 12, tzinfo=timezone.utc),
        cost_model_id="v1",
        epoch_id="epoch-1",
    )

    assert reason is None
    assert sample["paired_net_delta_bp"] == -100.0
    assert sample["paired_net_delta_quote"] == -2.0
    assert sample["cost_applied_once"] is True


def test_compiled_artifact_rejects_empty_provenance_even_with_rehashed_content():
    payload = evidence.build_profit_gradient_evidence(
        evidence_epoch_id="missing-epoch",
        cutoff_at="2026-08-14T00:00:00+00:00",
        cost_model_id="missing-cost-model",
    )
    payload = dict(payload)
    payload["registration"] = {}
    payload["source_status"] = {}
    payload["content_hash"] = evidence._hash({key: value for key, value in payload.items() if key != "content_hash"})

    verified, reason = evidence.verify_profit_gradient_evidence(payload)

    assert verified is False
    assert reason == "evidence_registration_invalid"


def test_compiled_artifact_hash_rejects_tampering():
    report = evidence.build_profit_gradient_evidence(
        evidence_epoch_id="missing-epoch",
        cutoff_at="2026-08-14T00:00:00+00:00",
        cost_model_id="missing-cost-model",
    )
    tampered = dict(report)
    tampered["evidence_context"] = {"candidate_count": 999}

    verified, reason = evidence.verify_profit_gradient_evidence(tampered)

    assert verified is False
    assert reason == "evidence_content_hash_mismatch"


def test_profit_gradient_evidence_cli_writes_compiler_artifact(tmp_path, monkeypatch):
    output = tmp_path / "profit-gradient-evidence.json"
    payload = {
        "contract_version": "profit_gradient_evidence/v1",
        "read_only": True,
        "behavior_changed": False,
        "producer": {
            "module": "aletheia.analytics.profit_gradient_evidence",
            "contract_version": "profit_gradient_evidence/v1",
            "source_mode": "database_read_only",
            "manual_layer_samples": False,
        },
        "registration": {"status": "unavailable", "reason": "test"},
        "evidence_context": {},
        "layer_samples": {layer: [] for layer in evidence.LAYERS},
        "exclusion_waterfall": {},
        "source_hashes": {
            "evidence_epoch": "a" * 64,
            "candidate_facts": "b" * 64,
            "position_replays": "c" * 64,
            "cost_model": "d" * 64,
            "counterfactual_registry": "e" * 64,
        },
        "source_status": {
            "evidence_epoch": {"status": "unavailable", "reason": "test"},
            "candidate_facts": {"status": "unavailable", "reason": "test"},
            "position_replays": {"status": "unavailable", "reason": "test"},
            "cost_model": {"status": "unavailable", "reason": "test"},
            "counterfactual": {"status": "available", "evidence_class": "exploratory"},
        },
        "exploratory_evidence": {"profit_loop_counterfactual": {"promotion_eligible": False}},
        "inputs": {
            "evidence_epoch_id": "epoch-1",
            "cutoff_at": "2026-08-14T00:00:00Z",
            "cost_model_id": "cost-v1",
        },
    }
    payload["content_hash"] = evidence._hash(payload)
    monkeypatch.setattr(
        evidence,
        "build_profit_gradient_evidence",
        lambda **kwargs: dict(payload),
    )

    result = runner.invoke(
        inspect_app,
        [
            "profit-gradient-evidence",
            "--epoch",
            "epoch-1",
            "--cutoff",
            "2026-08-14T00:00:00Z",
            "--cost-model-id",
            "cost-v1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.stdout
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["contract_version"] == "profit_gradient_evidence/v1"
    assert written["inputs"] == {
        "evidence_epoch_id": "epoch-1",
        "cutoff_at": "2026-08-14T00:00:00Z",
        "cost_model_id": "cost-v1",
    }
    assert "wrote" in result.stdout
