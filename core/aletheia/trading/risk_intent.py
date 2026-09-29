"""AI Risk Intent (Issue #3729 Contract A): posture, never a number.

The AI declares whether it holds the Owner's baseline risk allocation or
reduces it for one Entry. There is no INCREASED posture in V1 -- the AI can
only hold or reduce the Owner baseline, never scale it up. The multiplier
behind REDUCED always comes from Owner policy; the AI never supplies it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from aletheia.config.schema import ModeRiskPolicy

# Stable failure reason (Issue #3729 §12). Never carry exchange/validation text.
REASON_RISK_INTENT_INVALID = "RISK_INTENT_INVALID"


class RiskIntentInvalid(Exception):
    """A typed Risk Intent resolution failure. ``reason`` is the stable code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class RiskIntent(BaseModel):
    """The AI's declared risk posture for one Entry. Never a number."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    posture: Literal["BASELINE", "REDUCED"]
    reason: str = Field(min_length=1, max_length=512)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=8)


def resolve_risk_intent_multiplier(
    intent: "RiskIntent | None", policy: "ModeRiskPolicy"
) -> tuple[float, str]:
    """Turn a declared posture into the Owner-policy multiplier that governs sizing.

    BASELINE is always 1.0 -- the AI cannot request more than the Owner
    baseline. REDUCED reads ``policy.loss.reduced_risk_multiplier``; a missing,
    non-finite, or out-of-``(0, 1)`` policy value is a typed failure, never a
    fabricated fallback. ``intent`` is ``None`` for historical hypotheses that
    predate this contract; that is also a typed failure, not an AttributeError.
    """
    if intent is None:
        raise RiskIntentInvalid(REASON_RISK_INTENT_INVALID)
    if intent.posture == "BASELINE":
        return 1.0, "baseline"
    if intent.posture != "REDUCED":
        raise RiskIntentInvalid(REASON_RISK_INTENT_INVALID)
    loss = getattr(policy, "loss", None)
    raw = getattr(loss, "reduced_risk_multiplier", None) if loss is not None else None
    if raw is None:
        raise RiskIntentInvalid(REASON_RISK_INTENT_INVALID)
    try:
        multiplier = float(raw)
    except (TypeError, ValueError):
        raise RiskIntentInvalid(REASON_RISK_INTENT_INVALID) from None
    if not math.isfinite(multiplier) or not (0 < multiplier < 1):
        raise RiskIntentInvalid(REASON_RISK_INTENT_INVALID)
    return multiplier, "policy.loss.reduced_risk_multiplier"
