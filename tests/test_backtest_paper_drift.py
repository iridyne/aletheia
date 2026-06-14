"""Unit tests for aletheia.analytics.backtest_paper_drift."""
from __future__ import annotations

from aletheia.analytics.backtest_paper_drift import _classify_drift


def test_drift_no_backtest():
    status, reasons = _classify_drift(
        bt=None, paper_count=5, min_paper_trades=3,
        bt_expectancy=None, paper_expectancy=0.5, paper_to_backtest_ratio=None,
    )
    assert status == "no_backtest"


def test_drift_no_paper_evidence():
    status, reasons = _classify_drift(
        bt={"id": "bt-1"}, paper_count=0, min_paper_trades=3,
        bt_expectancy=1.0, paper_expectancy=0.0, paper_to_backtest_ratio=None,
    )
    assert status == "no_paper_evidence"


def test_drift_paper_not_enough():
    status, reasons = _classify_drift(
        bt={"id": "bt-1"}, paper_count=2, min_paper_trades=3,
        bt_expectancy=1.0, paper_expectancy=0.5, paper_to_backtest_ratio=None,
    )
    assert status == "paper_not_enough"


def test_drift_backtest_false_positive():
    status, reasons = _classify_drift(
        bt={"id": "bt-1"}, paper_count=5, min_paper_trades=3,
        bt_expectancy=2.0, paper_expectancy=-0.5, paper_to_backtest_ratio=-0.25,
    )
    assert status == "backtest_false_positive"


def test_drift_backtest_bad_and_paper_bad():
    status, reasons = _classify_drift(
        bt={"id": "bt-1"}, paper_count=5, min_paper_trades=3,
        bt_expectancy=-1.0, paper_expectancy=-0.5, paper_to_backtest_ratio=0.5,
    )
    assert status == "backtest_bad_and_paper_bad"


def test_drift_paper_outperforming():
    status, reasons = _classify_drift(
        bt={"id": "bt-1"}, paper_count=5, min_paper_trades=3,
        bt_expectancy=1.0, paper_expectancy=1.5, paper_to_backtest_ratio=1.5,
    )
    assert status == "paper_outperforming"


def test_drift_paper_underperforming():
    status, reasons = _classify_drift(
        bt={"id": "bt-1"}, paper_count=5, min_paper_trades=3,
        bt_expectancy=2.0, paper_expectancy=0.5, paper_to_backtest_ratio=0.25,
    )
    assert status == "paper_underperforming"


def test_drift_paper_confirming():
    status, reasons = _classify_drift(
        bt={"id": "bt-1"}, paper_count=5, min_paper_trades=3,
        bt_expectancy=1.0, paper_expectancy=0.8, paper_to_backtest_ratio=0.8,
    )
    assert status == "paper_confirming"
