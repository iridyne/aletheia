"""Paper broker — simulates order fills for paper/demo trading."""

from typing import Any
import json
import math
from datetime import datetime, timezone

from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    find_compatible_open_position,
    insert_order,
    update_order,
    insert_fill,
    insert_position,
    update_position,
)
from aletheia.execution.position_enrichment import liquidation_price_for as _liq_price


def _apply_slippage(price: float, direction: str, slippage_bps: float) -> float:
    """Apply adverse slippage: worse fill for the trader."""
    slip = slippage_bps / 10_000.0
    if direction in ("long", "buy"):
        return round(price * (1 + slip), 8)
    return round(price * (1 - slip), 8)


def execute_paper_order(
    proposal_id: str,
    risk_decision_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str,
    quantity: float,
    price: float,
    execution_mode: str = "paper_internal",
    strategy_id: str | None = None,
    stop_price: float | None = None,
    take_profit: float | None = None,
    leverage: int | None = None,
    opportunity_id: str | None = None,
    trade_school: str = "short_term",
    attribution_snapshot: dict[str, Any] | str | None = None,
    source: str = "native",
) -> dict:
    """Execute a paper order: create order, fill, and position.

    The protective stop_price and take_profit from the approved Proposal are
    recorded on a newly-opened position. Averaging into the same Proposal preserves
    its original stop/TP; a different Proposal always gets a separate lineage.
    """
    if execution_mode == "paper_internal" and order_type != "market":
        raise ValueError("paper_internal_market_only")

    from aletheia.config.loader import load_config
    cfg = load_config()
    slippage_bps = cfg.trading.slippage_bps
    fee_rate = cfg.trading.execution_fee_pct / 100.0

    fill_price = _apply_slippage(price, direction, slippage_bps)
    filled_at = datetime.now(timezone.utc).isoformat()

    order = Order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=fill_price,
        execution_mode=execution_mode,
        status="filled",
        filled_at=filled_at,
        source=source,
    )
    fill = Fill(
        order_id=order.id,
        fill_price=fill_price,
        fill_quantity=quantity,
        fee=quantity * fill_price * fee_rate,
        filled_at=filled_at,
    )
    position_id, filled_take_profit = _materialize_paper_entry(
        order=order,
        fill=fill,
        proposal_id=proposal_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        quantity=quantity,
        requested_price=price,
        fill_price=fill_price,
        fee_rate=fee_rate,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        stop_price=stop_price,
        take_profit=take_profit,
        leverage=leverage,
        opportunity_id=opportunity_id,
        trade_school=trade_school,
        attribution_snapshot=attribution_snapshot,
    )

    result = {
        "order_id": order.id,
        "fill_id": fill.id,
        "position_id": position_id,
        "fill_price": fill_price,
        "fill_quantity": quantity,
        # The Gateway owns the outer transaction that also marks the Proposal
        # executed. Return notification intent instead of publishing an event
        # while that transaction can still roll back.
        "_notification_payload": {
            "symbol": symbol,
            "direction": direction,
            "quantity": quantity,
            "fill_price": fill_price,
            "position_id": position_id,
            "execution_mode": execution_mode,
            "stop_price": stop_price,
            "take_profit_price": filled_take_profit,
        },
    }
    return result


def _materialize_paper_entry(
    *,
    order: Order,
    fill: Fill,
    proposal_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    quantity: float,
    requested_price: float,
    fill_price: float,
    fee_rate: float,
    execution_mode: str,
    strategy_id: str | None,
    stop_price: float | None,
    take_profit: float | None,
    leverage: int | None,
    opportunity_id: str | None,
    trade_school: str,
    attribution_snapshot: dict[str, Any] | str | None,
) -> tuple[str, float | None]:
    """Commit the local Order, Fill, Position, and cash event atomically."""
    from aletheia.storage import repositories as repo

    filled_take_profit: float | None = None
    with repo.transaction(immediate=True):
        insert_order(order)
        insert_fill(fill)
        existing = _find_open_position(
            symbol,
            market_type,
            direction,
            execution_mode,
            strategy_id,
            proposal_id,
        )
        if existing:
            new_qty = existing["quantity"] + quantity
            new_entry = (
                existing["entry_price"] * existing["quantity"]
                + fill_price * quantity
            ) / new_qty
            update_kwargs = {
                "quantity": new_qty,
                "entry_price": new_entry,
                "current_price": fill_price,
            }
            existing_lev = existing.get("leverage")
            if existing_lev and existing_lev > 1:
                update_kwargs["liquidation_price"] = _liq_price(
                    new_entry, direction, existing_lev, symbol
                )
            if stop_price is not None:
                old_stop = existing.get("stop_price")
                if old_stop is not None:
                    update_kwargs["stop_price"] = (
                        max(stop_price, old_stop)
                        if direction in ("long", "buy")
                        else min(stop_price, old_stop)
                    )
                else:
                    update_kwargs["stop_price"] = stop_price
            new_entry_fee = round(fill_price * quantity * fee_rate, 8)
            new_slippage_cost = round(
                abs(fill_price - requested_price) * quantity, 8
            )
            update_kwargs["entry_fee"] = round(
                (existing.get("entry_fee") or 0.0) + new_entry_fee, 8
            )
            update_kwargs["slippage_cost"] = round(
                (existing.get("slippage_cost") or 0.0) + new_slippage_cost, 8
            )
            update_position(existing["id"], **update_kwargs)
            position_id = existing["id"]
        else:
            # Protection belongs to the immutable Proposal/Risk authority. The
            # broker may materialize the approved value but must never invent a
            # new take-profit from mutable config after approval.
            take_profit_price = take_profit
            filled_take_profit = take_profit_price
            attribution = _paper_position_attribution(
                symbol, fill_price, attribution_snapshot
            )
            decision_reasoning = None
            try:
                proposal = repo._select_one("trade_proposals", proposal_id)
                if proposal:
                    decision_reasoning = proposal.get("agent_rationale")
            except Exception:
                pass
            position = Position(
                symbol=symbol,
                market_type=market_type,
                direction=direction,
                quantity=quantity,
                entry_price=fill_price,
                current_price=fill_price,
                execution_mode=execution_mode,
                strategy_id=strategy_id,
                proposal_id=proposal_id,
                order_id=order.id,
                opportunity_id=opportunity_id,
                leverage=leverage,
                liquidation_price=_liq_price(
                    fill_price, direction, leverage, symbol
                ),
                stop_price=stop_price,
                take_profit_price=take_profit_price,
                btc_entry_price=attribution["btc_entry_price"],
                regime_tag=attribution["regime_tag"],
                entry_market_summary=attribution["entry_market_summary"],
                decision_reasoning=decision_reasoning,
                entry_fee=round(fill_price * quantity * fee_rate, 8),
                slippage_cost=round(
                    abs(fill_price - requested_price) * quantity, 8
                ),
                entry_price_source="local_fill",
                entry_fill_verified=True,
                opened_at=fill.filled_at,
                trade_school=trade_school,
            )
            insert_position(position)
            position_id = position.id
            if execution_mode == "paper_internal":
                from aletheia.portfolio.paper_account import record_position_opened

                record_position_opened(position_id)
        # Every entry leg, including an averaging leg, retains a direct backlink
        # to the Position it materialized. The Position.order_id remains the
        # original opening leg, while later Orders no longer become orphan facts.
        update_order(order.id, position_id=position_id)
    return position_id, filled_take_profit


def _existing_paper_close(position_id: str) -> tuple[dict | None, dict | None]:
    """Return the already-committed local close chain, if any."""
    from aletheia.storage.sqlite import get_db

    order_row = get_db().execute(
        """
        SELECT * FROM orders
        WHERE position_id=?
          AND execution_mode='paper_internal'
          AND close_reason IS NOT NULL
        ORDER BY rowid ASC
        LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if order_row is None:
        return None, None
    order = dict(order_row)
    fill_row = get_db().execute(
        "SELECT * FROM fills WHERE order_id=? ORDER BY rowid ASC LIMIT 1",
        (order["id"],),
    ).fetchone()
    return order, dict(fill_row) if fill_row is not None else None


def _already_closed_result(position: dict) -> dict | None:
    order, fill = _existing_paper_close(position["id"])
    if order is None or fill is None or position.get("status") != "closed":
        return None
    realized = position.get("realized_pnl")
    return {
        "order_id": order["id"],
        "fill_id": fill["id"],
        "position_id": position["id"],
        "fill_price": float(fill["fill_price"]),
        "fill_quantity": float(fill["fill_quantity"]),
        "realized_pnl": float(realized) if realized is not None else None,
        "exit_price": float(fill["fill_price"]),
        "reason": order["close_reason"],
        "closed": True,
        "already_closed": True,
    }


def close_paper_position(
    position_id: str,
    *,
    mark_price: float,
    reason: str = "manual",
    actor: str = "system",
    source: str = "scheduler",
) -> dict:
    """Simulate and atomically materialize one ``paper_internal`` close.

    ``mark_price`` is the real observed reference. The fill applies the same
    configured adverse slippage model as paper entry, using the opposite trade
    side. Order, Fill, Position, and paper cash either all commit or all roll
    back; duplicate calls return the first committed close chain.
    """
    from aletheia.config.loader import load_config
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo

    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("execution_mode") != "paper_internal":
        raise ValueError(
            f"close_paper_position only handles paper_internal, "
            f"got {row.get('execution_mode')!r}"
        )
    if row.get("status") != "open":
        existing = _already_closed_result(row)
        if existing is not None:
            return existing
        raise ValueError(f"position is not open: {position_id}")

    mark = float(mark_price)
    if not math.isfinite(mark) or mark <= 0:
        raise ValueError("paper close mark_price must be finite and positive")
    cfg = load_config()
    slippage_bps = float(cfg.trading.slippage_bps)
    fee_rate = float(cfg.trading.execution_fee_pct) / 100.0
    close_side = "sell" if row["direction"] in ("long", "buy") else "buy"
    fill_price = _apply_slippage(mark, close_side, slippage_bps)
    quantity = float(row["quantity"])
    fee = round(quantity * fill_price * fee_rate, 8)
    filled_at = datetime.now(timezone.utc).isoformat()

    close_order = Order(
        proposal_id=row.get("proposal_id"),
        client_order_id=f"close-{position_id}",
        position_id=position_id,
        close_reason=reason,
        symbol=row["symbol"],
        market_type=row["market_type"],
        direction=row["direction"],
        order_type="market",
        quantity=quantity,
        price=fill_price,
        execution_mode="paper_internal",
        status="filled",
        filled_at=filled_at,
    )
    close_fill = Fill(
        id=f"paper-close:{position_id}:{filled_at}",
        order_id=close_order.id,
        fill_price=fill_price,
        fill_quantity=quantity,
        fee=fee,
        filled_at=filled_at,
    )

    funding_kwargs: dict[str, Any] = {}
    if row.get("market_type") == "usdt_futures":
        try:
            from aletheia.portfolio.local_funding import (
                compute_local_funding,
                try_seed_close_anchor_snapshot,
            )

            # Best-effort: seed a close-time funding snapshot so the
            # `close_anchor` predicate is satisfied even when the periodic
            # derivatives collector last observation is > 15 min stale.
            # Silent on failure — the settler retry path still runs. The
            # settlement-time history endpoint cannot rescue a mid-window
            # close on its own (audit funding architecture, FUND-A).
            # The helper returns a per-venue attempt trail; if the funding
            # computation still comes back incomplete, we attach that trail
            # to the evidence so the operator can see exactly which link in
            # the audit chain broke (missing_close_anchor vs. proxy failure
            # vs. no perp mapping, etc.) — FUND-Q2 audit-chain diagnostics.
            seed_diagnostic = try_seed_close_anchor_snapshot(row, filled_at)
            funding = compute_local_funding(row, closed_at=filled_at)
            if isinstance(funding.evidence, dict) and seed_diagnostic:
                funding.evidence["seed_close_anchor"] = seed_diagnostic
            # The just-computed result is authoritative even when unavailable.
            # Passing only incomplete evidence let close_position fall back to a
            # stale value already present on the Position and publish invented
            # net economics.
            funding_kwargs.update({
                "funding_cost": funding.funding_cost,
                "funding_source": funding.funding_source,
                "funding_evidence": funding.evidence,
            })
        except Exception as exc:
            # A protective exit must remain available during a data/read outage.
            # Close the Position, but preserve an explicit incomplete simulator
            # fact so PnL/Outcome stays unavailable instead of guessing zero.
            funding_kwargs.update({
                "funding_cost": None,
                "funding_source": "unavailable",
                "funding_evidence": {
                    "status": "incomplete",
                    "reason": "funding_read_failed",
                    "source": "local_simulation",
                    "error_type": type(exc).__name__,
                    "position_id": position_id,
                    "symbol": row.get("symbol"),
                    "opened_at": row.get("opened_at"),
                    "closed_at": filled_at,
                    "events": [],
                },
            })

    def _materialize_close() -> None:
        insert_order(close_order)
        insert_fill(close_fill)

    try:
        result = close_position(
            position_id,
            exit_price=fill_price,
            reason=reason,
            actual_exit_fee=fee,
            closed_at=filled_at,
            materialize_close=_materialize_close,
            actor=actor,
            source=source,
            **funding_kwargs,
        )
    except ValueError:
        # A concurrent worker may have committed while this caller waited for
        # SQLite's writer claim. Return that single durable chain, never create a
        # second local close.
        current = repo._select_one("positions", position_id)
        existing = _already_closed_result(current) if current is not None else None
        if existing is not None:
            return existing
        raise

    return {
        **result,
        "order_id": close_order.id,
        "fill_id": close_fill.id,
        "fill_price": fill_price,
        "fill_quantity": quantity,
        "closed": True,
        "already_closed": False,
    }


def _btc_price_now() -> float | None:
    """Fetch current BTC/USDT price for benchmark tracking."""
    try:
        from aletheia.trading.market_data import latest_snapshot
        snap = latest_snapshot("BTC/USDT")
        return float(snap["price"]) if snap else None
    except Exception:
        return None


def capture_paper_attribution_snapshot(symbol: str) -> dict[str, Any]:
    """Capture proposal-time attribution inputs for replay-stable paper fills."""
    regime_tag = _current_regime()
    snapshot: dict[str, Any] = {
        "btc_entry_price": _btc_price_now(),
        "regime_tag": regime_tag,
    }
    try:
        from aletheia.data.context_builder import build_market_context_from_db
        ctx = build_market_context_from_db([symbol])
        snapshot["market_context"] = {
            "data_completeness": ctx.data_completeness,
            "tradability": ctx.tradability.get(symbol, {}),
            "leverage": ctx.leverage.get(symbol, {}),
        }
    except Exception:
        pass
    return snapshot


def _paper_position_attribution(
    symbol: str,
    fill_price: float,
    attribution_snapshot: dict[str, Any] | str | None,
) -> dict[str, Any]:
    snapshot = _coerce_attribution_snapshot(attribution_snapshot)
    if snapshot:
        regime_tag = str(snapshot.get("regime_tag") or "unknown")
        return {
            "btc_entry_price": _float_or_none(snapshot.get("btc_entry_price")),
            "regime_tag": regime_tag,
            "entry_market_summary": _entry_market_summary_from_snapshot(
                snapshot, fill_price, regime_tag
            ),
        }

    regime_tag = _current_regime()
    return {
        "btc_entry_price": _btc_price_now(),
        "regime_tag": regime_tag,
        "entry_market_summary": _live_entry_market_summary(symbol, fill_price, regime_tag),
    }


def _coerce_attribution_snapshot(value: dict[str, Any] | str | None) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _entry_market_summary_from_snapshot(
    snapshot: dict[str, Any],
    fill_price: float,
    regime_tag: str,
) -> str | None:
    explicit = snapshot.get("entry_market_summary")
    if isinstance(explicit, str):
        return explicit[:2000]
    if isinstance(explicit, dict):
        return json.dumps(explicit, default=str)[:2000]

    context = snapshot.get("market_context")
    if not isinstance(context, dict):
        return None
    return json.dumps({
        "regime": regime_tag,
        "price": fill_price,
        "data_completeness": context.get("data_completeness"),
        "tradability": context.get("tradability", {}),
        "leverage": context.get("leverage", {}),
    }, default=str)[:2000]


def _live_entry_market_summary(symbol: str, fill_price: float, regime_tag: str) -> str | None:
    try:
        from aletheia.data.context_builder import build_market_context_from_db
        ctx = build_market_context_from_db([symbol])
        return json.dumps({
            "regime": regime_tag,
            "price": fill_price,
            "data_completeness": ctx.data_completeness,
            "tradability": ctx.tradability.get(symbol, {}),
            "leverage": ctx.leverage.get(symbol, {}),
        }, default=str)[:2000]
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _current_regime() -> str:
    """Current MARKET regime stamped on a new paper Position (attribution + learning).

    A1 fix: reads market_regimes.state first (real market regime) so regime_tag
    feeds insight_synthesizer's regime_playbook with a true dimension; falls back
    to macro risk_regime, then 'unknown'. Mirrors position_enrichment.current_regime
    so paper and demo/live brokers stamp the same regime source. Never raises.
    """
    try:
        from aletheia.storage.repositories import get_latest_market_regime
        mr = get_latest_market_regime()
        if mr and mr.get("state"):
            return mr["state"]
    except Exception:
        pass
    try:
        from aletheia.storage.repositories import latest_macro_snapshot
        macro = latest_macro_snapshot()
        if macro and macro.get("risk_regime"):
            return macro["risk_regime"]
    except Exception:
        pass
    return "unknown"


def _find_open_position(
    symbol: str,
    market_type: str,
    direction: str,
    execution_mode: str,
    strategy_id: str | None,
    proposal_id: str,
) -> dict | None:
    return find_compatible_open_position(
        symbol,
        market_type,
        direction,
        execution_mode,
        strategy_id,
        proposal_id=proposal_id,
    )
