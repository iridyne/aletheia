"""Replay-safe materialization of exchange-confirmed order fills.

The exchange is the source of truth for fill economics.  This module converts
private Bitget fill rows into the same durable Order -> Fill -> Position chain
that the synchronous broker path writes.  It never places an order and never
falls back to requested price/quantity/fees when private evidence is incomplete.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from typing import Iterable

from aletheia.data.symbols import to_canonical
from aletheia.execution.position_enrichment import liquidation_price_for
from aletheia.execution.stops import merge_protective_stop
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Position


class MaterializationIntegrityError(RuntimeError):
    """Private evidence conflicts with the durable local causal chain."""


@dataclass(frozen=True)
class PrivateFill:
    fill_id: str
    exchange_order_id: str
    client_order_id: str | None
    price: float
    quantity: float
    fee: float
    fee_currency: str
    filled_at: str
    filled_at_ms: int


@dataclass(frozen=True)
class MaterializationResult:
    state: str
    new_fill_count: int = 0
    position_id: str | None = None
    exchange_order_id: str | None = None


def durable_private_fill_id(execution_mode: str, remote_fill_id: str) -> str:
    """Namespace a venue fill identity by its exact logical exchange account.

    ``fills.id`` is globally unique, while venue trade ids are only guaranteed
    inside one account. Keeping both strings directly in this deterministic id
    avoids a schema migration and leaves the original remote id auditable.
    """
    execution_mode = str(execution_mode or "").strip()
    remote_fill_id = str(remote_fill_id or "").strip()
    if not execution_mode or not remote_fill_id:
        raise MaterializationIntegrityError(
            "private Fill identity requires account scope and remote id"
        )
    return f"private-fill:{execution_mode}:{remote_fill_id}"


def _private_fill_already_local(
    item: PrivateFill,
    local_ids: set[str],
    *,
    execution_mode: str,
) -> bool:
    """Recognize new scoped ids plus exact legacy raw-id rows."""
    return (
        durable_private_fill_id(execution_mode, item.fill_id) in local_ids
        or item.fill_id in local_ids
    )


@contextmanager
def _atomic_materialization():
    """Own a rollback boundary even when the caller already has a transaction."""
    db = repo.get_db()
    if not db.in_transaction:
        with repo.transaction(immediate=True):
            yield
        return
    # Repository transactions intentionally join an existing transaction.  A
    # reconciler must be stricter: Fill + Position + terminal statuses are one
    # indivisible unit, so use a nested savepoint when a caller already owns the
    # outer transaction.
    savepoint = "exchange_fill_materialization"
    db.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def _positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # #1722: non-finite values (NaN, inf) must never enter fill economics.
    # Return None (row skipped by caller) rather than raising to avoid aborting
    # the entire fill batch when one row has a bad value.
    if not math.isfinite(number):
        import logging as _log
        _log.getLogger(__name__).warning(
            "non-finite fill value rejected and row skipped: %r", value
        )
        return None
    return number if number > 0 else None


def _timestamp_ms(value) -> int | None:
    """Normalize an ISO timestamp for exact exchange-time comparison."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(round(parsed.timestamp() * 1000))


def _fee_from_row(row: dict) -> tuple[float, str] | None:
    """Return an actual exchange fee, including a legitimate zero fee."""
    direct = row.get("fee")
    if direct not in (None, ""):
        try:
            fee = abs(float(direct))
        except (TypeError, ValueError):
            return None
        return fee, str(row.get("feeCoin") or "USDT")

    details = row.get("feeDetail")
    if isinstance(details, dict):
        details = [details]
    if not isinstance(details, list) or not details:
        return None
    fees: list[float] = []
    currency = "USDT"
    for detail in details:
        if not isinstance(detail, dict):
            return None
        raw_fee = detail.get("totalFee")
        if raw_fee in (None, ""):
            raw_fee = detail.get("fee")
        try:
            fees.append(abs(float(raw_fee)))
        except (TypeError, ValueError):
            return None
        currency = str(detail.get("feeCoin") or currency)
    return sum(fees), currency


def _coerce_rows(rows) -> list[dict]:
    if isinstance(rows, dict):
        rows = rows.get("fillList") or rows.get("list") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def parse_bitget_private_fills(rows) -> list[PrivateFill]:
    """Normalize only complete private rows; incomplete economics stay unusable."""
    parsed: dict[str, PrivateFill] = {}
    for row in _coerce_rows(rows):
        fill_id = str(row.get("tradeId") or row.get("execId") or "").strip()
        exchange_order_id = str(row.get("orderId") or row.get("order_id") or "").strip()
        client_order_id = str(
            row.get("clientOid") or row.get("clientOrderId") or ""
        ).strip() or None
        price = _positive_float(
            row.get("fillPrice") or row.get("price") or row.get("execPrice")
        )
        quantity = _positive_float(
            row.get("fillQuantity")
            or row.get("fillQty")
            or row.get("baseVolume")
            or row.get("execQty")
            or row.get("size")
            or row.get("fillSize")
        )
        fee = _fee_from_row(row)
        try:
            timestamp_ms = int(row.get("cTime") or row.get("createdTime") or 0)
            filled_at = datetime.fromtimestamp(
                timestamp_ms / 1000, tz=timezone.utc
            ).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            timestamp_ms = 0
            filled_at = ""
        if (
            not fill_id
            or not exchange_order_id
            or price is None
            or quantity is None
            or fee is None
            or timestamp_ms <= 0
        ):
            continue
        evidence = PrivateFill(
            fill_id=fill_id,
            exchange_order_id=exchange_order_id,
            client_order_id=client_order_id,
            price=price,
            quantity=quantity,
            fee=fee[0],
            fee_currency=fee[1],
            filled_at=filled_at,
            filled_at_ms=timestamp_ms,
        )
        prior = parsed.get(fill_id)
        if prior is not None and prior != evidence:
            raise MaterializationIntegrityError(
                f"conflicting private fill identity: {fill_id}"
            )
        parsed[fill_id] = evidence
    return sorted(parsed.values(), key=lambda item: (item.filled_at_ms, item.fill_id))


def evidence_for_order(order: dict, rows) -> tuple[str | None, list[PrivateFill]]:
    """Match by venue order id, or recover a lost id through stable clientOid."""
    evidence = parse_bitget_private_fills(rows)
    exchange_order_id = str(order.get("exchange_order_id") or "").strip() or None
    client_order_id = str(order.get("client_order_id") or "").strip() or None
    if exchange_order_id:
        return exchange_order_id, [
            item for item in evidence if item.exchange_order_id == exchange_order_id
        ]
    if not client_order_id:
        return None, []
    matched = [item for item in evidence if item.client_order_id == client_order_id]
    remote_ids = {item.exchange_order_id for item in matched}
    if len(remote_ids) > 1:
        raise MaterializationIntegrityError(
            "one client order identity maps to multiple exchange orders"
        )
    return (next(iter(remote_ids)) if remote_ids else None), matched


def _attribution_fields(proposal: dict) -> dict:
    raw = proposal.get("attribution_snapshot")
    try:
        snapshot = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    market_context = snapshot.get("market_context")
    sizing_manifest = None
    try:
        from aletheia.trading.persona_rollout import (
            extract_sizing_manifest,
            serialize_sizing_manifest,
        )

        sizing_manifest = serialize_sizing_manifest(
            extract_sizing_manifest(
                {
                    "agent_rationale": proposal.get("agent_rationale"),
                    "attribution_snapshot": snapshot,
                }
            )
        )
    except Exception:
        sizing_manifest = None
    return {
        "btc_entry_price": snapshot.get("btc_entry_price"),
        "regime_tag": str(snapshot.get("regime_tag") or "unknown"),
        "entry_market_summary": (
            json.dumps(market_context, sort_keys=True)
            if isinstance(market_context, dict)
            else None
        ),
        "decision_reasoning": proposal.get("agent_rationale"),
        "sizing_manifest": sizing_manifest,
    }


def _require_compatible_entry_position(order: dict, position: dict) -> dict:
    """Fail closed when a durable entry backlink crosses position identity."""
    for field in ("symbol", "market_type", "direction", "execution_mode"):
        if position.get(field) != order.get(field):
            raise MaterializationIntegrityError(
                f"entry Order {field} does not match linked Position"
            )
    if position.get("status") != "open":
        raise MaterializationIntegrityError(
            "entry Order references a linked Position that is not open"
        )
    return position


def _linked_open_position(order: dict, proposal: dict) -> dict | None:
    db = repo.get_db()
    if order.get("position_id"):
        position = repo._select_one("positions", order["position_id"])
        if position is None:
            raise MaterializationIntegrityError("Order references a missing Position")
        return _require_compatible_entry_position(order, position)
    row = db.execute(
        "SELECT * FROM positions WHERE order_id=? ORDER BY rowid ASC LIMIT 1",
        (order["id"],),
    ).fetchone()
    if row is not None:
        return _require_compatible_entry_position(order, dict(row))

    # Exchange brokers intentionally aggregate compatible entry legs into one
    # open Position.  The Order.position_id backlink records the exact later leg.
    rows = db.execute(
        """
        SELECT * FROM positions
        WHERE status='open' AND symbol=? AND market_type=? AND direction=?
          AND execution_mode=?
        ORDER BY rowid ASC
        """,
        (
            order["symbol"], order["market_type"], order["direction"],
            order["execution_mode"],
        ),
    ).fetchall()
    strategy_id = proposal.get("strategy_id")
    proposal_school = proposal.get("trade_school") or "short_term"
    for row in rows:
        candidate = dict(row)
        if strategy_id is not None and candidate.get("strategy_id") not in (
            None,
            "",
            strategy_id,
        ):
            continue
        if (candidate.get("trade_school") or "short_term") != proposal_school:
            continue
        return candidate
    return None


def _tail_delta(evidence: list[PrivateFill], quantity: float) -> list[PrivateFill]:
    """Require whole unseen private Fills to explain the exact local delta.

    A venue trade id names one indivisible economic fact.  Splitting it into a
    synthetic ``:delta:`` id makes the next replay unable to match that durable
    row back to the original private Fill.  Any non-whole quantity relationship
    is therefore conflicting evidence, not a value to approximate locally.
    """
    evidence_quantity = sum(item.quantity for item in evidence)
    tolerance = max(1e-12, abs(quantity) * 1e-9)
    if quantity < -tolerance or abs(evidence_quantity - quantity) > tolerance:
        raise MaterializationIntegrityError(
            "whole private fills cannot explain the local/exchange quantity delta"
        )
    return list(evidence)


def _fee_in_quote(
    *,
    fee: float,
    fee_currency: str,
    fill_price: float,
    symbol: str,
) -> float:
    """Convert a directly usable private fee into the instrument quote asset.

    When the fee is denominated in a third currency (e.g. BGB, BNB), the exact
    conversion is not available from local fill evidence.  Return 0.0 so
    materialization proceeds conservatively rather than blocking.  (#1752)
    """
    if fee == 0:
        return 0.0
    canonical = to_canonical(symbol)
    base, separator, quote = canonical.partition("/")
    currency = str(fee_currency or quote).upper()
    if not separator or not base or not quote:
        raise MaterializationIntegrityError("fee conversion requires a canonical symbol")
    if currency == quote.upper():
        return fee
    if currency == base.upper():
        return fee * fill_price
    # Third-currency fee (e.g. BGB, BNB): treat as zero — conservative and
    # avoids blocking a legitimate fill when the exchange charges in a deduction asset.
    import logging as _log
    _log.getLogger(__name__).warning(
        "private fill fee in unknown currency %r for %s — treating as 0.0",
        fee_currency, symbol,
    )
    return 0.0


def _fill_totals(rows: Iterable[dict], *, symbol: str) -> tuple[float, float, float]:
    quantity = 0.0
    notional = 0.0
    fee = 0.0
    for row in rows:
        row_quantity = float(row["fill_quantity"])
        row_price = float(row["fill_price"])
        quantity += row_quantity
        notional += row_quantity * row_price
        fee += _fee_in_quote(
            fee=float(row.get("fee") or 0.0),
            fee_currency=str(row.get("fee_currency") or "USDT"),
            fill_price=row_price,
            symbol=symbol,
        )
    return quantity, notional, fee


def _verify_durable_fills(
    db,
    local_rows: list[dict],
    evidence: list[PrivateFill],
    *,
    quantity_tolerance: float,
    lifecycle: str,
    execution_mode: str,
    upgrade_source: bool = True,
) -> None:
    """Require every local Fill to be the same private exchange fact."""
    for local in local_rows:
        local_id = str(local["id"])
        matches = [
            item
            for item in evidence
            if local_id
            in {
                item.fill_id,
                durable_private_fill_id(execution_mode, item.fill_id),
            }
        ]
        if len(matches) != 1:
            raise MaterializationIntegrityError(
                f"durable {lifecycle} Fill has no same-id private evidence"
            )
        matched = matches[0]
        if (
            abs(float(local["fill_quantity"]) - matched.quantity)
            > quantity_tolerance
            or abs(float(local["fill_price"]) - matched.price)
            > max(1e-12, matched.price * 1e-9)
            or abs(float(local.get("fee") or 0.0) - matched.fee)
            > max(1e-12, matched.fee * 1e-9)
            or str(local.get("fee_currency") or "").upper()
            != matched.fee_currency.upper()
            or _timestamp_ms(local.get("filled_at")) != matched.filled_at_ms
        ):
            raise MaterializationIntegrityError(
                f"durable {lifecycle} Fill conflicts with same-id private evidence"
            )
        if upgrade_source and local.get("fee_source") != "reconciled":
            # Exact identity, economics, and exchange time allow an older row's
            # provenance to be upgraded. Any mismatch above remains fail-closed.
            db.execute(
                "UPDATE fills SET fee_source='reconciled' WHERE id=?",
                (local["id"],),
            )


def materialize_bitget_entry(
    order_id: str,
    rows,
    *,
    historical_gap_recovery: bool = False,
) -> MaterializationResult:
    """Atomically converge one native entry Order to complete private evidence.

    Historical recovery is deliberately stricter than ordinary unresolved-order
    replay: it must prove the entire authoritative quantity in one exact private
    response and must create a new Position.  It may never reinterpret an old
    filled Order as a later averaging leg.
    """
    with _atomic_materialization():
        order = repo.get_order(order_id)
        if order is None:
            raise MaterializationIntegrityError("Order disappeared before materialization")
        if order.get("position_id") and repo._select_one("positions", order["position_id"]):
            # A linked open Position still represents an entry Order.  Close
            # Orders are linked to a Position *and* are its pending close id.
            pending = repo.get_db().execute(
                "SELECT 1 FROM positions WHERE id=? AND pending_close_order_id=?",
                (order["position_id"], order_id),
            ).fetchone()
            if pending is not None:
                return MaterializationResult(state="not_entry", position_id=order["position_id"])

        repo.validate_native_order_authority(order)

        proposal_id = order.get("proposal_id")
        proposal = repo.get_trade_proposal(proposal_id) if proposal_id else None
        if proposal is None or proposal.get("source") != "native":
            return MaterializationResult(state="authority_missing")

        exchange_order_id, evidence = evidence_for_order(order, rows)
        if exchange_order_id is None or not evidence:
            return MaterializationResult(state="evidence_missing")
        if not order.get("exchange_order_id"):
            repo.update_order(order_id, exchange_order_id=exchange_order_id)
            order["exchange_order_id"] = exchange_order_id

        db = repo.get_db()
        local_rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                (order_id,),
            ).fetchall()
        ]
        local_quantity, _, _ = _fill_totals(local_rows, symbol=order["symbol"])
        exchange_quantity = sum(item.quantity for item in evidence)
        tolerance = max(1e-12, float(order["quantity"]) * 1e-9)
        if historical_gap_recovery and abs(
            exchange_quantity - float(order["quantity"])
        ) > tolerance:
            raise MaterializationIntegrityError(
                "historical filled-gap recovery requires complete private quantity"
            )
        if local_quantity > exchange_quantity + tolerance:
            raise MaterializationIntegrityError(
                "local fill quantity exceeds complete private evidence"
            )

        _verify_durable_fills(
            db,
            local_rows,
            evidence,
            quantity_tolerance=tolerance,
            lifecycle="entry",
            execution_mode=str(order.get("execution_mode") or ""),
        )
        local_ids = {str(row["id"]) for row in local_rows}
        execution_mode = str(order.get("execution_mode") or "")
        unseen_evidence = [
            item
            for item in evidence
            if not _private_fill_already_local(
                item,
                local_ids,
                execution_mode=execution_mode,
            )
        ]
        missing_quantity = max(0.0, exchange_quantity - local_quantity)
        delta_evidence = _tail_delta(unseen_evidence, missing_quantity)

        # Keep the exposure check inside the same BEGIN IMMEDIATE transaction as
        # the Fill/Position writes.  The read-only preflight in reconciliation is
        # useful for an explicit reason, but cannot by itself close the race with
        # another worker opening a compatible Position.
        if historical_gap_recovery:
            compatible = db.execute(
                """
                SELECT 1
                FROM positions
                WHERE status='open' AND symbol=? AND market_type=? AND direction=?
                  AND execution_mode=?
                LIMIT 1
                """,
                (
                    order["symbol"],
                    order["market_type"],
                    order["direction"],
                    order["execution_mode"],
                ),
            ).fetchone()
            if compatible is not None:
                raise MaterializationIntegrityError(
                    "historical filled-gap recovery cannot merge into an open Position"
                )

        position = _linked_open_position(order, proposal)
        if historical_gap_recovery and position is not None:
            raise MaterializationIntegrityError(
                "historical filled-gap recovery cannot attach to an existing Position"
            )
        for item in delta_evidence:
            repo.insert_fill(
                Fill(
                    id=durable_private_fill_id(execution_mode, item.fill_id),
                    order_id=order_id,
                    fill_price=item.price,
                    fill_quantity=item.quantity,
                    fee=item.fee,
                    fee_currency=item.fee_currency,
                    fee_source="reconciled",
                    filled_at=item.filled_at,
                )
            )

        delta_quantity = sum(item.quantity for item in delta_evidence)
        delta_notional = sum(item.quantity * item.price for item in delta_evidence)
        delta_fee = sum(
            _fee_in_quote(
                fee=item.fee,
                fee_currency=item.fee_currency,
                fill_price=item.price,
                symbol=order["symbol"],
            )
            for item in delta_evidence
        )
        if position is None:
            all_local_rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                    (order_id,),
                ).fetchall()
            ]
            total_quantity, total_notional, total_fee = _fill_totals(
                all_local_rows,
                symbol=order["symbol"],
            )
            if total_quantity <= 0:
                return MaterializationResult(
                    state="evidence_missing", exchange_order_id=exchange_order_id
                )
            entry_price = total_notional / total_quantity
            requested_price = float(order.get("price") or entry_price)
            position_model = Position(
                symbol=order["symbol"],
                market_type=order["market_type"],
                direction=order["direction"],
                quantity=total_quantity,
                entry_price=entry_price,
                current_price=entry_price,
                execution_mode=order["execution_mode"],
                strategy_id=proposal.get("strategy_id"),
                proposal_id=proposal_id,
                order_id=order_id,
                opportunity_id=proposal.get("opportunity_id"),
                leverage=proposal.get("leverage"),
                liquidation_price=liquidation_price_for(
                    entry_price,
                    order["direction"],
                    proposal.get("leverage"),
                    order["symbol"],
                ),
                stop_price=proposal.get("stop_price"),
                take_profit_price=proposal.get("take_profit"),
                trade_school=proposal.get("trade_school") or "short_term",
                entry_fee=total_fee,
                entry_price_source="broker_fill",
                entry_fill_verified=True,
                opened_at=min(
                    evidence, key=lambda item: item.filled_at_ms
                ).filled_at,
                slippage_cost=abs(entry_price - requested_price) * total_quantity,
                **_attribution_fields(proposal),
            )
            repo.insert_position(position_model)
            position_id = position_model.id
            _exit_intent: dict = {}
            if proposal.get("expected_horizon_hours") is not None:
                _exit_intent["expected_horizon_hours"] = proposal["expected_horizon_hours"]
            if proposal.get("stop_source"):
                _exit_intent["stop_source"] = proposal["stop_source"]
            if _exit_intent:
                _exit_intent["exit_plan_version"] = 1
                repo.update_position(position_id, **_exit_intent)
        else:
            if position.get("status") != "open":
                raise MaterializationIntegrityError(
                    "entry fill maps to a non-open Position"
                )
            position_id = position["id"]
            if delta_quantity > 0:
                old_quantity = float(position["quantity"])
                new_quantity = old_quantity + delta_quantity
                new_entry = (
                    old_quantity * float(position["entry_price"]) + delta_notional
                ) / new_quantity
                update_fields = {
                    "quantity": new_quantity,
                    "entry_price": new_entry,
                    "current_price": delta_notional / delta_quantity,
                    "entry_fee": float(position.get("entry_fee") or 0.0) + delta_fee,
                    "slippage_cost": float(position.get("slippage_cost") or 0.0)
                    + abs(
                        delta_notional / delta_quantity
                        - float(order.get("price") or delta_notional / delta_quantity)
                    )
                    * delta_quantity,
                }
                # The new leg is privately verified, but it cannot retroactively
                # prove how an older entry leg was priced.  Only a Position whose
                # existing aggregate was already verified can remain verified
                # after this weighted-average update.
                existing_entry_verified = bool(
                    position.get("entry_fill_verified")
                ) and position.get("entry_price_source") == "broker_fill"
                update_fields["entry_fill_verified"] = existing_entry_verified
                if existing_entry_verified:
                    update_fields["entry_price_source"] = "broker_fill"
                merged_stop = merge_protective_stop(
                    order["direction"],
                    proposal.get("stop_price"),
                    position.get("stop_price"),
                )
                if merged_stop is not None:
                    update_fields["stop_price"] = merged_stop
                if proposal.get("expected_horizon_hours") is not None:
                    update_fields["expected_horizon_hours"] = proposal["expected_horizon_hours"]
                    update_fields["exit_plan_version"] = 1
                if proposal.get("stop_source"):
                    update_fields["stop_source"] = proposal["stop_source"]
                    update_fields["exit_plan_version"] = 1
                if proposal.get("trade_school"):
                    update_fields["trade_school"] = proposal["trade_school"]
                position_leverage = position.get("leverage") or proposal.get("leverage")
                if position_leverage and float(position_leverage) > 1:
                    update_fields["liquidation_price"] = liquidation_price_for(
                        new_entry,
                        order["direction"],
                        int(position_leverage),
                        order["symbol"],
                    )
                repo.update_position(position_id, **update_fields)

        requested_quantity = float(order["quantity"])
        if abs(exchange_quantity - requested_quantity) <= tolerance:
            state = "filled"
            proposal_state = "executed"
            error_reason = None
            filled_at = max(item.filled_at for item in evidence)
        elif exchange_quantity < requested_quantity - tolerance:
            state = "partially_filled"
            proposal_state = "partially_filled"
            error_reason = None
            filled_at = None
        else:
            state = "overfilled"
            proposal_state = "error"
            error_reason = "exchange_quantity_exceeds_authoritative_order"
            filled_at = max(item.filled_at for item in evidence)
        repo.update_order(
            order_id,
            exchange_order_id=exchange_order_id,
            position_id=position_id,
            status=state,
            error_reason=error_reason,
            filled_at=filled_at,
        )
        repo.update_trade_proposal(proposal_id, status=proposal_state)
        return MaterializationResult(
            state=state,
            new_fill_count=len(delta_evidence),
            position_id=position_id,
            exchange_order_id=exchange_order_id,
        )


def _position_for_close_order(order: dict) -> dict | None:
    position_id = order.get("position_id")
    if position_id:
        position = repo._select_one("positions", position_id)
        if position is None:
            raise MaterializationIntegrityError("close Order references a missing Position")
        if position.get("pending_close_order_id") not in (None, "", order["id"]):
            raise MaterializationIntegrityError(
                "close Order does not own the Position pending-close claim"
            )
        return position
    row = repo.get_db().execute(
        "SELECT * FROM positions WHERE pending_close_order_id=? ORDER BY rowid ASC LIMIT 1",
        (order["id"],),
    ).fetchone()
    return dict(row) if row is not None else None


def funding_request_for_complete_close(order_id: str, rows) -> dict | None:
    """Return the exact account/symbol/window needed for close funding evidence."""
    order = repo.get_order(order_id)
    if order is None or order.get("market_type") != "usdt_futures":
        return None
    position = _position_for_close_order(order)
    if position is None or position.get("status") != "open":
        return None
    exchange_order_id, evidence = evidence_for_order(order, rows)
    if exchange_order_id is None or not evidence:
        return None
    requested_quantity = float(order["quantity"])
    exchange_quantity = sum(item.quantity for item in evidence)
    tolerance = max(1e-12, requested_quantity * 1e-9)
    if abs(exchange_quantity - requested_quantity) > tolerance:
        return None
    closed_at = max(item.filled_at for item in evidence)
    from aletheia.execution.broker_funding import (
        BrokerFundingIntegrityError,
        funding_query_window,
    )

    try:
        start_ms, end_ms = funding_query_window(position, closed_at)
    except BrokerFundingIntegrityError:
        # Fill materialization remains operationally authoritative even when an
        # old fixture/legacy row has no usable holding window. Funding stays
        # unavailable and the recovery path can retry only after valid evidence.
        return None
    return {
        "position": position,
        "closed_at": closed_at,
        "start_time_ms": start_ms,
        "end_time_ms": end_ms,
    }


_PRIVATE_CLOSE_EVIDENCE = {
    "bitget_demo": ("bitget_private_close_fills", "private_fills", "demo"),
    "bitget_live_limited": (
        "bitget_private_close_fills",
        "private_fills",
        "live",
    ),
    "okx_demo": (
        "okx_demo_private_close_fills",
        "/api/v5/trade/fills-history",
        "demo",
    ),
    "okx_live_limited": (
        "okx_live_limited_private_close_fills",
        "/api/v5/trade/fills-history",
        "live",
    ),
    "binance_testnet": (
        "binance_testnet_private_close_fills",
        "/fapi/v1/userTrades",
        "testnet",
    ),
    "binance_live_limited": (
        "binance_live_limited_private_close_fills",
        "/fapi/v1/userTrades",
        "live",
    ),
}


def _materialize_complete_private_close(
    order_id: str, rows, funding_ledger: dict | None = None
) -> MaterializationResult | None:
    """Close only when private evidence exactly covers the requested quantity.

    ``close_position`` owns the transaction and runs our database-only callback
    before committing. Its audit, notification, and feedback side effects happen
    only after that commit, so a late Order/Fill write failure cannot announce a
    close that SQLite rolled back.
    """
    order = repo.get_order(order_id)
    if order is None:
        raise MaterializationIntegrityError(
            "close Order disappeared before materialization"
        )
    position = _position_for_close_order(order)
    if position is None:
        return None
    if position.get("status") == "closed" and order.get("status") == "filled":
        return MaterializationResult(
            state="filled",
            position_id=position["id"],
            exchange_order_id=order.get("exchange_order_id"),
        )
    if position.get("status") != "open":
        raise MaterializationIntegrityError(
            "close fill maps to a Position that is neither open nor closed"
        )
    repo.validate_native_order_authority(order)
    exchange_order_id, evidence = evidence_for_order(order, rows)
    if exchange_order_id is None or not evidence:
        return None
    requested_quantity = float(order["quantity"])
    exchange_quantity = sum(item.quantity for item in evidence)
    tolerance = max(1e-12, requested_quantity * 1e-9)
    if abs(exchange_quantity - requested_quantity) > tolerance:
        return None

    db = repo.get_db()
    local_rows = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
            (order_id,),
        ).fetchall()
    ]
    execution_mode = str(order.get("execution_mode") or "")
    _verify_durable_fills(
        db,
        local_rows,
        evidence,
        quantity_tolerance=tolerance,
        lifecycle="close",
        execution_mode=execution_mode,
        upgrade_source=False,
    )
    local_quantity, _, _ = _fill_totals(local_rows, symbol=order["symbol"])
    if local_quantity > exchange_quantity + tolerance:
        raise MaterializationIntegrityError(
            "local close quantity exceeds complete private evidence"
        )

    total_notional = sum(item.quantity * item.price for item in evidence)
    total_fee = sum(
        _fee_in_quote(
            fee=item.fee,
            fee_currency=item.fee_currency,
            fill_price=item.price,
            symbol=order["symbol"],
        )
        for item in evidence
    )
    exit_price = total_notional / exchange_quantity
    evidence_identity = _PRIVATE_CLOSE_EVIDENCE.get(execution_mode)
    if evidence_identity is None:
        raise MaterializationIntegrityError(
            "close private evidence account is unsupported"
        )
    evidence_source, source_endpoint, account_environment = evidence_identity
    inserted_count = 0

    def _materialize_close_rows() -> None:
        nonlocal inserted_count
        current_order = repo.get_order(order_id)
        if current_order is None:
            raise MaterializationIntegrityError(
                "close Order disappeared during materialization"
            )
        current_position = _position_for_close_order(current_order)
        if current_position is None or current_position["id"] != position["id"]:
            raise MaterializationIntegrityError(
                "close Order lost its Position claim"
            )
        current_exchange_id, current_evidence = evidence_for_order(
            current_order, rows
        )
        if (
            current_exchange_id != exchange_order_id
            or current_evidence != evidence
        ):
            raise MaterializationIntegrityError(
                "private close evidence changed during materialization"
            )
        current_rows = [
            dict(row)
            for row in repo.get_db().execute(
                "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                (order_id,),
            ).fetchall()
        ]
        _verify_durable_fills(
            repo.get_db(),
            current_rows,
            evidence,
            quantity_tolerance=tolerance,
            lifecycle="close",
            execution_mode=execution_mode,
        )
        current_ids = {str(row["id"]) for row in current_rows}
        unseen = [
            item
            for item in evidence
            if not _private_fill_already_local(
                item,
                current_ids,
                execution_mode=execution_mode,
            )
        ]
        current_quantity, _, _ = _fill_totals(
            current_rows, symbol=order["symbol"]
        )
        missing_quantity = exchange_quantity - current_quantity
        if (
            missing_quantity < -tolerance
            or abs(sum(item.quantity for item in unseen) - missing_quantity)
            > tolerance
        ):
            raise MaterializationIntegrityError(
                "durable close Fill quantity conflicts with private evidence"
            )
        for item in unseen:
            repo.insert_fill(
                Fill(
                    id=durable_private_fill_id(execution_mode, item.fill_id),
                    order_id=order_id,
                    fill_price=item.price,
                    fill_quantity=item.quantity,
                    fee=item.fee,
                    fee_currency=item.fee_currency,
                    fee_source="reconciled",
                    filled_at=item.filled_at,
                )
            )
        inserted_count = len(unseen)
        repo.update_order(
            order_id,
            exchange_order_id=exchange_order_id,
            position_id=position["id"],
            status="filled",
            error_reason=None,
            filled_at=max(item.filled_at for item in evidence),
        )
        repo.update_position(
            position["id"],
            pending_close_order_id=None,
            pending_close_status=None,
            pending_close_requested_at=None,
        )

    from aletheia.portfolio.pnl import close_position

    funding_kwargs: dict = {}
    if position.get("market_type") == "usdt_futures" and funding_ledger is not None:
        from aletheia.execution.broker_funding import compute_broker_funding

        candidate_rows = [
            dict(row)
            for row in repo.get_db().execute(
                """SELECT * FROM positions
                   WHERE id<>? AND execution_mode=? AND market_type='usdt_futures'
                     AND symbol=?""",
                (
                    position["id"],
                    position.get("execution_mode"),
                    position.get("symbol"),
                ),
            ).fetchall()
        ]
        funding = compute_broker_funding(
            position=position,
            closed_at=max(item.filled_at for item in evidence),
            ledger=funding_ledger,
            candidate_positions=candidate_rows,
        )
        funding_kwargs = {
            "funding_cost": funding.funding_cost,
            "funding_source": funding.funding_source,
            "funding_evidence": funding.evidence,
        }

    close_position(
        position["id"],
        exit_price=exit_price,
        reason=order.get("close_reason") or "verified_exit",
        actual_exit_fee=total_fee,
        closed_at=max(item.filled_at for item in evidence),
        verified_exit_evidence={
            "source": evidence_source,
            "source_endpoint": source_endpoint,
            "broker_id": execution_mode,
            "account_environment": account_environment,
            "execution_mode": execution_mode,
            "exchange_order_id": exchange_order_id,
            "client_order_id": order.get("client_order_id"),
            "fill_ids": [item.fill_id for item in evidence],
            "durable_fill_ids": [
                durable_private_fill_id(execution_mode, item.fill_id)
                for item in evidence
            ],
            "fill_quantity": exchange_quantity,
            "fee_in_quote": total_fee,
            "fills": [
                {
                    "fill_id": item.fill_id,
                    "remote_fill_id": item.fill_id,
                    "durable_fill_id": durable_private_fill_id(
                        execution_mode, item.fill_id
                    ),
                    "price": item.price,
                    "quantity": item.quantity,
                    "fee": item.fee,
                    "fee_currency": item.fee_currency,
                    "filled_at": item.filled_at,
                }
                for item in evidence
            ],
        },
        materialize_close=_materialize_close_rows,
        **funding_kwargs,
    )
    return MaterializationResult(
        state="filled",
        new_fill_count=inserted_count,
        position_id=position["id"],
        exchange_order_id=exchange_order_id,
    )


def materialize_bitget_close(
    order_id: str, rows, funding_ledger: dict | None = None
) -> MaterializationResult:
    """Converge an asynchronously filled exchange close without invention."""
    try:
        complete = _materialize_complete_private_close(
            order_id, rows, funding_ledger=funding_ledger
        )
        if complete is not None:
            return complete
        with _atomic_materialization():
            order = repo.get_order(order_id)
            if order is None:
                raise MaterializationIntegrityError(
                    "close Order disappeared before materialization"
                )
            position = _position_for_close_order(order)
            if position is None:
                return MaterializationResult(state="not_close")
            if position.get("status") == "closed" and order.get("status") == "filled":
                return MaterializationResult(
                    state="filled",
                    position_id=position["id"],
                    exchange_order_id=order.get("exchange_order_id"),
                )
            if position.get("status") != "open":
                raise MaterializationIntegrityError(
                    "close fill maps to a Position that is neither open nor closed"
                )
            repo.validate_native_order_authority(order)

            exchange_order_id, evidence = evidence_for_order(order, rows)
            if exchange_order_id is None or not evidence:
                return MaterializationResult(state="evidence_missing")
            if not order.get("exchange_order_id"):
                repo.update_order(order_id, exchange_order_id=exchange_order_id)
                order["exchange_order_id"] = exchange_order_id

            db = repo.get_db()
            local_rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                    (order_id,),
                ).fetchall()
            ]
            local_quantity, _, _ = _fill_totals(
                local_rows,
                symbol=order["symbol"],
            )
            exchange_quantity = sum(item.quantity for item in evidence)
            requested_quantity = float(order["quantity"])
            tolerance = max(1e-12, requested_quantity * 1e-9)
            execution_mode = str(order.get("execution_mode") or "")
            if local_quantity > exchange_quantity + tolerance:
                raise MaterializationIntegrityError(
                    "local close quantity exceeds complete private evidence"
                )
            _verify_durable_fills(
                db,
                local_rows,
                evidence,
                quantity_tolerance=tolerance,
                lifecycle="close",
                execution_mode=execution_mode,
            )
            local_ids = {str(row["id"]) for row in local_rows}
            unseen_evidence = [
                item
                for item in evidence
                if not _private_fill_already_local(
                    item,
                    local_ids,
                    execution_mode=execution_mode,
                )
            ]
            delta_evidence = _tail_delta(
                unseen_evidence, max(0.0, exchange_quantity - local_quantity)
            )
            for item in delta_evidence:
                repo.insert_fill(
                    Fill(
                        id=durable_private_fill_id(
                            execution_mode, item.fill_id
                        ),
                        order_id=order_id,
                        fill_price=item.price,
                        fill_quantity=item.quantity,
                        fee=item.fee,
                        fee_currency=item.fee_currency,
                        fee_source="reconciled",
                        filled_at=item.filled_at,
                    )
                )

            repo.update_order(
                order_id,
                exchange_order_id=exchange_order_id,
                position_id=position["id"],
            )
            if exchange_quantity < requested_quantity - tolerance:
                repo.update_order(
                    order_id,
                    status="partially_filled",
                    error_reason=None,
                    filled_at=None,
                )
                repo.update_position(
                    position["id"],
                    pending_close_order_id=order_id,
                    pending_close_status="partially_filled",
                )
                return MaterializationResult(
                    state="partially_filled",
                    new_fill_count=len(delta_evidence),
                    position_id=position["id"],
                    exchange_order_id=exchange_order_id,
                )
            if exchange_quantity > requested_quantity + tolerance:
                repo.update_order(
                    order_id,
                    status="overfilled",
                    error_reason="exchange_close_quantity_exceeds_position",
                    filled_at=max(item.filled_at for item in evidence),
                )
                repo.update_position(
                    position["id"],
                    pending_close_order_id=order_id,
                    pending_close_status="overfilled",
                )
                return MaterializationResult(
                    state="overfilled",
                    new_fill_count=len(delta_evidence),
                    position_id=position["id"],
                    exchange_order_id=exchange_order_id,
                )

            # Exact complete closes are handled before this transaction by
            # _materialize_complete_private_close(), which lets close_position
            # own the commit and delays all external side effects until after it.
            raise MaterializationIntegrityError(
                "complete private close bypassed atomic close callback"
            )
    except Exception:
        # If any local write fails after private evidence was accepted, preserve
        # an explicit retry-suppressing handoff state in a fresh transaction.
        try:
            with repo.transaction(immediate=True):
                order = repo.get_order(order_id)
                if order is not None:
                    repo.update_order(
                        order_id,
                        status="materialization_missing",
                        error_reason="exchange_fill_local_materialization_missing",
                        filled_at=None,
                    )
                    position = _position_for_close_order(order)
                    if position is not None and position.get("status") == "open":
                        repo.update_position(
                            position["id"],
                            pending_close_order_id=order_id,
                            pending_close_status="materialization_missing",
                        )
        except Exception:
            pass
        raise
