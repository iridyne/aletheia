"""Tests for the adjust_position action (M3, exit-intent plan).

apply_operator_adjustment lets the System Operator revise an open position's ExitPlan
without closing it: validates the stop against the catastrophic bound, bumps the
version, re-marks provenance, re-arms the horizon wake, and syncs the exchange stop.
"""

import aletheia.portfolio.position_manager as pm
from aletheia.storage.repositories import _select_one


def _insert(db, pid, *, direction="long", entry=100.0, stop=None, horizon=None,
            wake_at=None, version=0):
    cols = {
        "id": pid, "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": direction, "quantity": 1.0, "entry_price": entry,
        "execution_mode": "paper_internal", "status": "open", "exit_plan_version": version,
    }
    if stop is not None:
        cols["stop_price"] = stop
    if horizon is not None:
        cols["expected_horizon_hours"] = horizon
    if wake_at is not None:
        cols["horizon_wake_sent_at"] = wake_at
    keys = ",".join(cols)
    ph = ",".join("?" for _ in cols)
    db.execute(f"INSERT INTO positions ({keys}) VALUES ({ph})", tuple(cols.values()))


def test_adjust_updates_plan_and_bumps_version():
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "a1", entry=100.0, stop=98.0, version=0)

    res = pm.apply_operator_adjustment(
        "a1", new_stop=99.0, new_target=110.0, new_horizon=24.0, reason="tighten after run-up"
    )
    assert res["updated"] is True
    assert res["version"] == 1
    assert res["exchange_synced"] is False  # paper_internal has no exchange bracket

    row = _select_one("positions", "a1")
    assert row["stop_price"] == 99.0
    assert row["take_profit_price"] == 110.0
    assert row["expected_horizon_hours"] == 24.0
    assert row["stop_source"] == "operator"
    assert row["exit_plan_version"] == 1


def test_rejects_stop_looser_than_catastrophic_long():
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "a2", entry=100.0, stop=98.0, version=0)
    # default catastrophic short_term = 10% → line at 90; a stop at 85 is looser → reject
    res = pm.apply_operator_adjustment("a2", new_stop=85.0)
    assert res.get("error") == "stop_looser_than_catastrophic"
    row = _select_one("positions", "a2")
    assert row["stop_price"] == 98.0          # unchanged
    assert row["exit_plan_version"] == 0      # not bumped


def test_short_catastrophic_bound():
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "a4", direction="short", entry=100.0, stop=102.0, version=0)
    # short catastrophic line at 110; 115 is looser → reject, 108 is inside → ok
    assert pm.apply_operator_adjustment("a4", new_stop=115.0).get("error") == "stop_looser_than_catastrophic"
    assert pm.apply_operator_adjustment("a4", new_stop=108.0)["updated"] is True


def test_horizon_change_rearms_wake():
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "a3", entry=100.0, horizon=10.0, wake_at="2026-07-01T00:00:00+00:00", version=2)

    res = pm.apply_operator_adjustment("a3", new_horizon=20.0, reason="let it run")
    assert res["updated"] is True
    assert res["version"] == 3

    row = _select_one("positions", "a3")
    assert row["expected_horizon_hours"] == 20.0
    assert row["horizon_wake_sent_at"] is None   # re-armed → can wake again at the new horizon


def test_adjust_missing_position():
    res = pm.apply_operator_adjustment("does-not-exist", new_stop=99.0)
    assert res.get("error") == "position_not_found_or_not_open"
