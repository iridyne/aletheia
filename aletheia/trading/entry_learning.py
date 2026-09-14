"""Core contract and optional binding for bounded Entry Learning evidence."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ENTRY_LEARNING_CONTEXT_VERSION = "entry-learning-context/v1"
ENTRY_LEARNING_OUTCOME_VERSION = "entry-learning-outcome/v1"
ENTRY_LEARNING_METRIC_VERSION = "entry-learning-metrics/v1"


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("entry_learning_timezone_required")
    return parsed.astimezone(timezone.utc)


class EntryLearningContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["entry-learning-context/v1"] = ENTRY_LEARNING_CONTEXT_VERSION
    as_of_at: str
    status: Literal["unavailable", "observational", "experimental"]
    scope: dict[str, Any]
    support: dict[str, Any]
    metrics: dict[str, Any]
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    source_fingerprint: str | None
    experiment_identity: dict[str, Any] | None = None
    outcome_contract_version: str = ENTRY_LEARNING_OUTCOME_VERSION
    metric_version: str = ENTRY_LEARNING_METRIC_VERSION
    blocking_reasons: tuple[str, ...] = ()

    @field_validator("as_of_at")
    @classmethod
    def _utc_cutoff(cls, value: str) -> str:
        return _instant(value).isoformat()

    @model_validator(mode="after")
    def _bounded(self) -> "EntryLearningContext":
        for name in ("complete_outcome_coverage",):
            value = self.support.get(name)
            if value is not None and (
                not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1
            ):
                raise ValueError(f"entry_learning_{name}_invalid")
        for name in ("fill_rate", "no_plan_rate", "no_trigger_rate", "no_fill_rate"):
            value = self.metrics.get(name)
            if value is not None and (
                not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1
            ):
                raise ValueError(f"entry_learning_{name}_invalid")
        if self.status == "unavailable" and not self.blocking_reasons:
            raise ValueError("entry_learning_unavailable_reason_required")
        if self.status != "unavailable" and (not self.source_fingerprint or self.blocking_reasons):
            raise ValueError("entry_learning_available_identity_invalid")
        if self.status == "experimental":
            identity = self.experiment_identity or {}
            if (
                identity.get("assignment") != "treatment"
                or not identity.get("observation_id")
                or not identity.get("manifest_hash")
                or not identity.get("receipt_id")
            ):
                raise ValueError("entry_learning_experiment_identity_invalid")
        elif self.experiment_identity is not None:
            raise ValueError("entry_learning_experiment_identity_forbidden")
        return self


def empty_entry_learning_context(
    *,
    as_of_at: str,
    mechanism: str,
    direction: str,
    market_type: str,
    policy: str,
    policy_epoch: str | None,
    reasons: tuple[str, ...] = ("entry_learning_evidence_unavailable",),
) -> EntryLearningContext:
    return EntryLearningContext(
        as_of_at=as_of_at,
        status="unavailable",
        scope={
            "mechanism": mechanism,
            "direction": direction,
            "market_type": market_type,
            "entry_profile_id": "mechanism-aware-grounded/v1",
            "horizon": "4h",
            "candidate_selection_policy": policy,
            "policy_epoch": policy_epoch,
        },
        support={
            "independent_n": 0,
            "distinct_symbols": 0,
            "distinct_dates": 0,
            "cycle_n": 0,
            "planning_thread_n": 0,
            "max_cycles_per_thread": 0,
            "complete_outcome_coverage": 0.0,
            "evidence_ref_total": 0,
            "evidence_refs_truncated": False,
        },
        metrics={
            "intent_to_treat_net_utility_pct": None,
            "fill_rate": None,
            "no_plan_rate": None,
            "no_trigger_rate": None,
            "no_fill_rate": None,
            "mae_p95_pct": None,
            "mfe_pct": None,
            "mfe_to_realized_capture": None,
            "model_and_data_cost": None,
        },
        evidence_refs=(),
        source_fingerprint=None,
        blocking_reasons=tuple(sorted(set(reasons))),
    )


_context_loader: Callable[..., EntryLearningContext] | None = None


def configure_entry_learning(loader: Callable[..., EntryLearningContext] | None) -> None:
    """Bind the optional evidence producer without resolving any treatment."""
    global _context_loader
    _context_loader = loader


def resolve_entry_learning_context(*, generation: dict, model_provider: str, profiles: tuple, **scope) -> EntryLearningContext:
    if _context_loader is None:
        return empty_entry_learning_context(**scope, reasons=("entry_learning_extension_unavailable",))
    result = _context_loader(
        generation=generation, model_provider=model_provider, profiles=profiles, **scope,
    )
    context = EntryLearningContext.model_validate(
        result.model_dump(mode="json") if isinstance(result, EntryLearningContext) else result,
    )
    if context.status == "observational":
        raise ValueError("entry_learning_treatment_identity_required")
    return context
