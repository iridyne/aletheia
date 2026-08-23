"""Regression tests for learning lifecycle fixes #1429 and #1452."""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from aletheia.learning.lifecycle import (
    _stage_allocation,
    _ExperimentAlreadyClosedError,
    STAGE_ALLOCATION,
)


# ─────────────────────────────────────────────────────────────────────────────
# #1452 — _stage_allocation fail-closed on config failure
# ─────────────────────────────────────────────────────────────────────────────

def test_stage_allocation_raises_on_config_failure():
    """Config read failure must raise ValueError, not fall back to 0.80."""
    with patch("aletheia.learning.lifecycle.load_config", side_effect=RuntimeError("db locked")):
        with pytest.raises(ValueError, match="paper_active_allocation_pct unavailable"):
            _stage_allocation("paper_active")


def test_stage_allocation_uses_configured_value(monkeypatch):
    """Valid config value is returned exactly."""
    cfg = MagicMock()
    cfg.learning.paper_active_allocation_pct = 0.75
    monkeypatch.setattr("aletheia.learning.lifecycle.load_config", lambda: cfg)
    assert _stage_allocation("paper_active") == pytest.approx(0.75)


def test_stage_allocation_rejects_out_of_range(monkeypatch):
    """Values outside [0.01, 0.90] raise ValueError."""
    cfg = MagicMock()
    cfg.learning.paper_active_allocation_pct = 0.95  # > 0.90
    monkeypatch.setattr("aletheia.learning.lifecycle.load_config", lambda: cfg)
    with pytest.raises(ValueError, match="out of range"):
        _stage_allocation("paper_active")


def test_stage_allocation_non_paper_active_uses_constants():
    """Non-paper_active stages return STAGE_ALLOCATION constants without config read."""
    for stage, expected in STAGE_ALLOCATION.items():
        assert _stage_allocation(stage) == pytest.approx(expected)


def test_promote_holds_on_config_failure_source_check():
    """_promote_one_stage source must contain the allocation ValueError guard."""
    import inspect
    import aletheia.learning.lifecycle as mod
    src = inspect.getsource(mod._promote_one_stage)
    assert "learning_config_unavailable" in src, (
        "_promote_one_stage must return hold with learning_config_unavailable on config failure"
    )
    assert "ValueError" in src, (
        "_promote_one_stage must catch ValueError from _stage_allocation"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1429 — ExperimentAlreadyClosedError class exists and is used
# ─────────────────────────────────────────────────────────────────────────────

def test_experiment_already_closed_error_is_runtime_error():
    """_ExperimentAlreadyClosedError subclasses RuntimeError."""
    err = _ExperimentAlreadyClosedError("exp-1")
    assert isinstance(err, RuntimeError)
    assert "exp-1" in str(err)
    assert err.experiment_id == "exp-1"


def test_promote_rowcount_check_in_source():
    """_promote_one_stage source contains rowcount check and error handling."""
    import inspect
    import aletheia.learning.lifecycle as mod
    src = inspect.getsource(mod._promote_one_stage)
    assert "rowcount" in src, "_promote_one_stage must check rowcount after UPDATE"
    assert "_ExperimentAlreadyClosedError" in src
    assert "already_transitioned_by_concurrent_safety" in src


def test_safety_action_source_check_present():
    """_apply_safety_action code contains rowcount check for #1429."""
    import inspect
    import aletheia.learning.lifecycle as mod
    src = inspect.getsource(mod._apply_safety_action)
    assert "rowcount" in src, "_apply_safety_action must check rowcount for concurrent promotion"
    assert "_ExperimentAlreadyClosedError" in src
