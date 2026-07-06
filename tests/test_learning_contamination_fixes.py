"""Focused regressions for learning-loop contamination/dead-feedback fixes (B5a).

#157 render_calibration_for_pi must not crash on a numeric ECE (broken f-string).
#159 guard promotion/effectiveness must drop synthetic-close + pre-clean-window rows.
"""


def test_render_calibration_does_not_crash_on_numeric_ece():
    from aletheia.analytics.confidence_calibration import render_calibration_for_pi
    out = render_calibration_for_pi({"ece": 0.123, "ece_grade": "B", "total_executed": 12})
    assert "Conviction Calibration" in out
    assert "0.123" in out                       # numeric ECE renders (previously raised ValueError)
    assert "0.000" in render_calibration_for_pi({"ece": 0.0, "total_executed": 5})
    assert "?" in render_calibration_for_pi({"ece": None, "total_executed": 0})   # missing → "?"


def test_guard_clean_rows_drops_synthetic_and_pre_floor():
    from aletheia.analytics.guard_effectiveness import _clean_rows
    rows = [
        {"close_reason": "stop_loss", "closed_at": "2026-07-01T00:00:00+00:00", "realized_pnl": -5},      # genuine, post-floor
        {"close_reason": "reconciled_gone", "closed_at": "2026-07-01T00:00:00+00:00", "realized_pnl": 0}, # synthetic → drop
        {"close_reason": "take_profit", "closed_at": "2026-06-01T00:00:00+00:00", "realized_pnl": 5},     # pre-floor → drop
        {"close_reason": None, "closed_at": "2026-07-02T00:00:00+00:00", "realized_pnl": 3},              # genuine (null reason), post-floor
    ]
    kept = _clean_rows(rows)
    reasons = [r["close_reason"] for r in kept]
    assert "reconciled_gone" not in reasons     # synthetic dropped
    assert "take_profit" not in reasons          # pre-floor dropped
    assert "stop_loss" in reasons and None in reasons
    assert len(kept) == 2
