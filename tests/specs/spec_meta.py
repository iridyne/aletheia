"""SDD core: SpecClaim dataclass, global registry, and @spec_claim decorator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SpecLevel = Literal["L2a", "L2b", "L2c"]
SpecStatus = Literal["implemented", "partial", "missing"]


@dataclass(frozen=True)
class SpecClaim:
    claim_id: str
    contract: str
    section: str
    level: SpecLevel
    invariant: str
    status: SpecStatus = "implemented"


SPEC_REGISTRY: dict[str, list[tuple[SpecClaim, object]]] = {}


def spec_claim(*claims: SpecClaim):
    """Bind SpecClaims to a test function. Populates SPEC_REGISTRY at import time."""

    def decorator(fn):
        for claim in claims:
            SPEC_REGISTRY.setdefault(claim.contract, []).append((claim, fn))
        fn._spec_claims = claims  # type: ignore[attr-defined]
        return fn

    return decorator
