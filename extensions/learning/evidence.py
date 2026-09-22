"""Evidence classes for measured Learning contribution."""

from __future__ import annotations

from enum import StrEnum


class EvidenceClass(StrEnum):
    SETTLED_QUANTITATIVE = "settled_quantitative"
    DETERMINISTIC_DERIVED = "deterministic_derived"
    NARRATIVE = "narrative"
    DIAGNOSTIC_PROPOSAL = "diagnostic_proposal"


def quantitative_sample_weight(evidence_class: EvidenceClass | str) -> int:
    """Return whether an artifact is an independent quantitative observation."""
    try:
        kind = EvidenceClass(evidence_class)
    except (TypeError, ValueError):
        return 0
    return 1 if kind is EvidenceClass.SETTLED_QUANTITATIVE else 0
