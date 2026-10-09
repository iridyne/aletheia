"""End-to-end smoke tests for ``aletheia observe`` (Issue #1072)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia.cli.observe_cmd import observe_app


def _collecting_window_times() -> tuple[str, str, str]:
    """A window whose phase is `collecting` whenever the test runs.

    `_phase` compares collect_until against the clock, so a window pinned to
    2026-09-15 reported `collecting` only until that date and then flipped to
    `ready_to_finalize`. The assertion had nothing to do with calendar dates, so
    the bounds are relative to now.
    """
    now = datetime.now(timezone.utc)
    return (
        (now - timedelta(days=14)).isoformat(),
        (now + timedelta(days=14)).isoformat(),
        (now + timedelta(days=15)).isoformat(),
    )


@pytest.fixture()
def runner() -> CliRunner:
    from extensions.observation_lab.canonical import set_authority_source

    set_authority_source("lab", actor="test", reason="CLI fixture")
    return CliRunner()


def _register_ranker(*, scanner_family: str = "mean_reversion") -> dict:
    """Seed one canonical Lab observation."""
    from extensions.observation_lab.canonical import register_observation_spec

    return register_observation_spec(
        {
            "filter_name": "ranker_confidence_gate",
            "filter_version": "cli-test-v1",
            "stage": "paper_ranker",
            "scanner_family": scanner_family,
            "direction": "long",
            "market_type": "perp",
            "horizon": "4h",
            "control": {"minimum_confidence": "high"},
            "treatment": {"minimum_confidence": "medium"},
            "min_samples": 20,
        },
    )


def test_observe_list_empty_and_populated(runner: CliRunner) -> None:
    result = runner.invoke(observe_app, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []

    _register_ranker()
    result = runner.invoke(observe_app, ["list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["filter_name"] == "ranker_confidence_gate"
    assert payload[0]["status"] == "collecting"
    assert payload[0]["frozen_units"] == 0


def test_observe_register_yaml_creates_row(runner: CliRunner, tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "filter_name: ranker_confidence_gate\n"
        "filter_version: yaml-v1\n"
        "stage: paper_ranker\n"
        "scanner_family: momentum\n"
        "direction: long\n"
        "market_type: perp\n"
        "horizon: 4h\n"
        "control:\n"
        "  minimum_confidence: high\n"
        "treatment:\n"
        "  minimum_confidence: medium\n"
        "min_samples: 15\n",
        encoding="utf-8",
    )
    result = runner.invoke(observe_app, ["register", str(spec_path), "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert "observation_id" in payload


def test_observe_status_text_projects_durable_window_contract(runner: CliRunner) -> None:
    from extensions.observation_lab.kernel import (
        ObservationHandler, ObservationProgress, register_handler, start_window,
    )

    register_handler(ObservationHandler(
        kind="cli-window",
        version="v1",
        normalize_spec=lambda spec: spec,
        capture=None,
        advance=lambda _window: ObservationProgress(summary={"ready": False}),
        finalize=lambda _window: {},
        retention_claims=lambda _window: (),
    ), replace=True)
    start_window(
        observation_id="cli-durable-window",
        kind="cli-window",
        handler_version="v1",
        slot_key="cli-slot",
        authority_mode="observe_only",
        spec={"contract": "v1"},
        started_at="2026-08-17T12:00:00+00:00",
        collect_until="2026-08-18T12:00:00+00:00",
        finalize_not_before="2026-08-19T12:00:00+00:00",
    )

    result = runner.invoke(observe_app, ["status", "cli-durable-window"])
    assert result.exit_code == 0, result.stdout + result.stderr
    for key in ("slot_key", "latest_summary", "handler_available"):
        assert f"{key}=" in result.stdout



def test_observe_status_bootstraps_builtin_handlers_without_writing_window(
    runner: CliRunner, monkeypatch
) -> None:
    from extensions.observation_lab import kernel
    from extensions.observation_lab.db import get_lab_db
    from extensions.observation_lab.runner import register_builtin_handlers

    monkeypatch.setattr(kernel, "_HANDLERS", {})
    register_builtin_handlers()
    window = kernel.start_window(
        observation_id="universe-standalone-handler",
        kind="universe_discovery",
        handler_version=next(
            version
            for kind, version in kernel.registered_handlers()
            if kind == "universe_discovery"
        ),
        slot_key="universe_discovery_benchmark",
        authority_mode="observe_only",
        spec={"contract": "frozen"},
        started_at=_collecting_window_times()[0],
        collect_until=_collecting_window_times()[1],
        finalize_not_before=_collecting_window_times()[2],
    )
    before = get_lab_db().execute(
        "SELECT last_error,last_advanced_at FROM observation_windows "
        "WHERE observation_id=? AND generation=?",
        (window["observation_id"], window["generation"]),
    ).fetchone()

    kernel._HANDLERS.clear()
    text = runner.invoke(observe_app, ["status", window["observation_id"]])
    assert text.exit_code == 0, text.stdout + text.stderr
    assert "handler_available=True" in text.stdout
    assert "phase=collecting" in text.stdout

    kernel._HANDLERS.clear()
    prefixed = runner.invoke(
        observe_app, ["status", "universe-standalone", "--json"]
    )
    assert prefixed.exit_code == 0, prefixed.stdout + prefixed.stderr
    payload = json.loads(prefixed.stdout)
    assert payload["handler_available"] is True
    assert payload["phase"] == "collecting"

    after = get_lab_db().execute(
        "SELECT last_error,last_advanced_at FROM observation_windows "
        "WHERE observation_id=? AND generation=?",
        (window["observation_id"], window["generation"]),
    ).fetchone()
    assert tuple(after) == tuple(before) == (None, None)


def test_observe_status_keeps_unknown_durable_handler_unavailable(
    runner: CliRunner, monkeypatch
) -> None:
    from extensions.observation_lab import kernel

    monkeypatch.setattr(kernel, "_HANDLERS", {})
    kernel.register_handler(kernel.ObservationHandler(
        kind="unknown-domain",
        version="v1",
        normalize_spec=lambda spec: spec,
        capture=None,
        advance=lambda _window: kernel.ObservationProgress(summary={}),
        finalize=lambda _window: {},
        retention_claims=lambda _window: (),
    ))
    kernel.start_window(
        observation_id="unknown-domain-window",
        kind="unknown-domain",
        handler_version="v1",
        slot_key="unknown-domain",
        authority_mode="observe_only",
        spec={},
        started_at=_collecting_window_times()[0],
        collect_until=_collecting_window_times()[1],
        finalize_not_before=_collecting_window_times()[2],
    )
    kernel._HANDLERS.clear()

    result = runner.invoke(
        observe_app, ["status", "unknown-domain-window", "--json"]
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["handler_available"] is False
    assert payload["phase"] == "handler_unavailable"
def test_observe_status_resolves_unique_durable_prefix_and_rejects_ambiguity(runner: CliRunner) -> None:
    from extensions.observation_lab.kernel import (
        ObservationHandler, ObservationProgress, register_handler, start_window,
    )

    register_handler(ObservationHandler(
        kind="cli-window", version="v1", normalize_spec=lambda spec: spec,
        capture=None, advance=lambda _window: ObservationProgress(summary={}),
        finalize=lambda _window: {}, retention_claims=lambda _window: (),
    ), replace=True)

    for suffix in ("alpha", "alpine"):
        start_window(
            observation_id=f"durable-{suffix}", kind="cli-window", handler_version="v1",
            slot_key=f"cli-{suffix}", authority_mode="observe_only", spec={"suffix": suffix},
            started_at="2026-08-17T12:00:00+00:00",
            collect_until="2026-08-18T12:00:00+00:00",
            finalize_not_before="2026-08-19T12:00:00+00:00",
        )

    unique = runner.invoke(observe_app, ["status", "durable-alph", "--json"])
    assert unique.exit_code == 0
    assert json.loads(unique.stdout)["observation_id"] == "durable-alpha"
    ambiguous = runner.invoke(observe_app, ["status", "durable-al", "--json"])
    assert ambiguous.exit_code == 2
    assert "is ambiguous" in ambiguous.stderr
def test_observe_close_stops_collection_without_deleting_history(
    runner: CliRunner,
) -> None:
    from extensions.observation_lab.db import get_lab_db

    experiment = _register_ranker()
    result = runner.invoke(
        observe_app,
        ["close", experiment["observation_id"], "--reason", "manual test cleanup", "--json"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "pending_lab_application"
    sync = runner.invoke(observe_app, ["sync", "--json"])
    assert sync.exit_code == 0, sync.stdout + sync.stderr
    after = get_lab_db().execute(
        "SELECT status, terminal_reason FROM observation_windows WHERE observation_id=?",
        (experiment["observation_id"],),
    ).fetchone()
    assert after["status"] == "aborted"
    assert after["terminal_reason"] == "manual test cleanup"


def test_observe_missing_app_database_is_unavailable_without_creation(tmp_path):
    from aletheia.storage.sqlite import db_path, reset_db

    reset_db()
    path = Path(db_path())
    assert path.is_relative_to(tmp_path)
    assert not path.exists()
    result = CliRunner().invoke(observe_app, ["list", "--json"])
    assert result.exit_code == 1
    assert "authority unavailable" in result.stderr
    assert not path.exists()


def test_observe_legacy_authority_refuses_current_list():
    from extensions.observation_lab.canonical import get_authority_source

    before = get_authority_source()
    assert before.source == "legacy"
    result = CliRunner().invoke(observe_app, ["list", "--json"])
    assert result.exit_code == 1
    assert get_authority_source() == before


def _drift_after_migration_and_receipt():
    """#3856: v95->v96 applied outside the updater plus one new Main receipt."""
    from extensions.observation_lab.canonical import append_delivery_receipt
    from extensions.observation_lab.checkpoint import (
        _read_app_migration_state,
        compute_joint_checkpoint,
        save_joint_checkpoint,
    )

    save_joint_checkpoint(compute_joint_checkpoint())
    head, count = _read_app_migration_state()
    append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="ack-drift-receipt",
        candidate_key="candidate-ack-drift",
        resolution_reason="adapter_unavailable",
    )
    from unittest.mock import patch

    return patch(
        "extensions.observation_lab.checkpoint._read_app_migration_state",
        return_value=(f"{head}-v96", count + 1),
    )


def test_checkpoint_acknowledge_exact_components_saves_and_audits(runner: CliRunner) -> None:
    from aletheia.files.jsonl import read_jsonl
    from extensions.observation_lab.checkpoint import verify_joint_checkpoint

    with _drift_after_migration_and_receipt():
        blocked = runner.invoke(observe_app, ["checkpoint", "--json"])
        assert json.loads(blocked.output)["saved"] is False

        result = runner.invoke(
            observe_app,
            ["checkpoint", "--acknowledge", "app_migration,main_outbox", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["saved"] is True
        assert payload["acknowledged_components"] == ["app_migration", "main_outbox"]
        assert verify_joint_checkpoint().reason == "fingerprint_match"

    events = [
        event for event in read_jsonl("audit.jsonl")
        if event.get("event_type") == "observation_checkpoint_drift_acknowledged"
    ]
    assert len(events) == 1
    assert events[0]["after"]["acknowledged_components"] == ["app_migration", "main_outbox"]


def test_checkpoint_acknowledge_refuses_an_unnamed_component(runner: CliRunner) -> None:
    from aletheia.files.jsonl import read_jsonl
    from extensions.observation_lab.checkpoint import latest_joint_checkpoint

    with _drift_after_migration_and_receipt():
        before = latest_joint_checkpoint()
        result = runner.invoke(observe_app, ["checkpoint", "--acknowledge", "app_migration"])
        after = latest_joint_checkpoint()

    assert result.exit_code == 1
    assert "app_migration" in result.output and "main_outbox" in result.output
    assert after is not None and before is not None
    assert after.checkpoint_id == before.checkpoint_id
    assert not any(
        event.get("event_type") == "observation_checkpoint_drift_acknowledged"
        for event in read_jsonl("audit.jsonl")
    )
