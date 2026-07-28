"""SDD core: SpecClaim dataclass, global registry, and @spec_claim decorator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SpecLevel = Literal["L2a", "L2b", "L2c"]
# #74 fix: added "superseded" to the accepted status values. spec_pi_decision_chain.py:126
# already carried a claim tagged status="superseded" — the Literal previously listed
# only implemented/partial/missing so type checkers accepted the string at construction
# time but the conformance matrix rendered it as "?" (unknown). Now the type reflects
# reality: a claim can be superseded by a newer contract without being deleted, and the
# matrix knows how to icon it.
# Compatibility vocabulary for claim-level conformance only. The stored value
# "implemented" means that this individual executable claim conforms; it is not
# a product implementation or Product Support status.
SpecStatus = Literal["implemented", "partial", "missing", "superseded"]

_VALID_SPEC_STATUSES = frozenset({"implemented", "partial", "missing", "superseded"})


@dataclass(frozen=True)
class SpecClaim:
    """One executable-contract claim.

    ``status`` is local claim conformance. It must never be used to infer that
    a complete product capability is Implemented or Supported.
    """

    claim_id: str
    contract: str
    section: str
    level: SpecLevel
    invariant: str
    status: SpecStatus = "implemented"

    def __post_init__(self) -> None:
        # #74 fix: the previous frozen dataclass accepted any string as `status`
        # (the Literal is a type hint, not a runtime guard). A typo silently
        # rendered as "?" in the matrix. Validate at construction so bad
        # statuses fail loudly right where they're introduced.
        if self.status not in _VALID_SPEC_STATUSES:
            raise ValueError(
                f"SpecClaim.status must be one of {sorted(_VALID_SPEC_STATUSES)}, "
                f"got {self.status!r} on claim {self.claim_id!r}"
            )


SPEC_REGISTRY: dict[str, list[tuple[SpecClaim, object]]] = {}


def spec_claim(*claims: SpecClaim):
    """Bind SpecClaims to a test function. Populates SPEC_REGISTRY at import time."""

    def decorator(fn):
        for claim in claims:
            SPEC_REGISTRY.setdefault(claim.contract, []).append((claim, fn))
        fn._spec_claims = claims  # type: ignore[attr-defined]
        return fn

    return decorator
