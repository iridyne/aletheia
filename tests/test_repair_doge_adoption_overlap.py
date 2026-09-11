"""Bounded recovery: preserve history, reject incomplete/changed state, replay once."""
import copy
import json

import pytest

from aletheia.maintenance import repair_doge_adoption_overlap as repair
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, Position, TradeProposal


@pytest.fixture
def case():
    identity = {"execution_mode": "bitget_demo", "environment": "demo", "profile": "classic_v2"}
    common = dict(symbol="DOGE/USDT", market_type="usdt_futures", direction="long", execution_mode="bitget_demo")
    for oid, pid, source in [(repair.NATIVE_ORDER, repair.NATIVE_PROPOSAL, "native"),
                              (repair.ADOPTED_ORDER, repair.ADOPTED_PROPOSAL, "adopted")]:
        proposal = TradeProposal(id=pid, source=source, quantity=204, price=0.08689, **common)
        repo._insert("trade_proposals", proposal.model_dump())
        order = Order(id=oid, proposal_id=pid, position_id=repair.POSITION, source=source,
                      order_type="limit", quantity=204, price=0.08689, status="filled",
                      exchange_order_id="venue-order" if source == "native" else None,
                      client_order_id="native-client", uta_account_identity_json=json.dumps(identity), **common)
        repo._insert("orders", order.model_dump())
    repo.insert_position(Position(id=repair.POSITION, source="adopted", quantity=408,
        entry_price=0.08689, current_price=0.087, proposal_id=repair.ADOPTED_PROPOSAL,
        order_id=repair.ADOPTED_ORDER, opened_at="2026-09-11T15:40:42.962000+00:00",
        stop_price=0.08, take_profit_price=0.1, **common))
    repo.insert_fill(Fill(id="private-fill:bitget_demo:venue-fill", order_id=repair.NATIVE_ORDER,
                         fill_quantity=204, fill_price=0.08689, fee=0.01063533,
                         fee_currency="USDT", fee_source="reconciled"))
    base = dict(identity=identity, instrument={"canonical_symbol": "DOGE/USDT", "market_type": "usdt_futures"})
    envelope = dict(identity=identity, completeness="complete", scope={"start_at": repair.START,
                       "end_at": "2026-09-11T17:00:00Z", "next_cursor": None})
    evidence = {
        "fills": dict(**envelope, fills=[dict(**base, venue_fill_id="venue-fill", venue_order_id="venue-order",
            side="buy", trade_intent="open", quantity="204", price="0.08689", fee="0.01063533",
            fee_currency="USDT", executed_at="2026-09-11T15:40:42.951000Z")]),
        "order": dict(**envelope, found=dict(**base, venue_order_id="venue-order", client_order_id="native-client",
            state="filled", reduce_only=False, original_quantity="204", filled_quantity="204",
            source_at="2026-09-11T15:40:42.962000Z")),
        "positions": dict(**envelope, positions=[dict(**base, quantity="204", entry_price="0.08689", side="long",
            opened_at="2026-09-11T15:40:42.962000Z", opened_at_provenance="exchange_reported",
            source_at="2026-09-11T15:40:42.962000Z")]),
    }
    return repair.snapshot(), evidence


def test_recovery_is_atomic_preserves_history_and_replays_once(case):
    before, evidence = case
    assert repair.apply(before, evidence)["status"] == "applied"
    after = repair.snapshot()
    assert after["position"]["quantity"] == 204
    assert after["position"]["entry_fee"] == 0.01063533
    assert after["position"]["proposal_id"] == repair.NATIVE_PROPOSAL
    assert after["position"]["entry_fill_verified"] == 1
    for key in ("orders", "proposals", "fills", "closes", "exit_versions", "protection"):
        assert after[key] == before[key]
    for key in ("stop_price", "take_profit_price", "exit_plan_version", "stop_source"):
        assert after["position"][key] == before["position"][key]
    assert repair.apply(before, evidence) == {"status": "already_applied"}
    assert repair.snapshot() == after
    assert repo.get_db().execute("SELECT count(*) FROM audit_events WHERE id=?", (repair.AUDIT,)).fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["partial", "identity", "second_entry", "changed_state", "audit_failure"])
def test_recovery_rejects_incomplete_or_changed_evidence_without_mutation(case, monkeypatch, failure):
    before, evidence = case
    evidence = copy.deepcopy(evidence)
    if failure == "partial":
        evidence["fills"]["completeness"] = "partial"
    elif failure == "identity":
        evidence["fills"]["identity"] = {"environment": "live"}
    elif failure == "second_entry":
        evidence["fills"]["fills"].append(copy.deepcopy(evidence["fills"]["fills"][0]))
    elif failure == "changed_state":
        repo.get_db().execute("UPDATE positions SET stop_price=0.081 WHERE id=?", (repair.POSITION,))
        repo.get_db().commit()
    elif failure == "audit_failure":
        def fail(_):
            raise ValueError("audit_unavailable")
        monkeypatch.setattr(repo, "insert_audit_event_outbox", fail)
    unchanged = repair.snapshot()
    with pytest.raises(ValueError):
        repair.apply(before, evidence)
    assert repair.snapshot() == unchanged
    assert repo.get_db().execute("SELECT count(*) FROM audit_events WHERE id=?", (repair.AUDIT,)).fetchone()[0] == 0
