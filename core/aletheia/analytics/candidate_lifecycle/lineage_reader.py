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

Cutoff semantics: a row whose `created_at` is strictly after `cutoff` is
treated as not-yet-visible; the row is dropped and an `evidence_gap`
"<entity>_not_yet_visible" is recorded. `universe_members` has no
`created_at` column, so its visibility is gated by its `universe_snapshots.created_at`.
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
    candidate: dict[str, Any] | None
    generation: dict[str, Any] | None
    entry_plan: dict[str, Any] | None
    universe_member: dict[str, Any] | None
    snapshot: dict[str, Any] | None
    opportunity_row: dict[str, Any] | None
    legs: list[dict[str, Any]] = field(default_factory=list)
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
    if not _visible(pos, cutoff, "opened_at"):
        return _empty("position", position_id, gap="position_not_yet_visible")
    ep_id = _ep_id_from_proposal(db, pos.get("proposal_id"), cutoff)
    if ep_id is None:
        return _empty("position", position_id, gap="entry_plan_not_found")
    return _resolve_from_ep(ep_id, ("position", position_id), db, cutoff)


def for_proposal(proposal_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from a trade_proposal id."""
    ep_id = _ep_id_from_proposal(db, proposal_id, cutoff)
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
    if not _visible(gen, cutoff, "created_at"):
        return _empty("generation", generation_id, gap="generation_not_yet_visible")
    ep_id = gen.get("entry_plan_id")
    if not ep_id:
        return _empty("generation", generation_id, gap="entry_plan_not_found")
    return _resolve_from_ep(str(ep_id), ("generation", generation_id), db, cutoff)


def for_order(order_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from an order id."""
    order = _fetchone(db, "SELECT * FROM orders WHERE id=?", (order_id,))
    if order is None:
        return _empty("order", order_id, gap="order_not_found")
    if not _visible(order, cutoff, "created_at"):
        return _empty("order", order_id, gap="order_not_yet_visible")
    ep_id: str | None = order.get("entry_plan_id") or None
    if ep_id is None:
        ep_id = _ep_id_from_proposal(db, order.get("proposal_id"), cutoff)
    if ep_id is None:
        return _empty("order", order_id, gap="entry_plan_not_found")
    return _resolve_from_ep(ep_id, ("order", order_id), db, cutoff)


def for_candidate(candidate_id: str, cutoff: datetime, db: Any) -> LifecycleLineage:
    """Resolve lineage from a universe_member (candidate) id.

    Finds the most recent generation with entry_plan_id whose `created_at <= cutoff`.
    """
    row = _fetchone(
        db,
        """
        SELECT id, entry_plan_id, created_at FROM candidate_planning_generations
        WHERE last_candidate_id=? AND entry_plan_id IS NOT NULL
          AND datetime(created_at) <= datetime(?)
        ORDER BY datetime(created_at) DESC, rowid DESC LIMIT 1
        """,
        (candidate_id, cutoff.isoformat()),
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
    if not _visible(ep, cutoff, "created_at"):
        return _empty(anchor[0], anchor[1], gap="entry_plan_not_yet_visible")

    ep_dict = ep
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

    # source_kind: three-level fallback per issue #3281 §4.
    # 1) plan_json.$.source_kind
    # 2) proposal columns: opportunity_id present → serenity/opportunity;
    #    opportunity_id null + entry_plan_id present → universe
    # 3) discriminating tables (opportunities vs serenity_opportunities vs universe_members)
    source_kind = extracts["plan_source_kind"] or _infer_source_kind_from_proposals(
        db, ep_id, cutoff, ep_dict, gaps,
    )

    conflicts: list[str] = []
    candidate: dict[str, Any] | None = None
    generation: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    opportunity_row: dict[str, Any] | None = None

    if source_kind == "universe":
        gen_id = ep_dict.get("planning_generation_id") or extracts["plan_generation_id"]
        if gen_id:
            generation = _fetchone(
                db,
                "SELECT * FROM candidate_planning_generations WHERE id=?",
                (gen_id,),
            )
            if generation is None:
                gaps.append("generation_not_found")
            elif not _visible(generation, cutoff, "created_at"):
                gaps.append("generation_not_yet_visible")
                generation = None
        else:
            gaps.append("generation_id_missing")

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
                elif not _visible(snapshot, cutoff, "created_at"):
                    # universe_members has no created_at; gate on snapshot visibility
                    gaps.append("universe_snapshot_not_yet_visible")
                    snapshot = None
                    candidate = None

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
        proposals = _fetchall(
            db,
            "SELECT * FROM trade_proposals WHERE entry_plan_id=? "
            "AND datetime(created_at) <= datetime(?) "
            "ORDER BY entry_leg_index",
            (ep_id, cutoff.isoformat()),
        )
        opp_id = None
        for prop in proposals:
            if prop.get("opportunity_id"):
                opp_id = prop["opportunity_id"]
                break
        if opp_id:
            opportunity_row = _fetchone(
                db,
                "SELECT * FROM opportunities WHERE id=? AND datetime(created_at) <= datetime(?)",
                (opp_id, cutoff.isoformat()),
            )
            if opportunity_row is None:
                opportunity_row = _fetchone(
                    db,
                    "SELECT * FROM serenity_opportunities WHERE id=? "
                    "AND datetime(created_at) <= datetime(?)",
                    (opp_id, cutoff.isoformat()),
                )
            if opportunity_row is None:
                # Distinguish "not exists at all" from "not_yet_visible".
                exists = _fetchone(
                    db, "SELECT 1 AS one FROM opportunities WHERE id=?", (opp_id,),
                ) or _fetchone(
                    db, "SELECT 1 AS one FROM serenity_opportunities WHERE id=?", (opp_id,),
                )
                gaps.append(
                    "opportunity_not_yet_visible" if exists else "opportunity_not_found"
                )

    legs = _fetchall(
        db,
        "SELECT * FROM trade_proposals WHERE entry_plan_id=? "
        "AND datetime(created_at) <= datetime(?) "
        "ORDER BY entry_leg_index",
        (ep_id, cutoff.isoformat()),
    )

    return LifecycleLineage(
        anchor=anchor,
        source_kind=source_kind,
        candidate=candidate,
        generation=generation,
        entry_plan=ep_dict,
        universe_member=candidate,
        snapshot=snapshot,
        opportunity_row=opportunity_row,
        legs=legs,
        identity_conflicts=conflicts,
        evidence_gaps=gaps,
    )


def _infer_source_kind_from_proposals(
    db: Any,
    ep_id: str,
    cutoff: datetime,
    ep_dict: dict[str, Any],
    gaps: list[str],
) -> str:
    """Level-2/3 fallback: infer source_kind when plan_json.$.source_kind is missing."""
    proposals = _fetchall(
        db,
        "SELECT opportunity_id, entry_plan_id FROM trade_proposals "
        "WHERE entry_plan_id=? AND datetime(created_at) <= datetime(?) "
        "ORDER BY entry_leg_index",
        (ep_id, cutoff.isoformat()),
    )
    if not proposals:
        # No proposals visible yet, but the entry_plan has a candidate_id → universe.
        if ep_dict.get("candidate_id"):
            return "universe"
        return "unknown"

    opp_id: Any = None
    all_universe = True
    for prop in proposals:
        if prop.get("opportunity_id"):
            opp_id = prop["opportunity_id"]
            all_universe = False
            break
    if all_universe and ep_dict.get("candidate_id"):
        return "universe"
    if not opp_id:
        return "unknown"

    # Level 3: discriminating tables. Serenity wins if the row exists there.
    if _fetchone(db, "SELECT 1 AS one FROM serenity_opportunities WHERE id=?", (opp_id,)):
        return "serenity"
    if _fetchone(db, "SELECT 1 AS one FROM opportunities WHERE id=?", (opp_id,)):
        return "opportunity"
    return "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetchone(db: Any, sql: str, params: tuple) -> dict[str, Any] | None:
    row = db.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _fetchall(db: Any, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def _ep_id_from_proposal(db: Any, proposal_id: Any, cutoff: datetime) -> str | None:
    if not proposal_id:
        return None
    row = _fetchone(
        db,
        "SELECT entry_plan_id FROM trade_proposals WHERE id=? "
        "AND datetime(created_at) <= datetime(?)",
        (str(proposal_id), cutoff.isoformat()),
    )
    if row is None or not row.get("entry_plan_id"):
        return None
    return str(row["entry_plan_id"])


def _visible(row: dict[str, Any], cutoff: datetime, field_name: str) -> bool:
    """Return True when the row's `field_name` timestamp is <= cutoff.

    Missing / unparseable timestamps count as visible (fail-open on schema
    quirks) — the callers we own always set `created_at`; SQLite's default
    covers legacy rows. Do not use for security-sensitive gates.
    """
    value = row.get(field_name)
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= cutoff


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
