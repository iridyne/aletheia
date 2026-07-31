"""Tests for exit-outcome shadow comparison (M7, exit-intent plan)."""

from aletheia.counterfactual.exits import (
    compare_exit_outcomes,
    exit_driver,
    format_comparison,
    shadow_report,
)


def test_exit_driver_classification():
    assert exit_driver("operator_close") == "operator"
    assert exit_driver("operator_limit_exit") == "operator"
    assert exit_driver("stop_loss") == "rule"
    assert exit_driver("take_profit") == "rule"
    assert exit_driver("thesis_invalidated") == "rule"
    assert exit_driver("catastrophic_stop") == "safety"
    assert exit_driver("liquidation_proximity") == "safety"
    assert exit_driver("reconciled_gone") == "other"
    assert exit_driver(None) == "other"


def _row(reason, pnl, opened=None, closed=None):
    r = {"close_reason": reason, "realized_pnl": pnl}
    if opened:
        r["opened_at"] = opened
    if closed:
        r["closed_at"] = closed
    return r


def test_compare_groups_and_stats():
    rows = [
        _row("operator_close", 10.0),
        _row("operator_limit_exit", 5.0),
        _row("stop_loss", -8.0),
        _row("take_profit", 12.0),
        _row("catastrophic_stop", -30.0),
    ]
    stats = compare_exit_outcomes(rows)

    op = stats["operator"]
    assert op.count == 2 and op.wins == 2 and op.total_pnl == 15.0
    assert op.avg_pnl == 7.5 and op.win_rate == 1.0

    rule = stats["rule"]
    assert rule.count == 2 and rule.wins == 1 and rule.total_pnl == 4.0
    assert rule.win_rate == 0.5

    safety = stats["safety"]
    assert safety.count == 1 and safety.wins == 0 and safety.total_pnl == -30.0


def test_avg_hold_hours():
    rows = [
        _row("operator_close", 1.0, "2026-07-10T00:00:00+00:00", "2026-07-10T02:00:00+00:00"),
        _row("operator_close", 1.0, "2026-07-10T00:00:00+00:00", "2026-07-10T04:00:00+00:00"),
    ]
    op = compare_exit_outcomes(rows)["operator"]
    assert op.avg_hold_hours == 3.0  # (2 + 4) / 2


def test_empty():
    assert compare_exit_outcomes([]) == {}
    assert "no closed positions" in format_comparison({})


def test_format_smoke():
    stats = compare_exit_outcomes([_row("operator_close", 10.0), _row("stop_loss", -5.0)])
    out = format_comparison(stats)
    assert "operator" in out and "rule" in out and "win%" in out


def test_db_shadow_report():
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for pid, reason, pnl in [
        ("s1", "operator_close", 10.0),
        ("s2", "stop_loss", -8.0),
        ("s3", "take_profit", 12.0),
    ]:
        db.execute(
            "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
            "execution_mode,status,close_reason,realized_pnl,closed_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?)",
            (pid, "BTC/USDT", "usdt_futures", "long", 1.0, 100.0, "paper_internal",
             "closed", reason, pnl, "2026-07-12T00:00:00+00:00"),
        )

    stats = shadow_report()
    assert stats["operator"].count == 1 and stats["operator"].total_pnl == 10.0
    assert stats["rule"].count == 2 and stats["rule"].wins == 1
