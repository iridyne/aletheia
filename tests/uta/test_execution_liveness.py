"""The execution liveness surfaces report the stuck shape that went unnoticed for 14 days.

Fixture shape (Fedora Bitget Demo, 2026-10-08): four old entry Orders in
`submission_unknown` on credential generation N, no open Position, and a configured
account that is generation N+1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.diagnostics import doctor
from aletheia.execution import liveness
from aletheia.storage import repositories as repo

from tests.uta.test_execution_coordinator import _identity, _setup_db
from tests.uta.test_identity_liveness import MODES, _drift, _stuck_entry


class _Registry:
    def __init__(self, *identities):
        self._identities = identities

    def configured_identities(self):
        return list(self._identities)


def _age(order_id: str, hours: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    repo.get_db().execute("UPDATE orders SET created_at=? WHERE id=?", (stamp, order_id))
    repo.get_db().commit()


def _configure(monkeypatch, identity) -> None:
    from aletheia.uta import manager

    monkeypatch.setattr(manager, "build_uta_registry", lambda *a, **k: _Registry(identity))


def _check() -> doctor.Check:
    report = doctor.Report(generated_at="t")
    doctor._probe_execution_liveness(report)
    return next(c for c in report.checks if c.name == "loop.execution_liveness")


@pytest.mark.parametrize("mode", MODES)
def test_the_fedora_shape_is_reported_and_clears_when_the_orders_end(tmp_path, monkeypatch, mode):
    _setup_db(tmp_path, monkeypatch)
    old = _identity(mode)
    _configure(monkeypatch, _drift(old))
    stuck = [_stuck_entry(mode, old, symbol=f"C{i}/USDT", client_id=f"c-{i}") for i in range(4)]
    for order in stuck:
        _age(order.id, 80)

    assert len(liveness.overdue_orders()) == 4
    assert liveness.identity_blocked_modes() == [mode]
    check = _check()
    assert check.severity == "warn"
    assert "4 Order(s) non-terminal" in check.detail and mode in check.detail

    for order in stuck:
        repo.transition_order(order.id, status="cancelled", error_reason="venue_confirmed_absence")
    assert liveness.overdue_orders() == []
    assert liveness.identity_blocked_modes() == []
    assert _check().severity == "ok"


def test_a_young_order_and_an_unconfigured_mode_are_not_problems(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    old = _identity("bitget_demo")
    _stuck_entry("bitget_demo", old)  # created just now
    _configure(monkeypatch, _identity("okx_demo"))  # bitget_demo not configured
    assert liveness.overdue_orders() == []
    assert liveness.identity_blocked_modes() == []


def test_an_error_order_retry_storm_is_reported(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage.models import Order

    old = _identity("bitget_demo")
    for _ in range(liveness.RETRY_STORM_ERROR_ORDERS_PER_HOUR + 1):
        repo.insert_order(Order(
            symbol="UNI/USDT", market_type="spot", direction="long", order_type="limit", quantity=1.0,
            price=1.0, execution_mode="bitget_demo", source="exchange", status="error",
            proposal_id="storm-proposal", uta_account_identity_json=old.model_dump_json(),
        ))
    assert [s["proposal_id"] for s in liveness.retry_storm_proposals()] == ["storm-proposal"]


def test_funnel_is_dead_needs_enough_plans_and_zero_fills():
    assert liveness.funnel_is_dead({"plans": 501, "ordered": 4, "filled": 0})
    assert not liveness.funnel_is_dead({"plans": 501, "ordered": 40, "filled": 1})
    assert not liveness.funnel_is_dead({"plans": 3, "ordered": 0, "filled": 0})


def test_an_empty_database_has_an_empty_funnel(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    assert liveness.entry_funnel() == {"window_days": 7, "plans": 0, "ordered": 0, "filled": 0}


def test_overdue_orders_degrade_health(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import health

    old = _identity("bitget_demo")
    _configure(monkeypatch, _drift(old))
    stuck = _stuck_entry("bitget_demo", old)
    _age(stuck.id, 80)
    result = health.compute_health()
    assert "execution_orders_overdue:1" in result["issues"]
    assert "execution_identity_gate_blocked:bitget_demo" in result["issues"]
