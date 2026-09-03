"""Versioned capability registry for deterministic Grounded Entry geometry."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


Mechanism = Literal[
    "continuation", "reversal", "positioning_forced_flow", "information_catalyst"
]


@dataclass(frozen=True)
class EntryProfileCapability:
    profile_id: str
    version: str
    supported_mechanisms: tuple[Mechanism, ...]
    required_fact_sections: tuple[str, ...]
    primary_anchor_kinds: tuple[str, ...]
    invalidation_anchor_kinds: tuple[str, ...]
    supported_timeframes: tuple[str, ...]
    compiler_id: str
    compiler_version: str
    trigger_spec_id: str | None
    trigger_spec_version: str | None
    trigger_freshness_ms: int | None
    unavailable_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"id": self.profile_id, **{key: value for key, value in asdict(self).items() if key != "profile_id"}}


_SUPPORTED: tuple[Mechanism, ...] = (
    "continuation", "reversal", "positioning_forced_flow",
)
_REGISTRY = (
    EntryProfileCapability(
        "grounded-anchor-resting-maker", "v1", _SUPPORTED,
        ("market_structure", "execution_context"),
        ("candidate_reference", "causal_breakout", "confirmed_swing", "broken_structure_level"),
        ("confirmed_swing", "broken_structure_level"),
        ("15m",), "grounded-anchor-compiler", "v2", None, None, None,
    ),
    EntryProfileCapability(
        "grounded-anchor-retrace-reclaim", "v1", _SUPPORTED,
        ("market_structure", "execution_context"),
        ("causal_breakout", "confirmed_swing", "broken_structure_level"),
        ("confirmed_swing", "broken_structure_level"),
        ("15m",), "grounded-anchor-compiler", "v2",
        "anchor-retrace-reclaim", "v1", 2_000,
    ),
)


def compatible_entry_profiles(mechanism: str) -> tuple[EntryProfileCapability, ...]:
    return tuple(profile for profile in _REGISTRY if mechanism in profile.supported_mechanisms)


def require_compatible_entry_profile(
    mechanism: str,
    profile_id: str,
    version: str,
) -> EntryProfileCapability:
    profile = next(
        (
            item for item in _REGISTRY
            if (item.profile_id, item.version) == (profile_id, version)
        ),
        None,
    )
    if profile is None:
        raise ValueError("entry_profile_not_registered")
    if mechanism not in profile.supported_mechanisms or profile.unavailable_reason:
        raise ValueError(profile.unavailable_reason or "entry_profile_mechanism_unsupported")
    return profile


__all__ = [
    "EntryProfileCapability", "compatible_entry_profiles",
    "require_compatible_entry_profile",
]
