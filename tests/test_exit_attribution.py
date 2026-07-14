"""Tests for exit attribution (M0, exit-intent plan).

summarize_closures is a pure function over closed-position rows, so these tests
use synthetic rows and assert the reason/category counts directly — no DB.
"""

from aletheia.analytics.exit_attribution import summarize_closures
from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION


def _strategy_row(reason: str) -> dict:
    """A fully-verified strategy close (eligible → category 'strategy')."""
    return {
        "close_reason": reason,
        "status": "closed",
        "close_category": "strategy",
        "exit_price_source": "broker_fill",
        "exit_fill_verified": 1,
        "outcome_eligible": 1,
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
    }


def _legacy_row(reason: str) -> dict:
    """Old close with no structured evidence → category 'unknown'."""
    return {"close_reason": reason, "status": "closed"}


def test_empty_input():
    att = summarize_closures([])
    assert att.total == 0
    assert att.by_reason == {}
    assert att.by_category == {}
    assert att.strategy_by_reason == {}
    assert "no closed positions" in att.format_report()


def test_reason_and_category_counts():
    rows = [
        _strategy_row("stop_loss"),
        _strategy_row("stop_loss"),
        _strategy_row("take_profit"),
        {"close_reason": "reconciled_gone", "status": "closed"},   # administrative
        {"close_reason": "units_bug_cleanup", "status": "closed"},  # maintenance
        _legacy_row("max_hold"),                                    # unknown
    ]
    att = summarize_closures(rows)

    assert att.total == 6
    assert att.by_reason == {
        "stop_loss": 2,
        "take_profit": 1,
        "reconciled_gone": 1,
        "units_bug_cleanup": 1,
        "max_hold": 1,
    }
    assert att.by_category["strategy"] == 3
    assert att.by_category["administrative"] == 1
    assert att.by_category["maintenance"] == 1
    assert att.by_category["unknown"] == 1
    # strategy-only breakdown excludes admin/maintenance/unknown
    assert att.strategy_by_reason == {"stop_loss": 2, "take_profit": 1}
    # reason→category split is preserved
    assert att.by_reason_category["reconciled_gone"] == {"administrative": 1}
    assert att.by_reason_category["max_hold"] == {"unknown": 1}


def test_missing_reason_defaults_to_unknown_label():
    att = summarize_closures([{"close_reason": None, "status": "closed"}, {"status": "closed"}])
    assert att.total == 2
    assert att.by_reason == {"unknown": 2}


def test_format_report_smoke():
    rows = [_strategy_row("stop_loss"), {"close_reason": "reconciled_gone", "status": "closed"}]
    report = summarize_closures(rows).format_report()
    assert "exit attribution — 2 closed positions" in report
    assert "stop_loss" in report
    assert "strategy-only exits" in report


# ── DB-backed path: verifies list_closed_positions + attribution_report end-to-end
# against the autouse in-memory SQLite DB (not just the pure summarizer). ──────────

def _insert_closed(db, *, pid, reason, mode="paper_internal", closed_at, strategy=True):
    cols = {
        "id": pid,
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "direction": "long",
        "quantity": 1.0,
        "entry_price": 100.0,
        "execution_mode": mode,
        "status": "closed",
        "close_reason": reason,
        "closed_at": closed_at,
    }
    if strategy:
        cols.update({
            "close_category": "strategy",
            "exit_price_source": "broker_fill",
            "exit_fill_verified": 1,
            "outcome_eligible": 1,
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        })
    keys = ",".join(cols)
    placeholders = ",".join("?" for _ in cols)
    db.execute(f"INSERT INTO positions ({keys}) VALUES ({placeholders})", tuple(cols.values()))


def test_db_roundtrip_filters_and_report():
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import list_closed_positions
    from aletheia.analytics.exit_attribution import attribution_report

    db = get_db()
    _insert_closed(db, pid="p1", reason="stop_loss", closed_at="2026-07-12T00:00:00+00:00")
    _insert_closed(db, pid="p2", reason="take_profit", mode="bitget_demo",
                   closed_at="2026-07-11T00:00:00+00:00")
    _insert_closed(db, pid="p3", reason="reconciled_gone", strategy=False,
                   closed_at="2026-01-01T00:00:00+00:00")
    # An OPEN position must never appear in closed-position analytics.
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status) VALUES "
        "('open1','ETH/USDT','usdt_futures','long',1,10,'paper_internal','open')"
    )

    # status filter: open excluded
    assert {r["id"] for r in list_closed_positions()} == {"p1", "p2", "p3"}
    # execution_mode filter
    assert {r["id"] for r in list_closed_positions(execution_mode="bitget_demo")} == {"p2"}
    assert list_closed_positions(execution_mode="binance_testnet") == []

    # full attribution over all closed
    att = attribution_report()
    assert att.total == 3
    assert att.by_reason == {"stop_loss": 1, "take_profit": 1, "reconciled_gone": 1}
    assert att.strategy_by_reason == {"stop_loss": 1, "take_profit": 1}
    assert att.by_category["administrative"] == 1

    # since window (days): p3 (Jan) drops, July rows stay
    recent = attribution_report(days=30)
    assert recent.total == 2
    assert set(recent.by_reason) == {"stop_loss", "take_profit"}
