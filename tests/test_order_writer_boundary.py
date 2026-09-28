"""Named Order writers and literal SQL stay within declared Core owners."""

from pathlib import Path

import pytest

from tests.helpers.authority_writer_checks import assert_writer_boundary


_OWNERS = {
    "insert_order": {"execution/coordinator.py", "execution/exchange_materialization.py", "portfolio/position_reconciler.py"},
    "transition_order": {"execution/coordinator.py", "execution/exchange_materialization.py", "execution/reconciliation.py", "execution/gateway.py", "execution/pending_close_recovery.py", "execution/stale_order_cleanup.py"},
    "update_order_metadata": {"execution/coordinator.py", "execution/exchange_materialization.py", "execution/reconciliation.py"},
    "replace_order_error_reason": {"execution/pending_close_recovery.py"},
    "finalize_exchange_acknowledgement": {"execution/coordinator.py"},
}
_RAW_OWNERS = {"insert_order", "transition_order", "update_order_metadata", "replace_order_error_reason"}


def _assert_order_writer_boundary(root):
    return assert_writer_boundary(
        root, table="orders", owners=_OWNERS, raw_owners=_RAW_OWNERS,
        delegations={("finalize_exchange_acknowledgement", "transition_order")},
    )


def test_order_writer_ownership():
    scanned = _assert_order_writer_boundary(Path(__file__).resolve().parents[1])
    assert scanned > 0, "order-writer boundary scan must visit repository files"


@pytest.mark.parametrize("directory", ["core/aletheia", "extensions", "legacy", "ops", "scripts"])
@pytest.mark.parametrize("source", [
    "from aletheia.storage.repositories import insert_order as persist\npersist(row)",
    "persist = repo.transition_order\npersist(order_id, status='filled')",
    "repo._insert(table='orders', data=row)",
    "repo._update('orders', order_id, {'status': 'filled'})",
    "from aletheia.storage.repositories import _insert as persist\npersist('orders', row)",
    "persist = repo._update\npersist(table='orders', rid=order_id, data=row)",
    "repo._update('ORDERS', order_id, row)",
    "repo._insert('\"orders\"', row)",
    "from aletheia.execution.coordinator import insert_order as persist\npersist(row)",
    "db.execute('UPDATE main.\"orders\" SET status=?')",
    "db.execute('DELETE FROM [orders]')",
    "db.execute('INSERT OR REPLACE INTO `orders` VALUES (?)')",
    "db.execute('UPDATE OR IGNORE orders SET status=?')",
    'db.execute("UPDATE \'orders\' SET status=?")',
    "db.execute('UPDATE /* bypass */ orders SET status=?')",
    "db.execute('INSERT /* bypass */ INTO orders DEFAULT VALUES')",
    "db.execute('UPDATE -- bypass\\norders SET status=?')",
    "persist: Callable = repo._update\npersist('orders', order_id, row)",
    "db.execute('DROP TABLE orders')",
    "db.execute('ALTER TABLE orders ADD COLUMN bad TEXT')",
])
def test_order_writer_guard_rejects_other_owners(tmp_path, directory, source):
    target = tmp_path / directory / "other.py"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_order_writer_boundary(tmp_path)


@pytest.mark.parametrize("source", [
    "def rewrite(row):\n    _insert('orders', row)",
    "def rewrite(row):\n    db.execute('UPDATE orders SET status=?')",
    "def rewrite(row):\n    transition_order(row['id'], status='filled')",
    "class Other:\n    def insert_order(self, row):\n        _insert('orders', row)",
    "def insert_order(row):\n    pass\ndef insert_order(row):\n    _insert('orders', row)",
    "def other():\n    def insert_order(row):\n        _insert('orders', row)",
    "def insert_order(seed=_insert('orders', row)):\n    pass",
    "@decorate(_insert('orders', row))\ndef insert_order(row):\n    pass",
    "def insert_order(row):\n    db.execute('DROP TABLE orders')",
])
def test_new_repository_wrapper_is_not_an_order_owner(tmp_path, source):
    target = tmp_path / "core/aletheia/storage/repositories.py"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_order_writer_boundary(tmp_path)


def test_order_guard_accepts_canonical_owner_and_delegation(tmp_path):
    sources = {
        "core/aletheia/execution/coordinator.py": "def submit(row):\n    from ..storage.repositories import insert_order as persist\n    persist(row)",
        "core/aletheia/storage/repositories.py": "def insert_order(row):\n    _insert('orders', row)\ndef finalize_exchange_acknowledgement(row):\n    transition_order(row['id'], status='submitted')",
        "core/aletheia/storage/migrations.py": "db.execute('UPDATE [orders] SET status=?')\ndb.execute('ALTER TABLE orders ADD COLUMN example TEXT')\ndb.execute('DROP TABLE IF EXISTS orders')",
    }
    for path, source in sources.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    scanned = _assert_order_writer_boundary(tmp_path)
    assert scanned >= len(sources), (
        f"guard must visit every fixture source file (scanned {scanned})"
    )
