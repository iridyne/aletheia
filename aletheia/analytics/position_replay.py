"""Exact-identity, read-only replay facts for one Position.

This module reports evidence only. It never imports or calls behavior owners.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Iterable

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
    for field in ("created_at", "evaluated_at", "filled_at", "opened_at"):
        if row.get(field):
            value = _utc(row[field])
            return value is not None and value <= cutoff
    return True


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
    session_ids = sorted({str(row.get("runtime_session_id")) for row in rows if row and row.get("runtime_session_id")})
    sessions = []
    for session_id in session_ids:
        found = db.execute("SELECT * FROM runtime_sessions WHERE session_id=?", (session_id,)).fetchone()
        if found is not None:
            sessions.append(dict(found))
    return {
        "runtime_session_ids": session_ids,
        "producer_commits": sorted(
            {str(row.get("producer_commit")) for row in rows if row and row.get("producer_commit")}
            | {str(row.get("boot_commit")) for row in sessions if row.get("boot_commit")}
        ),
        "behavior_fingerprints": sorted(
            {str(row.get("behavior_fingerprint")) for row in sessions if row.get("behavior_fingerprint")}
        ),
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


def _ohlcv_evidence(
    db: Any,
    *,
    position: dict[str, Any],
    path: dict[str, Any] | None,
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
        "window_end_ts_ms": (path or {}).get("exit_ts_ms"),
        "row_count": 0,
        "expected_row_count": (path or {}).get("holding_bars_expected"),
        "rows": [],
        "ohlcv_window_hash": None,
    }
    if path is None or path.get("path_evidence_status") == "unavailable":
        base["unavailable_reason"] = (path or {}).get("path_evidence_reason") or base["unavailable_reason"]
        return base
    entry_ts = path.get("entry_ts_ms")
    exit_ts = path.get("exit_ts_ms")
    expected = path.get("holding_bars_expected")
    exchange = path.get("ohlcv_exchange")
    market_type = path.get("ohlcv_market_type")
    if any(value is None for value in (entry_ts, exit_ts, expected, exchange, market_type)):
        base["unavailable_reason"] = "ohlcv_window_identity_incomplete"
        return base
    entry_ts_i = int(entry_ts) if entry_ts is not None else 0
    exit_ts_i = int(exit_ts) if exit_ts is not None else 0
    expected_i = int(expected) if expected is not None else 0
    start = entry_ts_i - entry_ts_i % _BAR_MS
    end = ((exit_ts_i + _BAR_MS - 1) // _BAR_MS) * _BAR_MS
    candles = repo.get_ohlcv_candles(
        str(position.get("symbol") or ""),
        "1m",
        limit=expected_i,
        exchange=str(exchange),
        market_type=str(market_type),
        before_ts=end,
        db=db,
    )
    canonical = tuple(_canonical_row(row) for row in candles if start <= int(row["ts"]) < end)
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
        }
    )
    return base


def _economics(position: dict[str, Any]) -> dict[str, Any]:
    assessment = assess_reportable_outcome(position)
    entry = _finite(position.get("entry_price"))
    exit_price = _finite(
        position.get("current_price") if position.get("current_price") is not None else position.get("exit_price")
    )
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
    return {
        "availability": "available" if assessment.eligible else "unavailable",
        "unavailable_reason": None if assessment.eligible else assessment.reason,
        "gross_pnl": gross,
        "entry_fee": entry_fee,
        "exit_fee": exit_fee,
        "funding_cost": funding,
        "net_pnl": realized,
        "expected_net_pnl": expected_net,
        "accounting_residual": residual,
        "accounting_tolerance": tolerance,
        "residual_within_tolerance": (
            abs(residual) <= tolerance if residual is not None and tolerance is not None else None
        ),
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
    cutoff = _utc(as_of_at) or datetime.now(timezone.utc)
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
    if position is None or not _at_or_before(position, cutoff):
        return base

    proposal = _row(source, "trade_proposals", position.get("proposal_id"))
    entry_order = _row(source, "orders", position.get("order_id"))
    opportunity = _row(source, "opportunities", position.get("opportunity_id"))
    if opportunity is None:
        opportunity = _row(source, "serenity_opportunities", position.get("opportunity_id"))
    member = _row(source, "universe_members", (opportunity or {}).get("universe_member_id"))
    asset = _row(source, "universe_registry", (member or {}).get("asset_id"))
    pi_decision, pi_error = (
        _pi_decision(source, str(proposal["id"]), cutoff) if proposal is not None else (None, "proposal_not_recorded")
    )
    risk = _row(source, "risk_decisions", (entry_order or {}).get("risk_decision_id"))
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
    exit_plans = [row for row in exit_plans if (_utc(row.get("effective_at")) or cutoff) <= cutoff]
    protection_row = source.execute(
        "SELECT * FROM position_protection_state WHERE position_id=?",
        (position_id,),
    ).fetchone()
    protection = dict(protection_row) if protection_row is not None else None

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

    path = getattr(trade_path_analytics, "build_position_trade_path")(position_id, db=source)
    ohlcv = _ohlcv_evidence(source, position=position, path=path)
    economics = _economics(position)
    provenance = _runtime_identity(
        source,
        [position, opportunity, proposal, pi_decision, risk, entry_order, *entry_fills, *close_orders, *close_fills],
    )
    market_identity = _instrument(asset, position)

    base.update(
        {
            "availability": "available",
            "unavailable_reason": None,
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
                    "availability": "available" if path is not None else "unavailable",
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
                    "position": position,
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
