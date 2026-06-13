"""Gateway leader lock — prevents dual live gateway submission."""

from datetime import datetime, timedelta, timezone

from aletheia.storage.sqlite import get_db


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
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        now_iso = now.isoformat()
        lease_until = (now + timedelta(seconds=self._lease_seconds)).isoformat()

        row = db.execute(
            "SELECT * FROM ps_gateway_locks WHERE lock_name = ?", (lock_name,)
        ).fetchone()

        if row is None:
            db.execute(
                "INSERT INTO ps_gateway_locks (lock_name, owner_id, lease_until, heartbeat_at) VALUES (?, ?, ?, ?)",
                (lock_name, owner_id, lease_until, now_iso),
            )
            if not was_in_tx:
                db.commit()
            return True

        if row["lease_until"] < now_iso:
            # Expired — take over
            db.execute(
                "UPDATE ps_gateway_locks SET owner_id = ?, lease_until = ?, heartbeat_at = ? WHERE lock_name = ?",
                (owner_id, lease_until, now_iso, lock_name),
            )
            if not was_in_tx:
                db.commit()
            return True

        if row["owner_id"] == owner_id:
            # Renew
            db.execute(
                "UPDATE ps_gateway_locks SET lease_until = ?, heartbeat_at = ? WHERE lock_name = ?",
                (lease_until, now_iso, lock_name),
            )
            if not was_in_tx:
                db.commit()
            return True

        # Another owner holds the lock
        if not was_in_tx:
            db.commit()
        return False

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
