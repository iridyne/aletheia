"""Spec: Decision Lifecycle Contract (decision_lifecycle_contract.md)

The cross-cutting spine opportunity → PiDecision(signal_pending) → TradingDecision →
TradeProposal. This contract governs the *seams* between subsystems (the carriers and
their field semantics) that the per-subsystem contracts do not cover.

Contract: decision_lifecycle_contract
Claim prefix: DL
"""

import dataclasses
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


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
        "A watch/skip System Operator result persists the symbol and direction it evaluated, "
        "alongside opportunity_id and signal_source, instead of creating an anonymous audit row.",
        status="implemented",
    ),
)
def test_skip_decision_persists_evaluated_symbol_and_direction(tmp_path, monkeypatch):
    import asyncio
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import commands, reflection
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity
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

    decision = TradingDecision(
        action="skip",
        symbol="BTC/USDT",
        direction="long",
        conviction=48,
        reasoning="candidate evaluated but not selected",
        market_type="usdt_futures",
        opportunity_id="opp-skip-identity",
        signal_source="market_observer",
        scanner_family="momentum",
    )

    async def fake_evaluate(_context):
        return decision

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda: {
            "layer1": {
                "data_health": {"signal_status": "healthy", "ohlcv_ready": True}
            }
        },
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision.evaluate_trading_opportunity",
        fake_evaluate,
    )
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    row = get_db().execute(
        "SELECT symbol, direction, market_type, opportunity_id, signal_source "
        "FROM pi_decisions "
        "WHERE action='skip' AND opportunity_id=?",
        (decision.opportunity_id,),
    ).fetchone()
    opportunity_status = get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (decision.opportunity_id,),
    ).fetchone()[0]
    assert result["symbol"] == "BTC/USDT"
    assert result["direction"] == "long"
    assert row is not None
    assert dict(row) == {
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "usdt_futures",
        "opportunity_id": "opp-skip-identity",
        "signal_source": "market_observer",
    }
    assert opportunity_status == "reviewed"


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
        lambda: {
            "layer1": {
                "data_health": {"signal_status": "healthy", "ohlcv_ready": True}
            },
            "layer2": {"pending_queue": {"unresolved_total": 0}},
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
        "DL-S13", "decision_lifecycle_contract", "§2.4 durable submission result", "L2b",
        "A proposal submission exception still persists the final trade intent with a sanitized "
        "failed status and stable submission_exception reason.",
        status="implemented",
    ),
)
def test_submission_exception_still_persists_final_decision(tmp_path, monkeypatch):
    import asyncio
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import commands, reflection
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision
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
        lambda: {
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

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    row = get_db().execute(
        "SELECT action, proposal_id, proposal_status, proposal_reason, reasoning "
        "FROM pi_decisions WHERE opportunity_id=? AND action='trade'",
        (decision.opportunity_id,),
    ).fetchone()
    assert row is not None
    assert row["proposal_id"] is None
    assert row["proposal_status"] == "failed"
    assert row["proposal_reason"] == "submission_exception"
    assert "sensitive provider detail" not in (row["reasoning"] or "")
    assert result["proposal_status"] == "failed"
    assert result["proposal_reason"] == "submission_exception"


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
        lambda: {
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
        lambda: {"layer1": {"data_health": {"signal_status": "healthy", "ohlcv_ready": True}}},
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
        "When assessment is unavailable, the fallback skip consumes the oldest queued signal "
        "with its exact identity instead of writing an anonymous skip and retrying forever.",
        status="implemented",
    ),
)
def test_llm_unavailable_skip_consumes_oldest_pending_signal(tmp_path, monkeypatch):
    import asyncio
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import commands, reflection
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
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

    async def unavailable(_context):
        return None

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda: {
            "layer1": {"data_health": {"signal_status": "healthy", "ohlcv_ready": True}},
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

    result = asyncio.run(commands.evaluate_trading_opportunity({}))
    final = get_db().execute(
        "SELECT symbol, direction, market_type, opportunity_id FROM pi_decisions "
        "WHERE action='skip' AND opportunity_id=?",
        (opportunity.id,),
    ).fetchone()
    status = get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,),
    ).fetchone()[0]

    assert result["action"] == "skip"
    assert result["pending_queue_remaining"] == 0
    assert dict(final) == {
        "symbol": "ETH/USDT", "direction": "short",
        "market_type": "usdt_futures", "opportunity_id": opportunity.id,
    }
    assert status == "reviewed"


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
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

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
        "DL-S18", "decision_lifecycle_contract", "§2.3 pending-only operator input", "L2b",
        "A heartbeat with zero unresolved signal_pending rows cannot turn an open/ranker "
        "Opportunity into a TradeProposal; paper_trader must create the durable queue carrier first.",
        status="implemented",
    ),
)
def test_heartbeat_cannot_submit_unqueued_open_or_ranker_opportunity(tmp_path, monkeypatch):
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
    from aletheia.agent_shell import reflection, trading_decision
    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution import gateway
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
        MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=100.0)
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

    async def choose_unqueued_candidate(_context):
        return {
            "action": "trade",
            "symbol": "BTC/USDT",
            "direction": "long",
            "market_type": "spot",
            "conviction": 90,
            "reasoning": "select the visible open ranker candidate",
            "bear_case": "the breakout can fail",
            "key_assumptions": ["the stored snapshot remains representative"],
            "parameter_rationale": {
                "entry_price": "the current snapshot anchors the entry",
                "stop_loss": "the stop invalidates the setup",
                "take_profit": "the target preserves positive reward to risk",
                "notional_usdt": "the notional is intentionally bounded",
                "leverage": "spot uses no leverage",
            },
        }

    def store_without_executing(proposal, **_kwargs):
        return {
            "proposal_id": proposal.id,
            "status": "stored_only",
            "reason": "test_blocks_execution_after_persistence",
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", choose_unqueued_candidate)
    monkeypatch.setattr(gateway, "_execute_proposal", store_without_executing)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "pi_reflection.json")

    asyncio.run(pi_daemon._run_evaluation("heartbeat"))

    db = get_db()
    pending_count = db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? AND action='signal_pending'",
        (opportunity.id,),
    ).fetchone()[0]
    proposals = db.execute(
        "SELECT id, opportunity_id FROM trade_proposals WHERE opportunity_id=?",
        (opportunity.id,),
    ).fetchall()
    anonymous_finals = db.execute(
        "SELECT id FROM pi_decisions WHERE action!='signal_pending' "
        "AND opportunity_id IS NULL"
    ).fetchall()
    opportunity_status = db.execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,)
    ).fetchone()[0]
    assert pending_count == 0
    assert not proposals, (
        "heartbeat formed a TradeProposal from an open/ranker Opportunity with no "
        f"signal_pending carrier: {[dict(row) for row in proposals]!r}"
    )
    assert not anonymous_finals, (
        "a trade-like choice without a durable carrier persisted an anonymous final: "
        f"{[dict(row) for row in anonymous_finals]!r}"
    )
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
        "DL-S22", "decision_lifecycle_contract", "§2.1 open-position authority", "L2b",
        "Layer 1 keeps a complete open-position authority set for close/reduce identity "
        "matching while the prompt honestly reports its bounded visible slice.",
        status="implemented",
    ),
)
def test_old_position_beyond_layer1_display_still_attaches_close_identity(
    tmp_path, monkeypatch
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import _build_layer1_snapshot
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        _attach_opportunity_context,
        _build_assessment_prompt,
    )
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

    decision = TradingDecision(
        action="close",
        symbol=target.symbol,
        direction=target.direction,
        market_type=None,
        conviction=90,
        reasoning="close the old position",
    )
    ctx = {"layer1": layer1, "layer2": {}, "layer3": {}}
    _attach_opportunity_context(decision, ctx)

    assert decision.opportunity_id == target.opportunity_id
    assert decision.market_type == target.market_type
    prompt = _build_assessment_prompt(ctx)
    assert "Open positions: 11 total; showing 5; 6 not shown" in prompt


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
    insert_market_snapshot(
        MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=100.0)
    )
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
    from aletheia.storage.repositories import insert_pi_decision, update_pi_decision

    opportunity_id = "opp-stale-submitting-recovery"
    insert_pi_decision(PiDecision(
        id="stale-pending-before-claim",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        action="signal_pending",
        opportunity_id=opportunity_id,
        signal_source="market_observer",
        created_at=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
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

    visible, queue = _build_pending_signal_queue(max_age_minutes=30)
    assert [row["opportunity_id"] for row in visible] == [opportunity_id]
    assert queue["expired_unconsumed_rows"] == 0

    update_pi_decision("unfinished-final-claim", proposal_status="executed")
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
        "prepare_decision_as_proposal resolves the final market_type before calling "
        "latest_snapshot and passes that market_type into the price lookup.",
    ),
)
def test_proposal_price_lookup_is_market_type_scoped():
    from aletheia.agent_shell import trading_decision

    src = Path(trading_decision.__file__).read_text(encoding="utf-8")
    start = src.index("async def prepare_decision_as_proposal")
    end = src.index("\ndef _proposal_direction_overheated", start)
    body = src[start:end]

    resolution = body.index("market_type = decision.market_type")
    lookup = body.index("latest_snapshot(decision.symbol, market_type=market_type)")
    assert resolution < lookup


@spec_claim(
    SpecClaim(
        "DL-C02", "decision_lifecycle_contract", "§4 DL-INV-9 proposal outcome", "L2c",
        "A trade-like System Operator intent that does not create a proposal must retain a "
        "structured proposal_submission rejection reason; it must not collapse into an "
        "unexplained NULL proposal_id.",
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
    assert '"status": "failed"' in body
    assert '"reason": "market_price_unavailable"' in body
