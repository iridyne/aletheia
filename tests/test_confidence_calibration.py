"""Focused regressions for typed confidence-calibration availability."""

from __future__ import annotations


def test_perfect_calibration_zero_ece_keeps_full_score(monkeypatch):
    import aletheia.analytics.confidence_calibration as calibration

    monkeypatch.setattr(
        calibration,
        "get_latest_calibration_report",
        lambda: {
            "total_executed": 20,
            "reliable_sample_count": 20,
            "calibration_state": "available",
            "ece": 0.0,
        },
    )

    assert calibration.compute_calibration_score() == 100.0


def test_unavailable_calibration_never_grants_readiness_score(monkeypatch):
    import aletheia.analytics.confidence_calibration as calibration

    monkeypatch.setattr(
        calibration,
        "get_latest_calibration_report",
        lambda: {
            "total_executed": 5,
            "reliable_sample_count": 0,
            "calibration_state": "insufficient_data",
            "ece": None,
        },
    )

    assert calibration.compute_calibration_score() is None


def test_legacy_zero_ece_is_treated_as_ambiguous(monkeypatch):
    import aletheia.analytics.confidence_calibration as calibration

    monkeypatch.setattr(
        calibration,
        "get_latest_calibration_report",
        lambda: {"total_executed": 20, "ece": 0.0},
    )

    assert calibration.compute_calibration_score() is None


def test_go_live_calibration_keeps_zero_ece_as_a_pass(monkeypatch):
    import aletheia.analytics.confidence_calibration as calibration
    from aletheia.analytics.go_live_scorecard import _check_calibration

    report = {
        "total_executed": 20,
        "calibration_state": "available",
        "ece": 0.0,
        "ece_grade": "excellent",
    }
    monkeypatch.setattr(calibration, "get_latest_calibration_report", lambda: report)
    monkeypatch.setattr(calibration, "compute_calibration_score", lambda: 100.0)

    result = _check_calibration()

    assert result["status"] == "pass"
    assert result["score"] == 100.0
    assert "ECE=0.000" in result["detail"]
