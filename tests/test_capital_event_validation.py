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
