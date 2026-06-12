"""DTOs for PI runtime continuity control-plane responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuntimeRecord:
    runtime_id: str
    state: str
    code_version: str | None = None
    config_version: str | None = None
    schema_version: str | None = None
    build_git_sha: str | None = None
    release_bundle_hash: str | None = None
    artifact_path: str | None = None
    mode: str | None = None
    started_at: str | None = None
    promoted_at: str | None = None
    drained_at: str | None = None
    failed_at: str | None = None
    rolled_back_at: str | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class RuntimeCheckpoint:
    id: str
    runtime_id: str
    checkpoint_type: str
    status: str = "available"
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    restored_at: str | None = None


@dataclass(slots=True)
class RuntimeEvent:
    id: str
    event_type: str
    phase: str
    runtime_id: str | None = None
    severity: str = "info"
    actor: str = "system"
    cause: str | None = None
    kind: str = "runtime_transition"
    status: str = "recorded"
    operation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass(slots=True)
class RuntimeOwnership:
    id: str
    entity_type: str
    entity_id: str
    runtime_id: str
    ownership_kind: str = "operator_task"
    status: str = "active"
    acquired_at: str | None = None
    released_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
