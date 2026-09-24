from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

import aletheia.analytics.profit_gradient_evidence as evidence
from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ISO
from aletheia.cli.inspect_cmd import inspect_app

runner = CliRunner()

# All datetime fixtures in this file are derived from the clean-window anchor so
# they remain above the learning data floor after any re-anchor.  The two names
# below give the tests a stable relative "epoch" timeline:
#   _EPOCH_START — 2 days after anchor (stands in for the epoch opening)
#   _CUTOFF      — 14 days after anchor (stands in for the as-of cutoff)
_ANCHOR = datetime.fromisoformat(CLEAN_WINDOW_ANCHOR_ISO)
_CLEAN_ENTRY = (_ANCHOR + timedelta(minutes=1)).isoformat()
_CLEAN_DUE = (_ANCHOR + timedelta(hours=4)).isoformat()
_AS_OF_CLEAN = _ANCHOR + timedelta(hours=5)
_PRE_FLOOR_ENTRY = (_ANCHOR - timedelta(days=80)).isoformat()
_PRE_FLOOR_DUE = (_ANCHOR - timedelta(days=79)).isoformat()
# A naive (tz-less) variant and a post-cutoff variant, both kept on the same
# timeline so the properties under test survive an anchor bump.
_CLEAN_DUE_NAIVE = (_ANCHOR + timedelta(hours=4)).replace(tzinfo=None).isoformat()
_AFTER_CUTOFF = (_ANCHOR + timedelta(days=1)).isoformat()
# Fixed-timeline anchors for tests that are not about floor membership.
_EPOCH_START = _ANCHOR + timedelta(days=2)
_CUTOFF = _ANCHOR + timedelta(days=14)


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
        "id TEXT, cost_model_version TEXT, entry_ts TEXT, due_at TEXT, "
        "settled_at TEXT, status TEXT)"
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes VALUES (?, ?, ?, ?, ?, ?)",
        (
            "future",
            "v1",
            (_CUTOFF - timedelta(days=1)).isoformat(),
            _CUTOFF.isoformat(),
            (_CUTOFF + timedelta(days=1)).isoformat(),
            "settled",
        ),
    )

    resolved = evidence._resolve_cost_model(
        db,
        "v1",
        _CUTOFF,
    )

    assert resolved["status"] == "unavailable"
    assert resolved["reason"] == "cost_model_not_recorded"


def test_cost_model_source_ignores_pre_floor_history_when_clean_source_exists():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE filter_candidate_outcomes ("
        "id TEXT, cost_model_version TEXT, entry_ts TEXT, due_at TEXT, "
        "settled_at TEXT, status TEXT)"
    )
    db.executemany(
        "INSERT INTO filter_candidate_outcomes VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                "historical",
                "v1",
                _PRE_FLOOR_ENTRY,
                _PRE_FLOOR_DUE,
                _PRE_FLOOR_DUE,
                "settled",
            ),
            (
                "clean",
                "v1",
                _CLEAN_ENTRY,
                _CLEAN_DUE,
                _CLEAN_DUE,
                "settled",
            ),
        ],
    )

    resolved = evidence._resolve_cost_model(
        db,
        "v1",
        _AS_OF_CLEAN,
    )

    assert resolved["status"] == "available"
    assert resolved["source_count"] == 1


def test_candidate_provenance_requires_exposure_and_settlement_identity():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE runtime_sessions ("
        "session_id TEXT PRIMARY KEY, boot_commit TEXT, behavior_fingerprint TEXT)"
    )
    registration = {"deploy_commit": "commit-a", "behavior_fingerprint": "behavior-a"}
    outcome = {
        "producer_runtime_session_id": "session-a",
        "producer_commit": "commit-a",
        "producer_behavior_fingerprint": "behavior-a",
    }

    assert evidence._candidate_provenance_reason(db, outcome, registration) == (
        "runtime_session_not_recorded"
    )
    db.execute("INSERT INTO runtime_sessions VALUES (?,?,?)", ("session-a", "commit-a", "behavior-a"))
    assert evidence._candidate_provenance_reason(db, outcome, registration) == (
        "candidate_settlement_provenance_missing"
    )
    outcome.update({
        "settlement_runtime_session_id": "session-b",
        "settlement_producer_commit": "commit-a",
        "settlement_behavior_fingerprint": "behavior-a",
    })
    assert evidence._candidate_provenance_reason(db, outcome, registration) == (
        "runtime_session_not_recorded"
    )
    db.execute(
        "INSERT INTO runtime_sessions VALUES (?,?,?)",
        ("session-b", "commit-b", "behavior-b"),
    )
    assert evidence._candidate_provenance_reason(db, outcome, registration) == (
        "candidate_producer_identity_mismatch"
    )
    outcome.update({
        "settlement_producer_commit": "commit-a",
        "settlement_behavior_fingerprint": "behavior-a",
    })
    db.execute(
        "UPDATE runtime_sessions SET boot_commit=?, behavior_fingerprint=? WHERE session_id=?",
        ("commit-a", "behavior-a", "session-b"),
    )
    assert evidence._candidate_provenance_reason(db, outcome, registration) is None


def test_position_replay_cost_model_identity_is_explicit_and_resolvable():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    cost_model_id = "fixture-cost/v1"

    assert evidence._fact_cost_model_id(
        {"cost_identity": {"entry_fee_sources": [], "exit_fee_sources": [], "funding_source": None}}
    ) is None
    assert evidence._fact_cost_model_id(
        {"cost_identity": {"cost_model_id": cost_model_id}}
    ) == cost_model_id

    resolved = evidence._resolve_cost_model(
        db,
        cost_model_id,
        _CUTOFF,
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

    first, _ = evidence._load_candidates(db, _CUTOFF)
    second, _ = evidence._load_candidates(db, _CUTOFF)

    assert first["source_hash"] != second["source_hash"]


def test_candidate_loader_threads_epoch_bounds_into_source_identity(monkeypatch):
    captured: dict[str, object] = {}

    def _report(**kwargs):
        captured.update(kwargs)
        return {
            "contract_version": "profit_loop_fact/v1",
            "fixed_horizon": "24h",
            "epoch_start_at": kwargs["epoch_start_at"],
            "as_of_at": kwargs["as_of_at"],
            "candidate_count": 0,
            "settled_outcome_count": 0,
            "unavailable_count": 0,
            "unavailable_reasons": {},
            "exclusions": [],
            "facts": [],
        }

    monkeypatch.setattr(evidence, "build_candidate_facts", _report)

    cutoff = _CUTOFF
    epoch_start = _EPOCH_START
    source, facts = evidence._load_candidates(
        sqlite3.connect(":memory:"),
        cutoff,
        epoch_start,
    )

    assert captured["epoch_start_at"] == _EPOCH_START.isoformat()
    assert source["as_of_at"] == _CUTOFF.isoformat()
    assert source["epoch_start_at"] == _EPOCH_START.isoformat()
    assert facts == []


def test_layer_compilation_keeps_temporal_reason_when_cost_source_is_unavailable():
    _, stats = evidence._add_layer_samples(
        db=sqlite3.connect(":memory:"),
        candidate_facts=[{
            "fact_id": "candidate-temporal",
            "fixed_horizon_outcome": None,
            "fixed_horizon_outcome_unavailable_reason": "missing_settled_at",
        }],
        position_facts=[],
        cutoff=_CUTOFF,
        epoch_start=_EPOCH_START,
        cost_model_id="v1",
        epoch_id="epoch-1",
        cost_status="unavailable",
        registration_ok=True,
    )

    for layer in ("Selection", "Decision", "Risk"):
        assert stats[layer]["reason_counts"] == {"missing_settled_at": 1}


def _execution_fact(*, residual_within_tolerance: bool = True) -> dict:
    # Timestamps must fall within the epoch (_EPOCH_START through _CUTOFF)
    # so validation logic reaches cost-evidence field checks.
    opened = _EPOCH_START + timedelta(hours=1)
    closed = opened + timedelta(minutes=1)
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
                    "opened_at": opened.isoformat(),
                    "closed_at": closed.isoformat(),
                    "quantity": 1.0,
                    "entry_price": 100.0,
                    "direction": "long",
                },
                "close_orders": [{"reference_price": 110.0}],
                "close_fills": [{}],
            },
        },
    }


def _cost_evidence(**overrides) -> dict:
    base = {
        "avoidable": True,
        "baseline_kind": "turnover",
        "cost_event_id": "cost-evt-1",
        "baseline_id": "baseline-1",
        "cost_model_id": "fixture-cost/v1",
        "avoidable_extra_cost_quote": 0.5,
    }
    base.update(overrides)
    return base


def _cost_fact(**ce_overrides) -> dict:
    fact = _execution_fact()
    fact["cost_evidence"] = _cost_evidence(**ce_overrides)
    return fact


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("avoidable", False, "cost_not_avoidable"),
        ("avoidable", None, "cost_not_avoidable"),
        ("baseline_kind", "zero_fees", "cost_baseline_not_realizable"),
        ("baseline_kind", None, "cost_baseline_not_realizable"),
        ("cost_event_id", "", "cost_event_identity_missing"),
        ("cost_event_id", None, "cost_event_identity_missing"),
        ("baseline_id", "", "cost_baseline_identity_missing"),
        ("baseline_id", None, "cost_baseline_identity_missing"),
        ("cost_model_id", "", "cost_model_identity_missing"),
        ("cost_model_id", None, "cost_model_identity_missing"),
        ("avoidable_extra_cost_quote", -0.01, "avoidable_cost_amount_invalid"),
        ("avoidable_extra_cost_quote", None, "avoidable_cost_amount_invalid"),
    ],
)
def test_cost_evidence_granular_rejection_codes(field, value, expected_reason, monkeypatch):
    monkeypatch.setattr(
        evidence,
        "_position_assignment",
        lambda *_a, **_k: ("treatment", "cluster-1", "pair-1", "policy-1", "commit-1", None),
    )
    fact = _cost_fact(**{field: value})
    sample, reason = evidence._position_cost_sample(
        sqlite3.connect(":memory:"),
        fact,
        cutoff=_CUTOFF,
        epoch_start=_EPOCH_START,
        cost_model_id="fixture-cost/v1",
        epoch_id="epoch-1",
    )
    assert sample is None
    assert reason == expected_reason


def test_cost_evidence_all_valid_baseline_kinds_are_admitted(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "_position_assignment",
        lambda *_a, **_k: ("treatment", "cluster-1", "pair-1", "policy-1", "commit-1", None),
    )
    for kind in ("realizable_limit", "spot_vs_perpetual", "turnover", "venue"):
        fact = _cost_fact(baseline_kind=kind)
        sample, reason = evidence._position_cost_sample(
            sqlite3.connect(":memory:"),
            fact,
            cutoff=_CUTOFF,
            epoch_start=_EPOCH_START,
            cost_model_id="fixture-cost/v1",
            epoch_id="epoch-1",
        )
        assert reason is None, f"baseline_kind={kind!r} rejected with {reason!r}"
        assert sample is not None
        assert sample["component"] == "avoidable_extra_cost"
        assert sample["cost_applied_once"] is True


def test_cost_evidence_absent_is_baseline_unavailable_not_zero(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "_position_assignment",
        lambda *_a, **_k: ("treatment", "cluster-1", "pair-1", "policy-1", "commit-1", None),
    )
    fact = _execution_fact()
    sample, reason = evidence._position_cost_sample(
        sqlite3.connect(":memory:"),
        fact,
        cutoff=_CUTOFF,
        epoch_start=_EPOCH_START,
        cost_model_id="fixture-cost/v1",
        epoch_id="epoch-1",
    )
    assert sample is None
    assert reason == "cost_baseline_unavailable"


def test_incomplete_source_cannot_emit_numeric_coverage():
    cutoff = _CUTOFF
    context = evidence._coverage_context(
        registration={},
        candidate_facts=[
            {
                "event_at": (_CUTOFF - timedelta(days=1)).isoformat(),
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "settled_at": _CUTOFF.isoformat(),
                    "cost_model_version": "v1",
                    "horizon": "24h",
                    "net_return_pct": 1.0,
                },
            }
        ],
        position_facts=[],
        epoch_start=_EPOCH_START,
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
    assert context["metric_versions"] == [evidence._learning_metric_version()]
    assert context["statistical_unit_versions"] == ["profit_loop_fact/v1"]
    assert context["stages"] == ["paper_10"]

    missing_outcome = evidence._coverage_context(
        registration={},
        candidate_facts=[
            {
                "event_at": (_CUTOFF - timedelta(days=1)).isoformat(),
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "settled_at": _CUTOFF.isoformat(),
                    "cost_model_version": "v1",
                    "horizon": "24h",
                    "net_return_pct": 1.0,
                },
            }
        ],
        position_facts=[],
        epoch_start=_EPOCH_START,
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
        epoch_start=_EPOCH_START,
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


def test_paired_execution_evidence_preserves_both_arms_and_reconciles_delta():
    from aletheia.analytics import profit_gradient as gradient

    common = {
        "fact_unit": "position_fact",
        "economic_unit": "verified_settled_net_economics",
        "pair_id": "pair-execution",
        "component": "execution_fill",
        "outcome_contract_version": "v1",
        "stage": "paper_10",
        "notional_quote": 1000.0,
        "stage_window": {
            "start_at": "2026-08-13T00:00:00+00:00",
            "end_at": "2026-08-13T00:01:00+00:00",
        },
        "ownership": {
            "stage": "execution",
            "decision": "exit_authorized",
            "loss_component": "fill_or_execution",
        },
    }
    treatment = {
        **common,
        "unit_id": "position-treatment",
        "arm": "treatment",
        "cluster_id": "pair-execution",
        "occurred_at": "2026-08-13T00:02:00+00:00",
        "position_id": "position-treatment",
        "arm_net_delta_bp": 50.0,
        "arm_net_delta_quote": 5.0,
        "execution_evidence": {
            "fees_excluded": True,
            "funding_excluded": True,
            "baseline_id": "baseline-treatment",
            "incremental_fill_loss_quote": 5.0,
        },
        "evidence_refs": ["position_fact:treatment"],
    }
    control = {
        **common,
        "unit_id": "position-control",
        "arm": "control",
        "cluster_id": "pair-execution",
        "occurred_at": "2026-08-13T00:02:00+00:00",
        "position_id": "position-control",
        "arm_net_delta_bp": 20.0,
        "arm_net_delta_quote": 2.0,
        "execution_evidence": {
            "fees_excluded": True,
            "funding_excluded": True,
            "baseline_id": "baseline-control",
            "incremental_fill_loss_quote": 2.0,
        },
        "evidence_refs": ["position_fact:control"],
    }

    paired, excluded = evidence._pair_confirmatory_samples([treatment, control])

    assert excluded == []
    assert paired[0]["arm_evidence"]["control"]["position_id"] == "position-control"
    assert paired[0]["evidence_refs"] == ["position_fact:control", "position_fact:treatment"]
    assert paired[0]["execution_evidence"]["incremental_fill_loss_quote"] == 3.0
    normalized, exclusions, _ = gradient._normalized_samples("Execution", paired)
    assert len(normalized) == 1
    assert exclusions == []

    tampered = dict(paired[0])
    tampered["execution_evidence"] = {
        **tampered["execution_evidence"],
        "incremental_fill_loss_quote": 5.0,
    }
    _, exclusions, _ = gradient._normalized_samples("Execution", [tampered])
    assert "execution_component_delta_mismatch" in exclusions


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
        ("exposure-1", "treatment", "cluster-1", "epoch-1", "candidate-1", _CLEAN_ENTRY),
    )
    fact = {
        "fact_id": "candidate-1",
        "event_at": _CLEAN_ENTRY,
        "identity": {"proposal_id": "proposal-1"},
        "terminal_state": "risk_reject",
        "fixed_horizon_outcome": {
                "status": "settled",
                "venue": "bitget",
                "direction": "long",
                "market_type": "perp",
            "horizon": "24h",
            "outcome_candidate_key": "candidate-1",
            "entry_ts": _CLEAN_ENTRY,
            "due_at": _CLEAN_DUE,
            "settled_at": _CLEAN_DUE,
            "settlement_price_source": "horizon_ohlcv",
            "settlement_lag_sec": 0.0,
            "entry_price_usd": 100.0,
            "exit_price_usd": 99.2,
            "directional_return_pct": -0.8,
            "estimated_fee_pct": 0.1,
            "estimated_spread_slippage_pct": 0.1,
            "estimated_funding_pct": 0.0,
            "net_return_pct": -1.0,
            "min_tradable_edge_pct": 0.05,
            "cost_model_version": "v1",
            "analysis_notional_quote": 1.0,
        },
    }

    sample, reason = evidence._candidate_sample(
        db,
        fact,
        layer="Risk",
        cutoff=_AS_OF_CLEAN,
        epoch_start=_ANCHOR,
        cost_model_id="v1",
        epoch_id="epoch-1",
    )

    assert reason is None
    assert sample["paired_net_delta_bp"] == -100.0
    assert sample["paired_net_delta_quote"] == pytest.approx(-0.01)
    assert sample["cost_applied_once"] is True

    legacy_fact = fact | {
        "fixed_horizon_outcome": {
            key: value
            for key, value in fact["fixed_horizon_outcome"].items()
            if key != "analysis_notional_quote"
        }
    }
    legacy_sample, legacy_reason = evidence._candidate_sample(
        db,
        legacy_fact,
        layer="Risk",
        cutoff=_AS_OF_CLEAN,
        epoch_start=_ANCHOR,
        cost_model_id="v1",
        epoch_id="epoch-1",
    )
    assert legacy_sample is None
    assert legacy_reason == "candidate_analysis_notional_unavailable"

    naive_settlement_fact = fact | {
        "fixed_horizon_outcome": fact["fixed_horizon_outcome"] | {
            "settled_at": _CLEAN_DUE_NAIVE,
        },
    }
    sample, reason = evidence._candidate_sample(
        db,
        naive_settlement_fact,
        layer="Risk",
        cutoff=_AS_OF_CLEAN,
        epoch_start=_ANCHOR,
        cost_model_id="v1",
        epoch_id="epoch-1",
    )
    assert reason is None
    assert sample["stage_window"]["end_at"] == _CLEAN_DUE

    future_fact = fact | {
        "fixed_horizon_outcome": fact["fixed_horizon_outcome"] | {
            "settled_at": _AFTER_CUTOFF,
        },
    }
    sample, reason = evidence._candidate_sample(
        db,
        future_fact,
        layer="Risk",
        cutoff=_AS_OF_CLEAN,
        epoch_start=_ANCHOR,
        cost_model_id="v1",
        epoch_id="epoch-1",
    )
    assert sample is None
    assert reason == "settlement_after_as_of"


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


def test_execution_layer_has_no_external_paired_reference():
    samples, stats = evidence._add_layer_samples(
        db=sqlite3.connect(":memory:"), candidate_facts=[], position_facts=[_execution_fact()],
        cutoff=_CUTOFF,
        epoch_start=_EPOCH_START,
        cost_model_id="fixture-cost/v1", epoch_id="epoch-1",
        cost_status="available", registration_ok=True,
    )
    assert samples["Execution"] == []
    assert stats["Execution"]["eligible_count"] == 0
    assert stats["Execution"]["reason_counts"] == {"paired_evidence_unavailable": 1}


def test_retired_whole_policy_qualification_command_is_removed():
    result = runner.invoke(inspect_app, ["profit-loop-qualification"])
    assert result.exit_code != 0
    assert "No such command" in result.output
