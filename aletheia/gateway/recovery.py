"""Gateway Recovery Service — orchestrates startup reconciliation.

On every gateway start: STARTING → RECOVERY_MODE → (reconcile) → RUNNING / SAFE_FREEZE.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aletheia.gateway.exchange_adapter import ExchangeAdapter
from aletheia.gateway.locks import GatewayLockService
from aletheia.gateway.reconciliation import (
    build_reconciliation_report,
    reconcile_fills,
    reconcile_orders,
    reconcile_positions,
)
from aletheia.gateway.session import GatewaySessionStore
from aletheia.gateway.state_machine import GatewayStateMachine, GatewayStatus
from aletheia.gateway.state_store import GatewayStateStore
from aletheia.storage.artifact_store import get_artifact_store
from aletheia.storage.event_store import get_event_store, make_event
from aletheia.storage.models import PSReconciliationReport

logger = logging.getLogger("aletheia.gateway.recovery")


@dataclass
class RecoveryResult:
    status: str  # "ok" | "frozen" | "failed"
    report: PSReconciliationReport | None = None
    freeze_reason: str | None = None
    error: str | None = None


class GatewayRecoveryService:
    """Orchestrates the gateway recovery protocol on startup."""

    def __init__(
        self,
        state_store: GatewayStateStore,
        session_store: GatewaySessionStore,
        lock_service: GatewayLockService,
        state_machine: GatewayStateMachine,
        exchange_adapter: ExchangeAdapter,
        gateway_id: str = "gateway-local",
        mode: str = "paper",
    ):
        self._state_store = state_store
        self._session_store = session_store
        self._lock_service = lock_service
        self._sm = state_machine
        self._exchange = exchange_adapter
        self._gateway_id = gateway_id
        self._mode = mode

    async def recover(self) -> RecoveryResult:
        """Run the full recovery protocol. Returns result with status."""
        event_store = get_event_store()

        # Create session
        session = self._session_store.start(self._gateway_id, self._mode)
        session_id = session.gateway_session_id

        # STARTING → RECOVERY_MODE
        self._sm.transition_to(GatewayStatus.STARTING)
        self._sm.transition_to(GatewayStatus.RECOVERY_MODE)
        self._session_store.mark_recovery(session_id)

        event_store.append(make_event(
            "GATEWAY_RECOVERY_STARTED", "gateway_session", session_id,
            payload={"gateway_id": self._gateway_id, "mode": self._mode},
        ))

        # Acquire lock for live mode
        if self._mode == "live":
            lock_ok = self._lock_service.acquire("live_gateway", self._gateway_id)
            if not lock_ok:
                return self._enter_freeze(session_id, "live_gateway_lock_unavailable")

        try:
            # Fetch local state
            local_open_orders = self._state_store.get_open_orders(self._mode)
            local_open_positions = self._state_store.get_open_positions(self._mode)

            # Fetch remote state
            remote_open_orders = await self._exchange.fetch_open_orders()
            remote_positions = await self._exchange.fetch_positions()
            last_fill_ts = self._state_store.last_seen_fill_ts(self._mode)
            remote_fills = await self._exchange.fetch_recent_fills(since=last_fill_ts)

            # Reconcile
            fill_diffs, _ = reconcile_fills(remote_fills, self._state_store, self._mode)
            order_diffs = reconcile_orders(local_open_orders, remote_open_orders, self._state_store)
            position_diffs = reconcile_positions(local_open_positions, remote_positions, self._state_store, self._mode)

            all_diffs = fill_diffs + order_diffs + position_diffs
            report = build_reconciliation_report(session_id, all_diffs)

            # Save report as artifact
            artifact_store = get_artifact_store()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            artifact_ref = artifact_store.put_json(
                f"reconciliation_reports/{today}/{report.reconciliation_report_id}.json",
                report.model_dump(),
            )
            report.artifact_path = artifact_ref.path

            # Save report to DB
            self._save_report(report)
            self._session_store.set_recovery_report(session_id, report.reconciliation_report_id)

            # Decide outcome
            if report.status == "frozen":
                return self._enter_freeze(session_id, "reconciliation_diff", report)

            # All clear — enter RUNNING
            self._sm.transition_to(GatewayStatus.RUNNING)
            self._session_store.mark_running(session_id)
            event_store.append(make_event(
                "GATEWAY_RECOVERY_COMPLETED", "gateway_session", session_id,
                payload={"status": "ok", "diffs_count": len(all_diffs)},
            ))

            logger.info("Gateway recovery completed: %d diffs resolved, entering RUNNING", len(all_diffs))
            return RecoveryResult(status="ok", report=report)

        except Exception as e:
            logger.error("Gateway recovery failed: %s", e)
            return self._enter_freeze(session_id, f"recovery_error: {e}")

    def _enter_freeze(
        self,
        session_id: str,
        reason: str,
        report: PSReconciliationReport | None = None,
    ) -> RecoveryResult:
        """Enter SAFE_FREEZE state."""
        self._sm.enter_freeze(reason)
        self._session_store.mark_frozen(session_id, reason)

        get_event_store().append(make_event(
            "GATEWAY_SAFE_FREEZE_ENTERED", "gateway_session", session_id,
            payload={"reason": reason},
        ))

        logger.warning("Gateway entered SAFE_FREEZE: %s", reason)
        return RecoveryResult(status="frozen", report=report, freeze_reason=reason)

    def _save_report(self, report: PSReconciliationReport) -> None:
        """Persist reconciliation report to DB."""
        from aletheia.storage.sqlite import get_db
        db = get_db()
        was_in_tx = db.in_transaction
        data = report.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        db.execute(f"INSERT INTO ps_reconciliation_reports ({cols}) VALUES ({placeholders})", list(data.values()))
        if not was_in_tx:
            db.commit()
