"""``aletheia observe overview`` + ``checkpoint`` CLI tests (G4).

Backs the "is Lab alive and healthy?" answer path the Fedora deployment
plan (§8/§9 of ``observation_lab_v1_design.md``) depends on.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from aletheia.cli.observe_cmd import _collect_overview, observe_app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _register_ranker() -> None:
    from aletheia.counterfactual.filter_experiments import register_observation_from_spec

    register_observation_from_spec(
        {
            "filter_name": "ranker_confidence_gate",
            "filter_version": "overview-test-v1",
            "stage": "paper_ranker",
            "scanner_family": "mean_reversion",
            "direction": "long",
            "market_type": "perp",
            "horizon": "4h",
            "control": {"minimum_confidence": "high"},
            "treatment": {"minimum_confidence": "medium"},
            "min_samples": 20,
        },
        now=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
    )


def _seed_equivalence_row(*, result: str) -> None:
    import uuid

    from aletheia.observation_lab.db import get_lab_db

    assert result in {"matched", "mismatched", "skipped"}
    get_lab_db().execute(
        "INSERT INTO ufc_shadow_equivalence "
        "(id, observation_id, experiment_id, cohort_key, checked_at, matched, "
        "mismatch_field, learning_verdict, recomputed_verdict, "
        "learning_treatment_n, recomputed_treatment_n, "
        "learning_control_n, recomputed_control_n, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex,
            f"ufc:{result}:epoch-1:0",
            "exp-x",
            "hyp-x:epoch-1:shadow",
            "2026-08-19T12:00:00+00:00",
            1 if result == "matched" else 0,
            "verdict" if result == "mismatched" else None,
            "positive",
            "negative" if result == "mismatched" else "positive",
            50, 50, 50, 50,
            "no_exact_current_verdict" if result == "skipped" else None,
        ),
    )


@pytest.mark.parametrize(
    "note",
    [
        "skipped",
        "no_stored_verdict_yet",
        "no_cohorts_yet",
        "current_policy_epoch_unavailable",
        "no_exact_current_cohort",
        "no_exact_current_verdict",
    ],
)
def test_decode_equivalence_result_preserves_historical_skip_notes(note: str):
    from aletheia.observation_lab.ufc_shadow import decode_equivalence_result

    assert decode_equivalence_result({"matched": 0, "notes": note}) == "skipped"
    assert (
        decode_equivalence_result({"matched": 0, "notes": "evaluator failed"})
        == "mismatched"
    )


def test_collect_overview_shape_on_empty_lab():
    payload = _collect_overview()

    assert payload["experiments"]["total"] == 0
    assert payload["ufc_equivalence"]["recent_total"] == 0
    assert payload["ufc_equivalence"]["recent_checked"] == 0
    assert payload["ufc_equivalence"]["recent_matched"] == 0
    assert payload["ufc_equivalence"]["recent_mismatched"] == 0
    assert payload["ufc_equivalence"]["recent_skipped"] == 0
    assert payload["manifests"]["active_count"] == 0
    # First verify on a fresh lab reports first_checkpoint.
    assert payload["checkpoint"]["verified"]["ok"] is True
    assert payload["checkpoint"]["verified"]["reason"] == "first_checkpoint"


def test_collect_overview_reports_experiments_and_equivalence():
    _register_ranker()
    _seed_equivalence_row(result="matched")
    _seed_equivalence_row(result="mismatched")
    _seed_equivalence_row(result="skipped")

    payload = _collect_overview(equivalence_limit=10)

    assert payload["experiments"]["total"] == 1
    assert payload["experiments"]["collecting"] == 1
    assert payload["ufc_equivalence"]["recent_total"] == 3
    assert payload["ufc_equivalence"]["recent_checked"] == 2
    assert payload["ufc_equivalence"]["recent_matched"] == 1
    assert payload["ufc_equivalence"]["recent_mismatched"] == 1
    assert payload["ufc_equivalence"]["recent_skipped"] == 1
    records = {
        row["result"]: row
        for row in payload["ufc_equivalence"]["recent_records"]
    }
    assert records["matched"]["matched"] is True
    assert records["mismatched"]["matched"] is False
    assert records["skipped"]["matched"] is None
    # Records share a timestamp, so compare the complete identity set.
    ids = [r["observation_id"] for r in payload["ufc_equivalence"]["recent_records"]]
    assert set(ids) == {
        "ufc:matched:epoch-1:0",
        "ufc:mismatched:epoch-1:0",
        "ufc:skipped:epoch-1:0",
    }


def test_collect_overview_reports_active_manifests():
    from aletheia.observation_lab.manifest import build_manifest, publish_manifest

    manifest = build_manifest(
        observation_id="active-manifest",
        generation=1,
        spec_hash="s",
        treatment_adapter_id="t",
        treatment_adapter_version="v1",
        assignment_policy="stable",
        assignment_salt="salt",
        assignment_unit_key_template="{k}",
        allocation_pct=0.5,
        control_payload={"m": 1.0},
        treatment_payload={"m": 1.1},
        eligible_population={"path_id": "test"},
        behavior_contract_hash="bh",
        interference_key="ifk",
        composability_contract="exclusive",
        effective_from="2020-01-01T00:00:00+00:00",
        deterministic_baseline={"m": 1.0},
        unavailable_fallback="baseline",
    )
    publish_manifest(manifest)

    payload = _collect_overview()
    assert payload["manifests"]["active_count"] == 1
    assert payload["manifests"]["active"][0]["observation_id"] == "active-manifest"
    assert payload["manifests"]["active"][0]["composability_contract"] == "exclusive"


def test_observe_overview_json_matches_collect(runner: CliRunner):
    _register_ranker()
    _seed_equivalence_row(result="matched")

    result = runner.invoke(observe_app, ["overview", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr

    payload = json.loads(result.stdout)
    # Same structure as the helper — CLI is a thin renderer.
    assert "experiments" in payload
    assert "ufc_equivalence" in payload
    assert "manifests" in payload
    assert "checkpoint" in payload


def test_observe_overview_renders_skip_neutrally(runner: CliRunner):
    _seed_equivalence_row(result="matched")
    _seed_equivalence_row(result="mismatched")
    _seed_equivalence_row(result="skipped")

    result = runner.invoke(observe_app, ["overview", "--equivalence-limit", "10"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "matched=1/2 skipped=1" in result.stdout
    assert "✓" in result.stdout
    assert "✗" in result.stdout
    assert "○" in result.stdout
    assert "skipped=no_exact_current_verdict" in result.stdout


def test_observe_checkpoint_command_saves_and_verifies(runner: CliRunner):
    from aletheia.observation_lab.checkpoint import latest_joint_checkpoint

    result = runner.invoke(observe_app, ["checkpoint", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["saved"] is True

    stored = latest_joint_checkpoint()
    assert stored is not None
    assert stored.joint_fingerprint == payload["current_fingerprint"]


def test_observe_checkpoint_verify_only_does_not_save(runner: CliRunner):
    from aletheia.observation_lab.checkpoint import latest_joint_checkpoint

    result = runner.invoke(observe_app, ["checkpoint", "--verify-only", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["saved"] is False
    # Fresh lab, verify-only, nothing stored yet.
    assert latest_joint_checkpoint() is None
