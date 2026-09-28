"""Read-only reports of archived paired-exit outcomes."""

from __future__ import annotations

from tests.helpers.position_fixture import force_update_position

import json


from aletheia.analytics.exit_breakeven_shadow import (
    build_exit_breakeven_shadow_report,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import Position


OPENED = "2026-08-08T00:00:00+00:00"
ASSIGNED = "2026-08-08T00:00:10+00:00"
CLOSED = "2026-08-08T00:05:30+00:00"
BASE_MS = 1_786_147_200_000


def _seed_position(position_id: str = "exit-shadow-position", *, direction: str = "long") -> dict:
    position = Position(
        id=position_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction=direction,
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        realized_pnl=0.0,
        execution_mode="paper_internal",
        stop_price=95.0 if direction == "long" else 105.0,
        take_profit_price=110.0 if direction == "long" else 90.0,
        entry_fee=0.06,
        opened_at=OPENED,
    )
    repo._insert("positions", position.model_dump())
    return repo.get_position(position.id)


def _assignment(position: dict, *, direction: str = "long") -> dict:
    rule_json = json.dumps({"test": True}, sort_keys=True, separators=(",", ":"))
    import hashlib

    return dict(
        experiment_version="exit-breakeven-shadow/v1",
        treatment_id="entry-lock-after-1.5pct/v1",
        timeframe="1m",
        position_id=position["id"],
        assigned_at=ASSIGNED,
        venue="bitget",
        symbol="BTC/USDT",
        market_type="spot",
        direction=direction,
        entry_price=100.0,
        quantity=1.0,
        entry_fee=0.06,
        exit_fee_rate=0.0006,
        slippage_bps=6.0,
        trigger_return_pct=1.5,
        trigger_price=101.5 if direction == "long" else 98.5,
        shadow_stop_price=100.0,
        exit_plan_version=1,
        exit_plan_effective_at=OPENED,
        exit_plan_hash=hashlib.sha256(b"archived-plan").hexdigest(),
        rule_json=rule_json,
        rule_hash=hashlib.sha256(rule_json.encode()).hexdigest(),
        created_at=ASSIGNED,
    )


def _close(
    position_id: str,
    *,
    realized_pnl: float = -1.0,
    close_reason: str | None = None,
) -> dict:
    force_update_position(
        position_id,
        status="closed",
        current_price=99.0,
        realized_pnl=realized_pnl,
        closed_at=CLOSED,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        close_reason=close_reason,
    )
    return repo.get_position(position_id)


def test_report_bootstrap_is_deterministic_by_position(monkeypatch):
    rows = [
        {
            "position_id": "b",
            "status": "unchanged",
            "treatment_changed": 0,
            "incremental_net_pnl": 2.0,
            "close_reason": None,
        },
        {
            "position_id": "a",
            "status": "unchanged",
            "treatment_changed": 0,
            "incremental_net_pnl": 0.0,
            "close_reason": None,
        },
    ]
    monkeypatch.setattr(repo, "list_exit_breakeven_shadow_report_rows", lambda **_: rows)

    first = build_exit_breakeven_shadow_report()
    rows.reverse()
    second = build_exit_breakeven_shadow_report()

    assert first["incremental_net_pnl_mean_ci_95"] == [0.0, 2.0]
    assert second["incremental_net_pnl_mean_ci_95"] == first["incremental_net_pnl_mean_ci_95"]


def test_report_counts_closed_assignment_without_outcome_as_unsettled():
    position = _seed_position()
    repo._insert("exit_breakeven_shadow_assignments", _assignment(position))
    _close(position["id"])

    before = repo.get_db().total_changes
    report = build_exit_breakeven_shadow_report()
    assert repo.get_db().total_changes == before

    assert report["outcome_count"] == 1
    assert report["available_count"] == 0
    assert report["unavailable_count"] == 0
    assert report["unsettled_count"] == 1
    assert report["outcome_coverage"] == 0.0
