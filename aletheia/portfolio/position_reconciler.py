"""Position reconciler — discover, classify, and (later) adopt account positions.

Aletheia normally manages only positions it opened (proposal → risk → order →
position). But the exchange account may hold positions Aletheia did NOT open:
pre-existing positions at takeover, or anything opened outside the system. Those
were invisible to management — `exchange_sync` cached them read-only and only a
bare drift *count* reached the UI.

This module joins the cached exchange position view against the local positions
table and classifies every position so the rest of the system (UI, scheduler,
operator commands) can act on the gaps:

  - managed  : tracked locally AND present on the exchange — already under control.
  - external : on the exchange, NOT tracked locally — an adoption candidate.
  - orphaned : open locally, NOT on the exchange — a reconcile-close candidate
               (our books say open; the exchange says gone).

Classification is PURE and READ-ONLY (no exchange call, no DB write) — it reads the
exchange cache and the local positions table. Adoption and orphan-close (which DO
write local rows) live in later phases and are clearly separated.

Match identity is the PAIR (canonical symbol, direction), never the symbol alone:
the demo account runs hedge mode and can hold SOL long AND SOL short at the same
time. Quantity is compared within a tolerance because the exchange aggregates fills
(e.g. paper_trader averaging in) and may drift slightly from the local sum.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from aletheia.data.symbols import to_canonical
from aletheia.portfolio import exchange_sync
from aletheia.storage.repositories import list_positions

logger = logging.getLogger("aletheia.position_reconciler")

# Relative quantity tolerance when matching an exchange position to a local one.
# The exchange aggregates fills, so local-vs-exchange size can drift a little.
_QTY_REL_TOL = 0.02  # 2%

# Adopted positions inherit the honest execution_mode of the account they live on,
# which also drives management: position_manager auto-manages bitget_demo and SKIPS
# bitget_live_limited (live stays human-only). Mapping the exchange account mode to
# the execution_mode is the single point where the demo-auto / live-human split is set.
_MODE_TO_EXECUTION = {
    "demo": "bitget_demo",
    "live": "bitget_live_limited",
}


def _norm_direction(side: str) -> str:
    """Normalise a side/direction to 'long' or 'short'."""
    s = (side or "").lower()
    if s in ("long", "buy"):
        return "long"
    if s in ("short", "sell"):
        return "short"
    return s


def _key(symbol: str, direction: str) -> tuple[str, str]:
    """Match identity: (canonical symbol, normalised direction)."""
    return (to_canonical(symbol), _norm_direction(direction))


@dataclass
class ReconcileItem:
    """One classified position. `local`/`exchange` carry the source rows when present."""

    symbol: str
    direction: str
    classification: str  # "managed" | "external" | "orphaned"
    local: Optional[dict] = None
    exchange: Optional[dict] = None
    qty_drift: Optional[float] = None  # exchange_qty - local_qty, when both present

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "classification": self.classification,
            "local": self.local,
            "exchange": self.exchange,
            "qty_drift": self.qty_drift,
        }


@dataclass
class ReconcileResult:
    managed: list[ReconcileItem] = field(default_factory=list)
    external: list[ReconcileItem] = field(default_factory=list)
    orphaned: list[ReconcileItem] = field(default_factory=list)
    sync_status: str = "unconfigured"

    def to_dict(self) -> dict:
        return {
            "sync_status": self.sync_status,
            "counts": {
                "managed": len(self.managed),
                "external": len(self.external),
                "orphaned": len(self.orphaned),
            },
            "managed": [i.to_dict() for i in self.managed],
            "external": [i.to_dict() for i in self.external],
            "orphaned": [i.to_dict() for i in self.orphaned],
        }


def classify_positions(mode: Optional[str] = None) -> ReconcileResult:
    """Classify every position on the account as managed / external / orphaned.

    Pure read-only join of the cached exchange position view against the local
    open positions table. When the exchange cache is unavailable (unconfigured /
    error / never-synced), every local open position is reported as `managed` and
    `sync_status` reflects why — we never infer `orphaned` from a missing cache,
    because "no exchange data" must not look like "the exchange has no positions".
    """
    cached = exchange_sync.get_cached_state(mode)
    sync_status = exchange_sync.status_for(cached)

    local_open = list_positions(status="open")
    local_by_key: dict[tuple[str, str], dict] = {}
    for p in local_open:
        local_by_key[_key(p["symbol"], p["direction"])] = p

    result = ReconcileResult(sync_status=sync_status)

    # Without a usable exchange view we cannot classify externals/orphans honestly.
    # Report locals as managed and bail — the caller sees sync_status and knows why.
    if cached is None or sync_status not in ("ok", "stale"):
        for p in local_open:
            result.managed.append(
                ReconcileItem(
                    symbol=to_canonical(p["symbol"]),
                    direction=_norm_direction(p["direction"]),
                    classification="managed",
                    local=p,
                )
            )
        return result

    exchange_positions = cached.get("positions") or []
    exchange_by_key: dict[tuple[str, str], dict] = {}
    for ep in exchange_positions:
        # Skip zero-quantity rows the exchange sometimes returns for closed slots.
        if float(ep.get("quantity") or 0) <= 0:
            continue
        exchange_by_key[_key(ep.get("symbol", ""), ep.get("side", ""))] = ep

    # managed + external: walk the exchange (the source of truth for what exists).
    for k, ep in exchange_by_key.items():
        symbol, direction = k
        lp = local_by_key.get(k)
        if lp is not None:
            ex_qty = float(ep.get("quantity") or 0)
            lo_qty = float(lp.get("quantity") or 0)
            result.managed.append(
                ReconcileItem(
                    symbol=symbol,
                    direction=direction,
                    classification="managed",
                    local=lp,
                    exchange=ep,
                    qty_drift=round(ex_qty - lo_qty, 8),
                )
            )
        else:
            result.external.append(
                ReconcileItem(
                    symbol=symbol,
                    direction=direction,
                    classification="external",
                    exchange=ep,
                )
            )

    # orphaned: local open positions with no matching exchange position.
    for k, lp in local_by_key.items():
        if k not in exchange_by_key:
            symbol, direction = k
            result.orphaned.append(
                ReconcileItem(
                    symbol=symbol,
                    direction=direction,
                    classification="orphaned",
                    local=lp,
                )
            )

    return result


def adopt_position(exchange_pos: dict, account_mode: str) -> dict:
    """Adopt one external exchange position into the local books under management.

    Builds a synthetic-but-honest causal chain — proposal → order(filled) → position
    — all tagged `source="adopted"`, in a single transaction. This is the deliberate
    amendment to trust §1.1 (see docs/DECISIONS.md 2026-06-12): an external position
    DOES enter the local table, but only with a complete, truthfully-labelled chain so
    the position→proposal→order links stay non-null and auditable. The synthetic
    proposal/order assert "Aletheia adopted a pre-existing position", NOT "Aletheia
    decided to open this trade" — the `adopted` source and rationale make that explicit.

    The position's `execution_mode` is derived from the account mode (demo →
    bitget_demo, live → bitget_live_limited). That single mapping is what makes
    position_manager auto-manage adopted DEMO positions and SKIP adopted LIVE ones —
    live management stays human-only by construction.

    Entry price and quantity come from the exchange (the truth); no exit/PnL is
    realized here. Returns the created ids. Raises ValueError on bad input.
    """
    from aletheia.storage.models import TradeProposal, Order, Position
    from aletheia.storage.repositories import (
        transaction, insert_trade_proposal, insert_order, insert_position,
    )
    from aletheia.files.jsonl import write_audit

    execution_mode = _MODE_TO_EXECUTION.get(account_mode)
    if execution_mode is None:
        raise ValueError(f"adopt_position: unknown account_mode {account_mode!r}")

    symbol = to_canonical(exchange_pos.get("symbol", ""))
    direction = _norm_direction(exchange_pos.get("side", ""))
    quantity = float(exchange_pos.get("quantity") or 0)
    entry_price = float(exchange_pos.get("entry_price") or 0)
    if not symbol or direction not in ("long", "short"):
        raise ValueError(f"adopt_position: bad symbol/direction ({symbol!r}, {direction!r})")
    if quantity <= 0 or entry_price <= 0:
        # Never synthesize a chain off a missing size/entry — that would fabricate
        # the very causal facts this design exists to keep honest.
        raise ValueError(
            f"adopt_position: refusing to adopt {symbol} {direction} with "
            f"quantity={quantity} entry_price={entry_price} (need both > 0)"
        )

    # Adopted positions are USDT-futures mix positions on Bitget.
    market_type = "usdt_futures"
    now = datetime.now(timezone.utc).isoformat()
    rationale = (
        f"Adopted pre-existing {execution_mode} position {symbol} {direction} "
        f"qty={quantity} @ {entry_price} into management (source=adopted). Not a "
        f"newly-decided trade; brought under management at takeover/reconcile."
    )

    proposal = TradeProposal(
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=entry_price,
        execution_mode=execution_mode,
        agent_rationale=rationale,
        status="executed",
        source="adopted",
    )
    order = Order(
        proposal_id=proposal.id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=entry_price,
        execution_mode=execution_mode,
        status="filled",
        source="adopted",
        filled_at=now,
    )
    position = Position(
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        current_price=float(exchange_pos.get("mark_price") or 0) or None,
        unrealized_pnl=float(exchange_pos.get("unrealized_pnl") or 0),
        execution_mode=execution_mode,
        proposal_id=proposal.id,
        order_id=order.id,
        status="open",
        source="adopted",
        opened_at=now,
    )

    with transaction():
        insert_trade_proposal(proposal)
        insert_order(order)
        insert_position(position)

    write_audit({
        "actor": "system",
        "source": "position_reconciler",
        "event_type": "position_adopted",
        "entity_type": "position",
        "entity_id": position.id,
        "after": {
            "symbol": symbol, "direction": direction, "quantity": quantity,
            "entry_price": entry_price, "execution_mode": execution_mode,
            "source": "adopted", "proposal_id": proposal.id, "order_id": order.id,
        },
        "message": rationale,
    })

    return {
        "position_id": position.id,
        "proposal_id": proposal.id,
        "order_id": order.id,
        "execution_mode": execution_mode,
        "symbol": symbol,
        "direction": direction,
    }
