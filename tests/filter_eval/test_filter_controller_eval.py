"""
Filter evaluation: Filter Feedback Controller (Phase 1, shadow / observe-only).

Run with:  uv run pytest tests/filter_eval/test_filter_controller_eval.py -v

Verifies:
  1. Per-family hit-rate → verdict mapping (loosen / tighten / hold / insufficient_data).
  2. Only family-keyed, forward-return-signalled, tunable filters get family cells.
  3. The controller NEVER mutates trading config (shadow invariant) — asserted by
     ensuring save_config is never importable-and-called during a compute cycle.
"""
from __future__ import annotations

import pytest

from aletheia.analytics import filter_controller as fc
from aletheia.analytics.filter_registry import (
    tunable_filters,
    report_only_filters,
    FILTER_REGISTRY,
)
from .scenarios.filter_controller import ALL_SCENARIOS


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.name)
def test_controller_verdict_scenario(scenario, monkeypatch):
    """Each synthetic calibration_stats() feed produces the expected family verdict."""
    monkeypatch.setattr(
        "aletheia.storage.repositories.calibration_stats",
        lambda: scenario.calibration_rows,
    )
    recs = fc.compute_filter_feedback(min_samples=scenario.min_samples)

    # Collect verdicts per family for family-keyed forward-return filters only.
    family_keyed_names = {
        f.name for f in tunable_filters()
        if f.family_keyed and f.feedback_source != "none"
    }
    by_family: dict[str, set[str]] = {}
    for r in recs:
        if r.filter_name not in family_keyed_names:
            continue
        if r.scanner_family in ("(all)", "(global)"):
            continue
        by_family.setdefault(r.scanner_family, set()).add(r.verdict)

    for family, expected in scenario.expected_verdict_by_family.items():
        assert family in by_family, (
            f"{scenario.name}: expected family '{family}' to have cells, got {sorted(by_family)}"
        )
        assert by_family[family] == {expected}, (
            f"{scenario.name}: family '{family}' expected verdict '{expected}', "
            f"got {by_family[family]}"
        )

    # Families NOT expected (e.g. wrong-horizon) must not appear.
    if not scenario.expected_verdict_by_family:
        assert not by_family, (
            f"{scenario.name}: expected no family cells, got {sorted(by_family)}"
        )


def test_report_only_filters_never_actionable(monkeypatch):
    """Hard rules and change-controlled filters must never emit loosen/tighten."""
    # Strong signal for every family — would trip loosen if a report-only filter leaked in.
    monkeypatch.setattr(
        "aletheia.storage.repositories.calibration_stats",
        lambda: [
            {"scanner_family": "momentum", "horizon": "4h", "n": 40, "win_count": 30},
            {"scanner_family": "mean_reversion", "horizon": "1h", "n": 40, "win_count": 30},
        ],
    )
    recs = fc.compute_filter_feedback()
    report_only_names = {f.name for f in report_only_filters()}
    leaked = [r for r in recs
              if r.filter_name in report_only_names and r.verdict in ("loosen", "tighten")]
    assert not leaked, f"report-only filters must never be actionable, got {leaked}"


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
    # momentum strong signal → at least one loosen among family-keyed filters
    assert report["verdict_counts"].get("loosen", 0) >= 1


def test_insufficient_data_when_no_stats(monkeypatch):
    """No calibration data → every tunable family filter reports insufficient_data, no crash."""
    monkeypatch.setattr("aletheia.storage.repositories.calibration_stats", lambda: [])
    recs = fc.compute_filter_feedback()
    assert recs, "should still emit rows explaining insufficient data"
    assert all(r.verdict == "insufficient_data" for r in recs), (
        "with no stats, every recommendation must be insufficient_data"
    )
