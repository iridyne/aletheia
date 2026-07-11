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
        "aletheia.analytics.filter_replay.build_bounded_filter_replays",
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
        "aletheia.analytics.filter_replay.build_bounded_filter_replays",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_shadow_experiments.build_filter_shadow_experiment_report",
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
        "aletheia.analytics.filter_replay.build_bounded_filter_replays",
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
        "aletheia.analytics.filter_shadow_experiments.register_replay_ready_experiments",
        fake_register,
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_shadow_experiments.evaluate_active_shadow_experiments",
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
