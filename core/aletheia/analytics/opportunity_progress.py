"""Current entry facts projected from the canonical planning workbench."""

from datetime import datetime

from aletheia.storage import repositories as repo


def build_current_entry_status(*, now: datetime) -> dict:
    """Keep current planning authority separate from historical Opportunity cohorts."""
    return {
        "as_of": now.isoformat(),
        "summary": repo.get_entry_workbench_summary(now=now),
        "candidate_attention": repo.list_entry_workbench_candidates(),
        "entry_plans": repo.list_watchable_entry_plans(),
        "planning_history": repo.list_planning_history(now=now),
    }
