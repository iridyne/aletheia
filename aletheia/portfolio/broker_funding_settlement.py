"""Idempotent recovery for exchange closes missing private funding economics."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone

from aletheia.analytics.outcome_integrity import outcome_write_eligibility
from aletheia.execution.broker_funding import (
    BrokerFundingHistoryExpired,
    broker_funding_account_environment,
    compute_broker_funding,
    funding_query_window,
    require_broker_funding_history,
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
_FUNDING_QUEUE_ID = "exchange_funding"


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

    def _page(clause: str, params: tuple[object, ...], page_limit: int) -> list[dict]:
        return [
            dict(row)
            for row in db.execute(
                base + clause + " ORDER BY COALESCE(closed_at,''), rowid LIMIT ?",
                (*_EXCHANGE_FUNDING_MODES, *params, page_limit),
            ).fetchall()
        ]

    if cursor is None:
        return _page("", (), limit), None
    closed_at, position_rowid = cursor
    rows = _page(
        " AND (COALESCE(closed_at,'') > ? OR "
        "(COALESCE(closed_at,'') = ? AND rowid > ?))",
        (closed_at, closed_at, position_rowid),
        limit,
    )
    if len(rows) < limit:
        rows.extend(_page(
            " AND (COALESCE(closed_at,'') < ? OR "
            "(COALESCE(closed_at,'') = ? AND rowid <= ?))",
            (closed_at, closed_at, position_rowid),
            limit - len(rows),
        ))
    return rows, cursor


def _advance_funding_queue_cursor(
    previous: tuple[str, int] | None,
    row: dict,
) -> bool:
    """CAS the scheduling cursor without changing Position owner state."""
    closed_at = str(row.get("closed_at") or "")
    position_rowid = int(row["funding_queue_rowid"])
    now = datetime.now(timezone.utc).isoformat()
    with repo.transaction(immediate=True):
        if previous is None:
            cursor = get_db().execute(
                "INSERT OR IGNORE INTO broker_funding_settlement_cursor "
                "(queue_id,last_closed_at,last_position_rowid,updated_at) "
                "VALUES (?,?,?,?)",
                (_FUNDING_QUEUE_ID, closed_at, position_rowid, now),
            )
        else:
            cursor = get_db().execute(
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
    return cursor.rowcount == 1


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
                for key in set(row) | set(current)
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
        audit_event_id = funding_audit_id(
            position_id=position_id,
            event_type="exchange_position_funding_settled",
            accounting_version="broker-funding-v1",
            fingerprint=fingerprint,
        )
        insert_pending_funding_audit(AuditEvent(
            id=audit_event_id,
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
    expiry: BrokerFundingHistoryExpired,
    *,
    history_start_ms: int,
    history_end_ms: int,
) -> dict:
    """Durably exclude one irrecoverable window without inventing economics."""
    from aletheia.portfolio.funding_audit import (
        funding_audit_id,
        insert_pending_funding_audit,
    )

    position_id = str(row.get("id") or "")
    terminal = {
        "status": "terminal",
        "reason": BrokerFundingHistoryExpired.code,
        "position_id": position_id,
        "execution_mode": str(row.get("execution_mode") or ""),
        "symbol": str(row.get("symbol") or ""),
        "history_start_ms": int(history_start_ms),
        "history_end_ms": int(history_end_ms),
        "retention_days": expiry.retention_days,
        "source": expiry.source,
        "accounting_version": "broker-funding-retention-v1",
    }
    fingerprint = _fingerprint(terminal)
    audit_event_id = funding_audit_id(
        position_id=position_id,
        event_type="exchange_position_funding_history_expired",
        accounting_version="broker-funding-retention-v1",
        fingerprint=fingerprint,
    )

    with repo.transaction(immediate=True):
        current = repo._select_one("positions", position_id)
        identity_fields = ("execution_mode", "symbol", "opened_at", "closed_at")
        if (
            current is None
            or any(current.get(field) != row.get(field) for field in identity_fields)
            or current.get("status") != "closed"
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

        raw_evidence = current.get("reconciliation_evidence")
        evidence_root: dict = {}
        if raw_evidence is not None:
            try:
                parsed = json.loads(str(raw_evidence))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("reconciliation evidence is malformed") from exc
            if not isinstance(parsed, dict):
                raise ValueError("reconciliation evidence must be an object")
            evidence_root = parsed
        if "funding" in evidence_root and not isinstance(
            evidence_root["funding"], dict
        ):
            raise ValueError("funding evidence must be an object")
        if "funding_terminal" in evidence_root and evidence_root[
            "funding_terminal"
        ] != terminal:
            raise ValueError("funding terminal evidence conflict")

        raw_identity = current.get("reconciliation_identity")
        try:
            identity = json.loads(str(raw_identity))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("reconciliation identity is malformed") from exc
        if not isinstance(identity, dict):
            raise ValueError("reconciliation identity must be an object")
        execution_mode = str(current.get("execution_mode") or "")
        account_environment = broker_funding_account_environment(execution_mode)
        from aletheia.portfolio.disappearance_confirmation import (
            logical_account_fingerprint,
        )

        expected_identity = {
            "broker_id": execution_mode,
            "account_environment": account_environment,
            "account_fingerprint": logical_account_fingerprint(
                broker_id=execution_mode,
                account_environment=account_environment,
                execution_mode=execution_mode,
            ),
            "execution_mode": execution_mode,
            "position_id": position_id,
            "symbol": current.get("symbol"),
            "market_type": current.get("market_type"),
            "direction": current.get("direction"),
        }
        if any(identity.get(key) != value for key, value in expected_identity.items()):
            raise ValueError("reconciliation identity conflict")

        close_events = get_db().execute(
            """SELECT * FROM position_close_events
               WHERE position_id=? AND is_partial=0 ORDER BY rowid""",
            (position_id,),
        ).fetchall()
        close_matches = False
        if len(close_events) == 1:
            close_event = close_events[0]
            try:
                close_matches = (
                    close_event["realized_pnl"] is None
                    and close_event["symbol"] == current.get("symbol")
                    and close_event["direction"] == current.get("direction")
                    and close_event["execution_mode"] == execution_mode
                    and close_event["close_reason"] == current.get("close_reason")
                    and close_event["closed_at"] == current.get("closed_at")
                    and math.isclose(
                        float(close_event["closed_qty"]),
                        float(current["quantity"]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    and math.isclose(
                        float(close_event["exit_price"]),
                        float(current["current_price"]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    and math.isclose(
                        float(close_event["entry_fee"]),
                        float(current["entry_fee"]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    and math.isclose(
                        float(close_event["exit_fee"]),
                        float(current["exit_fee"]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                )
            except (TypeError, ValueError):
                close_matches = False
        if not close_matches:
            raise ValueError("exactly one matching NULL-economics close is required")

        evidence_root["funding_terminal"] = terminal
        repo.update_position(
            position_id,
            funding_source="unavailable",
            outcome_eligible=False,
            outcome_exclusion_reason=BrokerFundingHistoryExpired.code,
            reconciliation_evidence=json.dumps(
                evidence_root,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        insert_pending_funding_audit(AuditEvent(
            id=audit_event_id,
            actor="system",
            source="broker_funding_settler",
            event_type="exchange_position_funding_history_expired",
            entity_type="position",
            entity_id=position_id,
            before_state=json.dumps({
                "realized_pnl": None,
                "funding_cost": None,
                "funding_source": "unavailable",
                "outcome_eligible": False,
                "outcome_exclusion_reason": "incomplete_funding_economics",
            }, sort_keys=True),
            after_state=json.dumps({
                "realized_pnl": None,
                "funding_cost": None,
                "funding_source": "unavailable",
                "outcome_eligible": False,
                "outcome_exclusion_reason": BrokerFundingHistoryExpired.code,
                "funding_terminal": terminal,
            }, sort_keys=True),
            message=(
                f"Excluded expired broker funding history for Position {position_id}; "
                "net economics remain unavailable"
            ),
        ))
    return {"status": "terminalized", "audit_event_id": audit_event_id}


def _client_for_mode(execution_mode: str):
    if execution_mode in {"bitget_demo", "bitget_live_limited"}:
        from aletheia.exchanges.bitget.client import BitgetClient

        return BitgetClient(
            mode="demo" if execution_mode == "bitget_demo" else "live"
        )
    if execution_mode in {"okx_demo", "okx_live_limited"}:
        from aletheia.exchanges.okx.client import OKXClient

        return OKXClient(
            mode="demo" if execution_mode == "okx_demo" else "live"
        )
    if execution_mode in {"binance_testnet", "binance_live_limited"}:
        from aletheia.exchanges.binance.client import BinanceClient

        return BinanceClient(
            mode="testnet" if execution_mode == "binance_testnet" else "live"
        )
    raise ValueError(f"unsupported broker funding mode: {execution_mode}")


async def settle_unsettled_exchange_closes(
    limit: int = 50,
    *,
    scan_limit: int = 100,
) -> dict:
    """Recover already-closed exchange futures after funding bills become visible."""
    broker_budget = min(max(int(limit), 0), 50)
    classification_budget = min(max(int(scan_limit), 0), 100)
    rows, starting_cursor = (
        _select_funding_queue_rows(classification_budget)
        if broker_budget and classification_budget
        else ([], None)
    )
    checked = 0
    broker_io_attempted = 0
    settled = 0
    history_expired = 0
    stale_noop = 0
    still_pending = 0
    audit_pending = 0
    terminal_audit_ids: list[str] = []
    last_checked: dict | None = None
    errors: list[str] = []
    for row in rows:
        if broker_io_attempted >= broker_budget:
            break
        checked += 1
        last_checked = row
        position_id = str(row.get("id") or "")
        client = None
        try:
            start_ms, end_ms = funding_query_window(row, row.get("closed_at"))
            history_expiry: BrokerFundingHistoryExpired | None = None
            try:
                require_broker_funding_history(
                    str(row["execution_mode"]),
                    start_ms,
                )
            except BrokerFundingHistoryExpired as exc:
                history_expiry = exc
            else:
                broker_io_attempted += 1
                client = _client_for_mode(str(row["execution_mode"]))
                ledger = await client.get_funding_ledger(
                    broker_id=str(row["execution_mode"]),
                    symbol=str(row["symbol"]),
                    start_time_ms=start_ms,
                    end_time_ms=end_ms,
                )
            if history_expiry is not None:
                try:
                    terminalized = _terminalize_funding_history_expired(
                        row,
                        history_expiry,
                        history_start_ms=start_ms,
                        history_end_ms=end_ms,
                    )
                    if terminalized["status"] == "terminalized":
                        history_expired += 1
                        terminal_audit_ids.append(terminalized["audit_event_id"])
                    else:
                        stale_noop += 1
                except Exception as exc:
                    logger.warning(
                        "broker funding terminalization failed for %s (%s)",
                        position_id, type(exc).__name__,
                    )
                    errors.append(f"{position_id}: {type(exc).__name__}")
                    still_pending += 1
                continue
            candidates = [
                dict(item)
                for item in get_db().execute(
                    """SELECT * FROM positions
                       WHERE id<>? AND execution_mode=?
                         AND market_type='usdt_futures' AND symbol=?""",
                    (position_id, row["execution_mode"], row["symbol"]),
                ).fetchall()
            ]
            funding = compute_broker_funding(
                position=row,
                closed_at=row["closed_at"],
                ledger=ledger,
                candidate_positions=candidates,
            )
            if not funding.complete or funding.funding_cost is None:
                still_pending += 1
                continue
            materialized = materialize_broker_funding_economics(
                row,
                funding_cost=funding.funding_cost,
                funding_evidence=funding.evidence,
            )
            settled += 1
            audit_pending += int(
                materialized.get("audit_mirror_status") != "mirrored"
            )
        except Exception as exc:
            logger.warning(
                "broker funding settler failed for %s (%s)",
                position_id, type(exc).__name__,
            )
            errors.append(f"{position_id}: {type(exc).__name__}")
            still_pending += 1
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass
    cursor_advanced = (
        _advance_funding_queue_cursor(starting_cursor, last_checked)
        if last_checked is not None
        else False
    )
    if terminal_audit_ids:
        audit_placeholders = ",".join("?" for _ in terminal_audit_ids)
        audit_pending += int(get_db().execute(
            f"SELECT COUNT(*) FROM audit_events WHERE id IN ({audit_placeholders}) "
            "AND jsonl_mirror_status!='mirrored'",
            tuple(terminal_audit_ids),
        ).fetchone()[0])
    return {
        "checked": checked,
        "broker_io_attempted": broker_io_attempted,
        "settled": settled,
        "history_expired": history_expired,
        "stale_noop": stale_noop,
        "cursor_advanced": cursor_advanced,
        "still_pending": still_pending,
        "audit_pending": audit_pending,
        "errors": errors,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
