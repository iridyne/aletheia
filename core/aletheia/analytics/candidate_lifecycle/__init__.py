"""Candidate-first lifecycle reader for Universe-origin trade chains.

Single entry point for all consumers that need to walk a complete ancestry
from any anchor (candidate_id, generation_id, entry_plan_id, proposal_id,
order_id, position_id) to the LifecycleLineage dataclass.
"""

from aletheia.analytics.candidate_lifecycle.lineage_reader import (
    LifecycleLineage,
    for_candidate,
    for_entry_plan,
    for_generation,
    for_order,
    for_position,
    for_proposal,
)

__all__ = [
    "LifecycleLineage",
    "for_candidate",
    "for_entry_plan",
    "for_generation",
    "for_order",
    "for_position",
    "for_proposal",
]
