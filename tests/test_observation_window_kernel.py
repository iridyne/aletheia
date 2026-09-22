from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("observation_readers")


def _utc(day: int = 1) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


def _closed_bars(*, gapped: bool = False) -> list[dict]:
    bars = []
    offsets = (0.0, 0.8, 1.6, 0.8, 0.0)
    for index in range(40):
        center = 100.0 + index * 0.2
        offset = offsets[index % len(offsets)]
        bars.append(
            {
                "ts": index * 60_000 + (1 if gapped and index == 20 else 0),
                "open": center,
                "high": center + 2.0 + offset,
                "low": center - 2.0 - offset,
                "close": center + 0.1,
                "volume": 10.0,
            }
        )
    return bars


def test_event_captured_universe_shape_keeps_complete_cohort_and_commit_is_provenance(
    tmp_path, monkeypatch
):
    import aletheia.config.loader as loader
    from aletheia.observation_lab.kernel import (
        ObservationHandler,
        ObservationProgress,
        active_retention_claims,
        capture_event,
        invalidate_active_for_restore,
        register_handler,
        start_window,
        status_window,
    )
    from aletheia.observation_lab.db import get_lab_db, reset_lab_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()

    def normalize(spec):
        return {
            key: spec[key]
            for key in (
                "population_contract",
                "scanner_version",
                "allocator_version",
                "outcome_version",
                "cost_version",
                "stopping_rule",
            )
        }

    def capture(_window, event):
        batch = event["batch_id"]
        return ObservationProgress(
            summary={"batch_id": batch, "population_size": len(event["population"])},
            units=tuple(
                {
                    "unit_key": f"{batch}:{row['symbol']}",
                    "source_kind": "universe_candidate",
                    "source_ref": f"filter_decision_batch:{batch}:{row['symbol']}",
                    "source_hash": row["source_hash"],
                    "known_at": event["known_at"],
                    "admission": "admitted",
                    "reason": "selected" if row["selected"] else "unselected_control",
                }
                for row in event["population"]
            ),
        )

    register_handler(
        ObservationHandler(
            kind="test_universe_cohort",
            version="test-universe/v1",
            normalize_spec=normalize,
            capture=capture,
            advance=lambda _window: ObservationProgress(summary={}),
            finalize=lambda _window: {"shape": "universe"},
            retention_claims=lambda window: (
                {"domain": "filter_decision_batches", "earliest_at": window["started_at"]},
            ),
        ),
        replace=True,
    )
    spec = {
        "population_contract": "complete_post_dedup_pre_allocation/v1",
        "scanner_version": "scanner/v1",
        "allocator_version": "allocator/v1",
        "outcome_version": "four-horizon/v1",
        "cost_version": "cost/v1",
        "stopping_rule": {"days": 14, "early_stopping": False},
    }
    start_args = {
        "observation_id": "universe-shape",
        "kind": "test_universe_cohort",
        "handler_version": "test-universe/v1",
        "slot_key": "universe-discovery",
        "authority_mode": "observe_only",
        "spec": spec,
        "started_at": _utc().isoformat(),
        "collect_until": (_utc() + timedelta(days=14)).isoformat(),
        "finalize_not_before": (_utc() + timedelta(days=15, hours=6)).isoformat(),
    }
    first = start_window(**start_args, producer_commit="a" * 40)
    same = start_window(
        **{**start_args, "observation_id": "retry-generated-another-id"},
        producer_commit="b" * 40,
    )
    assert same["observation_id"] == first["observation_id"]
    assert same["generation"] == first["generation"]
    assert same["spec_hash"] == first["spec_hash"]
    event = {
        "batch_id": "batch-1",
        "known_at": (_utc() + timedelta(hours=4)).isoformat(),
        "population": [
            {"symbol": "BTC/USDT", "selected": True, "source_hash": "1" * 64},
            {"symbol": "ETH/USDT", "selected": False, "source_hash": "2" * 64},
            {"symbol": "SOL/USDT", "selected": False, "source_hash": "3" * 64},
        ],
    }
    captured = capture_event(
        first["observation_id"], first["generation"], first["spec_hash"], event,
        producer_commit="b" * 40, producer_runtime_session_id="runtime-2",
    )
    repeated = capture_event(
        first["observation_id"], first["generation"], first["spec_hash"], event,
        producer_commit="c" * 40, producer_runtime_session_id="runtime-3",
    )
    conflicting_event = {
        **event,
        "population": [{**row, "source_hash": "f" * 64} for row in event["population"]],
    }
    with pytest.raises(ValueError, match="unit_identity_conflict"):
        capture_event(
            first["observation_id"], first["generation"], first["spec_hash"],
            conflicting_event,
        )
    distinct_event = {
        **event,
        "batch_id": "batch-2",
        "known_at": (_utc() + timedelta(hours=8)).isoformat(),
    }
    distinct = capture_event(
        first["observation_id"], first["generation"], first["spec_hash"], distinct_event,
        producer_commit="d" * 40, producer_runtime_session_id="runtime-4",
    )
    status = status_window(first["observation_id"], first["generation"])

    assert captured["inserted"] == 3
    assert repeated["inserted"] == 0
    assert distinct["inserted"] == 3
    assert status["unit_counts"] == {"admitted": 6}
    claims = active_retention_claims()
    assert claims == {
        "available": True,
        "unavailable_windows": [],
        "claims": {"filter_decision_batches": _utc().isoformat()},
    }
    with pytest.raises(sqlite3.IntegrityError, match="observation_windows identity is immutable"):
        get_lab_db().execute(
            "UPDATE observation_windows SET spec_json='{}' WHERE observation_id=? AND generation=?",
            (first["observation_id"], first["generation"]),
        )
    explicit = start_window(
        **{**start_args, "observation_id": "explicit-generation"},
        producer_commit="d" * 40,
        new_generation=True,
    )
    assert explicit["generation"] == first["generation"] + 1
    assert status_window(first["observation_id"], first["generation"])["status"] == "invalidated"
    replacement = start_window(
        **{**start_args, "spec": {**spec, "stopping_rule": {"days": 21}}},
        producer_commit="e" * 40,
    )
    assert replacement["generation"] == explicit["generation"] + 1
    superseded = status_window(explicit["observation_id"], explicit["generation"])
    assert superseded["status"] == "invalidated"
    assert superseded["terminal_reason"] == f"superseded_by_generation:{replacement['generation']}"
    assert invalidate_active_for_restore(joint_cut_proven=False) == 1
    restored = status_window(replacement["observation_id"], replacement["generation"])
    assert restored["status"] == "invalidated"
    assert restored["terminal_reason"] == "restore_continuity_unproven"
    reset_lab_db()



def test_missing_handler_fails_closed_for_runner_status_and_retention(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.observation_lab.kernel as kernel
    from aletheia.observation_lab.db import reset_lab_db
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()
    handler = kernel.ObservationHandler(
        kind="restart-only-handler",
        version="v1",
        normalize_spec=lambda spec: spec,
        capture=None,
        advance=lambda _window: kernel.ObservationProgress(summary={}, can_finalize=False),
        finalize=lambda _window: {},
        retention_claims=lambda window: (
            {"domain": "source_rows", "earliest_at": window["started_at"]},
        ),
    )
    kernel.register_handler(handler, replace=True)
    window = kernel.start_window(
        observation_id="missing-after-restart",
        kind=handler.kind,
        handler_version=handler.version,
        slot_key="missing-handler-slot",
        authority_mode="observe_only",
        spec={"contract": "v1"},
        started_at=_utc(),
        collect_until=_utc() + timedelta(days=1),
        finalize_not_before=_utc() + timedelta(days=2),
    )
    removed = kernel._HANDLERS.pop((handler.kind, handler.version))
    try:
        status = kernel.status_window(window["observation_id"], window["generation"])
        advanced = kernel.advance_windows(now=_utc() + timedelta(hours=1))
        claims = kernel.active_retention_claims()
        assert status["phase"] == "handler_unavailable"
        assert advanced["results"]["missing-after-restart:1"]["status"] == "unavailable"
        assert claims["available"] is False
        assert claims["unavailable_windows"] == ["missing-after-restart:1"]
        monkeypatch.setattr(
            repo, "_delete_by_rowid_batches",
            lambda *_args, **_kwargs: pytest.fail("cleanup must not delete without retention resolution"),
        )
        with pytest.raises(repo.ExperimentStateReadError, match="retention claims unavailable"):
            repo.prune_filter_decision_batches(days=30)
    finally:
        kernel._HANDLERS[(handler.kind, handler.version)] = removed
        reset_lab_db()


def test_finalization_publishes_immutable_lab_result_with_source_cut(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.observation_lab.db import get_lab_db, reset_lab_db
    from aletheia.observation_lab.kernel import (
        ObservationHandler,
        ObservationProgress,
        finalize_window,
        register_handler,
        start_window,
    )

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()
    register_handler(ObservationHandler(
        kind="result-window",
        version="v1",
        normalize_spec=lambda spec: spec,
        capture=None,
        advance=lambda _window: ObservationProgress(summary={"ready": True}, can_finalize=True),
        finalize=lambda _window: {"verdict": "neutral", "reason": "no economic claim"},
        retention_claims=lambda _window: (),
    ), replace=True)
    window = start_window(
        observation_id="result-window",
        kind="result-window",
        handler_version="v1",
        slot_key="result-slot",
        authority_mode="observe_only",
        spec={"contract": "v1"},
        started_at=_utc(),
        collect_until=_utc(2),
        finalize_not_before=_utc(3),
    )
    finalize_window(
        window["observation_id"], window["generation"],
        now=_utc(4), output=tmp_path / "result.json.gz",
    )
    result = get_lab_db().execute(
        "SELECT verdict, disposition, source_cutoff, evaluation_id FROM observation_results "
        "WHERE observation_id='result-window'"
    ).fetchone()
    assert tuple(result)[:2] == ("neutral", "finalized")
    assert '"main_import_contiguous_sequence":null' in result["source_cutoff"]
    assert result["evaluation_id"]
    reset_lab_db()


def test_runner_checkpoint_mismatch_is_diagnostic_only(tmp_path, monkeypatch):
    from aletheia.observation_lab.checkpoint import CheckpointVerification

    import aletheia.config.loader as loader
    import aletheia.observation_lab.checkpoint as checkpoint
    import aletheia.observation_lab.runner as runner
    from aletheia.observation_lab.db import reset_lab_db
    from aletheia.observation_lab.kernel import (
        ObservationHandler, ObservationProgress, register_handler, start_window, status_window,
    )

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()
    register_handler(ObservationHandler(
        kind="restore-window", version="v1", normalize_spec=lambda spec: spec,
        capture=None, advance=lambda _window: ObservationProgress(summary={}),
        finalize=lambda _window: {}, retention_claims=lambda _window: (),
    ), replace=True)
    window = start_window(
        observation_id="restore-window", kind="restore-window", handler_version="v1",
        slot_key="restore-slot", authority_mode="observe_only", spec={"contract": "v1"},
        started_at=_utc(), collect_until=_utc(2), finalize_not_before=_utc(3),
    )
    monkeypatch.setattr(
        checkpoint, "verify_joint_checkpoint",
        lambda **_: CheckpointVerification(
            ok=False, reason="joint_fingerprint_mismatch", last=None, current_fingerprint="new",
        ),
    )

    summary = runner._pass_checkpoint()

    assert summary["ok"] is False
    assert summary["reason"] == "joint_fingerprint_mismatch"
    status = status_window(window["observation_id"], window["generation"])
    assert status["status"] == "active"
    assert status["terminal_reason"] is None
    reset_lab_db()

def test_explicit_unproven_restore_invalidates_active_windows(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.observation_lab.db import reset_lab_db
    from aletheia.observation_lab.kernel import (
        ObservationHandler, ObservationProgress, invalidate_active_for_restore,
        register_handler, start_window, status_window,
    )

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()
    register_handler(ObservationHandler(
        kind="restore-contract", version="v1", normalize_spec=lambda spec: spec,
        capture=None, advance=lambda _window: ObservationProgress(summary={}),
        finalize=lambda _window: {}, retention_claims=lambda _window: (),
    ), replace=True)
    window = start_window(
        observation_id="restore-contract", kind="restore-contract", handler_version="v1",
        slot_key="restore-contract-slot", authority_mode="observe_only",
        spec={"contract": "v1"}, started_at=_utc(), collect_until=_utc(2),
        finalize_not_before=_utc(3),
    )

    assert invalidate_active_for_restore(joint_cut_proven=False) == 1
    status = status_window(window["observation_id"], window["generation"])
    assert status["status"] == "invalidated"
    assert status["terminal_reason"] == "restore_continuity_unproven"
    reset_lab_db()
def test_proven_joint_cut_preserves_active_windows(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.observation_lab.db import reset_lab_db
    from aletheia.observation_lab.kernel import (
        ObservationHandler, ObservationProgress, invalidate_active_for_restore,
        register_handler, start_window, status_window,
    )

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()
    register_handler(ObservationHandler(
        kind="proven-restore", version="v1", normalize_spec=lambda spec: spec,
        capture=None, advance=lambda _window: ObservationProgress(summary={}),
        finalize=lambda _window: {}, retention_claims=lambda _window: (),
    ), replace=True)
    window = start_window(
        observation_id="proven-restore", kind="proven-restore", handler_version="v1",
        slot_key="proven-restore-slot", authority_mode="observe_only", spec={"v": 1},
        started_at=_utc(), collect_until=_utc(2), finalize_not_before=_utc(3),
    )

    assert invalidate_active_for_restore(joint_cut_proven=True) == 0
    assert status_window(window["observation_id"], window["generation"])["status"] == "active"
    reset_lab_db()


def test_finalize_recovers_verified_artifact_left_before_terminal_commit(tmp_path, monkeypatch):
    import gzip
    from contextlib import contextmanager

    import aletheia.config.loader as loader
    import aletheia.observation_lab.kernel as kernel
    from aletheia.observation_lab.db import reset_lab_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    domain_result = {"result": "first-attempt"}
    reset_lab_db()
    kernel.register_handler(kernel.ObservationHandler(
        kind="crash-recovery", version="v1", normalize_spec=lambda spec: spec,
        capture=None,
        advance=lambda _window: kernel.ObservationProgress(
            summary={"ready": True}, can_finalize=True
        ),
        finalize=lambda _window: dict(domain_result), retention_claims=lambda _window: (),
    ), replace=True)
    window = kernel.start_window(
        observation_id="crash-recovery", kind="crash-recovery", handler_version="v1",
        slot_key="crash-recovery-slot", authority_mode="observe_only", spec={"v": 1},
        started_at=_utc(), collect_until=_utc(2), finalize_not_before=_utc(3),
    )
    output = tmp_path / "orphan.json.gz"
    real_transaction = kernel.lab_transaction

    @contextmanager
    def crash_before_commit(*args, **kwargs):
        with real_transaction(*args, **kwargs) as db:
            yield db
            raise SystemExit("simulated process exit")

    monkeypatch.setattr(kernel, "lab_transaction", crash_before_commit)
    with pytest.raises(SystemExit, match="simulated process exit"):
        kernel.finalize_window(
            window["observation_id"], window["generation"], now=_utc(3), output=output,
        )
    reset_lab_db()
    assert output.exists()
    assert kernel.status_window(window["observation_id"], window["generation"])["status"] == "active"
    domain_result["result"] = "reconciled"

    monkeypatch.setattr(kernel, "lab_transaction", real_transaction)
    finalized = kernel.finalize_window(
        window["observation_id"], window["generation"], now=_utc(4), output=output,
    )
    assert finalized["status"] == "finalized"
    assert finalized["terminal_at"] == _utc(4).isoformat()
    artifact = json.loads(gzip.decompress(output.read_bytes()))
    assert artifact["finalized_at"] == _utc(4).isoformat()
    assert artifact["domain_result"] == {"result": "reconciled"}

    foreign = kernel.start_window(
        observation_id="foreign-artifact", kind="crash-recovery", handler_version="v1",
        slot_key="foreign-artifact-slot", authority_mode="observe_only", spec={"v": 1},
        started_at=_utc(), collect_until=_utc(2), finalize_not_before=_utc(3),
    )
    foreign_output = tmp_path / "foreign.json.gz"
    foreign_bytes = gzip.compress(
        json.dumps({
            "observation_id": "another-window", "generation": foreign["generation"],
            "kind": "crash-recovery", "handler_version": "v1",
            "spec_hash": foreign["spec_hash"],
        }).encode(),
        mtime=0,
    )
    foreign_output.write_bytes(foreign_bytes)
    with pytest.raises(FileExistsError):
        kernel.finalize_window(
            foreign["observation_id"], foreign["generation"], now=_utc(4),
            output=foreign_output,
        )
    assert foreign_output.read_bytes() == foreign_bytes
    assert kernel.status_window(foreign["observation_id"], foreign["generation"])["status"] == "active"
    reset_lab_db()

def test_real_smc_shape_uses_causal_v1_unavailable_no_trigger_and_external_authority(
    tmp_path, monkeypatch
):
    from aletheia.data.market_structure import CORE_ALGORITHM_VERSION, PARAMS_HASH
    from aletheia.universe.market_structure import evaluate_market_structure

    no_trigger = evaluate_market_structure(_closed_bars(), direction="long")
    unavailable = evaluate_market_structure(_closed_bars(gapped=True), direction="long")
    assert no_trigger["core_algorithm_version"] == "causal-market-structure/v1"
    assert no_trigger["event"] is None
    assert unavailable["reason"] == "candle_gap"

    import aletheia.config.loader as loader
    from aletheia.observation_lab.db import reset_lab_db
    from aletheia.observation_lab.kernel import (
        ObservationHandler,
        ObservationProgress,
        capture_event,
        finalize_window,
        register_handler,
        start_window,
        status_window,
    )

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()
    observed: list[dict] = []

    def normalize(spec):
        return {
            key: spec[key]
            for key in (
                "instrument", "timeframe", "control", "treatment",
                "core_algorithm_version", "params_hash", "external_authority_ref",
                "outcome", "cost",
            )
        }

    def capture(_window, event):
        result = evaluate_market_structure(event["bars"], direction=event["direction"])
        observed.append(result)
        admission = "unavailable" if result["verdict"] == "unavailable" else "admitted"
        reason = result["reason"] if admission == "unavailable" else (
            "no_trigger" if result.get("event") is None else "trigger"
        )
        return ObservationProgress(
            summary={"core_algorithm_version": result["core_algorithm_version"]},
            units=(
                {
                    "unit_key": event["candidate_key"],
                    "source_kind": "candidate_market_structure_pair",
                    "source_ref": (
                        f"candidate:{event['candidate_key']}:control=candidate-price/v1:"
                        f"treatment={result['core_algorithm_version']}"
                    ),
                    "source_hash": result.get("input_fingerprint") or "0" * 64,
                    "known_at": event["known_at"],
                    "admission": admission,
                    "reason": reason,
                },
            ),
        )

    def finalize(_window):
        return {
            "denominator": len(observed),
            "paired_coverage": sum(row["verdict"] != "unavailable" for row in observed),
            "net_result_pct": 0.0,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "adverse_tail_pct": 0.0,
            "exclusions": sum(row["verdict"] == "unavailable" for row in observed),
        }

    register_handler(
        ObservationHandler(
            kind="test_smc_pair",
            version="test-smc-handler/v1",
            normalize_spec=normalize,
            capture=capture,
            advance=lambda _window: ObservationProgress(
                summary={"denominator": len(observed)}, can_finalize=len(observed) == 2
            ),
            finalize=finalize,
            retention_claims=lambda window: (
                {"domain": "market_candles", "earliest_at": window["started_at"]},
            ),
        ),
        replace=True,
    )
    spec = {
        "instrument": {"venue": "bitget", "market": "perp", "symbol": "BTC/USDT"},
        "timeframe": "1m",
        "control": "candidate-price/v1",
        "treatment": "causal-market-structure/v1",
        "core_algorithm_version": CORE_ALGORITHM_VERSION,
        "params_hash": PARAMS_HASH,
        "external_authority_ref": "learning:experiment-7:policy-3:behavior-9",
        "outcome": "same-t0-4h-mfe-mae/v1",
        "cost": "frozen-round-trip/v1",
    }
    start_args = {
        "observation_id": "smc-shape",
        "kind": "test_smc_pair",
        "handler_version": "test-smc-handler/v1",
        "slot_key": "entry-geometry",
        "authority_mode": "external_authority",
        "started_at": _utc().isoformat(),
        "collect_until": (_utc() + timedelta(days=1)).isoformat(),
        "finalize_not_before": (_utc() + timedelta(days=2)).isoformat(),
    }
    first = start_window(**start_args, spec=spec, producer_commit="a" * 40)
    same = start_window(**start_args, spec=spec, producer_commit="b" * 40)
    assert same["generation"] == first["generation"]

    for key, bars in (("candidate-1", _closed_bars()), ("candidate-2", _closed_bars(gapped=True))):
        capture_event(
            first["observation_id"], first["generation"], first["spec_hash"],
            {"candidate_key": key, "direction": "long", "bars": bars,
             "known_at": (_utc() + timedelta(hours=1)).isoformat()},
            producer_commit="b" * 40,
        )

    finalized = finalize_window(
        first["observation_id"], first["generation"], now=_utc(3).isoformat(),
        output=tmp_path / "smc-result.json",
    )
    artifact = json.loads((tmp_path / "smc-result.json").read_text())
    assert finalized["status"] == "finalized"
    assert artifact["domain_result"]["denominator"] == 2
    assert artifact["domain_result"]["paired_coverage"] == 1
    assert status_window(first["observation_id"], first["generation"])["unit_counts"] == {
        "admitted": 1, "unavailable": 1,
    }

    rotated = start_window(
        **{
            **start_args,
            "started_at": _utc(4).isoformat(),
            "collect_until": _utc(5).isoformat(),
            "finalize_not_before": _utc(6).isoformat(),
        },
        spec={**spec, "core_algorithm_version": "causal-market-structure/v-next"},
        producer_commit="b" * 40,
    )
    assert rotated["authority_mode"] == "external_authority"
    assert "learning:experiment-7" in rotated["spec_json"]
    reset_lab_db()

def test_finalize_capture_interleavings_are_fail_closed_without_io_transaction(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from threading import Event, Thread, current_thread

    import aletheia.config.loader as loader
    import aletheia.observation_lab.kernel as kernel
    from aletheia.observation_lab.db import reset_lab_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()
    finalizing = Event()
    continue_finalize = Event()

    def capture(_window, event):
        return kernel.ObservationProgress(summary={}, units=({
            "unit_key": event["key"], "source_kind": "race",
            "source_ref": event["key"], "source_hash": "a" * 64,
            "known_at": event["known_at"], "admission": "admitted",
        },))

    def finalize(_window):
        finalizing.set()
        assert continue_finalize.wait(2)
        return {"ok": True}

    kernel.register_handler(kernel.ObservationHandler(
        kind="race-window", version="v1", normalize_spec=lambda spec: spec,
        capture=capture, advance=lambda _window: kernel.ObservationProgress(summary={}, can_finalize=True),
        finalize=finalize, retention_claims=lambda _window: (),
    ), replace=True)
    window = kernel.start_window(
        observation_id="race-before-terminal", kind="race-window", handler_version="v1",
        slot_key="race-1", authority_mode="observe_only", spec={"v": 1},
        started_at=_utc(), collect_until=_utc(2), finalize_not_before=_utc(2),
    )
    failures = []
    thread = Thread(
        name="finalize-before-capture",
        target=lambda: _record_exception(
            failures, lambda: kernel.finalize_window(
                window["observation_id"], window["generation"], now=_utc(2),
                output=tmp_path / "race-before.json",
            )
        ),
    )
    thread.start()
    assert finalizing.wait(2)
    kernel.capture_event(
        window["observation_id"], window["generation"], window["spec_hash"],
        {"key": "captured-first", "known_at": _utc().isoformat()},
    )
    continue_finalize.set()
    thread.join(2)
    assert not thread.is_alive()
    assert len(failures) == 1 and str(failures[0]) == "window_finalize_race"
    assert kernel.status_window(window["observation_id"], window["generation"])["status"] == "active"

    finalizing.clear()
    continue_finalize.set()
    terminal_committed = Event()
    real_transaction = kernel.lab_transaction

    @contextmanager
    def observed_transaction(*args, **kwargs):
        with real_transaction(*args, **kwargs) as db:
            yield db
        if current_thread().name == "finalize-before-late-capture":
            terminal_committed.set()

    monkeypatch.setattr(kernel, "lab_transaction", observed_transaction)
    second = kernel.start_window(
        observation_id="race-after-terminal", kind="race-window", handler_version="v1",
        slot_key="race-2", authority_mode="observe_only", spec={"v": 1},
        started_at=_utc(), collect_until=_utc(2), finalize_not_before=_utc(2),
    )
    completed = []
    thread = Thread(
        name="finalize-before-late-capture",
        target=lambda: completed.append(kernel.finalize_window(
            second["observation_id"], second["generation"], now=_utc(2),
            output=tmp_path / "race-after.json",
        )),
    )
    thread.start()
    assert terminal_committed.wait(2)
    with pytest.raises(ValueError, match="window_finalized"):
        kernel.capture_event(
            second["observation_id"], second["generation"], second["spec_hash"],
            {"key": "captured-late", "known_at": _utc().isoformat()},
        )
    thread.join(2)
    assert completed[0]["status"] == "finalized"
    reset_lab_db()


def _record_exception(errors, action):
    try:
        action()
    except Exception as exc:
        errors.append(exc)
