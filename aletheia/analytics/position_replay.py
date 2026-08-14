"""Exact-identity, read-only replay facts for one Position.

This module reports evidence only. It never imports or calls behavior owners.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Iterable, Mapping

from aletheia.analytics.outcome_integrity import assess_reportable_outcome
from aletheia.analytics import trade_path as trade_path_analytics
from aletheia.storage import repositories as repo
from aletheia.storage.market_ohlcv_tiering import _canonical_row, _content_hash


FACT_CONTRACT_VERSION = "profit_loop_fact/v1"
_BAR_MS = 60_000


def _utc(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _json(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _row(db: Any, table: str, identity: object) -> dict[str, Any] | None:
    if identity in (None, ""):
        return None
    found = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    return dict(found) if found is not None else None


def _rows(db: Any, sql: str, params: Iterable[object]) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, tuple(params)).fetchall()]


def _at_or_before(row: dict[str, Any] | None, cutoff: datetime) -> bool:
    if row is None:
        return False
    seen_timestamp = False
    for field in ("created_at", "evaluated_at", "filled_at", "opened_at", "closed_at"):
        raw = row.get(field)
        if raw in (None, ""):
            continue
        seen_timestamp = True
        value = _utc(raw)
        if value is None or value > cutoff:
            return False
    return seen_timestamp


def _snapshot_at_or_before(
    row: dict[str, Any] | None,
    cutoff: datetime,
    *,
    fields: tuple[str, ...],
) -> bool:
    if row is None:
        return False
    seen_timestamp = False
    for field in fields:
        raw = row.get(field)
        if raw in (None, ""):
            continue
        seen_timestamp = True
        value = _utc(raw)
        if value is None or value > cutoff:
            return False
    return seen_timestamp


def _pi_decision(db: Any, proposal_id: str, cutoff: datetime) -> tuple[dict[str, Any] | None, str | None]:
    decisions = _rows(
        db,
        "SELECT * FROM pi_decisions WHERE proposal_id=? "
        "AND action!='signal_pending' ORDER BY datetime(created_at), rowid",
        (proposal_id,),
    )
    visible = [row for row in decisions if _at_or_before(row, cutoff)]
    if len(visible) > 1:
        return None, "ambiguous_pi_decision_for_proposal"
    return (visible[0], None) if visible else (None, "pi_decision_not_recorded")


def _runtime_identity(db: Any, rows: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    producers = [row for row in rows if row]
    identities = [
        (
            row.get("runtime_session_id"),
            row.get("producer_commit"),
            row.get("behavior_fingerprint"),
        )
        for row in producers
    ]
    identities.extend(
        (
            row.get("closed_runtime_session_id"),
            row.get("closed_producer_commit"),
            row.get("closed_behavior_fingerprint"),
        )
        for row in producers
        if row.get("closed_at")
    )
    session_ids = sorted({str(identity[0]) for identity in identities if identity[0]})
    sessions = {
        str(found["session_id"]): dict(found)
        for session_id in session_ids
        if (found := db.execute("SELECT * FROM runtime_sessions WHERE session_id=?", (session_id,)).fetchone())
        is not None
    }
    unresolved_session_ids = sorted(set(session_ids) - sessions.keys())
    resolved_identities = []
    incomplete_identity_count = 0
    mismatched_identity_count = 0
    for session_id, direct_commit, direct_fingerprint in identities:
        direct_pair = (str(direct_commit), str(direct_fingerprint)) if direct_commit and direct_fingerprint else None
        session = sessions.get(str(session_id)) if session_id else None
        session_pair = (
            (str(session["boot_commit"]), str(session["behavior_fingerprint"]))
            if session and session.get("boot_commit") and session.get("behavior_fingerprint")
            else None
        )
        if (direct_commit or direct_fingerprint) and direct_pair is None:
            incomplete_identity_count += 1
            continue
        if session_id and session_pair is None:
            incomplete_identity_count += 1
            continue
        if direct_pair and session_pair and direct_pair != session_pair:
            mismatched_identity_count += 1
            continue
        identity = direct_pair or session_pair
        if identity is None:
            incomplete_identity_count += 1
            continue
        resolved_identities.append(identity)
    errors = []
    if unresolved_session_ids:
        errors.append("runtime_session_not_recorded")
    if incomplete_identity_count:
        errors.append("producer_identity_incomplete")
    if mismatched_identity_count:
        errors.append("producer_identity_mismatch")
    return {
        "status": "available" if not errors else "unavailable",
        "errors": errors,
        "runtime_session_ids": session_ids,
        "unresolved_runtime_session_ids": unresolved_session_ids,
        "incomplete_identity_count": incomplete_identity_count,
        "mismatched_identity_count": mismatched_identity_count,
        "producer_commits": sorted({identity[0] for identity in resolved_identities}),
        "behavior_fingerprints": sorted({identity[1] for identity in resolved_identities}),
    }


def _instrument(discovery: dict[str, Any] | None, position: dict[str, Any]) -> dict[str, Any]:
    market = str(position.get("market_type") or "").lower()
    physical = "perp" if market in {"perp", "swap", "future", "futures", "usdt_futures"} else market
    return {
        "exchange": (discovery or {}).get("venue"),
        "symbol": position.get("symbol"),
        "market_type": physical or None,
        "timeframe": "1m",
    }


_COUNTERFACTUAL_WINDOW_BARS = 66


def _validated_replay_candles(
    candles: Iterable[Mapping[str, Any]],
    *,
    start: int,
    end: int,
    exchange: str,
    market_type: str,
) -> tuple[tuple[int, float, float, float, float, float, str], ...]:
    selected: list[tuple[int, float, float, float, float, float, str]] = []
    seen: set[int] = set()
    for row in candles:
        if not isinstance(row, Mapping):
            raise ValueError("holding_path_evidence_invalid")
        try:
            timestamp = int(row["ts"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("holding_path_evidence_invalid") from None
        if not start <= timestamp < end:
            continue
        if timestamp % _BAR_MS != 0 or timestamp in seen:
            raise ValueError("holding_path_timeline_invalid")
        row_exchange = str(row.get("exchange") or "").strip().lower()
        row_market_type = str(row.get("market_type") or "").strip().lower()
        if row_exchange != exchange.lower() or row_market_type != market_type.lower():
            raise ValueError("holding_path_identity_mismatch")
        if "is_closed" in row and row.get("is_closed") not in {True, 1, "true", "1"}:
            raise ValueError("holding_path_bar_not_closed")
        values = [_finite(row.get(key)) for key in ("open", "high", "low", "close", "volume")]
        if any(value is None for value in values):
            raise ValueError("holding_path_ohlcv_invalid")
        open_price, high, low, close, volume = values
        if (
            open_price is None
            or high is None
            or low is None
            or close is None
            or volume is None
            or min(open_price, high, low, close) <= 0
            or volume < 0
            or high < max(open_price, close)
            or low > min(open_price, close)
            or low > high
        ):
            raise ValueError("holding_path_ohlcv_invalid")
        selected.append(_canonical_row(row))
        seen.add(timestamp)
    expected = tuple(range(start, end, _BAR_MS))
    if tuple(row[0] for row in selected) != expected:
        raise ValueError("holding_path_timeline_invalid")
    return tuple(selected)


def _ohlcv_evidence(
    db: Any,
    *,
    position: dict[str, Any],
    path: dict[str, Any] | None,
    cutoff: datetime,
) -> dict[str, Any]:
    base = {
        "availability": "unavailable",
        "unavailable_reason": "path_evidence_unavailable",
        "identity": {
            "exchange": (path or {}).get("ohlcv_exchange"),
            "symbol": position.get("symbol"),
            "market_type": (path or {}).get("ohlcv_market_type"),
            "timeframe": "1m",
        },
        "window_start_ts_ms": (path or {}).get("entry_ts_ms"),
        "window_end_ts_ms": None,
        "row_count": 0,
        "expected_row_count": None,
        "rows": [],
        "ohlcv_window_hash": None,
    }
    if path is None or path.get("path_evidence_status") == "unavailable":
        base["unavailable_reason"] = (path or {}).get("path_evidence_reason") or base["unavailable_reason"]
        return base
    entry_ts = path.get("entry_ts_ms")
    exit_ts = path.get("exit_ts_ms")
    exchange = path.get("ohlcv_exchange")
    market_type = path.get("ohlcv_market_type")
    if any(value is None for value in (entry_ts, exit_ts, exchange, market_type)):
        base["unavailable_reason"] = "ohlcv_window_identity_incomplete"
        return base
    entry_ts_i = int(entry_ts) if entry_ts is not None else 0
    exit_ts_i = int(exit_ts) if exit_ts is not None else 0
    start = entry_ts_i - entry_ts_i % _BAR_MS
    actual_end = ((exit_ts_i + _BAR_MS - 1) // _BAR_MS) * _BAR_MS
    counterfactual_end = start + _COUNTERFACTUAL_WINDOW_BARS * _BAR_MS
    cutoff_end = int(cutoff.timestamp() * 1000) // _BAR_MS * _BAR_MS
    end = min(max(actual_end, counterfactual_end), cutoff_end)
    expected_i = max(0, (end - start) // _BAR_MS)
    if expected_i <= 0:
        base.update({"expected_row_count": expected_i, "unavailable_reason": "holding_path_cutoff_before_window"})
        return base
    base.update(
        {
            "window_start_ts_ms": start,
            "window_end_ts_ms": end,
            "expected_row_count": expected_i,
        }
    )
    try:
        candles = repo.get_ohlcv_candles(
            str(position.get("symbol") or ""),
            "1m",
            limit=expected_i,
            exchange=str(exchange),
            market_type=str(market_type),
            before_ts=end,
            db=db,
        )
        canonical = _validated_replay_candles(
            candles,
            start=start,
            end=end,
            exchange=str(exchange),
            market_type=str(market_type),
        )
    except ValueError as exc:
        base["unavailable_reason"] = str(exc)
        return base
    except Exception:
        base["unavailable_reason"] = "holding_path_evidence_invalid"
        return base
    if len(canonical) != expected_i:
        base.update(
            {
                "unavailable_reason": "holding_path_coverage_unavailable",
                "row_count": len(canonical),
            }
        )
        return base
    base.update(
        {
            "availability": "available",
            "unavailable_reason": None,
            "window_start_ts_ms": start,
            "window_end_ts_ms": end,
            "rows": [list(row) for row in canonical],
            "row_count": len(canonical),
            "ohlcv_window_hash": _content_hash(canonical),
            "expected_row_count": expected_i,
        }
    )
    return base


def _close_fill_vwap(close_fills: Iterable[dict[str, Any]]) -> float | None:
    total_quantity = 0.0
    total_notional = 0.0
    rows = list(close_fills)
    if not rows:
        return None
    for row in rows:
        price = _finite(row.get("fill_price"))
        quantity = _finite(row.get("fill_quantity"))
        if price is None or quantity is None or price <= 0 or quantity <= 0:
            return None
        total_quantity += quantity
        total_notional += price * quantity
    return total_notional / total_quantity if total_quantity > 0 else None


def _economics(
    position: dict[str, Any], close_fills: Iterable[dict[str, Any]] = ()
) -> dict[str, Any]:
    entry = _finite(position.get("entry_price"))
    # positions has no persisted exit_price column. Close-fill VWAP is therefore
    # the only durable exit-price source for replay economics.
    exit_price = _close_fill_vwap(close_fills)
    assessment_position = dict(position)
    assessment_position["current_price"] = None
    assessment_position["exit_price"] = exit_price
    assessment = assess_reportable_outcome(assessment_position)
    quantity = _finite(position.get("quantity"))
    entry_fee = _finite(position.get("entry_fee"))
    exit_fee = _finite(position.get("exit_fee"))
    funding = _finite(position.get("funding_cost"))
    realized = _finite(position.get("realized_pnl"))
    direction = str(position.get("direction") or "").lower()
    gross = None
    if entry is not None and exit_price is not None and quantity is not None:
        if direction == "long":
            gross = (exit_price - entry) * quantity
        elif direction == "short":
            gross = (entry - exit_price) * quantity
    expected_net = None
    if gross is not None and entry_fee is not None and exit_fee is not None and funding is not None:
        expected_net = gross - entry_fee - exit_fee - funding
    residual = round(realized - expected_net, 12) if realized is not None and expected_net is not None else None
    tolerance = (
        max(1e-8, abs(expected_net) * 1e-8, entry * quantity * 1e-10)
        if expected_net is not None and entry is not None and quantity is not None
        else None
    )
    residual_within_tolerance = (
        abs(residual) <= tolerance if residual is not None and tolerance is not None else None
    )
    economics_available = (
        assessment.eligible
        and exit_price is not None
        and residual_within_tolerance is True
    )
    unavailable_reason = (
        "exit_fill_price_unavailable"
        if exit_price is None
        else "accounting_residual_unavailable"
        if residual_within_tolerance is None
        else "accounting_residual_out_of_tolerance"
        if residual_within_tolerance is False
        else assessment.reason
        if not assessment.eligible
        else None
    )
    return {
        "availability": "available" if economics_available else "unavailable",
        "unavailable_reason": unavailable_reason,
        "gross_pnl": gross,
        "entry_fee": entry_fee,
        "exit_fee": exit_fee,
        "funding_cost": funding,
        "net_pnl": realized,
        "expected_net_pnl": expected_net,
        "accounting_residual": residual,
        "accounting_tolerance": tolerance,
        "residual_within_tolerance": residual_within_tolerance,
        "outcome_key": assessment.outcome_key,
        "horizon": assessment.horizon,
    }


def build_position_replay(
    position_id: str,
    *,
    as_of_at: str | datetime | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Return one immutable fact projection without mutating runtime state."""
    source = db or repo.get_db()
    parsed_cutoff = _utc(as_of_at)
    if as_of_at is not None and parsed_cutoff is None:
        return {
            "contract_version": FACT_CONTRACT_VERSION,
            "fact_unit": "position_fact",
            "fact_id": position_id,
            "as_of_at": str(as_of_at),
            "read_only": True,
            "availability": "unavailable",
            "unavailable_reason": "invalid_as_of_at",
        }
    cutoff = parsed_cutoff or datetime.now(timezone.utc)
    position = _row(source, "positions", position_id)
    base: dict[str, Any] = {
        "contract_version": FACT_CONTRACT_VERSION,
        "fact_unit": "position_fact",
        "fact_id": position_id,
        "as_of_at": cutoff.isoformat(),
        "read_only": True,
        "availability": "unavailable",
        "unavailable_reason": "position_not_found",
    }
    if position is None:
        return base
    if str(position.get("status") or "").lower() != "closed":
        base["unavailable_reason"] = "position_not_closed"
        return base
    closed_at = _utc(position.get("closed_at"))
    if closed_at is None:
        base["unavailable_reason"] = "position_close_time_unavailable"
        return base
    opened_at = _utc(position.get("opened_at"))
    if opened_at is None:
        base["unavailable_reason"] = "position_open_time_unavailable"
        return base
    if opened_at >= closed_at:
        base["unavailable_reason"] = "position_time_order_invalid"
        return base
    if closed_at > cutoff:
        base["unavailable_reason"] = "position_state_mutated_after_cutoff"
        return base
    if not _at_or_before(position, cutoff):
        return base

    proposal = _row(source, "trade_proposals", position.get("proposal_id"))
    if proposal is not None and not _at_or_before(proposal, cutoff):
        proposal = None
    entry_order = _row(source, "orders", position.get("order_id"))
    if entry_order is not None and not _at_or_before(entry_order, cutoff):
        entry_order = None
    opportunity = _row(source, "opportunities", position.get("opportunity_id"))
    if opportunity is not None and not _at_or_before(opportunity, cutoff):
        opportunity = None
    if opportunity is None:
        opportunity = _row(source, "serenity_opportunities", position.get("opportunity_id"))
        if opportunity is not None and not _at_or_before(opportunity, cutoff):
            opportunity = None
    member = _row(source, "universe_members", (opportunity or {}).get("universe_member_id"))
    asset = _row(source, "universe_registry", (member or {}).get("asset_id"))
    pi_decision, pi_error = (
        _pi_decision(source, str(proposal["id"]), cutoff) if proposal is not None else (None, "proposal_not_recorded")
    )
    risk = _row(source, "risk_decisions", (entry_order or {}).get("risk_decision_id"))
    if risk is not None and not _at_or_before(risk, cutoff):
        risk = None
    entry_fills = (
        _rows(
            source,
            "SELECT * FROM fills WHERE order_id=? ORDER BY datetime(filled_at), rowid",
            (entry_order["id"],),
        )
        if entry_order is not None
        else []
    )
    entry_fills = [row for row in entry_fills if _at_or_before(row, cutoff)]
    close_orders = _rows(
        source,
        "SELECT * FROM orders WHERE position_id=? "
        "AND COALESCE(trim(close_reason),'')!='' "
        "ORDER BY datetime(created_at), rowid",
        (position_id,),
    )
    close_orders = [row for row in close_orders if _at_or_before(row, cutoff)]
    close_fills = []
    for order in close_orders:
        close_fills.extend(
            _rows(
                source,
                "SELECT * FROM fills WHERE order_id=? ORDER BY datetime(filled_at), rowid",
                (order["id"],),
            )
        )
    close_fills = [row for row in close_fills if _at_or_before(row, cutoff)]
    exit_plans = _rows(
        source,
        "SELECT * FROM position_exit_plan_versions WHERE position_id=? ORDER BY version, datetime(effective_at), rowid",
        (position_id,),
    )
    exit_plans = [
        row
        for row in exit_plans
        if (effective_at := _utc(row.get("effective_at"))) is not None
        and effective_at <= cutoff
    ]
    protection_row = source.execute(
        "SELECT * FROM position_protection_state WHERE position_id=?",
        (position_id,),
    ).fetchone()
    protection = dict(protection_row) if protection_row is not None else None
    if protection is not None and not _snapshot_at_or_before(
        protection,
        cutoff,
        fields=("created_at", "updated_at", "activated_at", "gap_detected_at", "close_filled_at"),
    ):
        protection = None

    causal_errors = []
    if proposal is None:
        causal_errors.append("proposal_not_recorded")
    if entry_order is None:
        causal_errors.append("entry_order_not_recorded")
    if proposal is not None and proposal.get("opportunity_id") != position.get("opportunity_id"):
        causal_errors.append("proposal_opportunity_mismatch")
    if entry_order is not None and entry_order.get("proposal_id") != position.get("proposal_id"):
        causal_errors.append("order_proposal_mismatch")
    if entry_order is not None and entry_order.get("position_id") not in {None, "", position_id}:
        causal_errors.append("order_position_mismatch")
    if risk is None:
        causal_errors.append("authoritative_risk_not_recorded")
    elif (
        risk.get("proposal_id") != position.get("proposal_id")
        or risk.get("decision_role") != "authoritative"
        or entry_order is None
        or entry_order.get("risk_decision_id") != risk.get("id")
    ):
        causal_errors.append("authoritative_risk_identity_mismatch")
    if pi_error:
        causal_errors.append(pi_error)

    try:
        path = getattr(trade_path_analytics, "build_position_trade_path")(
            position_id, db=source, as_of_at=cutoff
        )
    except Exception:
        path = None
    ohlcv = _ohlcv_evidence(source, position=position, path=path, cutoff=cutoff)
    economics = _economics(position, close_fills)
    provenance = _runtime_identity(
        source,
        [position, opportunity, proposal, pi_decision, risk, entry_order, *entry_fills, *close_orders, *close_fills],
    )
    market_identity = _instrument(asset, position)
    fact_errors = list(causal_errors)
    if provenance.get("status") != "available":
        fact_errors.append("producer_evidence_unavailable")
    if economics.get("availability") != "available":
        fact_errors.append(str(economics.get("unavailable_reason") or "economics_unavailable"))
    if opportunity is None:
        fact_errors.append("discovery_unavailable")
    if proposal is None or pi_decision is None or risk is None:
        fact_errors.append("decision_unavailable")
    if entry_order is None or not entry_fills:
        fact_errors.append("execution_unavailable")
    if path is None or path.get("path_evidence_status") != "available" or ohlcv.get("availability") != "available":
        fact_errors.append("holding_evidence_unavailable")
    if not close_orders or not close_fills:
        fact_errors.append("exit_unavailable")
    fact_errors = list(dict.fromkeys(fact_errors))
    position_snapshot = dict(position)
    position_snapshot["current_price"] = None
    position_snapshot["unrealized_pnl"] = None

    base.update(
        {
            "availability": "available" if not fact_errors else "unavailable",
            "unavailable_reason": None if not fact_errors else fact_errors[0],
            "event_at": position.get("opened_at"),
            "evidence_available_at": position.get("closed_at") or cutoff.isoformat(),
            "identity": {
                "position_id": position_id,
                "opportunity_id": position.get("opportunity_id"),
                "proposal_id": position.get("proposal_id"),
                "pi_decision_id": (pi_decision or {}).get("id"),
                "risk_decision_id": (risk or {}).get("id"),
                "entry_order_id": position.get("order_id"),
                "entry_fill_ids": [row["id"] for row in entry_fills],
                "close_order_ids": [row["id"] for row in close_orders],
                "close_fill_ids": [row["id"] for row in close_fills],
            },
            "causal_link": {
                "status": "available" if not causal_errors else "unavailable",
                "errors": causal_errors,
            },
            "producer": provenance,
            "policy_identity": {
                "risk_policy_id": (risk or {}).get("policy_id"),
                "risk_policy_revision": (risk or {}).get("policy_revision"),
                "risk_policy_hash": (risk or {}).get("policy_hash"),
            },
            "cost_identity": {
                "entry_fee_sources": sorted(
                    {str(row.get("fee_source")) for row in entry_fills if row.get("fee_source")}
                ),
                "exit_fee_sources": sorted(
                    {str(row.get("fee_source")) for row in close_fills if row.get("fee_source")}
                ),
                "funding_source": position.get("funding_source"),
            },
            "market_data_identity": market_identity,
            "stages": {
                "discovery": {
                    "availability": "available" if opportunity is not None else "unavailable",
                    "opportunity": opportunity,
                    "universe_member": member,
                    "universe_asset": asset,
                },
                "decision": {
                    "availability": "available" if proposal and pi_decision and risk else "unavailable",
                    "pi_decision": pi_decision,
                    "proposal": {
                        **proposal,
                        "attribution_snapshot": _json(proposal.get("attribution_snapshot")),
                    }
                    if proposal
                    else None,
                    "authoritative_risk": risk,
                },
                "execution": {
                    "availability": "available" if entry_order and entry_fills else "unavailable",
                    "entry_order": entry_order,
                    "entry_fills": entry_fills,
                },
                "holding": {
                    "availability": (
                        "available"
                        if path is not None and path.get("path_evidence_status") == "available"
                        else "unavailable"
                    ),
                    "trade_path": path,
                    "ohlcv": ohlcv,
                    "exit_plan_versions": exit_plans,
                    "protection_state": protection,
                },
                "exit": {
                    "availability": (
                        "available"
                        if position.get("status") == "closed" and close_orders and close_fills
                        else "unavailable"
                    ),
                    "position": position_snapshot,
                    "close_orders": close_orders,
                    "close_fills": close_fills,
                    "settlement_identity": {
                        "reconciliation_identity": position.get("reconciliation_identity"),
                        "reconciliation_evidence": _json(position.get("reconciliation_evidence")),
                        "outcome_semantics_version": position.get("outcome_semantics_version"),
                    },
                },
            },
            "economics": economics,
        }
    )
    return base
