"""Policy version store — immutable version history for trading policies."""

import json
from datetime import datetime, timezone

from aletheia.storage.event_store import get_event_store, make_event
from aletheia.storage.hashing import sha256_json
from aletheia.storage.models import PSPolicyVersion, new_id
from aletheia.storage.sqlite import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PolicyStore:
    """Manages versioned policy documents. Each edit creates a new version (no in-place mutation)."""

    def create_version(
        self,
        policy_type: str,
        policy: dict,
        created_by: str,
        parent_version_id: str | None = None,
        diff: dict | None = None,
    ) -> PSPolicyVersion:
        """Create a new policy version. Never mutates an existing row."""
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        version_id = new_id()

        policy_json = json.dumps(policy, ensure_ascii=False, sort_keys=True)
        policy_hash = sha256_json({"type": policy_type, "policy": policy_json})

        pv = PSPolicyVersion(
            policy_version_id=version_id,
            parent_policy_version_id=parent_version_id,
            policy_type=policy_type,
            policy_json=policy_json,
            policy_hash=policy_hash,
            diff_json=json.dumps(diff, ensure_ascii=False) if diff else None,
            created_by=created_by,
            created_at=now,
            status="active",
        )

        db.execute(
            "INSERT INTO ps_policy_versions (policy_version_id, parent_policy_version_id, "
            "policy_type, policy_json, policy_hash, diff_json, created_by, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (version_id, parent_version_id, policy_type, policy_json, policy_hash,
             pv.diff_json, created_by, now, "active"),
        )
        if not was_in_tx:
            db.commit()

        get_event_store().append(make_event(
            "POLICY_VERSION_CREATED", "policy", version_id,
            payload={"policy_type": policy_type, "created_by": created_by},
        ))

        return pv

    def get_current(self, policy_type: str) -> PSPolicyVersion | None:
        """Get the latest active version of a policy type."""
        db = get_db()
        row = db.execute(
            "SELECT * FROM ps_policy_versions WHERE policy_type = ? AND status = 'active' "
            "ORDER BY created_at DESC LIMIT 1",
            (policy_type,),
        ).fetchone()
        if row is None:
            return None
        return PSPolicyVersion(**dict(row))

    def get_version(self, version_id: str) -> PSPolicyVersion | None:
        """Get a specific version by ID."""
        db = get_db()
        row = db.execute(
            "SELECT * FROM ps_policy_versions WHERE policy_version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            return None
        return PSPolicyVersion(**dict(row))

    def list_versions(self, policy_type: str) -> list[PSPolicyVersion]:
        """List all versions of a policy type in descending order."""
        db = get_db()
        rows = db.execute(
            "SELECT * FROM ps_policy_versions WHERE policy_type = ? ORDER BY created_at DESC",
            (policy_type,),
        ).fetchall()
        return [PSPolicyVersion(**dict(r)) for r in rows]

    def list_policy_types(self) -> list[str]:
        """List all unique policy types."""
        db = get_db()
        rows = db.execute(
            "SELECT DISTINCT policy_type FROM ps_policy_versions ORDER BY policy_type"
        ).fetchall()
        return [r["policy_type"] for r in rows]

    def deprecate_version(self, policy_version_id: str) -> None:
        """Mark a version as deprecated (not deleted)."""
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_policy_versions SET status = 'deprecated' WHERE policy_version_id = ?",
            (policy_version_id,),
        )
        if not was_in_tx:
            db.commit()
