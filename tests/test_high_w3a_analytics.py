"""Regression tests for analytics fixes #1549 #1625 #1511 #1266."""
from __future__ import annotations
import inspect


def test_equity_curve_preserves_cumulative_across_all_history():
    """#1549: all_rows used for accumulation in full_cumulative mode."""
    import aletheia.server.routes.analytics as mod
    src = inspect.getsource(mod.get_equity_curve)
    assert "all_rows" in src, (
        "get_equity_curve must use all_rows for cumulative calculation, not sliced rows"
    )
    # The slice must only apply to window_relative mode
    assert "window_relative" in src


def test_cost_summary_includes_funding():
    """#1625: funding_cost included in total_costs."""
    import aletheia.server.routes.analytics as mod
    src = inspect.getsource(mod.get_cost_summary)
    assert "funding_cost" in src, (
        "get_cost_summary must include funding_cost in total_costs (#1625)"
    )
    assert "total_funding" in src, (
        "get_cost_summary must return total_funding field"
    )


def test_system_operator_alpha_uses_row_number_for_tiebreaking():
    """#1511: ROW_NUMBER with rowid tiebreaker prevents timestamp-tie duplicates."""
    import aletheia.analytics.alpha_attribution as mod
    src = inspect.getsource(mod._system_operator_alpha)
    assert "ROW_NUMBER()" in src, (
        "_system_operator_alpha must use ROW_NUMBER() for stable first-fire selection (#1511)"
    )
    assert "rowid ASC" in src, (
        "_system_operator_alpha must use rowid as stable tiebreaker"
    )
    # The old equality filter (evaluated_at=first_evaluated_at) should be gone
    assert "exposure_rank=1" in src, (
        "_system_operator_alpha must filter on exposure_rank=1 from ROW_NUMBER"
    )


def test_to_utc_iso_normalizes_offset_timestamps():
    """#1266: _to_utc_iso converts offset-formatted timestamps to UTC."""
    from aletheia.analytics.filter_controller import _to_utc_iso
    # Same moment: '+08:00' is 8 hours ahead of UTC
    result = _to_utc_iso("2026-08-22T10:00:00+08:00")
    assert "02:00:00" in result, f"Expected UTC 02:00:00, got {result}"
    assert "+00:00" in result


def test_filter_controller_uses_utc_normalization_for_first_exposure():
    """#1266: first_exposure comparison uses _to_utc_iso to prevent offset replacement."""
    import aletheia.analytics.filter_controller as mod
    src = inspect.getsource(mod._batch_scanner_weights_quota_stats)
    assert "_to_utc_iso" in src, (
        "first_exposure comparison must use _to_utc_iso for correct UTC ordering (#1266)"
    )
