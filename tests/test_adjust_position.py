"""Tests for the adjust_position action (M3, exit-intent plan).

apply_operator_adjustment lets the System Operator revise an open position's ExitPlan
without closing it: validates the stop against the catastrophic bound, bumps the
version, re-marks provenance, re-arms the horizon wake, and syncs the exchange stop.
"""

import pytest

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


def _insert_authorized(
    pid: str,
    *,
    direction: str = "long",
    entry: float = 100.0,
    stop: float | None = None,
    target: float | None = None,
    version: int = 0,
) -> None:
    """Create the native Proposal -> RiskDecision -> Order -> Position authority chain."""
    from aletheia.storage.models import Order, Position, RiskDecision, TradeProposal
    from aletheia.storage.repositories import (
        insert_order,
        insert_position,
        insert_risk_decision,
        insert_trade_proposal,
    )

    proposal_stop = stop if stop is not None else (98.0 if direction == "long" else 102.0)
    proposal_target = target if target is not None else (108.0 if direction == "long" else 92.0)
    proposal = TradeProposal(
        id=f"proposal-{pid}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction=direction,
        order_type="market",
        quantity=1.0,
        price=entry,
        stop_price=proposal_stop,
        take_profit=proposal_target,
        risk_reward=4.0,
        execution_mode="paper_internal",
        source="native",
    )
    insert_trade_proposal(proposal)
    decision = RiskDecision(
        id=f"risk-{pid}",
        proposal_id=proposal.id,
        approved=True,
        violations="[]",
        allowed_execution_mode="paper_internal",
        proposal_execution_mode="paper_internal",
        decision_role="authoritative",
        evaluated_quantity=1.0,
        proposal_commit_hash=proposal.commit_hash,
    )
    insert_risk_decision(decision)
    order = Order(
        id=f"order-{pid}",
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=1.0,
        price=entry,
        execution_mode=proposal.execution_mode,
        status="filled",
        source="native",
    )
    insert_order(order)
    insert_position(Position(
        id=pid,
        proposal_id=proposal.id,
        order_id=order.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        quantity=1.0,
        entry_price=entry,
        execution_mode=proposal.execution_mode,
        status="open",
        stop_price=proposal_stop,
        take_profit_price=proposal_target,
        exit_plan_version=version,
        source="native",
    ))


def _plan_state(pid: str) -> tuple:
    row = _select_one("positions", pid)
    return tuple(
        row[field]
        for field in (
            "stop_price",
            "take_profit_price",
            "expected_horizon_hours",
            "trailing_json",
            "stop_source",
            "exit_plan_version",
            "horizon_wake_sent_at",
        )
    )


def test_adjust_updates_plan_and_bumps_version(monkeypatch):
    _insert_authorized("a1", entry=100.0, stop=98.0, version=0)
    observed_marks = []
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: observed_marks.append(
            (symbol, market_type, venue)
        ) or 105.0,
    )
    monkeypatch.setattr(
        pm,
        "_sync_exchange_stop",
        lambda position_id, stop, position, cfg: False,
    )

    res = pm.apply_operator_adjustment(
        "a1",
        new_stop=103.0,
        new_target=108.0,
        new_horizon=24.0,
        new_trailing={
            "mode": "pct",
            "distance": 1.0,
            "activation_profit_pct": 0.5,
            "floor_at_breakeven": True,
        },
        reason="tighten into profit after run-up",
    )
    assert res["updated"] is True
    assert res["version"] == 1
    assert res["exchange_synced"] is False  # paper_internal has no exchange bracket
    assert len(observed_marks) == 1
    assert observed_marks[0][:2] == ("BTC/USDT", "usdt_futures")
    assert observed_marks[0][2]

    row = _select_one("positions", "a1")
    assert row["stop_price"] == 103.0  # profit-locking stops remain legal
    assert row["take_profit_price"] == 108.0
    assert row["expected_horizon_hours"] == 24.0
    assert row["trailing_json"]
    assert row["stop_source"] == "operator"
    assert row["exit_plan_version"] == 1


@pytest.mark.parametrize(
    ("direction", "old_stop", "profit_stop", "mark"),
    [
        ("long", 98.0, 103.0, 105.0),
        ("short", 102.0, 97.0, 95.0),
    ],
)
def test_stop_only_adjustment_may_lock_profit_with_one_fresh_mark(
    monkeypatch, direction, old_stop, profit_stop, mark
):
    from aletheia.storage.sqlite import get_db

    _insert(
        get_db(),
        "profit-stop",
        direction=direction,
        entry=100.0,
        stop=old_stop,
        version=1,
    )
    mark_fetches = []
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: mark_fetches.append(
            (symbol, market_type, venue)
        ) or mark,
    )
    monkeypatch.setattr(pm, "_sync_exchange_stop", lambda *args, **kwargs: False)

    result = pm.apply_operator_adjustment("profit-stop", new_stop=profit_stop)

    assert result["updated"] is True
    row = _select_one("positions", "profit-stop")
    assert row["stop_price"] == profit_stop
    assert row["exit_plan_version"] == 2
    assert len(mark_fetches) == 1


@pytest.mark.parametrize(
    ("direction", "old_stop", "mark", "new_stop"),
    [
        ("long", 98.0, 101.0, 102.0),
        ("long", 98.0, 101.0, 101.0),
        ("short", 102.0, 99.0, 98.0),
        ("short", 102.0, 99.0, 99.0),
    ],
)
def test_stop_must_stay_strictly_on_the_protective_side_of_fresh_mark(
    monkeypatch, direction, old_stop, mark, new_stop
):
    from aletheia.storage.sqlite import get_db

    _insert(
        get_db(),
        "wrong-side-stop",
        direction=direction,
        entry=100.0,
        stop=old_stop,
        version=6,
    )
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: mark,
    )
    before = _plan_state("wrong-side-stop")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("wrong-side-stop", new_stop=new_stop)

    assert result["error"] == "stop_geometry_invalid"
    assert result["hint"] == "use_proactive_exit"
    assert _plan_state("wrong-side-stop") == before
    assert get_db().total_changes == writes_before


@pytest.mark.parametrize("mark", [None, float("nan"), float("inf"), "raise"])
def test_stop_requires_fresh_exact_mark_and_writes_nothing(monkeypatch, mark):
    from aletheia.storage.sqlite import get_db

    _insert(get_db(), "stop-mark", entry=100.0, stop=98.0, version=3)

    def mark_result(symbol, market_type, *, venue):  # noqa: ARG001
        if mark == "raise":
            raise RuntimeError("market source unavailable")
        return mark

    monkeypatch.setattr(pm, "get_fresh_price_for_protective_close", mark_result)
    before = _plan_state("stop-mark")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("stop-mark", new_stop=99.0)

    assert result["error"] == "stop_fresh_mark_unavailable"
    assert _plan_state("stop-mark") == before
    assert get_db().total_changes == writes_before


@pytest.mark.parametrize(
    ("direction", "old_stop", "new_stop"),
    [
        ("long", 98.0, 97.0),
        ("short", 102.0, 103.0),
    ],
)
def test_stop_cannot_loosen_existing_finite_protection(
    monkeypatch, direction, old_stop, new_stop
):
    from aletheia.storage.sqlite import get_db

    _insert(
        get_db(),
        "loosen-stop",
        direction=direction,
        entry=100.0,
        stop=old_stop,
        version=8,
    )
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda *args, **kwargs: pytest.fail("static loosening must fail before mark fetch"),
    )
    before = _plan_state("loosen-stop")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("loosen-stop", new_stop=new_stop)

    assert result["error"] == "stop_would_loosen_protection"
    assert _plan_state("loosen-stop") == before
    assert get_db().total_changes == writes_before


@pytest.mark.parametrize(
    ("kwargs", "expected_error"),
    [
        ({"new_stop": float("nan")}, "invalid_new_stop"),
        ({"new_stop": float("inf")}, "invalid_new_stop"),
        ({"new_stop": -1.0}, "invalid_new_stop"),
        ({"new_stop": True}, "invalid_new_stop"),
        ({"new_target": float("nan")}, "invalid_new_target"),
        ({"new_target": float("inf")}, "invalid_new_target"),
        ({"new_target": -1.0}, "invalid_new_target"),
        ({"new_target": "108.0"}, "invalid_new_target"),
        ({"new_horizon": float("nan")}, "invalid_new_horizon"),
        ({"new_horizon": float("inf")}, "invalid_new_horizon"),
        ({"new_horizon": -1.0}, "invalid_new_horizon"),
        ({"new_horizon": False}, "invalid_new_horizon"),
        ({"new_trailing": "pct"}, "invalid_new_trailing"),
        ({"new_trailing": {"distance": float("nan")}}, "invalid_new_trailing"),
        ({"new_trailing": {"distance": float("inf")}}, "invalid_new_trailing"),
        ({"new_trailing": {"distance": -1.0}}, "invalid_new_trailing"),
        ({"new_trailing": {"activation_profit_pct": 0.0}}, "invalid_new_trailing"),
        ({"new_trailing": {"floor_at_breakeven": 1}}, "invalid_new_trailing"),
        ({"new_trailing": {"mode": "mystery"}}, "invalid_new_trailing"),
    ],
)
def test_invalid_adjustment_values_fail_before_any_write(kwargs, expected_error):
    from aletheia.storage.sqlite import get_db

    _insert(get_db(), "invalid", entry=100.0, stop=98.0, horizon=12.0, version=7)
    before = _plan_state("invalid")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("invalid", **kwargs)

    assert result["error"] == expected_error
    assert _plan_state("invalid") == before
    assert get_db().total_changes == writes_before


def test_target_requires_original_risk_approved_entry_plan(monkeypatch):
    from aletheia.storage.sqlite import get_db

    _insert(get_db(), "legacy", entry=100.0, stop=98.0, version=4)
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 103.0,
    )
    before = _plan_state("legacy")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("legacy", new_target=108.0)

    assert result["error"] == "target_risk_authority_unavailable"
    assert _plan_state("legacy") == before
    assert get_db().total_changes == writes_before


def test_target_rejects_a_non_approved_risk_chain_without_writing(monkeypatch):
    from aletheia.storage.sqlite import get_db

    _insert_authorized("rejected-risk", entry=100.0, stop=98.0, version=4)
    get_db().execute(
        "UPDATE risk_decisions SET approved=0 WHERE id=?",
        ("risk-rejected-risk",),
    )
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 103.0,
    )
    before = _plan_state("rejected-risk")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("rejected-risk", new_target=108.0)

    assert result["error"] == "target_risk_authority_unavailable"
    assert _plan_state("rejected-risk") == before
    assert get_db().total_changes == writes_before


@pytest.mark.parametrize("mark", [None, float("nan"), float("inf")])
def test_target_requires_fresh_exact_mark_and_writes_nothing(monkeypatch, mark):
    from aletheia.storage.sqlite import get_db

    _insert_authorized("stale-mark", entry=100.0, stop=98.0, version=2)
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: mark,
    )
    before = _plan_state("stale-mark")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("stale-mark", new_target=108.0)

    assert result["error"] == "target_fresh_mark_unavailable"
    assert _plan_state("stale-mark") == before
    assert get_db().total_changes == writes_before


@pytest.mark.parametrize(
    ("direction", "mark", "target"),
    [
        ("long", 103.0, 102.0),
        ("short", 97.0, 98.0),
    ],
)
def test_target_must_remain_beyond_entry_and_mark(
    monkeypatch, direction, mark, target
):
    from aletheia.storage.sqlite import get_db

    _insert_authorized("wrong-side", direction=direction, entry=100.0, version=5)
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: mark,
    )
    before = _plan_state("wrong-side")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("wrong-side", new_target=target)

    assert result["error"] == "target_geometry_invalid"
    assert _plan_state("wrong-side") == before
    assert get_db().total_changes == writes_before


@pytest.mark.parametrize(
    ("mark", "target"),
    [
        (100.25, 100.5),  # 0.50R
        (101.0, 111.0),   # 11.00R
    ],
)
def test_target_reuses_entry_plan_risk_reward_envelope(monkeypatch, mark, target):
    from aletheia.storage.sqlite import get_db

    _insert_authorized("rr", entry=100.0, stop=99.0, target=104.0, version=3)
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: mark,
    )
    before = _plan_state("rr")
    writes_before = get_db().total_changes

    result = pm.apply_operator_adjustment("rr", new_target=target)

    assert result["error"] == "target_risk_reward_outside_envelope"
    assert _plan_state("rr") == before
    assert get_db().total_changes == writes_before


def test_rejects_stop_looser_than_catastrophic_long():
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "a2", entry=100.0, stop=98.0, version=0)
    # default catastrophic short_term = 10% → line at 90; a stop at 85 is looser → reject
    res = pm.apply_operator_adjustment("a2", new_stop=85.0)
    assert res.get("error") == "stop_looser_than_catastrophic"
    row = _select_one("positions", "a2")
    assert row["stop_price"] == 98.0          # unchanged
    assert row["exit_plan_version"] == 0      # not bumped


def test_short_catastrophic_bound(monkeypatch):
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "a4", direction="short", entry=100.0, stop=102.0, version=0)
    # short catastrophic line at 110; 115 is looser.  A valid adjustment must also
    # tighten the existing 102 stop and remain above the fresh mark.
    assert pm.apply_operator_adjustment("a4", new_stop=115.0).get("error") == "stop_looser_than_catastrophic"
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 99.0,
    )
    assert pm.apply_operator_adjustment("a4", new_stop=101.0)["updated"] is True


def test_horizon_change_rearms_wake():
    from aletheia.storage.sqlite import get_db
    _insert(get_db(), "a3", entry=100.0, horizon=10.0, wake_at="2026-07-01T00:00:00+00:00", version=2)

    res = pm.apply_operator_adjustment("a3", new_horizon=20.0, reason="let it run")
    assert res["updated"] is True
    assert res["version"] == 3

    row = _select_one("positions", "a3")
    assert row["expected_horizon_hours"] == 20.0
    assert row["horizon_wake_sent_at"] is None   # re-armed → can wake again at the new horizon


def test_noop_adjustment_is_rejected_without_version_bump():
    from aletheia.portfolio import position_manager as pm
    from aletheia.storage.sqlite import get_db

    _insert(get_db(), "noop-adjustment", entry=100.0, stop=95.0, horizon=10.0, version=4)
    before = get_db().total_changes

    result = pm.apply_operator_adjustment("noop-adjustment")

    assert result == {
        "error": "no_adjustment_requested",
        "position_id": "noop-adjustment",
    }
    assert get_db().total_changes == before
    row = get_db().execute(
        "SELECT exit_plan_version FROM positions WHERE id='noop-adjustment'"
    ).fetchone()
    assert row["exit_plan_version"] == 4


def test_horizon_must_be_strictly_later_than_current_position_age(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from aletheia.portfolio import position_manager as pm
    from aletheia.storage.sqlite import get_db

    _insert(get_db(), "expired-horizon", entry=100.0, stop=95.0, horizon=4.0, version=2)
    opened_at = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    get_db().execute(
        "UPDATE positions SET opened_at=? WHERE id='expired-horizon'",
        (opened_at,),
    )
    before = get_db().total_changes

    result = pm.apply_operator_adjustment(
        "expired-horizon", new_horizon=10.0
    )

    assert result == {
        "error": "new_horizon_not_in_future",
        "position_id": "expired-horizon",
    }
    assert get_db().total_changes == before


def test_stop_sync_only_reports_success_when_dispatcher_replaces(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "aletheia.execution.stops.replace_exchange_protective_stop",
        lambda position_id, stop: calls.append((position_id, stop)) or True,
    )
    assert pm._sync_exchange_stop("supported", 99.0, {}, object()) is True
    assert calls == [("supported", 99.0)]

    monkeypatch.setattr(
        "aletheia.execution.stops.replace_exchange_protective_stop",
        lambda position_id, stop: False,
    )
    assert pm._sync_exchange_stop("unsupported", 99.0, {}, object()) is False


def test_adjust_missing_position():
    res = pm.apply_operator_adjustment("does-not-exist", new_stop=99.0)
    assert res.get("error") == "position_not_found_or_not_open"
