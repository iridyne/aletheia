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


def test_memo_scoring_fades_with_age(monkeypatch):
    """#131: a stale memo must move ranker scoring far less than a fresh one."""
    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.regime.detector import MarketRegime
    from datetime import datetime, timezone, timedelta

    rule = {"regime": "bull", "direction": "long", "edge": "strong"}

    def _memo(days_old):
        gen = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        return {"regime_playbook": [rule], "symbol_affinity": [], "anti_patterns": [], "generated_at": gen}

    regime = MarketRegime(state="bull", confidence=0.8)

    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: _memo(0))
    fresh = ranker._memo_scoring("BTC/USDT", "long", regime)
    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: _memo(30))
    stale = ranker._memo_scoring("BTC/USDT", "long", regime)

    assert fresh > 5                    # fresh "strong" rule adds ~+12
    assert 0 <= stale < fresh / 2       # 30-day-old memo (7-day half-life) is heavily faded


def test_case_builder_windows_on_close_time():
    """#16: the case-builder window filters on CLOSE time, not open time."""
    from aletheia.analytics.trade_case_builder import _closed_after
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600  # last 24h
    recent_close = {"opened_at": "2026-06-01T00:00:00+00:00",
                    "closed_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    old_close = {"opened_at": "2026-06-01T00:00:00+00:00",
                 "closed_at": "2026-06-02T00:00:00+00:00"}
    assert _closed_after(recent_close, cutoff) is True    # opened long ago, closed recently → captured
    assert _closed_after(old_close, cutoff) is False
    assert _closed_after({"closed_at": None}, cutoff) is False
