"""Regression tests for #1181: non-finite capital event amounts must be rejected."""

import math
import pytest
from pydantic import ValidationError

from aletheia.storage.models import CapitalEvent
from aletheia.server.routes.capital import CapitalEventRequest


@pytest.mark.parametrize("amount", [float("inf"), float("-inf"), float("nan")])
def test_capital_event_model_rejects_non_finite(amount):
    with pytest.raises(ValidationError):
        CapitalEvent(broker_id="b", event_type="deposit", amount_usdt=amount)


@pytest.mark.parametrize("amount", [0.0, -1.0, -100.0])
def test_capital_event_model_rejects_non_positive(amount):
    with pytest.raises(ValidationError):
        CapitalEvent(broker_id="b", event_type="deposit", amount_usdt=amount)


def test_capital_event_model_accepts_valid():
    e = CapitalEvent(broker_id="b", event_type="deposit", amount_usdt=100.0)
    assert e.amount_usdt == 100.0


@pytest.mark.parametrize("amount", [float("inf"), float("-inf"), float("nan")])
def test_request_model_rejects_non_finite(amount):
    with pytest.raises(ValidationError):
        CapitalEventRequest(broker_id="b", event_type="deposit", amount_usdt=amount)


@pytest.mark.parametrize("amount", [0.0, -1.0])
def test_request_model_rejects_non_positive(amount):
    with pytest.raises(ValidationError):
        CapitalEventRequest(broker_id="b", event_type="deposit", amount_usdt=amount)


def test_request_model_accepts_valid():
    r = CapitalEventRequest(broker_id="b", event_type="deposit", amount_usdt=50.0)
    assert r.amount_usdt == 50.0


def test_insert_capital_event_rejects_non_finite(tmp_path, monkeypatch):
    import sqlite3
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sqlite._db_conn = conn
    run_migrations()

    # Use model_construct to bypass Pydantic validation, simulating a non-HTTP caller
    bad = CapitalEvent.model_construct(
        id="poison", broker_id="b", event_type="deposit",
        amount_usdt=float("inf"), equity_at_event=None,
        detected_at="2026-01-01T00:00:00+00:00", note="",
    )
    with pytest.raises(ValueError, match="finite"):
        repo.insert_capital_event(bad)


def test_get_net_deposits_excludes_historical_non_finite(tmp_path, monkeypatch):
    import sqlite3
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sqlite._db_conn = conn
    run_migrations()

    repo.insert_capital_event(CapitalEvent(broker_id="b", event_type="deposit", amount_usdt=100.0))
    # Directly insert a non-finite row to simulate historical pollution
    conn.execute(
        "INSERT INTO capital_events (id, broker_id, event_type, amount_usdt, detected_at, note)"
        " VALUES ('poison', 'b', 'deposit', 1e999, '2026-01-01T00:00:00+00:00', 'legacy')"
    )
    conn.commit()

    net = repo.get_net_deposits()
    assert math.isfinite(net)
    assert net == pytest.approx(100.0)


def test_concurrent_first_syncs_record_one_initial_deposit(monkeypatch):
    """#1555: two first syncs must not both pass the 'no event yet' check and insert."""
    import threading
    from types import SimpleNamespace

    import aletheia.storage.models as models
    from aletheia.portfolio import exchange_sync
    from aletheia.storage.sqlite import get_db

    account = {
        "broker_id": "bitget_demo", "available_usdt": 100.0, "equity_usdt": 100.0,
        "account_completeness": "complete", "position_completeness": "complete",
    }
    unified = SimpleNamespace(
        total_equity_usdt=100.0, total_available_usdt=100.0, total_unrealized_pnl=0.0,
        accounts=[account], positions=[], errors=[], connected_brokers=["bitget_demo"],
        fetched_at="2026-10-09T00:00:00+00:00", execution_modes=("bitget_demo",),
    )
    monkeypatch.setattr("aletheia.uta.manager.get_unified_state_sync", lambda **_: unified)
    monkeypatch.setattr(exchange_sync, "_store_unified", lambda _payload: None)
    # Both syncs build their event only after reading the table, then insert together.
    both_built = threading.Barrier(2, timeout=10)

    class RacingCapitalEvent(CapitalEvent):
        def __init__(self, **data):
            super().__init__(**data)
            both_built.wait()

    monkeypatch.setattr(models, "CapitalEvent", RacingCapitalEvent)
    syncs = [threading.Thread(target=exchange_sync.sync_all_exchanges) for _ in range(2)]
    for sync in syncs:
        sync.start()
    for sync in syncs:
        sync.join(timeout=20)

    rows = get_db().execute(
        "SELECT amount_usdt, note FROM capital_events WHERE broker_id = 'bitget_demo'"
    ).fetchall()
    assert [tuple(row) for row in rows] == [(100.0, "auto:initial_sync")]


def test_first_capital_event_is_recorded_once_per_broker():
    from aletheia.storage import repositories as repo

    event = CapitalEvent(broker_id="okx_demo", event_type="deposit", amount_usdt=50.0)
    assert repo.insert_first_capital_event(event) is True
    assert repo.insert_first_capital_event(
        CapitalEvent(broker_id="okx_demo", event_type="deposit", amount_usdt=50.0)
    ) is False
    assert repo.get_net_deposits() == 50.0
