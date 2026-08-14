"""Exact, read-only Opportunity lifecycle story for Agent inspection."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from aletheia.analytics.position_lifecycle_story import (
    build_position_lifecycle_story,
    render_position_lifecycle_story,
)

OPPORTUNITY_LIFECYCLE_CONTRACT_VERSION = "opportunity_lifecycle/v1"


def _parse_as_of(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid as_of_at") from None


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _table_exists(db: Any, table: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _has_column(db: Any, table: str, column: str) -> bool:
    try:
        return column in {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return False


def _empty_lifecycle() -> dict[str, Any]:
    return {
        "decisions": [],
        "proposals": [],
        "risk_decisions": [],
        "orders": [],
        "fills": [],
        "positions": [],
    }


def _empty_multiplicity() -> dict[str, Any]:
    return {
        "decisions": 0,
        "proposals": 0,
        "risk_decisions": 0,
        "orders": 0,
        "fills": 0,
        "positions": 0,
        "zero_position": None,
        "multiple_positions": False,
    }


def _envelope(
    opportunity_id: str,
    *,
    as_of_at: str,
    availability: str,
    unavailable_reason: str | None,
    opportunity: Mapping[str, Any] | None = None,
    evidence_gaps: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": OPPORTUNITY_LIFECYCLE_CONTRACT_VERSION,
        "story_unit": "opportunity_lifecycle",
        "opportunity_id": opportunity_id,
        "as_of_at": as_of_at,
        "read_only": True,
        "availability": availability,
        "unavailable_reason": unavailable_reason,
        "opportunity": dict(opportunity) if opportunity is not None else None,
        "lifecycle": _empty_lifecycle(),
        "multiplicity": _empty_multiplicity(),
        "linkage_audit": {
            "matched_edges": [],
            "mismatches": [],
            "orphan_children": [],
            "evidence_gaps": list(evidence_gaps or []),
        },
        "evidence": {
            "evidence_gaps": list(evidence_gaps or []),
            "source_status": {},
        },
        "counterfactual": {
            "shadow": None,
            "shadow_observations": [],
            "shadow_multiplicity": 0,
            "forward": {},
        },
        "primary_cause": None,
    }


def _rows(
    db: Any,
    table: str,
    where: str,
    params: tuple[Any, ...],
    *,
    order_by: str = "",
    gaps: list[str],
) -> list[dict[str, Any]]:
    if not _table_exists(db, table):
        gaps.append(f"{table}:table_unavailable")
        return []
    try:
        sql = f"SELECT * FROM {table} WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        return [dict(row) for row in db.execute(sql, params).fetchall()]
    except sqlite3.DatabaseError as exc:
        gaps.append(f"{table}:query_unavailable:{type(exc).__name__}")
        return []


def _visible(
    rows: list[dict[str, Any]],
    cutoff: datetime,
    *,
    fields: tuple[str, ...],
    label: str,
    gaps: list[str],
) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for row in rows:
        raw = next((row.get(field) for field in fields if row.get(field) is not None), None)
        if raw is None:
            gaps.append(f"{label}:{fields[0]}_unavailable:{row.get('id', 'unknown')}")
            continue
        parsed = _timestamp(raw)
        if parsed is None:
            gaps.append(f"{label}:invalid_timestamp:{row.get('id', 'unknown')}")
            continue
        if parsed <= cutoff:
            candidate = dict(row)
            for field in fields[1:]:
                future = _timestamp(row.get(field))
                if future is not None and future > cutoff:
                    candidate[field] = None
                    if field == "filled_at" and str(candidate.get("status") or "").lower() in {
                        "filled", "closed", "complete", "completed"
                    }:
                        candidate["status"] = "open"
            visible.append(candidate)
    return visible


def _ids(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(row["id"]) for row in rows if row.get("id") is not None]


def _in_clause(values: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    return ",".join("?" for _ in values), tuple(values)


def _child_rows(
    db: Any,
    table: str,
    column: str,
    values: list[str],
    cutoff: datetime,
    *,
    time_fields: tuple[str, ...],
    label: str,
    gaps: list[str],
) -> list[dict[str, Any]]:
    if not values:
        return []
    if not _table_exists(db, table):
        gaps.append(f"{table}:table_unavailable")
        return []
    if not _has_column(db, table, column):
        gaps.append(f"{table}:{column}_unavailable")
        return []
    placeholders, params = _in_clause(values)
    return _visible(
        _rows(db, table, f"{column} IN ({placeholders})", params, order_by="rowid ASC", gaps=gaps),
        cutoff,
        fields=time_fields,
        label=label,
        gaps=gaps,
    )


def _position_rows(
    db: Any,
    opportunity_id: str,
    proposal_ids: list[str],
    order_ids: list[str],
    cutoff: datetime,
    gaps: list[str],
) -> list[dict[str, Any]]:
    if not _table_exists(db, "positions"):
        gaps.append("positions:table_unavailable")
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if _has_column(db, "positions", "opportunity_id"):
        clauses.append("opportunity_id=?")
        params.append(opportunity_id)
    else:
        gaps.append("positions:opportunity_id_unavailable")
    if proposal_ids and _has_column(db, "positions", "proposal_id"):
        placeholders, values = _in_clause(proposal_ids)
        clauses.append(f"proposal_id IN ({placeholders})")
        params.extend(values)
    elif proposal_ids:
        gaps.append("positions:proposal_id_unavailable")
    if order_ids and _has_column(db, "positions", "order_id"):
        placeholders, values = _in_clause(order_ids)
        clauses.append(f"order_id IN ({placeholders})")
        params.extend(values)
    elif order_ids:
        gaps.append("positions:order_id_unavailable")
    if not clauses:
        return []
    rows = _rows(
        db,
        "positions",
        " OR ".join(clauses),
        tuple(params),
        order_by="datetime(opened_at) ASC, rowid ASC",
        gaps=gaps,
    )
    visible = _visible(
        rows,
        cutoff,
        fields=("opened_at", "created_at"),
        label="positions",
        gaps=gaps,
    )
    projected: list[dict[str, Any]] = []
    for row in visible:
        closed_at = _timestamp(row.get("closed_at"))
        if closed_at is not None and closed_at > cutoff:
            row = dict(row)
            row.update(
                {
                    "status": "open",
                    "closed_at": None,
                    "current_price": None,
                    "realized_pnl": None,
                    "close_reason": None,
                    "exit_price_source": "unavailable",
                    "exit_fill_verified": False,
                    "outcome_eligible": False,
                }
            )
        projected.append(row)
    return projected


def _order_kind(
    order: Mapping[str, Any], positions: list[Mapping[str, Any]]
) -> str:
    order_id = str(order.get("id"))
    if any(str(position.get("order_id")) == order_id for position in positions):
        return "entry"
    if str(order.get("close_reason") or "").strip():
        return "close"
    # A position-linked order without an explicit close reason is an
    # averaging/entry leg, not proof of an exit.
    if order.get("position_id") is not None or order.get("proposal_id") is not None:
        return "entry"
    return "unknown"


def _counterfactual(
    db: Any,
    opportunity: Mapping[str, Any],
    direction: str | None,
    cutoff: datetime,
    gaps: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "shadow": None,
        "shadow_observations": [],
        "shadow_multiplicity": 0,
        "forward": {},
    }
    opportunity_id = str(opportunity.get("id"))
    shadow_rows = _rows(
        db,
        "shadow_trades",
        "opportunity_id=?",
        (opportunity_id,),
        order_by="datetime(COALESCE(resolved_at,created_at)) DESC, rowid DESC",
        gaps=gaps,
    )
    shadow_created = _visible(
        shadow_rows,
        cutoff,
        fields=("created_at",),
        label="shadow_trades",
        gaps=gaps,
    )
    if shadow_created:
        observations: list[dict[str, Any]] = []
        for row in shadow_created:
            resolved = _timestamp(row.get("resolved_at"))
            resolved_visible = resolved is not None and resolved <= cutoff
            observations.append(
                {
                    "id": row.get("id"),
                    "pnl_pct": row.get("pnl_pct") if resolved_visible else None,
                    "entry_price": row.get("entry_price"),
                    "exit_price": row.get("exit_price") if resolved_visible else None,
                    "resolved_at": row.get("resolved_at") if resolved_visible else None,
                    "status": "resolved" if resolved_visible else "pending",
                }
            )
        result["shadow_observations"] = observations
        result["shadow_multiplicity"] = len(observations)
        result["shadow"] = observations[0]
        if len(observations) > 1:
            gaps.append("shadow_trades:multiple_observations_not_collapsed")

    snapshot_id = opportunity.get("universe_snapshot_id")
    member_id = opportunity.get("universe_member_id")
    if not snapshot_id or not member_id or not direction:
        if snapshot_id and not member_id:
            gaps.append("universe_forward_outcomes:member_identity_unavailable")
        return result
    if not _table_exists(db, "universe_forward_outcomes"):
        gaps.append("universe_forward_outcomes:table_unavailable")
        return result
    if not _has_column(db, "universe_forward_outcomes", "member_id"):
        gaps.append("universe_forward_outcomes:member_id_unavailable")
        return result
    if not _has_column(db, "universe_forward_outcomes", "direction"):
        gaps.append("universe_forward_outcomes:direction_unavailable")
        return result
    for horizon in ("15m", "1h", "4h", "24h"):
        clauses = ["snapshot_id=?", "member_id=?", "direction=?", "horizon=?", "status='settled'"]
        params: list[Any] = [snapshot_id, member_id, direction, horizon]
        rows = _rows(
            db,
            "universe_forward_outcomes",
            " AND ".join(clauses),
            tuple(params),
            order_by="datetime(settled_at) DESC, rowid DESC",
            gaps=gaps,
        )
        visible = _visible(
            rows,
            cutoff,
            fields=("settled_at",),
            label="universe_forward_outcomes",
            gaps=gaps,
        )
        if visible:
            row = visible[0]
            result["forward"][horizon] = {
                "id": row.get("id"),
                "forward_return_pct": row.get("forward_return_pct"),
                "net_return_pct": row.get("net_return_pct"),
                "settled_at": row.get("settled_at"),
                "status": row.get("status"),
            }
    return result


def build_opportunity_lifecycle_story(
    opportunity_id: str,
    *,
    as_of_at: str | datetime | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Build an exact-identity, read-only Opportunity lifecycle contract."""

    cutoff = _parse_as_of(as_of_at)
    canonical_id = str(opportunity_id)
    as_of = _iso(cutoff)
    gaps: list[str] = []
    if db is None:
        try:
            from aletheia.storage.sqlite import get_db

            db = get_db()
        except (OSError, sqlite3.DatabaseError) as exc:
            return _envelope(
                canonical_id,
                as_of_at=as_of,
                availability="unavailable",
                unavailable_reason="opportunity_source_unavailable",
                evidence_gaps=[f"database:{type(exc).__name__}"],
            )

    if not _table_exists(db, "opportunities"):
        return _envelope(
            canonical_id,
            as_of_at=as_of,
            availability="unavailable",
            unavailable_reason="opportunity_source_unavailable",
            evidence_gaps=["opportunities:table_unavailable"],
        )
    try:
        row = db.execute("SELECT * FROM opportunities WHERE id=?", (canonical_id,)).fetchone()
    except sqlite3.DatabaseError as exc:
        return _envelope(
            canonical_id,
            as_of_at=as_of,
            availability="unavailable",
            unavailable_reason="opportunity_source_unavailable",
            evidence_gaps=[f"opportunities:query_unavailable:{type(exc).__name__}"],
        )
    if row is None:
        return _envelope(
            canonical_id,
            as_of_at=as_of,
            availability="unavailable",
            unavailable_reason="opportunity_not_found",
        )
    opportunity = dict(row)
    created_at = _timestamp(opportunity.get("created_at"))
    if created_at is None:
        return _envelope(
            canonical_id,
            as_of_at=as_of,
            availability="unavailable",
            unavailable_reason="opportunity_created_at_unavailable",
            evidence_gaps=["opportunities:created_at_unavailable"],
        )
    if created_at > cutoff:
        return _envelope(
            canonical_id,
            as_of_at=as_of,
            availability="unavailable",
            unavailable_reason="opportunity_created_after_cutoff",
        )
    opportunity["canonical_identity"] = {"opportunity_id": canonical_id}

    direct_decisions = _visible(
        _rows(
            db,
            "pi_decisions",
            "opportunity_id=?",
            (canonical_id,),
            order_by="datetime(created_at) ASC, rowid ASC",
            gaps=gaps,
        ),
        cutoff,
        fields=("created_at",),
        label="pi_decisions",
        gaps=gaps,
    )
    proposals = _visible(
        _rows(
            db,
            "trade_proposals",
            "opportunity_id=?",
            (canonical_id,),
            order_by="datetime(created_at) ASC, rowid ASC",
            gaps=gaps,
        ),
        cutoff,
        fields=("created_at",),
        label="trade_proposals",
        gaps=gaps,
    )
    proposal_ids = _ids(proposals)
    proposal_decisions = _child_rows(
        db,
        "pi_decisions",
        "proposal_id",
        proposal_ids,
        cutoff,
        time_fields=("created_at",),
        label="pi_decisions",
        gaps=gaps,
    )
    risks = _child_rows(
        db,
        "risk_decisions",
        "proposal_id",
        proposal_ids,
        cutoff,
        time_fields=("created_at",),
        label="risk_decisions",
        gaps=gaps,
    )
    proposal_orders = _child_rows(
        db,
        "orders",
        "proposal_id",
        proposal_ids,
        cutoff,
        time_fields=("created_at", "filled_at"),
        label="orders",
        gaps=gaps,
    )
    proposal_order_ids = _ids(proposal_orders)
    positions = _position_rows(
        db,
        canonical_id,
        proposal_ids,
        proposal_order_ids,
        cutoff,
        gaps,
    )
    position_ids = _ids(positions)
    position_orders = _child_rows(
        db,
        "orders",
        "position_id",
        position_ids,
        cutoff,
        time_fields=("created_at", "filled_at"),
        label="orders",
        gaps=gaps,
    )
    all_order_rows = {str(row["id"]): row for row in proposal_orders + position_orders if row.get("id")}
    order_rows = list(all_order_rows.values())
    order_ids = list(all_order_rows)
    fill_rows = _child_rows(
        db,
        "fills",
        "order_id",
        order_ids,
        cutoff,
        time_fields=("filled_at",),
        label="fills",
        gaps=gaps,
    )
    fills_by_order: dict[str, list[dict[str, Any]]] = {}
    for fill in fill_rows:
        fills_by_order.setdefault(str(fill.get("order_id")), []).append(fill)

    enriched_orders: list[dict[str, Any]] = []
    for order in sorted(order_rows, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or ""))):
        enriched = dict(order)
        enriched["kind"] = _order_kind(order, positions)
        enriched["fills"] = [dict(fill) for fill in fills_by_order.get(str(order.get("id")), [])]
        enriched_orders.append(enriched)

    risk_by_proposal: dict[str, list[dict[str, Any]]] = {}
    for risk in risks:
        risk_by_proposal.setdefault(str(risk.get("proposal_id")), []).append(dict(risk))
    decisions_by_proposal: dict[str, list[dict[str, Any]]] = {}
    for decision in proposal_decisions:
        decisions_by_proposal.setdefault(str(decision.get("proposal_id")), []).append(dict(decision))
    orders_by_proposal: dict[str, list[dict[str, Any]]] = {}
    for order in enriched_orders:
        proposal_id = order.get("proposal_id")
        if proposal_id is not None:
            orders_by_proposal.setdefault(str(proposal_id), []).append(dict(order))

    embedded_proposals: list[dict[str, Any]] = []
    for proposal in proposals:
        embedded = dict(proposal)
        proposal_id = str(proposal.get("id"))
        embedded["pi_decisions"] = decisions_by_proposal.get(proposal_id, [])
        embedded["risk_decisions"] = risk_by_proposal.get(proposal_id, [])
        embedded["orders"] = orders_by_proposal.get(proposal_id, [])
        embedded_proposals.append(embedded)

    mismatches: list[dict[str, Any]] = []
    orphan_children: list[dict[str, Any]] = []
    for decision in direct_decisions:
        proposal_id = decision.get("proposal_id")
        if proposal_id is not None and str(proposal_id) not in proposal_ids:
            mismatches.append({
                "entity_type": "pi_decision",
                "entity_id": decision.get("id"),
                "reason": "decision_proposal_not_in_opportunity",
                "proposal_id": proposal_id,
            })
    for decision in proposal_decisions:
        decision_opportunity_id = decision.get("opportunity_id")
        if (
            decision_opportunity_id is not None
            and str(decision_opportunity_id) != canonical_id
        ):
            mismatches.append(
                {
                    "entity_type": "pi_decision",
                    "entity_id": decision.get("id"),
                    "reason": "proposal_decision_opportunity_mismatch",
                    "opportunity_id": decision_opportunity_id,
                }
            )
    for order in enriched_orders:
        proposal_id = order.get("proposal_id")
        if proposal_id is not None and str(proposal_id) not in proposal_ids:
            orphan_children.append({
                "entity_type": "order",
                "entity_id": order.get("id"),
                "reason": "order_proposal_not_in_opportunity",
                "proposal_id": proposal_id,
            })
    for risk in risks:
        proposal_id = str(risk.get("proposal_id"))
        if proposal_id not in proposal_ids:
            orphan_children.append({
                "entity_type": "risk_decision",
                "entity_id": risk.get("id"),
                "reason": "risk_proposal_not_in_opportunity",
                "proposal_id": risk.get("proposal_id"),
            })
    matched_edges: list[dict[str, Any]] = []
    linked_positions: list[dict[str, Any]] = []
    for position in positions:
        position_id = str(position.get("id"))
        edge_names: list[str] = []
        if position.get("opportunity_id") == canonical_id:
            edge_names.append("position.opportunity_id")
        if position.get("proposal_id") is not None and str(position.get("proposal_id")) in proposal_ids:
            edge_names.append("position.proposal_id")
        if position.get("order_id") is not None and str(position.get("order_id")) in order_ids:
            edge_names.append("position.order_id")
        position_mismatches: list[dict[str, Any]] = []
        if position.get("opportunity_id") is not None and position.get("opportunity_id") != canonical_id:
            position_mismatches.append({"field": "opportunity_id", "value": position.get("opportunity_id")})
        if position.get("proposal_id") is not None and str(position.get("proposal_id")) not in proposal_ids:
            position_mismatches.append({"field": "proposal_id", "value": position.get("proposal_id")})
        if position.get("order_id") is not None and str(position.get("order_id")) not in order_ids:
            position_mismatches.append({"field": "order_id", "value": position.get("order_id")})
        if position_mismatches:
            mismatches.append({
                "entity_type": "position",
                "entity_id": position_id,
                "reason": "contradictory_or_unmatched_identity",
                "fields": position_mismatches,
            })
        matched_edges.append({"position_id": position_id, "edges": edge_names})
        linked_positions.append(
            {
                "position_id": position_id,
                "linkage": {
                    "opportunity_id": position.get("opportunity_id"),
                    "proposal_id": position.get("proposal_id"),
                    "entry_order_id": position.get("order_id"),
                    "close_order_ids": [
                        str(order.get("id"))
                        for order in enriched_orders
                        if str(order.get("position_id")) == position_id
                        and str(order.get("id")) != str(position.get("order_id"))
                    ],
                    "matched_edges": edge_names,
                    "mismatches": position_mismatches,
                },
                "story": _position_story(position_id, cutoff, db, gaps),
            }
        )

    lifecycle = {
        "decisions": direct_decisions,
        "proposals": embedded_proposals,
        "risk_decisions": risks,
        "orders": enriched_orders,
        "fills": fill_rows,
        "positions": linked_positions,
    }
    position_source_available = not any(gap.startswith("positions:") for gap in gaps)
    multiplicity = {
        "decisions": len(direct_decisions),
        "proposals": len(proposals),
        "risk_decisions": len({str(row.get("id")) for row in risks}),
        "orders": len(enriched_orders),
        "fills": len({str(row.get("id")) for row in fill_rows}),
        "positions": len(linked_positions),
        "zero_position": not linked_positions and position_source_available,
        "multiple_positions": len(linked_positions) > 1,
    }
    counterfactual = _counterfactual(
        db,
        opportunity,
        {
            "buy": "long",
            "sell": "short",
        }.get(str(opportunity.get("direction_bias") or "").strip().lower(), str(opportunity.get("direction_bias") or "").strip().lower() or None),
        cutoff,
        gaps,
    )
    unique_gaps = list(dict.fromkeys(gaps))
    source_status = {
        "opportunity": "available",
        "decisions": "available" if not any(gap.startswith("pi_decisions:") for gap in unique_gaps) else "unavailable",
        "proposals": "available" if not any(gap.startswith("trade_proposals:") for gap in unique_gaps) else "unavailable",
        "positions": "available" if position_source_available else "unavailable",
    }
    return {
        "contract_version": OPPORTUNITY_LIFECYCLE_CONTRACT_VERSION,
        "story_unit": "opportunity_lifecycle",
        "opportunity_id": canonical_id,
        "as_of_at": as_of,
        "read_only": True,
        "availability": "available",
        "unavailable_reason": None,
        "opportunity": opportunity,
        "lifecycle": lifecycle,
        "multiplicity": multiplicity,
        "linkage_audit": {
            "matched_edges": matched_edges,
            "mismatches": mismatches,
            "orphan_children": orphan_children,
            "evidence_gaps": unique_gaps,
        },
        "evidence": {
            "evidence_gaps": unique_gaps,
            "source_status": source_status,
        },
        "counterfactual": counterfactual,
        "primary_cause": None,
    }


def _position_story(
    position_id: str, cutoff: datetime, db: Any, gaps: list[str]
) -> dict[str, Any]:
    try:
        return build_position_lifecycle_story(
            position_id,
            as_of_at=cutoff,
            db=db,
        )
    except (sqlite3.DatabaseError, TypeError, ValueError, KeyError) as exc:
        gaps.append(f"position_story:{position_id}:unavailable:{type(exc).__name__}")
        return {
            "contract_version": "position_lifecycle_story/v1",
            "position_id": position_id,
            "availability": "unavailable",
            "unavailable_reason": "position_story_unavailable",
            "read_only": True,
        }


def render_opportunity_lifecycle_story(story: Mapping[str, Any]) -> str:
    """Render the complete Opportunity funnel and every nested Position story."""

    lines = [
        f"Opportunity: {story.get('opportunity_id', 'unknown')}",
        f"Availability: {story.get('availability') or 'unavailable'}",
        f"As of: {story.get('as_of_at') or 'unknown'}",
    ]
    reason = story.get("unavailable_reason")
    if reason:
        lines.append(f"Unavailable reason: {reason}")
    opportunity = story.get("opportunity")
    if isinstance(opportunity, Mapping):
        lines.extend(
            [
                f"Root: {opportunity.get('symbol') or 'unknown'} "
                f"{opportunity.get('direction_bias') or 'unknown'}; "
                f"created_at={opportunity.get('created_at') or 'unavailable'}",
                f"Thesis: {opportunity.get('thesis') or 'not recorded'}",
            ]
        )
    lifecycle = story.get("lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
    multiplicity = story.get("multiplicity")
    multiplicity = multiplicity if isinstance(multiplicity, Mapping) else {}
    lines.extend(
        [
            "",
            "Causal funnel",
            f"Decisions: {multiplicity.get('decisions', 0)}",
            f"Proposals: {multiplicity.get('proposals', 0)}",
            f"Risk decisions: {multiplicity.get('risk_decisions', 0)}",
            f"Orders: {multiplicity.get('orders', 0)}",
            f"Fills: {multiplicity.get('fills', 0)}",
            f"Positions: {multiplicity.get('positions', 0)}",
        ]
    )
    source_status = story.get("evidence")
    source_status = source_status.get("source_status", {}) if isinstance(source_status, Mapping) else {}
    if story.get("availability") != "available" or source_status.get("positions") == "unavailable":
        lines.append("Position state: unavailable; zero-position count is not evidence.")
    elif multiplicity.get("zero_position"):
        lines.append("Position state: zero exact linked Positions (valid evidence).")
    elif multiplicity.get("multiple_positions"):
        lines.append("Position state: multiple exact linked Positions; no singleton collapse.")

    for position in lifecycle.get("positions") or []:
        if not isinstance(position, Mapping):
            continue
        lines.extend(
            [
                "",
                f"Position linkage: {position.get('position_id')}",
                f"  matched_edges: {json.dumps(position.get('linkage', {}).get('matched_edges', []), ensure_ascii=False)}",
                f"  mismatches: {json.dumps(position.get('linkage', {}).get('mismatches', []), ensure_ascii=False, default=str)}",
            ]
        )
        nested = position.get("story")
        if isinstance(nested, Mapping):
            lines.extend(render_position_lifecycle_story(nested).splitlines())

    audit = story.get("linkage_audit")
    audit = audit if isinstance(audit, Mapping) else {}
    lines.extend(["", "Linkage and evidence limits"])
    for key in ("mismatches", "orphan_children", "evidence_gaps"):
        values = audit.get(key) or []
        lines.append(f"{key}: {json.dumps(values, ensure_ascii=False, default=str) if values else 'none recorded'}")
    counterfactual = story.get("counterfactual")
    lines.append(f"Counterfactual evidence: {json.dumps(counterfactual or {}, ensure_ascii=False, default=str)}")
    lines.append("Primary cause: not claimed; this contract is descriptive evidence only.")
    return "\n".join(lines)


__all__ = [
    "OPPORTUNITY_LIFECYCLE_CONTRACT_VERSION",
    "build_opportunity_lifecycle_story",
    "render_opportunity_lifecycle_story",
]
