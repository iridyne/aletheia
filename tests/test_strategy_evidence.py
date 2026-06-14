"""Unit tests for aletheia.analytics.strategy_evidence."""
from __future__ import annotations

from aletheia.analytics.strategy_evidence import (
    _classify_evidence_status,
    _recommend_action,
)


# ---------------------------------------------------------------------------
# Evidence status classification
# ---------------------------------------------------------------------------

def test_evidence_not_executable_no_ruleset():
    status = _classify_evidence_status(
        has_ruleset=False, compile_status="no_ruleset",
        latest_backtest=None, paper_summary=None, drift=None,
    )
    assert status == "not_executable"


def test_evidence_not_executable_compile_error():
    status = _classify_evidence_status(
        has_ruleset=True, compile_status="compile_error",
        latest_backtest=None, paper_summary=None, drift=None,
    )
    assert status == "not_executable"


def test_evidence_needs_backtest():
    status = _classify_evidence_status(
        has_ruleset=True, compile_status="ok",
        latest_backtest=None, paper_summary=None, drift=None,
    )
    assert status == "needs_backtest"


def test_evidence_backtest_killed_negative_exp():
    status = _classify_evidence_status(
        has_ruleset=True, compile_status="ok",
        latest_backtest={"expectancy": -0.5, "profit_factor": 0.5},
        paper_summary=None, drift=None,
    )
    assert status == "backtest_killed"


def test_evidence_backtest_killed_low_pf():
    status = _classify_evidence_status(
        has_ruleset=True, compile_status="ok",
        latest_backtest={"expectancy": 0.1, "profit_factor": 0.6},
        paper_summary=None, drift=None,
    )
    assert status == "backtest_killed"


def test_evidence_needs_paper():
    status = _classify_evidence_status(
        has_ruleset=True, compile_status="ok",
        latest_backtest={"expectancy": 1.0, "profit_factor": 1.5},
        paper_summary={"trade_count": 2, "expectancy": 0.5},
        drift=None,
    )
    assert status == "needs_paper"


def test_evidence_paper_failing():
    status = _classify_evidence_status(
        has_ruleset=True, compile_status="ok",
        latest_backtest={"expectancy": 1.0, "profit_factor": 1.5},
        paper_summary={"trade_count": 5, "expectancy": -0.3},
        drift={"drift_status": "backtest_false_positive"},
    )
    assert status == "paper_failing"


def test_evidence_paper_promising():
    status = _classify_evidence_status(
        has_ruleset=True, compile_status="ok",
        latest_backtest={"expectancy": 1.0, "profit_factor": 1.5},
        paper_summary={"trade_count": 5, "expectancy": 0.8},
        drift={"drift_status": "paper_confirming"},
    )
    assert status == "paper_promising"


# ---------------------------------------------------------------------------
# Recommended actions
# ---------------------------------------------------------------------------

def test_action_needs_executable_ruleset():
    action, reasons = _recommend_action("not_executable", None, None, None)
    assert action == "needs_executable_ruleset"


def test_action_retire_backtest_killed():
    action, reasons = _recommend_action("backtest_killed", None, None, None)
    assert action == "retire_candidate"


def test_action_pause_paper_failing():
    action, reasons = _recommend_action(
        "paper_failing", {"drift_status": "paper_underperforming"}, None, None
    )
    assert action == "pause_paper_trading"


def test_action_retire_false_positive():
    action, reasons = _recommend_action(
        "paper_failing", {"drift_status": "backtest_false_positive"}, None, None
    )
    assert action == "retire_candidate"


def test_action_keep_running_promising():
    action, reasons = _recommend_action("paper_promising", None, None, None)
    assert action == "keep_running"


def test_action_mark_under_review():
    action, reasons = _recommend_action("review_needed", None, None, None)
    assert action == "mark_under_review"
