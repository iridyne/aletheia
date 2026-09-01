from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia.analytics.filter_evidence import decode_filter_decision_batch
from aletheia.config import loader
from aletheia.storage import repositories as repo
from aletheia.storage import sqlite as sqlite_store
from aletheia.storage.migrations import apply_migrations
from aletheia.storage.models import FilterCandidateOutcome
from aletheia.universe import discovery_benchmark as benchmark
_REAL_CURRENT_COMMIT = benchmark._current_commit


@pytest.fixture
def benchmark_db(tmp_path, monkeypatch):
    sqlite_store.reset_db()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    apply_migrations()
    monkeypatch.setattr(benchmark, "_current_commit", lambda: "commit-a")
    yield tmp_path
    sqlite_store.reset_db()
    loader._invalidate_config_cache()


def _population(count: int = 18) -> list[dict]:
    rows = []
    for index in range(count):
        ret = (index - count / 2) * 0.6
        rows.append({
            "asset_id": f"asset-{index}",
            "feature_snapshot_id": f"feature-{index}",
            "venue": "okx",
            "symbol": f"ASSET{index}/USDT",
            "market_type": "perp",
            "last_price": 100.0 + index,
            "quote_volume_24h": 10_000_000.0 + index * 100_000,
            "spread_bps": 5.0 + index % 3,
            "depth_10bps_usd": 50_000.0,
            "atr_pct": 2.0 + index % 4,
            "return_4h": ret,
            "return_24h": ret * 2,
            "relative_strength_24h": ret * 2,
            "relative_strength_4h": ret,
            "vol_ratio": 2.0,
            "rsi_14": 55.0,
            "rsi_15m": 52.0,
            "rsi_4h": 55.0,
            "funding_rate": 0.0001,
            "open_interest": 1_000_000.0,
            "oi_delta_1h": 0.1,
            "long_short_ratio": 1.0,
            "data_freshness_sec": 5,
            "tradability_score": 80.0,
            "scanner_family": "momentum",
            "scanner_direction": "long" if ret > 0 else "short",
            "total_score": 70.0 - index,
            "execution_eligible": True,
        })
    return rows


def test_start_supersedes_legacy_active_without_copying_samples(benchmark_db):
    legacy = repo.insert_universe_discovery_benchmark({
        "id": "legacy-active",
        "status": "active",
        "contract_json": json.dumps({"legacy": True}),
        "contract_hash": "a" * 64,
        "code_commit": "legacy-commit",
        "started_at": "2025-12-01T00:00:00+00:00",
        "capture_ends_at": "2025-12-15T00:00:00+00:00",
        "finalize_not_before": "2025-12-16T06:00:00+00:00",
        "created_at": "2025-12-01T00:00:00+00:00",
        "summary_json": json.dumps({"legacy_samples": 7}),
    })

    fresh = benchmark.start_benchmark(
        now="2026-01-01T00:00:00+00:00", code_commit="fresh-commit"
    )
    persisted = repo.get_universe_discovery_benchmark(legacy["id"])
    status = benchmark.status_benchmark(fresh["id"])

    assert persisted["status"] == "invalidated"
    assert persisted["invalidation_reason"] == "superseded_by_observation_window_kernel"
    assert json.loads(persisted["summary_json"]) == {"legacy_samples": 7}
    assert fresh["id"] != legacy["id"]
    assert fresh["generation"] == 1
    assert status["unit_counts"] == {}

def test_capture_is_frozen_lossless_and_idempotent(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    allocated = population[:5]

    first = benchmark.capture_completed_run(
        scanner_run_id="scanner-run-1",
        population=population,
        allocated=allocated,
        entry_ts=started_at,
        regime_tag="sideways",
    )
    second = benchmark.capture_completed_run(
        scanner_run_id="scanner-run-1",
        population=population,
        allocated=allocated,
        entry_ts=started_at,
        regime_tag="sideways",
    )

    assert first["accepted"] is True
    assert first["outcomes_inserted"] > 0
    assert second == {
        "accepted": True,
        "idempotent": True,
        "reason": "already_captured",
        "batch_id": first["batch_id"],
    }
    batches = repo.list_universe_discovery_batches(window["id"])
    assert len(batches) == 1
    facts = decode_filter_decision_batch(batches[0]["candidate_facts"])
    assert len(facts) == len(population)
    assert facts[0]["production_scanner_family"] == "momentum"
    assert set(facts[0]["family_evidence"]) == set(benchmark.FAMILIES)
    status = benchmark.status_benchmark(window["id"], now=started_at)
    assert status["phase"] == "collecting"
    assert status["promotion_eligible"] is False
def test_capture_rejects_unavailable_current_producer_provenance(benchmark_db, monkeypatch):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="startup-commit")
    monkeypatch.setattr(benchmark, "_current_commit", lambda: None)

    result = benchmark.capture_completed_run(
        scanner_run_id="unknown-producer",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
    )

    assert result == {"accepted": False, "reason": "producer_provenance_unavailable"}
    assert repo.list_universe_discovery_batches(window["id"]) == []



def test_app_batch_reconciles_when_lab_append_initially_fails(benchmark_db, monkeypatch):
    from aletheia.observation_lab import kernel

    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    real_capture_event = kernel.capture_event
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("lab append failed")
        return real_capture_event(*args, **kwargs)

    monkeypatch.setattr(kernel, "capture_event", fail_once)
    capture = {
        "scanner_run_id": "reconcile-run",
        "population": _population(),
        "allocated": _population()[:5],
        "entry_ts": started_at,
        "regime_tag": "sideways",
    }
    with pytest.raises(RuntimeError, match="lab append failed"):
        benchmark.capture_completed_run(**capture)
    repeated = benchmark.capture_completed_run(**capture)

    assert repeated["idempotent"] is True
    status = benchmark.status_benchmark(window["id"], now=started_at)
    assert status["unit_counts"] == {"admitted": len(_population()) + 1}
    batches, outcomes, orphan_count = benchmark._admitted_snapshot(status)
    assert len(batches) == 1
    assert outcomes
    assert orphan_count == 0
def test_app_batch_retry_reconciles_units_from_persisted_canonical_batch(
    benchmark_db, monkeypatch
):
    from aletheia.observation_lab import kernel
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.agent_runtime import runtime_session

    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    real_capture_event = kernel.capture_event
    monkeypatch.setattr(runtime_session, "current_provenance", lambda: {})
    monkeypatch.setattr(
        kernel, "capture_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("lab append failed"))
    )
    original = _population()
    with pytest.raises(RuntimeError, match="lab append failed"):
        benchmark.capture_completed_run(
            scanner_run_id="canonical-retry", population=original,
            allocated=original[:5], entry_ts=started_at,
        )

    monkeypatch.setattr(kernel, "capture_event", real_capture_event)
    monkeypatch.setattr(benchmark, "_current_commit", lambda: None)
    monkeypatch.setattr(
        runtime_session, "current_provenance",
        lambda: {"runtime_session_id": "retry-session"},
    )
    changed = [{**row, "asset_id": f"changed-{index}"} for index, row in enumerate(original)]
    result = benchmark.capture_completed_run(
        scanner_run_id="canonical-retry", population=changed,
        allocated=changed[:5], entry_ts=started_at,
    )

    persisted = decode_filter_decision_batch(
        repo.list_universe_discovery_batches(window["id"])[0]["candidate_facts"]
    )
    keys = {
        row[0] for row in get_lab_db().execute(
            "SELECT unit_key FROM observation_units WHERE observation_id=? "
            "AND source_kind='universe_candidate'", (window["id"],)
        ).fetchall()
    }
    assert result["idempotent"] is True
    assert keys == {
        f"{result['batch_id']}:{'|'.join(benchmark._identity(item))}" for item in persisted
    }
    assert not any("changed-" in key for key in keys)
    sessions = {
        row[0] for row in get_lab_db().execute(
            "SELECT producer_runtime_session_id FROM observation_units WHERE observation_id=?",
            (window["id"],),
        ).fetchall()
    }
    assert sessions == {None}


def test_terminal_capture_race_leaves_orphan_out_of_domain_cohort(benchmark_db, monkeypatch):
    from aletheia.observation_lab import kernel

    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    real_capture_event = kernel.capture_event

    def abort_before_lab_append(observation_id, generation, *args, **kwargs):
        kernel.abort_window(observation_id, generation, reason="operator race")
        return real_capture_event(observation_id, generation, *args, **kwargs)

    monkeypatch.setattr(kernel, "capture_event", abort_before_lab_append)
    with pytest.raises(ValueError, match="window_aborted"):
        benchmark.capture_completed_run(
            scanner_run_id="terminal-race",
            population=_population(),
            allocated=_population()[:5],
            entry_ts=started_at,
            regime_tag="sideways",
        )

    terminal = benchmark.status_benchmark(window["id"], now=started_at)
    assert len(repo.list_universe_discovery_batches(window["id"])) == 1
    batches, outcomes, orphan_count = benchmark._admitted_snapshot(terminal)
    assert batches == []
    assert outcomes == []
    assert orphan_count == 1
    domain = benchmark._handler_finalize(terminal)
    assert domain["population_batches"] == []
    assert domain["outcomes"] == []


def test_empty_batch_has_durable_admission_marker(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")

    result = benchmark.capture_completed_run(
        scanner_run_id="empty-run", population=[], allocated=[], entry_ts=started_at,
    )
    batches, outcomes, orphan_count = benchmark._admitted_snapshot(
        benchmark.status_benchmark(window["id"])
    )

    assert result["accepted"] is True
    assert len(batches) == 1
    assert outcomes == []
    assert orphan_count == 0


def test_legacy_admitted_batch_retry_keeps_historical_unit_hashes(benchmark_db, monkeypatch):
    from aletheia.observation_lab import kernel

    handler = kernel._handler("universe_discovery", benchmark._HANDLER_VERSION)
    assert handler is not None

    def legacy_capture(_window, event):
        batch_id = str(event["batch_id"])
        return kernel.ObservationProgress(
            summary={"batch_id": batch_id},
            units=tuple({
                "unit_key": f"{batch_id}:{'|'.join(benchmark._identity(item))}",
                "source_kind": "universe_candidate",
                "source_ref": f"legacy:{batch_id}",
                "source_hash": "f" * 64,
                "known_at": event["known_at"],
                "admission": "admitted",
            } for item in event["population"]),
        )

    kernel.register_handler(
        kernel.ObservationHandler(
            kind=handler.kind, version=handler.version,
            normalize_spec=handler.normalize_spec, capture=legacy_capture,
            advance=handler.advance, finalize=handler.finalize,
            retention_claims=handler.retention_claims,
        ),
        replace=True,
    )
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    first = benchmark.capture_completed_run(
        scanner_run_id="legacy-units", population=population,
        allocated=population[:5], entry_ts=started_at,
    )

    kernel.register_handler(handler, replace=True)
    monkeypatch.setattr(
        kernel, "capture_event",
        lambda *_args, **_kwargs: pytest.fail("admitted legacy batch must not be re-appended"),
    )
    repeated = benchmark.capture_completed_run(
        scanner_run_id="legacy-units", population=population,
        allocated=population[:5], entry_ts=started_at,
    )

    assert first["accepted"] is True
    assert repeated["idempotent"] is True
    assert benchmark._admitted_snapshot(benchmark.status_benchmark(window["id"]))[2] == 0
def test_runtime_commit_change_is_capture_provenance_not_causal_identity(
    benchmark_db, monkeypatch
):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    commit_reads = 0

    def current_commit():
        nonlocal commit_reads
        commit_reads += 1
        return "commit-b"

    monkeypatch.setattr(benchmark, "_current_commit", current_commit)
    result = benchmark.capture_completed_run(
        scanner_run_id="scanner-run-2",
        population=_population(),
        allocated=[],
        entry_ts=started_at,
        regime_tag=None,
    )

    assert result["accepted"] is True
    assert commit_reads == 1
    status = benchmark.status_benchmark(window["id"], now=started_at)
    batches = repo.list_universe_discovery_batches(window["id"])
    outcomes = repo.list_universe_discovery_outcomes(window["id"])
    assert status["status"] == "active"
    assert status["spec_hash"] == window["spec_hash"]
    assert batches[0]["code_commit"] == "commit-b"
    assert {row["producer_commit"] for row in outcomes} == {"commit-b"}
    assert "commit-a" not in status["contract_json"]
    assert "commit-b" not in status["contract_json"]

    output = benchmark_db / "commit-drift.json.gz"
    benchmark.finalize_benchmark(
        window["id"], output, now="2026-01-16T06:00:00+00:00"
    )
    package = json.loads(gzip.decompress(output.read_bytes()))
    assert package["manifest"]["producer_commits"] == ["commit-a", "commit-b"]
    assert package["producer_provenance"]["commits"] == ["commit-a", "commit-b"]


def test_finalize_reports_unavailable_and_writes_hashed_package(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="scanner-run-3",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    output = benchmark_db / "universe-discovery.json.gz"

    with pytest.raises(ValueError, match="benchmark_finalize_not_due"):
        benchmark.finalize_benchmark(window["id"], output, now="2026-01-15T00:00:00+00:00")
    result = benchmark.finalize_benchmark(window["id"], output, now="2026-01-16T06:00:00+00:00")

    package = json.loads(gzip.decompress(output.read_bytes()))
    assert result["status"] == "finalized"
    assert result["analysis"]["conclusion"] == "unavailable"
    assert result["analysis"]["promotion_eligible"] is False
    assert package["manifest"]["contract_hash"] == window["contract_hash"]
    assert package["manifest"]["promotion_eligible"] is False
    assert len(package["population_batches"][0]["candidate_facts"]) == len(_population())
    assert result["artifact_sha256"]
    pending = next(row for row in repo.list_universe_discovery_outcomes(window["id"]) if row["status"] == "pending")
    assert repo.settle_filter_candidate_outcome(
        pending["id"], exit_price=101.0, settled_at="2026-01-16T06:00:01+00:00",
        economics={}, settlement_price_source="horizon_ohlcv", settlement_lag_sec=0.0,
    ) is False

def test_finalize_marks_orphan_app_batch_integrity_unavailable(benchmark_db, monkeypatch):
    from aletheia.observation_lab import kernel
    from aletheia.observation_lab.kernel import advance_windows

    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    monkeypatch.setattr(
        kernel, "capture_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("lab unavailable"))
    )
    with pytest.raises(RuntimeError, match="lab unavailable"):
        benchmark.capture_completed_run(
            scanner_run_id="orphan-final", population=_population(),
            allocated=_population()[:5], entry_ts=started_at,
        )

    advanced = advance_windows(now="2026-01-16T06:00:00+00:00")
    result = advanced["results"][f"{window['id']}:{window['generation']}"]
    status = benchmark.status_benchmark(window["id"])
    output = Path(status["artifact_path"])
    package = json.loads(gzip.decompress(output.read_bytes()))
    analysis = package["domain_result"]["analysis"]
    assert result["status"] == "finalized"
    assert analysis["conclusion"] == "unavailable"
    assert "orphan_app_batch" in analysis["integrity"]["reasons"]
    assert package["manifest"]["orphan_batch_count"] == 1

def test_finalize_waits_for_frozen_outcome_contract_readiness(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="not-ready",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    outcome = repo.list_universe_discovery_outcomes(window["id"])[0]
    repo.execute_write(
        "UPDATE filter_candidate_outcomes SET due_at=? WHERE id=?",
        ("2026-01-17T00:00:00+00:00", outcome["id"]),
    )

    progress = benchmark._handler_advance(benchmark.status_benchmark(window["id"]))
    assert progress.can_finalize is False
    assert progress.summary["outcome_contract_closed"] is False
    with pytest.raises(ValueError, match="window_handler_not_ready"):
        benchmark.finalize_benchmark(
            window["id"], benchmark_db / "not-ready.json.gz",
            now="2026-01-16T06:00:00+00:00",
        )


def test_migrated_handler_matches_legacy_evaluator_on_same_frozen_batches(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="differential-fixture",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    frozen_window = benchmark.status_benchmark(window["id"])
    batches = repo.list_universe_discovery_batches(window["id"])
    outcomes = repo.list_universe_discovery_outcomes(window["id"])
    legacy_payload = benchmark.analyze_benchmark(
        window["id"], snapshot=(frozen_window, batches, outcomes)
    )
    migrated_payload = benchmark._handler_finalize(frozen_window)["analysis"]

    assert migrated_payload == legacy_payload


def test_active_benchmark_outcomes_preempt_older_backlog(benchmark_db, monkeypatch):
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="scanner-run-priority",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    ordinary = FilterCandidateOutcome(
        outcome_candidate_key="ordinary-older-backlog",
        venue="okx",
        symbol="OLD/USDT",
        market_type="spot",
        direction="long",
        horizon="24h",
        entry_price_usd=100.0,
        entry_ts="2025-01-01T00:00:00+00:00",
        due_at="2025-01-02T00:00:00+00:00",
    )
    repo.insert_filter_candidate_outcomes_batch([ordinary])

    async def observations(rows, *, now):
        return {
            row["id"]: {
                "exit_price": 101.0,
                "range_high": 102.0,
                "range_low": 99.0,
                "price_source": "horizon_ohlcv",
                "price_quality_ok": True,
                "settlement_lag_sec": 0.0,
            }
            for row in rows
        }

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )
    assert asyncio.run(settle_due_filter_outcomes(max_rows=1)) == 1
    benchmark_rows = repo.list_universe_discovery_outcomes(window["id"])
    assert sum(row["status"] == "settled" for row in benchmark_rows) == 1
    pending = repo.list_pending_filter_candidate_outcomes(
        datetime.now(timezone.utc).isoformat(), limit=1000
    )
    assert ordinary.id in {row["id"] for row in pending}


def _insert_open_runtime_session(*, session_id: str, commit: str, started_at: datetime) -> None:
    from aletheia.storage.models import RuntimeSession

    repo.insert_runtime_session(
        RuntimeSession(
            session_id=session_id,
            boot_commit=commit,
            started_at=started_at.isoformat(),
        )
    )


def _mock_gateway_process(monkeypatch, *, started_at: datetime) -> None:
    import psutil

    from aletheia.gateway import pidfile

    class Process:
        def create_time(self):
            return started_at.timestamp()

        def children(self, *, recursive):
            assert recursive is True
            return []

    monkeypatch.setattr(pidfile, "read_pid", lambda: 1234)
    monkeypatch.setattr(pidfile, "is_running", lambda: True)
    monkeypatch.setattr(psutil, "Process", lambda pid: Process())


def test_cli_start_uses_verified_current_runtime_among_stale_open_sessions(
    benchmark_db, monkeypatch
):
    from datetime import timedelta

    from aletheia.agent_runtime import build_info
    from aletheia.cli.evidence_cmd import evidence_app

    current_started = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)
    _insert_open_runtime_session(
        session_id="current-session",
        commit="current-commit",
        started_at=current_started,
    )
    for index in range(34):
        _insert_open_runtime_session(
            session_id=f"stale-{index}",
            commit=f"stale-commit-{index}",
            started_at=current_started + timedelta(days=index + 1),
        )
    _mock_gateway_process(monkeypatch, started_at=current_started)
    monkeypatch.setattr(build_info, "get_boot_commit", lambda: None)
    monkeypatch.setattr(benchmark, "_current_commit", _REAL_CURRENT_COMMIT)

    started = CliRunner().invoke(
        evidence_app, ["universe-discovery", "start", "--json"]
    )

    assert started.exit_code == 0, started.output
    payload = json.loads(started.output)
    assert payload["code_commit"] == "current-commit"
    from aletheia.observation_lab.db import get_lab_db

    row = get_lab_db().execute(
        "SELECT producer_commit FROM observation_windows WHERE observation_id=?",
        (payload["id"],),
    ).fetchone()
    assert row["producer_commit"] == "current-commit"


def test_cli_start_fails_closed_when_external_runtime_cannot_be_verified(
    benchmark_db, monkeypatch
):
    from aletheia.agent_runtime import build_info
    from aletheia.cli.evidence_cmd import evidence_app

    stale_started = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)
    _insert_open_runtime_session(
        session_id="newest-stale",
        commit="newest-stale-commit",
        started_at=stale_started,
    )
    _mock_gateway_process(
        monkeypatch,
        started_at=datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(build_info, "get_boot_commit", lambda: None)
    monkeypatch.setattr(benchmark, "_current_commit", _REAL_CURRENT_COMMIT)

    started = CliRunner().invoke(
        evidence_app, ["universe-discovery", "start", "--json"]
    )

    assert started.exit_code != 0
    assert "runtime_boot_commit_unavailable" in started.output
    from aletheia.observation_lab.db import get_lab_db

    count = get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_windows"
    ).fetchone()[0]
    assert count == 0


def test_current_commit_prefers_in_process_boot_identity(monkeypatch):
    from aletheia.agent_runtime import build_info, runtime_session

    monkeypatch.setattr(build_info, "get_boot_commit", lambda: "gateway-boot-commit")
    monkeypatch.setattr(
        runtime_session,
        "current_external_gateway_session",
        lambda: pytest.fail("external identity must not be consulted"),
    )

    assert _REAL_CURRENT_COMMIT() == "gateway-boot-commit"
def test_cli_start_status_and_abort(benchmark_db):
    from aletheia.cli.evidence_cmd import evidence_app

    runner = CliRunner()
    started = runner.invoke(evidence_app, ["universe-discovery", "start", "--json"])
    assert started.exit_code == 0, started.output
    payload = json.loads(started.output)
    assert payload["phase"] == "collecting"
    assert payload["promotion_eligible"] is False

    status = runner.invoke(evidence_app, ["universe-discovery", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["id"] == payload["id"]

    aborted = runner.invoke(
        evidence_app,
        ["universe-discovery", "abort", "--reason", "operator test", "--json"],
    )
    assert aborted.exit_code == 0, aborted.output
    assert json.loads(aborted.output)["status"] == "aborted"


def test_service_capture_failure_is_nonfatal(monkeypatch, caplog):
    from aletheia.universe import service

    def fail(**_kwargs):
        raise RuntimeError("capture unavailable")

    monkeypatch.setattr(benchmark, "capture_completed_run", fail)
    service._capture_universe_discovery_nonfatal(
        scanner_run_id="run",
        population=[],
        allocated=[],
        entry_ts=datetime.now(timezone.utc).isoformat(),
        regime_tag=None,
        cfg=None,
    )
    assert "capture failed (non-fatal)" in caplog.text


def test_directional_annotations_and_absolute_return_ranks(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    benchmark.capture_completed_run(
        scanner_run_id="directional-annotations",
        population=population,
        allocated=population[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    facts = decode_filter_decision_batch(
        repo.list_universe_discovery_batches(window["id"])[0]["candidate_facts"]
    )
    assert all(isinstance(fact["direction_annotations"], dict) for fact in facts)
    for row in repo.list_universe_discovery_outcomes(window["id"]):
        path = json.loads(row["benchmark_path_facts"])
        if path.get("matched_pair_id"):
            assert path["matched_pair_role"] in {"selected", "control"}

    ranks = benchmark._rank_values(
        [
            {"venue": "okx", "market_type": "perp", "symbol": "A/USDT", "asset_id": "a", "return_4h": -9.0},
            {"venue": "okx", "market_type": "perp", "symbol": "B/USDT", "asset_id": "b", "return_4h": 2.0},
        ],
        "return_4h",
        absolute=True,
    )
    assert ranks[("okx", "perp", "A/USDT", "a")] > ranks[("okx", "perp", "B/USDT", "b")]


def test_holm_uses_frozen_twenty_one_hypothesis_family():
    results = {
        f"comparison-{index}": (
            {"available": True, "p_value": 0.01} if index == 0
            else {"available": False, "reason": "insufficient_complete_batches"}
        )
        for index in range(21)
    }
    benchmark._holm(results)
    assert results["comparison-0"]["holm_adjusted_p"] == pytest.approx(0.21)
    assert {result["holm_family_size"] for result in results.values()} == {21}
    assert results["comparison-1"]["holm_adjusted_p"] is None


def test_price_quality_failure_is_terminal_but_not_settled(benchmark_db, monkeypatch):
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    benchmark.capture_completed_run(
        scanner_run_id="bad-price-quality",
        population=population,
        allocated=population[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )

    async def observations(rows, *, now):
        return {
            row["id"]: {
                "exit_price": 101.0,
                "range_high": 102.0,
                "range_low": 99.0,
                "price_source": "horizon_ohlcv",
                "price_quality_ok": False,
                "settlement_lag_sec": 321.0,
            }
            for row in rows
        }

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )
    asyncio.run(settle_due_filter_outcomes(max_rows=1))
    rows = repo.list_universe_discovery_outcomes(window["id"])
    failed = [row for row in rows if row["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["settlement_lag_sec"] == 321.0
    assert sum(row["status"] == "settled" for row in rows) == 0


def test_analysis_reports_gaps_strata_costs_and_settlement(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    benchmark.capture_completed_run(
        scanner_run_id="report-contract",
        population=population,
        allocated=population[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    analysis = benchmark.analyze_benchmark(window["id"])
    assert analysis["capture_continuity"]["material_gap_detected"] is True
    assert "material_capture_continuity_gap" in analysis["integrity"]["reasons"]
    assert set(analysis["stratified_4h_summary"]) == {"direction", "market_type", "regime_tag"}
    assert set(analysis["costs"]) == {"assumptions", "selected_4h_mean_components"}
    assert set(analysis["settlement"]) == {"status_counts", "price_source_counts", "lag_sec"}
    assert "insufficient_matched_outcome_coverage" in analysis["integrity"]["reasons"]
def test_cluster_bootstrap_preserves_asset_level_estimand(monkeypatch):
    monkeypatch.setattr(benchmark, "MIN_INFERENCE_BATCHES", 2)
    monkeypatch.setattr(benchmark, "BOOTSTRAP_DRAWS", 100)
    result = benchmark._paired_inference(
        {"batch-a": [10.0], "batch-b": [0.0, 0.0, 0.0]},
        contract_hash="contract",
        comparison="asset-weighted",
    )
    assert result["complete_batches"] == 2
    assert result["complete_pairs"] == 4
    assert result["mean_difference_pct"] == 2.5


def test_capture_rejects_t0_before_preregistered_window(benchmark_db):
    window = benchmark.start_benchmark(now="2026-01-02T00:00:00+00:00", code_commit="commit-a")
    result = benchmark.capture_completed_run(
        scanner_run_id="predates-window",
        population=_population(),
        allocated=_population()[:5],
        entry_ts="2026-01-01T23:59:59+00:00",
        regime_tag="sideways",
    )
    assert result == {
        "accepted": False,
        "reason": "capture_before_window",
        "batch_id": result["batch_id"],
    }
    assert repo.list_universe_discovery_batches(window["id"]) == []


def test_analysis_uses_frozen_costs_and_gates_incomplete_family_winner(benchmark_db, monkeypatch):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="frozen-costs",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    batches = repo.list_universe_discovery_batches(window["id"])
    outcomes = repo.list_universe_discovery_outcomes(window["id"])
    for row in outcomes:
        row.update({
            "status": "settled",
            "exit_price_usd": float(row["entry_price_usd"]) * 1.01,
            "settlement_price_source": "horizon_ohlcv",
            "estimated_fee_pct": 99.0,
            "estimated_spread_slippage_pct": 99.0,
            "estimated_funding_pct": 99.0,
        })

    def favorable(_differences, *, contract_hash, comparison):
        return {
            "available": True,
            "complete_batches": 40,
            "mean_difference_pct": 1.0,
            "ci95": [0.5, 1.5],
            "p_value": 0.001,
        }

    def significant(results):
        for result in results.values():
            result["holm_adjusted_p"] = 0.001

    monkeypatch.setattr(benchmark, "_paired_inference", favorable)
    monkeypatch.setattr(benchmark, "_holm", significant)
    analysis = benchmark.analyze_benchmark(window["id"], snapshot=(window, batches, outcomes))
    components = analysis["costs"]["selected_4h_mean_components"]
    assert all(value != 99.0 for value in components.values())
    assert analysis["integrity"]["complete"] is False
    assert analysis["family_winner"] == {"status": "no_supported_winner"}


def test_finalize_cleans_artifact_when_transaction_snapshot_fails(benchmark_db, monkeypatch):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="finalize-race",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    original = repo.list_universe_discovery_outcomes
    calls = 0

    def changing(window_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("snapshot read failed")
        return original(window_id)

    monkeypatch.setattr(repo, "list_universe_discovery_outcomes", changing)
    output = benchmark_db / "raced.json.gz"
    with pytest.raises(RuntimeError, match="snapshot read failed"):
        benchmark.finalize_benchmark(window["id"], output, now="2026-01-16T06:00:00+00:00")
    assert output.exists() is False
    assert benchmark.status_benchmark(window["id"])["status"] == "active"


def test_finalize_uses_durable_start_provenance_when_runtime_commit_is_unavailable(
    benchmark_db, monkeypatch
):
    window = benchmark.start_benchmark(now="2026-01-01T00:00:00+00:00", code_commit="commit-a")
    output = benchmark_db / "missing-commit.json.gz"
    monkeypatch.setattr(benchmark, "_current_commit", lambda: None)
    result = benchmark.finalize_benchmark(
        window["id"], output, now="2026-01-16T06:00:00+00:00"
    )
    package = json.loads(gzip.decompress(output.read_bytes()))
    assert result["status"] == "finalized"
    assert package["manifest"]["producer_commits"] == ["commit-a"]
def test_cli_finalize_copies_runner_canonical_artifact_without_reanalysis(benchmark_db, monkeypatch):
    from aletheia.cli.evidence_cmd import evidence_app
    from aletheia.observation_lab.kernel import advance_windows

    window = benchmark.start_benchmark(
        now="2026-01-01T00:00:00+00:00", code_commit="commit-a"
    )
    advanced = advance_windows(now="2026-01-16T06:00:00+00:00")
    assert advanced["results"][f"{window['id']}:{window['generation']}"]["status"] == "finalized"
    canonical_status = benchmark.status_benchmark(window["id"])
    canonical = Path(canonical_status["artifact_path"])
    monkeypatch.setattr(
        benchmark, "analyze_benchmark",
        lambda *_args, **_kwargs: pytest.fail("idempotent finalize must not re-analyze"),
    )
    requested = benchmark_db / "operator-requested.json.gz"
    cli = CliRunner().invoke(
        evidence_app,
        [
            "universe-discovery", "finalize", "--id", window["id"],
            "--output", str(requested), "--json",
        ],
    )
    assert cli.exit_code == 0, cli.output
    result = json.loads(cli.output)

    assert result["status"] == "finalized"
    assert requested.read_bytes() == canonical.read_bytes()
    assert hashlib.sha256(requested.read_bytes()).hexdigest() == result["artifact_sha256"]


def test_cli_finalize_decodes_canonical_gzip_to_requested_json(benchmark_db):
    from aletheia.cli.evidence_cmd import evidence_app
    from aletheia.observation_lab.kernel import advance_windows

    window = benchmark.start_benchmark(
        now="2026-01-01T00:00:00+00:00", code_commit="commit-a"
    )
    advanced = advance_windows(now="2026-01-16T06:00:00+00:00")
    assert advanced["results"][f"{window['id']}:{window['generation']}"]["status"] == "finalized"
    requested = benchmark_db / "operator-requested.json"
    cli = CliRunner().invoke(
        evidence_app,
        [
            "universe-discovery", "finalize", "--id", window["id"],
            "--output", str(requested), "--json",
        ],
    )
    assert cli.exit_code == 0, cli.output
    result = json.loads(cli.output)
    canonical = Path(result["artifact_path"])
    assert canonical.suffix == ".gz"
    assert gzip.decompress(canonical.read_bytes()) == requested.read_bytes()
    assert json.loads(requested.read_text())["observation_id"] == window["id"]
def test_inactive_benchmark_rows_leave_general_settlement_queue(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="inactive-queue",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    benchmark.abort_benchmark(window["id"], reason="test", now="2026-01-02T00:00:00+00:00")
    pending = repo.list_pending_filter_candidate_outcomes("2026-02-01T00:00:00+00:00", limit=1000)
    assert not {row["benchmark_window_id"] for row in pending} - {None}


def test_capture_preserves_source_specific_regime_and_durable_session_only(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population(2)
    population[0]["analysis_context"] = {"kind": "crypto"}
    population[1]["analysis_context"] = {"kind": "equity"}
    benchmark.capture_completed_run(
        scanner_run_id="source-semantics",
        population=population,
        allocated=population,
        entry_ts=started_at,
        regime_tag="sideways",
    )
    rows = repo.list_universe_discovery_outcomes(window["id"])
    by_asset = {}
    for row in rows:
        by_asset.setdefault(json.loads(row["benchmark_path_facts"])["asset_id"], row)
    assert by_asset["asset-0"]["regime_tag"] == "sideways"
    assert by_asset["asset-1"]["regime_tag"] is None
    assert all(row["producer_runtime_session_id"] is None for row in rows)


def test_matching_rejects_invalid_control_price():
    population = _population(2)
    selected_identity = benchmark._identity(population[0])
    population[1]["last_price"] = 0.0
    memberships, annotations = benchmark._cohort_memberships(
        population,
        allocated=[population[0]],
        audits={
            benchmark._identity(item): {
                "family_evidence": {family: {"applicability": "not_applicable"} for family in benchmark.FAMILIES}
            }
            for item in population
        },
        max_candidates=2,
    )
    assert "matched_non_selected" not in memberships.get(
        (benchmark._identity(population[1]), population[0]["scanner_direction"]), set()
    )
    assert "matched_pair_id" not in annotations.get((selected_identity, population[0]["scanner_direction"]), {})


def test_analysis_excludes_partial_family_batch_and_keeps_selected_asset_weights(benchmark_db, monkeypatch):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="inference-inputs",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    batches = repo.list_universe_discovery_batches(window["id"])
    outcomes = repo.list_universe_discovery_outcomes(window["id"])
    family_labels = [f"family:{family}" for family in benchmark.FAMILIES] + ["simple_momentum"]
    target = next(
        row
        for row in outcomes
        if row["horizon"] == "4h"
        and "production_selected" not in json.loads(row["benchmark_cohorts"])
        and any(label in family_labels for label in json.loads(row["benchmark_cohorts"]))
    )
    target_label = next(label for label in json.loads(target["benchmark_cohorts"]) if label in family_labels)
    other_label = next(label for label in family_labels if label != target_label)
    ordered = sorted((target_label, other_label), key=family_labels.index)
    comparison = f"{ordered[0]}_vs_{ordered[1]}"
    for row in outcomes:
        if row is target or row["horizon"] != "4h":
            continue
        row.update(
            status="settled",
            exit_price_usd=float(row["entry_price_usd"]) * 1.01,
            settlement_price_source="horizon_ohlcv",
        )

    calls = {}

    def capture_inputs(differences, *, contract_hash, comparison):
        calls[comparison] = differences
        return {"available": False, "reason": "test"}

    monkeypatch.setattr(benchmark, "MIN_INFERENCE_BATCHES", 1)
    monkeypatch.setattr(benchmark, "_paired_inference", capture_inputs)
    benchmark.analyze_benchmark(window["id"], snapshot=(window, batches, outcomes))
    assert calls[comparison] == {}
    selected_values = next(iter(calls["selected_vs_zero"].values()))
    assert len(selected_values) == sum(
        row["horizon"] == "4h" and "production_selected" in json.loads(row["benchmark_cohorts"]) for row in outcomes
    )
def test_matching_percentiles_are_tie_aware():
    rows = _population(3)
    rows[0]["spread_bps"] = rows[1]["spread_bps"] = 7.0
    rows[2]["spread_bps"] = 9.0
    ranks = benchmark._rank_values(rows, "spread_bps")
    assert ranks[benchmark._identity(rows[0])] == ranks[benchmark._identity(rows[1])]
    assert ranks[benchmark._identity(rows[1])] < ranks[benchmark._identity(rows[2])]


def test_seeded_bootstrap_is_independent_of_batch_insertion_order(monkeypatch):
    monkeypatch.setattr(benchmark, "MIN_INFERENCE_BATCHES", 2)
    monkeypatch.setattr(benchmark, "BOOTSTRAP_DRAWS", 100)
    first = benchmark._paired_inference({"batch-b": [2.0], "batch-a": [-1.0]}, contract_hash="contract", comparison="stable-order")
    second = benchmark._paired_inference({"batch-a": [-1.0], "batch-b": [2.0]}, contract_hash="contract", comparison="stable-order")
    assert first == second


def test_stratified_summary_is_separated_by_cohort(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    benchmark.capture_completed_run(scanner_run_id="cohort-strata", population=population, allocated=population[:5], entry_ts=started_at, regime_tag="sideways")
    strata = benchmark.analyze_benchmark(window["id"])["stratified_4h_summary"]
    assert "production_selected" in strata["direction"]
    assert "family:momentum" in strata["direction"]
    selected_count = sum(item["intended"] for item in strata["direction"]["production_selected"].values())
    family_count = sum(item["intended"] for item in strata["direction"]["family:momentum"].values())
    assert selected_count == 5
    assert family_count != selected_count


def test_finalize_contract_drift_persists_invalidation(benchmark_db, monkeypatch):
    window = benchmark.start_benchmark(now="2026-01-01T00:00:00+00:00", code_commit="commit-a")
    original = benchmark.build_contract
    def changed_contract(**kwargs):
        contract = original(**kwargs)
        contract["capture"]["days"] += 1
        return contract
    monkeypatch.setattr(benchmark, "build_contract", changed_contract)
    with pytest.raises(ValueError, match="benchmark_frozen_contract_drift"):
        benchmark.finalize_benchmark(window["id"], benchmark_db / "drift.json.gz", now="2026-01-16T06:00:00+00:00")
    persisted = benchmark.status_benchmark(window["id"])
    assert persisted["status"] == "invalidated"
    assert persisted["terminal_reason"] == "frozen_contract_drift_at_finalization"


def test_finalized_manifest_unions_capture_and_settlement_sessions(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(scanner_run_id="runtime-sessions", population=_population(), allocated=_population()[:5], entry_ts=started_at, regime_tag="sideways")
    outcome = repo.list_universe_discovery_outcomes(window["id"])[0]
    repo.execute_write("UPDATE filter_candidate_outcomes SET producer_runtime_session_id=?, settlement_runtime_session_id=? WHERE id=?", ("capture-session", "settlement-session", outcome["id"]))
    output = benchmark_db / "runtime-sessions.json.gz"
    benchmark.finalize_benchmark(window["id"], output, now="2026-01-16T06:00:00+00:00")
    package = json.loads(gzip.decompress(output.read_bytes()))
    assert package["manifest"]["runtime_session_ids"] == ["capture-session", "settlement-session"]


def test_legacy_cutover_failure_compensates_new_window(benchmark_db, monkeypatch):
    legacy = repo.insert_universe_discovery_benchmark({
        "id": "legacy-cutover-failure", "status": "active",
        "contract_json": "{}", "contract_hash": "a" * 64,
        "code_commit": "legacy", "started_at": "2025-12-01T00:00:00+00:00",
        "capture_ends_at": "2025-12-15T00:00:00+00:00",
        "finalize_not_before": "2025-12-16T06:00:00+00:00",
        "created_at": "2025-12-01T00:00:00+00:00",
    })
    monkeypatch.setattr(repo, "invalidate_universe_discovery_benchmark", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="legacy_benchmark_cutover_failed"):
        benchmark.start_benchmark(now="2026-01-01T00:00:00+00:00", code_commit="new")

    assert repo.get_universe_discovery_benchmark(legacy["id"])["status"] == "active"
    from aletheia.observation_lab.db import get_lab_db
    generic = dict(get_lab_db().execute(
        "SELECT * FROM observation_windows WHERE slot_key='universe-discovery'"
    ).fetchone())
    assert generic["status"] == "invalidated"
    assert generic["terminal_reason"] == "legacy_cutover_failed"


def test_idempotent_capture_uses_persisted_provenance_and_evidence_time(benchmark_db, monkeypatch):
    from aletheia.agent_runtime import runtime_session
    from aletheia.observation_lab.db import get_lab_db

    started_at = "2026-01-01T00:00:00+00:00"
    known_at = "2026-01-01T04:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="start")
    monkeypatch.setattr(runtime_session, "current_provenance", lambda: {"runtime_session_id": "original-session"})
    monkeypatch.setattr(benchmark, "_current_commit", lambda: "original-commit")
    batch, outcomes = benchmark.build_capture(
        window=window, scanner_run_id="retry-provenance", population=_population(),
        allocated=[], entry_ts=known_at, regime_tag=None, producer_commit="original-commit",
    )
    first = repo.capture_universe_discovery_batch(
        window["id"], batch, outcomes, captured_at=known_at,
        cadence_sec=benchmark.CADENCE_SEC, max_batches=benchmark.MAX_BATCHES,
        window_snapshot=window,
    )
    monkeypatch.setattr(runtime_session, "current_provenance", lambda: {"runtime_session_id": "retry-session"})
    monkeypatch.setattr(benchmark, "_current_commit", lambda: "retry-commit")

    second = benchmark.capture_completed_run(
        scanner_run_id="retry-provenance", population=_population(), allocated=[],
        entry_ts=known_at, regime_tag=None,
    )

    assert second == {"accepted": True, "idempotent": True, "reason": "already_captured", "batch_id": first["batch_id"]}
    units = get_lab_db().execute(
        "SELECT DISTINCT producer_commit,producer_runtime_session_id,known_at FROM observation_units"
    ).fetchall()
    assert {tuple(row) for row in units} == {("original-commit", "original-session", known_at)}
    assert {claim["earliest_at"] for claim in benchmark._retention_claims(
        benchmark.status_benchmark(window["id"])
    )} == {known_at}


@pytest.mark.asyncio
async def test_independent_scheduler_settler_obeys_observation_cycle_lock(benchmark_db, monkeypatch):
    from threading import Event, Thread

    from aletheia.agent_runtime import scheduler
    from aletheia.observation_lab.worker_lock import (
        WorkerCycleLockError, worker_cycle_exclusion,
    )
    from aletheia.universe import outcome_recorder

    window = benchmark.start_benchmark(now="2026-01-01T00:00:00+00:00", code_commit="a")
    benchmark.capture_completed_run(
        scanner_run_id="scheduler-lock", population=_population(), allocated=[],
        entry_ts="2026-01-01T00:00:00+00:00", regime_tag=None,
    )
    outcome = repo.list_universe_discovery_outcomes(window["id"])[0]
    acquired = Event()
    release = Event()

    def holder():
        with worker_cycle_exclusion():
            acquired.set()
            assert release.wait(2)

    thread = Thread(target=holder)
    thread.start()
    assert acquired.wait(2)

    async def settle_due():
        return int(repo.fail_filter_candidate_outcome(
            outcome["id"], "2026-01-01T01:00:00+00:00"
        ))

    monkeypatch.setattr(outcome_recorder, "settle_due", settle_due)
    with pytest.raises(WorkerCycleLockError):
        await scheduler._async_settle_outcomes()
    release.set()
    thread.join(2)
    await scheduler._async_settle_outcomes()
    assert repo._select_one("filter_candidate_outcomes", outcome["id"])["status"] == "failed"
