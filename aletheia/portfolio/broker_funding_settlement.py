"""Idempotent recovery for exchange closes missing private funding economics."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import math

from aletheia.analytics.outcome_integrity import outcome_write_eligibility
from aletheia.execution.broker_funding import (
    compute_broker_funding,
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


def materialize_broker_funding_economics(
    row: dict,
    *,
    funding_cost: float,
    funding_evidence: dict,
) -> dict:
    """Atomically repair Position, close ledger, audit, and outcome-hook authority."""
    from aletheia.files.jsonl import write_audit
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
    realized = _realized(row, float(funding_cost))
    candidate = {
        **row,
        "realized_pnl": realized,
        "funding_cost": float(funding_cost),
        "funding_source": "broker_ledger",
        "exit_fill_verified": True,
    }
    outcome_eligible, outcome_reason = outcome_write_eligibility(
        candidate, exit_fill_verified=True,
    )
    fingerprint = _fingerprint(funding_evidence)
    audit_payload: dict | None = None

    with repo.transaction(immediate=True):
        current = repo._select_one("positions", position_id)
        if current is None:
            raise ValueError(f"position not found: {position_id}")
        if current.get("status") != "closed":
            raise ValueError("position is no longer closed")
        if current.get("realized_pnl") is not None:
            same = (
                current.get("funding_cost") is not None
                and math.isclose(
                    float(current["funding_cost"]), float(funding_cost),
                    rel_tol=0.0, abs_tol=1e-12,
                )
                and math.isclose(
                    float(current["realized_pnl"]), realized,
                    rel_tol=0.0, abs_tol=1e-8,
                )
            )
            if same:
                return {
                    "position_id": position_id,
                    "status": "already_current",
                    "realized_pnl": realized,
                }
            raise ValueError("existing exchange economics cannot be overwritten")

        evidence_root: dict = {}
        raw_evidence = current.get("reconciliation_evidence")
        if raw_evidence:
            try:
                parsed = json.loads(str(raw_evidence))
                if isinstance(parsed, dict):
                    evidence_root = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence_root = {}
        evidence_root.update({
            "funding": funding_evidence,
            "funding_economics_fingerprint": fingerprint,
            "funding_accounting_version": "broker-funding-v1",
        })
        close_events = [
            dict(event)
            for event in get_db().execute(
                """SELECT * FROM position_close_events
                   WHERE position_id=? AND is_partial=0 ORDER BY rowid""",
                (position_id,),
            ).fetchall()
        ]
        if len(close_events) != 1:
            raise ValueError("exactly one full close event is required")

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
        get_db().execute(
            "UPDATE position_close_events SET realized_pnl=? WHERE id=?",
            (realized, close_events[0]["id"]),
        )
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
        repo.insert_audit_event(AuditEvent(
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
        audit_payload = {
            "actor": "system",
            "source": "broker_funding_settler",
            "event_type": "exchange_position_funding_settled",
            "entity_type": "position",
            "entity_id": position_id,
            "before": before,
            "after": after,
            "message": f"Materialized broker funding economics for {position_id}",
        }

    if audit_payload is not None:
        try:
            write_audit(audit_payload, mirror_db=False)
        except Exception as exc:
            logger.error(
                "broker funding JSONL audit sink failed (%s)", type(exc).__name__,
            )
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
    }


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


async def settle_unsettled_exchange_closes(limit: int = 50) -> dict:
    """Recover already-closed exchange futures after funding bills become visible."""
    placeholders = ",".join("?" for _ in _EXCHANGE_FUNDING_MODES)
    rows = [
        dict(row)
        for row in get_db().execute(
            f"""SELECT * FROM positions
                WHERE status='closed' AND market_type='usdt_futures'
                  AND execution_mode IN ({placeholders})
                  AND realized_pnl IS NULL
                  AND outcome_exclusion_reason='incomplete_funding_economics'
                ORDER BY closed_at ASC, rowid ASC LIMIT ?""",
            (*_EXCHANGE_FUNDING_MODES, int(limit)),
        ).fetchall()
    ]
    settled = 0
    still_pending = 0
    errors: list[str] = []
    for row in rows:
        position_id = str(row.get("id") or "")
        client = None
        try:
            start_ms, end_ms = funding_query_window(row, row.get("closed_at"))
            client = _client_for_mode(str(row["execution_mode"]))
            ledger = await client.get_funding_ledger(
                broker_id=str(row["execution_mode"]),
                symbol=str(row["symbol"]),
                start_time_ms=start_ms,
                end_time_ms=end_ms,
            )
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
            materialize_broker_funding_economics(
                row,
                funding_cost=funding.funding_cost,
                funding_evidence=funding.evidence,
            )
            settled += 1
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
    return {
        "checked": len(rows),
        "settled": settled,
        "still_pending": still_pending,
        "errors": errors,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
