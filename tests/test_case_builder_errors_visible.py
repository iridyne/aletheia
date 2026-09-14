"""Regression test for issue #1246: case-builder failures must surface to AgentRun health."""

from unittest.mock import MagicMock, patch

import pytest

from extensions.learning.post_trade_reviewer import _run_case_builder_safe

_CASES_MODULE = "extensions.learning.trade_case_builder"


@pytest.fixture(autouse=True)
def _no_log_tool():
    with patch("extensions.learning.post_trade_reviewer.log_tool"):
        yield


def test_build_case_studies_top_level_failure_returns_error():
    """Top-level build_case_studies failure produces an error record."""
    with patch(f"{_CASES_MODULE}.build_case_studies", side_effect=RuntimeError("DB connection lost")):
        cases, candidates, errors = _run_case_builder_safe("run-123")

    assert cases == []
    assert candidates == []
    assert len(errors) == 1
    assert "case_builder:build_case_studies:RuntimeError" in errors[0]


def test_build_case_studies_per_position_failure_returns_error():
    """Per-position failure within build_case_studies produces position-specific error."""
    with patch(f"{_CASES_MODULE}.build_case_studies", return_value={
        "created": [{"case_study_id": "cs-1", "position_id": "pos-ok"}],
        "failed": [{"position_id": "pos-fail", "error": "ValueError"}],
    }):
        cases, candidates, errors = _run_case_builder_safe("run-123")

    assert len(cases) == 1
    assert cases[0]["position_id"] == "pos-ok"
    assert candidates == []
    assert len(errors) == 1
    assert "case_builder:position_failed:pos-fail:ValueError" in errors[0]


def test_detect_guard_candidates_failure_preserves_created_cases():
    """detect_guard_candidates failure reports independently, preserving created cases."""
    with patch(f"{_CASES_MODULE}.build_case_studies", return_value={
        "created": [{"case_study_id": "cs-1", "position_id": "pos-1"}],
        "failed": [],
    }), patch(f"{_CASES_MODULE}.detect_guard_candidates", side_effect=KeyError("missing_factor_key")):
        cases, candidates, errors = _run_case_builder_safe("run-123")

    assert len(cases) == 1
    assert cases[0]["case_study_id"] == "cs-1"
    assert candidates == []
    assert len(errors) == 1
    assert "case_builder:detect_guard_candidates:KeyError" in errors[0]


def test_healthy_zero_eligible_artifact_result_has_no_errors():
    """Legitimate zero-artifact success produces no errors."""
    with patch(f"{_CASES_MODULE}.build_case_studies", return_value={"created": [], "failed": []}), \
         patch(f"{_CASES_MODULE}.detect_guard_candidates", return_value=[]):
        cases, candidates, errors = _run_case_builder_safe("run-123")

    assert cases == []
    assert candidates == []
    assert errors == []


def test_missing_case_builder_reports_both_unavailable_operations(monkeypatch):
    import builtins
    import importlib.util

    original_import = builtins.__import__

    def absent(name, *args, **kwargs):
        if name == _CASES_MODULE:
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", absent)
    cases, candidates, errors = _run_case_builder_safe("missing-builder")
    assert cases == [] and candidates == []
    assert errors == [
        "case_builder:build_case_studies:ModuleNotFoundError",
        "case_builder:detect_guard_candidates:ModuleNotFoundError",
    ]
    assert importlib.util.find_spec("aletheia.analytics.trade_case_builder") is None
