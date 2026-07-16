"""Deterministic System and Operator viability accounting."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.viability import (
    build_operator_viability,
    build_system_viability,
)
from aletheia.storage.sqlite import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_known_llm_cost(
    *,
    event_id: str,
    cost: float,
    run_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    now = _now()
    ensure_economics_schema()
    get_db().execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,run_id,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,cost_status,
            metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            "llm",
            run_id,
            event_id,
            "test",
            "model",
            0,
            "success",
            now,
            now,
            "{}",
            cost,
            "USD",
            "estimated",
            json.dumps(metadata or {}),
            now,
        ),
    )


def test_system_viability_uses_already_net_genuine_outcome(monkeypatch):
    ensure_economics_schema()
    _insert_known_llm_cost(event_id="system-cost", cost=1.0)
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **kwargs: [{"id": "position-1", "realized_pnl": 3.0}],
    )

    report = build_system_viability(
        days=1,
        data_cost_usd=0,
        infrastructure_cost_usd=0,
        other_cost_usd=0,
        persist=False,
    )
    assert report["net_trading_result_usd"] == pytest.approx(3.0)
    assert report["deployment_cost_usd"] == pytest.approx(1.0)
    assert report["viability_amount_usd"] == pytest.approx(2.0)
    assert report["verdict"] == "positive"
    assert any("not deducted twice" in item for item in report["assumptions"])


def test_system_viability_fails_closed_when_fixed_costs_missing(monkeypatch):
    ensure_economics_schema()
    _insert_known_llm_cost(event_id="system-cost-incomplete", cost=0.1)
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **kwargs: [{"id": "position-1", "realized_pnl": 3.0}],
    )
    report = build_system_viability(days=1, persist=False)
    assert report["verdict"] == "invalid"
    assert report["cost_completeness"] == "fixed_cost_incomplete"
    assert report["viability_amount_usd"] is None


def test_operator_viability_requires_same_close_shadow_cohort(monkeypatch):
    db = ensure_economics_schema()
    now = _now()
    db.execute(
        """INSERT INTO trade_proposals
           (id,opportunity_id,symbol,market_type,direction,quantity,price,
            execution_mode,status,created_at)
           VALUES ('proposal-1','opportunity-1','BTC/USDT','spot','long',1,100,
                   'paper_internal','executed',?)""",
        (now,),
    )
    db.execute(
        """INSERT INTO shadow_trades
           (id,batch_id,opportunity_id,symbol,market_type,direction,rank,entry_price,
            was_selected,exit_price,pnl_pct,net_pnl_pct,resolved_at,cost_model_version,created_at)
           VALUES
           ('selected-shadow','batch-1','opportunity-1','BTC/USDT','spot','long',1,100,
            1,101.5,1.5,1.5,?,'shadow-cost-v1',?),
           ('control-shadow','batch-1','opportunity-2','ETH/USDT','spot','long',2,100,
            0,100.5,0.5,0.5,?,'shadow-cost-v1',?)""",
        (now, now, now, now),
    )
    _insert_known_llm_cost(
        event_id="operator-cost",
        cost=0.2,
        metadata={
            "logical_agent": "pi_trading_decision",
            "identity_source": "active_module_stack",
        },
    )
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **kwargs: [
            {
                "id": "position-1",
                "proposal_id": "proposal-1",
                "market_type": "spot",
                "direction": "long",
                "entry_price": 100.0,
                "quantity": 1.0,
                "realized_pnl": 1.5,
                "closed_at": now,
            }
        ],
    )

    report = build_operator_viability(days=1, data_cost_usd=0, persist=False)
    assert report["sample_count"] == 1
    assert report["baseline_result_usd"] == pytest.approx(0.5)
    assert report["incremental_value_usd"] == pytest.approx(1.0)
    assert report["viability_amount_usd"] == pytest.approx(0.8)
    assert report["verdict"] == "positive"
    assert report["operator_run_count"] == 0
    assert report["logical_operator_cost_event_count"] == 1


def test_operator_horizon_mismatch_is_excluded(monkeypatch):
    db = ensure_economics_schema()
    closed_at = datetime.now(timezone.utc)
    shadow_at = (closed_at - timedelta(hours=2)).isoformat()
    closed_iso = closed_at.isoformat()
    db.execute(
        """INSERT INTO trade_proposals
           (id,opportunity_id,symbol,market_type,direction,quantity,price,
            execution_mode,status,created_at)
           VALUES ('proposal-mismatch','opportunity-mismatch','BTC/USDT','spot','long',1,100,
                   'paper_internal','executed',?)""",
        (closed_iso,),
    )
    db.execute(
        """INSERT INTO shadow_trades
           (id,batch_id,opportunity_id,symbol,market_type,direction,rank,entry_price,
            was_selected,exit_price,pnl_pct,net_pnl_pct,resolved_at,cost_model_version,created_at)
           VALUES ('selected-mismatch','batch-mismatch','opportunity-mismatch','BTC/USDT',
                   'spot','long',1,100,1,101,1,1,?,'shadow-cost-v1',?)""",
        (shadow_at, shadow_at),
    )
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **kwargs: [
            {
                "id": "position-mismatch",
                "proposal_id": "proposal-mismatch",
                "market_type": "spot",
                "direction": "long",
                "entry_price": 100.0,
                "quantity": 1.0,
                "realized_pnl": 1.0,
                "closed_at": closed_iso,
            }
        ],
    )
    report = build_operator_viability(days=1, data_cost_usd=0, persist=False)
    assert report["verdict"] == "insufficient_data"
    assert report["exclusion_counts"]["shadow_horizon_mismatch"] == 1


def test_viability_report_is_idempotent_for_fixed_window(monkeypatch):
    ensure_economics_schema()
    as_of = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    event_time = (as_of - timedelta(hours=1)).isoformat()
    get_db().execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,
            cost_status,metadata_json,created_at
        ) VALUES ('idempotent-cost','llm','idempotent-cost','test','model',0,'success',
                  ?,?,'{}',0.25,'USD','estimated','{}',?)""",
        (event_time, event_time, event_time),
    )
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **kwargs: [{"id": "position-idempotent", "realized_pnl": 1.0}],
    )
    kwargs = dict(
        days=1,
        data_cost_usd=0,
        infrastructure_cost_usd=0,
        other_cost_usd=0,
        window_end=as_of,
    )
    first = build_system_viability(**kwargs)
    second = build_system_viability(**kwargs)
    assert first["report_id"] == second["report_id"]
    assert first["dedup_key"] == second["dedup_key"]
    count = get_db().execute(
        "SELECT COUNT(*) FROM viability_reports WHERE id=?",
        (first["report_id"],),
    ).fetchone()[0]
    assert count == 1
