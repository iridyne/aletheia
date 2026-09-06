"""Exact-identity, read-only replay facts for one Position.

This module reports evidence only. It never imports or calls behavior owners.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import sqlite3
from typing import Any, Iterable, Mapping

from aletheia.analytics.outcome_integrity import assess_reportable_outcome
from aletheia.analytics import trade_path as trade_path_analytics
from aletheia.storage import repositories as repo
from aletheia.storage.market_ohlcv_tiering import _canonical_row, _content_hash
from aletheia.trading.trading_mandate import (
    validate_frozen_trading_mandate_provenance,
)


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


_ECONOMICS_AUDIT_EVENTS = frozenset({
    "exchange_position_funding_settled",
    "paper_position_funding_settled",
    "paper_position_economics_repaired",
    "paper_position_economics_corrected",
})


def _json_object(value: object) -> dict[str, Any] | None:
    parsed = _json(value)
    return parsed if isinstance(parsed, dict) else None


def _same_number(left: object, right: object) -> bool:
    left_value = _finite(left)
    right_value = _finite(right)
    return (
        left_value is not None
        and right_value is not None
        and math.isclose(left_value, right_value, rel_tol=0.0, abs_tol=1e-8)
    )


def _economics_visibility(
    db: Any,
    position: dict[str, Any],
    *,
    cutoff: datetime,
    explicit_cutoff: bool,
) -> tuple[bool, str | None, str | None]:
    """Prove that mutable funding economics were materialized by ``cutoff``."""
    if not explicit_cutoff or str(position.get("market_type") or "").lower() not in {
        "perp", "swap", "future", "futures", "usdt_futures",
    }:
        return True, None, None
    try:
        rows = _rows(
            db,
            "SELECT timestamp, event_type, after_state FROM audit_events "
            "WHERE entity_type='position' AND entity_id=? "
            "AND event_type IN (?, ?, ?, ?) "
            "ORDER BY datetime(timestamp), rowid",
            (position.get("id"), *_ECONOMICS_AUDIT_EVENTS),
        )
    except (sqlite3.Error, OSError):
        return False, "economics_provenance_unavailable", None
    parsed_rows: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        timestamp = _utc(row.get("timestamp"))
        after = _json_object(row.get("after_state"))
        if timestamp is None or after is None:
            return False, "economics_provenance_malformed", None
        parsed_rows.append((timestamp, after))
    visible = [(timestamp, after) for timestamp, after in parsed_rows if timestamp <= cutoff]
    if not visible:
        return False, "economics_materialized_after_cutoff", None
    visible_at, visible_after = visible[-1]
    current_evidence = _json_object(position.get("reconciliation_evidence")) or {}
    current_fingerprint = current_evidence.get("funding_economics_fingerprint")
    visible_fingerprint = visible_after.get("funding_economics_fingerprint")
    if not current_fingerprint or current_fingerprint != visible_fingerprint:
        return False, "historical_economics_unavailable", visible_at.isoformat()
    for field in ("realized_pnl", "funding_cost"):
        if not _same_number(position.get(field), visible_after.get(field)):
            return False, "historical_economics_unavailable", visible_at.isoformat()
    if bool(position.get("outcome_eligible")) != bool(visible_after.get("outcome_eligible")):
        return False, "historical_economics_unavailable", visible_at.isoformat()
    if position.get("outcome_exclusion_reason") != visible_after.get("outcome_exclusion_reason"):
        return False, "historical_economics_unavailable", visible_at.isoformat()
    latest_at, latest_after = parsed_rows[-1]
    if latest_at > cutoff and latest_after != visible_after:
        return False, "historical_economics_unavailable", visible_at.isoformat()
    return True, None, visible_at.isoformat()


def _unavailable_economics(reason: str) -> dict[str, Any]:
    return {
        "availability": "unavailable",
        "unavailable_reason": reason,
        "gross_pnl": None,
        "entry_fee": None,
        "exit_fee": None,
        "funding_cost": None,
        "net_pnl": None,
        "expected_net_pnl": None,
        "accounting_residual": None,
        "accounting_tolerance": None,
        "residual_within_tolerance": None,
        "outcome_key": None,
        "horizon": None,
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


def _ohlcv_evidence(
    db: Any,
    *,
    position: dict[str, Any],
    path: dict[str, Any] | None,
    cutoff: datetime,
    explicit_cutoff: bool,
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
        "availability_provenance": [],
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
    base.update(
        {
            "window_start_ts_ms": start,
            "window_end_ts_ms": end,
            "expected_row_count": expected_i,
        }
    )
    candles = repo.get_ohlcv_candles(
        str(position.get("symbol") or ""),
        "1m",
        limit=expected_i,
        exchange=str(exchange),
        market_type=str(market_type),
        before_ts=end,
        db=db,
        include_provenance=True,
    )
    selected_candles = [row for row in candles if start <= int(row["ts"]) < end]
    if explicit_cutoff:
        availability: list[dict[str, Any]] = []
        for row in selected_candles:
            first_seen = _utc(row.get("first_persisted_at")) if isinstance(row, Mapping) else None
            last_mutated = _utc(row.get("last_mutated_at")) if isinstance(row, Mapping) else None
            if first_seen is None or last_mutated is None:
                base["unavailable_reason"] = "ohlcv_availability_unproven"
                return base
            if first_seen > cutoff or last_mutated > cutoff:
                base["unavailable_reason"] = "ohlcv_materialized_after_cutoff"
                return base
            availability.append({
                "ts": int(row["ts"]),
                "first_persisted_at": first_seen.isoformat(),
                "last_mutated_at": last_mutated.isoformat(),
            })
        base["availability_provenance"] = availability
    canonical = tuple(_canonical_row(row) for row in selected_candles)
    # #1272: require a contiguous, on-grid, duplicate-free window
    expected_ts_set = set(range(start, end, _BAR_MS))
    actual_ts_set = {int(row[0]) for row in canonical}  # first element of canonical tuple is ts
    if actual_ts_set != expected_ts_set:
        base.update(
            {
                "unavailable_reason": "holding_path_continuity_unavailable",
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


def _frozen_mandate_provenance(
    proposal: dict[str, Any] | None,
    *,
    source_identities: dict[str, Any],
) -> dict[str, Any]:
    """Expose only the Proposal-frozen pair; never resolve current config."""
    base = {
        "status": "not_recorded",
        "identity": None,
        "fit": None,
        "source_identities": source_identities,
        "unavailable_reason": "mandate_provenance_not_recorded",
    }
    if proposal is None:
        return base
    snapshot = _json(proposal.get("attribution_snapshot"))
    if not isinstance(snapshot, dict):
        return base
    has_identity = "trading_mandate_identity" in snapshot
    has_fit = "trading_mandate_fit" in snapshot
    if not has_identity and not has_fit:
        return base
    identity = snapshot.get("trading_mandate_identity")
    fit = snapshot.get("trading_mandate_fit")
    try:
        validate_frozen_trading_mandate_provenance(snapshot)
    except ValueError as exc:
        return {
            **base,
            "status": "mandate_unavailable",
            "identity": identity if isinstance(identity, dict) else None,
            "fit": fit,
            "unavailable_reason": "malformed_frozen_mandate_provenance",
            "validation_error": str(exc),
        }
    status = fit.get("status") if isinstance(fit, dict) else None
    if status not in {
        "in_scope",
        "out_of_scope",
        "not_assessable",
        "not_configured",
        "mandate_unavailable",
    }:
        return {
            **base,
            "status": "mandate_unavailable",
            "identity": identity if isinstance(identity, dict) else None,
            "fit": fit,
            "unavailable_reason": "unsupported_frozen_mandate_fit_status",
        }
    return {
        "status": status,
        "identity": identity,
        "fit": fit,
        "source_identities": source_identities,
        "unavailable_reason": None,
    }


def _economics(position: dict[str, Any], *, db: Any = None) -> dict[str, Any]:
    assessment = assess_reportable_outcome(position, db=db)
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
    if isinstance(as_of_at, str) and as_of_at != "" and _utc(as_of_at) is None:
        raise ValueError("invalid as_of_at")
    if db is None:
        from aletheia.storage.sqlite import readonly_db

        try:
            with readonly_db() as read_db:
                return build_position_replay(
                    position_id,
                    as_of_at=as_of_at,
                    db=read_db,
                )
        except FileNotFoundError:
            cutoff = _utc(as_of_at) or datetime.now(timezone.utc)
            return {
                "contract_version": FACT_CONTRACT_VERSION,
                "fact_unit": "position_fact",
                "fact_id": position_id,
                "as_of_at": cutoff.isoformat(),
                "read_only": True,
                "availability": "unavailable",
                "unavailable_reason": "database_not_available",
                "trading_mandate": {
                    "status": "not_recorded",
                    "identity": None,
                    "fit": None,
                    "source_identities": {
                        "opportunity_id": None,
                        "decision_id": None,
                        "proposal_id": None,
                    },
                    "unavailable_reason": "mandate_provenance_not_recorded",
                },
                "trading_mandate_identity": None,
                "trading_mandate_fit": None,
            }
    source = db
    explicit_cutoff = as_of_at not in (None, "")
    cutoff = _utc(as_of_at) or datetime.now(timezone.utc)
    started_txn = not getattr(source, "in_transaction", False)
    if started_txn:
        source.execute("BEGIN DEFERRED")
    try:
        return _build_position_replay_inner(
            position_id,
            cutoff=cutoff,
            explicit_cutoff=explicit_cutoff,
            source=source,
        )
    finally:
        if started_txn:
            source.execute("ROLLBACK")


def _build_position_replay_inner(
    position_id: str,
    *,
    cutoff: datetime,
    explicit_cutoff: bool,
    source: Any,
) -> dict[str, Any]:
    position = _row(source, "positions", position_id)
    base: dict[str, Any] = {
        "contract_version": FACT_CONTRACT_VERSION,
        "fact_unit": "position_fact",
        "fact_id": position_id,
        "as_of_at": cutoff.isoformat(),
        "read_only": True,
        "availability": "unavailable",
        "unavailable_reason": "position_not_found",
        "trading_mandate": {
            "status": "not_recorded",
            "identity": None,
            "fit": None,
            "source_identities": {
                "opportunity_id": None,
                "decision_id": None,
                "proposal_id": None,
            },
            "unavailable_reason": "mandate_provenance_not_recorded",
        },
        "trading_mandate_identity": None,
        "trading_mandate_fit": None,
    }
    if position is None or not position.get("opened_at") or not _at_or_before(position, cutoff):
        return base
    closed_at = _utc(position.get("closed_at"))
    if closed_at is not None and closed_at > cutoff:
        base["unavailable_reason"] = "position_state_mutated_after_cutoff"
        return base

    proposal = _row(source, "trade_proposals", position.get("proposal_id"))
    if proposal is not None and not _at_or_before(proposal, cutoff):
        proposal = None
    entry_order = _row(source, "orders", position.get("order_id"))
    if entry_order is not None and not _at_or_before(entry_order, cutoff):
        entry_order = None
    opportunity = _row(source, "opportunities", position.get("opportunity_id"))
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
    # #1271: collect all entry legs — original order + scale-in orders for this position
    scale_in_orders = _rows(
        source,
        "SELECT * FROM orders WHERE position_id=? "
        "AND COALESCE(trim(close_reason),'')='' "
        "ORDER BY datetime(created_at), rowid",
        (position_id,),
    )
    scale_in_orders = [row for row in scale_in_orders if _at_or_before(row, cutoff)]
    all_entry_order_ids: list[str] = []
    if entry_order is not None:
        all_entry_order_ids.append(str(entry_order["id"]))
    for scale_order in scale_in_orders:
        oid = str(scale_order.get("id") or "")
        if oid and oid not in all_entry_order_ids:
            all_entry_order_ids.append(oid)
    entry_fills = []
    for oid in all_entry_order_ids:
        fills_for_order = _rows(
            source,
            "SELECT * FROM fills WHERE order_id=? ORDER BY datetime(filled_at), rowid",
            (oid,),
        )
        entry_fills.extend(row for row in fills_for_order if _at_or_before(row, cutoff))
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
        if (
            (effective_at := _utc(row.get("effective_at"))) is not None
            and effective_at <= cutoff
        )
        or (not explicit_cutoff and row.get("effective_at") in (None, ""))
    ]
    # #1270: apply cutoff to protection state
    protection_row = source.execute(
        "SELECT * FROM position_protection_state WHERE position_id=?",
        (position_id,),
    ).fetchone()
    protection: dict[str, Any] | None = None
    if protection_row is not None:
        prot_raw = dict(protection_row)
        updated_at = _utc(prot_raw.get("updated_at"))
        if updated_at is not None and updated_at <= cutoff:
            protection = prot_raw
        elif not explicit_cutoff and updated_at is None:
            protection = prot_raw
        else:
            activated_at = _utc(prot_raw.get("activated_at"))
            if activated_at is not None and activated_at <= cutoff:
                # activated before cutoff but mutable state updated after — emit identity only
                protection = {
                    "position_id": prot_raw.get("position_id"),
                    "activated_at": prot_raw.get("activated_at"),
                    "state_at_cutoff": "unavailable",
                }
            # else: activated after cutoff or timestamp provenance is missing — omit.

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
    # #1271: verify entry fill chains
    if entry_order is not None and not entry_fills:
        causal_errors.append("entry_fills_missing")
    # #1271: closed position must have a close fill chain
    if position.get("status") == "closed" and not close_fills:
        causal_errors.append("close_fills_missing")

    mandate_source_identities = {
        "opportunity_id": position.get("opportunity_id"),
        "decision_id": (pi_decision or {}).get("id"),
        "proposal_id": (proposal or {}).get("id") or position.get("proposal_id"),
    }
    mandate_provenance = _frozen_mandate_provenance(
        proposal,
        source_identities=mandate_source_identities,
    )
    economics_visible, economics_reason, economics_at = _economics_visibility(
        source,
        position,
        cutoff=cutoff,
        explicit_cutoff=explicit_cutoff,
    )
    path = None
    if economics_visible:
        path_builder = getattr(trade_path_analytics, "build_position_trade_path")
        path_kwargs: dict[str, Any] = {"db": source}
        if explicit_cutoff:
            path_kwargs["as_of_at"] = cutoff
        try:
            path = path_builder(position_id, **path_kwargs)
        except TypeError as exc:
            if "unexpected keyword argument 'as_of_at'" not in str(exc):
                raise
            path = path_builder(position_id, db=source)
    ohlcv = _ohlcv_evidence(
        source,
        position=position,
        path=path,
        cutoff=cutoff,
        explicit_cutoff=explicit_cutoff,
    )
    economics = (
        _economics(position, db=source)
        if economics_visible
        else _unavailable_economics(economics_reason or "economics_unavailable")
    )
    position_projection = dict(position)
    if not economics_visible:
        for field in (
            "realized_pnl",
            "current_price",
            "exit_price",
            "entry_fee",
            "exit_fee",
            "funding_cost",
            "funding_source",
            "outcome_eligible",
            "outcome_exclusion_reason",
            "reconciliation_identity",
            "reconciliation_evidence",
            "outcome_semantics_version",
        ):
            position_projection[field] = None
    provenance = _runtime_identity(
        source,
        [position, opportunity, proposal, pi_decision, risk, entry_order, *scale_in_orders, *entry_fills, *close_orders, *close_fills],
    )
    market_identity = _instrument(asset, position)

    base.update(
        {
            "availability": "available",
            "unavailable_reason": None,
            "trading_mandate": mandate_provenance,
            "trading_mandate_identity": mandate_provenance["identity"],
            "trading_mandate_fit": mandate_provenance["fit"],
            "event_at": position.get("opened_at"),
            "evidence_available_at": position.get("closed_at") or cutoff.isoformat(),
            "economic_evidence_available_at": economics_at,
            "identity": {
                "position_id": position_id,
                "opportunity_id": position.get("opportunity_id"),
                "proposal_id": position.get("proposal_id"),
                "pi_decision_id": (pi_decision or {}).get("id"),
                "risk_decision_id": (risk or {}).get("id"),
                "trading_mandate_identity": mandate_provenance["identity"],
                "trading_mandate_fit": mandate_provenance["fit"],
                "entry_order_id": position.get("order_id"),
                "entry_order_ids": all_entry_order_ids,
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
                "cost_model_id": "paper-execution-reference/v1",
                "entry_fee_sources": sorted(
                    {str(row.get("fee_source")) for row in entry_fills if row.get("fee_source")}
                ),
                "exit_fee_sources": sorted(
                    {str(row.get("fee_source")) for row in close_fills if row.get("fee_source")}
                ),
                "funding_source": position_projection.get("funding_source"),
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
                    "position": position_projection,
                    "close_orders": close_orders,
                    "close_fills": close_fills,
                    "settlement_identity": {
                        "reconciliation_identity": position_projection.get("reconciliation_identity"),
                        "reconciliation_evidence": _json(position_projection.get("reconciliation_evidence")),
                        "outcome_semantics_version": position_projection.get("outcome_semantics_version"),
                    },
                },
            },
            "economics": economics,
        }
    )
    return base
