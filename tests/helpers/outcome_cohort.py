"""Build an in-memory complete outcome cohort for consumer unit tests."""

from __future__ import annotations


def available_cohort(eligible: list[dict], excluded: list[dict] | None = None):
    """Return an available cohort: ``eligible`` rows pass, ``excluded`` rows do not."""
    from aletheia.analytics.outcome_integrity import (
        CompleteOutcomeCohort,
        OutcomeAssessment,
    )

    excluded = excluded or []
    rows = (*eligible, *excluded)
    return CompleteOutcomeCohort(
        rows=rows,
        assessments=(
            *(OutcomeAssessment(eligible=True, reason="eligible") for _ in eligible),
            *(OutcomeAssessment(eligible=False, reason="excluded") for _ in excluded),
        ),
        source_raw_event_count=len(rows),
        loaded_raw_event_count=len(rows),
        cohort_complete=True,
        cohort_state="available",
        ordered_source_digest="test-digest",
        identity={},
    )
