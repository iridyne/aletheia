"""Regression scenario for the 2026-07-13 confidence control-loop deadlock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml


SCENARIO_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "control_loop"
    / "confidence_deadlock_20260713.yaml"
)


class _FrozenDateTime(datetime):
    current = datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        return value if tz is None else value.astimezone(tz)


def _load_scenario() -> dict:
    return yaml.safe_load(SCENARIO_PATH.read_text(encoding="utf-8"))


def _prepare_workspace(tmp_path, monkeypatch, scenario: dict):
    import aletheia.config.loader as loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import get_db, reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    reset_db()
    run_migrations()

    cfg = AletheiaConfig()
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = scenario["initial_state"]["kill_switch_active"]
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.trading.min_confidence_to_trade = scenario["initial_state"][
        "min_confidence_to_trade"
    ]
    cfg.risk.max_demo_position_notional_usdt = 200.0
    loader.save_config(cfg)
    return get_db()


def _seed_real_control_facts(scenario: dict) -> None:
    from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        Fill,
        Opportunity,
        Order,
        Position,
        RiskDecision,
        TradeProposal,
    )

    now = _FrozenDateTime.current
    last_fill_at = now - timedelta(
        hours=scenario["initial_state"]["hours_since_last_fill"]
    )

    proposal = TradeProposal(
        id="dcls-confidence-proposal",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        execution_mode="paper_internal",
        status="executed",
        created_at=last_fill_at.isoformat(),
    )
    decision = RiskDecision(
        id="dcls-confidence-risk",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode="paper_internal",
        proposal_execution_mode="paper_internal",
        created_at=last_fill_at.isoformat(),
    )
    order = Order(
        id="dcls-confidence-order",
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        execution_mode="paper_internal",
        status="filled",
        created_at=last_fill_at.isoformat(),
        filled_at=last_fill_at.isoformat(),
    )
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(decision)
    repo.insert_order(order)
    repo.insert_fill(Fill(
        id="dcls-confidence-fill",
        order_id=order.id,
        fill_price=60_000.0,
        fill_quantity=0.001,
        filled_at=last_fill_at.isoformat(),
    ))

    # 2 wins / 21 verified outcomes = 9.5238%, the recorded 9.5% incident value.
    for index in range(21):
        closed_at = now - timedelta(hours=1, minutes=index)
        repo.insert_position(Position(
            id=f"dcls-confidence-position-{index:02d}",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=60_000.0,
            realized_pnl=1.0 if index < 2 else -1.0,
            execution_mode="paper_internal",
            strategy_id="dcls-confidence",
            status="closed",
            close_reason="simulation_verified_exit",
            close_category="strategy",
            exit_price_source="local_fill",
            exit_fill_verified=True,
            outcome_eligible=True,
            outcome_exclusion_reason="eligible",
            outcome_semantics_version=OUTCOME_SEMANTICS_VERSION,
            opened_at=(closed_at - timedelta(hours=1)).isoformat(),
            closed_at=closed_at.isoformat(),
        ))

    counts = scenario["initial_state"]["candidates"]
    for confidence, count in counts.items():
        for index in range(count):
            repo.insert_opportunity(Opportunity(
                id=f"dcls-{confidence}-{index}",
                symbol=f"DCLS{confidence[:1].upper()}{index}/USDT",
                market="spot",
                direction_bias="long",
                thesis="deterministic control-loop candidate",
                confidence=confidence,
                confidence_score=80 if confidence == "high" else 55,
                source_agent="dcls",
                scanner_family="dcls",
                created_at=(now - timedelta(minutes=index)).isoformat(),
            ))


def _assert_no_opposing_actions(summary: dict) -> None:
    actions = {result.get("action") for result in summary["results"]}
    assert not {"tighten_confidence", "loosen_confidence"}.issubset(actions), (
        "one Operator cycle emitted opposing confidence actions"
    )


def test_confidence_deadlock_recovers_or_persists_visible_blocker(
    tmp_path, monkeypatch
):
    scenario = _load_scenario()
    db = _prepare_workspace(tmp_path, monkeypatch, scenario)
    _seed_real_control_facts(scenario)

    import aletheia.agent_shell.operator_loop as operator_loop
    import aletheia.agent_shell.trading_context as trading_context
    from aletheia.config.loader import load_config

    monkeypatch.setattr(operator_loop, "datetime", _FrozenDateTime)
    monkeypatch.setattr(
        trading_context,
        "_get_data_health_summary",
        lambda: {
            "ohlcv_ready": True,
            "members_with_full_coverage": 8,
            "total_members": 8,
            "signal_win_rate_24h": scenario["initial_state"][
                "signal_win_rate_24h"
            ],
            "signal_status": "degraded",
        },
    )
    operator_loop._action_log.clear()

    first = operator_loop.run_operator_loop(trigger=scenario["incident_id"])
    _assert_no_opposing_actions(first)
    assert first["assessment"]["signal_win_rate"] == pytest.approx(37.2)
    assert first["assessment"]["win_rate_7d"] == pytest.approx(9.5, abs=0.1)
    assert first["assessment"]["hours_since_last_trade"] == pytest.approx(34.5)

    current_confidence = load_config().trading.min_confidence_to_trade
    blockers = db.execute(
        "SELECT * FROM audit_events "
        "WHERE event_type='operator_control_loop_blocked' "
        "AND entity_id='min_confidence_to_trade'"
    ).fetchall()
    recovered = current_confidence == "medium"
    assert recovered or blockers, (
        "liveness invariant failed: confidence stayed tightened with no durable, "
        "visible blocker"
    )

    loosen_results = [
        result for result in first["results"]
        if result.get("action") == "loosen_confidence"
    ]
    assert not any(result.get("status") == "proposed" for result in loosen_results), (
        "loosen_confidence was orphaned in an unconsumed proposal"
    )

    if recovered:
        assert any(result.get("status") == "executed" for result in loosen_results)
        audit = db.execute(
            "SELECT * FROM audit_events WHERE event_type='config_patch' "
            "AND entity_id='min_confidence_to_trade' AND after_state='medium'"
        ).fetchall()
        assert audit, "confidence recovery was not persisted to the audit log"
        assert "confidence drought: no fills in 34.5h" in audit[0]["message"]
        assert "high→medium" in audit[0]["message"]

        from aletheia.agents.paper_trader_ranker import build_paper_candidates

        candidates = build_paper_candidates(limit=20)
        medium = [c for c in candidates if c.get("confidence") == "medium"]
        assert len(medium) == scenario["initial_state"]["candidates"]["medium"]
        assert all(
            any(
                step["filter_name"] == "ranker_confidence_gate"
                and step["decision"] == "passed"
                for step in candidate["filter_trace"]
            )
            for candidate in medium
        )

        second = operator_loop.run_operator_loop(trigger=scenario["incident_id"])
        _assert_no_opposing_actions(second)
        assert not any(
            result.get("action") == "tighten_confidence"
            and result.get("status") in {"executed", "proposed"}
            for result in second["results"]
        ), "unchanged facts immediately re-tightened confidence after recovery"
