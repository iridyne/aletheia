"""One immutable Proposal per Plan leg; old NULL identity keeps its hash."""
import sqlite3

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.hashing import sha256_json
from aletheia.storage.models import TradeProposal


def proposal(leg):
    return TradeProposal(symbol="BTC/USDT", market_type="futures", direction="long",
        execution_mode="bitget_demo", order_type="limit", price=100, quantity=1,
        stop_price=95, take_profit=110, risk_reward=2, entry_plan_id="plan", entry_leg_index=leg)


def test_leg_uniqueness_is_durable_and_hash_covered():
    first = proposal(0)
    repo.insert_trade_proposal(first)
    second = proposal(1)
    repo.insert_trade_proposal(second)
    assert repo.get_trade_proposal_by_entry_plan("plan", leg_index=0)["id"] == first.id
    assert repo.get_trade_proposal_by_entry_plan("plan")["id"] == second.id
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_trade_proposal(proposal(1))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        repo.execute_write("UPDATE trade_proposals SET entry_leg_index=0 WHERE id=?", (second.id,))
    assert repo._compute_proposal_hash(second.model_copy(update={"entry_leg_index": 0})) != second.commit_hash


def test_historical_null_leg_keeps_pre_migration_hash_and_occupies_leg_zero():
    old = proposal(None)
    old_payload = old.model_dump(exclude={"status", "commit_hash", "entry_leg_index"})
    assert repo._compute_proposal_hash(old) == sha256_json(old_payload)[:16]
    repo.insert_trade_proposal(old)
    retained = repo.get_trade_proposal(old.id)
    assert repo.validate_trade_proposal_commit_hash(retained) == old.commit_hash
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_trade_proposal(proposal(0))
    repo.insert_trade_proposal(proposal(1))


@pytest.mark.real_db
def test_v86_upgrade_preserves_history_and_enforces_new_leg_identity(tmp_path, monkeypatch):
    """Upgrade nonempty production schema, including its immutable Plan triggers."""
    from aletheia.config import loader
    from aletheia.storage import migrations, sqlite as storage
    from tests.test_entry_plan_execution import _persist_waiting_plan

    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(migrations, "_is_pytest", lambda: False)
    registry = migrations._MIGRATIONS
    monkeypatch.setattr(migrations, "_MIGRATIONS", registry[:-1])
    storage.reset_db()
    try:
        migrations.apply_migrations()
        plan = _persist_waiting_plan()
        old = proposal(None).model_copy(update={"entry_plan_id": plan.plan_id})
        old.commit_hash = repo._compute_proposal_hash(old)
        repo._insert("trade_proposals", old.model_dump(exclude={"entry_leg_index"}))
        before_plan = repo.get_entry_plan(plan.plan_id)
        before_proposal = repo.get_trade_proposal(old.id)
        monkeypatch.setattr(migrations, "_MIGRATIONS", registry)
        db = storage.get_db()
        # An interruption after the Plan rebuild must roll back the Proposal DDL too.
        db.set_authorizer(lambda action, name, *_args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_INDEX and name == "idx_trade_proposals_entry_plan_leg"
            else sqlite3.SQLITE_OK)
        try:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                migrations.apply_migrations()
        finally:
            db.set_authorizer(None)
        assert repo.get_entry_plan(plan.plan_id) == before_plan
        assert repo.get_trade_proposal(old.id) == before_proposal
        assert "entry_leg_index" not in {row[1] for row in db.execute("PRAGMA table_info(trade_proposals)")}
        assert migrations.apply_migrations()
        assert repo.get_entry_plan(plan.plan_id) == before_plan
        after = repo.get_trade_proposal(old.id)
        assert after.pop("entry_leg_index") is None
        assert after == before_proposal
        assert repo.validate_trade_proposal_commit_hash(repo.get_trade_proposal(old.id)) == old.commit_hash
        assert not storage.get_db().execute("PRAGMA foreign_key_check").fetchall()
        assert not migrations.apply_migrations()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            repo.execute_write("UPDATE entry_plans SET plan_json='{}' WHERE id=?", (plan.plan_id,))
        with pytest.raises(sqlite3.IntegrityError):
            repo.insert_trade_proposal(proposal(0).model_copy(update={"entry_plan_id": plan.plan_id}))
        repo.insert_trade_proposal(proposal(1).model_copy(update={"entry_plan_id": plan.plan_id}))
        assert repo.transition_entry_plan(plan.plan_id, expected_status="waiting", status="active")
    finally:
        storage.reset_db()


def test_earlier_order_blocks_activation_refresh_and_pre_order_invalidation():
    from datetime import datetime, timezone
    from aletheia.agent_runtime.fast_trigger_watch import _invalidate_pre_order_entry_plan
    from aletheia.storage.models import Order
    from tests.test_entry_plan_execution import _persist_waiting_plan

    plan = _persist_waiting_plan()
    first = proposal(0).model_copy(update={"entry_plan_id": plan.plan_id})
    repo.insert_trade_proposal(first)
    repo.insert_trade_proposal(proposal(1).model_copy(update={"entry_plan_id": plan.plan_id}))
    order = Order(proposal_id=first.id, entry_plan_id=plan.plan_id, symbol=first.symbol,
        market_type=first.market_type, direction=first.direction, execution_mode=first.execution_mode,
        order_type="limit", price=100, quantity=1, status="submitted", source="adopted")
    repo.insert_order(order)
    assert repo.get_entry_plan(plan.plan_id)["order_id"] is None
    assert not repo.entry_plan_activation_can_refresh(plan.plan_id)
    assert _invalidate_pre_order_entry_plan(plan.plan_id, expected_status="waiting", reason="test_expiry",
        now=datetime.now(timezone.utc)) == "order_present"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"
