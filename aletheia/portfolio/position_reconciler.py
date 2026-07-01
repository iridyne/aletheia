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
_LIVE_EXECUTION_MODE = "bitget_live_limited"


def _execution_mode_for(account_mode: str) -> str | None:
    """Resolve execution_mode from account mode, respecting paper_execution_mode config."""
    if account_mode == "live":
        return _LIVE_EXECUTION_MODE
    if account_mode == "demo":
        from aletheia.config.loader import load_config
        return load_config().exchange.paper_execution_mode
    return None


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
        # market_type lets the frontend render the symbol per the spot/derivative
        # convention (perp → no slash, spot → slash). Prefer the local row's type;
        # fall back to the exchange row's when this position is exchange-only.
        market_type = None
        if self.local:
            market_type = self.local.get("market_type")
        if not market_type and self.exchange:
            market_type = self.exchange.get("market_type")
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "classification": self.classification,
            "market_type": market_type,
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
    # Prefer unified (UTA) state — covers all brokers (OKX/Bitget/Binance).
    # Fall back to legacy Bitget-only cache for backwards compat.
    cached = None
    unified = exchange_sync.get_unified_cached_state()
    if unified is not None:
        age = unified.get("age_seconds")
        if age is not None and age < 600:
            cached = {
                "positions": unified.get("positions") or [],
                "age_seconds": age,
                "fetched_at": unified.get("fetched_at", ""),
            }
    if cached is None:
        # No fresh multi-broker (unified) view. The legacy per-mode cache is
        # written ONLY by the Bitget sync, so falling back to it is safe only when
        # the active paper broker IS Bitget. On a non-Bitget account (e.g.
        # okx_demo) that cache is fresh-but-EMPTY of this account's positions, so
        # classifying against it marks every real position `orphaned` →
        # reconcile-close (local only) → the position survives on the exchange →
        # re-adopt next cycle → an endless false-orphan thrash (41% of positions
        # closed reconciled_gone, 56% re-adopted, strategy attribution destroyed).
        # For a non-Bitget account with no usable unified view, leave `cached`
        # None so the guard below reports all-managed and bails — never infer
        # orphans from a wrong-broker cache. Live keeps the legacy path (live
        # reconcile is alert-only and Bitget-based today). See
        # docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md.
        from aletheia.config.loader import load_config
        _active_is_bitget = (load_config().exchange.paper_execution_mode or "").startswith("bitget")
        if mode == "live" or _active_is_bitget:
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

    execution_mode = _execution_mode_for(account_mode)
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
    # Prefer exchange-provided open time for accurate max_hold calculation
    ctime_ms = exchange_pos.get("cTime") or exchange_pos.get("created_at")
    if ctime_ms:
        try:
            opened_at = datetime.fromtimestamp(int(ctime_ms) / 1000, tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OSError):
            opened_at = now
    else:
        opened_at = now
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
    # Compute default protective stop and take-profit at adoption time so the
    # position row is never left with NULL stop/tp. The values are derived from
    # the config defaults (same percentages position_manager uses at runtime) but
    # are written once and locked — if the user later changes the defaults the
    # adopted position keeps its original levels, preventing silent SL drift.
    from aletheia.config.loader import load_config as _load_cfg
    _cfg = _load_cfg()
    _sl_pct = _cfg.trading.default_stop_loss_pct   # e.g. 2.0
    _tp_pct = _cfg.trading.default_take_profit_pct  # e.g. 4.0
    _is_long = direction in ("long", "buy")
    _adopted_stop = (
        round(entry_price * (1 - _sl_pct / 100.0), 8) if _is_long
        else round(entry_price * (1 + _sl_pct / 100.0), 8)
    ) if _sl_pct else None
    _adopted_tp = (
        round(entry_price * (1 + _tp_pct / 100.0), 8) if _is_long
        else round(entry_price * (1 - _tp_pct / 100.0), 8)
    ) if _tp_pct else None

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
        opened_at=opened_at,
        stop_price=_adopted_stop,
        take_profit_price=_adopted_tp,
        # Adopted positions are price-action holdings by definition (the exchange
        # already opened them outside of any Serenity research thesis), so stamp
        # the short_term school explicitly. The Pydantic default would also pick
        # this up, but the explicit assignment keeps intent visible at the call
        # site and satisfies the SDD propagation rule (see spec_serenity_holding
        # SH-S05: ALL FOUR brokers + reconciler must propagate trade_school).
        trade_school="short_term",
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
            "stop_price": _adopted_stop, "take_profit_price": _adopted_tp,
            "stop_source": "adopted_default_pct",
            # P1-2 (2026-06-29, OpenAlice TaG borrow): chain the reconcile event
            # into the proposal hash chain. proposal.commit_hash + parent_hash
            # were filled in-place by insert_trade_proposal() inside the
            # transaction block above, so they're available verbatim here. See
            # PL-ADOPT-09 and docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md.
            "commit_hash": proposal.commit_hash,
            "parent_hash": proposal.parent_hash,
        },
        "message": rationale,
    })
    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            title=f"Position Adopted: {symbol}",
            message=f"{direction} qty={quantity} @ {entry_price:.4f} ({execution_mode})",
            level="info",
            event_type="trade_notification",
            entity_type="position",
            entity_id=position.id,
            error_log=False,
        )
    except Exception:
        pass

    return {
        "position_id": position.id,
        "proposal_id": proposal.id,
        "order_id": order.id,
        "execution_mode": execution_mode,
        "symbol": symbol,
        "direction": direction,
    }


def _try_get_tpsl_fill_price(local: dict) -> tuple[float | None, str | None]:
    """Attempt to find the real fill price from a triggered TPSL order.

    Reads exchange_tpsl_order_id from the position, queries Bitget plan order
    history, and returns (fill_price, close_reason) if a triggered order is found.
    Returns (None, None) on any failure (non-critical).
    """
    import asyncio
    import json as _json

    tpsl_raw = local.get("exchange_tpsl_order_id")
    if not tpsl_raw:
        return None, None
    try:
        tpsl_ids = _json.loads(tpsl_raw) if isinstance(tpsl_raw, str) else tpsl_raw
    except (ValueError, TypeError):
        return None, None
    if not isinstance(tpsl_ids, dict):
        return None, None

    try:
        from aletheia.exchanges.bitget.client import BitgetClient
        client = BitgetClient()

        async def _query():
            for plan_type, order_id in [("sl", tpsl_ids.get("sl")), ("tp", tpsl_ids.get("tp"))]:
                if not order_id:
                    continue
                try:
                    history = await client.get_plan_order_history(order_id=order_id)
                    for h in history:
                        if h.get("orderId") == order_id and h.get("status") == "executed":
                            price = float(h.get("executePrice") or h.get("triggerPrice") or 0)
                            if price > 0:
                                reason = "stop_loss" if plan_type == "sl" else "take_profit"
                                return price, reason
                except Exception:
                    continue
            return None, None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _query()).result(timeout=10)
        else:
            return asyncio.run(_query())
    except Exception as e:
        logger.debug("TPSL fill price lookup failed (non-critical): %s", e)
        return None, None


def reconcile_close_orphan(item: ReconcileItem) -> dict:
    """Close an orphaned position (local open, exchange gone) via local bookkeeping.

    First attempts to find the real fill price from triggered TPSL orders on the
    exchange. Falls back to last known price (current_price or entry_price).
    Returns the close result dict from pnl.close_position.

    Only call on DEMO orphans. Live orphans are alert-only (caller enforces).
    """
    from aletheia.portfolio.pnl import close_position

    local = item.local
    if local is None:
        raise ValueError("reconcile_close_orphan: item has no local position")

    position_id = local["id"]

    # Try to get actual fill price from exchange TPSL history
    tpsl_price, tpsl_reason = _try_get_tpsl_fill_price(local)

    if tpsl_price and tpsl_price > 0:
        exit_price = tpsl_price
        close_reason = tpsl_reason or "reconciled_gone"
    else:
        exit_price = float(local.get("current_price") or local.get("entry_price") or 0)
        if exit_price <= 0:
            exit_price = float(local.get("entry_price") or 0)
        close_reason = "reconciled_gone"

    if exit_price <= 0:
        raise ValueError(
            f"reconcile_close_orphan: cannot close {position_id} — no price available"
        )

    result = close_position(position_id, exit_price, reason=close_reason)
    logger.info(
        "Reconcile-closed orphan %s %s %s @ %.6f (realized=%.4f, reason=%s, tpsl=%s)",
        position_id, item.symbol, item.direction, exit_price,
        result.get("realized_pnl", 0), close_reason,
        "exchange" if tpsl_price else "fallback",
    )
    return result


def run_reconcile(mode: str | None = None) -> dict:
    """Full reconcile cycle: classify → auto-adopt externals → close orphans (demo).

    Behavior depends on the exchange mode:
      - demo: auto-adopt externals (if auto_adopt_enabled) + reconcile-close orphans.
      - live: classify only + alert (never autonomous adoption or close).

    Returns a summary dict suitable for agent-run outputs / API response.
    """
    from aletheia.config.loader import load_config
    from aletheia.files.jsonl import write_audit

    cfg = load_config()
    account_mode = mode or cfg.exchange.mode

    result = classify_positions(account_mode)
    summary: dict = {
        "account_mode": account_mode,
        "sync_status": result.sync_status,
        "counts": {
            "managed": len(result.managed),
            "external": len(result.external),
            "orphaned": len(result.orphaned),
        },
        "adopted": [],
        "closed_orphans": [],
        "errors": [],
    }

    if account_mode != "demo":
        if result.external or result.orphaned:
            logger.warning(
                "Live reconcile: %d external, %d orphaned — alert only (no autonomous action)",
                len(result.external), len(result.orphaned),
            )
        return summary

    # Demo mode: auto-adopt + orphan close
    if cfg.trading.auto_adopt_enabled:
        for item in result.external:
            try:
                adopted = adopt_position(item.exchange, account_mode)
                summary["adopted"].append(adopted)
            except Exception as e:
                err = f"adopt {item.symbol} {item.direction}: {e}"
                logger.warning("Reconcile adopt failed: %s", err)
                summary["errors"].append(err)

    for item in result.orphaned:
        try:
            closed = reconcile_close_orphan(item)
            summary["closed_orphans"].append(closed)
        except Exception as e:
            err = f"close orphan {item.symbol} {item.direction}: {e}"
            logger.warning("Reconcile close failed: %s", err)
            summary["errors"].append(err)

    if summary["adopted"] or summary["closed_orphans"]:
        write_audit({
            "actor": "system",
            "source": "position_reconciler",
            "event_type": "reconcile_cycle",
            "entity_type": "portfolio",
            "message": (
                f"Reconcile: adopted={len(summary['adopted'])}, "
                f"closed_orphans={len(summary['closed_orphans'])}, "
                f"errors={len(summary['errors'])}"
            ),
            "after": {
                "adopted_ids": [a["position_id"] for a in summary["adopted"]],
                "closed_ids": [c["position_id"] for c in summary["closed_orphans"]],
            },
        })

    return summary
