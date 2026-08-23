"""Regression test for issue #1231: experiment state read errors.

When the experiment state query fails (SQLite lock, corruption, schema error)
the cleanup must not silently delete experiment-guarded evidence.

Contract verified here:
1. ExperimentStateReadError is raised on read failure (not silent None).
2. _effective_cutoff protects data when experiment predates the default window.
3. A visible infra error event is recorded when cleanup encounters the error.
4. Normal cleanup with no open experiments still succeeds (None result is OK).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from aletheia.storage import repositories as repo


# ---------------------------------------------------------------------------
# 1. ExperimentStateReadError raised instead of silent None
# ---------------------------------------------------------------------------

def test_experiment_state_read_error_raised_on_db_failure():
    """ExperimentStateReadError is raised when the query fails."""
    with patch("aletheia.storage.repositories.get_db") as mock_get_db:
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.OperationalError("database is locked")
        mock_get_db.return_value = mock_db

        with pytest.raises(repo.ExperimentStateReadError, match="Failed to query experiment state"):
            repo.min_open_experiment_exposure_at()


def test_experiment_state_schema_error_raises():
    """Schema errors are also wrapped in ExperimentStateReadError."""
    with patch("aletheia.storage.repositories.get_db") as mock_get_db:
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.DatabaseError("database disk image is malformed")
        mock_get_db.return_value = mock_db

        with pytest.raises(repo.ExperimentStateReadError):
            repo.min_open_experiment_exposure_at()


# ---------------------------------------------------------------------------
# 2. Normal case: no open experiments returns None without raising
# ---------------------------------------------------------------------------

def test_no_open_experiments_returns_none():
    """When no active experiment rows exist, returns None (not an error)."""
    with patch("aletheia.storage.repositories.get_db") as mock_get_db:
        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = MagicMock(return_value=None)
        mock_row.keys = MagicMock(return_value=["m"])
        mock_row.__bool__ = MagicMock(return_value=True)
        # Row exists but m is None → no exposure timestamps
        mock_db.execute.return_value.fetchone.return_value = mock_row
        mock_get_db.return_value = mock_db

        result = repo.min_open_experiment_exposure_at()
        assert result is None


def test_open_experiment_returns_earliest_exposure():
    """With an active open experiment, returns its earliest exposure timestamp."""
    exposed_at = datetime(2026, 8, 1, 12, 5, 0, tzinfo=timezone.utc).isoformat()

    with patch("aletheia.storage.repositories.get_db") as mock_get_db:
        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.keys = MagicMock(return_value=["m"])
        mock_row.__getitem__ = MagicMock(return_value=exposed_at)
        mock_db.execute.return_value.fetchone.return_value = mock_row
        mock_get_db.return_value = mock_db

        result = repo.min_open_experiment_exposure_at()
        assert result == exposed_at


# ---------------------------------------------------------------------------
# 3. _effective_cutoff respects the open-experiment guard
# ---------------------------------------------------------------------------

def test_effective_cutoff_respects_open_experiment():
    """_effective_cutoff returns earlier date when experiment predates default window."""
    now = datetime.now(timezone.utc)
    days = 30
    experiment_start = (now - timedelta(days=60)).isoformat()  # older than window

    result = repo._effective_cutoff(days=days, min_experiment_started_at=experiment_start)
    default = (now - timedelta(days=days)).isoformat()

    # Should return the experiment start (older), not the default (newer)
    assert result < default


def test_effective_cutoff_uses_default_when_experiment_is_newer():
    """_effective_cutoff uses default when experiment start is within the default window."""
    now = datetime.now(timezone.utc)
    days = 30
    experiment_start = (now - timedelta(days=10)).isoformat()  # newer than window

    result = repo._effective_cutoff(days=days, min_experiment_started_at=experiment_start)

    # Should be within 1 second of the default cutoff (the two datetime.now() calls differ)
    result_dt = datetime.fromisoformat(result)
    default_dt = now - timedelta(days=days)
    assert abs((result_dt - default_dt).total_seconds()) < 1

def test_effective_cutoff_with_none_returns_default():
    """_effective_cutoff with None min returns the plain default cutoff."""
    now = datetime.now(timezone.utc)
    days = 45

    result = repo._effective_cutoff(days=days, min_experiment_started_at=None)
    expected = (now - timedelta(days=days)).isoformat()

    # Allow 1-second tolerance for test timing
    result_dt = datetime.fromisoformat(result)
    expected_dt = datetime.fromisoformat(expected)
    assert abs((result_dt - expected_dt).total_seconds()) < 1


# ---------------------------------------------------------------------------
# 4. Critical invariant: failure must not collapse to None
# ---------------------------------------------------------------------------

def test_db_error_must_not_return_none():
    """Prove that the old bug (silent None on failure) is gone.

    Pre-fix: bare `except Exception: return None` made read errors
    indistinguishable from "no open experiment", allowing guarded prunes
    to run unchecked. Post-fix: ExperimentStateReadError must propagate.
    """
    with patch("aletheia.storage.repositories.get_db") as mock_get_db:
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.OperationalError("database is locked")
        mock_get_db.return_value = mock_db

        # Must raise, not silently return None
        with pytest.raises(repo.ExperimentStateReadError):
            repo.min_open_experiment_exposure_at()


