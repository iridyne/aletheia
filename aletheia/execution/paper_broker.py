"""Paper broker — simulates order fills for paper/demo trading."""

from typing import Any
import hashlib
import json
import math
from datetime import datetime, timezone

from aletheia.config.execution_costs import validate_execution_costs
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    find_compatible_open_position,
    insert_order,
    update_order,
    insert_fill,
    insert_position,
    update_position,
    transaction,
)
from aletheia.execution.position_enrichment import liquidation_price_for as _liq_price


def _apply_slippage(price: float, direction: str, slippage_bps: float) -> float:
    """Apply adverse slippage: worse fill for the trader."""
    slip = slippage_bps / 10_000.0
    if direction in ("long", "buy"):
        return round(price * (1 + slip), 8)
    return round(price * (1 - slip), 8)


def _paper_close_costs(position: dict, cfg) -> tuple[float, float]:
    """Resolve legacy config costs or the Position's frozen net-balance contract."""
    try:
        trailing = json.loads(position.get("trailing_json") or "null")
    except (TypeError, json.JSONDecodeError):
        trailing = None
    if not isinstance(trailing, dict) or trailing.get("cost_model_version") != "paper-net-balance-v1":
        fee_pct, slippage_bps, _ = validate_execution_costs(
            execution_fee_pct=cfg.trading.execution_fee_pct,
            slippage_bps=cfg.trading.slippage_bps,
        )
        return fee_pct / 100.0, slippage_bps
    try:
        fee_rate = float(trailing["exit_fee_rate"])
        slippage_bps = float(trailing["slippage_bps"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("paper_close_cost_contract_invalid") from exc
    try:
        fee_pct, slippage_bps, _ = validate_execution_costs(
            execution_fee_pct=fee_rate * 100.0,
            slippage_bps=slippage_bps,
        )
    except ValueError as exc:
        raise ValueError("paper_close_cost_contract_invalid") from exc
    return fee_pct / 100.0, slippage_bps


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
    entry_plan_id: str | None = None,
    order_tactic: str | None = None,
    time_in_force: str | None = None,
    post_only: bool | None = None,
    execution_receipt=None,
) -> dict:
    """Create a local Paper Order and materialize only evidenced execution.

    The protective stop_price and take_profit from the approved Proposal are
    recorded on a newly-opened position. Averaging into the same Proposal preserves
    its original stop/TP; a different Proposal always gets a separate lineage.
    """
    proposal = None
    if execution_mode == "paper_internal" and order_type != "market":
        if not entry_plan_id:
            raise ValueError("paper_internal_market_only")
        from aletheia.storage.repositories import get_trade_proposal

        proposal = get_trade_proposal(proposal_id)
        if (
            proposal is None
            or proposal.get("entry_plan_id") != entry_plan_id
            or order_tactic not in {"POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"}
        ):
            raise ValueError("paper_internal_entry_plan_limit_authority_required")

    from aletheia.config.loader import load_config
    cfg = load_config()
    fee_pct, slippage_bps, _ = validate_execution_costs(
        execution_fee_pct=cfg.trading.execution_fee_pct,
        slippage_bps=cfg.trading.slippage_bps,
    )
    fee_rate = fee_pct / 100.0

    fill_price = _apply_slippage(price, direction, slippage_bps)
    filled_at = datetime.now(timezone.utc).isoformat()

    if entry_plan_id:
        from aletheia.execution.paper_execution import (
            PAPER_FILL_MODEL_VERSION,
            EntryExecutionReceiptV1,
            consume_known_depth,
        )

        receipt = EntryExecutionReceiptV1.model_validate(execution_receipt)
        receipt.validate_now()
        if (
            receipt.proposal_id != proposal_id
            or receipt.entry_plan_id != entry_plan_id
            or receipt.instrument.symbol != symbol
        ):
            raise ValueError("paper_execution_receipt_identity_mismatch")
        evidence = {
            "model_version": PAPER_FILL_MODEL_VERSION,
            "attempt_id": receipt.attempt_id,
            "receipt_hash": receipt.receipt_hash,
            "stream_generation_id": receipt.stream_generation_id,
            "subscription_generation_id": receipt.subscription_generation_id,
        }
        order = Order(
            proposal_id=proposal_id,
            risk_decision_id=risk_decision_id,
            symbol=symbol,
            market_type=market_type,
            direction=direction,
            order_type=order_type,
            quantity=quantity,
            price=price,
            reference_price=(receipt.quote.ask if direction in ("long", "buy") else receipt.quote.bid),
            reference_price_source="entry_execution_receipt/v1",
            execution_mode=execution_mode,
            status="pending",
            source=source,
            entry_plan_id=entry_plan_id,
            order_tactic=order_tactic,
            time_in_force=time_in_force,
            post_only=post_only,
            requested_liquidity="maker" if post_only is True else "taker",
            attempt_id=receipt.attempt_id,
            receipt_hash=receipt.receipt_hash,
            paper_fill_model_version=PAPER_FILL_MODEL_VERSION,
            paper_execution_evidence=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        )
        if order_tactic == "POST_ONLY_LIMIT":
            with transaction(immediate=True):
                insert_order(order)
            return {
                "order_id": order.id,
                "fill_id": None,
                "position_id": None,
                "fill_price": None,
                "fill_quantity": 0.0,
                "entry_plan_status": None,
            }

        depth_fill = consume_known_depth(
            direction=direction,
            quantity=quantity,
            limit_price=price,
            depth=receipt.depth,
        )
        if depth_fill.quantity <= 0 or depth_fill.vwap is None:
            evidence.update({"outcome": "no_known_liquidity", "levels": []})
            order.status = "cancelled"
            order.error_reason = "paper_ioc_known_depth_unavailable"
            order.paper_execution_evidence = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            with transaction(immediate=True):
                insert_order(order)
            return {
                "order_id": order.id,
                "fill_id": None,
                "position_id": None,
                "fill_price": None,
                "fill_quantity": 0.0,
                "entry_plan_status": "cancelled",
                "terminal_reason": "paper_ioc_known_depth_unavailable",
                "terminal_evidence": evidence,
            }
        fill_price = depth_fill.vwap
        evidence.update({
            "outcome": "full" if depth_fill.quantity + 1e-12 >= quantity else "partial_cancelled",
            "levels": [list(level) for level in depth_fill.levels],
            "known_filled_quantity": depth_fill.quantity,
        })
        evidence_json = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        order.status = "filled" if depth_fill.quantity + 1e-12 >= quantity else "cancelled"
        order.filled_at = filled_at
        order.error_reason = None if order.status == "filled" else "paper_ioc_remainder_cancelled"
        order.paper_execution_evidence = evidence_json
        fill = Fill(
            order_id=order.id,
            fill_price=fill_price,
            fill_quantity=depth_fill.quantity,
            fee=depth_fill.quantity * fill_price * fee_rate,
            filled_at=filled_at,
            paper_fill_model_version=PAPER_FILL_MODEL_VERSION,
            paper_execution_evidence=evidence_json,
            paper_execution_evidence_hash=hashlib.sha256(evidence_json.encode()).hexdigest(),
            public_event_id=(
                f"depth:{receipt.instrument.venue}:{receipt.instrument.market_type}:"
                f"{receipt.depth.exchange_timestamp_ms}:{receipt.depth.sequence}"
            ),
        )
        position_id, filled_take_profit = _materialize_paper_entry(
            order=order,
            fill=fill,
            proposal_id=proposal_id,
            symbol=symbol,
            market_type=market_type,
            direction=direction,
            quantity=depth_fill.quantity,
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
            paper_fill_model_version=PAPER_FILL_MODEL_VERSION,
        )
        return {
            "order_id": order.id,
            "fill_id": fill.id,
            "position_id": position_id,
            "fill_price": fill_price,
            "fill_quantity": depth_fill.quantity,
            "entry_plan_status": "filled" if order.status == "filled" else "cancelled",
            "terminal_reason": None if order.status == "filled" else "paper_ioc_remainder_cancelled",
            "terminal_evidence": evidence,
            "_notification_payload": {
                "symbol": symbol, "direction": direction, "quantity": depth_fill.quantity,
                "fill_price": fill_price, "position_id": position_id, "execution_mode": execution_mode,
                "stop_price": stop_price, "take_profit_price": filled_take_profit,
            },
        }

    order = Order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=fill_price,
        reference_price=price,
        reference_price_source="risk_verified_proposal_price",
        execution_mode=execution_mode,
        status="filled",
        filled_at=filled_at,
        source=source,
        entry_plan_id=entry_plan_id,
        order_tactic=order_tactic,
        time_in_force=time_in_force,
        post_only=post_only,
        requested_liquidity=(
            "maker" if post_only is True else "taker" if post_only is False else None
        ),
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
        paper_fill_model_version=None,
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
    paper_fill_model_version: str | None = None,
    persist_order: bool = True,
) -> tuple[str, float | None]:
    """Commit the local Order, Fill, Position, and cash event atomically."""
    from aletheia.storage import repositories as repo

    filled_take_profit: float | None = None
    with repo.transaction(immediate=True):
        if persist_order:
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
            if paper_fill_model_version is not None:
                update_kwargs["paper_fill_model_version"] = paper_fill_model_version
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
            if execution_mode == "paper_internal" and "stop_price" in update_kwargs:
                from aletheia.portfolio.exit_plan import load_exit_plan
                from aletheia.portfolio.protective_barrier import (
                    persist_position_exit_plan,
                )

                updated_plan = load_exit_plan(existing)
                updated_plan.stop_price = float(update_kwargs.pop("stop_price"))
                persist_position_exit_plan(
                    existing["id"],
                    updated_plan,
                    provenance="paper_position_addition_v1",
                    extra_position_updates=update_kwargs,
                )
            else:
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
            sizing_manifest = None
            try:
                proposal = repo._select_one("trade_proposals", proposal_id)
                if proposal:
                    decision_reasoning = proposal.get("agent_rationale")
                    from aletheia.trading.persona_rollout import (
                        extract_sizing_manifest,
                        serialize_sizing_manifest,
                    )

                    sizing_manifest = serialize_sizing_manifest(
                        extract_sizing_manifest(
                            {
                                "decision_reasoning": decision_reasoning,
                                "attribution_snapshot": attribution_snapshot,
                            }
                        )
                    )
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
                sizing_manifest=sizing_manifest,
                entry_fee=round(fill_price * quantity * fee_rate, 8),
                slippage_cost=round(
                    abs(fill_price - requested_price) * quantity, 8
                ),
                entry_price_source="local_fill",
                entry_fill_verified=True,
                paper_fill_model_version=paper_fill_model_version,
                opened_at=fill.filled_at,
                trade_school=trade_school,
            )
            insert_position(position)
            position_id = position.id
            if execution_mode == "paper_internal":
                from aletheia.portfolio.protective_barrier import (
                    activate_new_paper_position,
                )
                from aletheia.portfolio.paper_account import record_position_opened

                # When the approved entry has a resolved protective level, the
                # Position, first immutable ExitPlan, protection cursor and paper
                # cash reservation share this outer transaction. Level-less Paper
                # entries retain their existing contract without fabricated state.
                activate_new_paper_position(position_id)
                record_position_opened(position_id)
        # Every entry leg, including an averaging leg, retains a direct backlink
        # to the Position it materialized. The Position.order_id remains the
        # original opening leg, while later Orders no longer become orphan facts.
        update_order(order.id, position_id=position_id)
    return position_id, filled_take_profit


def match_open_paper_orders(event) -> list[dict]:
    """Apply one exact trade event once across eligible resting Paper Orders."""

    if (
        event.event_type != "trade"
        or event.side not in {"buy", "sell"}
        or event.price is None
        or event.size is None
        or not math.isfinite(float(event.price))
        or not math.isfinite(float(event.size))
        or float(event.price) <= 0
        or float(event.size) <= 0
        or int(event.exchange_timestamp_ms) < 0
        or int(event.received_timestamp_ms) < 0
        or int(event.sequence) < 0
    ):
        return []
    from aletheia.config.loader import load_config
    from aletheia.execution.paper_execution import (
        PAPER_FILL_MODEL_VERSION,
        exact_public_event_id,
    )
    from aletheia.storage import repositories as repo

    event_id = exact_public_event_id(event)
    fee_pct, _, _ = validate_execution_costs(
        execution_fee_pct=load_config().trading.execution_fee_pct,
        slippage_bps=0.0,
    )
    fee_rate = fee_pct / 100.0
    results: list[dict] = []
    with repo.transaction(immediate=True):
        already_used = repo.get_db().execute(
            "SELECT COALESCE(SUM(fill_quantity), 0) AS quantity FROM fills WHERE public_event_id=?",
            (event_id,),
        ).fetchone()
        remaining_liquidity = max(0.0, float(event.size) - float(already_used["quantity"] or 0.0))
        if remaining_liquidity <= 0:
            return []
        rows = repo.get_db().execute(
            "SELECT o.* FROM orders o WHERE o.execution_mode='paper_internal' "
            "AND o.order_tactic='POST_ONLY_LIMIT' AND o.status IN ('pending','partially_filled') "
            "AND o.symbol=? AND o.market_type=? ORDER BY o.created_at ASC, o.id ASC",
            (
                event.instrument.symbol,
                "usdt_futures" if event.instrument.market_type == "perp" else "spot",
            ),
        ).fetchall()
        candidates = []
        for raw in rows:
            order = dict(raw)
            receipt_row = repo.get_entry_execution_receipt(str(order.get("attempt_id") or ""))
            try:
                receipt = json.loads(str(receipt_row["receipt_json"])) if receipt_row else {}
                instrument = receipt.get("instrument") or {}
                placed_at = datetime.fromisoformat(str(order["created_at"]).replace("Z", "+00:00"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                instrument.get("venue") != event.instrument.exchange
                or instrument.get("symbol") != event.instrument.symbol
                or instrument.get("market_type") != event.instrument.market_type
                or int(event.received_timestamp_ms) <= int(placed_at.timestamp() * 1000)
            ):
                continue
            price = float(order["price"])
            qualifies = (
                event.side == "sell" and order["direction"] in {"long", "buy"} and float(event.price) < price
            ) or (
                event.side == "buy" and order["direction"] in {"short", "sell"} and float(event.price) > price
            )
            if qualifies:
                candidates.append(order)
        candidates.sort(
            key=lambda order: (
                -float(order["price"]) if event.side == "sell" else float(order["price"]),
                str(order["created_at"]),
                str(order["id"]),
            )
        )
        for order in candidates:
            if remaining_liquidity <= 0:
                break
            used = repo.get_db().execute(
                "SELECT COALESCE(SUM(fill_quantity), 0) AS quantity FROM fills WHERE order_id=?",
                (order["id"],),
            ).fetchone()
            remaining_order = max(0.0, float(order["quantity"]) - float(used["quantity"] or 0.0))
            fill_quantity = min(remaining_order, remaining_liquidity)
            if fill_quantity <= 0:
                continue
            proposal = repo.get_trade_proposal(str(order["proposal_id"]))
            if proposal is None:
                continue
            evidence = {
                "model_version": PAPER_FILL_MODEL_VERSION,
                "rule": "strict_opposite_aggressor_trade_through",
                "public_event_id": event_id,
                "event_price": float(event.price),
                "event_size": float(event.size),
                "event_side": event.side,
                "event_sequence": int(event.sequence),
                "event_exchange_timestamp_ms": int(event.exchange_timestamp_ms),
                "event_received_timestamp_ms": int(event.received_timestamp_ms),
                "allocated_quantity": fill_quantity,
            }
            evidence_json = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            filled_at = datetime.fromtimestamp(event.received_timestamp_ms / 1000, tz=timezone.utc).isoformat()
            fill = Fill(
                order_id=order["id"],
                fill_price=float(order["price"]),
                fill_quantity=fill_quantity,
                fee=fill_quantity * float(order["price"]) * fee_rate,
                filled_at=filled_at,
                paper_fill_model_version=PAPER_FILL_MODEL_VERSION,
                paper_execution_evidence=evidence_json,
                paper_execution_evidence_hash=hashlib.sha256(evidence_json.encode()).hexdigest(),
                public_event_id=event_id,
            )
            order_model = Order.model_validate(order)
            position_id, take_profit = _materialize_paper_entry(
                order=order_model,
                fill=fill,
                proposal_id=proposal["id"],
                symbol=proposal["symbol"],
                market_type=proposal["market_type"],
                direction=proposal["direction"],
                quantity=fill_quantity,
                requested_price=float(order["price"]),
                fill_price=float(order["price"]),
                fee_rate=fee_rate,
                execution_mode="paper_internal",
                strategy_id=proposal.get("strategy_id"),
                stop_price=proposal.get("stop_price"),
                take_profit=proposal.get("take_profit"),
                leverage=proposal.get("leverage"),
                opportunity_id=proposal.get("opportunity_id"),
                trade_school=proposal.get("trade_school") or "short_term",
                attribution_snapshot=proposal.get("attribution_snapshot"),
                paper_fill_model_version=PAPER_FILL_MODEL_VERSION,
                persist_order=False,
            )
            remaining_after = remaining_order - fill_quantity
            if remaining_after <= max(1e-12, float(order["quantity"]) * 1e-9):
                repo.update_order(order["id"], status="filled", filled_at=filled_at)
                repo.release_entry_risk_reservation(proposal["id"])
                status = "filled"
            else:
                repo.update_order(order["id"], status="partially_filled")
                repo.retain_entry_risk_reservation_quantity(proposal["id"], remaining_after)
                status = "partially_filled"
            plan = repo.get_entry_plan(str(order["entry_plan_id"]))
            if plan is not None and plan["status"] != status:
                expected = "ordered" if status == "partially_filled" else plan["status"]
                if not repo.transition_entry_plan(
                    str(order["entry_plan_id"]),
                    expected_status=expected,
                    status=status,
                    order_id=order["id"],
                    evidence=evidence if status == "filled" else None,
                ):
                    raise RuntimeError("paper_entry_plan_fill_transition_failed")
            results.append({
                "order_id": order["id"], "fill_id": fill.id, "position_id": position_id,
                "fill_price": float(order["price"]), "fill_quantity": fill_quantity,
                "status": status, "take_profit_price": take_profit,
            })
            remaining_liquidity -= fill_quantity
    return results


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
    fee_rate, slippage_bps = _paper_close_costs(row, cfg)
    close_side = "sell" if row["direction"] in ("long", "buy") else "buy"
    quantity = float(row["quantity"])
    from aletheia.data.symbols import InstrumentId
    from aletheia.execution.paper_execution import (
        PAPER_FILL_MODEL_VERSION,
        PAPER_PROTECTIVE_FALLBACK_VERSION,
        consume_known_depth,
        paper_market_state,
    )

    execution_evidence: dict[str, Any]
    fill_model = PAPER_PROTECTIVE_FALLBACK_VERSION
    fill_price = _apply_slippage(mark, close_side, slippage_bps)
    try:
        venue = str(cfg.market_data.ccxt_exchange or "").strip().lower()
        instrument = InstrumentId.from_parts(
            venue,
            row["symbol"],
            "perp" if row["market_type"] == "usdt_futures" else "spot",
        )
        snapshot = paper_market_state.snapshot(instrument, ttl_ms=5_000)
        depth_fill = consume_known_depth(
            direction="short" if close_side == "sell" else "long",
            quantity=quantity,
            limit_price=1e-300 if close_side == "sell" else 1e300,
            depth=snapshot.depth,
        )
        if depth_fill.quantity + 1e-12 >= quantity and depth_fill.vwap is not None:
            fill_price = depth_fill.vwap
            fill_model = PAPER_FILL_MODEL_VERSION
            execution_evidence = {
                "model_version": fill_model,
                "source": "known_public_depth",
                "receipt_snapshot_version": snapshot.version,
                "stream_generation_id": snapshot.stream_generation_id,
                "subscription_generation_id": snapshot.subscription_generation_id,
                "levels": [list(level) for level in depth_fill.levels],
            }
        else:
            raise ValueError("known_depth_insufficient_for_protective_exit")
    except Exception as exc:
        execution_evidence = {
            "model_version": fill_model,
            "source": "public_mark_with_configured_slippage",
            "precision": "reduced",
            "reason": str(exc).split(":", 1)[0] or type(exc).__name__,
        }
    execution_evidence_json = json.dumps(
        execution_evidence, sort_keys=True, separators=(",", ":")
    )
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
        reference_price=mark,
        reference_price_source="paper_close_mark",
        execution_mode="paper_internal",
        status="filled",
        created_at=filled_at,
        filled_at=filled_at,
        paper_fill_model_version=fill_model,
        paper_execution_evidence=execution_evidence_json,
    )
    close_fill = Fill(
        id=f"paper-close:{position_id}:{filled_at}",
        order_id=close_order.id,
        fill_price=fill_price,
        fill_quantity=quantity,
        fee=fee,
        filled_at=filled_at,
        paper_fill_model_version=fill_model,
        paper_execution_evidence=execution_evidence_json,
        paper_execution_evidence_hash=hashlib.sha256(
            execution_evidence_json.encode()
        ).hexdigest(),
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
        outcome_fill_model = (
            fill_model
            if fill_model != PAPER_FILL_MODEL_VERSION
            else row.get("paper_fill_model_version")
        )
        update_position(
            position_id,
            paper_fill_model_version=outcome_fill_model,
        )

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
        regime_value = snapshot.get("regime_tag", "unknown")
        regime_tag = None if regime_value is None else str(regime_value)
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
    regime_tag: str | None,
) -> str | None:
    explicit = snapshot.get("entry_market_summary")
    if isinstance(explicit, str):
        return explicit[:2000]
    if isinstance(explicit, dict):
        return json.dumps(explicit, default=str)[:2000]

    context = snapshot.get("market_context")
    if not isinstance(context, dict):
        return None
    summary = {
        "price": fill_price,
        "data_completeness": context.get("data_completeness"),
        "tradability": context.get("tradability", {}),
        "leverage": context.get("leverage", {}),
    }
    if regime_tag is not None:
        summary["regime"] = regime_tag
    for key in ("analysis_context", "instrument_background"):
        if key in context:
            summary[key] = context[key]
    return json.dumps(summary, default=str)[:2000]


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
        if mr is not None:
            return "unknown"
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
