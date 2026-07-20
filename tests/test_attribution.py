"""Tests for aletheia.analytics.attribution (trade attribution).

Pure-function unit tests plus a light integration check that the report
script emits the attribution section when supplied with synthetic outcomes.
"""
from __future__ import annotations

import aletheia.analytics.attribution as attr


def _mk(symbol, strategy, reason, pnl, closed_at="2026-01-01T00:00:00"):
    return {
        "symbol": symbol,
        "strategy_id": strategy,
        "close_reason": reason,
        "realized_pnl": pnl,
        "closed_at": closed_at,
    }


def test_group_pnls_buckets_and_unknown():
    outs = [
        _mk("BTC", "a", "tp", 10.0),
        _mk("BTC", "a", "tp", -4.0),
        _mk("ETH", "b", None, 5.0),
        _mk("ETH", "", "sl", 2.0),
    ]
    g = attr.group_pnls(outs, "symbol")
    assert set(g) == {"BTC", "ETH"}
    assert g["BTC"] == [10.0, -4.0]

    # None / empty dimension collapses into a single "(unknown)" bucket.
    gr = attr.group_pnls(outs, "close_reason")
    assert "(unknown)" in gr
    assert gr["(unknown)"] == [5.0]


def test_group_pnls_null_or_nonnumeric_pnl():
    assert attr.group_pnls([{"symbol": "X", "realized_pnl": None}], "symbol")["X"] == [0.0]
    assert attr.group_pnls(
        [{"symbol": "X", "realized_pnl": "not-a-number"}], "symbol"
    )["X"] == [0.0]


def test_segment_stats_empty():
    s = attr.segment_stats([])
    assert s["trade_count"] == 0
    assert s["profit_factor"] == 0.0
    assert s["max_drawdown"] == 0.0


def test_segment_stats_values():
    pnls = [10.0, -4.0, 5.0]
    s = attr.segment_stats(pnls)
    assert s["trade_count"] == 3
    assert s["win_rate_pct"] == round(2 / 3 * 100, 2)
    assert s["total_pnl"] == 11.0
    # gross profit = 10 + 5 = 15, gross loss = 4 -> 3.75
    assert s["profit_factor"] == 15.0 / 4.0
    assert s["expectancy"] == round(11.0 / 3, 6)
    # equity curve = [10, 6, 11] -> peak-to-trough drawdown = 4.0
    assert s["max_drawdown"] == 4.0


def test_attribute_structure():
    outs = [
        _mk("BTC", "s1", "tp", 10.0),
        _mk("BTC", "s1", "sl", -2.0),
        _mk("ETH", "s2", "tp", 5.0),
    ]
    a = attr.attribute(outs)
    assert a["outcome_count"] == 3
    assert set(a["by_symbol"]) == {"BTC", "ETH"}
    assert a["by_symbol"]["BTC"]["total_pnl"] == 8.0
    assert set(a["by_strategy"]) == {"s1", "s2"}
    assert set(a["by_close_reason"]) == {"tp", "sl"}
    # top contributor by total PnL should be BTC (8.0) over ETH (5.0).
    assert a["top_contributors"]["best"][0][0] == "BTC"


def test_top_contributors_worst_ordering():
    seg = {
        "A": {"total_pnl": 10.0},
        "B": {"total_pnl": -3.0},
        "C": {"total_pnl": 1.0},
    }
    tc = attr.top_contributors(seg, "total_pnl", 2)
    assert [n for n, _ in tc["best"]] == ["A", "C"]
    # worst is ascending: most-negative first.
    assert [n for n, _ in tc["worst"]] == ["B", "C"]


def test_report_wiring_emits_attribution(monkeypatch):
    outs = [
        _mk("BTC", "s1", "tp", 10.0),
        _mk("ETH", "s2", "sl", -2.0),
    ]
    import aletheia.analytics.outcome_integrity as oi
    import performance_report

    monkeypatch.setattr(oi, "eligible_strategy_outcomes", lambda **kw: outs)
    rep = performance_report._collect()
    assert "attribution" in rep
    assert rep["attribution"]["by_symbol"]["BTC"]["total_pnl"] == 10.0
