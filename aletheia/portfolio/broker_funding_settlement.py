"""Idempotent recovery for exchange closes missing private funding economics."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone

from aletheia.analytics.outcome_integrity import outcome_write_eligibility
from aletheia.execution.broker_funding import (
    compute_stored_broker_funding,
    funding_query_window,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.storage.sqlite import get_db

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
          AND outcome_exclusion_reason='incomplete_funding_economics'"""

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
                  'broker_funding_history_expired'
              )
            GROUP BY outcome_exclusion_reason""",
        _EXCHANGE_FUNDING_MODES,
    ).fetchall()
    counts = {
        str(row["outcome_exclusion_reason"]): int(row["count"])
        for row in rows
    }
    return {
        "retry_pending": counts.get("incomplete_funding_economics", 0),
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


def _realized(row: dict, funding_cost: float) -> float:
    entry = float(row["entry_price"])
    exit_price = float(row["current_price"])
    quantity = float(row["quantity"])
    entry_fee = float(row["entry_fee"])
    exit_fee = float(row["exit_fee"])
    direction = str(row.get("direction") or "").lower()
    if direction in {"long", "buy"}:
        gross = (exit_price - entry) * quantity
    elif direction in {"short", "sell"}:
        gross = (entry - exit_price) * quantity
    else:
        raise ValueError(f"invalid direction {direction!r}")
    value = round(gross - entry_fee - exit_fee - funding_cost, 8)
    if not math.isfinite(value):
        raise ValueError("non-finite realized_pnl")
    return value


def _close_event_matches_position(event: dict, position: dict) -> bool:
    if any(
        event.get(event_field) != position.get(position_field)
        for event_field, position_field in (
            ("position_id", "id"),
            ("symbol", "symbol"),
            ("direction", "direction"),
            ("execution_mode", "execution_mode"),
            ("close_reason", "close_reason"),
            ("closed_at", "closed_at"),
        )
    ):
        return False
    for event_field, position_field in (
        ("exit_price", "current_price"),
        ("closed_qty", "quantity"),
        ("entry_fee", "entry_fee"),
        ("exit_fee", "exit_fee"),
    ):
        try:
            if not math.isclose(
                float(event[event_field]),
                float(position[position_field]),
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return int(event.get("is_partial") or 0) == 0


def materialize_broker_funding_economics(
    row: dict,
    *,
    funding_cost: float,
    funding_evidence: dict,
    funding_ledger: dict,
) -> dict:
    """Atomically repair Position, close ledger, audit, and outcome-hook authority."""
    from aletheia.portfolio.funding_audit import (
        funding_audit_id,
        insert_pending_funding_audit,
        reconcile_position_funding_audits,
    )
    from aletheia.portfolio.outcome_hooks import enqueue_outcome_hook_jobs

    position_id = str(row.get("id") or "")
    if (
        row.get("status") != "closed"
        or row.get("execution_mode") not in _EXCHANGE_FUNDING_MODES
        or row.get("market_type") != "usdt_futures"
        or not row.get("exit_fill_verified")
        or funding_evidence.get("status") != "complete"
        or funding_evidence.get("source") != "broker_ledger"
    ):
        raise ValueError("broker funding settlement requires a verified exchange close")
    fingerprint = _fingerprint(funding_evidence)
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

        realized = _realized(current, float(funding_cost))
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
        current_funding = compute_stored_broker_funding(
            get_db(),
            position=current,
            closed_at=current.get("closed_at"),
            ledger=funding_ledger,
        )
        if (
            not current_funding.complete
            or current_funding.funding_cost != funding_cost
            or current_funding.evidence != funding_evidence
        ):
            raise ValueError(
                "broker funding attribution changed before settlement commit"
            )
        candidate = {
            **current,
            "realized_pnl": realized,
            "funding_cost": float(funding_cost),
            "funding_source": "broker_ledger",
            "exit_fill_verified": True,
        }
        outcome_eligible, outcome_reason = outcome_write_eligibility(
            candidate, exit_fill_verified=True,
        )
        evidence_root.update({
            "funding": funding_evidence,
            "funding_economics_fingerprint": fingerprint,
            "funding_accounting_version": "broker-funding-v1",
        })

        repo.update_position(
            position_id,
            realized_pnl=realized,
            funding_cost=float(funding_cost),
            funding_source="broker_ledger",
            outcome_eligible=outcome_eligible,
            outcome_exclusion_reason=outcome_reason,
            reconciliation_evidence=json.dumps(
                evidence_root,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
        updated = get_db().execute(
            """UPDATE position_close_events SET realized_pnl=?
               WHERE id=? AND realized_pnl IS NULL""",
            (realized, close_events[0]["id"]),
        )
        if updated.rowcount != 1:
            raise ValueError("broker funding settlement snapshot changed")
        if outcome_eligible:
            enqueue_outcome_hook_jobs({
                **current,
                "realized_pnl": realized,
                "funding_cost": float(funding_cost),
                "funding_source": "broker_ledger",
                "outcome_eligible": True,
                "outcome_exclusion_reason": outcome_reason,
            })
        before = {
            "realized_pnl": current.get("realized_pnl"),
            "funding_cost": current.get("funding_cost"),
            "funding_source": current.get("funding_source"),
            "outcome_eligible": bool(current.get("outcome_eligible")),
        }
        after = {
            "realized_pnl": realized,
            "funding_cost": float(funding_cost),
            "funding_source": "broker_ledger",
            "outcome_eligible": bool(outcome_eligible),
            "outcome_exclusion_reason": outcome_reason,
            "funding_economics_fingerprint": fingerprint,
        }
        occurred_at = datetime.now(timezone.utc).isoformat()
        audit_event_id = funding_audit_id(
            position_id=position_id,
            event_type="exchange_position_funding_settled",
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
            event_type="exchange_position_funding_settled",
            entity_type="position",
            entity_id=position_id,
            before_state=json.dumps(before, sort_keys=True),
            after_state=json.dumps(after, sort_keys=True),
            message=(
                f"Materialized broker funding economics for {position_id}; "
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
    if outcome_eligible:
        try:
            from aletheia.portfolio.outcome_hooks import process_outcome_hook_jobs

            process_outcome_hook_jobs(position_id=position_id)
        except Exception as exc:
            logger.warning(
                "broker funding outcome hook dispatch failed for %s (%s)",
                position_id, type(exc).__name__,
            )
    return {
        "position_id": position_id,
        "status": "settled",
        "realized_pnl": realized,
        "funding_cost": float(funding_cost),
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

        repo.update_position(
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


class _UTAFundingProjection:
    """Phase-3 compatibility seam over normalized UTA funding reports."""

    def __init__(self, execution_mode: str) -> None:
        self.execution_mode = execution_mode

    async def get_funding_ledger(
        self,
        *,
        broker_id: str,
        symbol: str,
        start_time_ms: int,
        end_time_ms: int,
        market_type: str = "usdt_futures",
    ) -> dict:
        return await _uta_funding_ledger(
            {
                "execution_mode": self.execution_mode,
                "symbol": symbol,
                "market_type": market_type,
            },
            start_time_ms,
            end_time_ms,
        )

    async def close(self) -> None:
        return None


def _client_for_mode(execution_mode: str):
    """Temporary test/rollback seam; production implementation is UTA-only."""
    if execution_mode not in _EXCHANGE_FUNDING_MODES:
        raise ValueError(f"unsupported exchange funding mode: {execution_mode}")
    return _UTAFundingProjection(execution_mode)


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
        client = None
        try:
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
                client = _client_for_mode(execution_mode)
                try:
                    ledger = await client.get_funding_ledger(
                        broker_id=execution_mode,
                        symbol=str(row["symbol"]),
                        start_time_ms=start_ms,
                        end_time_ms=end_ms,
                    )
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
                    materialized = materialize_broker_funding_economics(
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
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass
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
