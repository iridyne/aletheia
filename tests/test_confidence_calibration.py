"""Focused regressions for typed confidence-calibration availability."""

from __future__ import annotations


def test_perfect_calibration_zero_ece_keeps_full_score(monkeypatch):
    import extensions.analytics.confidence_calibration as calibration

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
    import extensions.analytics.confidence_calibration as calibration

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
    import extensions.analytics.confidence_calibration as calibration

    monkeypatch.setattr(
        calibration,
        "get_latest_calibration_report",
        lambda: {"total_executed": 20, "ece": 0.0},
    )

    assert calibration.compute_calibration_score() is None


def test_go_live_calibration_keeps_zero_ece_as_a_pass(monkeypatch):
    import extensions.analytics.confidence_calibration as calibration
    from aletheia.analytics.go_live_scorecard import _check_calibration

    report = {
        "total_executed": 20,
        "calibration_state": "available",
        "ece": 0.0,
        "ece_grade": "excellent",
    }
    monkeypatch.setattr(calibration, "get_latest_calibration_report", lambda: report)
    monkeypatch.setattr(calibration, "compute_calibration_score", lambda: 100.0)
    from aletheia.analytics import go_live_scorecard
    monkeypatch.setattr(go_live_scorecard, "_compute_calibration_score", calibration.compute_calibration_score)
    monkeypatch.setattr(go_live_scorecard, "_get_calibration_report", calibration.get_latest_calibration_report)

    result = _check_calibration()

    assert result["status"] == "pass"
    assert result["score"] == 100.0
    assert "ECE=0.000" in result["detail"]


import pytest


@pytest.fixture
def selected_account():
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)


def _close_with_decision(position_id, pnl, *, hours_ago, decisions=(("trade", 70),), proposal_id=None):
    from datetime import datetime, timedelta, timezone

    from aletheia.storage.sqlite import get_db
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    proposal_id = proposal_id or f"proposal-{position_id}"
    now = datetime.now(timezone.utc)
    _insert_closed_position_via_producer(
        position_id=position_id, symbol="BTC/USDT", market_type="usdt_futures",
        direction="long", quantity=1.0, entry_price=100.0, execution_mode="bitget_demo",
        proposal_id=proposal_id, realized_pnl=pnl,
        closed_at=(now - timedelta(hours=hours_ago)).isoformat(),
    )
    db = get_db()
    for index, (action, conviction) in enumerate(decisions):
        db.execute(
            "INSERT INTO pi_decisions (id, created_at, action, conviction, proposal_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"decision-{position_id}-{index}", (now - timedelta(hours=hours_ago + 1)).isoformat(),
             action, conviction, proposal_id),
        )
    db.commit()


def test_calibration_reads_the_whole_window_beyond_ten_thousand_closes(selected_account):
    """#1955: the newest-10k reader silently dropped the oldest closes."""
    from extensions.analytics.confidence_calibration import build_calibration_report
    from tests.test_go_live_scorecard_evidence import _clone_close

    _close_with_decision("oldest-loss", -1.0, hours_ago=3)
    _close_with_decision("win", 1.0, hours_ago=2)
    _clone_close("win", 9_999)  # clones share the win's Proposal and decision

    report = build_calibration_report()

    assert report is not None
    assert report["total_executed"] == 10_001
    assert report["source_outcome_count"] == 10_001
    assert report["source_raw_event_count"] == 10_001
    assert report["cohort_complete"] is True


def test_calibration_counts_ambiguous_and_unlinked_decisions_separately(selected_account):
    """#1955: unlinked or ambiguous closes are counted, never silently skipped."""
    from extensions.analytics.confidence_calibration import build_calibration_report

    for index in range(5):
        _close_with_decision(f"linked-{index}", 1.0, hours_ago=2)
    # Two final decisions for one Proposal: the canonical reader reports ambiguity.
    _close_with_decision("duplicate", -1.0, hours_ago=2, decisions=(("trade", 90), ("trade", 60)))
    _close_with_decision("unlinked", -1.0, hours_ago=2, decisions=())
    _close_with_decision("skipped", -1.0, hours_ago=2, decisions=(("skip", 40),))

    report = build_calibration_report()

    assert report is not None
    assert report["total_executed"] == 5
    assert report["source_outcome_count"] == 8
    assert report["linked_count"] == 5
    assert report["linkage_exclusions"] == {
        "decision_ambiguous": 1,
        "decision_missing": 1,
        "non_trade_decision": 1,
    }
    assert report["overall_win_rate"] == 1.0


def test_calibration_is_unavailable_when_the_cohort_cannot_be_read(selected_account, monkeypatch):
    import extensions.analytics.confidence_calibration as calibration
    from tests.test_outcome_consumers import _unavailable_cohort

    for index in range(5):
        _close_with_decision(f"linked-{index}", 1.0, hours_ago=2)
    monkeypatch.setattr(calibration, "read_complete_outcome_cohort", lambda **_: _unavailable_cohort())

    assert calibration.build_calibration_report() is None
