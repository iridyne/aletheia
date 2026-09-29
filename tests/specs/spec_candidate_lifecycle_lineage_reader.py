"""Spec: Candidate-first lifecycle lineage reader (#3281).

LC-S01 — Universe lineage reader resolves the same ancestry as _ATTRIBUTION_SQL.
  When a Universe chain satisfies outcome_integrity.attribution_chain_complete,
  for_entry_plan / for_proposal / for_position must all return source_kind='universe'
  with a non-None candidate, generation, universe_member, and snapshot.
  No identity_conflicts, no evidence_gaps.

LC-S02 — All anchor types resolve to the same lineage for the same chain.
  for_candidate / for_generation / for_order / for_entry_plan / for_proposal /
  for_position all return the same source_kind and candidate.id for an
  identical underlying chain.

LC-S03 — Missing entry_plan produces evidence_gap, not a crash.
  for_position with a position whose proposal has no entry_plan_id returns
  source_kind='unknown' and 'entry_plan_not_found' in evidence_gaps.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.test_sqlite_writer import _setup_db
from tests.test_universe_lifecycle_regression import _build_universe_lifecycle_chain


_LC_S01 = SpecClaim(
    "LC-S01", "candidate_lifecycle_contract", "§universe-lineage-reader", "L2a",
    "for_entry_plan on a Universe chain returns source_kind='universe', non-None "
    "candidate/generation/universe_member/snapshot, and matches the JOIN logic of "
    "outcome_integrity._ATTRIBUTION_SQL.",
)
_LC_S02 = SpecClaim(
    "LC-S02", "candidate_lifecycle_contract", "§anchor-consistency", "L2a",
    "All six anchor types (candidate_id, generation_id, entry_plan_id, proposal_id, "
    "order_id, position_id) return the same source_kind and candidate.id for the "
    "same underlying chain.",
)
_LC_S03 = SpecClaim(
    "LC-S03", "candidate_lifecycle_contract", "§missing-entry-plan", "L2a",
    "for_position with a proposal that has no entry_plan_id returns "
    "source_kind='unknown' and 'entry_plan_not_found' in evidence_gaps without "
    "raising an exception.",
)


@spec_claim(_LC_S01)
def test_universe_lineage_reader_matches_attribution_chain(tmp_path, monkeypatch):
    """for_entry_plan resolves the same Universe ancestry as _ATTRIBUTION_SQL."""
    from aletheia.analytics.candidate_lifecycle import for_entry_plan, for_position, for_proposal
    from aletheia.analytics.outcome_integrity import attribution_chain_complete
    from aletheia.storage.sqlite import get_db

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain()
    db = get_db()
    cutoff = datetime.now(timezone.utc)

    # Precondition: OI considers the chain complete
    from aletheia.storage import repositories as repo
    pos_row = repo._select_one("positions", ids["position_id"])
    assert pos_row is not None
    assert attribution_chain_complete(dict(pos_row), db=db) is True

    # LC-S01: for_entry_plan resolves the same rows
    lin = for_entry_plan(ids["entry_plan_id"], cutoff, db)

    assert lin.source_kind == "universe", f"expected universe, got {lin.source_kind!r}"
    assert lin.identity_conflicts == [], f"conflicts: {lin.identity_conflicts}"
    assert lin.evidence_gaps == [], f"gaps: {lin.evidence_gaps}"

    assert lin.candidate is not None, "candidate must be resolved"
    assert lin.universe_member is not None, "universe_member must be resolved"
    assert lin.generation is not None, "generation must be resolved"
    assert lin.snapshot is not None, "snapshot must be resolved"

    assert str(lin.candidate["id"]) == str(ids["candidate_id"])
    assert str(lin.generation["id"]) == str(ids["generation_id"])
    assert str(lin.snapshot["id"]) == str(ids["snapshot_id"])

    # Proposals are attached
    assert len(lin.legs) >= 1, "at least one entry leg expected"
    assert lin.legs[0]["entry_plan_id"] == ids["entry_plan_id"]

    # Same result via for_proposal and for_position
    lin_p = for_proposal(ids["proposal_id"], cutoff, db)
    assert lin_p.source_kind == "universe"
    assert lin_p.candidate is not None
    assert str(lin_p.candidate["id"]) == str(ids["candidate_id"])

    lin_pos = for_position(ids["position_id"], cutoff, db)
    assert lin_pos.source_kind == "universe"
    assert lin_pos.candidate is not None
    assert str(lin_pos.candidate["id"]) == str(ids["candidate_id"])


@spec_claim(_LC_S02)
def test_all_anchor_types_return_consistent_lineage(tmp_path, monkeypatch):
    """All six anchor types return source_kind='universe' and the same candidate."""
    from aletheia.analytics.candidate_lifecycle import (
        for_candidate,
        for_entry_plan,
        for_generation,
        for_order,
        for_position,
        for_proposal,
    )
    from aletheia.storage.sqlite import get_db

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain()
    db = get_db()
    cutoff = datetime.now(timezone.utc)

    lineages = {
        "candidate": for_candidate(ids["candidate_id"], cutoff, db),
        "generation": for_generation(ids["generation_id"], cutoff, db),
        "entry_plan": for_entry_plan(ids["entry_plan_id"], cutoff, db),
        "proposal": for_proposal(ids["proposal_id"], cutoff, db),
        "order": for_order(ids["entry_order_id"], cutoff, db),
        "position": for_position(ids["position_id"], cutoff, db),
    }

    for anchor_kind, lin in lineages.items():
        assert lin.source_kind == "universe", (
            f"{anchor_kind}: expected source_kind='universe', got {lin.source_kind!r}"
        )
        assert lin.candidate is not None, f"{anchor_kind}: candidate must be resolved"
        assert str(lin.candidate["id"]) == str(ids["candidate_id"]), (
            f"{anchor_kind}: candidate.id mismatch"
        )
        assert lin.evidence_gaps == [], f"{anchor_kind}: evidence_gaps={lin.evidence_gaps}"


@spec_claim(_LC_S03)
def test_missing_entry_plan_yields_evidence_gap(tmp_path, monkeypatch):
    """for_position with a proposal that has no entry_plan_id returns evidence_gap."""
    from aletheia.analytics.candidate_lifecycle import for_position
    from aletheia.storage.sqlite import get_db
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Position, TradeProposal
    from tests.uta.test_execution_coordinator import _identity

    _setup_db(tmp_path, monkeypatch)
    db = get_db()
    cutoff = datetime.now(timezone.utc)

    # Insert a minimal position with a proposal that has no entry_plan_id
    proposal = TradeProposal(
        id="orphan-proposal",
        opportunity_id=None,
        entry_plan_id=None,   # explicitly no plan
        entry_leg_index=None,  # must be NULL when entry_plan_id is NULL (DB constraint)
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="bitget_demo",
        trade_school="short_term",
        status="executed",
    )
    repo.insert_trade_proposal(proposal)

    order = Order(
        id="orphan-order",
        uta_account_identity_json=_identity("bitget_demo").model_dump_json(),
        proposal_id=proposal.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="bitget_demo",
        status="filled",
        source="adopted",
        position_id="orphan-position",
    )
    repo.insert_order(order)

    position = Position(
        id="orphan-position",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=105.0,
        entry_fee=0.1,
        exit_fee=0.1,
        funding_cost=0.0,
        funding_source="not_applicable",
        realized_pnl=4.9,
        execution_mode="bitget_demo",
        opportunity_id=None,
        proposal_id=proposal.id,
        order_id=order.id,
        status="closed",
        close_reason="take_profit",
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

    lin = for_position("orphan-position", cutoff, db)

    assert lin.source_kind == "unknown"
    assert "entry_plan_not_found" in lin.evidence_gaps, (
        f"expected 'entry_plan_not_found' in evidence_gaps, got {lin.evidence_gaps}"
    )
    assert lin.candidate is None
