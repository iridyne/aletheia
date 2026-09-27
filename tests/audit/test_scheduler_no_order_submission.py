"""Scheduler orchestrates, it does not execute.

Contract: ``docs/design/contracts/scheduler_contract.md`` — the scheduler owns the
job registry, executor pools, failure counting and shutdown protocol. Order
placement belongs to the Execution Gateway, not to a timer. Segment isolation
(see ``docs/design/architecture/ARCHITECTURE.md``) requires that the scheduling
layer reaches execution only through the reconciliation / maintenance entry
points it already imports (``reconciliation.reconcile``,
``stale_order_cleanup.cleanup_stale_orphan_orders``, ``venue_capabilities.refresh_*``).

Why this gate exists: ``init_scheduler`` is a 467-line registration function and
already the largest reachable surface in ``agent_runtime/``. An accidental
placement import here would fire from a background thread on a timer, outside
every Risk/Gateway review the placement path is supposed to pass through.

Scope: ``agent_runtime/scheduler.py`` and ``agent_runtime/scheduler_retention.py``.
Purely static; it does not import the modules under test.
"""

from __future__ import annotations

import pytest

from tests.audit._segment_guard import ROOT, placement_violations

_TARGETS = [
    ROOT / "core" / "aletheia" / "agent_runtime" / "scheduler.py",
    ROOT / "core" / "aletheia" / "agent_runtime" / "scheduler_retention.py",
]

_PLACEMENT_TARGETS = {
    "aletheia.execution.gateway": frozenset(
        {
            "submit_proposal",
            "execute_existing_proposal",
            "cancel_entry_plan_order",
            "close_exchange_position",
            "place_exchange_protection",
            "replace_exchange_protective_stop",
        }
    ),
    "aletheia.execution.coordinator": frozenset(
        {
            "execute_order",
            "close_position",
            "place_protection",
            "replace_protective_stop",
            "modify_order",
            "cancel_order",
        }
    ),
}


@pytest.mark.fast
@pytest.mark.parametrize("path", _TARGETS, ids=lambda p: p.name)
def test_scheduler_reaches_no_placement_entry_point(path) -> None:
    violations = placement_violations(path, _PLACEMENT_TARGETS)
    assert not violations, (
        f"scheduler segment isolation violation in {path.name}: the scheduler "
        "registers jobs and records failures; order placement goes through the "
        "Execution Gateway under Risk approval.\n  " + "\n  ".join(violations)
    )
