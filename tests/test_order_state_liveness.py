"""Every Order state that is not finished must have a way to finish.

The Fedora Bitget Demo runtime kept four entry Orders in ``submission_unknown`` for
eleven days because each exit (the entry gate, the reconciler, stale cleanup)
needed evidence that only another exit could produce. Rather than pin that one
shape, these tests fix the property: for every non-terminal state and every venue
mode, an Order on a drifted credential generation with no open Position converges
to a terminal state under the system's own passes, given a complete venue read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.order_states import NON_TERMINAL_ORDER_STATES

from tests.uta.test_execution_coordinator import _identity, _setup_db
from tests.uta.test_identity_liveness import (
    MODES, _drift, _filled_sibling, _order_row, _stuck_entry, _venue_adapter,
)


def test_the_registry_is_exactly_the_non_terminal_states_of_the_transition_table():
    """A state added to the lifecycle without being classified fails here."""
    declared = {
        state for state, targets in repo._ORDER_TRANSITIONS.items()
        # ``cancelled`` can only revive to a fill; it is terminal.
        if targets and state != "cancelled"
    }
    assert declared == set(NON_TERMINAL_ORDER_STATES)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("state", NON_TERMINAL_ORDER_STATES)
def test_a_non_terminal_entry_order_converges_without_an_open_position(tmp_path, monkeypatch, mode, state):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import coordinator, reconciliation as recon, stale_order_cleanup as soc
    from aletheia.uta import manager
    from aletheia.uta.registry import UTAAdapterConfig, UTAAdapterFactory, UTARegistry

    old = _identity(mode)
    current = _drift(old)
    stuck = _stuck_entry(mode, old)
    _filled_sibling(mode, old)
    if state != "submission_unknown":
        repo.get_db().execute("UPDATE orders SET status=? WHERE id=?", (state, stuck.id))
    stale = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    repo.get_db().execute("UPDATE orders SET created_at=? WHERE id=?", (stale, stuck.id))
    repo.get_db().commit()

    adapter = _venue_adapter(current, known_orders=[("filled-client-1", "venue-filled-1")])
    for module in (coordinator, manager):
        monkeypatch.setattr(module, "create_uta_adapter", lambda _m, _cfg=None: adapter)
    registry = UTARegistry()
    registry.register(
        UTAAdapterConfig(identity=current),
        cast(UTAAdapterFactory, lambda _config: _venue_adapter(current, known_orders=[])),
    )
    monkeypatch.setattr(recon, "build_uta_registry", lambda: registry)

    recon.reconcile()
    soc.cleanup_stale_orphan_orders()

    row = _order_row(stuck.id)
    assert row["status"] not in NON_TERMINAL_ORDER_STATES, f"{mode}/{state} still blocking: {row['status']}"
