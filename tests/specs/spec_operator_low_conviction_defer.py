"""Spec: low-conviction / debate-not-confirmed outcomes are non-terminal defers.

Phase C (2026-07-18) closes the indirect judge-word bypass identified after
commit 11d435cd:

* Direct-skip authority is already deterministic (DL-INV-21).
* But the same weak reasoning — SIDEWAYS regime, tiny-sample recent losses,
  low account equity, missing optional telemetry, Ranker rank ordering —
  was allowed to *depress* conviction just enough for the debate channel or
  the initial-conviction gate to consume the pending signal as `skip`.

This spec locks the new non-terminal defer behavior. Reference the two new
Spec-as-Code lines (see decision_lifecycle_contract.md):

* ``DL-S47``  initial conviction < 55 without a debate trigger →
              ``DecisionUnavailable("operator_low_conviction",
                                    stage="operator_conviction_gate")``.
              No final PiDecision written, opportunity remains queued.
* ``DL-S48``  post-debate conviction < 55 →
              ``DecisionUnavailable("debate_not_confirmed",
                                    stage="operator_debate_gate")``.
              Same guarantees.

Anchoring case: TRUMP/USDT on 2026-07-18 — model returned trade with
initial=52, debate bull=42/bear=72, adjusted conviction=43. Before Phase C
this consumed the pending signal as a terminal skip; after Phase C it
becomes an ``evaluation_deferred`` with a persisted event.
"""

import asyncio
import dataclasses
from datetime import datetime, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _bypass_candidate_technical_preflight(monkeypatch) -> None:
    monkeypatch.setattr(
        "aletheia.agent_shell.decision_facts.candidate_context_unavailable_reason",
        lambda *_args, **_kwargs: None,
    )


def _bypass_b2_evidence(monkeypatch) -> None:
    from aletheia.agent_shell import b2_evidence_policy

    def _noop(ctx, *, policy_epoch_id):
        return

    monkeypatch.setattr(
        b2_evidence_policy, "attach_b2_candidate_evidence", _noop
    )


def _seed_pending_carrier(symbol: str, opportunity_id: str) -> None:
    from aletheia.storage.models import (
        MarketSnapshot,
        Opportunity,
        PiDecision,
    )
    from aletheia.storage.repositories import (
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
    )

    insert_opportunity(
        Opportunity(
            id=opportunity_id,
            symbol=symbol,
            market="usdt_futures",
            direction_bias="long",
            status="queued",
            scanner_family="momentum",
        )
    )
    insert_pi_decision(
        PiDecision(
            id=f"pending-{opportunity_id}",
            action="signal_pending",
            opportunity_id=opportunity_id,
            signal_source="market_observer",
            symbol=symbol,
            direction="long",
            market_type="usdt_futures",
        )
    )
    insert_market_snapshot(
        MarketSnapshot(
            symbol=symbol,
            market_type="perp",
            exchange="test",
            price=1.0,
        )
    )


def _wire_operator_stubs(
    monkeypatch,
    *,
    model_action: str,
    conviction: int,
    symbol: str,
    direction: str,
    reasoning: str = "Uncertain but tradeable setup",
    debate: "tuple[int, int] | None" = None,
) -> None:
    """Force the Operator LLM + debate to deterministic outputs for the test."""
    from aletheia.agent_shell import commands, reflection, trading_decision

    async def _fake_assess(_ctx):
        return {
            "action": model_action,
            "symbol": symbol,
            "direction": direction,
            "conviction": conviction,
            "reasoning": reasoning,
            "market_type": "usdt_futures",
            "leverage": 1,
        }

    monkeypatch.setattr(trading_decision, "_assess_opportunity", _fake_assess)

    if debate is not None:
        bull, bear = debate

        async def _fake_debate(_symbol, _direction, _ctx):
            return trading_decision.DebateResult(
                bull_confidence=bull,
                bull_thesis="bull",
                bull_evidence=[],
                bear_confidence=bear,
                bear_thesis="bear",
                bear_evidence=[],
            )

        monkeypatch.setattr(trading_decision, "_run_debate", _fake_debate)

    monkeypatch.setattr(
        commands, "_configured_paper_execution_mode", lambda: "paper_internal"
    )
    monkeypatch.setattr(
        reflection, "_REFLECTION_FILE",
        __import__("pathlib").Path("/tmp") / f"pi_reflection_{symbol.replace('/', '_')}.json",
    )


@spec_claim(
    SpecClaim(
        "DL-S48", "decision_lifecycle_contract", "§4 DL-INV-2 non-terminal low conviction after debate",
        "L2b",
        "A trade candidate whose conviction lands below the min after the bull/bear debate "
        "raises DecisionUnavailable(reason='debate_not_confirmed', stage='operator_debate_gate') — "
        "no final PiDecision, no proposal, opportunity remains queued, and an "
        "operator_decision_deferred_low_conviction event is persisted.",
        status="implemented",
    ),
)
def test_trump_case_debate_below_min_defers_instead_of_terminal_skip(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    _bypass_b2_evidence(monkeypatch)
    from aletheia.agent_shell import commands
    from aletheia.storage.sqlite import get_db

    opp_id = "opp-trump-debate"
    _seed_pending_carrier("TRUMP/USDT", opp_id)
    _wire_operator_stubs(
        monkeypatch,
        model_action="trade",
        conviction=52,  # matches TRUMP anchor
        symbol="TRUMP/USDT",
        direction="long",
        debate=(42, 72),  # anchor: adjusted = 52 + (42-72)*0.3 = 43 < 55
    )

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    assert result["action"] == "evaluation_deferred"
    assert result["observation_status"] == "debate_not_confirmed"
    assert result["defer_reason"] == "debate_not_confirmed"
    assert result["defer_stage"] == "operator_debate_gate"
    assert result["operator_evaluation_event_id"]

    # No final PiDecision was created.
    final_rows = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions "
        "WHERE opportunity_id=? AND action IN ('trade','skip','flip','scale_in')",
        (opp_id,),
    ).fetchone()[0]
    assert final_rows == 0

    # Opportunity is not consumed.
    status = get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,),
    ).fetchone()[0]
    assert status == "queued"

    # No trade_proposals appear.
    prop_count = get_db().execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE opportunity_id=?",
        (opp_id,),
    ).fetchone()[0]
    assert prop_count == 0

    # The defer event is persisted with the new event_kind.
    ev = get_db().execute(
        "SELECT event_kind FROM operator_evaluation_events "
        "WHERE id=?",
        (result["operator_evaluation_event_id"],),
    ).fetchone()
    assert ev is not None
    assert ev["event_kind"] == "operator_decision_deferred_low_conviction"


@spec_claim(
    SpecClaim(
        "DL-S47", "decision_lifecycle_contract", "§4 DL-INV-2 non-terminal low initial conviction",
        "L2b",
        "A trade action whose initial conviction is < 55 without falling in the debate range "
        "raises DecisionUnavailable(reason='operator_low_conviction', stage='operator_conviction_gate') — "
        "no final PiDecision, no proposal, opportunity remains queued.",
        status="implemented",
    ),
)
def test_low_initial_conviction_without_debate_defers(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    _bypass_b2_evidence(monkeypatch)
    from aletheia.agent_shell import commands
    from aletheia.storage.sqlite import get_db

    opp_id = "opp-lowconv"
    _seed_pending_carrier("SOL/USDT", opp_id)
    # conviction=35 < _DEBATE_RANGE[0]=40, so debate does not fire.
    _wire_operator_stubs(
        monkeypatch,
        model_action="trade",
        conviction=35,
        symbol="SOL/USDT",
        direction="long",
    )

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    assert result["action"] == "evaluation_deferred"
    assert result["defer_reason"] == "operator_low_conviction"
    assert result["defer_stage"] == "operator_conviction_gate"

    status = get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,),
    ).fetchone()[0]
    assert status == "queued"

    final_rows = get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions "
        "WHERE opportunity_id=? AND action IN ('trade','skip','flip','scale_in')",
        (opp_id,),
    ).fetchone()[0]
    assert final_rows == 0


@spec_claim(
    SpecClaim(
        "DL-S50", "decision_lifecycle_contract",
        "§4 DL-INV-24 conviction bypass reasons never become terminal skip",
        "L2b",
        "Even when the model bakes SIDEWAYS / equity / one-losing-trade reasoning into a "
        "sub-threshold conviction, the outcome is a non-terminal defer with a diagnostic "
        "reason code — not a direct adverse skip masquerade.",
        status="implemented",
    ),
)
def test_bypass_reasoning_does_not_dress_up_as_direct_adverse_skip(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _bypass_candidate_technical_preflight(monkeypatch)
    _bypass_b2_evidence(monkeypatch)
    from aletheia.agent_shell import commands
    from aletheia.storage.sqlite import get_db

    opp_id = "opp-bypass"
    _seed_pending_carrier("BCH/USDT", opp_id)
    tainted_reasoning = (
        "SIDEWAYS 50/50 regime; recent 7-day performance is 0% WR on one losing trade; "
        "account equity ~$398 USDT is small. Still tradeable long structure."
    )
    # Use conviction 39 to bypass the debate range and go through the
    # conviction-gate path directly (this test targets the bypass-reason ban,
    # not the debate channel).
    _wire_operator_stubs(
        monkeypatch,
        model_action="trade",
        conviction=39,
        symbol="BCH/USDT",
        direction="long",
        reasoning=tainted_reasoning,
    )

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    # Not consumed as a terminal skip …
    status = get_db().execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,),
    ).fetchone()[0]
    assert status == "queued"
    # … the outcome is a diagnostic defer, not an adverse_candidate_evidence.
    assert result["action"] == "evaluation_deferred"
    assert result["defer_reason"] in {
        "operator_low_conviction",
        "debate_not_confirmed",
    }


@spec_claim(
    SpecClaim(
        "DL-S49", "decision_lifecycle_contract",
        "§4 low-conviction defer event kind",
        "L2b",
        "Non-terminal low-conviction defer events use event_kind="
        "'operator_decision_deferred_low_conviction' so diagnostics can distinguish "
        "them from unchanged-context defers, and the dedup key is scoped by event_kind.",
        status="implemented",
    ),
)
def test_low_conviction_event_kind_has_distinct_dedup(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import _deferred_context_dedup_key

    baseline, _ = _deferred_context_dedup_key(
        "fp",
        opportunity_id="opp",
        policy_epoch_id="epoch",
        window_minutes=120,
    )
    low_conv, _ = _deferred_context_dedup_key(
        "fp",
        opportunity_id="opp",
        policy_epoch_id="epoch",
        window_minutes=120,
        event_kind="operator_decision_deferred_low_conviction",
    )
    assert baseline != low_conv, (
        "dedup key must vary with event_kind so INSERT OR IGNORE cannot silently drop "
        "a low-conviction defer that shares a fingerprint with an unchanged-context one"
    )


# ---------------------------------------------------------------------------
# DL-S65 — /operator-decisions API surfaces conviction gate deferrals
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-S65", "decision_lifecycle_contract",
        "§4 conviction gate defer visible in operator-decisions API",
        "L2b",
        "GET /analytics/operator-decisions must return conviction_gate_defer items "
        "when the LLM said 'trade' (operator_action_assessment=passed) but conviction "
        "was below the minimum (operator_conviction_gate=rejected). Fixes #327: these "
        "46 evaluations were invisible — no pi_decisions row is written (DL-S47 "
        "non-terminal), but filter_decisions records both gate outcomes. The API now "
        "joins these to surface conviction_gate_defer as a queryable outcome.",
        status="implemented",
    ),
)
def test_conviction_gate_defer_is_visible_in_operator_decisions_api():
    """DL-S65: conviction gate deferrals appear in GET /analytics/operator-decisions.

    Calls the route directly with a mocked DB that has paired filter_decision rows:
    operator_action_assessment=passed + operator_conviction_gate=rejected, same
    candidate_key and evaluated_at (one evaluation batch). No pi_decisions row
    is present (consistent with DL-S47 non-terminal defer).
    """
    import json
    import sqlite3
    from datetime import datetime, timezone, timedelta
    from unittest.mock import patch
    import aletheia.storage.sqlite as sqlite_mod

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # filter_decisions: the conviction gate evaluation batch
    conn.execute(
        "CREATE TABLE filter_decisions ("
        "  id TEXT PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE, "
        "  candidate_key TEXT NOT NULL, candidate_kind TEXT NOT NULL, "
        "  parent_candidate_key TEXT, run_id TEXT, opportunity_id TEXT, "
        "  universe_snapshot_id TEXT, universe_member_id TEXT, proposal_id TEXT, "
        "  venue TEXT, symbol TEXT NOT NULL, market_type TEXT NOT NULL DEFAULT 'spot', "
        "  direction TEXT, scanner_family TEXT, regime_tag TEXT, "
        "  filter_name TEXT NOT NULL, stage TEXT NOT NULL, "
        "  sequence_no INTEGER NOT NULL DEFAULT 0, decision TEXT NOT NULL, "
        "  reason_code TEXT NOT NULL, reason_detail TEXT, "
        "  input_snapshot TEXT NOT NULL DEFAULT '{}', "
        "  parameter_snapshot TEXT NOT NULL DEFAULT '{}', "
        "  filter_version TEXT NOT NULL, code_commit TEXT, "
        "  outcome_candidate_key TEXT, evaluated_at TEXT NOT NULL)"
    )
    evaluated_at = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()
    opp_id = "opp-conviction-gate-test"
    candidate_key = f"opportunity:{opp_id}"
    _COLS = (
        "id, dedup_key, candidate_key, candidate_kind, opportunity_id, "
        "symbol, market_type, direction, filter_name, stage, sequence_no, "
        "decision, reason_code, input_snapshot, parameter_snapshot, "
        "filter_version, evaluated_at"
    )
    _PH = ",".join(["?"] * 17)
    # Row 1: action assessment passed (LLM said trade)
    conn.execute(
        f"INSERT INTO filter_decisions ({_COLS}) VALUES ({_PH})",
        ("fd-aa", "dedup-aa", candidate_key, "opportunity", opp_id,
         "SOL/USDT", "usdt_futures", "long",
         "operator_action_assessment", "system_operator", 1,
         "passed", "operator_model_trade", "{}", "{}", "v1", evaluated_at),
    )
    # Row 2: conviction gate rejected (conviction was 35, below min 55)
    input_json = json.dumps({"conviction": 35})
    param_json = json.dumps({"minimum_conviction": 55})
    conn.execute(
        f"INSERT INTO filter_decisions ({_COLS}) VALUES ({_PH})",
        ("fd-cg", "dedup-cg", candidate_key, "opportunity", opp_id,
         "SOL/USDT", "usdt_futures", "long",
         "operator_conviction_gate", "system_operator", 3,
         "rejected", "operator_low_conviction", input_json, param_json,
         "v1", evaluated_at),
    )
    conn.commit()

    # Stub tables required by the route (empty — no terminal decisions)
    conn.execute(
        "CREATE TABLE pi_decisions ("
        "  id TEXT PRIMARY KEY, created_at TEXT, symbol TEXT, direction TEXT, "
        "  market_type TEXT, action TEXT, conviction INTEGER, reasoning TEXT, "
        "  opportunity_id TEXT, proposal_id TEXT, evidence_manifest_json TEXT, "
        "  runtime_session_id TEXT, producer_commit TEXT, signal_source TEXT)"
    )
    conn.execute(
        "CREATE TABLE trade_proposals ("
        "  id TEXT PRIMARY KEY, opportunity_id TEXT, created_at TEXT, "
        "  symbol TEXT, direction TEXT, market_type TEXT, status TEXT, "
        "  runtime_session_id TEXT, producer_commit TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_events ("
        "  rowid INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, "
        "  event_type TEXT, actor TEXT, source TEXT, message TEXT, timestamp TEXT)"
    )
    conn.execute(
        "CREATE TABLE runtime_sessions ("
        "  session_id TEXT PRIMARY KEY, boot_commit TEXT, runtime_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE learning_policy_epochs ("
        "  epoch_id TEXT, epoch_kind TEXT, started_at TEXT, ended_at TEXT)"
    )
    conn.commit()

    with patch.object(sqlite_mod, "get_db", return_value=conn):
        from aletheia.server.routes.analytics import get_operator_decisions
        result = get_operator_decisions(hours=24)

    counts = result.get("counts", {})
    assert counts.get("conviction_gate_defer", 0) == 1, (
        f"Expected counts.conviction_gate_defer=1, got {counts} — "
        "conviction gate deferrals must be visible as a distinct outcome "
        "(fixes #327: 46 evaluations were invisible to the operator-decisions API)"
    )

    items = result.get("items", [])
    matching = [it for it in items if it.get("outcome") == "conviction_gate_defer"]
    assert len(matching) == 1, (
        f"Expected 1 conviction_gate_defer item, got {len(matching)} in {items}"
    )
    item = matching[0]
    assert item["opportunity_id"] == opp_id, (
        f"Expected opportunity_id={opp_id!r}, got {item['opportunity_id']!r}"
    )
    assert item["symbol"] == "SOL/USDT", (
        f"Expected symbol='SOL/USDT', got {item['symbol']!r}"
    )
    assert item["conviction"] == 35, (
        f"Expected conviction=35 extracted from input_snapshot, got {item['conviction']!r}"
    )
    from aletheia.server.routes.analytics import _VALID_OUTCOMES
    assert "conviction_gate_defer" in _VALID_OUTCOMES, (
        "'conviction_gate_defer' must be in _VALID_OUTCOMES so the outcome= filter works"
    )
