"""End-to-end smoke tests for ``aletheia observe`` (Issue #1072)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia.cli.observe_cmd import observe_app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _register_ranker(now: datetime, *, scanner_family: str = "mean_reversion") -> None:
    """Seed one collecting ranker observation via the register-from-spec path."""
    from aletheia.counterfactual.filter_experiments import register_observation_from_spec

    register_observation_from_spec(
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
        now=now,
    )


def test_observe_list_empty_and_populated(runner: CliRunner) -> None:
    result = runner.invoke(observe_app, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []

    _register_ranker(datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc))
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


def test_observe_status_reports_frozen_count_and_control(runner: CliRunner) -> None:
    from aletheia.counterfactual.filter_experiments import capture_frozen_units_for
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    _register_ranker(start)

    # Seed a small cohort so capture has something to freeze.
    from tests.test_filter_shadow_experiments import _seed_ranker_cohort

    _seed_ranker_cohort(start, count=5)
    experiment = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()
    capture_frozen_units_for(dict(experiment))

    result = runner.invoke(observe_app, ["status", experiment["id"], "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["spec"]["id"] == experiment["id"]
    assert payload["frozen_units"] == 10  # 5 passed + 5 rejected


def test_observe_status_text_projects_durable_window_contract(runner: CliRunner) -> None:
    from aletheia.observation_lab.kernel import (
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
    from aletheia.observation_lab import kernel
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.runner import register_builtin_handlers

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
        started_at="2026-09-01T00:00:00+00:00",
        collect_until="2026-09-15T00:00:00+00:00",
        finalize_not_before="2026-09-16T06:00:00+00:00",
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
    from aletheia.observation_lab import kernel

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
        started_at="2026-09-01T00:00:00+00:00",
        collect_until="2026-09-15T00:00:00+00:00",
        finalize_not_before="2026-09-16T06:00:00+00:00",
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
    from aletheia.observation_lab.kernel import (
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
def test_observe_sync_captures_and_evaluates(runner: CliRunner) -> None:
    from tests.test_filter_shadow_experiments import _seed_ranker_cohort

    start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    _register_ranker(start)
    _seed_ranker_cohort(start, count=25)

    result = runner.invoke(observe_app, ["sync", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["frozen_units_captured"] == 50
    assert payload["evaluation"]["evaluated"] == 1


def test_observe_close_stops_collection_without_deleting_history(
    runner: CliRunner,
) -> None:
    from aletheia.storage.sqlite import get_db

    _register_ranker(datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc))
    experiment = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()

    result = runner.invoke(
        observe_app,
        ["close", experiment["id"], "--reason", "manual test cleanup", "--json"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr

    after = get_db().execute(
        "SELECT status, invalidation_reason FROM filter_shadow_experiments WHERE id=?",
        (experiment["id"],),
    ).fetchone()
    assert after["status"] == "invalidated"
    assert after["invalidation_reason"].startswith("operator_closed:")


def test_observe_evaluate_targets_one_observation(runner: CliRunner) -> None:
    from tests.test_filter_shadow_experiments import _seed_ranker_cohort
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    _register_ranker(start)
    _seed_ranker_cohort(start, count=25)

    experiment = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()
    result = runner.invoke(
        observe_app, ["evaluate", experiment["id"], "--json"]
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert "disposition" in payload
