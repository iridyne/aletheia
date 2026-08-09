"""Tests for GET /api/decision-report/{opportunity_id}.

The endpoint is a pure read-only aggregation: it joins opportunity →
pi_decision → trade_proposal → risk_decision → filter_decisions →
position/PnL outcome → audit_events into one "why did we buy" report.

Asserted contracts:
- Full chain returns structured (parsed) evidence for every section.
- Partial chains degrade to null/"not_recorded" with HTTP 200.
- Unknown ids return found=false with HTTP 200 (opportunity-trace convention).
- committee_report surfaces persisted committee evaluations when present and
  honest "not_available" when none was recorded.
- A NULL realized_pnl stays null — never coerced to 0.
- GET mutates nothing: every relevant table keeps its row count.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Isolated DB fixture (mirrors tests/specs/spec_pipeline_risk_gate.py)
# ---------------------------------------------------------------------------


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    # ALETHEIA_DATA_DIR activates the runtime-home override so importing the
    # server app with an isolated DATA_DIR never trips the legacy-migration guard.
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


# ---------------------------------------------------------------------------
# Seed helpers (full decision chain via repository writers)
# ---------------------------------------------------------------------------

IDS = {
    "opportunity_id": "opp0decision01",
    "decision_id": "pid0decision0001",
    "proposal_id": "prp0decision0001",
    "risk_decision_id": "rsk0decision01",
    "position_id": "pos0decision0001",
}


def _seed_full_chain() -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        AuditEvent,
        FilterDecision,
        Opportunity,
        PiDecision,
        Position,
        RiskDecision,
        TradeProposal,
    )

    oid, dec_id = IDS["opportunity_id"], IDS["decision_id"]
    prop_id, rd_id, pos_id = (
        IDS["proposal_id"],
        IDS["risk_decision_id"],
        IDS["position_id"],
    )

    repo.insert_opportunity(
        Opportunity(
            id=oid,
            symbol="BTC/USDT",
            market="spot",
            direction_bias="long",
            thesis="Momentum continuation with funding still neutral.",
            confidence="high_confidence",
            confidence_score=78,
            scanner_family="momentum",
            key_observations=json.dumps(["breakout above range", "volume expansion"]),
            metadata=json.dumps({"evidence_manifest": {"source": "market_observer"}}),
        )
    )
    repo.insert_pi_decision(
        PiDecision(
            id=dec_id,
            opportunity_id=oid,
            signal_source="market_observer",
            action="trade",
            conviction=80,
            reasoning="Structure and flow both support continuation.",
            bear_case="Funding spike would invalidate the carry.",
            key_assumptions=json.dumps(["funding stays below 0.01%"]),
            evidence_manifest_json=json.dumps({"bundle": "momentum-v2"}),
            debate_ran=True,
            debate_initial_conviction=70,
            debate_bull_confidence=82,
            debate_bear_confidence=41,
            debate_adjustment=10,
            proposal_id=prop_id,
        )
    )
    repo.insert_trade_proposal(
        TradeProposal(
            id=prop_id,
            opportunity_id=oid,
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.1,
            price=50000.0,
            stop_price=49000.0,
            take_profit=52000.0,
            risk_reward=2.0,
            execution_mode="paper_internal",
            status="approved",
            agent_rationale="Planned bet with committed four numbers.",
            attribution_snapshot=json.dumps({"benchmark": "BTC", "regime": "risk_on"}),
        )
    )
    repo.insert_risk_decision(
        RiskDecision(
            id=rd_id,
            proposal_id=prop_id,
            approved=True,
            violations=json.dumps([]),
            decision_role="authoritative",
            message="within policy",
            policy_id="policy-1",
            policy_revision=2,
            policy_hash="hash-abc",
        )
    )
    repo.insert_filter_decision(
        FilterDecision(
            dedup_key=f"{oid}:ttl_gate:1",
            candidate_key=f"opportunity:{oid}",
            candidate_kind="opportunity",
            opportunity_id=oid,
            symbol="BTC/USDT",
            filter_name="ttl_gate",
            stage="pre_rank",
            decision="passed",
            reason_code="within_ttl",
            filter_version="v1",
        )
    )
    repo.insert_filter_decision(
        FilterDecision(
            dedup_key=f"{dec_id}:conviction_gate:1",
            candidate_key=f"pi_decision:{dec_id}",
            candidate_kind="pi_decision",
            opportunity_id=oid,
            symbol="BTC/USDT",
            filter_name="operator_conviction_gate",
            stage="operator",
            decision="passed",
            reason_code="conviction_above_minimum",
            filter_version="v1",
        )
    )
    repo.insert_position(
        Position(
            id=pos_id,
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.1,
            entry_price=50000.0,
            execution_mode="paper_internal",
            proposal_id=prop_id,
            opportunity_id=oid,
            status="closed",
            close_reason="take_profit",
            # NULL realized_pnl: the funding ledger was unavailable. Must stay null.
            realized_pnl=None,
            closed_at="2026-08-05T12:00:00+00:00",
        )
    )
    repo.insert_position_close_event(
        {
            "id": "pce0decision0001",
            "position_id": pos_id,
            "symbol": "BTC/USDT",
            "direction": "long",
            "execution_mode": "paper_internal",
            "close_reason": "take_profit",
            "exit_price": 52000.0,
            "closed_qty": 0.1,
            "realized_pnl": None,
            "closed_at": "2026-08-05T12:00:00+00:00",
        }
    )
    repo.insert_audit_event(
        AuditEvent(
            actor="system",
            source="risk_engine",
            event_type="proposal_approved",
            entity_type="trade_proposal",
            entity_id=prop_id,
            message="authoritative decision approved the proposal",
        )
    )


def _seed_opportunity_only(oid: str) -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    repo.insert_opportunity(
        Opportunity(
            id=oid,
            symbol="ETH/USDT",
            market="spot",
            direction_bias="long",
            thesis="Lonely observation, nothing downstream.",
            confidence="speculative",
        )
    )


# ---------------------------------------------------------------------------
# Endpoint client
# ---------------------------------------------------------------------------


async def _get_report(opportunity_id: str):
    from httpx import ASGITransport, AsyncClient

    from aletheia.server.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/decision-report/{opportunity_id}")


_READ_ONLY_TABLES = [
    "opportunities",
    "serenity_opportunities",
    "pi_decisions",
    "trade_proposals",
    "risk_decisions",
    "filter_decisions",
    "positions",
    "position_close_events",
    "orders",
    "audit_events",
]


def _table_counts() -> dict[str, int]:
    from aletheia.storage.sqlite import get_db

    db = get_db()
    return {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in _READ_ONLY_TABLES
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_chain_report(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_full_chain()
    before = _table_counts()

    resp = await _get_report(IDS["opportunity_id"])
    assert resp.status_code == 200
    body = resp.json()

    assert body["found"] is True
    assert body["read_only"] is True
    assert body["schema_version"] == 1
    assert body["opportunity_id"] == IDS["opportunity_id"]

    opp = body["opportunity"]
    assert opp["status"] == "ok"
    assert opp["thesis"] == "Momentum continuation with funding still neutral."
    assert opp["confidence"] == "high_confidence"
    assert opp["confidence_score"] == 78
    assert opp["key_observations"] == ["breakout above range", "volume expansion"]
    assert opp["scanner_family"] == "momentum"
    assert opp["evidence_manifest"] == {"source": "market_observer"}

    pi = body["pi_decision"]
    assert pi["status"] == "ok"
    assert pi["decision_id"] == IDS["decision_id"]
    assert pi["action"] == "trade"
    assert pi["conviction"] == 80
    assert pi["bear_case"] == "Funding spike would invalidate the carry."
    assert pi["key_assumptions"] == ["funding stays below 0.01%"]
    assert pi["evidence_manifest"] == {"bundle": "momentum-v2"}  # parsed, not raw JSON
    assert pi["debate"]["ran"] is True
    assert pi["debate"]["adjustment"] == 10

    proposal = body["proposal"]
    assert proposal["status"] == "ok"
    assert len(proposal["proposals"]) == 1
    p0 = proposal["proposals"][0]
    assert p0["proposal_id"] == IDS["proposal_id"]
    assert p0["agent_rationale"] == "Planned bet with committed four numbers."
    assert p0["attribution_snapshot"] == {"benchmark": "BTC", "regime": "risk_on"}
    assert p0["price"] == 50000.0
    assert p0["stop_price"] == 49000.0
    assert p0["take_profit"] == 52000.0
    assert p0["risk_reward"] == 2.0
    assert p0["status"] == "approved"
    assert p0["execution_mode"] == "paper_internal"

    risk = body["risk_decision"]
    assert risk["status"] == "ok"
    assert risk["approved"] is True
    assert risk["violations"] == []  # parsed list, not raw JSON string
    assert risk["message"] == "within policy"
    assert risk["decision_role"] == "authoritative"
    assert risk["policy_id"] == "policy-1"
    assert risk["policy_revision"] == 2
    assert risk["policy_hash"] == "hash-abc"

    filters = body["filter_decisions"]
    assert filters["status"] == "ok"
    names = {item["filter_name"] for item in filters["items"]}
    assert names == {"ttl_gate", "operator_conviction_gate"}
    assert all(item["reason_code"] for item in filters["items"])

    outcome = body["outcome"]
    assert outcome["status"] == "ok"
    assert len(outcome["positions"]) == 1
    pos = outcome["positions"][0]
    assert pos["entry_price"] == 50000.0
    assert pos["exit_price"] == 52000.0
    assert pos["close_reason"] == "take_profit"
    assert pos["realized_pnl"] is None  # NULL must stay null, never coerced to 0

    audit = body["audit_events"]
    assert audit["status"] == "ok"
    assert any(
        item["event_type"] == "proposal_approved" for item in audit["items"]
    )
    assert all(set(item) == {"event_type", "actor", "source", "message", "timestamp"}
               for item in audit["items"])

    assert body["committee_report"] == {
        "status": "not_available",
        "reason": "no committee evaluation was persisted for this opportunity",
    }

    # Read-only guarantee: the GET changed nothing.
    assert _table_counts() == before


@pytest.mark.anyio
async def test_partial_chain_only_opportunity(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    oid = "opp0lonely00001"
    _seed_opportunity_only(oid)

    resp = await _get_report(oid)
    assert resp.status_code == 200
    body = resp.json()

    assert body["found"] is True
    assert body["opportunity"]["status"] == "ok"
    assert body["opportunity"]["thesis"] == "Lonely observation, nothing downstream."
    assert body["pi_decision"] == {"status": "not_recorded"}
    assert body["risk_decision"] == {"status": "not_recorded"}
    assert body["proposal"]["status"] == "not_recorded"
    assert body["proposal"]["proposals"] == []
    assert body["filter_decisions"] == {"status": "no_data", "items": []}
    assert body["outcome"]["status"] == "not_recorded"
    assert body["outcome"]["positions"] is None
    assert body["audit_events"] == {"status": "no_data", "items": []}
    assert body["committee_report"]["status"] == "not_available"


@pytest.mark.anyio
async def test_unknown_opportunity_not_found(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    resp = await _get_report("doesnotexist123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["opportunity_id"] == "doesnotexist123"
    assert body["schema_version"] == 1
    assert "opportunity" not in body or body.get("opportunity") in (None, {})


@pytest.mark.anyio
async def test_committee_report_section_surfaces_persisted_evaluation(tmp_path, monkeypatch):
    """Persisted committee evaluations must be readable through the report.

    Explainability contract: the "why" behind a decision (committee direction,
    confidence, ES, LLM reasoning and evidence) is retrievable for review.
    """
    _setup_env(tmp_path, monkeypatch)
    oid = "opp0committee001"
    _seed_opportunity_only(oid)

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import CommitteeReport

    repo.insert_committee_report(
        CommitteeReport(
            opportunity_id=oid,
            symbol="BTC/USDT",
            decision_id="dec-comm-0001",
            direction="long",
            confidence=0.62,
            explainability_score=0.71,
            blocked=0,
            model_used="sensenova/deepseek-v4-flash",
            fallback_used=1,
            indicators_json=json.dumps({"rsi_14": 48.0, "atr_pct": 0.9}),
            agent_opinions_json=json.dumps([{
                "agent": "LLM",
                "direction": "long",
                "confidence": 0.62,
                "reasoning": "RSI recovery with volume expansion.",
                "evidence": [{"indicator": "RSI", "value": "RSI 48 turning up", "contribution": 10.0}],
            }]),
        )
    )

    resp = await _get_report(oid)
    assert resp.status_code == 200
    cr = resp.json()["committee_report"]
    assert cr["status"] == "ok"
    assert len(cr["evaluations"]) == 1
    ev = cr["evaluations"][0]
    assert ev["direction"] == "long"
    assert ev["confidence"] == 0.62
    assert ev["explainability_score"] == 0.71
    assert ev["decision_id"] == "dec-comm-0001"
    assert ev["fallback_used"] is True
    assert ev["indicators"] == {"rsi_14": 48.0, "atr_pct": 0.9}
    opinion = ev["agent_opinions"][0]
    assert opinion["reasoning"] == "RSI recovery with volume expansion."
    assert opinion["evidence"][0]["indicator"] == "RSI"


@pytest.mark.anyio
async def test_serenity_source_mapping(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityOpportunity

    oid = "ser0decision0001"
    repo.insert_serenity_opportunity(
        SerenityOpportunity(
            id=oid,
            theme_id="theme-1",
            symbol="SOL/USDT",
            direction="long",
            thesis="Slow accumulation theme.",
            evidence_tier="strong",
            confidence_score=66,
            stage="research",
        )
    )

    resp = await _get_report(oid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    opp = body["opportunity"]
    assert opp["source"] == "serenity"
    assert opp["scanner_family"] == "serenity"
    assert opp["confidence"] == "strong"
    assert opp["confidence_score"] == 66
    assert opp["thesis"] == "Slow accumulation theme."
