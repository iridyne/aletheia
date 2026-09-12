"""Idempotent recovery for exchange closes missing private funding economics."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone

from aletheia.execution.broker_funding import (
    compute_stored_broker_funding,
    funding_query_window,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.storage.sqlite import get_db
from aletheia.portfolio.pnl import (
    calculated_realized_pnl,
    _close_event_matches_position,
    _commit_close_economics,
    _run_outcome_hooks,
)

logger = logging.getLogger("aletheia.broker_funding_settlement")

_EXCHANGE_FUNDING_MODES = (
    "bitget_demo",
    "bitget_live_limited",
    "okx_demo",
    "okx_live_limited",
    "binance_testnet",
    "binance_live_limited",
)

BROKER_FUNDING_HISTORY_EXPIRED = "broker_funding_history_expired"
_FUNDING_QUEUE_ID = "exchange_funding"


class FundingHistoryRetentionExpired(Exception):
    """Typed UTA profile result proving private funding history is irrecoverable."""

    def __init__(
        self,
        *,
        retention_days: int,
        profile_version: str,
        received_at: datetime,
        source: str,
    ) -> None:
        super().__init__("UTA funding history retention expired")
        self.retention_days = retention_days
        self.profile_version = profile_version
        self.received_at = received_at
        self.source = source


def _cursor_key(row: dict) -> tuple[str, int]:
    return str(row.get("closed_at") or ""), int(row["funding_queue_rowid"])


def _select_funding_queue_rows(
    limit: int,
) -> tuple[list[dict], tuple[str, int] | None]:
    """Read one bounded keyset page, wrapping only after reaching the tail."""
    db = get_db()
    cursor_row = db.execute(
        "SELECT last_closed_at, last_position_rowid "
        "FROM broker_funding_settlement_cursor WHERE queue_id=?",
        (_FUNDING_QUEUE_ID,),
    ).fetchone()
    cursor = (
        (str(cursor_row["last_closed_at"]), int(cursor_row["last_position_rowid"]))
        if cursor_row is not None
        else None
    )
    placeholders = ",".join("?" for _ in _EXCHANGE_FUNDING_MODES)
    base = f"""SELECT rowid AS funding_queue_rowid, * FROM positions
        WHERE status='closed' AND market_type='usdt_futures'
          AND execution_mode IN ({placeholders})
          AND realized_pnl IS NULL
          AND (outcome_exclusion_reason='incomplete_funding_economics'
               OR (outcome_exclusion_reason='incomplete_realized_economics' AND exit_fee IS NULL))"""

    def page(clause: str, params: tuple[object, ...], page_limit: int) -> list[dict]:
        return [
            dict(row)
            for row in db.execute(
                base + clause + " ORDER BY COALESCE(closed_at,''), rowid LIMIT ?",
                (*_EXCHANGE_FUNDING_MODES, *params, page_limit),
            ).fetchall()
        ]

    if cursor is None:
        return page("", (), limit), None
    closed_at, position_rowid = cursor
    rows = page(
        " AND (COALESCE(closed_at,'') > ? OR "
        "(COALESCE(closed_at,'') = ? AND rowid > ?))",
        (closed_at, closed_at, position_rowid),
        limit,
    )
    if len(rows) < limit:
        rows.extend(page(
            " AND (COALESCE(closed_at,'') < ? OR "
            "(COALESCE(closed_at,'') = ? AND rowid <= ?))",
            (closed_at, closed_at, position_rowid),
            limit - len(rows),
        ))
    return rows, cursor


def funding_recovery_state_counts() -> dict[str, int]:
    """Count durable unresolved and terminal exchange funding states."""
    placeholders = ",".join("?" for _ in _EXCHANGE_FUNDING_MODES)
    rows = get_db().execute(
        f"""SELECT outcome_exclusion_reason, COUNT(*) AS count
            FROM positions
            WHERE status='closed' AND market_type='usdt_futures'
              AND execution_mode IN ({placeholders})
              AND realized_pnl IS NULL
              AND outcome_exclusion_reason IN (
                  'incomplete_funding_economics',
                  'incomplete_realized_economics',
                  'broker_funding_history_expired'
              )
              AND (outcome_exclusion_reason != 'incomplete_realized_economics' OR exit_fee IS NULL)
            GROUP BY outcome_exclusion_reason""",
        _EXCHANGE_FUNDING_MODES,
    ).fetchall()
    counts = {
        str(row["outcome_exclusion_reason"]): int(row["count"])
        for row in rows
    }
    return {
        "retry_pending": counts.get("incomplete_funding_economics", 0) + counts.get("incomplete_realized_economics", 0),
        "terminal": counts.get(BROKER_FUNDING_HISTORY_EXPIRED, 0),
    }


def _advance_funding_queue_cursor(
    previous: tuple[str, int] | None,
    row: dict,
) -> bool:
    """CAS scheduling progress without changing Position owner state."""
    closed_at, position_rowid = _cursor_key(row)
    now = datetime.now(timezone.utc).isoformat()
    with repo.transaction(immediate=True):
        if previous is None:
            result = get_db().execute(
                "INSERT OR IGNORE INTO broker_funding_settlement_cursor "
                "(queue_id,last_closed_at,last_position_rowid,updated_at) "
                "VALUES (?,?,?,?)",
                (_FUNDING_QUEUE_ID, closed_at, position_rowid, now),
            )
        else:
            result = get_db().execute(
                "UPDATE broker_funding_settlement_cursor SET "
                "last_closed_at=?, last_position_rowid=?, updated_at=? "
                "WHERE queue_id=? AND last_closed_at=? AND last_position_rowid=?",
                (
                    closed_at,
                    position_rowid,
                    now,
                    _FUNDING_QUEUE_ID,
                    previous[0],
                    previous[1],
                ),
            )
    return result.rowcount == 1


def _fingerprint(evidence: dict) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _close_fill_rows(position: dict) -> list[dict]:
    """Resolve only the immutable Fill identities accepted by the full close."""
    from aletheia.execution.exchange_materialization import durable_private_fill_id

    evidence = json.loads(position.get("reconciliation_evidence") or "{}")
    ids = evidence.get("fill_ids") if isinstance(evidence, dict) else None
    if not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("close Fill identities unavailable")
    if len(set(ids)) != len(ids):
        raise ValueError("close Fill identities duplicated")
    rows = [dict(row) for row in get_db().execute(
        "SELECT f.*, o.exchange_order_id, o.execution_mode, o.symbol, o.market_type "
        "FROM fills f JOIN orders o ON o.id=f.order_id "
        "WHERE o.position_id=? AND o.close_reason IS NOT NULL",
        (position["id"],),
    ).fetchall()]
    resolved = []
    for remote_id in ids:
        matches = [row for row in rows if row["id"] in {
            remote_id, durable_private_fill_id(position["execution_mode"], remote_id),
        }]
        if len(matches) != 1:
            raise ValueError("close Fill has no unique durable identity")
        local = matches[0]
        if any(local[field] != position[field] for field in ("execution_mode", "symbol", "market_type")):
            raise ValueError("close Fill Order scope mismatch")
        if not local.get("exchange_order_id"):
            raise ValueError("close Fill remote Order identity unavailable")
        resolved.append({**local, "remote_fill_id": remote_id})
    return resolved


def _exit_fee_from_report(position: dict, report) -> tuple[float, dict]:
    """Supplement unknown cost from exact later evidence; never mutate a Fill."""
    from aletheia.execution.exchange_materialization import _fill_totals
    from aletheia.portfolio.pnl import fee_in_quote
    from aletheia.uta.compat import utc
    from aletheia.uta.models import FillListReport, ReadCompleteness, UTAAccountIdentity

    report = FillListReport.model_validate(report.model_dump())
    entry_order = repo.get_order(str(position.get("order_id") or ""))
    identity = UTAAccountIdentity.model_validate_json((entry_order or {}).get("uta_account_identity_json") or "")
    instrument = report.scope.instrument
    if (
        report.completeness is not ReadCompleteness.COMPLETE or report.identity != identity
        or identity.execution_mode != position["execution_mode"] or instrument is None
        or instrument.canonical_symbol != position["symbol"]
        or instrument.market_type != position["market_type"]
        or report.scope.sub_account_id is not None
        or report.scope.start_at is None or report.scope.end_at is None
    ):
        raise ValueError("fee completion requires complete exact-account Fill evidence")
    local_rows = _close_fill_rows(position)
    quantity, notional, _ = _fill_totals(local_rows, symbol=position["symbol"])
    if (
        not math.isclose(quantity, float(position["quantity"]), rel_tol=1e-9, abs_tol=1e-12)
        or not math.isclose(notional / quantity, float(position["current_price"]), rel_tol=1e-9, abs_tol=1e-12)
        or max(utc(row["filled_at"]) for row in local_rows) != utc(position["closed_at"])
    ):
        raise ValueError("fee completion full-close economics mismatch")
    order_ids = {row["exchange_order_id"] for row in local_rows}
    fills = [fill for fill in report.fills if fill.venue_order_id in order_ids]
    if len(fills) != len(local_rows) or len({fill.venue_fill_id for fill in fills}) != len(fills):
        raise ValueError("fee completion Fill set mismatch")
    total = 0.0
    facts = []
    for local in local_rows:
        matches = [fill for fill in fills if fill.venue_fill_id == local["remote_fill_id"]]
        if len(matches) != 1:
            raise ValueError("fee completion Fill identity mismatch")
        fill = matches[0]
        price, quantity = float(fill.price), float(fill.quantity)
        fee = None if fill.fee is None else float(fill.fee)
        executed_at = utc(local["filled_at"])
        if (
            fill.venue_order_id != local["exchange_order_id"]
            or fill.instrument.canonical_symbol != position["symbol"]
            or fill.instrument.market_type != position["market_type"]
            or not math.isfinite(price) or price <= 0
            or not math.isfinite(quantity) or quantity <= 0
            or not math.isclose(price, float(local["fill_price"]), rel_tol=1e-9, abs_tol=1e-12)
            or not math.isclose(quantity, float(local["fill_quantity"]), rel_tol=1e-9, abs_tol=1e-12)
            or executed_at is None or fill.executed_at != executed_at
            or not report.scope.start_at <= executed_at <= report.scope.end_at
            or fee is None or not math.isfinite(fee) or fee < 0
            or (local["fee"] is not None and not math.isclose(float(local["fee"]), fee, rel_tol=1e-9, abs_tol=1e-12))
            or (local["fee_currency"] and str(local["fee_currency"]).upper() != str(fill.fee_currency or "").upper())
        ):
            raise ValueError("fee completion conflicts with immutable Fill")
        quote_fee = fee_in_quote(fee=fee, fee_currency=fill.fee_currency, fill_price=price, symbol=position["symbol"])
        if quote_fee is None:
            raise ValueError("fee completion quote conversion unavailable")
        total += quote_fee
        facts.append({
            "durable_fill_id": local["id"], "remote_fill_id": fill.venue_fill_id,
            "remote_order_id": fill.venue_order_id, "fee": fee,
            "fee_currency": fill.fee_currency, "fee_in_quote": quote_fee,
        })
    if not math.isfinite(total):
        raise ValueError("fee completion total invalid")
    return round(total, 8), {
        "source": "uta_private_fills", "identity": identity.model_dump(mode="json"),
        "profile_version": report.profile_version, "received_at": report.received_at.isoformat(),
        "fills": facts,
    }


def materialize_exchange_close_costs(
    row: dict,
    *,
    funding_cost: float | None = None,
    funding_evidence: dict | None = None,
    funding_ledger: dict | None = None,
    fee_report=None,
) -> dict:
    """Commit verified new cost evidence through the shared economic owner."""
    from aletheia.portfolio.funding_audit import (
        funding_audit_id,
        insert_pending_funding_audit,
        reconcile_position_funding_audits,
    )

    position_id = str(row.get("id") or "")
    has_funding = any(value is not None for value in (funding_cost, funding_evidence, funding_ledger))
    if (
        row.get("status") != "closed"
        or row.get("execution_mode") not in _EXCHANGE_FUNDING_MODES
        or row.get("market_type") != "usdt_futures"
        or not row.get("exit_fill_verified")
        or (not has_funding and fee_report is None)
        or (has_funding and (
            not isinstance(funding_evidence, dict) or funding_ledger is None
            or funding_cost is None or funding_evidence.get("status") != "complete"
            or funding_evidence.get("source") != "broker_ledger"
        ))
    ):
        raise ValueError("broker funding settlement requires a verified exchange close")
    fingerprint = _fingerprint(funding_evidence) if has_funding else None
    try:
        audit_mirror_status = reconcile_position_funding_audits(position_id)
    except Exception as exc:
        logger.error(
            "broker funding audit reconcile failed (%s)", type(exc).__name__,
        )
        audit_mirror_status = "pending"
    audit_event_id: str | None = None

    with repo.transaction(immediate=True):
        current = repo._select_one("positions", position_id)
        if current is None:
            raise ValueError(f"position not found: {position_id}")
        if current.get("realized_pnl") is None:
            changed_fields = sorted(
                key
                for key in (set(row) | set(current)) - {"funding_queue_rowid"}
                if row.get(key) != current.get(key)
            )
            if changed_fields:
                raise ValueError(
                    "broker funding settlement snapshot changed"
                    f" ({', '.join(changed_fields)})"
                )
        if (
            current.get("status") != "closed"
            or current.get("execution_mode") not in _EXCHANGE_FUNDING_MODES
            or current.get("market_type") != "usdt_futures"
            or not current.get("exit_fill_verified")
        ):
            raise ValueError("position is no longer a verified exchange close")
        close_events = [
            dict(event)
            for event in get_db().execute(
                """SELECT * FROM position_close_events
                   WHERE position_id=? AND is_partial=0 ORDER BY rowid""",
                (position_id,),
            ).fetchall()
        ]
        if (
            len(close_events) != 1
            or not _close_event_matches_position(close_events[0], current)
        ):
            raise ValueError("broker funding settlement snapshot changed")

        raw_evidence = current.get("reconciliation_evidence")
        if raw_evidence:
            try:
                evidence_root = json.loads(str(raw_evidence))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("position reconciliation evidence is malformed") from exc
            if not isinstance(evidence_root, dict):
                raise ValueError("position reconciliation evidence is malformed")
        else:
            evidence_root = {}

        cost_fields = {"funding_cost": funding_cost, "funding_source": "broker_ledger"} if has_funding else {}
        if fee_report is not None:
            exit_fee, fee_evidence = _exit_fee_from_report(current, fee_report)
            if current.get("exit_fee") is not None and current["exit_fee"] != exit_fee:
                raise ValueError("existing exit fee cannot be overwritten")
            cost_fields["exit_fee"] = exit_fee
            if current.get("exit_fee") is None:
                if "fee_completion" in evidence_root:
                    raise ValueError("existing fee completion evidence cannot be overwritten")
                evidence_root["fee_completion"] = fee_evidence
            if not has_funding:
                fingerprint = _fingerprint(fee_evidence)
                if current.get("exit_fee") is not None:
                    return {
                        "position_id": position_id, "status": "already_current",
                        "realized_pnl": current.get("realized_pnl"),
                        "audit_mirror_status": audit_mirror_status,
                    }
        realized = calculated_realized_pnl({**current, **cost_fields})
        if realized is None and has_funding:
            raise ValueError("incomplete or invalid realized economics")
        realized = round(realized, 8) if realized is not None else None
        if current.get("realized_pnl") is not None:
            same = (
                current.get("funding_source") == "broker_ledger"
                and current.get("funding_cost") is not None
                and math.isclose(
                    float(current["funding_cost"]), float(funding_cost),
                    rel_tol=0.0, abs_tol=1e-12,
                )
                and math.isclose(
                    float(current["realized_pnl"]), realized,
                    rel_tol=0.0, abs_tol=1e-8,
                )
                and close_events[0].get("realized_pnl") is not None
                and math.isclose(
                    float(close_events[0]["realized_pnl"]), realized,
                    rel_tol=0.0, abs_tol=1e-8,
                )
                and evidence_root.get("funding_economics_fingerprint") == fingerprint
                and evidence_root.get("funding_accounting_version") == "broker-funding-v1"
                and evidence_root.get("funding") == funding_evidence
            )
            if same:
                return {
                    "position_id": position_id,
                    "status": "already_current",
                    "realized_pnl": realized,
                    "audit_mirror_status": audit_mirror_status,
                }
            raise ValueError("existing exchange economics cannot be overwritten")

        if close_events[0].get("realized_pnl") is not None:
            raise ValueError("broker funding settlement snapshot changed")
        if has_funding:
            current_funding = compute_stored_broker_funding(
                get_db(), position=current, closed_at=current.get("closed_at"),
                ledger=funding_ledger,
            )
            if (
                not current_funding.complete
                or current_funding.funding_cost != funding_cost
                or current_funding.evidence != funding_evidence
            ):
                raise ValueError("broker funding attribution changed before settlement commit")
            evidence_root.update({
                "funding": funding_evidence,
                "funding_economics_fingerprint": fingerprint,
                "funding_accounting_version": "broker-funding-v1",
            })

        fields = _commit_close_economics(current, {
            **cost_fields,
            "realized_pnl": realized,
            "reconciliation_evidence": json.dumps(
                evidence_root,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        }, close_event=close_events[0])
        outcome_eligible = fields["outcome_eligible"]
        outcome_reason = fields["outcome_exclusion_reason"]
        before = {
            "exit_fee": current.get("exit_fee"),
            "realized_pnl": current.get("realized_pnl"),
            "funding_cost": current.get("funding_cost"),
            "funding_source": current.get("funding_source"),
            "outcome_eligible": bool(current.get("outcome_eligible")),
        }
        after = {
            "exit_fee": fields.get("exit_fee", current.get("exit_fee")),
            "fee_completion": evidence_root.get("fee_completion"),
            "realized_pnl": realized,
            "funding_cost": fields.get("funding_cost", current.get("funding_cost")),
            "funding_source": fields.get("funding_source", current.get("funding_source")),
            "outcome_eligible": bool(outcome_eligible),
            "outcome_exclusion_reason": outcome_reason,
            "funding_economics_fingerprint": evidence_root.get("funding_economics_fingerprint"),
        }
        occurred_at = datetime.now(timezone.utc).isoformat()
        event_type = "exchange_position_funding_settled" if has_funding else "exchange_position_fee_completed"
        audit_event_id = funding_audit_id(
            position_id=position_id,
            event_type=event_type,
            accounting_version="broker-funding-v1",
            fingerprint=fingerprint,
            transition_fingerprint=_fingerprint({
                "before": before,
                "after": after,
            }),
            occurred_at=occurred_at,
        )
        insert_pending_funding_audit(AuditEvent(
            id=audit_event_id,
            timestamp=occurred_at,
            actor="system",
            source="broker_funding_settler",
            event_type=event_type,
            entity_type="position",
            entity_id=position_id,
            before_state=json.dumps(before, sort_keys=True),
            after_state=json.dumps(after, sort_keys=True),
            message=(
                f"Materialized broker cost evidence for {position_id}; "
                f"realized_pnl={realized}"
            ),
        ))

    if audit_event_id is not None:
        try:
            audit_mirror_status = reconcile_position_funding_audits(position_id)
        except Exception as exc:
            logger.error(
                "broker funding audit reconcile failed (%s)", type(exc).__name__,
            )
            audit_mirror_status = "pending"
    if outcome_eligible or outcome_reason == "attribution_read_failed":
        _run_outcome_hooks({**current, **fields}, realized, str(current.get("close_reason") or ""))
    return {
        "position_id": position_id,
        "status": "settled" if realized is not None else "cost_pending",
        "realized_pnl": realized,
        "funding_cost": fields.get("funding_cost", current.get("funding_cost")),
        "audit_mirror_status": audit_mirror_status,
    }


def _terminalize_funding_history_expired(
    row: dict,
    expiry: FundingHistoryRetentionExpired,
    *,
    history_start_ms: int,
    history_end_ms: int,
) -> dict:
    """Stop only automatic retry while preserving unavailable economics."""
    from aletheia.portfolio.funding_audit import (
        funding_audit_id,
        insert_pending_funding_audit,
    )

    position_id = str(row.get("id") or "")
    terminal = {
        "status": "terminal",
        "reason": BROKER_FUNDING_HISTORY_EXPIRED,
        "uta_reason": "funding_history_retention_expired",
        "position_id": position_id,
        "execution_mode": str(row.get("execution_mode") or ""),
        "symbol": str(row.get("symbol") or ""),
        "history_start_ms": int(history_start_ms),
        "history_end_ms": int(history_end_ms),
        "retention_days": expiry.retention_days,
        "profile_version": expiry.profile_version,
        "source": expiry.source,
        "accounting_version": "broker-funding-retention-v1",
    }
    terminal_fingerprint = _fingerprint(terminal)
    before = {
        "realized_pnl": None,
        "funding_cost": None,
        "funding_source": "unavailable",
        "outcome_eligible": False,
        "outcome_exclusion_reason": "incomplete_funding_economics",
    }
    after = {
        **before,
        "outcome_exclusion_reason": BROKER_FUNDING_HISTORY_EXPIRED,
        "funding_terminal": terminal,
    }
    occurred_at = datetime.fromtimestamp(
        (
            history_start_ms
            + expiry.retention_days * 24 * 60 * 60 * 1000
        )
        / 1000,
        tz=timezone.utc,
    ).isoformat()
    audit_event_id = funding_audit_id(
        position_id=position_id,
        event_type="exchange_position_funding_history_expired",
        accounting_version="broker-funding-retention-v1",
        fingerprint=terminal_fingerprint,
        transition_fingerprint=_fingerprint({"before": before, "after": after}),
        occurred_at=occurred_at,
    )

    with repo.transaction(immediate=True):
        current = repo._select_one("positions", position_id)
        if current is None:
            return {"status": "stale_noop", "audit_event_id": None}
        changed_fields = sorted(
            key
            for key in (set(row) | set(current)) - {"funding_queue_rowid"}
            if row.get(key) != current.get(key)
        )
        if (
            changed_fields
            or current.get("status") != "closed"
            or current.get("execution_mode") not in _EXCHANGE_FUNDING_MODES
            or current.get("market_type") != "usdt_futures"
            or not current.get("exit_fill_verified")
            or current.get("realized_pnl") is not None
            or current.get("funding_cost") is not None
            or current.get("funding_source") != "unavailable"
            or bool(current.get("outcome_eligible"))
            or current.get("outcome_exclusion_reason")
            != "incomplete_funding_economics"
        ):
            return {"status": "stale_noop", "audit_event_id": None}

        close_events = [
            dict(event)
            for event in get_db().execute(
                """SELECT * FROM position_close_events
                   WHERE position_id=? AND is_partial=0 ORDER BY rowid""",
                (position_id,),
            ).fetchall()
        ]
        if (
            len(close_events) != 1
            or close_events[0].get("realized_pnl") is not None
            or not _close_event_matches_position(close_events[0], current)
        ):
            raise ValueError("exactly one matching NULL-economics close is required")

        raw_evidence = current.get("reconciliation_evidence")
        if raw_evidence:
            try:
                evidence_root = json.loads(str(raw_evidence))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("position reconciliation evidence is malformed") from exc
            if not isinstance(evidence_root, dict):
                raise ValueError("position reconciliation evidence is malformed")
        else:
            evidence_root = {}
        previous_terminal = evidence_root.get("funding_terminal")
        if previous_terminal is not None and previous_terminal != terminal:
            raise ValueError("funding terminal evidence conflict")
        evidence_root["funding_terminal"] = terminal

        repo.update_closed_position_settlement(
            position_id,
            funding_source="unavailable",
            outcome_eligible=False,
            outcome_exclusion_reason=BROKER_FUNDING_HISTORY_EXPIRED,
            reconciliation_evidence=json.dumps(
                evidence_root,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        insert_pending_funding_audit(AuditEvent(
            id=audit_event_id,
            timestamp=occurred_at,
            actor="system",
            source="broker_funding_settler",
            event_type="exchange_position_funding_history_expired",
            entity_type="position",
            entity_id=position_id,
            before_state=json.dumps(before, sort_keys=True),
            after_state=json.dumps(after, sort_keys=True),
            message=(
                f"Excluded expired broker funding history for Position {position_id}; "
                "net economics remain unavailable"
            ),
        ))
    return {"status": "terminalized", "audit_event_id": audit_event_id}


async def _uta_exit_fee_report(position: dict):
    from aletheia.uta.compat import scope_from_local, utc
    from aletheia.uta.manager import query_uta
    from aletheia.uta.models import FillQuery, UTAAccountIdentity

    rows = _close_fill_rows(position)
    order = repo.get_order(str(position.get("order_id") or ""))
    identity = UTAAccountIdentity.model_validate_json((order or {}).get("uta_account_identity_json") or "")
    times = [utc(row["filled_at"]) for row in rows]
    if any(value is None for value in times):
        raise ValueError("close Fill time unavailable")
    return await query_uta(
        position["execution_mode"], "get_fills",
        FillQuery(scope=scope_from_local(
            position["symbol"], position["market_type"],
            start_at=min(times), end_at=max(times),
        )), expected_account=identity,
    )


async def _uta_funding_ledger(position: dict, start_ms: int, end_ms: int) -> dict:
    from aletheia.execution.broker_funding import make_complete_ledger
    from aletheia.uta.compat import scope_from_local
    from aletheia.uta.manager import query_uta
    from aletheia.uta.models import FundingLedgerQuery, ReadCompleteness, UTAAccountIdentity

    execution_mode = str(position["execution_mode"])
    order = repo.get_order(str(position.get("order_id") or ""))
    if order is None or not order.get("uta_account_identity_json"):
        raise ValueError("uta_account_identity_unavailable")
    identity = UTAAccountIdentity.model_validate_json(order["uta_account_identity_json"])
    report = await query_uta(
        execution_mode,
        "get_funding_ledger",
        FundingLedgerQuery(scope=scope_from_local(
            str(position["symbol"]),
            str(position["market_type"]),
            start_at=datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
            end_at=datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc),
        )),
        expected_account=identity,
    )
    if report.completeness is not ReadCompleteness.COMPLETE:
        from aletheia.uta.funding import FUNDING_HISTORY_RETENTION_EXPIRED
        if report.reason_code == FUNDING_HISTORY_RETENTION_EXPIRED:
            from aletheia.uta.manager import funding_history_retention_contract
            retention_days, _ = funding_history_retention_contract(execution_mode)
            raise FundingHistoryRetentionExpired(
                retention_days=retention_days,
                profile_version=report.profile_version,
                received_at=report.received_at,
                source="uta_report",
            )
        raise ValueError(report.reason_code or "UTA funding ledger incomplete")
    events = [
        {
            "event_id": entry.venue_ledger_id,
            "symbol": entry.instrument.canonical_symbol,
            "cash_change": float(entry.amount),
            "funding_cost": -float(entry.amount),
            "currency": entry.currency,
            "scheduled_at": entry.settlement_at.isoformat(),
            "occurred_at_ms": int(entry.settlement_at.timestamp() * 1000),
            "raw_type": entry.source_provenance,
            "direction": None,
        }
        for entry in report.entries
    ]
    return make_complete_ledger(
        broker_id=execution_mode,
        account_environment=report.identity.environment,
        source_endpoint="uta_v1",
        history_start_ms=start_ms,
        history_end_ms=end_ms,
        events=events,
    )


async def settle_unsettled_exchange_closes(
    limit: int = 50,
    *,
    scan_limit: int = 100,
) -> dict:
    """Recover exchange closes with bounded fair scheduling and broker I/O."""
    from aletheia.config.loader import load_config
    from aletheia.uta.funding import is_funding_history_retention_expired
    from aletheia.uta.manager import funding_history_retention_contract

    broker_budget = min(max(int(limit), 0), 50)
    classification_budget = min(max(int(scan_limit), 0), 100)
    rows, cursor = (
        _select_funding_queue_rows(classification_budget)
        if broker_budget and classification_budget
        else ([], None)
    )
    cfg = load_config()
    checked = 0
    broker_io_attempted = 0
    settled = 0
    history_expired = 0
    stale_noop = 0
    still_pending = 0
    audit_pending = 0
    cursor_advanced = False
    cursor_conflict = False
    errors: list[str] = []
    for row in rows:
        if cursor_conflict or broker_io_attempted >= broker_budget:
            break
        checked += 1
        position_id = str(row.get("id") or "")
        try:
            if row.get("exit_fee") is None:
                broker_io_attempted += 1
                report = await _uta_exit_fee_report(row)
                materialized = materialize_exchange_close_costs(row, fee_report=report)
                settled += int(materialized["status"] == "settled")
                still_pending += int(materialized["status"] == "cost_pending")
                stale_noop += int(materialized["status"] == "already_current")
                audit_pending += int(materialized.get("audit_mirror_status") != "mirrored")
                continue
            start_ms, end_ms = funding_query_window(row, row.get("closed_at"))
            execution_mode = str(row["execution_mode"])
            retention_days, profile_version = funding_history_retention_contract(
                execution_mode,
                cfg=cfg,
            )
            received_at = datetime.now(timezone.utc)
            expiry: FundingHistoryRetentionExpired | None = None
            ledger: dict | None = None
            if is_funding_history_retention_expired(
                datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
                received_at=received_at,
                retention_days=retention_days,
            ):
                expiry = FundingHistoryRetentionExpired(
                    retention_days=retention_days,
                    profile_version=profile_version,
                    received_at=received_at,
                    source="uta_profile_preflight",
                )
            else:
                broker_io_attempted += 1
                try:
                    ledger = await _uta_funding_ledger(row, start_ms, end_ms)
                except FundingHistoryRetentionExpired as exc:
                    expiry = exc

            if expiry is not None:
                terminalized = _terminalize_funding_history_expired(
                    row,
                    expiry,
                    history_start_ms=start_ms,
                    history_end_ms=end_ms,
                )
                if terminalized["status"] == "terminalized":
                    history_expired += 1
                    audit_pending += 1
                else:
                    stale_noop += 1
            else:
                if ledger is None:
                    raise RuntimeError("UTA funding ledger unavailable")
                funding = compute_stored_broker_funding(
                    get_db(),
                    position=row,
                    closed_at=row["closed_at"],
                    ledger=ledger,
                )
                if not funding.complete or funding.funding_cost is None:
                    still_pending += 1
                else:
                    materialized = materialize_exchange_close_costs(
                        row,
                        funding_cost=funding.funding_cost,
                        funding_evidence=funding.evidence,
                        funding_ledger=ledger,
                    )
                    settled += 1
                    audit_pending += int(
                        materialized.get("audit_mirror_status") != "mirrored"
                    )
        except Exception as exc:
            logger.warning(
                "broker funding settler failed for %s (%s)",
                position_id,
                type(exc).__name__,
            )
            errors.append(f"{position_id}: {type(exc).__name__}")
            still_pending += 1
        finally:
            if _advance_funding_queue_cursor(cursor, row):
                cursor = _cursor_key(row)
                cursor_advanced = True
            else:
                errors.append(f"{position_id}: FundingQueueCursorConflict")
                cursor_conflict = True
    recovery_state = funding_recovery_state_counts()
    return {
        "checked": checked,
        "broker_io_attempted": broker_io_attempted,
        "settled": settled,
        "history_expired": history_expired,
        "stale_noop": stale_noop,
        "cursor_advanced": cursor_advanced,
        "still_pending": still_pending,
        "audit_pending": audit_pending,
        "retry_pending_total": recovery_state["retry_pending"],
        "terminal_total": recovery_state["terminal"],
        "errors": errors,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
