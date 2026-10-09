"""Proposal persistence must preserve immutable content and declared owners."""

import pytest
from pathlib import Path


def _assert_proposal_writer_boundary(root):
    from tests.helpers.authority_writer_checks import assert_writer_boundary

    return assert_writer_boundary(
        root, table="trade_proposals",
        owners={
            "insert_trade_proposal": {
                "execution/gateway.py", "execution/entry_plan_execution.py",
                "portfolio/position_reconciler.py",
            },
            # execution_contract.md §6.1: stale_order_cleanup moves an in-flight Proposal to error.
            "update_trade_proposal": {
                "execution/gateway.py", "execution/exchange_materialization.py",
                "execution/stale_order_cleanup.py", "risk/engine.py", "server/routes/data.py",
            },
        },
        raw_owners={"insert_trade_proposal", "update_trade_proposal"},
    )


def test_proposal_writer_owners():
    scanned = _assert_proposal_writer_boundary(Path(__file__).resolve().parents[1])
    assert scanned > 0, "proposal-writer boundary scan must visit repository files"


@pytest.mark.parametrize("path,source", [
    ("extensions/other.py", "repo.insert_trade_proposal(proposal)"),
    ("core/aletheia/other.py", "from .storage.repositories import update_trade_proposal as update\nupdate('id', status='executed')"),
    ("core/aletheia/other.py", "writer = repo.insert_trade_proposal"),
    ("legacy/other.py", "repo._insert(table='trade_proposals', data=row)"),
    ("ops/other.py", "db.execute('UPDATE /* hidden */ main.[trade_proposals] SET quantity=2')"),
    ("scripts/other.py", "db.execute('DROP TABLE trade_proposals')"),
    ("core/aletheia/storage/repositories.py", "def other():\n    _update('trade_proposals', 'id', row)"),
    ("core/aletheia/storage/repositories.py", "def outer():\n    def insert_trade_proposal(row):\n        _insert('trade_proposals', row)"),
])
def test_proposal_guard_rejects_undeclared_writes(tmp_path, path, source):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_proposal_writer_boundary(tmp_path)


@pytest.mark.parametrize("field", ["quantity", "worst_price", "order_tactic", "commit_hash", "entry_leg_index"])
def test_proposal_update_rejects_immutable_fields_before_writing(monkeypatch, field):
    from aletheia.storage import repositories as repo

    writes = []
    monkeypatch.setattr(repo, "_update", lambda *args: writes.append(args))
    with pytest.raises(ValueError, match="immutable Proposal"):
        repo.update_trade_proposal("proposal", status="rejected", **{field: None})
    assert writes == []


def test_proposal_update_preserves_status_only_api(monkeypatch):
    from aletheia.storage import repositories as repo

    writes = []
    monkeypatch.setattr(repo, "_update", lambda *args: writes.append(args))
    repo.update_trade_proposal("proposal", status="rejected")
    assert writes == [("trade_proposals", "proposal", {"status": "rejected"})]


def test_rejected_content_update_preserves_durable_hash_and_status():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT", direction="long", quantity=1, price=100,
        execution_mode="bitget_demo", market_type="spot",
    )
    repo.insert_trade_proposal(proposal)
    before = repo.get_trade_proposal(proposal.id)
    with pytest.raises(ValueError, match="immutable Proposal"):
        repo.update_trade_proposal(proposal.id, status="rejected", quantity=2)
    assert repo.get_trade_proposal(proposal.id) == before
    repo.update_trade_proposal(proposal.id, status="rejected")
    after = repo.get_trade_proposal(proposal.id)
    assert after == {**before, "status": "rejected"}
    assert repo.validate_trade_proposal_commit_hash(after) == before["commit_hash"]
