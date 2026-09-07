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

from datetime import datetime, timezone


from tests.specs.spec_meta import SpecClaim, spec_claim




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












# ---------------------------------------------------------------------------
# DL-S65 — /operator-decisions keeps conviction gate deferrals non-terminal
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-S65", "decision_lifecycle_contract",
        "§4 conviction gate defer remains lifecycle evidence, not a ledger outcome",
        "L2b",
        "GET /analytics/operator-decisions must exclude conviction_gate_defer "
        "when the LLM said 'trade' (operator_action_assessment=passed) but conviction "
        "was below the minimum (operator_conviction_gate=rejected). These evaluations "
        "remain in filter_decisions as non-terminal evidence and do not create a "
        "Candidate Ledger row.",
        status="implemented",
    ),
)
def test_conviction_gate_defer_is_excluded_from_terminal_operator_decisions_api():
    """DL-S65: conviction gate deferrals stay out of the terminal projection.

    Calls the route directly with a mocked DB that has paired filter_decision rows:
    operator_action_assessment=passed + operator_conviction_gate=rejected, same
    candidate_key and evaluated_at (one evaluation batch). No pi_decisions row
    is present (consistent with DL-S47 non-terminal defer).
    """
    import json
    import sqlite3
    from datetime import timedelta
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
    assert counts == {
        "trade": 0,
        "operator_skip": 0,
        "operator_watch": 0,
        "portfolio_guard_reject": 0,
        "user_reject": 0,
        "auto_reject": 0,
        "reject_unknown": 0,
    }
    assert result.get("total_count") == 0
    assert result.get("items") == []
    assert conn.execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE opportunity_id=?", (opp_id,)
    ).fetchone()[0] == 2
