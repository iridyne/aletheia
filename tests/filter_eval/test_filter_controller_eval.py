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
    assert all("No per-filter passed/dropped cohort" in r.reasoning for r in recs)


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


def test_insufficient_data_when_no_stats(monkeypatch):
    """No calibration data → every tunable family filter reports insufficient_data, no crash."""
    monkeypatch.setattr("aletheia.storage.repositories.calibration_stats", lambda: [])
    recs = fc.compute_filter_feedback()
    assert recs, "should still emit rows explaining insufficient data"
    assert all(r.verdict == "insufficient_data" for r in recs), (
        "with no stats, every recommendation must be insufficient_data"
    )
