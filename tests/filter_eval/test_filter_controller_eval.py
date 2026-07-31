"""
Filter evaluation: Filter Feedback Controller (Phase 1, shadow / observe-only).

Run with:  uv run pytest tests/filter_eval/test_filter_controller_eval.py -v

Verifies:
  1. Aggregate family returns never become per-filter loosen/tighten advice.
  2. The controller NEVER mutates trading config (shadow invariant) — asserted by
     ensuring save_config is never importable-and-called during a compute cycle.
"""
from __future__ import annotations

from aletheia.analytics import filter_controller as fc
from aletheia.analytics.filter_registry import (
    tunable_filters,
    FILTER_REGISTRY,
)


def test_unattributed_family_performance_never_becomes_filter_advice(monkeypatch):
    """A family hit-rate alone cannot prove that any individual filter is miscalibrated."""
    monkeypatch.setattr(
        "aletheia.storage.repositories.calibration_stats",
        lambda: [
            {"scanner_family": "momentum", "horizon": "4h", "n": 40, "win_count": 30},
            {"scanner_family": "mean_reversion", "horizon": "1h", "n": 40, "win_count": 30},
        ],
    )
    recs = fc.compute_filter_feedback()
    assert recs
    assert all(r.verdict == "insufficient_data" for r in recs)
    assert all(
        "filter-specific" in r.reasoning
        or "No per-filter passed/dropped cohort" in r.reasoning
        for r in recs
    )


def test_shadow_compute_does_not_mutate_config(monkeypatch):
    """compute_filter_feedback must never call save_config (shadow invariant)."""
    import aletheia.config.loader as loader

    called = {"save": 0}
    monkeypatch.setattr(loader, "save_config", lambda *a, **k: called.__setitem__("save", called["save"] + 1))
    monkeypatch.setattr(
        "aletheia.storage.repositories.calibration_stats",
        lambda: [{"scanner_family": "momentum", "horizon": "4h", "n": 40, "win_count": 30}],
    )
    fc.compute_filter_feedback()
    assert called["save"] == 0, "compute_filter_feedback must not write config in shadow mode"


def test_build_report_shape(monkeypatch):
    """The report exposes the shadow_only flag and verdict counts for the UI/CLI."""
    monkeypatch.setattr(
        "aletheia.storage.repositories.calibration_stats",
        lambda: [{"scanner_family": "momentum", "horizon": "4h", "n": 40, "win_count": 30}],
    )
    report = fc.build_filter_controller_report()
    assert report["shadow_only"] is True
    assert "verdict_counts" in report
    assert report["total_filter_count"] == len(FILTER_REGISTRY)
    assert report["tunable_filter_count"] == len(tunable_filters())
    assert report["verdict_counts"].get("loosen", 0) == 0
    assert report["verdict_counts"].get("tighten", 0) == 0


def test_build_report_applies_requested_evidence_window(monkeypatch):
    captured = {}

    def fake_compute(min_samples=20, *, evaluated_since=None):
        captured["evaluated_since"] = evaluated_since
        return []

    monkeypatch.setattr(fc, "compute_filter_feedback", fake_compute)
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_trajectory_summary",
        lambda **kwargs: {
            "assignment_policy": "first_exposure",
            "candidate_trajectories": 10,
            "excluded_later_rows": 4,
            "decision_transition_candidates": 2,
        },
    )

    report = fc.build_filter_controller_report(hours=168)
    rendered = fc.render_filter_controller_report(report)

    assert captured["evaluated_since"] is not None
    assert report["window"]["hours"] == 168
    assert "Window: 168h" in rendered
    assert report["trajectory_summary"]["assignment_policy"] == "first_exposure"
    assert "later retries excluded=4" in rendered


def test_controller_report_renders_bounded_gate_only_replay(monkeypatch):
    from aletheia.analytics.filter_controller import FilterRecommendation

    recommendation = FilterRecommendation(
        filter_name="family_ttl",
        scanner_family="momentum",
        stage="paper_ranker",
        current_value='{"ttl_sec":1800}',
        verdict="overrestrictive_suspect",
        suggested_direction="review_loosen",
        error_signal=0.2,
        n_samples=25,
        reasoning="test",
        direction="long",
        market_type="perp",
        horizon="1h",
        filter_version="v1-fixed",
    )
    replay = {
        "filter_name": "family_ttl",
        "filter_version": "v1-fixed",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "1h",
        "current_ttl_sec": 1800,
        "candidate_cap_reached": False,
        "alternatives": [{
            "ttl_sec": 3600,
            "newly_admitted_candidates": 4,
            "settled_n": 4,
            "outcome_coverage": 1.0,
            "raw_avg_net_pct": 1.2,
            "shrunk_avg_net_pct": -0.1,
            "readiness": "insufficient_data",
        }],
    }
    monkeypatch.setattr(fc, "compute_filter_feedback", lambda **kwargs: [recommendation])
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_trajectory_summary",
        lambda **kwargs: {
            "assignment_policy": "first_exposure",
            "candidate_trajectories": 1,
            "excluded_later_rows": 0,
            "decision_transition_candidates": 0,
        },
    )
    monkeypatch.setattr(
        "aletheia.counterfactual.filter_replay.build_bounded_filter_replays",
        lambda *args, **kwargs: [replay],
    )

    report = fc.build_filter_controller_report(hours=24)
    rendered = fc.render_filter_controller_report(report)

    assert report["bounded_replays"] == [replay]
    assert "Bounded gate-only replays" in rendered
    assert "1800s -> 3600s" in rendered
    assert "readiness=insufficient_data" in rendered
    assert report["replay_summary"]["experiment_ready"] == 0
    assert "Replay disposition: experiment_ready=0" in rendered
    assert "Observational screening suspects" in rendered
    assert "no cohort ready for a controlled experiment" in rendered


def test_controller_report_renders_registered_prospective_experiment(monkeypatch):
    monkeypatch.setattr(fc, "compute_filter_feedback", lambda **kwargs: [])
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_trajectory_summary",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        "aletheia.counterfactual.filter_replay.build_bounded_filter_replays",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "aletheia.counterfactual.filter_experiments.build_filter_shadow_experiment_report",
        lambda: [{
            "filter_name": "family_ttl",
            "scanner_family": "momentum",
            "direction": "long",
            "market_type": "perp",
            "horizon": "4h",
            "filter_version": "fixed-v1",
            "started_at": "2026-07-11T12:00:00+00:00",
            "status": "collecting",
            "control_parameter": {"ttl_sec": 1800},
            "treatment_parameter": {"ttl_sec": 14400},
            "production_mutation_allowed": 0,
            "latest_evaluation": {
                "control_settled_n": 2,
                "treatment_settled_n": 1,
                "disposition": "collecting",
            },
        }],
    )

    report = fc.build_filter_controller_report(hours=24)
    rendered = fc.render_filter_controller_report(report)

    assert len(report["shadow_experiments"]) == 1
    assert "Prospective shadow experiments (production unchanged):" in rendered
    assert "1800s -> 14400s" in rendered
    assert "status=collecting" in rendered


def test_observe_cycle_registers_and_evaluates_shadow_experiments(monkeypatch):
    calls = {"registered": 0, "evaluated": 0}
    monkeypatch.setattr(fc, "compute_filter_feedback", lambda min_samples=20: [])
    monkeypatch.setattr(
        "aletheia.counterfactual.filter_replay.build_bounded_filter_replays",
        lambda *args, **kwargs: [{"filter_name": "family_ttl"}],
    )

    def fake_register(replays):
        assert replays == [{"filter_name": "family_ttl"}]
        calls["registered"] += 1
        return {"registered": 1, "already_registered": 0, "skipped": 0}

    def fake_evaluate():
        calls["evaluated"] += 1
        return {
            "evaluated": 1,
            "ready_for_review": 0,
            "completed_no_support": 0,
            "invalidated": 0,
        }

    monkeypatch.setattr(
        "aletheia.counterfactual.filter_experiments.register_replay_ready_experiments",
        fake_register,
    )
    monkeypatch.setattr(
        "aletheia.counterfactual.filter_experiments.evaluate_active_shadow_experiments",
        fake_evaluate,
    )

    summary = fc.run_controller_observe()

    assert calls == {"registered": 1, "evaluated": 1}
    assert summary["experiments_registered"] == 1
    assert summary["experiments_evaluated"] == 1


def test_insufficient_data_when_no_stats(monkeypatch):
    """No calibration data → every tunable family filter reports insufficient_data, no crash."""
    monkeypatch.setattr("aletheia.storage.repositories.calibration_stats", lambda: [])
    recs = fc.compute_filter_feedback()
    assert recs, "should still emit rows explaining insufficient data"
    assert all(r.verdict == "insufficient_data" for r in recs), (
        "with no stats, every recommendation must be insufficient_data"
    )


# ─── Cross-run stability signal ──────────────────────────────────────────────

import sqlite3


def _fake_recommendations_db(monkeypatch, rows: list[dict]):
    """Inject an in-memory controller_recommendations table populated with rows.

    Each row must contain at minimum:
      run_id, created_at, filter_name, scanner_family, evidence_verdict.
    Optional: direction, market_type, horizon, filter_version, error_signal, reasoning.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE controller_recommendations ("
        " id TEXT PRIMARY KEY, run_id TEXT, created_at TEXT, "
        " filter_name TEXT, scanner_family TEXT, direction TEXT, "
        " market_type TEXT, horizon TEXT, filter_version TEXT, "
        " evidence_verdict TEXT, verdict TEXT, error_signal REAL, reasoning TEXT)"
    )
    for i, row in enumerate(rows):
        conn.execute(
            "INSERT INTO controller_recommendations "
            "(id, run_id, created_at, filter_name, scanner_family, direction, "
            " market_type, horizon, filter_version, evidence_verdict, verdict, "
            " error_signal, reasoning) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.get("id") or f"row-{i}",
                row["run_id"], row["created_at"], row["filter_name"],
                row["scanner_family"], row.get("direction"), row.get("market_type"),
                row.get("horizon"), row.get("filter_version"),
                row.get("evidence_verdict"),
                # verdict is CHECK-constrained in production; the legacy coercion
                # never emits overrestrictive_suspect. Default to insufficient_data
                # so the COALESCE fallback still yields a valid Verdict literal.
                row.get("verdict") or "insufficient_data",
                row.get("error_signal"), row.get("reasoning") or "",
            ),
        )
    conn.commit()
    monkeypatch.setattr("aletheia.storage.sqlite.get_db", lambda: conn)
    return conn


def _mk_row(*, run_id, created_at, cell="momentum", filter_name="family_ttl",
            verdict="overrestrictive_suspect", error=0.12, reasoning=""):
    return {
        "run_id": run_id, "created_at": created_at,
        "filter_name": filter_name, "scanner_family": cell,
        "direction": "long", "market_type": "perp", "horizon": "4h",
        "filter_version": "v1", "evidence_verdict": verdict,
        "error_signal": error, "reasoning": reasoning,
    }


def test_persistent_empty_db(monkeypatch):
    _fake_recommendations_db(monkeypatch, [])
    assert fc.compute_persistent_recommendations() == []


def test_persistent_fewer_runs_than_minimum(monkeypatch):
    # Only 2 runs exist; min_consecutive_runs default is 3 → no output.
    _fake_recommendations_db(monkeypatch, [
        _mk_row(run_id="run-a", created_at="2026-07-19T00:00:00+00:00"),
        _mk_row(run_id="run-b", created_at="2026-07-19T06:00:00+00:00"),
    ])
    assert fc.compute_persistent_recommendations(min_consecutive_runs=3) == []


def test_persistent_full_streak(monkeypatch):
    _fake_recommendations_db(monkeypatch, [
        _mk_row(run_id="run-a", created_at="2026-07-19T00:00:00+00:00", error=0.10),
        _mk_row(run_id="run-b", created_at="2026-07-19T06:00:00+00:00", error=0.11),
        _mk_row(run_id="run-c", created_at="2026-07-19T12:00:00+00:00", error=0.13,
                reasoning="rejected mean +0.30% vs passed +0.15%"),
    ])
    persistent = fc.compute_persistent_recommendations(min_consecutive_runs=3)
    assert len(persistent) == 1
    p = persistent[0]
    assert p.filter_name == "family_ttl"
    assert p.scanner_family == "momentum"
    assert p.consecutive_suspect_runs == 3
    assert p.runs_analyzed == 3
    assert p.last_suspect_at == "2026-07-19T12:00:00+00:00"
    assert p.first_suspect_at == "2026-07-19T00:00:00+00:00"
    assert p.latest_error_signal == 0.13
    assert p.latest_run_id == "run-c"
    assert "rejected mean" in p.latest_reasoning


def test_persistent_broken_streak_by_hold_verdict(monkeypatch):
    # Newest run has suspect, but the run before it flipped to hold → streak = 1.
    _fake_recommendations_db(monkeypatch, [
        _mk_row(run_id="run-a", created_at="2026-07-19T00:00:00+00:00"),
        _mk_row(run_id="run-b", created_at="2026-07-19T06:00:00+00:00", verdict="hold"),
        _mk_row(run_id="run-c", created_at="2026-07-19T12:00:00+00:00"),
    ])
    persistent = fc.compute_persistent_recommendations(min_consecutive_runs=2)
    assert persistent == [], (
        "hold in the middle of the recent-run window must break the streak"
    )


def test_persistent_absence_in_run_breaks_streak(monkeypatch):
    # Cell only appears in the newest and oldest of 3 runs; middle run has no row.
    # Streak (newest → oldest) walks: run-c present, run-b absent → break at 1.
    _fake_recommendations_db(monkeypatch, [
        _mk_row(run_id="run-a", created_at="2026-07-19T00:00:00+00:00"),
        # run-b has a row for a DIFFERENT cell — the target cell is absent from run-b
        _mk_row(run_id="run-b", created_at="2026-07-19T06:00:00+00:00", cell="mean_reversion"),
        _mk_row(run_id="run-c", created_at="2026-07-19T12:00:00+00:00"),
    ])
    persistent = fc.compute_persistent_recommendations(min_consecutive_runs=2)
    # The 'momentum' cell had a streak of only 1 (newest run), then absent.
    # The 'mean_reversion' cell had a streak of only 1 (middle run), then absent from newest.
    assert persistent == []


def test_persistent_skips_insufficient_summary_marker_rows(monkeypatch):
    # (insufficient_summary) rows are per-run aggregate markers, not real cells.
    _fake_recommendations_db(monkeypatch, [
        _mk_row(run_id="run-a", created_at="2026-07-19T00:00:00+00:00",
                filter_name="(insufficient_summary)"),
        _mk_row(run_id="run-b", created_at="2026-07-19T06:00:00+00:00",
                filter_name="(insufficient_summary)"),
        _mk_row(run_id="run-c", created_at="2026-07-19T12:00:00+00:00",
                filter_name="(insufficient_summary)"),
    ])
    assert fc.compute_persistent_recommendations(min_consecutive_runs=3) == []


def test_persistent_ordering_by_streak_then_signal(monkeypatch):
    # Two cells both hit min streak; cell with longer streak comes first.
    rows = []
    # cell-A: 3 consecutive suspects, small signal
    for i, ts in enumerate([
        "2026-07-19T00:00:00+00:00",
        "2026-07-19T06:00:00+00:00",
        "2026-07-19T12:00:00+00:00",
    ]):
        rows.append(_mk_row(run_id=f"run-{i}", created_at=ts, cell="cell-A", error=0.05))
    # cell-B: 2 consecutive suspects, huge signal — should come SECOND (streak wins).
    for i, ts in enumerate([
        "2026-07-19T06:00:00+00:00",
        "2026-07-19T12:00:00+00:00",
    ]):
        rows.append(_mk_row(run_id=f"run-{i+1}", created_at=ts, cell="cell-B", error=0.99))
    _fake_recommendations_db(monkeypatch, rows)
    persistent = fc.compute_persistent_recommendations(min_consecutive_runs=2)
    assert [p.scanner_family for p in persistent] == ["cell-A", "cell-B"]


def test_build_report_includes_persistent_field(monkeypatch):
    monkeypatch.setattr(fc, "compute_filter_feedback", lambda **kwargs: [])
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_trajectory_summary",
        lambda **kwargs: {
            "assignment_policy": "first_exposure",
            "candidate_trajectories": 0,
            "excluded_later_rows": 0,
            "decision_transition_candidates": 0,
        },
    )
    monkeypatch.setattr(fc, "compute_persistent_recommendations", lambda **kwargs: [
        fc.PersistentRecommendation(
            filter_name="family_ttl", scanner_family="momentum",
            direction="long", market_type="perp", horizon="4h", filter_version="v1",
            consecutive_suspect_runs=4, runs_analyzed=6,
            first_suspect_at="2026-07-18T00:00:00+00:00",
            last_suspect_at="2026-07-19T12:00:00+00:00",
            latest_error_signal=0.18, latest_reasoning="held for 4 runs",
            latest_run_id="run-c",
        )
    ])
    report = fc.build_filter_controller_report()
    assert "persistent_recommendations" in report
    assert len(report["persistent_recommendations"]) == 1
    entry = report["persistent_recommendations"][0]
    assert entry["consecutive_suspect_runs"] == 4
    assert entry["scanner_family"] == "momentum"
    assert "persistent_config" in report
    assert report["persistent_config"]["min_consecutive_runs"] >= 1


def test_render_report_shows_persistent_section(monkeypatch):
    monkeypatch.setattr(fc, "compute_filter_feedback", lambda **kwargs: [])
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_trajectory_summary",
        lambda **kwargs: {
            "assignment_policy": "first_exposure",
            "candidate_trajectories": 0,
            "excluded_later_rows": 0,
            "decision_transition_candidates": 0,
        },
    )
    monkeypatch.setattr(fc, "compute_persistent_recommendations", lambda **kwargs: [
        fc.PersistentRecommendation(
            filter_name="family_ttl", scanner_family="momentum",
            direction="long", market_type="perp", horizon="4h", filter_version="v1",
            consecutive_suspect_runs=4, runs_analyzed=6,
            first_suspect_at="2026-07-18T00:00:00+00:00",
            last_suspect_at="2026-07-19T12:00:00+00:00",
            latest_error_signal=0.1875, latest_reasoning="rejected mean +0.30% vs passed +0.10%",
            latest_run_id="run-c",
        )
    ])
    report = fc.build_filter_controller_report()
    rendered = fc.render_filter_controller_report(report)
    assert "Persistent (>= " in rendered  # summary line
    assert "1 cell(s) — human review" in rendered
    assert "Persistent overrestrictive suspects" in rendered  # detail heading
    assert "family_ttl · momentum" in rendered
    assert "streak=4/6" in rendered
    assert "signal=+0.1875" in rendered
    assert "rejected mean" in rendered
