"""Gateway leader lock — prevents dual live gateway submission."""

from datetime import datetime, timedelta, timezone

from aletheia.storage.sqlite import execute_write, get_db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


class GatewayLockService:
    """Lease-based leader lock for the live gateway."""

    def __init__(self, lease_seconds: int = 30):
        self._lease_seconds = lease_seconds

    def acquire(self, lock_name: str, owner_id: str) -> bool:
        """Try to acquire or renew a lock. Returns True on success."""
        now = _utcnow()
        now_iso = now.isoformat()
        lease_until = (now + timedelta(seconds=self._lease_seconds)).isoformat()
        result = execute_write(
            "INSERT INTO ps_gateway_locks "
            "(lock_name, owner_id, lease_until, heartbeat_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(lock_name) DO UPDATE SET owner_id=excluded.owner_id, "
            "lease_until=excluded.lease_until, heartbeat_at=excluded.heartbeat_at "
            "WHERE ps_gateway_locks.owner_id=excluded.owner_id "
            "OR ps_gateway_locks.lease_until < excluded.heartbeat_at",
            (lock_name, owner_id, lease_until, now_iso),
            immediate=True,
        )
        return result.rowcount == 1

    def release(self, lock_name: str, owner_id: str) -> bool:
        """Release a lock. Returns True if we were the owner."""
        db = get_db()
        was_in_tx = db.in_transaction
        result = db.execute(
            "DELETE FROM ps_gateway_locks WHERE lock_name = ? AND owner_id = ?",
            (lock_name, owner_id),
        )
        if not was_in_tx:
            db.commit()
        return (result.rowcount or 0) > 0

    def heartbeat(self, lock_name: str, owner_id: str) -> bool:
        """Renew the lease. Returns False if lock was lost."""
        return self.acquire(lock_name, owner_id)

    def is_held_by(self, lock_name: str, owner_id: str) -> bool:
        """Check if we currently hold the lock."""
        db = get_db()
        row = db.execute(
            "SELECT * FROM ps_gateway_locks WHERE lock_name = ?", (lock_name,)
        ).fetchone()
        if row is None:
            return False
        now_iso = _utcnow_iso()
        return row["owner_id"] == owner_id and row["lease_until"] >= now_iso

    def current_owner(self, lock_name: str) -> str | None:
        """Return the current owner if the lock is valid, else None."""
        db = get_db()
        row = db.execute(
            "SELECT * FROM ps_gateway_locks WHERE lock_name = ?", (lock_name,)
        ).fetchone()
        if row is None:
            return None
        if row["lease_until"] < _utcnow_iso():
            return None
        return row["owner_id"]
