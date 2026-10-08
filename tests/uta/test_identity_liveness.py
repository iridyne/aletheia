"""An account identity gate must never be unsatisfiable by the system's own state.

Reproduced from the Fedora Bitget Demo runtime (2026-10-08): four entry Orders
stuck in ``submission_unknown`` under credential generation 137 while the account
was flat and the current generation was 277. The entry gate refused every new
entry (re-anchoring then needed an open Position), and the reconciler refused to
read those Orders at all, so nothing could ever resolve them. The generation had
moved by a config counter alone; the credentials were unchanged.

The fix must hold for every venue this layer serves, not only the one where it
was seen, so the scenarios are parametrized over the three admitted venue modes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aletheia.uta.identity import IdentityRelation, identity_relation
from aletheia.uta.models import (
    InstrumentIdentity,
    OrderLifecycle,
    OrderQueryReport,
    OrderStatusReport,
    QueryScope,
    ReadCompleteness,
    UTAAccountIdentity,
)

from tests.uta.test_execution_coordinator import _identity, _setup_db

MODES = ["bitget_demo", "okx_demo", "binance_testnet"]


def _approve(mode: str, proposal_id: str, risk_id: str) -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    proposal = TradeProposal(
        id=proposal_id, symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
        quantity=0.01, price=100.0, execution_mode=mode, status="approved",
    )
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(RiskDecision(
        id=risk_id, proposal_id=proposal_id, approved=True, allowed_execution_mode=mode,
        proposal_execution_mode=mode, decision_role="authoritative", evaluated_quantity=0.01,
        proposal_commit_hash=proposal.commit_hash,
    ))


def _stored_identity(order_id: str) -> UTAAccountIdentity:
    from aletheia.storage import repositories as repo

    row = repo.get_order(order_id)
    assert row is not None
    return UTAAccountIdentity.model_validate_json(row["uta_account_identity_json"])


def _order_row(order_id: str) -> dict:
    from aletheia.storage import repositories as repo

    row = repo.get_order(order_id)
    assert row is not None
    return row


def _drift(identity: UTAAccountIdentity, *, by: int = 1, **changes: object) -> UTAAccountIdentity:
    return identity.model_copy(update={"credential_generation": identity.credential_generation + by, **changes})


@pytest.mark.parametrize("mode", MODES)
def test_identity_relation_separates_the_account_from_how_it_was_reached(mode):
    base = _identity(mode)
    assert identity_relation(base, base) is IdentityRelation.SAME
    # Provenance fields describe the client library, not the account.
    assert identity_relation(base, base.model_copy(update={"ccxt_version": "9.9.9"})) is IdentityRelation.SAME
    assert identity_relation(base, base.model_copy(update={"adapter_version": "2"})) is IdentityRelation.SAME
    assert identity_relation(base, base.model_copy(update={"account_fingerprint": "x"})) is IdentityRelation.SAME
    # A different credential generation on the same account is drift, in either direction.
    assert identity_relation(base, _drift(base)) is IdentityRelation.GENERATION_DRIFT
    assert identity_relation(_drift(base, by=5), base) is IdentityRelation.GENERATION_DRIFT
    # Anything that names another account is a hard difference.
    for change in (
        {"account_id": "other"}, {"execution_mode": "other_demo"}, {"venue": "other"},
        {"environment": "live" if base.environment != "live" else "demo"}, {"profile": "other"},
    ):
        assert identity_relation(base, base.model_copy(update=change)) is IdentityRelation.DIFFERENT
        drifted = base.model_copy(update={"credential_generation": base.credential_generation + 1, **change})
        assert identity_relation(base, drifted) is IdentityRelation.DIFFERENT


def _stuck_entry(mode, identity, *, symbol="UNI/USDT", client_id="stuck-client-1"):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    order = Order(
        symbol=symbol, market_type="spot", direction="long", order_type="limit", quantity=2.0, price=10.0,
        execution_mode=mode, source="exchange", status="submission_unknown", client_order_id=client_id,
        uta_account_identity_json=identity.model_dump_json(),
    )
    repo.insert_order(order)
    return order


def _filled_sibling(mode, identity, *, client_id="filled-client-1", venue_id="venue-filled-1"):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    order = Order(
        symbol="BTC/USDT", market_type="spot", direction="long", order_type="market", quantity=0.01,
        execution_mode=mode, source="exchange", status="filled", client_order_id=client_id,
        exchange_order_id=venue_id, uta_account_identity_json=identity.model_dump_json(),
    )
    repo.insert_order(order)
    return order


_SPOT_BTC = InstrumentIdentity(
    canonical_symbol="BTC/USDT", venue_symbol="BTC/USDT", market_type="spot",
    base_currency="BTC", quote_currency="USDT",
)


def _venue_adapter(current, *, known_orders, position_ids=(), close_calls=None):
    """An adapter whose venue knows exactly ``known_orders`` (client_id, venue_id) and nothing else."""
    from aletheia.uta.models import PositionListReport

    class VenueAdapter:
        identity = current

        def __init__(self):
            self.reads = []

        async def get_order(self, query):
            self.reads.append((query.client_order_id, query.venue_order_id))
            for client_id, venue_id in known_orders:
                if query.client_order_id == client_id or query.venue_order_id == venue_id:
                    found = OrderStatusReport(
                        identity=current, instrument=_SPOT_BTC, client_order_id=client_id,
                        venue_order_id=venue_id, side="buy", order_type="market",
                        original_quantity=Decimal("0.01"), filled_quantity=Decimal("0.01"),
                        state=OrderLifecycle.FILLED, received_at=datetime.now(timezone.utc),
                    )
                    break
            else:
                found = None
            return OrderQueryReport(
                identity=current, completeness=ReadCompleteness.COMPLETE, scope=query.scope,
                received_at=datetime.now(timezone.utc), profile_version="1", found=found,
            )

        async def connect(self):
            pass

        async def get_mass_status(self, query):
            from aletheia.uta.models import ExecutionMassStatus

            # Complete, and the venue holds none of the queried ids.
            return ExecutionMassStatus(
                identity=current, completeness=ReadCompleteness.COMPLETE, scope=query.scope,
                received_at=datetime.now(timezone.utc), profile_version="1",
            )

        async def get_positions(self, query=None):
            return PositionListReport(
                identity=current, completeness=ReadCompleteness.COMPLETE, scope=QueryScope(),
                received_at=datetime.now(timezone.utc), profile_version="1", positions=(),
            )

        async def submit_order(self, command):
            from tests.uta.test_execution_coordinator import _ack

            if close_calls is not None:
                close_calls.append(command.expected_account)
            return _ack(command)

        async def close(self):
            pass

    return VenueAdapter()


@pytest.mark.parametrize("mode", MODES)
def test_flat_account_with_a_stale_drifted_order_can_still_enter(tmp_path, monkeypatch, mode):
    """The deadlock: a drifted in-flight Order and no open Position."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo

    old = _identity(mode)
    current = _drift(old)
    stuck = _stuck_entry(mode, old)
    sibling = _filled_sibling(mode, old)
    submitted = []
    adapter = _venue_adapter(current, known_orders=[("filled-client-1", "venue-filled-1")], close_calls=submitted)
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _m: adapter)
    audit = []
    monkeypatch.setattr(coordinator, "write_post_commit_audit", lambda event, **kw: audit.append(event) or True)

    _approve(mode, "p-flat", "r-flat")
    result = _execute_for(mode, "p-flat", "r-flat")

    assert result["order_status"] == "submitted"
    assert submitted == [current]
    # Only the Order that blocked the gate is re-bound; the Filled sibling is terminal
    # evidence and keeps the generation it was materialized under.
    assert _stored_identity(stuck.id) == current
    assert _stored_identity(sibling.id) == old
    reanchored = [e for e in audit if e["event_type"] == "uta_account_reanchored"]
    assert {e["entity_id"] for e in reanchored} == {stuck.id}
    assert reanchored[0]["proof_order_id"] == sibling.id
    assert reanchored[0]["position_id"] is None


@pytest.mark.parametrize("mode", MODES)
def test_without_venue_evidence_of_the_same_account_the_gate_stays_closed(tmp_path, monkeypatch, mode):
    """No locally-filled sibling is readable: the stuck Order alone proves nothing."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo

    old = _identity(mode)
    current = _drift(old)
    stuck = _stuck_entry(mode, old)
    _filled_sibling(mode, old)
    # The venue returns nothing for the sibling (e.g. credentials now reach another account).
    adapter = _venue_adapter(current, known_orders=[])
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _m: adapter)
    _approve(mode, "p-closed", "r-closed")

    with pytest.raises(ValueError, match="uta_account_identity_mismatch"):
        _execute_for(mode, "p-closed", "r-closed")
    assert _stored_identity(stuck.id) == old


def _execute_for(mode, proposal_id, risk_id):
    from aletheia.execution.coordinator import execute_order

    return execute_order(
        proposal_id=proposal_id, risk_decision_id=risk_id, symbol="BTC/USDT", market_type="spot",
        direction="long", order_type="market", quantity=0.01, price=100.0, execution_mode=mode,
    )


@pytest.mark.parametrize("mode", MODES)
def test_a_different_account_is_never_rebound(tmp_path, monkeypatch, mode):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.coordinator import reanchor_drifted_orders
    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo

    old = _identity(mode)
    current = _drift(old, account_id="another-account")
    stuck = _stuck_entry(mode, old)
    _filled_sibling(mode, old)
    adapter = _venue_adapter(current, known_orders=[("filled-client-1", "venue-filled-1")])
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _m: adapter)

    assert reanchor_drifted_orders(mode, [_order_row(stuck.id)]) == set()
    assert adapter.reads == []
    assert _stored_identity(stuck.id) == old


@pytest.mark.parametrize("mode", MODES)
def test_reconcile_reads_a_drifted_order_instead_of_skipping_it_forever(tmp_path, monkeypatch, mode):
    """The reconciler's own half of the deadlock: it used to skip these every pass."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import coordinator
    from aletheia.execution import reconciliation as recon
    from aletheia.storage import repositories as repo
    from typing import cast

    from aletheia.uta.registry import UTAAdapterConfig, UTAAdapterFactory, UTARegistry

    old = _identity(mode)
    current = _drift(old)
    stuck = _stuck_entry(mode, old)
    _filled_sibling(mode, old)
    adapter = _venue_adapter(current, known_orders=[("filled-client-1", "venue-filled-1")])
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _m: adapter)
    registry = UTARegistry()
    registry.register(
        UTAAdapterConfig(identity=current),
        cast(UTAAdapterFactory, lambda _config: _venue_adapter(current, known_orders=[])),
    )
    monkeypatch.setattr(recon, "build_uta_registry", lambda: registry)

    skipped = []
    original = recon._write_reconcile_event
    monkeypatch.setattr(
        recon, "_write_reconcile_event",
        lambda order, *, state, reason: skipped.append((order["id"], reason)) or original(order, state=state, reason=reason),
    )
    recon.reconcile()

    assert (stuck.id, "uta_account_identity_unavailable") not in skipped
    assert _stored_identity(stuck.id) == current


def test_a_library_upgrade_alone_does_not_turn_every_stored_identity_into_a_mismatch(tmp_path, monkeypatch):
    """Provenance is not identity: the upgrade must not block entries or closes."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    from tests.uta.test_execution_coordinator import _insert_bound_position

    old = _identity("bitget_demo").model_copy(update={"ccxt_version": "4.5.0"})
    position = Position(
        symbol="BTC/USDT", market_type="spot", direction="long", quantity=0.01, entry_price=100.0,
        current_price=101.0, execution_mode="bitget_demo", status="open",
    )
    _insert_bound_position(position)
    assert position.order_id is not None
    repo.update_order_metadata(position.order_id, uta_account_identity_json=old.model_dump_json())
    upgraded = old.model_copy(update={"ccxt_version": "4.5.84"})
    adapter = _venue_adapter(upgraded, known_orders=[])
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _m: adapter)
    _approve("bitget_demo", "p-up", "r-up")

    result = _execute_for("bitget_demo", "p-up", "r-up")
    assert result["order_status"] == "submitted"
    assert adapter.reads == []  # same account: no re-bind, no venue proof needed


@pytest.mark.parametrize("mode", MODES)
def test_an_unchanged_inconclusive_verdict_is_recorded_once(tmp_path, monkeypatch, mode):
    """A stuck Order used to accrue one identical audit row per 2-minute pass."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation as recon
    from aletheia.storage import repositories as repo

    stuck = _stuck_entry(mode, _identity(mode))
    row = _order_row(stuck.id)

    def rows() -> int:
        return repo.get_db().execute(
            "SELECT COUNT(*) c FROM audit_events WHERE entity_id=? AND event_type='order_reconciliation_inconclusive'",
            (stuck.id,),
        ).fetchone()["c"]

    for _ in range(3):
        recon._write_reconcile_event(row, state="submission_unknown", reason="same_reason")
    assert rows() == 1
    recon._write_reconcile_event(row, state="submission_unknown", reason="another_reason")
    recon._write_reconcile_event(row, state="submission_unknown", reason="same_reason")
    assert rows() == 3
