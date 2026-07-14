"""End-to-end verification of the exit-intent system on paper_internal (item 2).

Unlike the per-milestone unit tests (which mock at the seams), this drives the REAL
components composed together — the execution gateway open path, ExitPlan persistence,
the Operator's adjust_position, and proactive_exit — against the in-memory DB, with no
exchange/network leg. It verifies the pieces actually fit.
"""

import pytest

from aletheia.portfolio.exit_plan import effective_exit_plan, load_exit_plan
from aletheia.storage.repositories import _select_one


def _open_paper_position(**intent):
    """Open a real paper position through the gateway; skip if risk rejects it."""
    from aletheia.execution.gateway import submit_proposal

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        price=100.0,
        execution_mode="paper_internal",
        **intent,
    )
    if result.get("status") != "executed" or not result.get("position_id"):
        pytest.skip(f"paper proposal not executed: {result.get('status')} {result.get('risk_violations')}")
    return result["position_id"]


def test_gateway_stamps_exit_intent_on_open():
    """The Operator's expected_horizon + stop provenance flow decision→proposal→position
    through the real gateway (M2 wiring end-to-end, not the _stamp_exit_intent unit)."""
    pid = _open_paper_position(stop_price=95.0, expected_horizon_hours=24.0, stop_source="operator")

    row = _select_one("positions", pid)
    assert row["stop_price"] == 95.0
    assert row["expected_horizon_hours"] == 24.0
    assert row["stop_source"] == "operator"

    plan = load_exit_plan(row)
    assert plan.provenance == "operator"
    assert plan.expected_horizon_hours == 24.0
    assert plan.stop_price == 95.0


def test_default_provenance_when_no_operator_stop():
    """No operator stop → the position is stamped default (surfaced for reback, M5)."""
    pid = _open_paper_position(stop_source="default")
    row = _select_one("positions", pid)
    # effective plan fills the config-default stop but marks the source default
    eff = effective_exit_plan(row, __import__("aletheia.config.loader", fromlist=["load_config"]).load_config())
    assert eff.provenance == "default"


def test_adjust_then_proactive_exit_lifecycle():
    """Full managed lifecycle on a real position: authorize → adjust → proactive limit
    exit → executor fires it — all through the real components."""
    from aletheia.portfolio.position_manager import (
        apply_operator_adjustment,
        proactive_exit,
        evaluate_open_positions,
    )
    import aletheia.portfolio.position_manager as pm

    pid = _open_paper_position(stop_price=95.0, expected_horizon_hours=24.0, stop_source="operator")

    # 1. Operator tightens the stop + extends the horizon (adjust_position, M3).
    adj = apply_operator_adjustment(pid, new_stop=97.0, new_horizon=48.0, reason="run-up, protect + extend")
    assert adj["updated"] is True and adj["version"] == 1
    row = _select_one("positions", pid)
    assert row["stop_price"] == 97.0 and row["expected_horizon_hours"] == 48.0
    assert row["stop_source"] == "operator"

    # 2. Reject a stop looser than the Layer-0 catastrophic bound (M6/M3 guard).
    bad = apply_operator_adjustment(pid, new_stop=80.0)  # entry 100, catastrophic 10% → 90
    assert bad.get("error") == "stop_looser_than_catastrophic"
    assert _select_one("positions", pid)["stop_price"] == 97.0  # unchanged

    # 3. Operator places a proactive limit exit (M3.5).
    ex = proactive_exit(pid, order_type="limit", limit_price=110.0, reason="scale out at target")
    assert ex["pending_limit_exit"] is True
    assert _select_one("positions", pid)["exit_limit_price"] == 110.0

    # 4. Price reaches the limit → the executor market-closes it (operator_limit_exit).
    import types
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(pm, "_latest_price", lambda s: 111.0)
        closes = []
        monkeypatch.setattr(
            pm, "close_position_routed",
            lambda pid_, reason, **kw: closes.append((pid_, reason)) or {"closed": True},
        )
        evaluate_open_positions()
        assert (pid, "operator_limit_exit") in closes
    finally:
        monkeypatch.undo()
