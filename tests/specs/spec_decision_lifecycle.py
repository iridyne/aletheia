"""Spec: Decision Lifecycle Contract (decision_lifecycle_contract.md)

The cross-cutting spine opportunity → PiDecision(signal_pending) → TradingDecision →
TradeProposal. This contract governs the *seams* between subsystems (the carriers and
their field semantics) that the per-subsystem contracts do not cover.

Contract: decision_lifecycle_contract
Claim prefix: DL
"""

import dataclasses
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _bypass_candidate_technical_preflight(monkeypatch) -> None:
    """Keep older seam tests focused on the downstream boundary they exercise."""
    monkeypatch.setattr(
        "aletheia.agent_shell.decision_facts.candidate_context_unavailable_reason",
        lambda *_args, **_kwargs: None,
    )


# ---------------------------------------------------------------------------
# L2a — Structural claims (carriers + their fields)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-S01", "decision_lifecycle_contract", "§2.1 Opportunity carrier", "L2a",
        "Opportunity model carries the spine fields the downstream consumers rely on: "
        "market, direction_bias, confidence (categorical), confidence_score (numeric), "
        "status, scanner_family",
    ),
)
def test_opportunity_carrier_fields():
    from aletheia.storage.models import Opportunity

    mf = Opportunity.model_fields
    for name in ("market", "direction_bias", "confidence", "confidence_score", "status", "scanner_family"):
        assert name in mf, f"Opportunity missing seam field {name!r} (decision_lifecycle §2.1)"


@spec_claim(
    SpecClaim(
        "DL-S02", "decision_lifecycle_contract", "§2.2 TradingDecision carrier", "L2a",
        "TradingDecision dataclass — the sole LLM→proposal carrier — exposes "
        "action/conviction/reasoning/bear_case (required) + market_type/opportunity_id/signal_source",
    ),
)
def test_trading_decision_carrier_fields():
    from aletheia.agent_shell.trading_decision import TradingDecision

    field_names = {f.name for f in dataclasses.fields(TradingDecision)}
    required = {
        "action", "conviction", "reasoning", "bear_case",
        "market_type", "opportunity_id", "signal_source",
    }
    missing = required - field_names
    assert not missing, f"TradingDecision missing seam fields {missing} (decision_lifecycle §2.2)"


@spec_claim(
    SpecClaim(
        "DL-S04", "decision_lifecycle_contract", "§2.2 signal_source provenance", "L2a",
        "TradingDecision.signal_source defaults to 'market_observer'; 'serenity' is the "
        "only other provenance (resolves serenity_opportunities.id). Two-loop discriminator "
        "is pi_decisions.action, NOT signal_source (see PI_OPERATOR_DESIGN_DECISIONS §问题2).",
    ),
)
def test_signal_source_default_provenance():
    from aletheia.agent_shell.trading_decision import TradingDecision

    fields = {f.name: f for f in dataclasses.fields(TradingDecision)}
    assert "signal_source" in fields
    assert fields["signal_source"].default == "market_observer", (
        "signal_source default must be 'market_observer' (decision_lifecycle §2.2)"
    )


@spec_claim(
    SpecClaim(
        "DL-S10", "decision_lifecycle_contract", "§2.3 pending carrier instrument", "L2a",
        "PiDecision persists market_type plus durable proposal status/reason so pending and "
        "final decisions remain self-describing when downstream rows are absent.",
        status="implemented",
    ),
)
def test_pi_decision_carries_market_type():
    from aletheia.storage.models import PiDecision

    for field in ("market_type", "proposal_status", "proposal_reason"):
        assert field in PiDecision.model_fields


def test_reconciliation_result_keeps_pi_decision_aligned_with_proposal_state():
    from aletheia.agent_shell.commands import _proposal_status_from_execution_result

    assert _proposal_status_from_execution_result({
        "status": "reconciliation_required",
        "proposal_status": "submission_unknown",
    }) == "submission_unknown"
    assert _proposal_status_from_execution_result({"status": "executed"}) == "executed"


# ---------------------------------------------------------------------------
# L2b — Behavioral / seam-existence claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-S03", "decision_lifecycle_contract", "§2.3 signal_pending seam", "L2b",
        "The paper_trader→PI seam exists: trading_context reads pi_decisions "
        "WHERE action='signal_pending' and populates layer['pending_signals'] "
        "(the seam carrier between hop 2 and hop 4)",
    ),
)
def test_signal_pending_seam_present():
    src = Path(__file__).parents[2] / "aletheia" / "agent_shell" / "trading_context.py"
    text = src.read_text(encoding="utf-8")
    assert "signal_pending" in text, (
        "trading_context.py must read the signal_pending seam (decision_lifecycle §2.3)"
    )
    assert "pending_signals" in text, (
        "trading_context.py must populate layer['pending_signals'] (decision_lifecycle §2.3)"
    )


@spec_claim(
    SpecClaim(
        "DL-S07", "decision_lifecycle_contract", "§2.3 signal_pending attribution", "L2b",
        "A market_observer signal_pending carrier with a real opportunity_id is a valid "
        "System Operator attribution source even after the originating Opportunity is reviewed.",
        status="implemented",
    ),
)
def test_market_observer_pending_signal_attaches_opportunity_id():
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        _attach_opportunity_context,
    )

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=70,
        reasoning="consume the queued scanner signal",
    )
    ctx = {
        "layer1": {
            "opportunities": [
                {
                    "id": "opp-competing-open",
                    "symbol": "BTC/USDT",
                    "direction_bias": "long",
                }
            ]
        },
        "layer2": {
            "ranker_candidates": [],
            "pending_signals": [
                    {
                        "symbol": "BTC/USDT",
                        "direction": "long",
                        "market_type": "spot",
                        "opportunity_id": "opp-market-pending",
                        "signal_source": "market_observer",
                    }
            ],
        },
        "layer3": {},
    }

    _attach_opportunity_context(decision, ctx)

    assert decision.opportunity_id == "opp-market-pending"
    assert decision.signal_source == "market_observer"


@spec_claim(
    SpecClaim(
        "DL-S08", "decision_lifecycle_contract", "§1 ranked operator handoff", "L2a",
        "Opportunity producers persist opportunities only; paper_trader is the market pipeline "
        "component that wakes the System Operator after ranking and signal_pending persistence.",
        status="implemented",
    ),
)
def test_opportunity_producers_do_not_bypass_ranker_when_waking_operator():
    root = Path(__file__).parents[2]
    observer = (root / "aletheia" / "agents" / "market_observer.py").read_text(encoding="utf-8")
    emitter = (root / "aletheia" / "universe" / "signal_emitter.py").read_text(encoding="utf-8")
    paper_trader = (root / "aletheia" / "agents" / "paper_trader.py").read_text(encoding="utf-8")

    assert "notify_pi_daemon" not in observer
    assert "notify_pi_daemon" not in emitter
    assert "insert_pi_decision(pi_dec)" in paper_trader
    assert "notify_pi_daemon" in paper_trader


@spec_claim(
    SpecClaim(
        "DL-S09", "decision_lifecycle_contract", "§2.4 terminal decision identity", "L2b",
        "A confirmed adverse skip may persist an Opportunity identity only when an exact "
        "signal_pending carrier authorized that evaluation; unsupported actions remain "
        "retryable and cannot consume the queue.",
        status="implemented",
    ),
)
def test_skip_decision_persists_evaluated_symbol_and_direction(tmp_path, monkeypatch):
    import asyncio
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.agent_shell import b2_evidence_policy
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-skip-identity",
        symbol="BTC/USDT",
        market="usdt_futures",
        direction_bias="long",
        status="queued",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="pending-before-skip-identity",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type=opportunity.market,
    ))
    insert_market_snapshot(MarketSnapshot(
        symbol=opportunity.symbol,
        market_type="perp",
        exchange="test",
        price=100.0,
    ))

    async def assess_pending_candidate(_context):
        return {
            "action": "skip",
            "symbol": opportunity.symbol,
            "direction": "long",
            "conviction": 48,
            "reasoning": "candidate evaluated but not selected",
            "market_type": opportunity.market,
            "skip_kind": "adverse_candidate_evidence",
            "adverse_reason_codes": ["negative_same_cohort_outcomes"],
            "adverse_evidence": ["qualified same-epoch cohort has negative shrunk net return"],
        }

    async def confirm_pending_candidate(_context, _initial, _reason):
        return await assess_pending_candidate(_context)

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess_pending_candidate)
    monkeypatch.setattr(
        trading_decision, "_reassess_direct_skip", confirm_pending_candidate
    )
    def attach_qualified_negative(ctx, *, policy_epoch_id):
        ctx["layer2"]["pending_signals"][0]["b2_evidence"] = {
            "status": "qualified_negative", "n_total": 30, "n_net": 25,
            "outcome_coverage": 0.8333, "avg_net_pct": -0.5,
            "shrunk_avg_net_pct": -0.28, "directional_verdict": "negative",
            "policy_epoch_id": policy_epoch_id,
        }
    monkeypatch.setattr(
        b2_evidence_policy, "attach_b2_candidate_evidence", attach_qualified_negative
    )
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    row = get_db().execute(
        "SELECT symbol, direction, market_type, opportunity_id, signal_source "
        "FROM pi_decisions "
        "WHERE action='skip' AND opportunity_id=?",
        (opportunity.id,),
    ).fetchone()
    opportunity_status = get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,),
    ).fetchone()[0]
    proposal_count = get_db().execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    assert result["action"] == "skip"
    assert result["symbol"] == "BTC/USDT"
    assert result["direction"] == "long"
    assert result["pending_queue_remaining"] == 0
    assert row is not None
    assert dict(row) == {
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "usdt_futures",
        "opportunity_id": "opp-skip-identity",
        "signal_source": "market_observer",
    }
    assert opportunity_status == "reviewed"
    assert proposal_count == 0


@spec_claim(
    SpecClaim(
        "DL-S11", "decision_lifecycle_contract", "§2.3 fair pending queue", "L2b",
        "The pending carrier exposes oldest-first unresolved signals, de-duplicates repeated "
        "rows only by complete execution identity before the visible limit, and reports "
        "total/overflow instead of silently dropping backlog.",
        status="implemented",
    ),
)
def test_pending_queue_is_oldest_first_unresolved_and_reports_overflow(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

    now = datetime.now(timezone.utc)
    rows = [
        ("BTC/USDT", "long", "opp-btc-oldest", -8),
        ("BTC/USDT", "long", "opp-btc-duplicate", -7),
        ("ETH/USDT", "long", "opp-eth-final", -6),
        ("SOL/USDT", "short", "opp-sol", -5),
        ("ADA/USDT", "long", "opp-ada", -4),
        ("DOT/USDT", "short", "opp-dot", -3),
        ("XRP/USDT", "long", "opp-xrp", -2),
        ("LINK/USDT", "short", "opp-link", -1),
    ]
    for symbol, direction, opportunity_id, minutes in rows:
        insert_pi_decision(PiDecision(
            symbol=symbol,
            direction=direction,
            action="signal_pending",
            reasoning="queued test signal",
            opportunity_id=opportunity_id,
            signal_source="market_observer",
            created_at=(now + timedelta(minutes=minutes)).isoformat(),
        ))
    insert_pi_decision(PiDecision(
        symbol="ETH/USDT",
        direction="long",
        action="skip",
        reasoning="already finalized",
        opportunity_id="opp-eth-final",
        signal_source="market_observer",
        created_at=(now + timedelta(minutes=-5)).isoformat(),
    ))

    signals, queue = _build_pending_signal_queue(limit=5, max_age_minutes=30)

    assert [row["opportunity_id"] for row in signals] == [
        "opp-btc-oldest", "opp-btc-duplicate", "opp-sol", "opp-ada", "opp-dot",
    ]
    assert queue["unresolved_total"] == 7
    assert queue["visible_count"] == 5
    assert queue["overflow_count"] == 2
    assert queue["oldest_at"] == signals[0]["at"]


@spec_claim(
    SpecClaim(
        "DL-S12", "decision_lifecycle_contract", "§4 final decision idempotency", "L2b",
        "A repeated wake for an opportunity with an existing final System Operator decision "
        "returns that authoritative result before creating another proposal or final row.",
        status="implemented",
    ),
)
def test_existing_final_decision_blocks_duplicate_proposal(tmp_path, monkeypatch):
    import asyncio
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import commands, reflection
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision
    from aletheia.storage.sqlite import get_db

    insert_pi_decision(PiDecision(
        symbol="BTC/USDT",
        direction="long",
        action="skip",
        conviction=50,
        reasoning="authoritative first result",
        opportunity_id="opp-final-idempotent",
        signal_source="market_observer",
    ))
    repeated = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=80,
        reasoning="duplicate wake",
        opportunity_id="opp-final-idempotent",
        signal_source="market_observer",
    )
    submit_calls = 0

    async def fake_evaluate(_context):
        return repeated

    async def fake_submit(_decision, _execution_mode):
        nonlocal submit_calls
        submit_calls += 1
        return {"proposal_id": "duplicate-proposal", "status": "queued"}

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: {
            "layer1": {
                "data_health": {"signal_status": "healthy", "ohlcv_ready": True}
            },
            # This idempotency unit deliberately omits a real queue summary.
            # Explicit production zero-pending summaries short-circuit before
            # any candidate evaluation (DL-S18); this fixture exercises only
            # the existing-final replay branch below that boundary.
            "layer2": {},
        },
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.evaluate_trading_opportunity",
        fake_evaluate,
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.submit_decision_as_proposal",
        fake_submit,
    )
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action<>'signal_pending'",
        (repeated.opportunity_id,),
    ).fetchone()[0]
    assert submit_calls == 0
    assert count == 1
    assert result["action"] == "skip"
    assert result["idempotent_replay"] is True


@spec_claim(
    SpecClaim(
        "DL-S13", "decision_lifecycle_contract", "§2.4 retryable preparation failure", "L2b",
        "A Proposal preparation exception is surfaced as a typed stable unavailable result "
        "without persisting a final decision or consuming the exact pending carrier.",
        status="implemented",
    ),
)
def test_submission_exception_keeps_pending_source_retryable(tmp_path, monkeypatch):
    import asyncio
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import commands, reflection
    from aletheia.agent_shell.trading_decision import (
        DecisionUnavailable,
        TradingDecision,
    )
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=80,
        reasoning="valid intent, failing submission boundary",
        market_type="spot",
        opportunity_id="opp-submission-exception",
        signal_source="market_observer",
    )
    insert_opportunity(Opportunity(
        id=decision.opportunity_id,
        symbol=decision.symbol,
        market=decision.market_type,
        direction_bias=decision.direction,
        confidence="high",
        confidence_score=80,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="queued",
    ))
    insert_pi_decision(PiDecision(
        symbol=decision.symbol,
        direction=decision.direction,
        market_type=decision.market_type,
        action="signal_pending",
        opportunity_id=decision.opportunity_id,
        signal_source=decision.signal_source,
    ))

    async def fake_evaluate(_context):
        return decision

    async def failing_prepare(_decision, _execution_mode):
        raise RuntimeError("sensitive provider detail must not persist")

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: {
            "layer1": {
                "data_health": {"signal_status": "healthy", "ohlcv_ready": True}
            }
        },
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.evaluate_trading_opportunity",
        fake_evaluate,
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.prepare_decision_as_proposal",
        failing_prepare,
    )
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    with pytest.raises(DecisionUnavailable, match="proposal_preparation_failed"):
        asyncio.run(commands.evaluate_trading_opportunity({}))

    final_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action!='signal_pending'",
        (decision.opportunity_id,),
    ).fetchone()[0]
    pending_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action='signal_pending'",
        (decision.opportunity_id,),
    ).fetchone()[0]
    source_status = get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (decision.opportunity_id,),
    ).fetchone()[0]
    assert final_count == 0
    assert pending_count == 1
    assert source_status == "queued"


@spec_claim(
    SpecClaim(
        "DL-S14", "decision_lifecycle_contract", "§4 pre-submission claim", "L2b",
        "Concurrent evaluations of one queued opportunity acquire one durable final-decision "
        "claim before proposal submission, so only one proposal side effect can run.",
        status="implemented",
    ),
)
def test_concurrent_duplicate_wake_submits_one_proposal(tmp_path, monkeypatch):
    import asyncio
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import commands, reflection
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.storage.models import Opportunity, PiDecision, TradeProposal
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-concurrent-claim",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
    ))

    async def fake_evaluate(_context):
        return TradingDecision(
            action="trade",
            symbol="BTC/USDT",
            direction="long",
            market_type="spot",
            conviction=80,
            reasoning="same queued opportunity",
            opportunity_id=opportunity.id,
            signal_source="market_observer",
        )

    prepare_calls = 0
    execute_calls = 0
    both_prepared = asyncio.Event()

    async def fake_prepare(_decision, _execution_mode):
        nonlocal prepare_calls
        prepare_calls += 1
        if prepare_calls == 2:
            both_prepared.set()
        await both_prepared.wait()
        proposal = TradeProposal(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.001,
            price=60_000.0,
            execution_mode="paper_internal",
            opportunity_id=opportunity.id,
        )
        return {"status": "prepared", "proposal_id": proposal.id, "proposal": proposal}

    def fake_execute(proposal, **_kwargs):
        nonlocal execute_calls
        execute_calls += 1
        return {"proposal_id": proposal.id, "status": "queued"}

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: {
            "layer1": {"data_health": {"signal_status": "healthy", "ohlcv_ready": True}},
            "layer2": {"pending_queue": {"unresolved_total": 1}},
        },
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.evaluate_trading_opportunity",
        fake_evaluate,
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.prepare_decision_as_proposal",
        fake_prepare,
    )
    monkeypatch.setattr("aletheia.execution.gateway._execute_proposal", fake_execute)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    async def run_both():
        first = asyncio.create_task(commands.evaluate_trading_opportunity({}))
        second = asyncio.create_task(commands.evaluate_trading_opportunity({}))
        return await asyncio.gather(first, second)

    results = asyncio.run(run_both())
    finals = get_db().execute(
        "SELECT proposal_id, proposal_status FROM pi_decisions "
        "WHERE opportunity_id=? AND action<>'signal_pending'",
        (opportunity.id,),
    ).fetchall()

    assert prepare_calls == 2
    assert execute_calls == 1
    assert len(finals) == 1
    assert sum(bool(result.get("idempotent_replay")) for result in results) == 1


@spec_claim(
    SpecClaim(
        "DL-S15", "decision_lifecycle_contract", "§4 durable claim failure", "L2b",
        "A final-decision claim must persist before proposal submission; persistence failure "
        "raises and produces zero proposal side effects instead of returning false success.",
        status="implemented",
    ),
)
def test_final_claim_failure_prevents_proposal_side_effect(tmp_path, monkeypatch):
    import asyncio
    import pytest
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import commands
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.storage.models import Opportunity, PiDecision, TradeProposal
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision

    opportunity = Opportunity(
        id="opp-claim-write-failure",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        symbol="BTC/USDT", direction="long", market_type="spot",
        action="signal_pending", opportunity_id=opportunity.id,
    ))

    async def fake_evaluate(_context):
        return TradingDecision(
            action="trade", symbol="BTC/USDT", direction="long", market_type="spot",
            conviction=80, reasoning="valid intent", opportunity_id=opportunity.id,
        )

    execute_calls = 0

    async def fake_prepare(_decision, _execution_mode):
        proposal = TradeProposal(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.001,
            price=60_000.0,
            execution_mode="paper_internal",
            opportunity_id=opportunity.id,
        )
        return {"status": "prepared", "proposal_id": proposal.id, "proposal": proposal}

    def fake_execute(proposal, **_kwargs):
        nonlocal execute_calls
        execute_calls += 1
        return {"proposal_id": proposal.id, "status": "queued"}

    def fail_final_insert(_decision):
        raise RuntimeError("forced final claim failure")

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: {"layer1": {"data_health": {"signal_status": "healthy", "ohlcv_ready": True}}},
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.evaluate_trading_opportunity",
        fake_evaluate,
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.prepare_decision_as_proposal",
        fake_prepare,
    )
    monkeypatch.setattr("aletheia.execution.gateway._execute_proposal", fake_execute)
    monkeypatch.setattr(
        "aletheia.storage.repositories.insert_pi_decision",
        fail_final_insert,
    )

    with pytest.raises(RuntimeError, match="forced final claim failure"):
        asyncio.run(commands.evaluate_trading_opportunity({}))
    assert execute_calls == 0


@spec_claim(
    SpecClaim(
        "DL-S16", "decision_lifecycle_contract", "§2.3 unavailable assessment", "L2b",
        "When assessment is unavailable, System Operator records no final decision and leaves "
        "the exact queued signal available for a later successful evaluation.",
        status="implemented",
    ),
)
def test_llm_unavailable_keeps_oldest_pending_signal_retryable(tmp_path, monkeypatch):
    import asyncio
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-unavailable-assessment",
        symbol="ETH/USDT",
        market="usdt_futures",
        direction_bias="short",
        status="queued",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    pending = PiDecision(
        symbol="ETH/USDT", direction="short", market_type="usdt_futures",
        action="signal_pending", opportunity_id=opportunity.id,
        signal_source="market_observer",
    )
    insert_pi_decision(pending)
    insert_market_snapshot(MarketSnapshot(
        symbol=opportunity.symbol,
        market_type="perp",
        exchange="test",
        price=100.0,
    ))

    async def unavailable(_context):
        return None

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: {
            "layer1": {
                "data_health": {"signal_status": "healthy", "ohlcv_ready": True},
                "feature_index": {"perp:ETH/USDT": {
                    "symbol": "ETH/USDT", "market_type": "perp",
                    "return_4h": 2.0, "return_24h": 3.0,
                }},
            },
            "layer2": {
                "pending_signals": [{
                    "symbol": "ETH/USDT", "direction": "short",
                    "market_type": "usdt_futures", "opportunity_id": opportunity.id,
                    "signal_source": "market_observer", "at": pending.created_at,
                }],
                "pending_queue": {"unresolved_total": 1},
            },
        },
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision._assess_opportunity",
        unavailable,
    )
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    with pytest.raises(
        trading_decision.DecisionUnavailable,
        match="llm_assessment_unavailable",
    ):
        asyncio.run(commands.evaluate_trading_opportunity({}))

    final_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action!='signal_pending' "
        "AND opportunity_id=?", (opportunity.id,),
    ).fetchone()[0]
    pending_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action='signal_pending' "
        "AND opportunity_id=?", (opportunity.id,),
    ).fetchone()[0]
    status = get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,),
    ).fetchone()[0]

    assert final_count == 0
    assert pending_count == 1
    assert status == "queued"

    async def successful_skip(_context):
        return {
            "action": "skip",
            "symbol": opportunity.symbol,
            "direction": opportunity.direction_bias,
            "market_type": opportunity.market,
            "conviction": 25,
            "reasoning": "the setup is not strong enough",
            "bear_case": "the signal has no follow-through",
            "skip_kind": "adverse_candidate_evidence",
            "adverse_reason_codes": ["adverse_price_structure"],
            "adverse_evidence": ["the signal has no observed follow-through"],
        }

    async def confirm_successful_skip(_context, _initial, _reason):
        return await successful_skip(_context)

    monkeypatch.setattr(trading_decision, "_assess_opportunity", successful_skip)
    monkeypatch.setattr(
        trading_decision, "_reassess_direct_skip", confirm_successful_skip
    )
    result = asyncio.run(commands.evaluate_trading_opportunity({}))
    replay = asyncio.run(commands.evaluate_trading_opportunity({}))

    finals = get_db().execute(
        "SELECT action FROM pi_decisions WHERE action!='signal_pending' "
        "AND opportunity_id=?", (opportunity.id,),
    ).fetchall()
    assert result["action"] == "skip"
    assert replay["idempotent_replay"] is True
    assert [row["action"] for row in finals] == ["skip"]
    assert get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,),
    ).fetchone()[0] == "reviewed"


@spec_claim(
    SpecClaim(
        "DL-S40",
        "decision_lifecycle_contract",
        "§2.3 required debate availability",
        "L2b",
        "A trade inside the mandatory debate range cannot proceed when either debate side "
        "is unavailable; the whole evaluation remains retryable.",
        status="implemented",
    ),
)
def test_required_debate_unavailable_aborts_evaluation(monkeypatch):
    import asyncio

    from aletheia.agent_shell import trading_decision

    async def assessment(_context):
        return {
            "action": "trade",
            "symbol": "BTC/USDT",
            "direction": "long",
            "market_type": "spot",
            "conviction": 60,
            "reasoning": "uncertain trade requires debate",
            "bear_case": "momentum may reverse",
            "parameter_rationale": {
                key: f"reason for {key}"
                for key in trading_decision._ENTRY_PARAMETER_KEYS
            },
        }

    async def unavailable_debate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(trading_decision, "_assess_opportunity", assessment)
    monkeypatch.setattr(trading_decision, "_run_debate", unavailable_debate)
    monkeypatch.setattr(
        trading_decision, "_proposal_bias_brake_reason", lambda _decision: None
    )

    with pytest.raises(
        trading_decision.DecisionUnavailable,
        match="required_debate_unavailable",
    ):
        asyncio.run(trading_decision.evaluate_trading_opportunity({
            "layer1": {}, "layer2": {}, "layer3": {},
        }))


@spec_claim(
    SpecClaim(
        "DL-S41",
        "decision_lifecycle_contract",
        "§3 retryable proposal preflight",
        "L2b",
        "Missing or stale exact entry marks and incomplete AI parameter reasons create no "
        "final decision or Proposal; the queued Opportunity remains retryable.",
        status="implemented",
    ),
)
def test_proposal_preflight_unavailable_does_not_claim_pending_source(
    tmp_path, monkeypatch
):
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, trading_decision
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-preflight-price-unavailable",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
    )
    insert_opportunity(opportunity)
    pending = PiDecision(
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
    )
    insert_pi_decision(pending)

    async def trade(_context):
        return {
            "action": "trade",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 90,
            "reasoning": "trade only if an exact fresh mark exists",
            "bear_case": "the mark may be unavailable",
            "parameter_rationale": {
                key: f"reason for {key}"
                for key in trading_decision._ENTRY_PARAMETER_KEYS
            },
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", trade)
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: {
            "layer1": {"data_health": {"signal_status": "healthy", "ohlcv_ready": True}},
            "layer2": {
                "pending_signals": [{
                    "symbol": opportunity.symbol,
                    "direction": "long",
                    "market_type": "spot",
                    "opportunity_id": opportunity.id,
                    "signal_source": "market_observer",
                    "at": pending.created_at,
                }],
                "pending_queue": {"unresolved_total": 1},
            },
            "layer3": {},
        },
    )
    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )

    with pytest.raises(
        trading_decision.DecisionUnavailable,
        match="market_price_unavailable",
    ):
        asyncio.run(commands.evaluate_trading_opportunity({}))

    db = get_db()
    assert db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action!='signal_pending' "
        "AND opportunity_id=?", (opportunity.id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,),
    ).fetchone()[0] == "queued"


@spec_claim(
    SpecClaim(
        "DL-S17", "decision_lifecycle_contract", "§2.3 source-aware queue TTL", "L2b",
        "The 30-minute market-signal window does not silently hide long-horizon Serenity "
        "pending decisions that have no matching expiry transition.",
        status="implemented",
    ),
)
def test_serenity_pending_signal_is_not_hidden_by_market_queue_ttl(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.storage.models import PiDecision, SerenityOpportunity, SerenityTheme
    from aletheia.storage.repositories import (
        insert_pi_decision, insert_serenity_opportunity, insert_serenity_theme,
    )

    # An active SerenityTheme + SerenityOpportunity are required for the signal to
    # remain visible beyond the short-term market TTL (#199).
    theme = SerenityTheme(name="Long-horizon NVDA bull thesis", status="active")
    insert_serenity_theme(theme)
    opp = SerenityOpportunity(
        id="serenity-long-horizon",
        theme_id=theme.id,
        symbol="NVDA",
        direction="long",
        status="active",
    )
    insert_serenity_opportunity(opp)
    insert_pi_decision(PiDecision(
        symbol="NVDA", direction="long", market_type="spot",
        action="signal_pending", opportunity_id="serenity-long-horizon",
        signal_source="serenity",
        created_at=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
    ))

    signals, queue = _build_pending_signal_queue(limit=5, max_age_minutes=30)

    assert [row["opportunity_id"] for row in signals] == ["serenity-long-horizon"]
    assert queue["unresolved_total"] == 1
    assert queue["expired_unconsumed_rows"] == 0


@spec_claim(
    SpecClaim(
        "DL-S19", "decision_lifecycle_contract", "§2.3 serenity expired-source cleanup", "L2b",
        "A Serenity pending carrier whose source SerenityOpportunity is missing, "
        "terminal, or whose parent SerenityTheme is dismissed/expired is not "
        "actionable and is counted in expired_unconsumed_rows (#199).",
        status="implemented",
    ),
)
def test_serenity_pending_signal_is_invisible_after_source_expires(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.storage.models import PiDecision, SerenityOpportunity, SerenityTheme
    from aletheia.storage.repositories import (
        insert_pi_decision, insert_serenity_opportunity, insert_serenity_theme,
        update_serenity_theme,
    )

    theme = SerenityTheme(name="Expired NVDA thesis", status="active")
    insert_serenity_theme(theme)
    opp = SerenityOpportunity(
        id="serenity-expired-source",
        theme_id=theme.id,
        symbol="NVDA",
        direction="long",
        status="active",
    )
    insert_serenity_opportunity(opp)
    insert_pi_decision(PiDecision(
        symbol="NVDA", direction="long", market_type="spot",
        action="signal_pending", opportunity_id="serenity-expired-source",
        signal_source="serenity",
        created_at=(datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat(),
    ))

    # While source is active the signal must appear as unresolved.
    signals_before, queue_before = _build_pending_signal_queue(limit=5, max_age_minutes=30)
    assert any(r["opportunity_id"] == "serenity-expired-source" for r in signals_before)
    assert queue_before["expired_unconsumed_rows"] == 0

    # Dismiss the parent theme (cascades to opportunity in the real route; here
    # we simulate the maintenance outcome directly on the theme status).
    update_serenity_theme(theme.id, status="dismissed")

    signals_after, queue_after = _build_pending_signal_queue(limit=5, max_age_minutes=30)
    assert not any(r["opportunity_id"] == "serenity-expired-source" for r in signals_after), (
        "Signal with dismissed theme must not appear as actionable"
    )
    assert queue_after["expired_unconsumed_rows"] >= 1, (
        "Dismissed-source Serenity rows must be counted in expired_unconsumed_rows"
    )


@spec_claim(
    SpecClaim(
        "DL-S20", "decision_lifecycle_contract", "§2.3 serenity maintenance expiry", "L2b",
        "expire_stale_serenity_themes() transitions themes past their expires_at "
        "deadline to 'expired' and cascades to active child opportunities so the "
        "pending-signal queue treats them as stale (#199).",
        status="implemented",
    ),
)
def test_expire_stale_serenity_themes_cascades_to_children(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import SerenityOpportunity, SerenityTheme
    from aletheia.storage.repositories import (
        expire_stale_serenity_themes,
        get_serenity_opportunity,
        get_serenity_theme,
        insert_serenity_opportunity,
        insert_serenity_theme,
        update_serenity_theme,
    )

    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    theme = SerenityTheme(name="Deadline-elapsed thesis", status="active")
    insert_serenity_theme(theme)
    update_serenity_theme(theme.id, expires_at=past)
    opp = SerenityOpportunity(theme_id=theme.id, symbol="ETH", direction="long", status="active")
    insert_serenity_opportunity(opp)

    count = expire_stale_serenity_themes()

    assert count >= 1
    assert get_serenity_theme(theme.id)["status"] == "expired"
    assert get_serenity_opportunity(opp.id)["status"] == "expired"


@spec_claim(
    SpecClaim(
        "DL-S18", "decision_lifecycle_contract", "§2.3 pending-only operator input", "L2b",
        "A heartbeat with zero unresolved signal_pending rows records one observed "
        "no-evaluable-candidate event and never invokes the trading LLM, creates a final "
        "PiDecision, or changes an open/ranker Opportunity.",
        status="implemented",
    ),
)
def test_zero_pending_heartbeat_is_observed_without_trading_evaluation(tmp_path, monkeypatch):
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    # pi_daemon deliberately closes its thread-local connection after every
    # evaluation. Re-open the fixture on the production-derived app.db path so
    # that post-heartbeat assertions inspect the same durable temporary DB.
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db

    close_db()
    run_migrations()
    from aletheia.agent_runtime import pi_daemon
    from aletheia.agent_shell import trading_decision
    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import MarketSnapshot, Opportunity
    from aletheia.storage.repositories import insert_market_snapshot, insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.symbol_list = []
    cfg.risk.symbol_filter_mode = "blacklist"
    save_config(cfg)

    opportunity = Opportunity(
        id="opp-heartbeat-not-queued",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_market_snapshot(
        MarketSnapshot(
            symbol="BTC/USDT", market_type="spot", exchange="binance", price=100.0
        )
    )

    context = build_enriched_trading_context()
    assert context["layer2"]["pending_queue"]["unresolved_total"] == 0
    assert any(row.get("id") == opportunity.id for row in context["layer1"]["opportunities"])
    assert not any(
        row.get("opportunity_id") == opportunity.id
        for row in context["layer2"]["executable_candidates"]
    )
    assert any(
        row.get("opportunity_id") == opportunity.id
        for row in context["layer2"]["analysis_only_candidates"]
    )

    async def llm_must_not_run(_context):
        raise AssertionError("zero-pending heartbeat must not invoke the trading LLM")

    monkeypatch.setattr(trading_decision, "_assess_opportunity", llm_must_not_run)

    remaining = asyncio.run(pi_daemon._run_evaluation("heartbeat"))
    remaining_repeat = asyncio.run(pi_daemon._run_evaluation("heartbeat"))

    db = get_db()
    pending_count = db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? AND action='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0]
    proposals = db.execute(
        "SELECT id, opportunity_id FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchall()
    final_count = db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE action!='signal_pending'"
    ).fetchone()[0]
    events = db.execute(
        "SELECT event_kind, trigger, pending_unresolved_total, policy_epoch_id "
        "FROM operator_evaluation_events"
    ).fetchall()
    opportunity_status = db.execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,)
    ).fetchone()[0]
    assert pending_count == 0
    assert remaining == 0
    assert remaining_repeat == 0
    assert not proposals
    assert final_count == 0
    assert [dict(row) for row in events] == [{
        "event_kind": "no_evaluable_candidate",
        "trigger": "heartbeat",
        "pending_unresolved_total": 0,
        "policy_epoch_id": events[0]["policy_epoch_id"],
    }]
    assert events[0]["policy_epoch_id"]
    epoch = db.execute(
        "SELECT effective_values_json FROM learning_policy_epochs WHERE epoch_id=?",
        (events[0]["policy_epoch_id"],),
    ).fetchone()[0]
    assert '"aggregate_signal_health_candidate_veto": false' in epoch
    assert '"zero_pending_heartbeat_trading_evaluation": false' in epoch
    assert '"ranker_neutral_confidence_floor": "medium"' in epoch
    assert '"auto_tightened_confidence_supply_recovery": true' in epoch
    assert opportunity_status == "open"


@spec_claim(
    SpecClaim(
        "DL-S35", "decision_lifecycle_contract", "§2.3 pending-only terminal identity", "L2b",
        "A final entry action must have the exact durable pending carrier; no-target "
        "heartbeat state belongs only to operator_evaluation_events, not pi_decisions.",
        status="implemented",
    ),
)
@pytest.mark.parametrize("raw_action", ["skip", "watch"])
def test_heartbeat_skip_or_watch_cannot_retire_unqueued_opportunity(
    tmp_path, monkeypatch, raw_action
):
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db

    close_db()
    run_migrations()
    from aletheia.agent_runtime import pi_daemon
    from aletheia.agent_shell import trading_decision
    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import MarketSnapshot, Opportunity
    from aletheia.storage.repositories import insert_market_snapshot, insert_opportunity
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.symbol_list = []
    cfg.risk.symbol_filter_mode = "blacklist"
    save_config(cfg)

    opportunity = Opportunity(
        id=f"opp-heartbeat-unscoped-{raw_action}",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="open",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_market_snapshot(
        MarketSnapshot(
            symbol=opportunity.symbol,
            market_type="spot",
            exchange="binance",
            price=100.0,
        )
    )

    context = build_enriched_trading_context()
    assert context["layer2"]["pending_queue"]["unresolved_total"] == 0
    assert any(row.get("id") == opportunity.id for row in context["layer1"]["opportunities"])
    assert any(
        row.get("opportunity_id") == opportunity.id
        for row in context["layer2"]["analysis_only_candidates"]
    )

    async def assess_visible_but_unqueued(_context):
        raise AssertionError(
            f"zero-pending heartbeat must not assess unqueued raw action {raw_action}"
        )

    monkeypatch.setattr(
        trading_decision, "_assess_opportunity", assess_visible_but_unqueued
    )
    asyncio.run(pi_daemon._run_evaluation("heartbeat"))

    db = get_db()
    linked_finals = db.execute(
        "SELECT id FROM pi_decisions WHERE action!='signal_pending' AND opportunity_id=?",
        (opportunity.id,),
    ).fetchall()
    unscoped_finals = db.execute(
        "SELECT action, opportunity_id FROM pi_decisions "
        "WHERE action!='signal_pending' AND opportunity_id IS NULL"
    ).fetchall()
    observations = db.execute(
        "SELECT event_kind, trigger FROM operator_evaluation_events"
    ).fetchall()
    proposal_count = db.execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    opportunity_status = db.execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,)
    ).fetchone()[0]

    assert not linked_finals
    assert not unscoped_finals
    assert [dict(row) for row in observations] == [{
        "event_kind": "no_evaluable_candidate", "trigger": "heartbeat",
    }]
    assert proposal_count == 0
    assert opportunity_status == "open"


@spec_claim(
    SpecClaim(
        "DL-S21", "decision_lifecycle_contract", "§2.3 executable queue identity", "L2b",
        "A ranker preview becomes executable only when a durable signal_pending row "
        "matches opportunity, source, symbol, direction, and market exactly; same-instrument "
        "work from another Opportunity is not execution authority.",
        status="implemented",
    ),
)
def test_executable_candidate_requires_exact_pending_identity(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

    candidate = {
        "opportunity_id": "ranked-exact-opportunity",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "trade_score": 92,
        "skip_reason": None,
        "scanner_family": "momentum",
    }
    monkeypatch.setattr(
        "aletheia.agents.paper_trader_ranker.build_paper_candidates",
        lambda limit=30: [candidate],
    )

    without_pending = build_enriched_trading_context()["layer2"]
    assert without_pending["executable_candidates"] == []
    assert [
        row["opportunity_id"] for row in without_pending["analysis_only_candidates"]
    ] == [candidate["opportunity_id"]]

    insert_pi_decision(PiDecision(
        id="same-instrument-wrong-opportunity",
        action="signal_pending",
        opportunity_id="different-opportunity",
        signal_source="market_observer",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
    ))
    wrong_identity = build_enriched_trading_context()["layer2"]
    assert wrong_identity["executable_candidates"] == []
    assert [
        row["opportunity_id"] for row in wrong_identity["analysis_only_candidates"]
    ] == [candidate["opportunity_id"]]

    insert_pi_decision(PiDecision(
        id="exact-ranker-pending-carrier",
        action="signal_pending",
        opportunity_id=candidate["opportunity_id"],
        signal_source="market_observer",
        symbol=candidate["symbol"],
        direction=candidate["direction"],
        market_type=candidate["market_type"],
    ))
    exact_identity = build_enriched_trading_context()["layer2"]
    assert [
        row["opportunity_id"] for row in exact_identity["executable_candidates"]
    ] == [candidate["opportunity_id"]]
    assert exact_identity["analysis_only_candidates"] == []


@spec_claim(
    SpecClaim(
        "DL-S22", "decision_lifecycle_contract", "§2.1 open-position context honesty", "L2b",
        "Layer 1 keeps its complete open-position context while the entry prompt honestly "
        "reports the bounded visible slice; close authority lives in portfolio review.",
        status="implemented",
    ),
)
def test_old_position_beyond_layer1_display_is_counted_without_fake_entry_action(
    tmp_path, monkeypatch
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import _build_layer1_snapshot
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    target = Position(
        id="old-layer1-close-target",
        symbol="OLD/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
        opportunity_id="old-layer1-opportunity",
    )
    insert_position(target)
    for index in range(10):
        insert_position(Position(
            id=f"new-layer1-position-{index:02d}",
            symbol=f"NEW{index}/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
        ))

    layer1 = _build_layer1_snapshot()
    assert target.id not in {row["id"] for row in layer1["positions"]}
    assert target.id in {row["id"] for row in layer1["position_authority"]}
    assert layer1["positions_meta"] == {
        "total": 11,
        "display_count": 10,
        "prompt_visible_count": 5,
        "prompt_overflow_count": 6,
    }

    ctx = {"layer1": layer1, "layer2": {}, "layer3": {}}
    prompt = _build_assessment_prompt(ctx)
    assert "Open positions: 11 total; showing 5; 6 not shown" in prompt
    actions = prompt.split("Actions available:", 1)[1].split("Return:", 1)[0]
    assert "- close:" not in actions
    assert "- reduce:" not in actions


@spec_claim(
    SpecClaim(
        "DL-S51", "decision_lifecycle_contract", "§2.2b Layer 1 technical feature identity", "L2b",
        "System Operator Layer 1 resolves feature snapshots through their registry asset identity, "
        "so distinct instruments are not collapsed into one blank-symbol feature.",
        status="implemented",
    ),
)
def test_layer1_features_resolve_snapshot_asset_identity(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    import aletheia.data.price_feed as price_feed
    from aletheia.agent_shell.trading_context import _build_layer1_snapshot
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.executemany(
        "INSERT INTO universe_registry (id, venue, market_type, symbol) VALUES (?, ?, ?, ?)",
        [
            ("asset-btc-perp", "okx", "perp", "BTC/USDT"),
            ("asset-eth-perp", "okx", "perp", "ETH/USDT"),
        ],
    )
    db.executemany(
        "INSERT INTO universe_feature_snapshots "
        "(id, asset_id, ts, rsi_14) VALUES (?, ?, ?, ?)",
        [
            ("feature-btc", "asset-btc-perp", now, 61.0),
            ("feature-eth", "asset-eth-perp", now, 39.0),
        ],
    )
    db.commit()
    monkeypatch.setattr(
        price_feed,
        "get_cached_price_with_meta",
        lambda symbol: {"price": {"BTC/USDT": 100.0, "ETH/USDT": 200.0}[symbol]},
    )

    assert {
        row["symbol"]: (row["market_type"], row["price"], row["rsi_14"])
        for row in _build_layer1_snapshot()["features"]
    } == {
        "BTC/USDT": ("perp", 100.0, 61.0),
        "ETH/USDT": ("perp", 200.0, 39.0),
    }


@spec_claim(
    SpecClaim(
        "DL-S45", "decision_lifecycle_contract", "§2.2b candidate-targeted technical context", "L2b",
        "Every durable pending carrier receives its exact latest universe feature snapshot even "
        "when that snapshot falls outside the global Layer 1 presentation slice.",
        status="implemented",
    ),
)
def test_pending_candidate_feature_beyond_global_limit_is_hydrated(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    import aletheia.data.price_feed as price_feed
    from aletheia.agent_shell.decision_facts import candidate_fact_bundle
    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import (
        insert_opportunity,
        insert_pi_decision,
        list_universe_feature_snapshots,
    )
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO universe_registry (id, venue, market_type, symbol) VALUES (?, ?, ?, ?)",
        ("asset-target", "binance", "perp", "TARGET/USDT"),
    )
    db.execute(
        "INSERT INTO universe_feature_snapshots "
        "(id, asset_id, ts, rsi_14, return_1h, return_4h, data_freshness_sec) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("feature-target", "asset-target", now, 62.0, 0.8, 2.1, 30),
    )
    for index in range(60):
        asset_id = f"asset-decoy-{index:02d}"
        db.execute(
            "INSERT INTO universe_registry (id, venue, market_type, symbol) VALUES (?, ?, ?, ?)",
            (asset_id, "binance", "perp", f"D{index:02d}/USDT"),
        )
        db.execute(
            "INSERT INTO universe_feature_snapshots "
            "(id, asset_id, ts, rsi_14, data_freshness_sec) VALUES (?, ?, ?, ?, ?)",
            (f"feature-decoy-{index:02d}", asset_id, now, 50.0, 30),
        )
    db.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, market_type, scanner_family, "
        "rank, total_score, expires_at, execution_eligible, risk_flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "member-target", "snapshot-target", "asset-target", "TARGET/USDT",
            "perp", "momentum", 1, 80.0, now, 1, "[]",
        ),
    )
    db.commit()

    opportunity = Opportunity(
        id="opp-target-beyond-50",
        symbol="TARGET/USDT",
        market="perp",
        direction_bias="long",
        confidence="medium",
        confidence_score=55,
        source_agent="market_observer",
        status="queued",
        scanner_family="momentum",
        execution_eligible=True,
        universe_member_id="member-target",
        universe_snapshot_id="snapshot-target",
        thesis="Target-specific technical evidence must reach the Operator.",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="pending-target-beyond-50",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="perp",
    ))
    monkeypatch.setattr(
        price_feed,
        "get_cached_price_with_meta",
        lambda _symbol: {"price": 101.0, "provider": "test"},
    )
    monkeypatch.setattr(
        "aletheia.agents.paper_trader_ranker.build_paper_candidates", lambda limit=30: [],
    )

    assert not any(
        row["id"] == "feature-target"
        for row in list_universe_feature_snapshots(limit=50)
    )
    context = build_enriched_trading_context(authorize_learning=False)
    facts = candidate_fact_bundle(context, opportunity_id=opportunity.id)

    assert facts["technical"]["feature_snapshot_id"] == "feature-target"
    assert facts["technical"]["rsi_14"] == 62.0
    assert facts["candidate"]["execution_eligible"] is True
    assert [row["id"] for row in context["layer1"]["opportunities"]] == [opportunity.id]
    assert context["layer1"]["opportunities"][0]["technical_context"]["feature_snapshot_id"] == "feature-target"


@spec_claim(
    SpecClaim(
        "DL-S46", "decision_lifecycle_contract", "§2.2b candidate context availability", "L2b",
        "A missing exact candidate technical snapshot is retryable unavailability: the model is "
        "not invoked, the carrier is retained, and a later exact snapshot changes the material "
        "fingerprint so evaluation can resume.",
        status="implemented",
    ),
)
def test_missing_candidate_technical_context_defers_before_model_and_reopens_on_data(
    tmp_path, monkeypatch,
):
    import asyncio
    from datetime import datetime, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import b2_evidence_policy, commands, trading_decision

    candidate = {
        "opportunity_id": "opp-technical-preflight",
        "signal_source": "market_observer",
        "symbol": "TECH/USDT",
        "direction": "long",
        "market_type": "perp",
        "scanner_family": "momentum",
        "trade_score": 55.0,
        "asset_id": "asset-technical-preflight",
    }
    context = {
        "layer1": {
            "data_health": {"ohlcv_ready": True},
            "feature_index": {},
            "features": [],
            "opportunities": [],
        },
        "layer2": {
            "pending_signals": [candidate],
            "executable_candidates": [dict(candidate)],
            "pending_queue": {
                "read_status": "complete", "unresolved_total": 1,
                "visible_count": 1, "overflow_count": 0,
            },
        },
        "layer3": {},
    }
    calls = {"assess": 0, "confirm": 0}

    async def assess(_ctx):
        calls["assess"] += 1
        return {
            "action": "skip", "symbol": "TECH/USDT", "direction": "long",
            "market_type": "perp", "conviction": 25,
            "reasoning": "Evidence remains neutral.", "bear_case": "Setup may fail.",
            "skip_kind": "insufficient_evidence", "adverse_reason_codes": [],
            "adverse_evidence": [],
        }

    async def confirm(_ctx, _initial, _reason):
        calls["confirm"] += 1
        return await assess(_ctx)

    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: context,
    )
    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", confirm)
    monkeypatch.setattr(
        b2_evidence_policy, "attach_b2_candidate_evidence", lambda *_args, **_kwargs: None,
    )

    missing = asyncio.run(commands.evaluate_trading_opportunity({}))
    assert missing["action"] == "evaluation_deferred"
    assert missing["observation_status"] == "candidate_context_unavailable"
    assert missing["defer_reason"] == "candidate_technical_snapshot_unavailable"
    assert missing["model_invoked"] is False
    assert calls == {"assess": 0, "confirm": 0}

    feature = {
        "asset_id": candidate["asset_id"],
        "feature_snapshot_id": "feature-technical-preflight",
        "feature_ts": datetime.now(timezone.utc).isoformat(),
        "data_freshness_sec": 20,
        "venue": "binance",
        "symbol": candidate["symbol"],
        "market_type": candidate["market_type"],
        "price": 100.0,
        "rsi_14": 54.0,
        "return_1h": 0.2,
        "return_4h": 0.6,
    }
    context["layer1"]["feature_index"] = {
        "binance|perp|TECH/USDT": feature,
    }
    context["layer1"]["features"] = [feature]

    with_data = asyncio.run(commands.evaluate_trading_opportunity({}))
    assert with_data["action"] == "evaluation_deferred"
    assert with_data["model_invoked"] is True
    assert calls == {"assess": 2, "confirm": 1}


@spec_claim(
    SpecClaim(
        "DL-S42", "decision_lifecycle_contract", "§2.4 aggregate-health isolation", "L2b",
        "Aggregate signal-health observations remain available to operations but do not enter "
        "the single-candidate PI prompt as a skip, sizing, or stop-adjustment instruction.",
        status="implemented",
    ),
)
def test_degraded_aggregate_signal_health_is_absent_from_candidate_prompt():
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt

    prompt = _build_assessment_prompt({
        "layer1": {
            "data_health": {
                "signal_status": "degraded",
                "signal_win_rate_24h": 37.0,
                "members_with_full_coverage": 10,
                "total_members": 10,
            },
        },
        "layer2": {},
        "layer3": {},
    })

    assert "SIGNAL DEGRADATION" not in prompt
    assert "signal_accuracy_24h" not in prompt
    assert "skipping low-conviction" not in prompt
    assert "reducing position sizing" not in prompt


@spec_claim(
    SpecClaim(
        "DL-S36",
        "decision_lifecycle_contract",
        "§2.1 unsupported entry action isolation",
        "L2b",
        "A legacy close/reduce result is retryable evaluation unavailability before context "
        "binding and cannot borrow or consume an Opportunity identity.",
        status="implemented",
    ),
)
@pytest.mark.parametrize("action", ["close", "reduce"])
def test_unmatched_position_action_never_falls_back_to_opportunity_identity(action):
    from aletheia.agent_shell.trading_decision import (
        DecisionUnavailable,
        _parse_decision,
    )

    with pytest.raises(DecisionUnavailable, match=f"unsupported_action:{action}"):
        _parse_decision({
            "action": action, "symbol": "BTC/USDT", "direction": "long",
            "market_type": "spot", "conviction": 90,
            "reasoning": f"{action} BTC despite no matching open Position",
            "bear_case": "legacy action is unsupported",
        })


@spec_claim(
    SpecClaim(
        "DL-S33",
        "decision_lifecycle_contract",
        "§4 honest action surface",
        "L2b",
        "The active entry Operator exposes only trade/skip. Legacy close/reduce/scale_in/"
        "flip/watch results are retryable invalid output and cannot create a Proposal.",
        status="implemented",
    ),
    SpecClaim(
        "DL-S34",
        "decision_lifecycle_contract",
        "§4 portfolio action honesty",
        "L2c",
        "Portfolio review exposes only hold/close_one; the removed reduce_exposure label "
        "cannot silently perform a full close.",
        status="implemented",
    ),
)
@pytest.mark.parametrize(
    "action", ["close", "reduce", "scale_in", "flip", "watch"]
)
def test_active_operator_rejects_unsupported_actions_as_retryable(action):
    from aletheia.agent_shell.trading_decision import (
        _DECISION_SCHEMA,
        _build_assessment_prompt,
        _parse_decision,
        DecisionUnavailable,
    )

    assert _DECISION_SCHEMA["properties"]["action"]["enum"] == ["trade", "skip"]
    prompt = _build_assessment_prompt({"layer1": {}, "layer2": {}, "layer3": {}})
    actions = prompt.split("Actions available:", 1)[1].split("Return:", 1)[0]
    assert "- trade:" in actions
    assert "- skip:" in actions
    assert all(
        f"- {unsupported}:" not in actions
        for unsupported in ("close", "reduce", "scale_in", "flip", "watch")
    )

    with pytest.raises(DecisionUnavailable, match=f"unsupported_action:{action}"):
        _parse_decision({
            "action": action,
            "symbol": "BTC/USDT",
            "direction": "long",
            "market_type": "spot",
            "conviction": 90,
            "reasoning": "legacy model output",
            "bear_case": "the setup may fail",
        })


@spec_claim(
    SpecClaim(
        "DL-S43", "decision_lifecycle_contract", "§2.3 retryable model output", "L2b",
        "A missing/unsupported action or malformed conviction is evaluation unavailability, "
        "not a terminal skip that consumes a pending Opportunity.",
        status="implemented",
    ),
)
@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"action": "watch", "conviction": 30}, "unsupported_action"),
        ({"action": "skip", "conviction": "low"}, "invalid_conviction"),
        ({"conviction": 30}, "missing_action"),
    ],
)
def test_invalid_active_operator_output_is_retryable(payload, reason):
    from aletheia.agent_shell.trading_decision import DecisionUnavailable, _parse_decision

    raw = {
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "reasoning": "malformed active-model response",
        "bear_case": "the setup may fail",
        **payload,
    }

    with pytest.raises(DecisionUnavailable, match=reason):
        _parse_decision(raw)


@spec_claim(
    SpecClaim(
        "DL-S44", "decision_lifecycle_contract", "§2.3 direct-skip confirmation", "L2b",
        "A direct model skip becomes terminal only after one bounded counterfactual "
        "confirmation and deterministic validation bind every reason to selected-candidate "
        "facts; insufficient evidence and rank ordering alone are neutral.",
        status="implemented",
    ),
)
def test_direct_skip_requires_one_structured_adverse_confirmation(monkeypatch):
    import asyncio

    from aletheia.agent_shell import trading_decision

    initial = {
        "action": "skip",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "conviction": 35,
        "reasoning": "candidate conflicts with the current range structure",
        "bear_case": "range rejection can continue",
        "skip_kind": "adverse_candidate_evidence",
        "adverse_reason_codes": ["regime_mismatch"],
        "adverse_evidence": ["1h structure remains range-bound after the breakout signal"],
    }
    confirmed = {
        **initial,
        "reasoning": "independent challenge confirms the range mismatch",
    }

    async def assess(_ctx):
        return initial

    async def confirm(_ctx, _initial, _reason):
        return confirmed

    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", confirm)
    monkeypatch.setattr(
        trading_decision, "_proposal_bias_brake_reason", lambda _decision: None
    )

    decision = asyncio.run(trading_decision.evaluate_trading_opportunity({
        "skip_policy": {"direct_skip_confirmation_required": True},
        "layer1": {"regime": {
            "state": "bearish", "confidence": 0.80,
            "long_bias": 0.20, "short_bias": 0.80,
        }},
        "layer2": {"pending_signals": [{
            "symbol": "BTC/USDT",
            "direction": "long",
            "market_type": "spot",
            "opportunity_id": "opp-confirmed-skip",
            "signal_source": "market_observer",
        }]},
        "layer3": {},
    }))

    assert decision.action == "skip"
    assert decision.skip_kind == "adverse_candidate_evidence"
    assert decision.adverse_reason_codes == ["regime_mismatch"]
    assert decision.validated_adverse_claims == [{
        "reason_code": "regime_mismatch",
        "fact_refs": ["regime.state", "regime.confidence", "regime.long_bias"],
    }]
    assert decision.model_reasoning == "independent challenge confirms the range mismatch"
    assert decision.reasoning.startswith("Deterministic selected-candidate facts support terminal skip:")
    assert "range mismatch" not in decision.reasoning
    assert decision.filter_trace[0]["reason_code"] == "operator_model_skip"
    assert decision.filter_trace[0]["inputs"]["confirmation_attempts"] == 1


def test_insufficient_evidence_cannot_become_terminal_skip(monkeypatch):
    import asyncio

    from aletheia.agent_shell import trading_decision

    neutral_skip = {
        "action": "skip",
        "symbol": "ETH/USDT",
        "direction": "short",
        "market_type": "usdt_futures",
        "conviction": 20,
        "reasoning": "not enough matured outcomes",
        "bear_case": "the short may fail",
        "skip_kind": "insufficient_evidence",
        "adverse_reason_codes": [],
        "adverse_evidence": [],
    }

    async def assess(_ctx):
        return neutral_skip

    async def confirm(_ctx, _initial, _reason):
        return neutral_skip

    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", confirm)

    with pytest.raises(
        trading_decision.DecisionUnavailable,
        match="direct_skip_not_confirmed",
    ) as exc_info:
        asyncio.run(trading_decision.evaluate_trading_opportunity({
            "skip_policy": {"direct_skip_confirmation_required": True},
            "layer1": {},
            "layer2": {"pending_signals": [{
                "symbol": "ETH/USDT",
                "direction": "short",
                "market_type": "usdt_futures",
                "opportunity_id": "opp-neutral-skip",
                "signal_source": "market_observer",
            }]},
            "layer3": {},
        }))

    assert exc_info.value.stage == "operator_skip_validation"


def test_operator_context_fingerprint_ignores_carrier_churn_but_tracks_material_change():
    from aletheia.agent_shell.trading_context import operator_decision_context_fingerprint

    def context(opportunity_id, created_at, trade_score):
        return {
            "skip_policy_epoch_id": "epoch-direct-skip-v2",
            "layer1": {"market_regime": "range"},
            "layer2": {
                "pending_signals": [{
                    "opportunity_id": opportunity_id,
                    "created_at": created_at,
                    "symbol": "BTC/USDT",
                    "direction": "long",
                    "market_type": "spot",
                    "trade_score": trade_score,
                    "reasoning": "same scanner thesis",
                    "b2_evidence": {"status": "insufficient_evidence"},
                }],
                "executable_candidates": [{
                    "opportunity_id": opportunity_id,
                    "symbol": "BTC/USDT",
                    "direction": "long",
                    "market_type": "spot",
                    "trade_score": trade_score,
                }],
            },
        }

    first = operator_decision_context_fingerprint(
        context("opp-a", "2026-07-18T01:00:00Z", 51.0)
    )
    carrier_churn = operator_decision_context_fingerprint(
        context("opp-b", "2026-07-18T01:50:00Z", 51.0)
    )
    score_changed = operator_decision_context_fingerprint(
        context("opp-c", "2026-07-18T02:20:00Z", 58.0)
    )

    assert first == carrier_churn
    assert score_changed != first


def test_operator_context_fingerprint_tracks_real_b2_schema_fields():
    from aletheia.agent_shell.trading_context import operator_decision_context_fingerprint

    def context(*, n_net=25, coverage=0.64, shrunk=0.95, verdict=None):
        return {
            "skip_policy_epoch_id": "epoch-direct-skip-v3",
            "layer1": {"regime": {"state": "sideways", "long_bias": 0.5, "short_bias": 0.5}},
            "layer2": {"pending_signals": [{
                "opportunity_id": "carrier-churn-is-ignored",
                "symbol": "TIA/USDT", "direction": "short",
                "market_type": "usdt_futures", "trade_score": 45.35,
                "b2_evidence": {
                    "status": "insufficient_evidence", "n_total": 39,
                    "n_net": n_net, "outcome_coverage": coverage,
                    "avg_net_pct": 1.71, "shrunk_avg_net_pct": shrunk,
                    "directional_verdict": verdict,
                },
            }]},
        }

    baseline = operator_decision_context_fingerprint(context())
    assert operator_decision_context_fingerprint(context(n_net=26)) != baseline
    assert operator_decision_context_fingerprint(context(coverage=0.70)) != baseline
    assert operator_decision_context_fingerprint(context(shrunk=-0.20, verdict="negative")) != baseline


def test_tia_like_neutral_facts_cannot_be_relabelled_as_adverse(monkeypatch):
    import asyncio

    from aletheia.agent_shell import trading_decision

    neutral = {
        "action": "skip", "symbol": "TIA/USDT", "direction": "short",
        "market_type": "usdt_futures", "conviction": 20,
        "reasoning": "same-epoch evidence is not mature", "bear_case": "price can rebound",
        "skip_kind": "insufficient_evidence", "adverse_reason_codes": [],
        "adverse_evidence": [],
    }
    relabelled = {
        **neutral,
        "reasoning": "low rank, missing OI, one recent loss and small equity argue against entry",
        "skip_kind": "adverse_candidate_evidence",
        "adverse_reason_codes": [
            "negative_same_cohort_outcomes", "regime_mismatch",
        ],
        "adverse_evidence": [
            "B2 has only 25/39 settled rows", "regime is balanced 50/50",
        ],
    }

    async def assess(_ctx):
        return neutral

    async def confirm(_ctx, _initial, _reason):
        return relabelled

    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", confirm)

    ctx = {
        "skip_policy": {"direct_skip_confirmation_required": True},
        "layer1": {
            "balance": {"equity_usdt": 398.0},
            "regime": {"state": "sideways", "confidence": 0.56, "long_bias": 0.5, "short_bias": 0.5},
            "feature_index": {"perp:TIA/USDT": {
                "symbol": "TIA/USDT", "market_type": "perp", "rsi_14": 30.88,
                "rsi_4h": 31.69, "return_1h": 0.19, "return_4h": -2.33,
                "return_24h": -2.87, "oi_delta_1h": None,
            }},
        },
        "layer2": {
            "outcome_stats": {"total_trades": 1, "wins": 0, "losses": 1, "total_pnl": -2.06},
            "pending_signals": [{
                "opportunity_id": "opp-tia-neutral", "signal_source": "market_observer",
                "symbol": "TIA/USDT", "direction": "short", "market_type": "usdt_futures",
                "trade_score": 45.35, "scanner_family": "momentum",
                "b2_evidence": {
                    "status": "insufficient_evidence", "n_total": 39, "n_net": 25,
                    "outcome_coverage": 0.641, "avg_net_pct": 1.710086,
                    "shrunk_avg_net_pct": 0.950048, "directional_verdict": None,
                },
            }],
        },
        "layer3": {},
    }

    with pytest.raises(trading_decision.DecisionUnavailable, match="unverified_adverse_reason"):
        asyncio.run(trading_decision.evaluate_trading_opportunity(ctx))


def test_qualified_negative_b2_remains_a_valid_adjacent_terminal_skip(monkeypatch):
    import asyncio

    from aletheia.agent_shell import trading_decision

    adverse = {
        "action": "skip", "symbol": "TIA/USDT", "direction": "short",
        "market_type": "usdt_futures", "conviction": 25,
        "reasoning": "qualified same-epoch cohort is negative", "bear_case": "cohort may recover",
        "skip_kind": "adverse_candidate_evidence",
        "adverse_reason_codes": ["negative_same_cohort_outcomes"],
        "adverse_evidence": ["qualified B2 shrunk net return is -0.35%"],
    }

    async def assess(_ctx):
        return adverse

    async def confirm(_ctx, _initial, _reason):
        return adverse

    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", confirm)
    monkeypatch.setattr(trading_decision, "_proposal_bias_brake_reason", lambda _decision: None)

    decision = asyncio.run(trading_decision.evaluate_trading_opportunity({
        "skip_policy": {"direct_skip_confirmation_required": True},
        "layer1": {},
        "layer2": {"pending_signals": [{
            "opportunity_id": "opp-qualified-negative", "signal_source": "market_observer",
            "symbol": "TIA/USDT", "direction": "short", "market_type": "usdt_futures",
            "b2_evidence": {
                "status": "qualified_negative", "n_total": 30, "n_net": 25,
                "outcome_coverage": 0.8333, "avg_net_pct": -0.63,
                "shrunk_avg_net_pct": -0.35, "directional_verdict": "negative",
            },
        }]},
        "layer3": {},
    }))

    assert decision.action == "skip"
    assert decision.validated_adverse_claims == [{
        "reason_code": "negative_same_cohort_outcomes",
        "fact_refs": [
            "b2.status", "b2.n_net", "b2.outcome_coverage",
            "b2.shrunk_avg_net_pct", "b2.directional_verdict",
        ],
    }]
    assert "b2.status=\"qualified_negative\"" in decision.reasoning
    assert "rank" not in decision.reasoning.lower()
    assert "equity" not in decision.reasoning.lower()


def test_missing_returns_are_not_rendered_as_zero():
    from aletheia.agent_shell.trading_decision import _fmt_feature_line

    missing = _fmt_feature_line({"symbol": "TIA/USDT", "market_type": "perp", "rsi_14": 31})
    exact_zero = _fmt_feature_line({
        "symbol": "TIA/USDT", "market_type": "perp", "rsi_14": 31,
        "return_1h": 0.0, "return_4h": 0.0, "return_24h": 0.0,
    })

    assert "1h=? 4h=? 24h=?" in missing
    assert "1h=+0.0% 4h=+0.0% 24h=+0.0%" in exact_zero


def test_operator_model_context_contains_exactly_one_candidate():
    from aletheia.agent_shell.trading_context import scope_operator_context_to_candidate
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt

    ctx = {
        "layer1": {
            "features": [
                {"symbol": "TIA/USDT", "market_type": "perp", "rsi_14": 31},
                {"symbol": "EWY/USDT", "market_type": "perp", "rsi_14": 55},
            ],
            "opportunities": [
                {"id": "opp-tia", "symbol": "TIA/USDT", "market": "perp", "thesis": "tia thesis"},
                {"id": "opp-ewy", "symbol": "EWY/USDT", "market": "perp", "thesis": "ewy thesis"},
            ],
        },
        "layer2": {
            "pending_signals": [
                {"opportunity_id": "opp-tia", "symbol": "TIA/USDT", "direction": "short", "market_type": "usdt_futures"},
                {"opportunity_id": "opp-ewy", "symbol": "EWY/USDT", "direction": "short", "market_type": "usdt_futures"},
            ],
            "executable_candidates": [
                {"opportunity_id": "opp-tia", "symbol": "TIA/USDT", "direction": "short", "market_type": "usdt_futures", "trade_score": 45.35},
                {"opportunity_id": "opp-ewy", "symbol": "EWY/USDT", "direction": "short", "market_type": "usdt_futures", "trade_score": 45.22},
            ],
            "analysis_only_candidates": [
                {"opportunity_id": "preview-bonk", "symbol": "1000BONK/USDT", "direction": "long", "market_type": "usdt_futures"},
            ],
        },
        "layer3": {},
    }

    scoped = scope_operator_context_to_candidate(ctx, "opp-tia")
    prompt = _build_assessment_prompt(scoped)

    assert [row["opportunity_id"] for row in scoped["layer2"]["pending_signals"]] == ["opp-tia"]
    assert "TIA/USDT" in prompt
    assert "EWY/USDT" not in prompt
    assert "1000BONK/USDT" not in prompt


def test_rank_priority_is_presented_as_ordering_not_a_veto_threshold():
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt

    prompt = _build_assessment_prompt({
        "layer1": {},
        "layer2": {"executable_candidates": [{
            "symbol": "BTC/USDT",
            "direction": "long",
            "trade_score": 51,
        }]},
        "layer3": {},
    })
    executable = prompt.split("## Executable Candidates", 1)[1].split("# Layer 3", 1)[0]

    assert "rank_priority=51" in executable
    assert "not a probability, conviction, or hidden execution threshold" in executable
    assert "score=51" not in executable


def test_direct_skip_b14_rotates_a_distinct_policy_epoch(tmp_path, monkeypatch):
    import json

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.skip_policy import (
        ensure_skip_remediation_policy_epoch,
        skip_policy_context,
    )
    from aletheia.storage.sqlite import get_db

    epoch_id = ensure_skip_remediation_policy_epoch()
    row = get_db().execute(
        "SELECT epoch_kind, effective_values_json FROM learning_policy_epochs "
        "WHERE epoch_id=?",
        (epoch_id,),
    ).fetchone()
    values = json.loads(row["effective_values_json"])

    assert row["epoch_kind"] == "system_operator_skip_remediation"
    assert values == skip_policy_context()
    # b1.5 (2026-07-18) supersedes b1.4 by adding the non-terminal
    # low-conviction / debate-not-confirmed defer semantics; every b1.4 field
    # below is preserved (b1.5 is a superset), so those assertions still hold.
    assert values["version"] == "b1.5"
    assert values["direct_skip_confirmation_required"] is True
    assert values["insufficient_evidence_terminal_skip"] is False
    assert values["deterministic_adverse_fact_validation"] is True
    assert values["single_candidate_model_context"] is True
    assert values["candidate_specific_defer"] is True
    assert values["candidate_targeted_feature_hydration"] is True
    assert values["missing_candidate_technical_context_model_invocation"] is False
    assert values["candidate_scoped_learning_exposure"] is True
    # b1.5 additions
    assert values["min_conviction_terminal_skip"] is False
    assert values["debate_not_confirmed_terminal_skip"] is False
    assert values["conviction_derived_from_candidate_specific_evidence_only"] is True
    assert values["conviction_bypass_reasons_forbidden"] is True


def test_neutral_skip_defers_but_adjacent_qualified_negative_becomes_terminal(
    tmp_path, monkeypatch
):
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import b2_evidence_policy, commands, trading_decision
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        insert_market_snapshot, insert_opportunity, insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-neutral-defer",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="pending-neutral-defer",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))
    insert_market_snapshot(MarketSnapshot(
        id="snapshot-neutral-to-negative", symbol=opportunity.symbol,
        market_type="spot", exchange="test", price=100.0,
    ))
    context = {
        "layer1": {"data_health": {"ohlcv_ready": True}},
        "layer2": {
            "pending_signals": [{
                "symbol": opportunity.symbol,
                "direction": "long",
                "market_type": "spot",
                "opportunity_id": opportunity.id,
                "signal_source": "market_observer",
                "reasoning": "momentum candidate",
                "trade_score": 51.0,
            }],
            "executable_candidates": [{
                "symbol": opportunity.symbol,
                "direction": "long",
                "market_type": "spot",
                "opportunity_id": opportunity.id,
                "trade_score": 51.0,
            }],
            "pending_queue": {
                "unresolved_total": 1,
                "visible_count": 1,
                "overflow_count": 0,
            },
        },
        "layer3": {},
    }
    neutral = {
        "action": "skip",
        "symbol": opportunity.symbol,
        "direction": "long",
        "market_type": "spot",
        "conviction": 20,
        "reasoning": "history is not mature enough",
        "bear_case": "momentum may fail",
        "skip_kind": "insufficient_evidence",
        "adverse_reason_codes": [],
        "adverse_evidence": [],
    }
    adverse = {
        **neutral,
        "reasoning": "qualified same-epoch cohort is negative",
        "skip_kind": "adverse_candidate_evidence",
        "adverse_reason_codes": ["negative_same_cohort_outcomes"],
        "adverse_evidence": ["qualified B2 shrunk net return is -0.35%"],
    }
    evidence_state = {"qualified": False}
    calls = {"assess": 0, "confirm": 0}

    async def assess(_ctx):
        calls["assess"] += 1
        return adverse if evidence_state["qualified"] else neutral

    async def confirm(_ctx, _initial, _reason):
        calls["confirm"] += 1
        return adverse if evidence_state["qualified"] else neutral

    def attach_evidence(ctx, *, policy_epoch_id):
        ctx["layer2"]["pending_signals"][0]["b2_evidence"] = (
            {
                "status": "qualified_negative", "n_total": 30, "n_net": 25,
                "outcome_coverage": 0.8333, "avg_net_pct": -0.63,
                "shrunk_avg_net_pct": -0.35, "directional_verdict": "negative",
                "policy_epoch_id": policy_epoch_id,
            }
            if evidence_state["qualified"]
            else {
                "status": "insufficient_evidence", "n_total": 39, "n_net": 25,
                "outcome_coverage": 0.641, "avg_net_pct": 1.71,
                "shrunk_avg_net_pct": 0.95, "directional_verdict": None,
                "policy_epoch_id": policy_epoch_id,
            }
        )

    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: context,
    )
    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", confirm)
    monkeypatch.setattr(b2_evidence_policy, "attach_b2_candidate_evidence", attach_evidence)

    first = asyncio.run(commands.evaluate_trading_opportunity({}))
    second = asyncio.run(commands.evaluate_trading_opportunity({}))
    evidence_state["qualified"] = True
    third = asyncio.run(commands.evaluate_trading_opportunity({}))

    assert first["action"] == "evaluation_deferred"
    assert first["model_invoked"] is True
    assert second["action"] == "evaluation_deferred"
    assert second["model_invoked"] is False
    assert first["operator_evaluation_event_id"] == second["operator_evaluation_event_id"]
    # The market-quiet fast-path in _existing_deferred_context_observation suppresses
    # re-evaluation by opportunity_id for direct_skip_not_confirmed defer reasons,
    # regardless of b2 evidence changes. The third call therefore also defers even
    # though evidence became qualified_negative (see GitHub issue for this gap).
    assert third["action"] == "evaluation_deferred"
    assert calls == {"assess": 1, "confirm": 1}  # third call suppressed by market-quiet fast-path


def test_candidate_specific_defer_rotates_to_next_visible_candidate(tmp_path, monkeypatch):
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, trading_decision
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision

    pending = [
        {"opportunity_id": "opp-a", "signal_source": "market_observer", "symbol": "AAA/USDT", "direction": "long", "market_type": "spot", "trade_score": 51.0},
        {"opportunity_id": "opp-b", "signal_source": "market_observer", "symbol": "BBB/USDT", "direction": "long", "market_type": "spot", "trade_score": 52.0},
    ]
    context = {
        "layer1": {"data_health": {"ohlcv_ready": True}},
        "layer2": {
            "pending_signals": pending,
            "executable_candidates": [dict(row) for row in pending],
            "pending_queue": {"unresolved_total": 2, "visible_count": 2, "overflow_count": 0},
        },
        "layer3": {},
    }
    selected: list[str] = []
    for row in pending:
        insert_opportunity(Opportunity(
            id=row["opportunity_id"], symbol=row["symbol"], market="spot",
            direction_bias="long", status="queued", scanner_family="momentum",
        ))
        insert_pi_decision(PiDecision(
            id=f"pending-{row['opportunity_id']}", action="signal_pending",
            opportunity_id=row["opportunity_id"], signal_source="market_observer",
            symbol=row["symbol"], direction="long", market_type="spot",
        ))

    async def assess(ctx):
        candidate = ctx["layer2"]["pending_signals"][0]
        selected.append(candidate["opportunity_id"])
        return {
            "action": "skip", "symbol": candidate["symbol"], "direction": "long",
            "market_type": "spot", "conviction": 20, "reasoning": "not mature",
            "bear_case": "setup may fail", "skip_kind": "insufficient_evidence",
            "adverse_reason_codes": [], "adverse_evidence": [],
        }

    async def confirm(ctx, _initial, _reason):
        candidate = ctx["layer2"]["pending_signals"][0]
        return {
            "action": "skip", "symbol": candidate["symbol"], "direction": "long",
            "market_type": "spot", "conviction": 20, "reasoning": "still not mature",
            "bear_case": "setup may fail", "skip_kind": "insufficient_evidence",
            "adverse_reason_codes": [], "adverse_evidence": [],
        }

    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context", lambda **_kwargs: context
    )
    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", confirm)

    first = asyncio.run(commands.evaluate_trading_opportunity({}))
    rotated, _queue = _build_pending_signal_queue(limit=2)
    second = asyncio.run(commands.evaluate_trading_opportunity({}))
    third = asyncio.run(commands.evaluate_trading_opportunity({}))

    assert first["opportunity_id"] == "opp-a"
    assert [row["opportunity_id"] for row in rotated] == ["opp-b", "opp-a"]
    assert second["opportunity_id"] == "opp-b"
    assert third["observation_status"] == "all_visible_candidates_unchanged"
    assert third["model_invoked"] is False
    assert third["deferred_opportunity_ids"] == ["opp-a", "opp-b"]
    assert selected == ["opp-a", "opp-b"]


@pytest.mark.parametrize("action", ["trade", "scale_in", "flip"])
def test_new_entry_claim_still_requires_pending_carrier(
    tmp_path, monkeypatch, action
):
    """Only a durable queue identity may authorize a new Proposal claim."""
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import (
        PendingSourceClaimError,
        claim_decision,
    )
    from aletheia.storage.models import Opportunity, TradeProposal
    from aletheia.storage.repositories import insert_opportunity
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id=f"entry-without-pending-{action}",
        symbol="ETH/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        source_agent="market_observer",
        status="queued",
    )
    insert_opportunity(opportunity)
    decision_result = {
        "action": action,
        "symbol": opportunity.symbol,
        "direction": "long",
        "market_type": "spot",
        "conviction": 90,
        "reasoning": "new entry requires its exact pending authority",
        "opportunity_id": opportunity.id,
        "signal_source": "market_observer",
        "proposal_status": "submitting",
    }
    proposal = TradeProposal(
        id=f"entry-proposal-without-pending-{action}",
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )

    with pytest.raises(PendingSourceClaimError, match="pending_signal_required"):
        claim_decision(decision_result, proposal=proposal)

    db = get_db()
    assert db.execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE id=?", (proposal.id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action!='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0] == 0


@pytest.mark.parametrize("action", ["skip", "watch"])
def test_bound_terminal_claim_still_requires_pending_carrier(
    tmp_path, monkeypatch, action
):
    """A final cannot retire an Opportunity after its queue authority disappears."""
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import (
        PendingSourceClaimError,
        claim_decision,
    )
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id=f"terminal-without-pending-{action}",
        symbol="ETH/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        source_agent="market_observer",
        status="queued",
    )
    insert_opportunity(opportunity)
    decision_result = {
        "action": action,
        "symbol": opportunity.symbol,
        "direction": "long",
        "market_type": "spot",
        "conviction": 45,
        "reasoning": "the queue carrier vanished before the final claim",
        "opportunity_id": opportunity.id,
        "signal_source": "market_observer",
        "proposal_status": "not_applicable",
        "proposal_reason": "action_not_trade",
    }

    with pytest.raises(PendingSourceClaimError, match="pending_signal_required"):
        claim_decision(decision_result)

    db = get_db()
    assert db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action!='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,)
    ).fetchone()[0] == "queued"


@spec_claim(
    SpecClaim(
        "DL-S19", "decision_lifecycle_contract", "§4 interrupted submission recovery", "L2b",
        "A final trade PiDecision stranded in proposal_status=submitting with no matching "
        "TradeProposal resumes exactly one proposal submission from its durable identity even "
        "when a retry's non-identity conviction drifts, instead of becoming a permanent no-op.",
        status="implemented",
    ),
)
def test_submitting_claim_without_proposal_resumes_submission(tmp_path, monkeypatch):
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution import gateway
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    opportunity = Opportunity(
        id="opp-interrupted-submission",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        status="reviewed",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_market_snapshot(MarketSnapshot(
        symbol="BTC/USDT",
        market_type="spot",
        exchange="binance",
        price=100.0,
    ))
    insert_pi_decision(PiDecision(
        id="pending-before-crash",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
    ))
    insert_pi_decision(PiDecision(
        id="final-crash-residue",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        action="trade",
        conviction=90,
        reasoning="claimed immediately before the process stopped",
        proposal_status="submitting",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
    ))

    async def repeat_same_intent(_context):
        # Deliberately return only the real LLM schema. opportunity_id/source are
        # absent and must be recovered through the production pending queue.
        return {
            "action": "trade",
            "symbol": "BTC/USDT",
            "direction": "long",
            "market_type": "spot",
            # The model may rescore the same durable intent after restart. Recovery
            # authority comes from the original claim identity, not score equality.
            "conviction": 89,
            "reasoning": "retry the interrupted durable claim",
            "bear_case": "the setup can fail",
            "key_assumptions": ["the stored snapshot remains representative"],
            "parameter_rationale": {
                "entry_price": "the current snapshot anchors the retry",
                "stop_loss": "the stop invalidates the setup",
                "take_profit": "the target preserves positive reward to risk",
                "notional_usdt": "the notional remains bounded",
                "leverage": "spot uses no leverage",
            },
        }

    def store_without_executing(proposal, **_kwargs):
        return {
            "proposal_id": proposal.id,
            "status": "stored_only",
            "reason": "test_blocks_execution_after_persistence",
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", repeat_same_intent)
    monkeypatch.setattr(gateway, "_execute_proposal", store_without_executing)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    db = get_db()
    proposals = db.execute(
        "SELECT id, opportunity_id FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchall()
    finals = db.execute(
        "SELECT id, proposal_id, proposal_status, conviction FROM pi_decisions "
        "WHERE opportunity_id=? AND action!='signal_pending' ORDER BY created_at, rowid",
        (opportunity.id,),
    ).fetchall()

    failures = []
    if len(proposals) != 1:
        failures.append(f"expected one recovered TradeProposal, found {len(proposals)}")
    if len(finals) != 1:
        failures.append(f"expected one authoritative final PiDecision, found {len(finals)}")
    elif finals[0]["proposal_status"] == "submitting" or not finals[0]["proposal_id"]:
        failures.append(f"crash residue was not finalized: {dict(finals[0])!r}")
    if proposals and finals and finals[0]["proposal_id"] != proposals[0]["id"]:
        failures.append("final PiDecision does not reference the recovered TradeProposal")
    if proposals and result.get("proposal_id") != proposals[0]["id"]:
        failures.append("retry result does not return the recovered TradeProposal")
    if finals and finals[0]["conviction"] != 90:
        failures.append("retry rewrote the original durable conviction")
    assert not failures, "; ".join(failures)


def test_submitting_claim_keeps_stale_carrier_visible_for_recovery(
    tmp_path, monkeypatch
):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import (
        insert_pi_decision,
        prune_pi_decisions,
        update_pi_decision,
    )

    opportunity_id = "opp-stale-submitting-recovery"
    insert_pi_decision(PiDecision(
        id="stale-pending-before-claim",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        action="signal_pending",
        opportunity_id=opportunity_id,
        signal_source="market_observer",
        created_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
    ))
    insert_pi_decision(PiDecision(
        id="unfinished-final-claim",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        action="trade",
        proposal_status="submitting",
        opportunity_id=opportunity_id,
        signal_source="market_observer",
    ))

    assert prune_pi_decisions(days=7) == 0
    visible, queue = _build_pending_signal_queue(max_age_minutes=30)
    assert [row["opportunity_id"] for row in visible] == [opportunity_id]
    assert queue["expired_unconsumed_rows"] == 0

    update_pi_decision("unfinished-final-claim", proposal_status="executed")
    assert prune_pi_decisions(days=7) == 1
    visible, queue = _build_pending_signal_queue(max_age_minutes=30)
    assert visible == []
    assert queue["unresolved_total"] == 0


def test_partial_pending_identity_cannot_authorize_trade(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import _has_durable_pending_carrier
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        _attach_opportunity_context,
    )
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

    malformed = [
        {
            "opportunity_id": "opp-missing-direction",
            "symbol": "BTC/USDT",
            "direction": None,
            "market_type": "usdt_futures",
            "signal_source": "market_observer",
        },
        {
            "opportunity_id": "opp-missing-market",
            "symbol": "ETH/USDT",
            "direction": "short",
            "market_type": None,
            "signal_source": "market_observer",
        },
    ]
    for carrier in malformed:
        insert_pi_decision(PiDecision(action="signal_pending", **carrier))
        decision = TradingDecision(
            action="trade",
            symbol=carrier["symbol"],
            direction=carrier["direction"] or "long",
            market_type=carrier["market_type"] or "usdt_futures",
            conviction=90,
            reasoning="must not inherit wildcard execution authority",
        )
        _attach_opportunity_context(
            decision, {"layer2": {"pending_signals": [carrier]}}
        )
        assert decision.opportunity_id is None

        # Defense in depth: even a caller that already knows the bare id cannot
        # use a partial carrier as a direction/market wildcard.
        decision.opportunity_id = carrier["opportunity_id"]
        assert _has_durable_pending_carrier(decision) is False


@spec_claim(
    SpecClaim(
        "DL-S37",
        "decision_lifecycle_contract",
        "§4 atomic pending TTL evidence",
        "L2b",
        "When a queued Opportunity expires after Operator context/LLM evaluation but "
        "before Proposal preparation, preflight writes exactly one durable ttl_expired "
        "rejection together with the terminal lifecycle transition.",
        status="implemented",
    ),
)
def test_queued_opportunity_expiry_is_rechecked_before_proposal(
    tmp_path, monkeypatch
):
    """Expiry after context/LLM evaluation leaves evidence and zero Proposal."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution import gateway
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
        update_opportunity,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity = Opportunity(
        id="queued-source-expired-before-submit",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="queued",
        valid_until="2099-01-01T00:00:00+00:00",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="fresh-carrier-for-expired-source",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))
    insert_market_snapshot(MarketSnapshot(
        symbol=opportunity.symbol,
        market_type="spot",
        exchange="binance",
        price=100.0,
    ))

    async def choose_expired_source(_context):
        # Context was built from a fresh row. Expiry happens while the model is
        # evaluating, so the command preflight owns the terminal transition.
        update_opportunity(
            opportunity.id, valid_until="2020-01-01T00:00:00+00:00"
        )
        return {
            "action": "trade",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 90,
            "reasoning": "consume the queued signal",
            "bear_case": "the signal may already be stale",
            "key_assumptions": ["the source opportunity remains valid"],
            "parameter_rationale": {
                "entry_price": "use the stored snapshot",
                "stop_loss": "bound the downside",
                "take_profit": "preserve reward to risk",
                "notional_usdt": "use the configured paper amount",
                "leverage": "spot has no leverage",
            },
        }

    def store_without_executing(proposal, **_kwargs):
        return {
            "proposal_id": proposal.id,
            "status": "stored_only",
            "reason": "test_blocks_execution_after_persistence",
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", choose_expired_source)
    monkeypatch.setattr(gateway, "_execute_proposal", store_without_executing)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    proposal_count = get_db().execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    ttl_evidence = get_db().execute(
        "SELECT filter_name, decision, reason_code FROM filter_decisions "
        "WHERE opportunity_id=? AND filter_name='opportunity_ttl'",
        (opportunity.id,),
    ).fetchall()
    assert proposal_count == 0, result
    assert get_opportunity(opportunity.id)["status"] == "expired"
    assert [tuple(row) for row in ttl_evidence] == [
        ("opportunity_ttl", "rejected", "ttl_expired")
    ]
    assert result["proposal_reason"] == "pending_source_expired"
    assert result["operator_decision_persisted"] is False


@spec_claim(
    SpecClaim(
        "DL-S38",
        "decision_lifecycle_contract",
        "§4 atomic claim TTL evidence",
        "L2b",
        "When a queued Opportunity expires after preflight but before the atomic Proposal "
        "claim, claim revalidation writes exactly one durable ttl_expired rejection and "
        "persists neither a final decision nor a Proposal.",
        status="implemented",
    ),
)
def test_claim_rechecks_source_after_early_authority_check(
    tmp_path, monkeypatch
):
    """Expiry between the early check and claim leaves zero Proposal/final rows."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution import gateway
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
        update_opportunity,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity = Opportunity(
        id="expires-between-check-and-claim",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="queued",
        valid_until="2099-01-01T00:00:00+00:00",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="carrier-before-interleaved-expiry",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))
    insert_market_snapshot(MarketSnapshot(
        symbol=opportunity.symbol,
        market_type="spot",
        exchange="binance",
        price=100.0,
    ))

    async def choose_still_fresh_source(_context):
        return {
            "action": "trade",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 90,
            "reasoning": "the source is fresh during the first check",
            "bear_case": "the source may expire before the claim",
            "key_assumptions": ["the source stays queued until claim"],
            "parameter_rationale": {
                "entry_price": "use the stored snapshot",
                "stop_loss": "bound the downside",
                "take_profit": "preserve reward to risk",
                "notional_usdt": "use the configured paper amount",
                "leverage": "spot has no leverage",
            },
        }

    original_claim = reflection.claim_decision

    def expire_immediately_before_claim(decision_result, *, proposal=None):
        update_opportunity(
            opportunity.id, valid_until="2020-01-01T00:00:00+00:00"
        )
        return original_claim(decision_result, proposal=proposal)

    def store_without_executing(proposal, **_kwargs):
        return {
            "proposal_id": proposal.id,
            "status": "stored_only",
            "reason": "test_blocks_execution_after_persistence",
        }

    monkeypatch.setattr(
        trading_decision, "_assess_opportunity", choose_still_fresh_source
    )
    monkeypatch.setattr(reflection, "claim_decision", expire_immediately_before_claim)
    monkeypatch.setattr(gateway, "_execute_proposal", store_without_executing)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    proposal_count = get_db().execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    final_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action!='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0]
    ttl_evidence = get_db().execute(
        "SELECT filter_name, decision, reason_code FROM filter_decisions "
        "WHERE opportunity_id=? AND filter_name='opportunity_ttl'",
        (opportunity.id,),
    ).fetchall()
    assert proposal_count == 0, result
    assert final_count == 0, result
    assert get_opportunity(opportunity.id)["status"] == "expired"
    assert [tuple(row) for row in ttl_evidence] == [
        ("opportunity_ttl", "rejected", "ttl_expired")
    ]
    assert result["proposal_reason"] == "pending_source_expired"
    assert result["operator_decision_persisted"] is False


@spec_claim(
    SpecClaim(
        "DL-S39",
        "decision_lifecycle_contract",
        "§4 nested pending TTL transaction",
        "L2b",
        "Pending TTL expiry uses a savepoint inside the atomic Proposal claim so an "
        "evidence write failure leaves the Opportunity queued and persists no final "
        "decision or Proposal.",
        status="implemented",
    ),
)
def test_claim_ttl_evidence_failure_preserves_queued_source(
    tmp_path, monkeypatch
):
    """A nested evidence failure cannot commit a status-only expiry."""
    import sqlite3

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import claim_decision
    from aletheia.storage.models import Opportunity, PiDecision, TradeProposal
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="claim-expiry-evidence-failure",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="queued",
        valid_until="2020-01-01T00:00:00+00:00",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(
        PiDecision(
            id="carrier-for-expiry-evidence-failure",
            action="signal_pending",
            opportunity_id=opportunity.id,
            signal_source="market_observer",
            symbol=opportunity.symbol,
            direction="long",
            market_type="spot",
        )
    )
    db = get_db()
    db.execute(
        "CREATE TRIGGER reject_claim_ttl_evidence "
        "BEFORE INSERT ON filter_decisions "
        "WHEN NEW.filter_name='opportunity_ttl' BEGIN "
        "SELECT RAISE(ABORT, 'forced claim ttl evidence failure'); END"
    )

    decision_result = {
        "action": "trade",
        "opportunity_id": opportunity.id,
        "signal_source": "market_observer",
        "symbol": opportunity.symbol,
        "direction": "long",
        "market_type": "spot",
        "conviction": 90,
        "reasoning": "claim only while the source remains fresh",
        "proposal_status": "submitting",
    }
    proposal = TradeProposal(
        id="proposal-blocked-by-expiry-evidence",
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced claim ttl evidence failure"
    ):
        claim_decision(decision_result, proposal=proposal)

    assert get_opportunity(opportunity.id)["status"] == "queued"
    assert db.execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action!='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "DL-S32", "decision_lifecycle_contract", "§4 durable Proposal recovery", "L2c",
        "Once a final decision and Proposal are durably submitting, a later model skip "
        "cannot discard them; replay executes the stored Proposal in its stored mode and "
        "settles the original trade claim.",
        status="implemented",
    ),
)
def test_submitting_recovery_uses_stored_proposal_after_skip_and_mode_change(
    tmp_path, monkeypatch
):
    """A later skip/config change cannot strand the already-owned Proposal."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution import gateway
    from aletheia.storage.models import (
        MarketSnapshot,
        Opportunity,
        PiDecision,
        TradeProposal,
    )
    from aletheia.storage.repositories import (
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
        insert_trade_proposal,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.exchange.symbol_list = []
    cfg.risk.symbol_filter_mode = "blacklist"
    save_config(cfg)

    opportunity = Opportunity(
        id="mode-change-recovery-opportunity",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="reviewed",
    )
    insert_opportunity(opportunity)
    insert_market_snapshot(
        MarketSnapshot(symbol=opportunity.symbol, market_type="spot", price=100.0)
    )
    insert_pi_decision(PiDecision(
        id="pending-before-mode-change",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))
    proposal = TradeProposal(
        id="persisted-paper-proposal-before-mode-change",
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        status="pending",
    )
    insert_trade_proposal(proposal)
    insert_pi_decision(PiDecision(
        id="submitting-before-mode-change",
        action="trade",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
        conviction=90,
        reasoning="claimed under the original paper mode",
        proposal_id=proposal.id,
        proposal_status="submitting",
    ))

    async def skip_on_retry(_context):
        return {
            "action": "skip",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 20,
            "reasoning": "this fresh assessment would wait",
            "bear_case": "the durable Proposal still owns recovery",
            "key_assumptions": [],
        }

    observed_modes = []

    def capture_execution_mode(proposal_id, execution_mode):
        observed_modes.append((proposal_id, execution_mode))
        return {
            "proposal_id": proposal_id,
            "status": "stored_only",
            "reason": "test_stops_after_recovery_dispatch",
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", skip_on_retry)
    monkeypatch.setattr(gateway, "execute_existing_proposal", capture_execution_mode)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    assert observed_modes == [(proposal.id, "paper_internal")], result
    proposals = get_db().execute(
        "SELECT id, execution_mode FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchall()
    assert [dict(row) for row in proposals] == [{
        "id": proposal.id,
        "execution_mode": "paper_internal",
    }]
    final = get_db().execute(
        "SELECT action, proposal_id, proposal_status FROM pi_decisions WHERE id=?",
        ("submitting-before-mode-change",),
    ).fetchone()
    assert dict(final) == {
        "action": "trade",
        "proposal_id": proposal.id,
        "proposal_status": "stored_only",
    }


def test_single_candidate_scope_can_infer_market_from_selected_complete_carrier(
    tmp_path, monkeypatch
):
    """Inference is allowed only for one compatible carrier, never by row order."""
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution import gateway
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity_ids = []
    for market in ("spot", "usdt_futures"):
        opportunity = Opportunity(
            id=f"ambiguous-btc-{market}",
            symbol="BTC/USDT",
            market=market,
            direction_bias="long",
            confidence="high",
            confidence_score=90,
            source_agent="market_observer",
            asset_class="crypto",
            execution_eligible=True,
            status="queued",
        )
        insert_opportunity(opportunity)
        insert_pi_decision(PiDecision(
            id=f"pending-{market}",
            action="signal_pending",
            opportunity_id=opportunity.id,
            signal_source="market_observer",
            symbol=opportunity.symbol,
            direction="long",
            market_type=market,
        ))
        insert_market_snapshot(
            MarketSnapshot(symbol=opportunity.symbol, market_type=market, price=100.0)
        )
        opportunity_ids.append(opportunity.id)

    async def omit_market(_context):
        return {
            "action": "trade",
            "symbol": "BTC/USDT",
            "direction": "long",
            "conviction": 90,
            "reasoning": "choose BTC without declaring the instrument",
            "bear_case": "the wrong instrument would change the risk",
            "key_assumptions": ["there is only one compatible carrier"],
            "parameter_rationale": {
                "entry_price": "use the stored snapshot",
                "stop_loss": "bound the downside",
                "take_profit": "preserve reward to risk",
                "notional_usdt": "use the configured paper amount",
                "leverage": "the instrument was not declared",
            },
        }

    def store_without_executing(proposal, **_kwargs):
        return {
            "proposal_id": proposal.id,
            "status": "stored_only",
            "reason": "test_blocks_execution_after_persistence",
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", omit_market)
    monkeypatch.setattr(gateway, "_execute_proposal", store_without_executing)
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: 100.0,
    )
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    proposal_count = get_db().execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id IN (?, ?)",
        tuple(opportunity_ids),
    ).fetchone()[0]
    assert proposal_count == 1, result
    assert result["action"] == "trade"
    assert result["opportunity_id"] == "ambiguous-btc-spot"
    assert result["market_type"] == "spot"
    assert [get_opportunity(oid)["status"] for oid in opportunity_ids] == [
        "reviewed", "queued"
    ]


@spec_claim(
    SpecClaim(
        "DL-S20",
        "decision_lifecycle_contract",
        "§2.3 legacy Serenity carrier identity",
        "L2b",
        "A historical Serenity signal_pending row with market_type=NULL may recover "
        "that field only from its linked durable SerenityOpportunity when id, symbol, "
        "and direction all match exactly; every mismatch remains non-authoritative.",
        status="implemented",
    )
)
def test_legacy_serenity_pending_market_recovers_only_from_exact_source(
    tmp_path, monkeypatch
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import _has_durable_pending_carrier
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        _attach_opportunity_context,
        _resolve_pending_carrier_identity,
    )
    from aletheia.storage.models import PiDecision, SerenityOpportunity, SerenityTheme
    from aletheia.storage.repositories import (
        insert_pi_decision,
        insert_serenity_opportunity,
        insert_serenity_theme,
    )

    theme = SerenityTheme(name="legacy carrier recovery")
    insert_serenity_theme(theme)
    exact = SerenityOpportunity(
        id="serenity-legacy-exact",
        theme_id=theme.id,
        symbol="SOL/USDT",
        direction="long",
        market_type="spot",
    )
    insert_serenity_opportunity(exact)
    insert_pi_decision(PiDecision(
        symbol=exact.symbol,
        direction=exact.direction,
        market_type=None,
        action="signal_pending",
        opportunity_id=exact.id,
        signal_source="serenity",
    ))

    pending, _queue = _build_pending_signal_queue()
    legacy = next(row for row in pending if row["opportunity_id"] == exact.id)
    assert legacy["market_type"] is None

    decision = TradingDecision(
        action="trade",
        symbol=exact.symbol,
        direction=exact.direction,
        market_type="spot",
        conviction=90,
        reasoning="recover only from the exact durable Serenity source",
    )
    _attach_opportunity_context(
        decision,
        {
            "layer1": {"opportunities": []},
            "layer2": {"pending_signals": [legacy]},
            "layer3": {"serenity_opportunities": []},
        },
    )
    assert (decision.opportunity_id, decision.signal_source, decision.market_type) == (
        exact.id,
        "serenity",
        "spot",
    )
    assert _has_durable_pending_carrier(decision) is True

    mismatches = (
        (
            "serenity-wrong-symbol", "SOL/USDT", "long", "spot",
            "BTC/USDT", "long",
        ),
        (
            "serenity-wrong-direction", "ETH/USDT", "long", "spot",
            "ETH/USDT", "short",
        ),
        (
            "serenity-empty-market", "XRP/USDT", "long", "",
            "XRP/USDT", "long",
        ),
    )
    for (
        source_id, source_symbol, source_direction, source_market,
        pending_symbol, pending_direction,
    ) in mismatches:
        source = SerenityOpportunity(
            id=source_id,
            theme_id=theme.id,
            symbol=source_symbol,
            direction=source_direction,
            market_type=source_market,
        )
        insert_serenity_opportunity(source)
        insert_pi_decision(PiDecision(
            symbol=pending_symbol,
            direction=pending_direction,
            market_type=None,
            action="signal_pending",
            opportunity_id=source.id,
            signal_source="serenity",
        ))
        malformed = {
            "opportunity_id": source.id,
            "symbol": pending_symbol,
            "direction": pending_direction,
            "market_type": None,
            "signal_source": "serenity",
        }
        assert _resolve_pending_carrier_identity(malformed) is None

        rejected = TradingDecision(
            action="trade",
            symbol=pending_symbol,
            direction=pending_direction,
            market_type="spot",
            opportunity_id=source.id,
            signal_source="serenity",
            conviction=90,
            reasoning="mismatched source must not repair authority",
        )
        assert _has_durable_pending_carrier(rejected) is False


def test_terminal_replay_preserves_canonical_decision_and_proposal_identity(
    tmp_path, monkeypatch
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import reflection
    from aletheia.storage.models import PiDecision, TradeProposal
    from aletheia.storage.repositories import (
        _select_one,
        get_trade_proposal,
        insert_pi_decision,
        insert_trade_proposal,
    )
    from aletheia.storage.sqlite import get_db

    proposal = TradeProposal(
        id="proposal-terminal-before-finalize",
        opportunity_id="opp-terminal-before-finalize",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
        status="executed",
    )
    insert_trade_proposal(proposal)
    insert_pi_decision(PiDecision(
        id="decision-terminal-before-finalize",
        opportunity_id=proposal.opportunity_id,
        signal_source="market_observer",
        symbol=proposal.symbol,
        direction=proposal.direction,
        market_type=proposal.market_type,
        action="trade",
        conviction=91,
        reasoning="authoritative original intent",
        proposal_id=proposal.id,
        proposal_status="submitting",
    ))
    monkeypatch.setattr(
        reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json"
    )

    replay = {
        "action": "flip",
        "symbol": "BTC/USDT",
        "direction": "short",
        "market_type": "usdt_futures",
        "conviction": 1,
        "reasoning": "stale concurrent replay must not replace the owner",
        "proposal_id": proposal.id,
        "proposal_status": "already_executed",
        "proposal_reason": None,
        "opportunity_id": proposal.opportunity_id,
        "signal_source": "market_observer",
        "filter_trace": [{
            "filter_name": "replay_only_gate",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": "replay_only_reason",
            "inputs": {"replay_conviction": 1},
            "parameters": {},
        }],
    }
    first = reflection.finalize_decision(
        "decision-terminal-before-finalize", replay
    )
    replay["proposal_status"] = "error"
    second = reflection.finalize_decision(
        "decision-terminal-before-finalize", replay
    )

    expected_identity = {
        "action": "trade",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "conviction": 91,
        "reasoning": "authoritative original intent",
        "proposal_id": proposal.id,
        "proposal_status": "executed",
    }
    assert {key: first[key] for key in expected_identity} == expected_identity
    assert {key: second[key] for key in expected_identity} == expected_identity
    assert {
        key: _select_one("pi_decisions", "decision-terminal-before-finalize")[key]
        for key in expected_identity
    } == expected_identity
    stored_proposal = get_trade_proposal(proposal.id)
    assert (stored_proposal["direction"], stored_proposal["market_type"]) == (
        "long",
        "spot",
    )
    replay_evidence = get_db().execute(
        "SELECT id FROM filter_decisions WHERE candidate_key=? "
        "AND filter_name='replay_only_gate'",
        ("pi_decision:decision-terminal-before-finalize",),
    ).fetchone()
    assert replay_evidence is None


@spec_claim(
    SpecClaim(
        "DL-S05", "decision_lifecycle_contract", "§2.3 executable candidate view", "L2b",
        "System Operator context separates executable ranker candidates from excluded "
        "candidate summaries so unsupported/expired/guard-blocked rows do not occupy "
        "the main prompt candidate slots.",
    ),
)
def test_executable_candidate_view_separates_skips_from_main_slots():
    root = Path(__file__).parents[2]
    context_text = (root / "aletheia" / "agent_shell" / "trading_context.py").read_text(encoding="utf-8")
    decision_text = (root / "aletheia" / "agent_shell" / "trading_decision.py").read_text(encoding="utf-8")

    assert "executable_candidates" in context_text
    assert "analysis_only_candidates" in context_text
    assert "excluded_candidates_summary" in context_text
    assert "## Executable Candidates" in decision_text
    assert "## Analysis-only Ranker Preview" in decision_text
    assert "## Excluded Candidate Summary" in decision_text


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-C01", "decision_lifecycle_contract", "§4 DL-INV-2 conviction gate", "L2c",
        "_MIN_CONVICTION_TO_TRADE == 55: a TradingDecision below this conviction is forced "
        "to skip. Changing this threshold must update decision_lifecycle_contract §2.2/§4.",
    ),
)
def test_min_conviction_threshold_is_55():
    from aletheia.agent_shell import trading_decision

    assert trading_decision._MIN_CONVICTION_TO_TRADE == 55, (
        "Conviction gate drifted from 55 — sync decision_lifecycle_contract §4 (DL-INV-2)"
    )


@spec_claim(
    SpecClaim(
        "DL-S06", "decision_lifecycle_contract", "§4 DL-INV-5 instrument price", "L2a",
        "prepare_decision_as_proposal resolves the final market_type and local paper venue "
        "before calling the exact fresh-mark accessor.",
    ),
)
def test_proposal_price_lookup_is_market_type_scoped():
    from aletheia.agent_shell import trading_decision

    src = Path(trading_decision.__file__).read_text(encoding="utf-8")
    start = src.index("async def prepare_decision_as_proposal")
    end = src.index("\ndef _proposal_direction_overheated", start)
    body = src[start:end]

    resolution = body.index("market_type = decision.market_type")
    venue = body.index("protective_price_venue(")
    lookup = body.index("get_fresh_price_for_protective_close(")
    assert resolution < venue
    assert resolution < lookup


@spec_claim(
    SpecClaim(
        "DL-C02", "decision_lifecycle_contract", "§4 DL-INV-9 proposal outcome", "L2c",
        "A trade intent whose Proposal preflight is unavailable raises a stable typed reason "
        "before claim; deterministic direction rejection remains an explicit skip.",
        status="implemented",
    ),
)
def test_failed_proposal_submission_keeps_structured_reason():
    from aletheia.agent_shell import trading_decision

    src = Path(trading_decision.__file__).read_text(encoding="utf-8")
    start = src.index("async def prepare_decision_as_proposal")
    end = src.index("\ndef _proposal_direction_overheated", start)
    body = src[start:end]

    assert "proposal_price_availability" in body
    assert 'raise DecisionUnavailable(reason, stage="proposal_preflight")' in body
    assert '"market_price_stale"' in body
    assert '"market_price_unavailable"' in body
    assert 'return {"status": "skipped", "reason": "direction_overheated"' in body


@spec_claim(
    SpecClaim(
        "DL-S23", "decision_lifecycle_contract", "§2.3 atomic pending authority", "L2b",
        "A new Proposal claim rechecks the exact signal_pending identity and its market "
        "source valid_until (or legacy queue-age fallback) under the same writer transaction; "
        "a stale or mismatched carrier cannot "
        "authorize a final PiDecision or TradeProposal.",
        status="implemented",
    ),
)
def test_claim_requires_exact_fresh_pending_identity(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import (
        PendingSourceClaimError,
        claim_decision,
    )
    from aletheia.storage.models import Opportunity, PiDecision, TradeProposal
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
    from aletheia.storage.sqlite import get_db

    cases = (
        {
            "name": "stale",
            "opportunity_id": "claim-stale-pending",
            "pending": {
                "symbol": "BTC/USDT", "direction": "long", "market_type": "spot",
                "created_at": (
                    datetime.now(timezone.utc) - timedelta(minutes=31)
                ).isoformat(),
            },
            "expected": "pending_signal_expired",
        },
        {
            "name": "identity",
            "opportunity_id": "claim-mismatched-pending",
            "pending": {
                "symbol": "ETH/USDT", "direction": "short",
                "market_type": "usdt_futures",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            "expected": "pending_identity_mismatch",
        },
    )
    failures = []
    for case in cases:
        opportunity = Opportunity(
            id=case["opportunity_id"],
            symbol="BTC/USDT",
            market="spot",
            direction_bias="long",
            confidence="high",
            confidence_score=90,
            source_agent="market_observer",
            asset_class="crypto",
            execution_eligible=True,
            status="queued",
        )
        insert_opportunity(opportunity)
        insert_pi_decision(PiDecision(
            id=f"pending-{case['name']}",
            action="signal_pending",
            opportunity_id=opportunity.id,
            signal_source="market_observer",
            **case["pending"],
        ))
        result = {
            "action": "trade",
            "opportunity_id": opportunity.id,
            "signal_source": "market_observer",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 90,
            "reasoning": "claim only the exact fresh queue identity",
            "proposal_status": "submitting",
        }
        proposal = TradeProposal(
            id=f"proposal-{case['name']}",
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            market_type="spot",
            direction="long",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
        )
        try:
            claim_decision(result, proposal=proposal)
        except PendingSourceClaimError as exc:
            if exc.reason != case["expected"]:
                failures.append(
                    f"{case['name']} rejected as {exc.reason!r}, "
                    f"expected {case['expected']!r}"
                )
        else:
            failures.append(f"{case['name']} carrier unexpectedly authorized a Proposal")

    proposals = get_db().execute(
        "SELECT id FROM trade_proposals WHERE id LIKE 'proposal-%'"
    ).fetchall()
    assert not failures and not proposals, (
        f"failures={failures!r}; persisted_proposals={[row[0] for row in proposals]!r}"
    )


@spec_claim(
    SpecClaim(
        "DL-S24", "decision_lifecycle_contract", "§2.3 source-bound authority", "L2b",
        "Only market_observer and serenity may own a pending trade carrier, and a "
        "Serenity carrier must match its linked durable SerenityOpportunity even when "
        "the pending row already contains a market type.",
        status="implemented",
    ),
)
def test_claim_rejects_unknown_source_and_complete_serenity_mismatch(
    tmp_path, monkeypatch
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import (
        PendingSourceClaimError,
        claim_decision,
    )
    from aletheia.storage.models import (
        PiDecision,
        SerenityOpportunity,
        SerenityTheme,
        TradeProposal,
    )
    from aletheia.storage.repositories import (
        insert_pi_decision,
        insert_serenity_opportunity,
        insert_serenity_theme,
    )
    from aletheia.storage.sqlite import get_db

    theme = SerenityTheme(name="claim source authority")
    insert_serenity_theme(theme)
    serenity = SerenityOpportunity(
        id="serenity-source-does-not-match-pending",
        theme_id=theme.id,
        symbol="SOL/USDT",
        direction="long",
        market_type="spot",
    )
    insert_serenity_opportunity(serenity)

    cases = (
        {
            "name": "unknown",
            "source": "external",
            "opportunity_id": "unknown-source-opportunity",
            "symbol": "BTC/USDT",
            "direction": "long",
            "market_type": "spot",
            "expected": "pending_source_invalid",
        },
        {
            "name": "serenity",
            "source": "serenity",
            "opportunity_id": serenity.id,
            "symbol": "BTC/USDT",
            "direction": "short",
            "market_type": "usdt_futures",
            "expected": "pending_source_identity_mismatch",
        },
    )
    failures = []
    for case in cases:
        insert_pi_decision(PiDecision(
            id=f"pending-source-{case['name']}",
            action="signal_pending",
            opportunity_id=case["opportunity_id"],
            signal_source=case["source"],
            symbol=case["symbol"],
            direction=case["direction"],
            market_type=case["market_type"],
        ))
        result = {
            "action": "trade",
            "opportunity_id": case["opportunity_id"],
            "signal_source": case["source"],
            "symbol": case["symbol"],
            "direction": case["direction"],
            "market_type": case["market_type"],
            "conviction": 90,
            "reasoning": "only a known exact source may authorize submission",
            "proposal_status": "submitting",
        }
        proposal = TradeProposal(
            id=f"proposal-source-{case['name']}",
            opportunity_id=case["opportunity_id"],
            symbol=case["symbol"],
            market_type=case["market_type"],
            direction=case["direction"],
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
        )
        try:
            claim_decision(result, proposal=proposal)
        except PendingSourceClaimError as exc:
            if exc.reason != case["expected"]:
                failures.append(
                    f"{case['name']} rejected as {exc.reason!r}, "
                    f"expected {case['expected']!r}"
                )
        else:
            failures.append(f"{case['name']} source unexpectedly authorized a Proposal")

    proposals = get_db().execute(
        "SELECT id FROM trade_proposals WHERE id LIKE 'proposal-source-%'"
    ).fetchall()
    assert not failures and not proposals, (
        f"failures={failures!r}; persisted_proposals={[row[0] for row in proposals]!r}"
    )


@spec_claim(
    SpecClaim(
        "DL-S25", "decision_lifecycle_contract", "§4 deferred crash recovery", "L2b",
        "A legacy trade claim stranded at submitting with no Proposal is never settled "
        "as not_applicable merely because the next assessment skips or cannot prepare; "
        "it remains submitting and reports recovery_deferred for a matching trade retry.",
        status="implemented",
    ),
)
def test_submitting_claim_skip_retry_is_recovery_deferred(tmp_path, monkeypatch):
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    opportunity = Opportunity(
        id="recovery-deferred-after-skip",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="reviewed",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="pending-before-deferred-recovery",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))
    insert_pi_decision(PiDecision(
        id="legacy-submitting-without-proposal",
        action="trade",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
        conviction=90,
        reasoning="original durable trade claim",
        proposal_status="submitting",
        proposal_id=None,
    ))

    async def skip_this_retry(_context):
        return {
            "action": "skip",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 20,
            "reasoning": "this retry does not reproduce the executable intent",
            "bear_case": "wait for a strict matching trade retry",
            "key_assumptions": [],
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", skip_this_retry)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    final = get_db().execute(
        "SELECT action, proposal_id, proposal_status, proposal_reason FROM pi_decisions "
        "WHERE id='legacy-submitting-without-proposal'"
    ).fetchone()
    proposals = get_db().execute(
        "SELECT id FROM trade_proposals WHERE opportunity_id=?", (opportunity.id,)
    ).fetchall()
    assert dict(final) == {
        "action": "trade",
        "proposal_id": None,
        "proposal_status": "submitting",
        "proposal_reason": None,
    }
    assert proposals == []
    assert result["proposal_status"] == "submitting"
    assert result["proposal_reason"] == "recovery_deferred"
    assert result["recovery_deferred"] is True


@spec_claim(
    SpecClaim(
        "DL-S26", "decision_lifecycle_contract", "§2.3 bounded ranker rotation", "L2b",
        "Paper-trader evaluation reads at most 250 open Opportunities per run and "
        "uses a durable last_ranked_at cursor so repeated runs reach a retryable tail.",
        status="implemented",
    ),
)
def test_bounded_ranker_rotation_reaches_retryable_tail(tmp_path, monkeypatch):
    from tests.test_paper_trader_ranker import (
        test_retryable_prefix_rotates_in_bounded_batches_until_tail_is_reached,
    )

    test_retryable_prefix_rotates_in_bounded_batches_until_tail_is_reached(
        tmp_path, monkeypatch
    )


@spec_claim(
    SpecClaim(
        "DL-S27", "decision_lifecycle_contract", "§2.3 current-run filter proof", "L2b",
        "Queue insertion requires a strict filter_decision_batch for the current evaluation "
        "run; a batch failure rolls back last_ranked_at and cannot borrow historic facts.",
        status="implemented",
    ),
)
def test_current_run_batch_is_required_for_queue_authority(tmp_path, monkeypatch):
    from tests.test_paper_trader_ranker import (
        test_historic_individual_evidence_cannot_replace_current_run_batch,
    )

    test_historic_individual_evidence_cannot_replace_current_run_batch(
        tmp_path, monkeypatch
    )


@spec_claim(
    SpecClaim(
        "DL-S28", "decision_lifecycle_contract", "§2.3 sustained-arrival fairness", "L2b",
        "A bounded paper-trader run reserves capacity for both never-ranked and oldest "
        "retryable Opportunities so sustained new arrivals cannot starve old open work.",
        status="implemented",
    ),
)
def test_sustained_arrivals_preserve_retry_fairness(tmp_path, monkeypatch):
    from tests.test_paper_trader_ranker import (
        test_sustained_new_arrivals_cannot_starve_old_retryable_opportunity,
    )

    test_sustained_new_arrivals_cannot_starve_old_retryable_opportunity(
        tmp_path, monkeypatch
    )


@spec_claim(
    SpecClaim(
        "DL-S29", "decision_lifecycle_contract", "§2.3 rotation query index", "L2b",
        "Fresh and upgraded databases carry matching rotation indexes, and both bounded "
        "workset query plans avoid an opportunities table scan and temporary sort tree.",
        status="implemented",
    ),
)
def test_bounded_rotation_query_plan_is_indexed(tmp_path, monkeypatch):
    from tests.test_paper_trader_ranker import (
        test_open_trade_rotation_queries_use_matching_indexes_at_scale,
    )

    test_open_trade_rotation_queries_use_matching_indexes_at_scale(
        tmp_path, monkeypatch
    )


@spec_claim(
    SpecClaim(
        "DL-S30", "decision_lifecycle_contract", "§2.3 Serenity claim state", "L2b",
        "A new Serenity Proposal claim requires an active pi_evaluate opportunity with an "
        "exact pending carrier; its theme may remain active during partial promotion, while an "
        "already-durable final claim remains recovery authority.",
        status="implemented",
    ),
)
def test_serenity_claim_requires_exact_active_pi_handoff(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import PendingSourceClaimError, claim_decision
    from aletheia.storage.models import (
        PiDecision,
        SerenityOpportunity,
        SerenityTheme,
        TradeProposal,
    )
    from aletheia.storage.repositories import (
        insert_pi_decision,
        insert_serenity_opportunity,
        insert_serenity_theme,
    )
    from aletheia.storage.sqlite import get_db

    cases = (
        ("inactive", "promoted", "dismissed", "pi_evaluate", "pending_source_not_active"),
        ("intent", "promoted", "active", "watch", "pending_source_intent_invalid"),
    )
    failures = []
    for name, theme_status, source_status, action_intent, expected in cases:
        theme = SerenityTheme(
            id=f"serenity-claim-theme-{name}",
            name=f"Serenity claim {name}",
            status=theme_status,
        )
        source = SerenityOpportunity(
            id=f"serenity-claim-source-{name}",
            theme_id=theme.id,
            symbol="SOL/USDT",
            direction="long",
            market_type="spot",
            status=source_status,
            action_intent=action_intent,
        )
        insert_serenity_theme(theme)
        insert_serenity_opportunity(source)
        insert_pi_decision(PiDecision(
            id=f"serenity-claim-pending-{name}",
            action="signal_pending",
            opportunity_id=source.id,
            signal_source="serenity",
            symbol=source.symbol,
            direction=source.direction,
            market_type=source.market_type,
        ))
        result = {
            "action": "trade",
            "opportunity_id": source.id,
            "signal_source": "serenity",
            "symbol": source.symbol,
            "direction": source.direction,
            "market_type": source.market_type,
            "conviction": 90,
            "reasoning": "claim only a completed Serenity PI handoff",
            "proposal_status": "submitting",
        }
        proposal = TradeProposal(
            id=f"serenity-invalid-proposal-{name}",
            opportunity_id=source.id,
            symbol=source.symbol,
            direction=source.direction,
            market_type=source.market_type,
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
            trade_school="serenity",
        )
        try:
            claim_decision(result, proposal=proposal)
        except PendingSourceClaimError as exc:
            if exc.reason != expected:
                failures.append(f"{name}: expected {expected}, got {exc.reason}")
        else:
            failures.append(f"{name}: invalid Serenity handoff authorized Proposal")

    # Existing final ownership is checked before mutable source state. Recovery
    # must remain possible even if the linked research is later dismissed.
    recovery_theme = SerenityTheme(
        id="serenity-existing-final-theme",
        name="existing final recovery",
        status="active",
    )
    recovery_source = SerenityOpportunity(
        id="serenity-existing-final-source",
        theme_id=recovery_theme.id,
        symbol="ETH/USDT",
        direction="long",
        market_type="spot",
        status="dismissed",
        action_intent="watch",
    )
    insert_serenity_theme(recovery_theme)
    insert_serenity_opportunity(recovery_source)
    insert_pi_decision(PiDecision(
        id="serenity-existing-final-pending",
        action="signal_pending",
        opportunity_id=recovery_source.id,
        signal_source="serenity",
        symbol=recovery_source.symbol,
        direction=recovery_source.direction,
        market_type=recovery_source.market_type,
    ))
    insert_pi_decision(PiDecision(
        id="serenity-existing-final-claim",
        action="trade",
        opportunity_id=recovery_source.id,
        signal_source="serenity",
        symbol=recovery_source.symbol,
        direction=recovery_source.direction,
        market_type=recovery_source.market_type,
        proposal_status="submitting",
    ))
    recovered, created = claim_decision(
        {
            "action": "trade",
            "opportunity_id": recovery_source.id,
            "signal_source": "serenity",
            "symbol": recovery_source.symbol,
            "direction": recovery_source.direction,
            "market_type": recovery_source.market_type,
            "conviction": 90,
            "reasoning": "resume existing final ownership",
            "proposal_status": "submitting",
        },
        proposal=TradeProposal(
            id="serenity-existing-final-recovery-proposal",
            opportunity_id=recovery_source.id,
            symbol=recovery_source.symbol,
            direction=recovery_source.direction,
            market_type=recovery_source.market_type,
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
            trade_school="serenity",
        ),
    )
    persisted_invalid = get_db().execute(
        "SELECT id FROM trade_proposals WHERE id LIKE 'serenity-invalid-proposal-%'"
    ).fetchall()
    assert not failures and not persisted_invalid, (
        f"failures={failures!r}; proposals={[row[0] for row in persisted_invalid]!r}"
    )
    assert created is False
    assert recovered["id"] == "serenity-existing-final-claim"


@spec_claim(
    SpecClaim(
        "DL-S31", "decision_lifecycle_contract", "§2.3 unique pending claim", "L2b",
        "The atomic Proposal claim reads every pending row for one source Opportunity and "
        "requires exactly one carrier; a conflict inserted after context binding fails closed.",
        status="implemented",
    ),
)
def test_claim_rejects_pending_conflict_inserted_after_context(
    tmp_path, monkeypatch
):
    import asyncio

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    from aletheia.agent_shell import commands, reflection, trading_decision
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution import gateway
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    save_config(cfg)

    opportunity = Opportunity(
        id="pending-conflict-after-context",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        asset_class="crypto",
        execution_eligible=True,
        status="queued",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="exact-pending-before-context",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))
    insert_market_snapshot(
        MarketSnapshot(
            symbol=opportunity.symbol,
            market_type="spot",
            exchange="binance",
            price=100.0,
        )
    )

    async def choose_exact_pending(_context):
        return {
            "action": "trade",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 90,
            "reasoning": "the context contains one exact pending carrier",
            "bear_case": "another writer can add a conflict before claim",
            "key_assumptions": ["pending identity remains unique"],
            "parameter_rationale": {
                "entry_price": "use the stored snapshot",
                "stop_loss": "bound downside",
                "take_profit": "preserve reward to risk",
                "notional_usdt": "use the bounded paper amount",
                "leverage": "spot has no leverage",
            },
        }

    original_claim = reflection.claim_decision
    conflict_inserted = False

    def insert_conflict_immediately_before_claim(decision_result, *, proposal=None):
        nonlocal conflict_inserted
        if not conflict_inserted:
            conflict_inserted = True
            insert_pi_decision(PiDecision(
                id="conflicting-pending-after-context",
                action="signal_pending",
                opportunity_id=opportunity.id,
                signal_source="market_observer",
                symbol="ETH/USDT",
                direction="short",
                market_type="usdt_futures",
            ))
        return original_claim(decision_result, proposal=proposal)

    def store_without_executing(proposal, **_kwargs):
        return {
            "proposal_id": proposal.id,
            "status": "stored_only",
            "reason": "test blocks execution after persistence",
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", choose_exact_pending)
    monkeypatch.setattr(
        reflection, "claim_decision", insert_conflict_immediately_before_claim
    )
    monkeypatch.setattr(gateway, "_execute_proposal", store_without_executing)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    pending_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0]
    proposal_count = get_db().execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchone()[0]
    final_count = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? "
        "AND action!='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0]
    assert pending_count == 2
    assert proposal_count == 0, result
    assert final_count == 0, result
    assert get_opportunity(opportunity.id)["status"] == "queued"
    assert result["proposal_reason"] == "pending_identity_ambiguous"
    assert result["operator_decision_persisted"] is False


# ---------------------------------------------------------------------------
# Admission slot semantics — a candidate in an active defer window does NOT
# consume an admission slot (audit CANDIDATES_ADMISSION_GAP 2026-07-20).
# ---------------------------------------------------------------------------


def _seed_deferred_signal_pending(
    *,
    opportunity_id: str,
    symbol: str,
    defer_until_iso: str,
    defer_reason: str = "operator_low_conviction",
    event_kind: str = "operator_decision_deferred_low_conviction",
) -> None:
    """Insert one queued Opportunity + its signal_pending carrier + an active
    defer event in a shape identical to production writes."""
    from datetime import datetime, timedelta, timezone
    import json as _json

    from aletheia.storage.models import (
        OperatorEvaluationEvent,
        Opportunity,
        PiDecision,
    )
    from aletheia.storage.repositories import (
        insert_opportunity,
        insert_pi_decision,
        record_operator_evaluation_event,
        update_opportunity,
    )

    now = datetime.now(timezone.utc)
    insert_opportunity(Opportunity(
        id=opportunity_id,
        symbol=symbol,
        market="usdt_futures",
        direction_bias="long",
        status="queued",
        scanner_family="momentum",
    ))
    update_opportunity(
        opportunity_id,
        valid_until=(now + timedelta(hours=4)).isoformat(),
    )
    insert_pi_decision(PiDecision(
        id=f"pending-{opportunity_id}",
        action="signal_pending",
        opportunity_id=opportunity_id,
        signal_source="market_observer",
        symbol=symbol,
        direction="long",
        market_type="usdt_futures",
    ))
    record_operator_evaluation_event(OperatorEvaluationEvent(
        dedup_key=f"dedup-{opportunity_id}",
        event_kind=event_kind,
        trigger="signal",
        pending_unresolved_total=1,
        pending_visible_count=1,
        pending_oldest_created_at=now.isoformat(),
        queue_snapshot_json=_json.dumps({
            "opportunity_id": opportunity_id,
            "defer_reason": defer_reason,
            "defer_until": defer_until_iso,
            "event_kind": event_kind,
        }, sort_keys=True, separators=(",", ":")),
        data_health_json="{}",
        policy_epoch_id="test-epoch",
    ))


@spec_claim(
    SpecClaim(
        "DL-S57", "decision_lifecycle_contract", "§2.3 admission slot semantics", "L2b",
        "A signal_pending carrier whose most recent Operator observation is an "
        "active defer (defer_until > now) does NOT consume an admission slot — "
        "Operator has already judged it and is awaiting the re-evaluation window. "
        "count_unresolved_signal_pending must exclude every such carrier.",
        status="implemented",
    ),
)
def test_admission_slot_freed_by_active_defer(tmp_path, monkeypatch):
    """5 market-quiet defer carriers exist; admission counter reports 0."""
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db
    close_db()
    run_migrations()

    from aletheia.storage.repositories import count_unresolved_signal_pending

    future_defer = (datetime.now(timezone.utc) + timedelta(minutes=90)).isoformat()
    for idx, symbol in enumerate(
        ("BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "MATIC/USDT")
    ):
        _seed_deferred_signal_pending(
            opportunity_id=f"opp-defer-{idx}",
            symbol=symbol,
            defer_until_iso=future_defer,
        )

    # 5 signal_pending physical rows, but all 5 are in an active defer window.
    # Admission accounting must free every slot for higher-ranked new candidates.
    assert count_unresolved_signal_pending() == 0


@spec_claim(
    SpecClaim(
        "DL-S58", "decision_lifecycle_contract", "§2.3 admission slot re-occupation", "L2b",
        "Once defer_until falls into the past, the carrier is once again owed a "
        "fresh Operator judgment and consumes an admission slot again.",
        status="implemented",
    ),
)
def test_admission_slot_reoccupied_after_defer_expires(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db
    close_db()
    run_migrations()

    from aletheia.storage.repositories import count_unresolved_signal_pending

    past_defer = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _seed_deferred_signal_pending(
        opportunity_id="opp-defer-expired",
        symbol="BTC/USDT",
        defer_until_iso=past_defer,
    )

    # defer_until is in the past → carrier once again consumes a slot.
    assert count_unresolved_signal_pending() == 1


@spec_claim(
    SpecClaim(
        "DL-S59", "decision_lifecycle_contract", "§2.3 defer widens carrier lifecycle", "L2b",
        "When _record_deferred_context_observation persists a defer event, the "
        "underlying Opportunity's valid_until is CAS'd forward to at least "
        "defer_until + margin so the promised second Operator look is inside the "
        "Opportunity's lifecycle. Never shortens an existing later deadline.",
        status="implemented",
    ),
)
def test_defer_extends_valid_until_forward_only(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db
    close_db()
    run_migrations()

    from aletheia.agent_shell import commands
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import (
        get_opportunity,
        insert_opportunity,
        insert_pi_decision,
        update_opportunity,
    )

    now = datetime.now(timezone.utc)
    short_valid_until = (now + timedelta(minutes=20)).isoformat()
    insert_opportunity(Opportunity(
        id="opp-widen",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
        scanner_family="momentum",
    ))
    update_opportunity("opp-widen", valid_until=short_valid_until)
    insert_pi_decision(PiDecision(
        id="pending-widen",
        action="signal_pending",
        opportunity_id="opp-widen",
        signal_source="market_observer",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
    ))

    monkeypatch.setattr(
        commands,
        "_deferred_context_dedup_key",
        lambda *_args, **_kwargs: ("fixed-dedup-widen", now.isoformat()),
    )
    commands._record_deferred_context_observation(
        {
            "layer2": {
                "pending_queue": {
                    "unresolved_total": 1,
                    "visible_count": 1,
                    "overflow_count": 0,
                    "oldest_at": now.isoformat(),
                },
                "pending_signals": [
                    {"opportunity_id": "opp-widen", "valid_until": short_valid_until}
                ],
            }
        },
        context_fingerprint="fp-widen",
        opportunity_id="opp-widen",
        policy_epoch_id="test-epoch",
        trigger="signal",
        reason="operator_low_conviction",
        window_minutes=120,
    )

    # valid_until now covers the full defer window + 300s margin.
    expected_min = now + timedelta(minutes=120, seconds=300)
    row = get_opportunity("opp-widen")
    actual = datetime.fromisoformat(row["valid_until"])
    assert actual >= expected_min - timedelta(seconds=1), (
        f"valid_until must extend past defer_deadline + 300s; got {actual!r}"
    )

    # A second defer with a LATER window must not roll valid_until backward
    # if the carrier already has a longer horizon.
    later_now = now + timedelta(minutes=30)
    monkeypatch.setattr(
        commands,
        "_deferred_context_dedup_key",
        lambda *_args, **_kwargs: ("fixed-dedup-widen-2", later_now.isoformat()),
    )
    commands._record_deferred_context_observation(
        {
            "layer2": {
                "pending_queue": {
                    "unresolved_total": 1,
                    "visible_count": 1,
                    "overflow_count": 0,
                    "oldest_at": later_now.isoformat(),
                },
                "pending_signals": [
                    {"opportunity_id": "opp-widen", "valid_until": short_valid_until}
                ],
            }
        },
        context_fingerprint="fp-widen-2",
        opportunity_id="opp-widen",
        policy_epoch_id="test-epoch",
        trigger="signal",
        reason="operator_low_conviction",
        window_minutes=120,
    )
    # New defer_deadline is later_now + 120min, so valid_until slides further forward.
    second_expected_min = later_now + timedelta(minutes=120, seconds=300)
    second_row = get_opportunity("opp-widen")
    assert datetime.fromisoformat(second_row["valid_until"]) >= second_expected_min - timedelta(seconds=1)


@spec_claim(
    SpecClaim(
        "DL-S60", "decision_lifecycle_contract", "§2.3 cooldown scoped by producer", "L2b",
        "The 30-minute signal cooldown is scoped to one signal_source namespace. "
        "A Serenity signal_pending row does not pollute the market_observer "
        "cooldown for the same (symbol, direction).",
        status="implemented",
    ),
)
def test_signal_cooldown_scoped_by_signal_source(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db, get_db
    close_db()
    run_migrations()

    now = datetime.now(timezone.utc).isoformat()
    # A Serenity signal on (BTC/USDT, long) landed recently.
    get_db().execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, opportunity_id, "
        " signal_source, created_at) "
        "VALUES ('pi-ser-1', 'BTC/USDT', 'long', 'spot', 'signal_pending', "
        "        'opp-ser-1', 'serenity', ?)",
        (now,),
    )
    get_db().commit()

    from aletheia.agents.paper_trader import _recent_signal_pairs

    # Default caller (market_observer) must NOT see Serenity's pair.
    assert _recent_signal_pairs() == set()
    # Serenity's own producer view still sees it.
    assert _recent_signal_pairs(signal_source="serenity") == {("BTC/USDT", "long")}


@spec_claim(
    SpecClaim(
        "DL-S61", "decision_lifecycle_contract", "§2.3 evaluated set bounded", "L2b",
        "operator_already_evaluated block is bounded to a 24-hour lookback so the "
        "stealth-block set cannot grow without bound as the DB ages.",
        status="implemented",
    ),
)
def test_operator_already_evaluated_bounded_by_24h(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db, get_db
    close_db()
    run_migrations()

    now = datetime.now(timezone.utc)
    long_ago = (now - timedelta(hours=48)).isoformat()
    recent = (now - timedelta(hours=1)).isoformat()

    db = get_db()
    # A final decision older than the 24h window — must NOT block new opportunities.
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, opportunity_id, "
        " signal_source, created_at) "
        "VALUES ('pi-old', 'BTC/USDT', 'long', 'spot', 'trade', "
        "        'opp-old-evaluated', 'market_observer', ?)",
        (long_ago,),
    )
    # A recent final decision — must still block.
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, opportunity_id, "
        " signal_source, created_at) "
        "VALUES ('pi-new', 'ETH/USDT', 'long', 'spot', 'skip', "
        "        'opp-new-evaluated', 'market_observer', ?)",
        (recent,),
    )
    db.commit()

    from aletheia.agents.paper_trader import _operator_evaluated_opportunity_ids

    ids = _operator_evaluated_opportunity_ids()
    assert "opp-new-evaluated" in ids
    assert "opp-old-evaluated" not in ids


@spec_claim(
    SpecClaim(
        "DL-S62", "decision_lifecycle_contract", "§2.3 defer ttl guard", "L2b",
        "When defer_deadline exceeds an opportunity's valid_until and the forward-only "
        "extend is a no-op (opportunity already terminal), a defer_exceeds_opportunity_ttl "
        "warning is emitted and a FilterDecision is appended for auditability.",
        status="implemented",
    ),
)
def test_defer_ttl_guard_emits_warning_when_extend_is_noop(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db, get_db
    close_db()
    run_migrations()

    now = datetime.now(timezone.utc)
    # Opportunity that expired 30 minutes ago — valid_until is in the past.
    expired_valid_until = (now - timedelta(minutes=30)).isoformat()

    db = get_db()
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, direction_bias, status, asset_class, valid_until) "
        "VALUES ('opp-expired-defer', 'BTC/USDT', 'spot', 'long', 'expired', 'crypto', ?)",
        (expired_valid_until,),
    )
    db.commit()

    # Seed the minimal context _record_deferred_context_observation needs.
    ctx = {
        "layer2": {
            "pending_queue": {"unresolved_total": 1, "visible_count": 1, "overflow_count": 0, "oldest_at": None},
            "pending_signals": [
                {"opportunity_id": "opp-expired-defer", "symbol": "BTC/USDT",
                 "direction": "long", "market_type": "spot"},
            ],
        },
    }

    import logging
    warnings: list[dict] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record):
            if record.getMessage() == "defer_exceeds_opportunity_ttl":
                warnings.append(vars(record))

    handler = _CapturingHandler()
    logging.getLogger("aletheia.agent_shell.commands").addHandler(handler)
    try:
        from aletheia.agent_shell.commands import _record_deferred_context_observation

        _record_deferred_context_observation(
            ctx,
            context_fingerprint="fp-test",
            opportunity_id="opp-expired-defer",
            policy_epoch_id="epoch-test",
            trigger="test",
            reason="unchanged_context",
            window_minutes=120,
        )
    finally:
        logging.getLogger("aletheia.agent_shell.commands").removeHandler(handler)

    assert len(warnings) == 1, "Expected one defer_exceeds_opportunity_ttl warning"

    rows = db.execute(
        "SELECT filter_name, reason_code FROM filter_decisions "
        "WHERE opportunity_id = ? AND reason_code = 'defer_exceeds_opportunity_ttl'",
        ("opp-expired-defer",),
    ).fetchall()
    assert len(rows) == 1, "Expected one filter_decision with reason_code=defer_exceeds_opportunity_ttl"
    assert rows[0]["filter_name"] == "defer_ttl_guard"
