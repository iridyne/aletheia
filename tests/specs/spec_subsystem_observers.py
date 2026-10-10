"""Spec: retired subsystem observers stay unresolvable.

Contract: agent_contracts.md §4 (observer agents)
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "OBS-S08", "agent_contracts", "§4 stock_observer retired", "L2b",
        "The retired stock_observer cannot be resolved for execution",
    ),
)
def test_retired_stock_observer_cannot_run():
    from aletheia.agents import get_agent_fn

    with pytest.raises(ValueError, match="Unknown agent"):
        get_agent_fn("stock_observer")
