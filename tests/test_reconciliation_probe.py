"""Tests for reconciliation unresolved-order probe and shorter cleanup window.

The doctor should report unresolved order backlog and blocked risk capacity.
The stale order cleanup window should be shorter (24h soft / 72h hard).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import aletheia.execution.stale_order_cleanup as suc
from aletheia.diagnostics.doctor import Report


def test_soft_window_shortened():
    """Soft cleanup window should be 24h (not 48h)."""
    assert suc._STALE_ORDER_SOFT_HOURS == 24


def test_hard_window_shortened():
    """Hard cleanup window should be 72h (not 168h)."""
    assert suc._STALE_ORDER_HARD_HOURS == 72


def test_probe_reports_unresolved_count():
    """Doctor probe should report unresolved order count."""
    from aletheia.diagnostics.doctor import _probe_unresolved_orders

    report = Report()

    # Mock the DB query results — use a real dict for column access
    stats_row = {
        "total": 5,
        "submission_unknown": 2,
        "materialization_missing": 1,
        "submitted": 2,
        "oldest_at": (datetime.now(timezone.utc) - timedelta(hours=12.5)).isoformat(),
    }

    with patch("aletheia.storage.sqlite.get_db") as mock_db:
        mock_db.return_value.execute.return_value.fetchone.return_value = stats_row

        _probe_unresolved_orders(report)

        # Check that the probe added a check for unresolved orders
        checks = [c for c in report.checks if c.name == "reconciliation.unresolved"]
        assert len(checks) == 1
        assert "5" in checks[0].detail  # total count in message


def test_probe_warns_on_old_unresolved():
    """Doctor probe should warn when unresolved orders are too old."""
    from aletheia.diagnostics.doctor import _probe_unresolved_orders

    report = Report()

    stats_row = {
        "total": 8,
        "submission_unknown": 5,
        "materialization_missing": 2,
        "submitted": 1,
        "oldest_at": (datetime.now(timezone.utc) - timedelta(hours=10.0)).isoformat(),
    }

    with patch("aletheia.storage.sqlite.get_db") as mock_db:
        mock_db.return_value.execute.return_value.fetchone.return_value = stats_row

        _probe_unresolved_orders(report)

        checks = [c for c in report.checks if c.name == "reconciliation.unresolved"]
        assert len(checks) == 1
        # Should be warn severity for old unresolved orders
        assert checks[0].severity == "warn"


def test_probe_ok_when_no_unresolved():
    """Doctor probe should report ok when no unresolved orders."""
    from aletheia.diagnostics.doctor import _probe_unresolved_orders

    report = Report()

    stats_row = {
        "total": 0,
        "submission_unknown": 0,
        "materialization_missing": 0,
        "submitted": 0,
        "oldest_at": None,
    }

    with patch("aletheia.storage.sqlite.get_db") as mock_db:
        mock_db.return_value.execute.return_value.fetchone.return_value = stats_row

        _probe_unresolved_orders(report)

        checks = [c for c in report.checks if c.name == "reconciliation.unresolved"]
        assert len(checks) == 1
        assert checks[0].severity == "ok"
