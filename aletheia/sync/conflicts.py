"""Policy conflict detection for import/merge operations."""

from __future__ import annotations

from dataclasses import dataclass, field

from aletheia.storage.hashing import sha256_json
from aletheia.storage.sqlite import get_db


@dataclass
class PolicyConflict:
    """A detected conflict between local and incoming policy versions."""

    policy_type: str
    local_hash: str
    incoming_hash: str
    resolution: str = "unresolved"  # "unresolved" | "keep_local" | "take_incoming" | "merge"
    message: str = ""


@dataclass
class ConflictReport:
    """Summary of all conflicts found during policy comparison."""

    conflicts: list[PolicyConflict] = field(default_factory=list)
    safe_imports: list[str] = field(default_factory=list)  # policy types that can merge cleanly

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0

    def to_dict(self) -> dict:
        return {
            "has_conflicts": self.has_conflicts,
            "conflict_count": len(self.conflicts),
            "safe_import_count": len(self.safe_imports),
            "conflicts": [
                {
                    "policy_type": c.policy_type,
                    "local_hash": c.local_hash,
                    "incoming_hash": c.incoming_hash,
                    "resolution": c.resolution,
                    "message": c.message,
                }
                for c in self.conflicts
            ],
            "safe_imports": self.safe_imports,
        }


def detect_policy_conflicts(incoming_policies: list[dict]) -> ConflictReport:
    """Compare incoming policy versions against local ones.

    Each incoming policy dict must have at minimum:
      - policy_type: str
      - policy_hash: str
      - policy_json: str

    Conflict detection rules:
      - If no local version exists for this policy_type → safe import (new policy)
      - If incoming hash matches latest local hash → skip (already have it)
      - If incoming hash differs from latest local hash → CONFLICT (diverged)
    """
    report = ConflictReport()
    db = get_db()

    for incoming in incoming_policies:
        ptype = incoming["policy_type"]
        inc_hash = incoming.get("policy_hash", "")

        # Look up local latest version
        local_row = db.execute(
            "SELECT policy_hash FROM ps_policy_versions "
            "WHERE policy_type = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (ptype,),
        ).fetchone()

        if local_row is None:
            # New policy type — safe to import
            report.safe_imports.append(ptype)
            continue

        local_hash = local_row["policy_hash"]

        if inc_hash == local_hash:
            pass  # Identical — nothing to do
        else:
            # Different content — conflict
            report.conflicts.append(PolicyConflict(
                policy_type=ptype,
                local_hash=local_hash,
                incoming_hash=inc_hash,
                message=f"Policy type '{ptype}' diverged: local and incoming have different rules",
            ))

    return report
