from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from aletheia.gateway import locks
from aletheia.storage import sqlite as sqlite_store
from aletheia.storage.migrations import run_migrations


class _CoordinatedConnection:
    def __init__(self, connection, barrier):
        self._connection = connection
        self._barrier = barrier

    def execute(self, sql, parameters=()):
        cursor = self._connection.execute(sql, parameters)
        if (
            sql.startswith("SELECT * FROM ps_gateway_locks")
            and not self._connection.in_transaction
        ):
            self._barrier.wait(timeout=5)
        return cursor

    def __getattr__(self, name):
        return getattr(self._connection, name)


@pytest.mark.real_db
@pytest.mark.parametrize("existing_expired_lease", [False, True])
def test_gateway_lease_claim_has_exactly_one_winner(
    tmp_path, monkeypatch, existing_expired_lease,
):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite_store.reset_db()
    run_migrations()
    db = sqlite_store.get_db()
    if existing_expired_lease:
        db.execute(
            "INSERT INTO ps_gateway_locks "
            "(lock_name, owner_id, lease_until, heartbeat_at) VALUES (?, ?, ?, ?)",
            ("live_gateway", "expired", "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
        )

    barrier = threading.Barrier(2)
    real_get_db = locks.get_db

    def coordinated_get_db():
        return _CoordinatedConnection(real_get_db(), barrier)

    monkeypatch.setattr(locks, "get_db", coordinated_get_db)
    service = locks.GatewayLockService()
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(
            pool.map(
                lambda owner: (owner, service.acquire("live_gateway", owner)),
                ("owner-a", "owner-b"),
            )
        )

    winners = [owner for owner, acquired in attempts if acquired]
    assert len(winners) == 1
    row = db.execute(
        "SELECT owner_id FROM ps_gateway_locks WHERE lock_name='live_gateway'"
    ).fetchone()
    assert row and row["owner_id"] == winners[0]
    sqlite_store.reset_db()
