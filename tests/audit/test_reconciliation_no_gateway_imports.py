"""EX-C04 — reconciliation must not import order-placement functions.

Contract: ``docs/design/contracts/execution_contract.md § 6`` — the 红线 row says
对账绝不下单/撤单 (reconciliation never places or cancels an order); it only
compares state and writes convergence/audit records. The spec claim is named
``EX-C04``.

Why this gate exists: the clause was prose only. Nothing stopped a future change
from writing ``from aletheia.execution.gateway import submit_proposal`` inside
``execution/reconciliation.py`` — the reconciliation pass runs on a timer inside
the live runtime, so such an import turns a state-comparison loop into a trading
loop with no review step in between.

Scope: ``core/aletheia/execution/reconciliation.py``. The gate is purely static;
it does not import the module under test.
"""

from __future__ import annotations

import pytest

from tests.audit._segment_guard import ROOT, placement_violations

_TARGET = ROOT / "core" / "aletheia" / "execution" / "reconciliation.py"

# Order-placement entry points. Adding a new one to either module means adding it
# here as well; the gate cannot guess which names place orders.
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
def test_reconciliation_reaches_no_placement_entry_point() -> None:
    violations = placement_violations(_TARGET, _PLACEMENT_TARGETS)
    assert not violations, (
        "EX-C04 violation in execution/reconciliation.py ("
        "docs/design/contracts/execution_contract.md § 6): reconciliation "
        "compares state and writes convergence/audit records, it must never "
        "place or cancel an order.\n  " + "\n  ".join(violations)
    )
