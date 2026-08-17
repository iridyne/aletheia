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
    assert first["contract_version"] == evidence.PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION
    assert first["read_only"] is True
    assert first["behavior_changed"] is False
    assert set(first["layer_samples"]) == set(evidence.LAYERS)
    assert first["registration"]["status"] == "unavailable"
    assert first["content_hash"]
    assert len(first["content_hash"]) == 64
    assert first["source_hashes"]
    assert first["exclusion_waterfall"]["order"]
    assert evidence.verify_profit_gradient_evidence(first, revalidate_sources=True) == (True, None)


def test_historical_v1_artifact_keeps_hash_verification_without_new_selector_shape():
    payload = evidence.build_profit_gradient_evidence(
        evidence_epoch_id="missing-epoch",
        cutoff_at="2026-08-14T00:00:00Z",
        cost_model_id="missing-cost-model",
    )
    payload = dict(payload)
    payload["contract_version"] = evidence.LEGACY_PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION
    payload["producer"] = dict(evidence._LEGACY_COMPILER_PRODUCER)
    payload.pop("reconciliation")
    payload["evidence_context"] = {}
    payload["content_hash"] = evidence._hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )

    assert evidence.verify_profit_gradient_evidence(payload, revalidate_sources=True) == (True, None)
    assert payload["contract_version"] != evidence.PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION


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


def test_position_replay_cost_model_identity_is_explicit_and_resolvable():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    cost_model_id = "paper-execution-reference/v1"

    assert evidence._fact_cost_model_id(
        {"cost_identity": {"entry_fee_sources": [], "exit_fee_sources": [], "funding_source": None}}
    ) is None
    assert evidence._fact_cost_model_id(
        {"cost_identity": {"cost_model_id": cost_model_id}}
    ) == cost_model_id

    resolved = evidence._resolve_cost_model(
        db,
        cost_model_id,
        datetime(2026, 8, 14, tzinfo=timezone.utc),
        position_facts=[
            {
                "fact_id": "position-1",
                "availability": "available",
                "cost_identity": {"cost_model_id": cost_model_id},
            }
        ],
    )

    assert resolved["status"] == "available"
    assert resolved["source_tables"] == ["position_replays"]


def test_candidate_source_hash_includes_exclusion_identity(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    reports = iter(
        [
            {"facts": [], "unavailable_count": 1, "unavailable_reasons": {"bad": 1}, "exclusions": [{"id": "a"}]},
            {"facts": [], "unavailable_count": 1, "unavailable_reasons": {"bad": 1}, "exclusions": [{"id": "b"}]},
        ]
    )
    monkeypatch.setattr(evidence, "build_candidate_facts", lambda **_: next(reports))

    first, _ = evidence._load_candidates(db, datetime(2026, 8, 14, tzinfo=timezone.utc))
    second, _ = evidence._load_candidates(db, datetime(2026, 8, 14, tzinfo=timezone.utc))

    assert first["source_hash"] != second["source_hash"]


def _execution_fact(*, residual_within_tolerance: bool = True) -> dict:
    return {
        "fact_id": "position-1",
        "economics": {
            "availability": "available",
            "residual_within_tolerance": residual_within_tolerance,
            "entry_fee": 0.0,
            "exit_fee": 0.0,
            "funding_cost": 0.0,
            "gross_pnl": 10.0,
            "net_pnl": 9.0,
        },
        "stages": {
            "execution": {"entry_order": {"reference_price": 100.0}, "entry_fills": [{}]},
            "exit": {
                "position": {
                    "id": "position-1",
                    "opened_at": "2026-08-13T00:00:00+00:00",
                    "closed_at": "2026-08-13T00:01:00+00:00",
                    "quantity": 1.0,
                    "entry_price": 100.0,
                    "direction": "long",
                },
                "close_orders": [{"reference_price": 110.0}],
                "close_fills": [{}],
            },
        },
    }


def test_execution_sample_rejects_accounting_mismatch():
    sample, reason = evidence._position_execution_sample(
        sqlite3.connect(":memory:"),
        _execution_fact(residual_within_tolerance=False),
        cutoff=datetime(2026, 8, 14, tzinfo=timezone.utc),
        epoch_start=datetime(2026, 8, 12, tzinfo=timezone.utc),
        cost_model_id="paper-execution-reference/v1",
        epoch_id="epoch-1",
    )

    assert sample is None
    assert reason == "accounting_mismatch"


def test_position_assignment_precedes_entry_and_execution_chain_is_validated(monkeypatch):
    observed = []
    monkeypatch.setattr(evidence, "_paired_position_cost", lambda *_: ({}, None))

    def assign(_db, _fact, _cutoff, _epoch_start, _epoch_id, *, observed_at=None, **_kwargs):
        observed.append(observed_at)
        return "treatment", "cluster-1", "pair-1", "policy-1", "commit-1", None

    monkeypatch.setattr(evidence, "_position_assignment", assign)
    fact = _execution_fact()
    cutoff = datetime(2026, 8, 14, tzinfo=timezone.utc)
    epoch_start = datetime(2026, 8, 12, tzinfo=timezone.utc)

    cost_sample, cost_reason = evidence._position_cost_sample(
        sqlite3.connect(":memory:"),
        {**fact, "economics": {**fact["economics"], "gross_pnl": 10.0}},
        cutoff=cutoff,
        epoch_start=epoch_start,
        cost_model_id="paper-execution-reference/v1",
        epoch_id="epoch-1",
    )
    execution_sample, execution_reason = evidence._position_execution_sample(
        sqlite3.connect(":memory:"),
        fact,
        cutoff=cutoff,
        epoch_start=epoch_start,
        cost_model_id="paper-execution-reference/v1",
        epoch_id="epoch-1",
    )

    assert cost_sample is None
    assert cost_reason == "cost_not_actionable"
    assert execution_reason is None and execution_sample is not None
    assert observed == [datetime(2026, 8, 13, tzinfo=timezone.utc)]


def test_execution_delta_excludes_settled_fees_and_costs_need_an_alternative(monkeypatch):
    monkeypatch.setattr(evidence, "_paired_position_cost", lambda *_: ({}, None))
    monkeypatch.setattr(
        evidence,
        "_position_assignment",
        lambda *_args, **_kwargs: ("treatment", "cluster-1", "pair-1", "policy-1", "commit-1", None),
    )
    fact = _execution_fact()
    fact["economics"].update({"gross_pnl": 8.0, "net_pnl": 7.0, "entry_fee": 0.4, "exit_fee": 0.4, "funding_cost": 0.2})
    execution, reason = evidence._position_execution_sample(
        sqlite3.connect(":memory:"), fact,
        cutoff=datetime(2026, 8, 14, tzinfo=timezone.utc),
        epoch_start=datetime(2026, 8, 12, tzinfo=timezone.utc),
        cost_model_id="paper-execution-reference/v1", epoch_id="epoch-1",
    )
    cost, cost_reason = evidence._position_cost_sample(
        sqlite3.connect(":memory:"), fact,
        cutoff=datetime(2026, 8, 14, tzinfo=timezone.utc),
        epoch_start=datetime(2026, 8, 12, tzinfo=timezone.utc),
        cost_model_id="paper-execution-reference/v1", epoch_id="epoch-1",
    )

    assert reason is None and execution is not None
    assert execution["paired_net_delta_quote"] == 2.0
    assert execution["cost_applied_once"] is False
    assert execution["execution_evidence"]["fees_excluded"] is True
    assert cost is None and cost_reason == "cost_not_actionable"


def test_execution_sample_rejects_invalid_fill_chain(monkeypatch):
    monkeypatch.setattr(evidence, "_paired_position_cost", lambda *_: (None, "execution_fill_mismatch"))

    sample, reason = evidence._position_execution_sample(
        sqlite3.connect(":memory:"),
        _execution_fact(),
        cutoff=datetime(2026, 8, 14, tzinfo=timezone.utc),
        epoch_start=datetime(2026, 8, 12, tzinfo=timezone.utc),
        cost_model_id="paper-execution-reference/v1",
        epoch_id="epoch-1",
    )

    assert sample is None
    assert reason == "incomplete_path"


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
        layer_samples={"Costs": [{"stage": "paper_10", "outcome_contract_version": "v2"}]},
    )

    assert context["candidate_outcome_coverage"] is None
    assert context["position_causal_link_completeness"] is None
    assert context["comparison_method"] == "paired_same_unit"
    assert context["horizons"] == ["24h"]
    assert context["outcome_contract_versions"] == ["v2"]
    assert context["metric_versions"] == [evidence.METRIC_VERSION]
    assert context["statistical_unit_versions"] == ["profit_loop_fact/v1"]
    assert context["stages"] == ["paper_10"]

    missing_outcome = evidence._coverage_context(
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
        layer_samples={"Costs": [{"stage": "paper_10"}]},
    )
    assert missing_outcome["outcome_contract_versions"] == []

    mixed_outcome = evidence._coverage_context(
        registration={},
        candidate_facts=[],
        position_facts=[],
        epoch_start=datetime(2026, 8, 12, tzinfo=timezone.utc),
        cutoff=cutoff,
        candidate_source={"status": "available", "unavailable_count": 0},
        position_source={"status": "available", "error_count": 0},
        cost_source={"status": "available"},
        evidence_epoch_id="epoch-1",
        cost_model_id="v1",
        layer_samples={
            "Costs": [
                {"stage": "paper_10", "outcome_contract_version": "v2"},
                {"stage": "paper_10"},
            ]
        },
    )
    assert mixed_outcome["outcome_contract_versions"] == []


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


def test_pairing_builds_cost_delta_from_both_arms_and_preserves_execution_metadata():
    treatment_cost = {
        "cost_event_id": "treatment-cost",
        "avoidable": True,
        "baseline_kind": "realizable_limit",
        "baseline_id": "treatment-baseline",
        "cost_model_id": "cost-v1",
        "avoidable_extra_cost_quote": 5.0,
    }
    control_cost = {
        **treatment_cost,
        "cost_event_id": "control-cost",
        "baseline_id": "control-baseline",
        "avoidable_extra_cost_quote": 2.0,
    }
    cost_pair, excluded = evidence._pair_confirmatory_samples(
        [
            {
                "unit_id": "treatment-cost-unit",
                "pair_id": "cost-pair",
                "arm": "treatment",
                "component": "avoidable_extra_cost",
                "notional_quote": 1000.0,
                "arm_net_delta_bp": 50.0,
                "arm_net_delta_quote": 5.0,
                "cost_evidence": treatment_cost,
                "cost_applied_once": False,
            },
            {
                "unit_id": "control-cost-unit",
                "pair_id": "cost-pair",
                "arm": "control",
                "component": "avoidable_extra_cost",
                "notional_quote": 1000.0,
                "arm_net_delta_bp": 20.0,
                "arm_net_delta_quote": 2.0,
                "cost_evidence": control_cost,
            },
        ]
    )
    execution_pair, execution_excluded = evidence._pair_confirmatory_samples(
        [
            {
                "unit_id": "treatment-execution-unit",
                "pair_id": "execution-pair",
                "arm": "treatment",
                "component": "fill_or_execution",
                "notional_quote": 1000.0,
                "arm_net_delta_bp": 20.0,
                "arm_net_delta_quote": 2.0,
                "cost_applied_once": False,
            },
            {
                "unit_id": "control-execution-unit",
                "pair_id": "execution-pair",
                "arm": "control",
                "component": "fill_or_execution",
                "notional_quote": 1000.0,
                "arm_net_delta_bp": 10.0,
                "arm_net_delta_quote": 1.0,
            },
        ]
    )

    assert excluded == []
    assert execution_excluded == []
    assert cost_pair[0]["cost_evidence"]["avoidable_extra_cost_quote"] == 3.0
    assert cost_pair[0]["cost_evidence"]["cost_event_id"] == "treatment-cost|control-cost"
    assert cost_pair[0]["cost_evidence"]["cost_event_ids"] == ["treatment-cost", "control-cost"]
    assert cost_pair[0]["cost_applied_once"] is False
    assert execution_pair[0]["cost_applied_once"] is False


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


def test_cli_rejects_rehashed_fabricated_sources(tmp_path):
    report = evidence.build_profit_gradient_evidence(
        evidence_epoch_id="missing-epoch",
        cutoff_at="2026-08-14T00:00:00+00:00",
        cost_model_id="missing-cost-model",
    )
    report = dict(report)
    report["evidence_context"] = {"candidate_count": 30}
    report["content_hash"] = evidence._hash({key: value for key, value in report.items() if key != "content_hash"})
    path = tmp_path / "fabricated.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    result = runner.invoke(inspect_app, ["profit-gradient", "--evidence", str(path), "--json"])

    assert result.exit_code != 0
    assert "evidence_gradient_contract_invalid" in result.output


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
    assert reason == "evidence_gradient_contract_invalid"


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
        "evidence_context": {
            "gradient_contract_version": "profit_gradient/v3",
            "evidence_integrity": "verified",
            "reconciliation_contract_version": evidence.RECONCILIATION_CONTRACT_VERSION,
        },
        "layer_samples": {layer: [] for layer in evidence.LAYERS},
        "reconciliation": {
            "contract_version": evidence.RECONCILIATION_CONTRACT_VERSION,
            "tolerance_quote": evidence.RECONCILIATION_TOLERANCE_QUOTE,
            "status": "requires_selector_evaluation",
        },
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
