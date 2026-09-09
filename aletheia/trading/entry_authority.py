"""Canonical Entry permission and execution-route authority."""

from __future__ import annotations

from dataclasses import dataclass

from aletheia.agent_shell.permissions import WORKER_MODE_RANK
from aletheia.execution.venue_capabilities import ENTRY_EXECUTION_MINIMUM_PERMISSION


_PERMISSION_RANK = WORKER_MODE_RANK


@dataclass(frozen=True)
class EntryRuntimeAuthority:
    allowed: bool
    execution_route: str
    minimum_permission: str | None
    reason: str | None = None


def resolve_entry_runtime_authority(cfg) -> EntryRuntimeAuthority:
    """Resolve capability and target independently; Live stays a separate gate."""

    route = str(cfg.exchange.paper_execution_mode or "").strip()
    minimum = ENTRY_EXECUTION_MINIMUM_PERMISSION.get(route)
    if str(cfg.live.state) != "live_disabled":
        return EntryRuntimeAuthority(False, route, minimum, "entry_live_authority_unavailable")
    if minimum is None:
        return EntryRuntimeAuthority(False, route, None, "entry_execution_route_unsupported")
    if _PERMISSION_RANK.get(str(cfg.agent.permission_mode), -1) < _PERMISSION_RANK[minimum]:
        return EntryRuntimeAuthority(False, route, minimum, "entry_permission_insufficient")
    return EntryRuntimeAuthority(True, route, minimum)


def require_entry_runtime_authority(
    cfg,
    *,
    execution_route: str | None = None,
    required_permission: str | None = None,
) -> EntryRuntimeAuthority:
    authority = resolve_entry_runtime_authority(cfg)
    if execution_route is not None and authority.execution_route != execution_route:
        raise ValueError("entry_execution_route_changed")
    if required_permission is not None and authority.minimum_permission != required_permission:
        raise ValueError("entry_minimum_permission_mismatch")
    if not authority.allowed:
        raise ValueError(authority.reason or "entry_runtime_authority_unavailable")
    return authority
