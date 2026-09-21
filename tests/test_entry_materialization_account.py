"""Private entry evidence cannot coalesce across frozen UTA accounts."""

import pytest

from tests.specs import spec_pipeline_exchange_materialization as fixture


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("identity_change", [None, "credential_generation", "account_fingerprint", "missing"])
def test_entry_position_account_isolation(tmp_path, monkeypatch, linked, identity_change):
    from aletheia.execution.exchange_materialization import (
        MaterializationIntegrityError,
        materialize_bitget_entry,
    )
    from aletheia.storage import repositories as repo

    db = fixture._setup_db(tmp_path, monkeypatch)
    identity = fixture._spec_identity()

    def entry(order_id, fill_id, timestamp):
        _, _, _, order = fixture._seed_native_chain(exchange_order_id=order_id, quantity=0.25)
        fill = fixture._private_fill(
            order_id, fill_id, price=100.0, quantity=0.25,
            fee=0.01, filled_at_ms=timestamp,
        )
        return order, fill

    first, first_fill = entry("account-first", "account-fill-1", 1_784_600_000_000)
    first_result = materialize_bitget_entry(first.id, [first_fill])
    original = repo._select_one("positions", first_result.position_id)
    if identity_change == "missing":
        # Historical NULL must never inherit the current configured identity.
        db.execute("UPDATE orders SET uta_account_identity_json=NULL WHERE id=?", (first.id,))
    elif identity_change:
        value = identity.credential_generation + 1 if identity_change == "credential_generation" else "different-account"
        changed = identity.model_copy(update={identity_change: value})
        monkeypatch.setattr(fixture, "_spec_identity", lambda: changed)

    second, second_fill = entry("account-second", "account-fill-2", 1_784_600_001_000)
    if linked:
        repo.update_order_metadata(second.id, position_id=original["id"])

    if linked and identity_change:
        with pytest.raises(MaterializationIntegrityError, match="account.*linked Position"):
            materialize_bitget_entry(second.id, [second_fill])
        assert repo._select_one("positions", original["id"]) == original
        assert db.execute("SELECT COUNT(*) FROM fills WHERE order_id=?", (second.id,)).fetchone()[0] == 0
        assert repo.get_order(second.id)["status"] == "materialization_missing"
        return

    result = materialize_bitget_entry(second.id, [second_fill])
    if identity_change:
        assert result.position_id != original["id"]
        assert repo._select_one("positions", original["id"]) == original
        assert repo._select_one("positions", result.position_id)["quantity"] == pytest.approx(0.25)
    else:
        assert result.position_id == original["id"]
        assert repo._select_one("positions", original["id"])["quantity"] == pytest.approx(0.5)
    replay = materialize_bitget_entry(second.id, [second_fill])
    assert replay.position_id == result.position_id
    assert db.execute("SELECT COUNT(*) FROM fills WHERE order_id=?", (second.id,)).fetchone()[0] == 1
