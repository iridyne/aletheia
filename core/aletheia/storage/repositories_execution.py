"""Execution-domain reads and pure computations.

Shared repository dependencies are imported inside functions to preserve both
facade import orders and the existing helper and transaction owners.
"""

import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone

from pydantic import ValidationError

from aletheia.data.symbols import to_canonical
from aletheia.storage.models import Order, TradeProposal

__all__ = [
    '_compute_proposal_hash',
    '_validate_native_entry_order_snapshot',
    'entry_plan_activation_can_refresh',
    'get_authoritative_risk_decision',
    'get_entry_plan',
    'get_entry_plan_execution_lineage',
    'get_entry_risk_reservation',
    'get_entry_trigger_activation',
    'get_order',
    'get_order_for_entry_plan',
    'get_proposal_execution_claim',
    'get_trade_proposal',
    'get_trade_proposal_by_entry_plan',
    'has_order_for_proposal',
    'list_active_entry_plans',
    'list_active_entry_risk_reservations_for_risk',
    'list_entry_plans',
    'list_orders',
    'list_risk_decisions',
    'list_trade_proposals',
    'list_unresolved_native_entry_orders_for_risk',
    'list_watchable_entry_plans',
    'list_watchable_entry_plans_page',
    'proposal_execution_claim_is_fresh',
    'validate_native_order_authority',
    'validate_trade_proposal_commit_hash',
    'watchable_entry_plan_high_watermark',
]


def _compute_proposal_hash(p: TradeProposal) -> str:
    """Return 16-char SHA-256 prefix of the proposal's immutable content + parent_hash.

    Stable for a given proposal as long as no excluded field is mutated. 16 hex
    chars = 64-bit collision space (OpenAlice's reference impl uses 8; we widen
    to 16 because the cost is trivial and false-collision risk drops by ~2^32).
    """
    from aletheia.storage.repositories import (
        _PROPOSAL_HASH_EXCLUDES,
    )

    from aletheia.storage.hashing import sha256_json

    payload = p.model_dump(exclude=_PROPOSAL_HASH_EXCLUDES)
    # Additive identity: NULL historical rows retain their exact original hash.
    if p.entry_leg_index is None:
        payload.pop("entry_leg_index")
    return sha256_json(payload)[:16]



def validate_trade_proposal_commit_hash(
    proposal: TradeProposal | Mapping[str, object],
) -> str:
    """Return the stored Proposal hash only if immutable content still matches it."""
    from aletheia.storage.repositories import (
        _compute_proposal_hash,
    )

    try:
        stored = proposal if isinstance(proposal, TradeProposal) else TradeProposal.model_validate(proposal)
    except ValidationError as exc:
        raise ValueError("stored proposal content cannot be validated") from exc
    if stored.commit_hash is None:
        raise ValueError("stored proposal has no commit hash")
    if _compute_proposal_hash(stored) != stored.commit_hash:
        raise ValueError("stored proposal content does not match commit hash")
    return stored.commit_hash



def list_trade_proposals(limit: int = 50, status: str | None = None) -> list[dict]:
    from aletheia.storage.repositories import (
        _select_all,
    )

    if status:
        return _select_all("trade_proposals", where="status = ?", params=(status,), limit=limit)
    return _select_all("trade_proposals", limit=limit)



def get_trade_proposal(pid: str) -> dict | None:
    from aletheia.storage.repositories import (
        _select_one,
    )

    return _select_one("trade_proposals", pid)



def get_trade_proposal_by_entry_plan(entry_plan_id: str, *, leg_index: int | None = None) -> dict | None:
    from aletheia.storage.repositories import (
        get_db,
    )

    row = (
        get_db()
        .execute(
            "SELECT * FROM trade_proposals WHERE entry_plan_id=? "
            "AND (? IS NULL OR COALESCE(entry_leg_index,0)=?) "
            "ORDER BY COALESCE(entry_leg_index,0) DESC LIMIT 1",
            (entry_plan_id, leg_index, leg_index),
        )
        .fetchone()
    )
    return dict(row) if row else None



def get_proposal_execution_claim(proposal_id: str) -> dict | None:
    """Return the durable execution owner/lease for one Proposal."""
    from aletheia.storage.repositories import (
        get_db,
    )

    row = (
        get_db()
        .execute(
            "SELECT * FROM proposal_execution_claims WHERE proposal_id=?",
            (proposal_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None



def proposal_execution_claim_is_fresh(claim: dict, now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(str(claim.get("lease_expires_at")).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > now
    except (TypeError, ValueError):
        return False



def get_entry_risk_reservation(proposal_id: str) -> dict | None:
    """Return one durable pre-Order Risk allowance, if present."""
    from aletheia.storage.repositories import (
        get_db,
    )

    row = (
        get_db()
        .execute(
            "SELECT * FROM entry_risk_reservations WHERE proposal_id=?",
            (proposal_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None



def list_active_entry_risk_reservations_for_risk(
    *,
    exclude_proposal_id: str | None = None,
) -> list[dict]:
    """Return every unresolved allowance not represented by a durable Order.

    No row limit is permitted. Excluding rows that already have any Order avoids
    double counting while Gateway performs its best-effort reservation cleanup.
    A reservation whose Proposal is provably terminal is also inactive even if
    physical cleanup failed.  Missing Proposals and unknown statuses remain in
    the working set so Risk continues to fail closed on ambiguous state.
    """
    from aletheia.storage.repositories import (
        _RISK_PROPOSAL_PROJECTION,
        get_db,
    )

    sql = (
        "SELECT r.*, "
        f"{_RISK_PROPOSAL_PROJECTION}, "
        "(SELECT COUNT(*) FROM risk_decisions authority "
        " WHERE authority.proposal_id=r.proposal_id "
        "   AND authority.decision_role='authoritative' "
        "   AND authority.approved=1 "
        "   AND authority.proposal_commit_hash=r.proposal_commit_hash "
        "   AND authority.evaluated_quantity=r.quantity "
        "   AND authority.allowed_execution_mode=r.execution_mode "
        "   AND authority.proposal_execution_mode=r.execution_mode "
        "   AND COALESCE(authority.violations, '[]')='[]'"
        ") AS exact_authoritative_decision_count "
        "FROM entry_risk_reservations r "
        "LEFT JOIN trade_proposals tp ON tp.id=r.proposal_id "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM orders o WHERE o.proposal_id=r.proposal_id"
        ") "
        "AND COALESCE(tp.status, '') NOT IN ('rejected','error','executed')"
    )
    params: list = []
    if exclude_proposal_id is not None:
        sql += " AND r.proposal_id != ?"
        params.append(exclude_proposal_id)
    sql += " ORDER BY r.created_at ASC, r.rowid ASC"
    rows = get_db().execute(sql, params).fetchall()
    return [dict(row) for row in rows]



def has_order_for_proposal(proposal_id: str) -> bool:
    """Return whether any durable Order already represents this Proposal."""
    from aletheia.storage.repositories import (
        get_db,
    )

    return (
        get_db()
        .execute(
            "SELECT 1 FROM orders WHERE proposal_id=? LIMIT 1",
            (proposal_id,),
        )
        .fetchone()
        is not None
    )



def get_order_for_entry_plan(entry_plan_id: str) -> dict | None:
    """Read the latest leg Order; the Plan backlink is historical provenance only."""
    from aletheia.storage.repositories import (
        get_db,
    )

    row = get_db().execute(
        "SELECT o.* FROM orders o LEFT JOIN trade_proposals p ON p.id=o.proposal_id "
        "WHERE o.entry_plan_id=? AND o.close_reason IS NULL "
        "ORDER BY COALESCE(p.entry_leg_index,0) DESC, o.created_at DESC, o.id DESC LIMIT 1",
        (entry_plan_id,),
    ).fetchone()
    return dict(row) if row else None



def get_authoritative_risk_decision(
    proposal_id: str,
    proposal_commit_hash: str | None,
    evaluated_quantity: float | None,
    policy_hash: str | None = None,
    execution_attempt_id: str | None = None,
) -> dict | None:
    """Return the authoritative decision for one immutable evaluated snapshot."""
    from aletheia.storage.repositories import (
        get_db,
    )

    policy_clause = "AND policy_hash IS ?" if policy_hash is not None else ""
    attempt_clause = "AND execution_attempt_id IS ?" if execution_attempt_id is not None else ""
    params = [proposal_id, proposal_commit_hash, evaluated_quantity]
    if policy_hash is not None:
        params.append(policy_hash)
    if execution_attempt_id is not None:
        params.append(execution_attempt_id)
    row = (
        get_db()
        .execute(
            f"""
        SELECT *
        FROM risk_decisions
        WHERE proposal_id = ?
          AND decision_role = 'authoritative'
          AND proposal_commit_hash IS ?
          AND evaluated_quantity IS ?
          {policy_clause}
          {attempt_clause}
        ORDER BY rowid ASC
        LIMIT 1
        """,
            tuple(params),
        )
        .fetchone()
    )
    return dict(row) if row else None



def list_risk_decisions(limit: int = 50, approved: bool | None = None) -> list[dict]:
    from aletheia.storage.repositories import (
        _select_all,
    )

    if approved is not None:
        return _select_all(
            "risk_decisions",
            where="approved = ?",
            params=(int(approved),),
            limit=limit,
        )
    return _select_all("risk_decisions", limit=limit)



def get_order(oid: str) -> dict | None:
    from aletheia.storage.repositories import (
        _select_one,
    )

    return _select_one("orders", oid)



def _validate_native_entry_order_snapshot(
    data: dict,
    proposal: dict | None,
    decision: dict | None,
) -> None:
    """Validate one native entry against already-read immutable authority."""
    from aletheia.storage.repositories import (
        _compute_proposal_hash,
    )

    proposal_id = data.get("proposal_id")
    risk_decision_id = data.get("risk_decision_id")
    if not proposal_id or not risk_decision_id:
        raise ValueError("native entry Order requires Proposal and authoritative RiskDecision")
    if proposal is None or proposal.get("source") != "native":
        raise ValueError("native entry Order requires a native Proposal")
    if proposal.get("id") != proposal_id:
        raise ValueError("native entry Order Proposal identity does not match")
    if decision is None or decision.get("id") != risk_decision_id or decision.get("proposal_id") != proposal_id:
        raise ValueError("native entry Order RiskDecision does not match Proposal")
    if not bool(decision.get("approved")):
        raise ValueError("native entry Order requires an approved RiskDecision")
    if decision.get("decision_role") != "authoritative":
        raise ValueError("native entry Order requires an authoritative RiskDecision")
    try:
        decision_violations = json.loads(decision.get("violations") or "[]")
    except (TypeError, ValueError):
        decision_violations = None
    if decision_violations != []:
        raise ValueError("native entry Order approved RiskDecision is contradictory")

    stored = TradeProposal.model_validate(proposal)
    stored_hash = stored.commit_hash
    if not stored_hash or _compute_proposal_hash(stored) != stored_hash:
        raise ValueError("native entry Order Proposal integrity check failed")
    if decision.get("proposal_commit_hash") != stored_hash:
        raise ValueError("native entry Order RiskDecision snapshot is stale")

    try:
        evaluated_quantity = float(decision.get("evaluated_quantity"))
        order_quantity = float(data.get("quantity"))
    except (TypeError, ValueError):
        raise ValueError("native entry Order lacks evaluated quantity authority") from None
    if evaluated_quantity <= 0 or order_quantity <= 0 or order_quantity != evaluated_quantity:
        raise ValueError("native entry Order quantity does not match RiskDecision")

    execution_mode = data.get("execution_mode")
    if (
        decision.get("allowed_execution_mode") != execution_mode
        or decision.get("proposal_execution_mode") != stored.execution_mode
        or execution_mode != stored.execution_mode
    ):
        raise ValueError("native entry Order execution mode lacks matching authority")
    for field in ("symbol", "market_type", "direction", "order_type"):
        if data.get(field) != getattr(stored, field):
            raise ValueError(f"native entry Order {field} does not match Proposal")
    try:
        order_price = float(data.get("price"))
        proposal_price = float(stored.price)
    except (TypeError, ValueError):
        raise ValueError("native entry Order price lacks Proposal authority") from None
    # Every exchange Order preserves the submitted Proposal price.
    if (
        proposal_price <= 0
        or order_price <= 0
        or order_price != proposal_price
    ):
        raise ValueError("native entry Order price does not match Proposal")
    if data.get("client_order_id") != proposal_id:
        raise ValueError("exchange entry Order client identity is not durable")



def validate_native_order_authority(order: Order | Mapping[str, object]) -> None:
    """Fail closed when a new native Order lacks its immutable authority.

    Entry Orders consume exactly one approved authoritative RiskDecision. Close
    Orders consume the already-open Position they reduce. Adopted exchange state
    is deliberately exempt because its synthetic chain records observation, not
    a new Aletheia trading decision.
    """
    from aletheia.storage.repositories import (
        _select_one,
        _to_payload_dict,
        _validate_native_entry_order_snapshot,
        get_trade_proposal,
    )

    from aletheia.uta.manager import UTA_EXECUTION_MODES

    data = _to_payload_dict(order)
    if data.get("execution_mode") not in UTA_EXECUTION_MODES:
        raise ValueError("unsupported Order execution mode")
    if data.get("source", "native") != "native":
        return

    position_id = data.get("position_id")
    position = _select_one("positions", position_id) if position_id else None
    pending_owner = bool(position and position.get("pending_close_order_id") == data.get("id"))
    is_close = bool(data.get("close_reason")) or pending_owner
    if is_close:
        if position is None:
            raise ValueError("native close Order requires an existing Position")
        if position.get("status") != "open":
            raise ValueError("native close Order requires an open Position")
        if data.get("risk_decision_id") is not None:
            raise ValueError("native close Order consumes Position authority, not a RiskDecision")
        for field in ("proposal_id", "symbol", "market_type", "direction", "execution_mode"):
            if data.get(field) != position.get(field):
                raise ValueError(f"native close Order {field} does not match Position")
        requested = float(data.get("quantity") or 0.0)
        position_quantity = float(position.get("quantity") or 0.0)
        tolerance = max(1e-12, position_quantity * 1e-9)
        if requested <= 0:
            raise ValueError("native close Order quantity must be positive")
        protection_suffix = {
            "protective_stop": "sl",
            "take_profit": "tp",
        }.get(data.get("close_reason"))
        if protection_suffix and str(data.get("client_order_id") or "").startswith("protect-"):
            if requested > position_quantity + tolerance:
                raise ValueError("native protection Order exceeds Position quantity")
            expected_client_id = f"protect-{position_id}-{protection_suffix}"
            if data.get("client_order_id") != expected_client_id:
                raise ValueError("native protection Order client identity is not durable")
            return
        is_partial_reduce = requested < position_quantity - tolerance
        if is_partial_reduce:
            # Partial reduce: quantity must be strictly less than position quantity
            return
        # Full close: quantity must equal position quantity and client_order_id must be durable
        if abs(requested - position_quantity) > tolerance:
            raise ValueError("native close Order quantity does not match Position")
        from aletheia.execution.pending_close_recovery import is_durable_close_client_oid

        if not is_durable_close_client_oid(str(position_id), data.get("client_order_id")):
            raise ValueError("native close Order client identity is not durable")
        return

    proposal_id = data.get("proposal_id")
    risk_decision_id = data.get("risk_decision_id")
    proposal = get_trade_proposal(proposal_id) if proposal_id else None
    decision = _select_one("risk_decisions", risk_decision_id) if risk_decision_id else None
    _validate_native_entry_order_snapshot(data, proposal, decision)



def list_orders(
    limit: int = 100,
    status: str | None = None,
    entry_plan_id: str | None = None,
) -> list[dict]:
    from aletheia.storage.repositories import (
        _select_all,
    )

    if entry_plan_id:
        where = "entry_plan_id = ?"
        params: tuple[object, ...] = (entry_plan_id,)
        if status:
            where += " AND status = ?"
            params += (status,)
        return _select_all("orders", where=where, params=params, limit=limit)
    if status:
        return _select_all("orders", where="status = ?", params=(status,), limit=limit)
    return _select_all("orders", limit=limit)



def list_unresolved_native_entry_orders_for_risk() -> list[dict]:
    """Return every unresolved native entry plus its represented local state.

    This query is intentionally unbounded: truncating accepted exchange Orders
    would let Risk under-count possible exposure.  Close Orders are excluded by
    both their durable reason and the Position pending-close backlink so older
    rows that predate ``close_reason`` cannot consume a new-entry allowance.
    """
    from aletheia.storage.repositories import (
        NATIVE_ENTRY_ORDER_SQL,
        _RISK_DECISION_PROJECTION,
        _RISK_PROPOSAL_PROJECTION,
        get_db,
    )

    from aletheia.storage.order_states import NON_TERMINAL_ORDER_STATES

    placeholders = ",".join("?" for _ in NON_TERMINAL_ORDER_STATES)
    rows = (
        get_db()
        .execute(
            f"""
        SELECT
            o.*,
            {_RISK_PROPOSAL_PROJECTION},
            {_RISK_DECISION_PROJECTION},
            tp.leverage AS proposal_leverage,
            tp.market_type AS proposal_market_type,
            tp.commit_hash AS proposal_commit_hash,
            COUNT(f.id) AS persisted_fill_count,
            COALESCE(SUM(f.fill_quantity), 0.0) AS persisted_fill_quantity,
            COALESCE(SUM(
                CASE
                    WHEN f.id IS NOT NULL
                     AND (f.fill_quantity IS NULL OR f.fill_quantity <= 0)
                    THEN 1 ELSE 0
                END
            ), 0) AS invalid_fill_quantity_count,
            (
                SELECT COUNT(*)
                FROM positions linked_position
                WHERE linked_position.status = 'open'
                  AND (
                      linked_position.id = o.position_id
                      OR linked_position.order_id = o.id
                  )
            ) AS linked_open_position_count,
            (
                SELECT COUNT(*)
                FROM positions matching_position
                WHERE matching_position.status = 'open'
                  AND matching_position.id = o.position_id
                  AND matching_position.symbol = o.symbol
                  AND matching_position.market_type = o.market_type
                  AND matching_position.direction = o.direction
                  AND matching_position.execution_mode = o.execution_mode
            ) AS matching_open_position_count,
            (
                SELECT MAX(matching_position.quantity)
                FROM positions matching_position
                WHERE matching_position.status = 'open'
                  AND matching_position.id = o.position_id
                  AND matching_position.symbol = o.symbol
                  AND matching_position.market_type = o.market_type
                  AND matching_position.direction = o.direction
                  AND matching_position.execution_mode = o.execution_mode
            ) AS represented_position_quantity,
            (
                SELECT COALESCE(SUM(represented_fill.fill_quantity), 0.0)
                FROM positions representing_position
                JOIN orders represented_order
                  ON (
                      represented_order.id = representing_position.order_id
                      OR represented_order.position_id = representing_position.id
                  )
                JOIN fills represented_fill
                  ON represented_fill.order_id = represented_order.id
                WHERE representing_position.status = 'open'
                  AND representing_position.id = o.position_id
                  AND representing_position.symbol = o.symbol
                  AND representing_position.market_type = o.market_type
                  AND representing_position.direction = o.direction
                  AND representing_position.execution_mode = o.execution_mode
                  AND represented_order.source = 'native'
                  AND TRIM(COALESCE(represented_order.close_reason, '')) = ''
                  AND represented_order.id != COALESCE(
                      representing_position.pending_close_order_id,
                      ''
                  )
            ) AS represented_entry_fill_quantity,
            (
                SELECT COUNT(*)
                FROM positions representing_position
                JOIN orders represented_order
                  ON (
                      represented_order.id = representing_position.order_id
                      OR represented_order.position_id = representing_position.id
                  )
                JOIN fills represented_fill
                  ON represented_fill.order_id = represented_order.id
                WHERE representing_position.status = 'open'
                  AND representing_position.id = o.position_id
                  AND representing_position.symbol = o.symbol
                  AND representing_position.market_type = o.market_type
                  AND representing_position.direction = o.direction
                  AND representing_position.execution_mode = o.execution_mode
                  AND represented_order.source = 'native'
                  AND TRIM(COALESCE(represented_order.close_reason, '')) = ''
                  AND represented_order.id != COALESCE(
                      representing_position.pending_close_order_id,
                      ''
                  )
                  AND (
                      represented_fill.fill_quantity IS NULL
                      OR represented_fill.fill_quantity <= 0
                  )
            ) AS invalid_represented_entry_fill_count
        FROM orders o
        LEFT JOIN fills f ON f.order_id = o.id
        LEFT JOIN trade_proposals tp ON tp.id = o.proposal_id
        LEFT JOIN risk_decisions rd ON rd.id = o.risk_decision_id
        WHERE {NATIVE_ENTRY_ORDER_SQL}
          AND o.status IN ({placeholders})
        GROUP BY o.id
        ORDER BY o.created_at ASC, o.rowid ASC
        """,
            NON_TERMINAL_ORDER_STATES,
        )
        .fetchall()
    )
    return [dict(row) for row in rows]



def get_entry_plan_execution_lineage(plan_ids: list[str]) -> dict[str, dict]:
    """Read exact execution and settlement links from one SQLite snapshot."""
    from aletheia.storage.repositories import (
        get_db,
        transaction,
    )

    from aletheia.portfolio.pnl import _realized_economics_value
    from aletheia.data.symbols import normalize_market_type
    from aletheia.analytics.outcome_semantics import validated_outcome_economics
    from aletheia.trading.entry_learning import _instant

    cutoff = datetime.now(timezone.utc)

    identifiers = list(dict.fromkeys(plan_ids))
    result = {}
    with transaction():
        db = get_db()
        for offset in range(0, len(identifiers), 200):
            batch = identifiers[offset:offset + 200]
            placeholders = ",".join("?" for _ in batch)
            plans = {row["id"]: dict(row) for row in db.execute(
                f"SELECT * FROM entry_plans WHERE id IN ({placeholders})", batch
            )}
            linked_ids = [p["order_id"] for p in plans.values() if p.get("order_id")]
            linked_marks = ",".join("?" for _ in linked_ids) or "NULL"
            orders = {row["id"]: dict(row) for row in db.execute(
                f"SELECT * FROM orders WHERE entry_plan_id IN ({placeholders}) "
                f"OR id IN ({linked_marks})", [*batch, *linked_ids]
            )}
            proposal_ids = [o["proposal_id"] for o in orders.values() if o.get("proposal_id")]
            proposals = {row["id"]: dict(row) for row in db.execute(
                "SELECT * FROM trade_proposals WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(proposal_ids),),
            )}
            order_ids = list(orders)
            marks = ",".join("?" for _ in order_ids) or "NULL"
            fills = [dict(row) for row in db.execute(
                f"SELECT * FROM fills WHERE order_id IN ({marks})", order_ids
            )]
            outcomes = [dict(row) for row in db.execute(
                "SELECT * FROM filter_candidate_outcomes WHERE horizon='4h' "
                "AND outcome_candidate_key IN (SELECT value FROM json_each(?))",
                (json.dumps([f"entry-fill:{f['id']}" for f in fills]),),
            )]
            position_ids = [o["position_id"] for o in orders.values() if o.get("position_id")]
            position_marks = ",".join("?" for _ in position_ids) or "NULL"
            positions = {row["id"]: dict(row) for row in db.execute(
                f"SELECT * FROM positions WHERE order_id IN ({marks}) "
                f"OR id IN ({position_marks})", [*order_ids, *position_ids]
            )}
            pids = list(positions)
            pm = ",".join("?" for _ in pids) or "NULL"
            closes = [dict(row) for row in db.execute(
                f"SELECT * FROM orders WHERE position_id IN ({pm}) AND close_reason IS NOT NULL", pids
            )]
            cids = [row["id"] for row in closes]
            close_fills = [dict(row) for row in db.execute(
                "SELECT * FROM fills WHERE order_id IN (SELECT value FROM json_each(?))", (json.dumps(cids),)
            )]
            events = [dict(row) for row in db.execute(
                f"SELECT * FROM position_close_events WHERE position_id IN ({pm}) AND is_partial=0", pids
            )]
            cash = [dict(row) for row in db.execute(
                f"SELECT * FROM paper_account_events WHERE position_id IN ({pm}) "
                "AND action IN ('position_closed','manual_credit','manual_debit')", pids
            )]
            for plan_id in batch:
                item = {"orders": [], "fills": None, "position": None,
                        "close_orders": [], "close_fills": [],
                        "entry_outcomes": [],
                        "availability": {"available": True, "reason": None},
                        "economics": {"status": "not_applicable", "reason": None},
                        "execution_state": "NOT_STARTED"}
                result[plan_id] = item
                plan = plans.get(plan_id)
                related = [o for o in orders.values() if o.get("entry_plan_id") == plan_id and not o.get("close_reason")]
                order = orders.get(plan.get("order_id")) if plan else None
                reason = None
                if plan is None:
                    reason = "entry_plan_missing"
                elif plan.get("order_id") and order is None:
                    reason = "linked_order_missing"
                elif order and related and order["id"] not in {o["id"] for o in related}:
                    reason = "order_relation_ambiguous"
                elif order and order.get("entry_plan_id") not in (None, plan_id):
                    reason = "order_relation_conflict"
                if not related and order and not reason:
                    related = [order]
                related.sort(key=lambda o: ((proposals.get(o.get("proposal_id")) or {}).get("entry_leg_index") or 0,
                                           str(o["created_at"]), o["id"]))
                if related and not reason:
                    payload = json.loads(plan["plan_json"])
                    legs = [(proposals.get(o.get("proposal_id")) or {}).get("entry_leg_index") for o in related]
                    if ((payload.get("capture_policy_id") and legs not in ([0], [0, 1]))
                        or (not payload.get("capture_policy_id") and len(related) > 1)):
                        reason = "order_relation_ambiguous"
                for linked in related if not reason else []:
                    proposal = proposals.get(linked.get("proposal_id"))
                    if (proposal is None or proposal.get("entry_plan_id") != plan_id
                        or normalize_market_type(linked.get("market_type")) != plan["market_type"]
                        or to_canonical(str(linked.get("symbol") or "")) != plan["symbol"]
                        or linked.get("direction") != plan["direction"]):
                        reason = "order_proposal_identity_conflict"
                order = related[-1] if related else None
                if reason:
                    item.update(availability={"available": False, "reason": reason}, execution_state="UNAVAILABLE")
                    continue
                if order is None:
                    if plan["status"] in {"ordered", "partially_filled", "filled"}:
                        item.update(availability={"available": False, "reason": "expected_order_missing"}, execution_state="UNAVAILABLE")
                    continue
                entry_order_ids = {o["id"] for o in related}
                of = [f for f in fills if f["order_id"] in entry_order_ids]
                quantity = sum(float(f["fill_quantity"]) for f in of)
                item["orders"] = [{**{k: linked.get(k) for k in (
                    "id", "proposal_id", "risk_decision_id", "exchange_order_id", "status", "quantity",
                    "position_id", "created_at", "filled_at", "order_tactic", "error_reason")},
                    "entry_leg_index": proposals[linked["proposal_id"]].get("entry_leg_index")}
                    for linked in related]
                item["fills"] = {"ids": [f["id"] for f in of], "count": len(of), "quantity": quantity,
                                 "latest_at": max((str(f["filled_at"]) for f in of), default=None)}
                for fill in of:
                    matching = [outcome for outcome in outcomes if outcome["outcome_candidate_key"] == f"entry-fill:{fill['id']}"]
                    summary = {"fill_id": fill["id"], "horizon": "4h", "status": "unavailable", "reason": "fixed_horizon_outcome_missing"}
                    if len(matching) == 1:
                        outcome = matching[0]
                        summary.update(id=outcome["id"], due_at=outcome.get("due_at"), settled_at=outcome.get("settled_at"))
                        exact = all(outcome.get(key) == plan.get(key) for key in ("venue", "symbol", "market_type", "direction"))
                        try:
                            exact = exact and _instant(outcome["entry_ts"]) == _instant(fill["filled_at"])
                            ready = _instant(outcome["due_at"]) <= cutoff and _instant(outcome["settled_at"]) <= cutoff
                        except (TypeError, ValueError):
                            ready = False
                        economics = validated_outcome_economics(outcome) if exact and ready else None
                        if exact and outcome["status"] == "pending":
                            summary.update(status="pending", reason="fixed_horizon_pending")
                        elif economics is not None and outcome["status"] == "settled":
                            summary.update(status="complete", reason=None, economics=economics)
                        else:
                            summary["reason"] = "fixed_horizon_evidence_unavailable"
                    item["entry_outcomes"].append(summary)
                entry_position_ids = {o["position_id"] for o in related if o.get("position_id")}
                ps = [p for p in positions.values() if p["id"] in entry_position_ids or p.get("order_id") in entry_order_ids]
                if len(ps) > 1 or (quantity > 0 and not ps):
                    item.update(availability={"available": False, "reason": "position_relation_unavailable"}, execution_state="UNAVAILABLE")
                    continue
                if not ps:
                    item["execution_state"] = ("CANCEL_PENDING" if order["status"] == "cancel_pending" else
                        "NO_FILL" if order["status"] in {"cancelled", "rejected", "failed"} and quantity == 0 else "ORDER_WORKING")
                    continue
                if not of:
                    item.update(availability={"available": False, "reason": "entry_fill_relation_missing"}, execution_state="UNAVAILABLE")
                    continue
                position = ps[0]
                root = next((o for o in related if o["id"] == position.get("order_id")), None)
                if (root is None or position.get("proposal_id") != root.get("proposal_id")
                    or position.get("symbol") != order.get("symbol")
                    or normalize_market_type(position.get("market_type")) != plan["market_type"]
                    or position.get("direction") != order.get("direction")
                    or position.get("execution_mode") != order.get("execution_mode")):
                    item.update(availability={"available": False, "reason": "position_proposal_conflict"}, execution_state="UNAVAILABLE")
                    continue
                item["position"] = {k: position.get(k) for k in (
                    "id", "status", "quantity", "entry_price", "opened_at", "closed_at", "pending_close_order_id",
                    "pending_close_status", "outcome_eligible", "outcome_exclusion_reason")}
                pc = [c for c in closes if c["position_id"] == position["id"]]
                pci = {c["id"] for c in pc}
                item["close_orders"] = [{k: c.get(k) for k in ("id", "status", "quantity", "created_at", "filled_at")} for c in pc]
                item["close_fills"] = [{k: f.get(k) for k in ("id", "order_id", "fill_quantity", "filled_at")} for f in close_fills if f["order_id"] in pci]
                if position["status"] == "open":
                    item["execution_state"] = "POSITION_CLOSING" if position.get("pending_close_order_id") or position.get("pending_close_status") else "POSITION_OPEN"
                    continue
                exclusion = str(position.get("outcome_exclusion_reason") or "")
                if exclusion in {"funding_permanently_unrecoverable", "invalid_exit_price_evidence"}:
                    item["economics"] = {"status": "excluded", "reason": exclusion}
                    item["execution_state"] = "OUTCOME_EXCLUDED"
                    continue
                pnl = _realized_economics_value(position)
                if pnl is None:
                    item["economics"] = {"status": "pending", "reason": "costs_incomplete"}
                    item["execution_state"] = "CLOSED_PENDING_ECONOMICS"
                    continue
                pe = [e for e in events if e["position_id"] == position["id"]]
                ce = [e for e in cash if e["position_id"] == position["id"]]
                if position["execution_mode"] == "paper_internal" and (
                    len(pe) != 1 or pe[0].get("realized_pnl") is None
                    or not math.isclose(float(pe[0]["realized_pnl"]), pnl, rel_tol=0, abs_tol=1e-8)
                    or not any(e["action"] == "position_closed" for e in ce)
                    or not math.isclose(sum(float(e["amount_usdt"]) for e in ce), pnl, rel_tol=0, abs_tol=1e-8)
                ):
                    item["economics"] = {"status": "unavailable", "reason": "settlement_projection_inconsistent"}
                    item["execution_state"] = "UNAVAILABLE"
                elif not item["close_fills"]:
                    item["economics"] = {"status": "unavailable", "reason": "close_fill_relation_missing"}
                    item["execution_state"] = "UNAVAILABLE"
                else:
                    item["economics"] = {"status": "complete", "reason": None}
                    item["execution_state"] = "CLOSED"
    return result



def get_entry_plan(plan_id: str) -> dict | None:
    from aletheia.storage.repositories import (
        _select_one,
    )

    return _select_one("entry_plans", plan_id)



def list_active_entry_plans(limit: int = 100) -> list[dict]:
    from aletheia.storage.repositories import (
        _ACTIVE_ENTRY_PLAN_STATUSES,
        get_db,
    )

    placeholders = ",".join("?" for _ in _ACTIVE_ENTRY_PLAN_STATUSES)
    return [
        dict(row)
        for row in get_db()
        .execute(
            f"SELECT * FROM entry_plans WHERE status IN ({placeholders}) ORDER BY updated_at DESC, id DESC LIMIT ?",
            (*_ACTIVE_ENTRY_PLAN_STATUSES, max(1, min(int(limit), 200))),
        )
        .fetchall()
    ]



def list_entry_plans(status: str | None = None, limit: int = 100) -> list[dict]:
    """List entry plans filtered by status ('active', 'expired', other, or all)."""
    from aletheia.storage.repositories import (
        _ACTIVE_ENTRY_PLAN_STATUSES,
        get_db,
    )

    norm_status = (status or "active").strip().lower()
    lim = max(1, min(int(limit), 200))
    if norm_status == "active":
        placeholders = ",".join("?" for _ in _ACTIVE_ENTRY_PLAN_STATUSES)
        query = f"SELECT * FROM entry_plans WHERE status IN ({placeholders}) ORDER BY updated_at DESC, id DESC LIMIT ?"
        params = (*_ACTIVE_ENTRY_PLAN_STATUSES, lim)
    elif norm_status == "expired":
        query = "SELECT * FROM entry_plans WHERE status = 'expired' ORDER BY updated_at DESC, id DESC LIMIT ?"
        params = (lim,)
    elif norm_status == "all":
        query = "SELECT * FROM entry_plans ORDER BY updated_at DESC, id DESC LIMIT ?"
        params = (lim,)
    else:
        query = "SELECT * FROM entry_plans WHERE status = ? ORDER BY updated_at DESC, id DESC LIMIT ?"
        params = (norm_status, lim)
    return [dict(row) for row in get_db().execute(query, params).fetchall()]



def list_watchable_entry_plans(limit: int | None = 100) -> list[dict]:
    from aletheia.storage.repositories import (
        _select_all,
    )

    return _select_all(
        "entry_plans",
        where=("status IN ('waiting','active','triggered','risk_reserved','ordered','partially_filled')"),
        order="created_at ASC",
        limit=limit,
    )



def watchable_entry_plan_high_watermark() -> tuple[str, str] | None:
    from aletheia.storage.repositories import (
        get_db,
    )

    row = (
        get_db()
        .execute(
            "SELECT created_at, id FROM entry_plans "
            "WHERE status IN ('waiting','active','triggered','risk_reserved','ordered','partially_filled') "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        )
        .fetchone()
    )
    return (str(row["created_at"]), str(row["id"])) if row else None



def list_watchable_entry_plans_page(
    *,
    after: tuple[str, str] | None,
    through: tuple[str, str],
    limit: int = 100,
) -> list[dict]:
    from aletheia.storage.repositories import (
        _select_all,
    )

    where = [
        "status IN ('waiting','active','triggered','risk_reserved','ordered','partially_filled')",
        "(created_at < ? OR (created_at = ? AND id <= ?))",
    ]
    params: list[str] = [through[0], through[0], through[1]]
    if after is not None:
        where.append("(created_at > ? OR (created_at = ? AND id > ?))")
        params.extend((after[0], after[0], after[1]))
    return _select_all(
        "entry_plans",
        where=" AND ".join(where),
        params=tuple(params),
        order="created_at ASC, id ASC",
        limit=limit,
    )



def get_entry_trigger_activation(activation_id: str) -> dict | None:
    from aletheia.storage.repositories import (
        get_db,
    )

    row = get_db().execute("SELECT * FROM entry_trigger_activations WHERE id=?", (activation_id,)).fetchone()
    return dict(row) if row else None



def entry_plan_activation_can_refresh(plan_id: str, *, now: datetime | None = None) -> bool:
    """A new trigger can refresh an unsubmitted intent, never an exchange handoff."""
    from aletheia.storage.repositories import (
        _select_all,
        get_order_for_entry_plan,
        get_proposal_execution_claim,
        has_order_for_proposal,
        proposal_execution_claim_is_fresh,
    )

    if get_order_for_entry_plan(plan_id) is not None:
        return False
    proposals = _select_all("trade_proposals", where="entry_plan_id=?", params=(plan_id,), limit=None)
    for proposal in proposals:
        claim = get_proposal_execution_claim(proposal["id"])
        if (proposal["status"] not in {"pending", "approved"}
            or has_order_for_proposal(proposal["id"])
            or (claim is not None and proposal_execution_claim_is_fresh(claim, now or datetime.now(timezone.utc)))):
            return False
    return True
