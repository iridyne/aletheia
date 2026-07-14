"""Tests for the ExitPlan data layer (M1, exit-intent plan).

Two things must hold:
  1. Round-trip persistence: ExitPlan → flat columns → DB → back is lossless, and
     legacy rows (no new columns) read as provenance='default', version=0.
  2. Behavior equivalence: effective_exit_plan(...) returns the SAME stop/target the
     current executor computes via _effective_stop / _effective_take_profit — so the
     M2 executor swap changes nothing.
"""

from types import SimpleNamespace

import aletheia.portfolio.position_manager as pm
from aletheia.portfolio.exit_plan import (
    ExitPlan,
    effective_exit_plan,
    load_exit_plan,
    save_exit_plan,
)


def _cfg(stop=2.0, tp=4.0):
    return SimpleNamespace(
        trading=SimpleNamespace(default_stop_loss_pct=stop, default_take_profit_pct=tp)
    )


def test_load_legacy_row_defaults():
    plan = load_exit_plan({"entry_price": 100.0, "direction": "long"})
    assert plan.provenance == "default"
    assert plan.version == 0
    assert plan.stop_price is None and plan.target_price is None
    assert plan.trailing is None and plan.thesis_invalidation is None
    assert plan.expected_horizon_hours is None


def test_to_columns_and_back_lossless():
    plan = ExitPlan(
        stop_price=95.0,
        target_price=110.0,
        expected_horizon_hours=12.0,
        trailing={"mode": "atr", "distance": 1.5, "activation_profit_pct": 0.5},
        thesis_invalidation={"kind": "opportunity_status", "on_trigger": "wake"},
        provenance="operator",
        version=3,
    )
    cols = plan.to_columns()
    reloaded = load_exit_plan(
        {
            "stop_price": cols["stop_price"],
            "take_profit_price": cols["take_profit_price"],
            "expected_horizon_hours": cols["expected_horizon_hours"],
            "trailing_json": cols["trailing_json"],
            "thesis_invalidation_json": cols["thesis_invalidation_json"],
            "stop_source": cols["stop_source"],
            "exit_plan_version": cols["exit_plan_version"],
        }
    )
    assert reloaded == plan


def test_db_roundtrip():
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import _select_one

    db = get_db()
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status) VALUES "
        "('e1','BTC/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open')"
    )
    plan = ExitPlan(
        stop_price=95.0,
        target_price=110.0,
        expected_horizon_hours=8.0,
        trailing={"mode": "pct", "distance": 1.0},
        provenance="operator",
        version=2,
    )
    save_exit_plan("e1", plan)
    assert load_exit_plan(_select_one("positions", "e1")) == plan


def test_effective_matches_legacy_helpers():
    cfg = _cfg(stop=2.0, tp=4.0)
    cases = [
        {"direction": "long", "entry_price": 100.0},                             # default both
        {"direction": "short", "entry_price": 100.0},
        {"direction": "long", "entry_price": 100.0, "stop_price": 97.0},          # operator stop
        {"direction": "long", "entry_price": 100.0, "take_profit_price": 108.0},  # operator target
        {"direction": "short", "entry_price": 250.0, "stop_price": 260.0, "take_profit_price": 240.0},
        {"direction": "long", "entry_price": 0.0},                               # bad entry → None
    ]
    for pos in cases:
        eff = effective_exit_plan(pos, cfg)
        assert eff.stop_price == pm._effective_stop(pos, cfg), pos
        assert eff.target_price == pm._effective_take_profit(pos, cfg), pos


def test_effective_matches_when_no_default_pct():
    # pct=0 → no config fallback; only operator levels survive (matches helpers)
    cfg = _cfg(stop=0.0, tp=0.0)
    pos = {"direction": "long", "entry_price": 100.0, "stop_price": 97.0}
    eff = effective_exit_plan(pos, cfg)
    assert eff.stop_price == pm._effective_stop(pos, cfg) == 97.0
    assert eff.target_price is None
    assert pm._effective_take_profit(pos, cfg) is None


def test_effective_provenance():
    cfg = _cfg()
    assert effective_exit_plan({"direction": "long", "entry_price": 100.0}, cfg).provenance == "default"
    assert (
        effective_exit_plan(
            {"direction": "long", "entry_price": 100.0, "stop_price": 97.0}, cfg
        ).provenance
        == "operator"
    )
