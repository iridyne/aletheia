"""Focused regressions for typed confidence-calibration availability."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("kind", ["calibration", "feedback"])
def test_report_producer_does_not_hide_storage_failure(monkeypatch, kind):
    from extensions.learning import reasoning_feedback, confidence_calibration
    from aletheia.storage import repositories, sqlite

    module, builder, persist, reader = (
        (confidence_calibration, confidence_calibration.build_calibration_report,
         confidence_calibration._persist_report, "get_latest_calibration_report")
        if kind == "calibration" else
        (reasoning_feedback, reasoning_feedback.build_reasoning_feedback,
         reasoning_feedback._persist_feedback, "get_latest_reasoning_feedback")
    )

    def fail(*args, **kwargs):
        raise RuntimeError("fixture_report_storage_failure")

    monkeypatch.setattr(module, reader, lambda **kwargs: None)
    monkeypatch.setattr(repositories, "insert_knowledge_item", fail)
    with pytest.raises(RuntimeError, match="fixture_report_storage_failure"):
        persist({"generated_at": "2026-09-14T00:00:00+00:00", "execution_mode": "bitget_demo"})
    monkeypatch.setattr(sqlite, "get_db", fail)
    with pytest.raises(RuntimeError, match="fixture_report_storage_failure"):
        builder(execution_mode="bitget_demo")


def test_report_storage_failure_reaches_scheduler_and_maintenance(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.analytics import execution_mode
    from ops.maintenance.outcome_integrity import _rebuild_allowed_reports
    from aletheia.storage import sqlite

    def fail():
        raise RuntimeError("fixture_report_storage_failure")

    monkeypatch.setattr(execution_mode, "resolve_selected_execution_mode", lambda: "bitget_demo")
    from aletheia.analytics import confidence_calibration
    from extensions.learning.confidence_calibration import build_calibration_report
    monkeypatch.setattr(confidence_calibration, "_calibration_builder", build_calibration_report)
    from aletheia.analytics import reasoning_feedback
    from extensions.learning.reasoning_feedback import build_reasoning_feedback
    monkeypatch.setattr(reasoning_feedback, "_feedback_builder", build_reasoning_feedback)
    monkeypatch.setattr(sqlite, "get_db", fail)
    failures, successes = [], []
    monkeypatch.setattr(scheduler, "_record_job_failure", lambda *args: failures.append(args))
    monkeypatch.setattr(scheduler, "_record_job_success", successes.append)
    scheduler._run_confidence_calibration()
    assert failures == [("confidence_calibration", "fixture_report_storage_failure")]
    assert successes == []
    assert _rebuild_allowed_reports() == {
        "calibration_report": "failed", "reasoning_feedback": "failed",
    }


@pytest.mark.parametrize("ece, expected", [(None, "unavailable"), (0.0, "0.000"), (0.125, "0.125")])
def test_calibration_job_logs_nullable_ece_truthfully(monkeypatch, caplog, ece, expected):
    import logging
    from aletheia.agent_runtime import scheduler
    from aletheia.analytics import confidence_calibration

    monkeypatch.setattr(confidence_calibration, "_calibration_builder", lambda: {
        "ece": ece, "ece_grade": "insufficient_data" if ece is None else "good", "total_executed": 5,
    })
    failures, successes = [], []
    monkeypatch.setattr(scheduler, "_record_job_failure", lambda *args: failures.append(args))
    monkeypatch.setattr(scheduler, "_record_job_success", successes.append)
    with caplog.at_level(logging.INFO, logger=scheduler.logger.name):
        scheduler._run_confidence_calibration()
    assert failures == []
    assert successes == ["confidence_calibration"]
    assert f"ECE={expected}" in caplog.text


def test_absent_calibration_builder_is_unavailable_to_both_callers(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.analytics import confidence_calibration, reasoning_feedback
    from ops.maintenance.outcome_integrity import _rebuild_allowed_reports

    monkeypatch.setattr(confidence_calibration, "_calibration_builder", None)
    monkeypatch.setattr(reasoning_feedback, "_feedback_builder", lambda: None)
    failures = []
    monkeypatch.setattr(scheduler, "_record_job_failure", lambda *args: failures.append(args))
    monkeypatch.setattr(scheduler, "_record_job_success", lambda *args: pytest.fail("missing producer"))
    scheduler._run_confidence_calibration()
    assert failures == [("confidence_calibration", "calibration_builder_unavailable")]
    assert _rebuild_allowed_reports() == {
        "calibration_report": "unavailable", "reasoning_feedback": "insufficient",
    }


def test_calibration_read_does_not_generate_missing_report(monkeypatch):
    import aletheia.analytics.confidence_calibration as calibration
    from aletheia.analytics import outcome_integrity
    from aletheia.server.routes.analytics import get_confidence_calibration

    monkeypatch.setattr(calibration, "get_latest_calibration_report", lambda **kwargs: None)
    monkeypatch.setattr(outcome_integrity, "build_outcome_cohort_summary", lambda **kwargs: {})

    def forbidden(**kwargs):
        raise AssertionError("GET must not generate or persist a calibration report")

    monkeypatch.setattr(calibration, "_calibration_builder", forbidden)
    assert not hasattr(calibration, "build_calibration_report")
    result = get_confidence_calibration(execution_mode="bitget_demo")
    assert result["status"] == "no_data"
    assert result["ece"] is None
    assert result["buckets"] == []


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
