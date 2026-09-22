"""``aletheia observe overview`` + ``checkpoint`` CLI tests (G4).

Backs the "is Lab alive and healthy?" answer path the Fedora deployment
plan (§8/§9 of ``observation_lab_v1_design.md``) depends on.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import aletheia.cli.observe_cmd as observe_cmd
from aletheia.cli.observe_cmd import _collect_overview, observe_app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _worker_status(*, systemd_available: bool = False) -> dict:
    return {
        "systemd_available": systemd_available,
        "checkout": "/srv/aletheia-observer" if systemd_available else None,
        "checkout_available": systemd_available,
        "head_sha": "a" * 40 if systemd_available else None,
        "timer": {
            "load_state": "loaded" if systemd_available else None,
            "active_state": "active" if systemd_available else None,
        },
        "last_service_pass": {
            "load_state": "loaded" if systemd_available else None,
            "status": "success" if systemd_available else None,
            "at": "2026-09-01 12:00:00 UTC" if systemd_available else None,
            "exit_code": 0 if systemd_available else None,
        },
    }


def _set_owner(monkeypatch: pytest.MonkeyPatch, owner: str) -> None:
    from extensions.observation_lab import execution_owner

    monkeypatch.setattr(execution_owner, "get_execution_owner", lambda: owner)


def _ensure_lab() -> None:
    from extensions.observation_lab.db import get_lab_db

    get_lab_db()


def _register_ranker() -> None:
    from extensions.observation_lab.canonical import set_authority_source
    set_authority_source("lab", actor="test", reason="overview fixture")

    from extensions.counterfactual.filter_experiments import register_observation_from_spec

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

    from extensions.observation_lab.db import get_lab_db

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
    from extensions.observation_lab.ufc_shadow import decode_equivalence_result

    assert decode_equivalence_result({"matched": 0, "notes": note}) == "skipped"
    assert (
        decode_equivalence_result({"matched": 0, "notes": "evaluator failed"})
        == "mismatched"
    )


def test_collect_overview_missing_lab_is_unavailable_and_read_only(tmp_path):
    payload = _collect_overview()

    assert payload["experiments"]["total"] == 0
    assert payload["overall_status"] == "unavailable"
    assert payload["reason_codes"] == ["lab_db_missing"]
    assert payload["storage"]["lab_db"]["status"] == "missing"
    assert payload["ufc_equivalence"]["recent_total"] is None
    assert not (tmp_path / "observation-lab").exists()


@pytest.mark.real_db
def test_collect_overview_missing_databases_creates_nothing(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from extensions.observation_lab.db import close_lab_db
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    _set_owner(monkeypatch, "main")
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)
    close_lab_db()
    reset_db()

    payload = _collect_overview()

    assert payload["overall_status"] == "unavailable"
    assert payload["reason_codes"] == ["app_db_missing", "lab_db_missing"]
    assert list(tmp_path.iterdir()) == []


def test_collect_overview_does_not_persist_legacy_config_migration(
    tmp_path, monkeypatch
):
    import aletheia.config.loader as loader

    _ensure_lab()
    config = tmp_path / "config.yaml"
    original = "observation:\n  execution_owner: main\n"
    config.write_text(original, encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", config)
    loader._invalidate_config_cache()
    audit_calls = []
    monkeypatch.setattr(
        loader,
        "_audit_operator_authority_migration",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)

    payload = _collect_overview()

    assert payload["execution_owner"] == "main"
    assert config.read_text(encoding="utf-8") == original
    assert audit_calls == []


def test_collect_overview_shape_on_empty_existing_lab(monkeypatch):
    from extensions.observation_lab.db import get_lab_db

    get_lab_db()
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)
    payload = _collect_overview()

    assert payload["overall_status"] == "healthy"
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
    from extensions.observation_lab.manifest import build_manifest, publish_manifest

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


def test_collect_overview_healthy_worker(monkeypatch):
    _ensure_lab()
    _set_owner(monkeypatch, "worker")
    monkeypatch.setattr(
        observe_cmd, "_collect_worker_status", lambda: _worker_status(systemd_available=True)
    )

    payload = _collect_overview()

    assert payload["overall_status"] == "healthy"
    assert payload["reason_codes"] == []
    assert payload["execution_owner"] == "worker"
    assert payload["worker"]["head_sha"] == "a" * 40
    assert payload["windows"] == {"available": True, "active_count": 0, "items": []}


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("timer_inactive", {"worker_timer_inactive"}),
        ("timer_not_loaded", {"worker_timer_not_loaded"}),
        ("service_not_loaded", {"worker_service_not_loaded"}),
        ("never_run", {"worker_pass_not_recorded"}),
        ("failed", {"worker_last_pass_failed"}),
        ("checkout", {"worker_checkout_unavailable", "worker_head_unavailable"}),
    ],
)
def test_collect_overview_degraded_worker_evidence(monkeypatch, case, expected):
    _ensure_lab()
    _set_owner(monkeypatch, "worker")
    worker = _worker_status(systemd_available=True)
    if case == "timer_inactive":
        worker["timer"]["active_state"] = "inactive"
    elif case == "timer_not_loaded":
        worker["timer"]["load_state"] = "not-found"
    elif case == "service_not_loaded":
        worker["last_service_pass"]["load_state"] = "not-found"
    elif case == "never_run":
        worker["last_service_pass"]["at"] = None
    elif case == "failed":
        worker["last_service_pass"].update(status="exit-code", exit_code=4)
    else:
        worker.update(checkout=None, checkout_available=False, head_sha=None)
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", lambda: worker)

    payload = _collect_overview()

    assert payload["overall_status"] == "degraded"
    assert expected <= set(payload["reason_codes"])


def test_collect_overview_worker_requires_systemd(monkeypatch):
    _ensure_lab()
    _set_owner(monkeypatch, "worker")
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)

    payload = _collect_overview()

    assert payload["overall_status"] == "unavailable"
    assert "worker_systemd_unavailable" in payload["reason_codes"]


def test_collect_overview_main_ignores_absent_systemd_but_rejects_active_timer(monkeypatch):
    _ensure_lab()
    _set_owner(monkeypatch, "main")
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)
    assert _collect_overview()["overall_status"] == "healthy"

    monkeypatch.setattr(
        observe_cmd, "_collect_worker_status", lambda: _worker_status(systemd_available=True)
    )
    payload = _collect_overview()
    assert payload["overall_status"] == "degraded"
    assert payload["reason_codes"] == ["worker_timer_active_with_main_owner"]


def test_collect_overview_unknown_owner_is_unavailable(monkeypatch):
    _ensure_lab()
    _set_owner(monkeypatch, "unknown")
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)

    payload = _collect_overview()

    assert payload["overall_status"] == "unavailable"
    assert "execution_owner_unavailable" in payload["reason_codes"]


def test_collect_overview_reports_window_handler_and_error(monkeypatch):
    from extensions.observation_lab import kernel
    from extensions.observation_lab.db import get_lab_db
    from extensions.observation_lab.kernel import (
        ObservationHandler,
        ObservationProgress,
        register_handler,
        start_window,
    )

    handler = ObservationHandler(
        kind="overview-test",
        version="v1",
        normalize_spec=lambda spec: spec,
        capture=None,
        advance=lambda _window: ObservationProgress(summary={}),
        finalize=lambda _window: {},
        retention_claims=lambda _window: (),
    )
    register_handler(handler, replace=True)
    window = start_window(
        observation_id="overview-window",
        kind="overview-test",
        handler_version="v1",
        slot_key="overview-test",
        authority_mode="observe_only",
        spec={"v": 1},
        started_at="2026-09-01T00:00:00+00:00",
        collect_until="2026-09-02T00:00:00+00:00",
        finalize_not_before="2026-09-02T00:00:00+00:00",
    )
    get_lab_db().execute(
        "UPDATE observation_windows SET last_error='boom' WHERE observation_id=? AND generation=?",
        (window["observation_id"], window["generation"]),
    )
    monkeypatch.delitem(kernel._HANDLERS, ("overview-test", "v1"))
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)

    payload = _collect_overview()

    assert payload["windows"]["active_count"] == 1
    assert payload["windows"]["items"][0]["last_error"] == "boom"
    assert {
        "active_window_handler_unavailable",
        "active_window_last_error",
    } <= set(payload["reason_codes"])


def test_collect_overview_checkpoint_and_ufc_mismatch_are_degraded(monkeypatch):
    from extensions.observation_lab import checkpoint

    _ensure_lab()
    _seed_equivalence_row(result="mismatched")
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)
    monkeypatch.setattr(
        checkpoint,
        "verify_joint_checkpoint",
        lambda: SimpleNamespace(ok=False, reason="fingerprint_mismatch", current_fingerprint="x"),
    )

    payload = _collect_overview()

    assert payload["overall_status"] == "degraded"
    assert {
        "observation_checkpoint_mismatch",
        "ufc_equivalence_mismatch",
    } <= set(payload["reason_codes"])


def test_collect_overview_attributes_corrupt_checkpoint_to_lab(monkeypatch):
    from extensions.observation_lab.db import get_lab_db

    _ensure_lab()
    get_lab_db().execute(
        "INSERT INTO joint_checkpoints ("
        "checkpoint_id, recorded_at, app_migration_head, app_migration_count, "
        "active_manifest_hashes, active_manifest_count, joint_fingerprint, notes"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "corrupt-checkpoint",
            "2026-09-01T12:00:00+00:00",
            "migration-head",
            1,
            "{",
            0,
            "fingerprint",
            None,
        ),
    )
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)

    payload = _collect_overview()

    assert payload["overall_status"] == "unavailable"
    assert payload["storage"]["app_db"]["status"] == "reachable"
    assert payload["storage"]["lab_db"]["status"] == "unreachable"
    assert "lab_db_unreachable" in payload["reason_codes"]
    assert "app_db_unreachable" not in payload["reason_codes"]


def test_readonly_lab_db_physically_rejects_writes():
    from extensions.observation_lab.db import get_lab_db, readonly_lab_db

    get_lab_db()
    with readonly_lab_db() as db:
        assert db is not None
        with pytest.raises(sqlite3.OperationalError):
            db.execute("CREATE TABLE overview_must_not_write (id INTEGER)")


def test_overview_all_health_levels_exit_zero(runner, monkeypatch):
    _ensure_lab()
    _set_owner(monkeypatch, "main")
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", _worker_status)
    healthy = runner.invoke(observe_app, ["overview", "--json"])

    _set_owner(monkeypatch, "unknown")
    unavailable = runner.invoke(observe_app, ["overview", "--json"])

    _set_owner(monkeypatch, "main")
    monkeypatch.setattr(observe_cmd, "_collect_worker_status", lambda: _worker_status(systemd_available=True))
    degraded = runner.invoke(observe_app, ["overview", "--json"])

    assert [healthy.exit_code, degraded.exit_code, unavailable.exit_code] == [0, 0, 0]


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
    from extensions.observation_lab.checkpoint import latest_joint_checkpoint

    result = runner.invoke(observe_app, ["checkpoint", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["saved"] is True

    stored = latest_joint_checkpoint()
    assert stored is not None
    assert stored.joint_fingerprint == payload["current_fingerprint"]


def test_observe_checkpoint_verify_only_does_not_save(runner: CliRunner):
    from extensions.observation_lab.checkpoint import latest_joint_checkpoint

    result = runner.invoke(observe_app, ["checkpoint", "--verify-only", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["saved"] is False
    # Fresh lab, verify-only, nothing stored yet.
    assert latest_joint_checkpoint() is None
