"""Shared exact-lineage reader for Universe-origin (and Serenity/Opportunity) trade chains.

All public functions accept an injected sqlite3 connection (caller-owned; no
commits or rollbacks here) and a cutoff datetime for point-in-time reads.

Universe branch resolution mirrors the exact JOIN logic from
outcome_integrity._ATTRIBUTION_SQL:
  entry_plans → candidate_planning_generations (via planning_generation_id)
              → universe_members (via candidate_id / last_candidate_id)
              → universe_snapshots (via snapshot_id)
Cross-checked against plan_json.$.source_kind / source_id / candidate_id /
planning_generation_id to surface identity_conflicts when they disagree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class LifecycleLineage:
    anchor: tuple[str, str]           # (kind, id)
    source_kind: str                  # 'universe' | 'serenity' | 'opportunity' | 'unknown'
    candidate: dict[str, Any] | None  # universe_members row (None when absent or non-universe)
    generation: dict[str, Any] | None # candidate_planning_generations row
    entry_plan: dict[str, Any] | None # entry_plans row (includes plan_json_extracts)
    universe_member: dict[str, Any] | None  # same object as candidate; alias for consumers
    snapshot: dict[str, Any] | None   # universe_snapshots row
    opportunity_row: dict[str, Any] | None  # opportunities or serenity_opportunities row
    legs: list[dict[str, Any]] = field(default_factory=list)  # trade_proposals for this plan
    identity_conflicts: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public anchor resolution
# ---------------------------------------------------------------------------

def for_position(position_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from a position_id."""
    pos = _fetchone(db, "SELECT * FROM positions WHERE id=?", (position_id,))
    if pos is None:
        return _empty("position", position_id, gap="position_not_found")
    # Walk through proposal → entry_plan
    ep_id = _ep_id_from_proposal(db, pos.get("proposal_id"))
    if ep_id is None:
        return _empty("position", position_id, gap="entry_plan_not_found")
    return _resolve_from_ep(ep_id, ("position", position_id), db, cutoff)


def for_proposal(proposal_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from a trade_proposal id."""
    ep_id = _ep_id_from_proposal(db, proposal_id)
    if ep_id is None:
        return _empty("proposal", proposal_id, gap="entry_plan_not_found")
    return _resolve_from_ep(ep_id, ("proposal", proposal_id), db, cutoff)


def for_entry_plan(entry_plan_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from an entry_plan id."""
    return _resolve_from_ep(entry_plan_id, ("entry_plan", entry_plan_id), db, cutoff)


def for_generation(generation_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from a candidate_planning_generation id."""
    gen = _fetchone(
        db,
        "SELECT * FROM candidate_planning_generations WHERE id=?",
        (generation_id,),
    )
    if gen is None:
        return _empty("generation", generation_id, gap="generation_not_found")
    ep_id = gen.get("entry_plan_id")  # already a dict
    if not ep_id:
        return _empty("generation", generation_id, gap="entry_plan_not_found")
    return _resolve_from_ep(str(ep_id), ("generation", generation_id), db, cutoff)


def for_order(order_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from an order id."""
    order = _fetchone(db, "SELECT * FROM orders WHERE id=?", (order_id,))
    if order is None:
        return _empty("order", order_id, gap="order_not_found")
    ep_id: str | None = order.get("entry_plan_id") or None
    if ep_id is None:
        ep_id = _ep_id_from_proposal(db, order.get("proposal_id"))
    if ep_id is None:
        return _empty("order", order_id, gap="entry_plan_not_found")
    return _resolve_from_ep(ep_id, ("order", order_id), db, cutoff)


def for_candidate(candidate_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from a universe_member (candidate) id.

    Finds the most recent generation with an entry_plan_id.
    """
    row = _fetchone(
        db,
        """
        SELECT entry_plan_id FROM candidate_planning_generations
        WHERE last_candidate_id=? AND entry_plan_id IS NOT NULL
        ORDER BY rowid DESC LIMIT 1
        """,
        (candidate_id,),
    )
    if row is None or not row["entry_plan_id"]:
        return _empty("candidate", candidate_id, gap="entry_plan_not_found")
    return _resolve_from_ep(str(row["entry_plan_id"]), ("candidate", candidate_id), db, cutoff)


# ---------------------------------------------------------------------------
# Core resolution
# ---------------------------------------------------------------------------

def _resolve_from_ep(
    ep_id: str,
    anchor: tuple[str, str],
    db: Any,
    cutoff: datetime,
) -> LifecycleLineage:
    ep = _fetchone(db, "SELECT * FROM entry_plans WHERE id=?", (ep_id,))
    if ep is None:
        return _empty(anchor[0], anchor[1], gap="entry_plan_not_found")

    ep_dict = ep  # already a dict
    plan_json: dict[str, Any] = {}
    gaps: list[str] = []
    if ep_dict.get("plan_json"):
        try:
            plan_json = json.loads(ep_dict["plan_json"])
        except (ValueError, TypeError):
            gaps.append("plan_json_parse_error")

    extracts = {
        "plan_source_kind": plan_json.get("source_kind"),
        "plan_source_id": plan_json.get("source_id"),
        "plan_candidate_id": plan_json.get("candidate_id"),
        "plan_generation_id": plan_json.get("planning_generation_id"),
    }
    ep_dict.update(extracts)

    # Determine source_kind from plan_json first, then fall back
    source_kind = extracts["plan_source_kind"] or "unknown"

    conflicts: list[str] = []
    candidate: dict[str, Any] | None = None
    generation: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    opportunity_row: dict[str, Any] | None = None

    if source_kind == "universe":
        # Universe branch: resolve generation via ep.planning_generation_id
        gen_id = ep_dict.get("planning_generation_id") or extracts["plan_generation_id"]
        if gen_id:
            generation = _fetchone(
                db,
                "SELECT * FROM candidate_planning_generations WHERE id=?",
                (gen_id,),
            )
            if generation is None:
                gaps.append("generation_not_found")
        else:
            gaps.append("generation_id_missing")

        # Resolve universe_member via ep.candidate_id
        cand_id = ep_dict.get("candidate_id") or extracts["plan_candidate_id"]
        if cand_id:
            candidate = _fetchone(
                db, "SELECT * FROM universe_members WHERE id=?", (cand_id,)
            )
            if candidate is None:
                gaps.append("universe_member_not_found")
            else:
                snapshot = _fetchone(
                    db,
                    "SELECT * FROM universe_snapshots WHERE id=?",
                    (candidate["snapshot_id"],),
                )
                if snapshot is None:
                    gaps.append("universe_snapshot_not_found")

            # Cross-check plan_json extracts vs joined rows
            if extracts["plan_source_id"] and cand_id != extracts["plan_source_id"]:
                conflicts.append(
                    f"plan_json.source_id={extracts['plan_source_id']!r} "
                    f"!= ep.candidate_id={cand_id!r}"
                )
            if (
                generation is not None
                and extracts["plan_generation_id"]
                and str(generation["id"]) != str(extracts["plan_generation_id"])
            ):
                conflicts.append(
                    f"plan_json.planning_generation_id={extracts['plan_generation_id']!r} "
                    f"!= generation.id={generation['id']!r}"
                )
        else:
            gaps.append("candidate_id_missing")

    elif source_kind in ("serenity", "opportunity"):
        # Resolve via proposals' opportunity_id
        proposals = _fetchall(
            db,
            "SELECT * FROM trade_proposals WHERE entry_plan_id=? ORDER BY entry_leg_index",
            (ep_id,),
        )
        opp_id = None
        for prop in proposals:
            if prop.get("opportunity_id"):
                opp_id = prop["opportunity_id"]
                break
        if opp_id:
            opportunity_row = _fetchone(
                db, "SELECT * FROM opportunities WHERE id=?", (opp_id,)
            )
            if opportunity_row is None:
                opportunity_row = _fetchone(
                    db, "SELECT * FROM serenity_opportunities WHERE id=?", (opp_id,)
                )
            if opportunity_row is None:
                gaps.append("opportunity_not_found")

    # Collect entry-leg proposals for this plan
    legs = [
        dict(r)
        for r in _fetchall(
            db,
            "SELECT * FROM trade_proposals WHERE entry_plan_id=? ORDER BY entry_leg_index",
            (ep_id,),
        )
    ]

    return LifecycleLineage(
        anchor=anchor,
        source_kind=source_kind,
        candidate=candidate,
        generation=generation,
        entry_plan=ep_dict,
        universe_member=candidate,  # alias
        snapshot=snapshot,
        opportunity_row=opportunity_row,
        legs=legs,
        identity_conflicts=conflicts,
        evidence_gaps=gaps,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetchone(db: Any, sql: str, params: tuple) -> dict[str, Any] | None:
    row = db.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _fetchall(db: Any, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def _ep_id_from_proposal(db: Any, proposal_id: Any) -> str | None:
    if not proposal_id:
        return None
    row = _fetchone(
        db, "SELECT entry_plan_id FROM trade_proposals WHERE id=?", (str(proposal_id),)
    )
    if row is None or not row.get("entry_plan_id"):
        return None
    return str(row["entry_plan_id"])


def _empty(anchor_kind: str, anchor_id: str, *, gap: str) -> LifecycleLineage:
    return LifecycleLineage(
        anchor=(anchor_kind, anchor_id),
        source_kind="unknown",
        candidate=None,
        generation=None,
        entry_plan=None,
        universe_member=None,
        snapshot=None,
        opportunity_row=None,
        legs=[],
        identity_conflicts=[],
        evidence_gaps=[gap],
    )
