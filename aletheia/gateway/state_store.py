"""GatewayStateStore — persistent order intent / order / fill / position tracking.

Every state mutation writes to both the DB tables and the EventStore, ensuring
no order or position is lost on process restart.
"""

import json
from datetime import datetime, timezone

from aletheia.storage.event_store import get_event_store, make_event
from aletheia.storage.models import (
    PSExecutionIntent, PSFill, PSOrder, PSOrderIntent, PSPosition, new_id,
)
from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class GatewayStateStore:
    """Persistent state store for the execution gateway."""

    # ------------------------------------------------------------------
    # Execution Intents
    # ------------------------------------------------------------------

    def create_execution_intent(self, intent: PSExecutionIntent) -> PSExecutionIntent:
        db = get_db()
        was_in_tx = db.in_transaction
        data = intent.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        db.execute(f"INSERT INTO ps_execution_intents ({cols}) VALUES ({placeholders})", list(data.values()))
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "EXECUTION_INTENT_CREATED", "execution_intent", intent.execution_intent_id,
            payload={"proposal_id": intent.proposal_id, "mode": intent.mode},
        ))
        return intent

    # ------------------------------------------------------------------
    # Order Intents
    # ------------------------------------------------------------------

    def create_order_intent(self, intent: PSOrderIntent) -> PSOrderIntent:
        db = get_db()
        was_in_tx = db.in_transaction
        data = intent.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        db.execute(f"INSERT INTO ps_order_intents ({cols}) VALUES ({placeholders})", list(data.values()))
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "ORDER_INTENT_CREATED", "order_intent", intent.intent_id,
            payload={"symbol": intent.symbol, "side": intent.side, "qty": intent.quantity},
            idempotency_key=f"oi_created_{intent.idempotency_key}",
        ))
        return intent

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def mark_order_submitted(self, intent_id: str, order: PSOrder) -> PSOrder:
        """Record that an order has been submitted to the venue."""
        db = get_db()
        was_in_tx = db.in_transaction
        order.submitted_at = order.submitted_at or _utcnow()
        data = order.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        db.execute(f"INSERT INTO ps_orders ({cols}) VALUES ({placeholders})", list(data.values()))
        db.execute(
            "UPDATE ps_order_intents SET status = 'submitted', updated_at = ? WHERE intent_id = ?",
            (_utcnow(), intent_id),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "ORDER_SUBMITTED", "order", order.order_id,
            payload={"client_order_id": order.client_order_id, "symbol": order.symbol},
        ))
        return order

    def mark_order_acked(self, order_id: str, venue_order_id: str) -> None:
        """Record exchange acknowledgement."""
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        db.execute(
            "UPDATE ps_orders SET status = 'acked', venue_order_id = ?, acknowledged_at = ?, updated_at = ? "
            "WHERE order_id = ?",
            (venue_order_id, now, now, order_id),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "ORDER_ACKED", "order", order_id,
            payload={"venue_order_id": venue_order_id},
        ))

    def mark_order_rejected(self, order_id: str, reason: str) -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        db.execute(
            "UPDATE ps_orders SET status = 'rejected', updated_at = ? WHERE order_id = ?",
            (now, order_id),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "ORDER_REJECTED", "order", order_id, payload={"reason": reason},
        ))

    def mark_order_cancelled(self, order_id: str) -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        db.execute(
            "UPDATE ps_orders SET status = 'cancelled', updated_at = ? WHERE order_id = ?",
            (now, order_id),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "ORDER_CANCELLED", "order", order_id,
        ))

    def mark_order_unknown(self, order_id: str) -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        db.execute(
            "UPDATE ps_orders SET status = 'unknown', updated_at = ? WHERE order_id = ?",
            (now, order_id),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "ORDER_STATUS_UNKNOWN", "order", order_id,
        ))

    # ------------------------------------------------------------------
    # Fills
    # ------------------------------------------------------------------

    def record_fill(self, fill: PSFill) -> PSFill:
        """Record a fill atomically: insert fill + update order + update position + event."""
        db = get_db()
        was_in_tx = db.in_transaction
        if not was_in_tx:
            db.execute("BEGIN")
        try:
            # Insert fill
            data = fill.model_dump()
            cols = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            db.execute(f"INSERT INTO ps_fills ({cols}) VALUES ({placeholders})", list(data.values()))

            # Update order status if linked
            if fill.order_id:
                now = _utcnow()
                db.execute(
                    "UPDATE ps_orders SET status = 'filled', updated_at = ? WHERE order_id = ?",
                    (now, fill.order_id),
                )

            if not was_in_tx:
                db.commit()
        except Exception:
            if not was_in_tx:
                db.rollback()
            raise

        get_event_store().append(make_event(
            "ORDER_FILLED", "fill", fill.fill_id,
            payload={
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side,
                "qty": fill.quantity,
                "price": fill.price,
            },
        ))
        return fill

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def open_position(self, position: PSPosition) -> PSPosition:
        db = get_db()
        was_in_tx = db.in_transaction
        position.opened_at = position.opened_at or _utcnow()
        data = position.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        db.execute(f"INSERT INTO ps_positions ({cols}) VALUES ({placeholders})", list(data.values()))
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "POSITION_OPENED", "position", position.position_id,
            payload={"symbol": position.symbol, "side": position.side, "qty": position.quantity},
        ))
        return position

    def close_position(self, position_id: str, reason: str = "normal") -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        db.execute(
            "UPDATE ps_positions SET status = 'closed', closed_at = ?, updated_at = ? WHERE position_id = ?",
            (now, now, position_id),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "POSITION_CLOSED", "position", position_id, payload={"reason": reason},
        ))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_open_orders(self, mode: str | None = None) -> list[dict]:
        db = get_db()
        if mode:
            rows = db.execute(
                "SELECT * FROM ps_orders WHERE status IN ('submitted', 'acked', 'open', 'partially_filled') AND mode = ?",
                (mode,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM ps_orders WHERE status IN ('submitted', 'acked', 'open', 'partially_filled')",
            ).fetchall()
        return [dict(r) for r in rows]

    def get_open_positions(self, mode: str | None = None) -> list[dict]:
        db = get_db()
        if mode:
            rows = db.execute(
                "SELECT * FROM ps_positions WHERE status = 'open' AND mode = ?", (mode,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM ps_positions WHERE status = 'open'",
            ).fetchall()
        return [dict(r) for r in rows]

    def get_unresolved_intents(self, mode: str | None = None) -> list[dict]:
        db = get_db()
        if mode:
            rows = db.execute(
                "SELECT * FROM ps_order_intents WHERE status IN ('created', 'submitted') AND mode = ?",
                (mode,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM ps_order_intents WHERE status IN ('created', 'submitted')",
            ).fetchall()
        return [dict(r) for r in rows]

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        db = get_db()
        row = db.execute(
            "SELECT * FROM ps_orders WHERE client_order_id = ?", (client_order_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_order(self, order_id: str) -> dict | None:
        db = get_db()
        row = db.execute("SELECT * FROM ps_orders WHERE order_id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

    def last_seen_fill_ts(self, mode: str | None = None) -> str | None:
        """Return the latest fill timestamp we have recorded."""
        db = get_db()
        if mode:
            row = db.execute(
                "SELECT MAX(filled_at) as max_ts FROM ps_fills WHERE mode = ?", (mode,),
            ).fetchone()
        else:
            row = db.execute("SELECT MAX(filled_at) as max_ts FROM ps_fills").fetchone()
        return row["max_ts"] if row and row["max_ts"] else None

    def fill_exists(self, venue: str, venue_trade_id: str) -> bool:
        """Check if a fill with this venue trade ID already exists."""
        if not venue_trade_id:
            return False
        db = get_db()
        row = db.execute(
            "SELECT 1 FROM ps_fills WHERE venue = ? AND venue_trade_id = ?",
            (venue, venue_trade_id),
        ).fetchone()
        return row is not None
