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
