"""#2084: an entry overfill is an ordinary Position carrying a quantity-divergence flag."""

import json

import pytest

from tests.specs import spec_pipeline_exchange_materialization as fixture


def test_entry_overfill_is_flagged_normal_position(tmp_path, monkeypatch):
    from aletheia.execution.exchange_materialization import materialize_bitget_entry
    from aletheia.storage import repositories as repo

    fixture._setup_db(tmp_path, monkeypatch)
    _, proposal, _, order = fixture._seed_native_chain(exchange_order_id="over-1", quantity=1.0)
    fill = fixture._private_fill(
        "over-1", "over-fill-1", price=100.0, quantity=1.5, fee=0.01, filled_at_ms=1_784_600_000_000
    )

    result = materialize_bitget_entry(order.id, [fill])
    materialize_bitget_entry(order.id, [fill])  # replay is idempotent

    assert (repo.get_trade_proposal(proposal.id) or {})["status"] == "executed"
    assert (repo.get_order(order.id) or {})["status"] == "overfilled"
    position = repo.get_position(str(result.position_id)) or {}
    assert position["status"] == "open"
    assert position["quantity"] == pytest.approx(1.5)
    assert position["entry_fill_verified"]
    assert json.loads(position["entry_quantity_divergence"]) == {
        "order_id": order.id, "planned_quantity": 1.0, "filled_quantity": 1.5,
    }
    events = [e["event_type"] for e in repo.list_audit_events(limit=50)]
    assert events.count("entry_fill_quantity_diverged") == 1
