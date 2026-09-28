"""Batch A — Universe-origin lifecycle regression tests.

These tests build a legal Universe-only chain (opportunity_id=NULL) and prove
that the current Opportunity-first consumers fail where they shouldn't:

  - position_replay → discovery=unavailable, pi_decision_not_recorded
  - position_lifecycle_story → opportunity=None, no universe_member
  - family_scorecard → scanner_family='unknown'

After Batch C migrates the consumers to the shared lineage_reader, these
tests are updated to assert the corrected behaviour (discovery available,
family resolved).

Fixture helpers live in _build_universe_lifecycle_chain(), which is the
canonical Universe-only chain builder: a single entry leg with one Fill
and a closed Position.  No Opportunity row, no pi_decisions row.

The fixture reuses _persist_waiting_plan from test_entry_plan_execution.py
to get a structurally valid EntryPlan (including plan_json that satisfies
_ATTRIBUTION_SQL) without duplicating the complex plan construction logic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------

def _build_universe_lifecycle_chain(
    *,
    candidate_family: str = "mean_reversion",
    scanner_family: str = "mean_reversion",
    position_id: str = "uni-position-1",
) -> dict[str, Any]:
    """Insert a complete Universe-origin trade chain and return key IDs.

    Schema:
        UniverseSnapshot → UniverseMember (candidate)
        → candidate_planning_generations (generation, entry_plan_id set)
        → entry_plans (plan_json source_kind='universe')
        → TradeProposal (opportunity_id=NULL, entry_plan_id set)
        → authoritative RiskDecision
        → Order (entry_plan_id set, position_id set)
        → Fill
        → Position (opportunity_id=NULL, proposal_id, order_id set)
        → close Order + close Fill (position closed)

    Returns a dict with all inserted IDs so assertions can be made.

    Builds the entry plan via _persist_waiting_plan from test_entry_plan_execution
    to reuse the correct plan_json construction (satisfies _ATTRIBUTION_SQL
    Universe branch including plan_json.$.source_kind='universe',
    $.source_id, $.candidate_id, $.planning_generation_id).
    """
    # Import _persist_waiting_plan to reuse its correct plan construction.
    # It inserts candidate, snapshot, generation, and entry_plan in one call.
    from tests.test_entry_plan_execution import _persist_waiting_plan
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position, RiskDecision, TradeProposal
    from tests.uta.test_execution_coordinator import _identity

    # _persist_waiting_plan inserts: UniverseAsset, UniverseSnapshot, UniverseMember,
    # candidate_planning_generation, and entry_plan.  Returns the EntryPlan object.
    plan = _persist_waiting_plan(
        candidate_family=candidate_family,
        mode="TRIGGERED",
        tactic="OPPOSING_BBO_IOC",
    )
    # Retrieve the generation id from DB (generation was created internally).
    ep_row = repo.get_entry_plan(plan.plan_id)
    assert ep_row is not None, "entry plan must exist after _persist_waiting_plan"
    resolved_gen_id = ep_row["planning_generation_id"]
    candidate_id = ep_row["candidate_id"]

    # Update scanner_family on the universe_members row to what the test expects.
    repo.execute_write(
        "UPDATE universe_members SET scanner_family=? WHERE id=?",
        (scanner_family, candidate_id),
    )
    snap_row = repo.get_db().execute(
        "SELECT snapshot_id FROM universe_members WHERE id=?", (candidate_id,)
    ).fetchone()
    snapshot_id = snap_row["snapshot_id"] if snap_row else None

    now = datetime.now(timezone.utc)

    # ---- TradeProposal (opportunity_id=NULL, source_kind='universe') --------
    proposal = TradeProposal(
        id=f"uni-proposal-{position_id}",
        opportunity_id=None,          # Universe: explicitly NULL
        entry_plan_id=plan.plan_id,
        entry_leg_index=0,
        symbol=plan.symbol,
        market_type=plan.market_type,
        direction=plan.direction,
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode=plan.execution_account_mode,
        trade_school=plan.trade_school or "short_term",
        status="executed",
        attribution_snapshot=json.dumps({
            "entry_plan_id": plan.plan_id,
            "source_kind": "universe",
            "source_id": candidate_id,
            "trade_school": plan.trade_school,
            "planning_generation_id": resolved_gen_id,
        }, sort_keys=True, separators=(",", ":")),
    )
    repo.insert_trade_proposal(proposal)

    # ---- Authoritative RiskDecision ----------------------------------------
    risk = RiskDecision(
        id=f"uni-risk-{position_id}",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=plan.execution_account_mode,
        proposal_execution_mode=plan.execution_account_mode,
        decision_role="authoritative",
        evaluated_quantity=1.0,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(risk)

    # ---- Entry Order (entry_plan_id set) -----------------------------------
    entry_order = Order(
        id=f"uni-order-entry-{position_id}",
        uta_account_identity_json=_identity("bitget_demo").model_dump_json(),
        proposal_id=proposal.id,
        risk_decision_id=risk.id,
        entry_plan_id=plan.plan_id,
        symbol=plan.symbol,
        market_type=plan.market_type,
        direction=plan.direction,
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode=plan.execution_account_mode,
        exchange_order_id="uni-exchange-1",
        status="filled",
        source="adopted",
        position_id=position_id,
    )
    repo.insert_order(entry_order)

    entry_fill = Fill(
        id=f"uni-fill-entry-{position_id}",
        order_id=entry_order.id,
        fill_price=100.0,
        fill_quantity=1.0,
        fee=0.1,
        fee_source="reconciled",
    )
    repo.insert_fill(entry_fill)

    # ---- Position (opportunity_id=NULL) ------------------------------------
    position = Position(
        id=position_id,
        symbol=plan.symbol,
        market_type=plan.market_type,
        direction=plan.direction,
        quantity=1.0,
        entry_price=100.0,
        current_price=108.0,
        entry_fee=0.1,
        exit_fee=0.1,
        funding_cost=0.0,
        funding_source="not_applicable",
        realized_pnl=7.8,
        execution_mode=plan.execution_account_mode,
        opportunity_id=None,           # Universe: explicitly NULL
        proposal_id=proposal.id,
        order_id=entry_order.id,
        status="closed",
        close_reason="take_profit",
        closed_at=now.isoformat(),
        close_category="strategy",
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        exit_price_source="broker_fill",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        outcome_semantics_version="oi-v2",
    )
    repo.insert_position(position)
    # Back-link the entry order to the position
    repo.execute_write(
        "UPDATE orders SET position_id=? WHERE id=?",
        (position.id, entry_order.id),
    )

    # ---- Close Order + Fill ------------------------------------------------
    close_order = Order(
        id=f"uni-order-close-{position_id}",
        uta_account_identity_json=_identity("bitget_demo").model_dump_json(),
        symbol=plan.symbol,
        market_type=plan.market_type,
        direction="short",
        order_type="market",
        quantity=1.0,
        price=108.0,
        execution_mode=plan.execution_account_mode,
        status="filled",
        source="adopted",
        position_id=position_id,
        close_reason="take_profit",
    )
    repo.insert_order(close_order)
    close_fill = Fill(
        id=f"uni-fill-close-{position_id}",
        order_id=close_order.id,
        fill_price=108.0,
        fill_quantity=1.0,
        fee=0.1,
        fee_source="reconciled",
    )
    repo.insert_fill(close_fill)

    return {
        "candidate_id": candidate_id,
        "candidate_family": candidate_family,
        "scanner_family": scanner_family,
        "snapshot_id": snapshot_id,
        "generation_id": resolved_gen_id,
        "entry_plan_id": plan.plan_id,
        "proposal_id": proposal.id,
        "risk_id": risk.id,
        "entry_order_id": entry_order.id,
        "entry_fill_id": entry_fill.id,
        "position_id": position.id,
        "close_order_id": close_order.id,
        "close_fill_id": close_fill.id,
    }


# ---------------------------------------------------------------------------
# Batch A — fixture legality proof
# ---------------------------------------------------------------------------

def test_universe_lifecycle_chain_satisfies_attribution_chain_complete(
    tmp_path, monkeypatch,
):
    """A legal Universe chain must pass outcome_integrity.attribution_chain_complete.

    This is the existence proof that the fixture is structurally sound.
    The _ATTRIBUTION_SQL Universe branch already handles opportunity_id=NULL;
    this test locks that behaviour as a precondition for the Batch B migration.
    """
    from tests.test_sqlite_writer import _setup_db
    from aletheia.storage import repositories as repo
    from aletheia.analytics.outcome_integrity import attribution_chain_complete

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain()

    position_row = repo._select_one("positions", ids["position_id"])
    assert position_row is not None, "position not found"
    assert position_row["opportunity_id"] is None, "fixture must have NULL opportunity_id"

    result = attribution_chain_complete(dict(position_row))
    assert result is True, (
        "Universe chain with opportunity_id=NULL must satisfy attribution_chain_complete. "
        "If this fails, the fixture is not legal and must be fixed before continuing."
    )


# ---------------------------------------------------------------------------
# Batch A — current broken behaviour (failing regressions)
# These tests document the *current* broken output.  After Batch C they
# become green passing assertions for the corrected behaviour.
# ---------------------------------------------------------------------------

def test_position_replay_universe_discovery_currently_unavailable(
    tmp_path, monkeypatch,
):
    """REGRESSION — position_replay currently marks discovery=unavailable for Universe positions.

    Root cause: lines 582-588 of position_replay.py join exclusively via
    Position.opportunity_id.  For Universe rows (opportunity_id=NULL) the
    opportunity/member/asset resolution always returns None, which causes:
      - stages.discovery.availability = 'unavailable'
      - pi_decision_not_recorded in causal_errors (via proposal_not_found path)

    After Batch C (shared lineage_reader migration) this test should assert:
      - stages.discovery.availability = 'available'
      - no pi_decision_not_recorded in causal_errors
      - stages.discovery.universe_member is not None
    """
    from tests.test_sqlite_writer import _setup_db
    from aletheia.analytics.position_replay import build_position_replay

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain()

    replay = build_position_replay(ids["position_id"])

    discovery = replay.get("stages", {}).get("discovery", {})
    # causal errors live in replay["causal_link"]["errors"], not top-level
    causal_errors = replay.get("causal_link", {}).get("errors", [])

    # Current broken behaviour — document it:
    assert discovery.get("availability") == "unavailable", (
        "REGRESSION GUARD: Universe position_replay discovery should be 'unavailable' "
        "until Batch C migrates position_replay to use the shared lineage_reader. "
        "If this fails, position_replay was fixed without the shared reader — "
        "verify the fix uses the shared lineage_reader and update this assertion."
    )
    assert "pi_decision_not_recorded" in causal_errors, (
        "REGRESSION GUARD: pi_decision_not_recorded must appear in causal_link.errors "
        "until the Universe branch is handled correctly."
    )


def test_position_lifecycle_story_universe_opportunity_currently_none(
    tmp_path, monkeypatch,
):
    """REGRESSION — position_lifecycle_story _related_rows returns opportunity=None for Universe.

    Root cause: readers.py lines 204-208 join via Position.opportunity_id only.
    For Universe rows this is always None, so the story has no source identity.

    After Batch C this test should assert opportunity is None (still, as it
    correctly is for Universe) but source_kind='universe' is populated from
    the shared lineage reader instead.
    """
    from tests.test_sqlite_writer import _setup_db
    from aletheia.analytics.position_lifecycle_story.readers import _related_rows

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain()

    from aletheia.storage.sqlite import get_db
    db = get_db()
    position_row = dict(
        db.execute("SELECT * FROM positions WHERE id=?", (ids["position_id"],)).fetchone()
    )

    cutoff = datetime.now(timezone.utc)
    related = _related_rows(db, position_row, cutoff=cutoff)

    # Current state: opportunity is None (no opportunity table row exists — correct)
    assert related.get("opportunity") is None, (
        "Universe positions have no opportunity row — this should remain None."
    )
    # Current state: no universe_member or candidate resolution
    # After Batch C: a 'universe_member' or 'source_lineage' key should appear
    assert "universe_member" not in related, (
        "REGRESSION GUARD: universe_member not yet in _related_rows. "
        "After Batch C this should be present."
    )


def test_family_scorecard_universe_family_currently_unknown(
    tmp_path, monkeypatch,
):
    """REGRESSION — family_scorecard cannot resolve scanner_family for Universe positions.

    Root cause has two layers:
    1. family_scorecard.py lines 71-81 resolve family ONLY via
       proposal.opportunity_id → opportunities.scanner_family.
       For Universe rows opportunity_id is NULL → scanner_family='unknown'.
    2. _persist_waiting_plan produces market_type='perp', which assess_position_closure
       excludes from eligible_strategy_outcomes (only 'spot'/'usdt_futures' accepted).
       So the position never appears in the scorecard at all.

    Current observable state: compute_family_scorecard returns [] (empty) for this
    fixture because the perp position is ineligible — it doesn't even reach the
    broken Universe resolver.

    After Batch C this test should assert scanner_family='mean_reversion' using a
    spot/usdt_futures fixture that reaches the resolver and resolves the family
    via lineage_reader → universe_member.scanner_family.
    """
    from tests.test_sqlite_writer import _setup_db
    from aletheia.analytics.family_scorecard import compute_family_scorecard

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain(
        scanner_family="mean_reversion",
    )

    families = compute_family_scorecard(execution_mode="bitget_demo")

    family_names = [f["family"] for f in families]
    # Current broken behaviour: perp position excluded by economics check → empty scorecard.
    # Universe ancestry resolver bug is masked by this exclusion.
    assert "mean_reversion" not in family_names, (
        "REGRESSION GUARD: family_scorecard should NOT resolve 'mean_reversion' "
        "until Batch C migrates family_scorecard to use the shared lineage_reader. "
        "If this fails, family_scorecard was fixed — update this assertion."
    )
    # Scorecard is empty because the perp market_type fails economics eligibility check;
    # after Batch C a spot fixture should produce a non-empty list with 'mean_reversion'.
    assert families == [], (
        "REGRESSION GUARD: Universe perp position must be excluded (empty scorecard) "
        "until Batch C introduces a spot/usdt_futures fixture and lineage_reader fix."
    )
