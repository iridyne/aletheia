"""Gateway session store — tracks gateway lifecycle across restarts."""

import platform
from datetime import datetime, timezone

from aletheia.storage.models import PSGatewaySession, new_id
from aletheia.storage.sqlite import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _machine_id() -> str:
    return platform.node() or "unknown"


class GatewaySessionStore:
    """Manages gateway session records in the DB."""

    def start(self, gateway_id: str, mode: str) -> PSGatewaySession:
        """Create a new gateway session (status=STARTING)."""
        session = PSGatewaySession(
            gateway_id=gateway_id,
            mode=mode,
            status="STARTING",
            machine_id=_machine_id(),
        )
        db = get_db()
        was_in_tx = db.in_transaction
        data = session.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        db.execute(f"INSERT INTO ps_gateway_sessions ({cols}) VALUES ({placeholders})", list(data.values()))
        if not was_in_tx:
            db.commit()
        return session

    def mark_recovery(self, session_id: str) -> None:
        self._update_status(session_id, "RECOVERY_MODE")

    def mark_running(self, session_id: str) -> None:
        self._update_status(session_id, "RUNNING")

    def mark_frozen(self, session_id: str, reason: str) -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        db.execute(
            "UPDATE ps_gateway_sessions SET status = 'SAFE_FREEZE', freeze_reason = ?, "
            "heartbeat_at = ? WHERE gateway_session_id = ?",
            (reason, now, session_id),
        )
        if not was_in_tx:
            db.commit()

    def mark_stopped(self, session_id: str) -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        db.execute(
            "UPDATE ps_gateway_sessions SET status = 'STOPPED', ended_at = ? WHERE gateway_session_id = ?",
            (now, session_id),
        )
        if not was_in_tx:
            db.commit()

    def heartbeat(self, session_id: str) -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_gateway_sessions SET heartbeat_at = ? WHERE gateway_session_id = ?",
            (_utcnow(), session_id),
        )
        if not was_in_tx:
            db.commit()

    def set_recovery_report(self, session_id: str, report_id: str) -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_gateway_sessions SET recovery_report_id = ? WHERE gateway_session_id = ?",
            (report_id, session_id),
        )
        if not was_in_tx:
            db.commit()

    def get_latest(self, gateway_id: str | None = None) -> dict | None:
        db = get_db()
        if gateway_id:
            row = db.execute(
                "SELECT * FROM ps_gateway_sessions WHERE gateway_id = ? ORDER BY started_at DESC LIMIT 1",
                (gateway_id,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM ps_gateway_sessions ORDER BY started_at DESC LIMIT 1",
            ).fetchone()
        return dict(row) if row else None

    def _update_status(self, session_id: str, status: str) -> None:
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_gateway_sessions SET status = ?, heartbeat_at = ? WHERE gateway_session_id = ?",
            (status, _utcnow(), session_id),
        )
        if not was_in_tx:
            db.commit()
