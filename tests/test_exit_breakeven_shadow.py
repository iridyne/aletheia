from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from aletheia.analytics.exit_breakeven_shadow import (
    assign_new_protected_position,
    build_exit_breakeven_shadow_report,
    settle_position_shadow,
)
from aletheia.data.providers import Candle
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    ExitBreakevenShadowAssignment,
    Position,
    PositionExitPlanVersion,
    PositionProtectionState,
)


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
    repo.insert_position(position)
    payload = json.dumps(
        {
            "exit_limit_price": None,
            "exit_limit_source": None,
            "stop_price": position.stop_price,
            "take_profit_price": position.take_profit_price,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    plan = PositionExitPlanVersion(
        position_id=position.id,
        version=1,
        effective_at=OPENED,
        stop_price=position.stop_price,
        take_profit_price=position.take_profit_price,
        plan_json=payload,
        plan_hash=repo.hash_position_exit_plan_json(payload),
        provenance="test",
        created_at=OPENED,
    )
    assert repo.append_position_exit_plan_version(plan, expected_effective_at=None)
    assert repo.insert_position_protection_state(PositionProtectionState(
        position_id=position.id,
        venue="bitget",
        symbol="BTC/USDT",
        market_type="spot",
        activated_at=OPENED,
        next_bar_open_ms=BASE_MS,
        created_at=OPENED,
        updated_at=OPENED,
    ))
    return repo.get_position(position.id)


def _assignment(position: dict, *, direction: str = "long") -> ExitBreakevenShadowAssignment:
    plan = repo.get_current_position_exit_plan(position["id"])
    rule_json = json.dumps({"test": True}, sort_keys=True, separators=(",", ":"))
    import hashlib

    return ExitBreakevenShadowAssignment(
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
        exit_plan_hash=plan["plan_hash"],
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
    repo.update_position(
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


def _bars(values: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [
        Candle(
            timestamp=BASE_MS + (index + 1) * 60_000,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=100.0,
        )
        for index, (open_, high, low, close) in enumerate(values)
    ]


def _store_bars(candles: list[Candle]) -> None:
    repo.upsert_ohlcv_candles(
        "BTC/USDT",
        "1m",
        candles,
        provider="test",
        exchange="bitget",
        market_type="spot",
        window=100,
    )


def test_assignment_is_prospective_immutable_and_non_authoritative():
    position = _seed_position()
    bad = _assignment(position).model_copy(update={"rule_hash": "0" * 64})
    with pytest.raises(repo.ExitBreakevenShadowConflict, match="rule_hash mismatch"):
        repo.insert_exit_breakeven_shadow_assignment(bad)

    result = assign_new_protected_position(position["id"])

    stored = repo.get_exit_breakeven_shadow_assignment(position["id"])
    assert result["assigned"] is True
    assert stored["behavior_authority"] == 0
    assert stored["treatment_id"] == "entry-lock-after-1.5pct/v1"
    assert stored["assigned_at"] >= position["opened_at"]
    with pytest.raises(Exception, match="immutable"):
        repo.get_db().execute(
            "UPDATE exit_breakeven_shadow_assignments SET venue='okx' WHERE position_id=?",
            (position["id"],),
        )


def test_long_shadow_closes_only_after_later_bar_and_reports_incremental_net(monkeypatch):
    position = _seed_position()
    assert repo.insert_exit_breakeven_shadow_assignment(_assignment(position))
    _store_bars(_bars([
        (100.2, 101.6, 100.1, 101.2),  # trigger; stop cannot activate in this bar
        (101.2, 101.4, 100.4, 100.8),
        (100.8, 101.0, 99.8, 100.1),   # later stop touch; use completed-bar close
        (100.1, 100.3, 99.7, 99.9),
    ]))

    outcome = settle_position_shadow(_close(position["id"], close_reason="stop_loss"))

    assert outcome["status"] == "counterfactual_close"
    assert outcome["trigger_bar_open_ms"] == BASE_MS + 60_000
    assert outcome["shadow_exit_bar_open_ms"] == BASE_MS + 180_000
    assert outcome["shadow_fill_price"] == pytest.approx(100.1 * (1 - 0.0006))
    assert outcome["incremental_net_pnl"] > 0
    report = build_exit_breakeven_shadow_report()
    assert report["changed_count"] == 1
    assert report["outcome_coverage"] == 1.0
    assert report["incremental_net_pnl_mean_ci_95"] is None
    assert report["bootstrap_method"] == "position_percentile_bootstrap/v1"
    assert report["close_reason_attribution_available"] is True
    assert report["close_reason_breakdown"] == [{
        "close_reason": "stop_loss",
        "outcome_count": 1,
        "available_count": 1,
        "changed_count": 1,
        "incremental_net_pnl_total": pytest.approx(outcome["incremental_net_pnl"]),
        "incremental_net_pnl_mean": pytest.approx(outcome["incremental_net_pnl"]),
    }]
    assert report["promotion_eligible"] is False

    import aletheia.analytics.alpha_attribution as alpha_attribution

    monkeypatch.setattr(
        alpha_attribution,
        "_utcnow",
        lambda: datetime(2026, 8, 9, 10, tzinfo=timezone.utc),
    )
    paired = alpha_attribution.build_alpha_attribution_audit(days=30)["exit_paired_evidence"]
    assert paired["measurement_status"] == "treatment_specific_paired_evidence"
    assert paired["statistical_unit"] == "position_id"
    assert paired["economic_unit"] == "net_pnl_quote_currency"
    assert paired["contract_version"] == "exit-paired-evidence/v2"
    assert paired["incremental_net_pnl_mean"] == pytest.approx(outcome["incremental_net_pnl"])
    assert paired["behavior_authority"] is False
    assert paired["contribution_verdict"] == "insufficient_data"
    assert "stop_loss: available=1 changed=1" in alpha_attribution.render_alpha_audit(
        alpha_attribution.build_alpha_attribution_audit(days=30)
    )


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
    monkeypatch.setattr(repo, "list_exit_breakeven_shadow_outcomes", lambda **_: rows)

    first = build_exit_breakeven_shadow_report()
    rows.reverse()
    second = build_exit_breakeven_shadow_report()

    assert first["incremental_net_pnl_mean_ci_95"] == [0.0, 2.0]
    assert second["incremental_net_pnl_mean_ci_95"] == first["incremental_net_pnl_mean_ci_95"]


def test_same_bar_trigger_and_stop_is_not_retrospectively_activated():
    position = _seed_position()
    assert repo.insert_exit_breakeven_shadow_assignment(_assignment(position))
    _store_bars(_bars([
        (100.0, 101.7, 99.5, 101.0),
        (101.0, 101.2, 100.2, 100.8),
        (100.8, 101.1, 100.1, 100.7),
        (100.7, 101.0, 100.2, 100.5),
    ]))

    outcome = settle_position_shadow(_close(position["id"], realized_pnl=0.25))

    assert outcome["status"] == "unchanged"
    assert outcome["shadow_net_pnl"] == pytest.approx(0.25)
    assert outcome["incremental_net_pnl"] == 0.0


def test_short_shadow_is_directionally_symmetric():
    position = _seed_position("exit-shadow-short", direction="short")
    assert repo.insert_exit_breakeven_shadow_assignment(
        _assignment(position, direction="short")
    )
    _store_bars(_bars([
        (99.8, 99.9, 98.4, 98.8),
        (98.8, 99.5, 98.6, 99.2),
        (99.2, 100.2, 99.0, 99.9),
        (99.9, 100.4, 99.6, 100.2),
    ]))

    outcome = settle_position_shadow(_close(position["id"], realized_pnl=-0.8))

    assert outcome["status"] == "counterfactual_close"
    assert outcome["shadow_fill_price"] == pytest.approx(99.9 * (1 + 0.0006))
    assert outcome["incremental_net_pnl"] > 0


def test_missing_exact_bar_fails_closed_and_does_not_invent_delta():
    position = _seed_position()
    assert repo.insert_exit_breakeven_shadow_assignment(_assignment(position))
    _store_bars(_bars([
        (100.2, 101.6, 100.1, 101.2),
        (101.2, 101.4, 100.4, 100.8),
        (100.8, 101.0, 99.8, 100.1),
    ]))

    outcome = settle_position_shadow(_close(position["id"]))

    assert outcome["status"] == "unavailable"
    assert outcome["reason"] == "exact_1m_coverage_unavailable"
    assert outcome["incremental_net_pnl"] is None


def test_existing_outcome_outbox_replays_shadow_without_trading_authority(monkeypatch):
    from aletheia.portfolio import outcome_hooks
    import aletheia.analytics.exit_breakeven_shadow as shadow

    position = _seed_position()
    assert repo.insert_exit_breakeven_shadow_assignment(_assignment(position))
    closed = _close(position["id"], realized_pnl=0.1)
    jobs = outcome_hooks.enqueue_outcome_hook_jobs(closed)
    assert {job["hook_type"] for job in jobs} == {
        outcome_hooks.SHADOW_TRADES_HOOK,
        outcome_hooks.EXIT_BREAKEVEN_SHADOW_HOOK,
    }

    delivered: list[str] = []
    monkeypatch.setattr(
        outcome_hooks,
        "_load_eligible_position",
        lambda position_id: (closed, "eligible"),
    )
    monkeypatch.setattr(
        shadow,
        "settle_position_shadow",
        lambda row: delivered.append(row["id"]),
    )

    summary = outcome_hooks.process_outcome_hook_jobs(
        position_id=position["id"],
        hook_type=outcome_hooks.EXIT_BREAKEVEN_SHADOW_HOOK,
    )

    assert summary["completed"] == 1
    assert delivered == [position["id"]]
