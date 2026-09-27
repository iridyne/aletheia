"""Close clientOrderId uniqueness across retries (execution_contract close-clientOid clause).

A close claim must never reuse a ``client_order_id`` already used by a prior
close attempt on the same position. ``_claim_close`` delegates allocation to
``pending_close_recovery.next_close_client_oid``: ``close-{pid}`` when no prior
attempt exists, ``close-{pid}-r{N+1}`` otherwise. Each test asserts the
resulting order's ``client_order_id`` string, not internal state.
"""

from __future__ import annotations

import sqlite3

from aletheia.uta.models import UTAAccountIdentity


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    monkeypatch.setattr(sq, "_db_conn", None)
    conn = sqlite3.connect(
        str(tmp_path / "app.db"), check_same_thread=False, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()
    return conn


def _identity(mode: str = "binance_testnet") -> UTAAccountIdentity:
    return UTAAccountIdentity(
        account_id=mode,
        execution_mode=mode,
        venue="binance",
        environment="testnet",
        profile=mode,
        credential_generation=1,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
    )


def _bind_open_position(position_id: str, *, mode: str = "binance_testnet") -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Position

    entry = Order(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=100.0,
        execution_mode=mode,
        status="filled",
        source="adopted",
        uta_account_identity_json=_identity(mode).model_dump_json(),
    )
    repo.insert_order(entry)
    repo.insert_position(
        Position(
            id=position_id,
            order_id=entry.id,
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=100.0,
            execution_mode=mode,
            status="open",
            source="native",
        )
    )


def _seed_close_order(
    *, order_id: str, position_id: str, client_order_id: str, created_at: str
) -> None:
    """Seed a historical close order (bypasses authority validation, like
    tests/test_pending_close_recovery.py ``_seed_order``)."""
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    order = Order(
        id=order_id,
        client_order_id=client_order_id,
        position_id=position_id,
        close_reason="stop_loss",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=100.0,
        execution_mode="binance_testnet",
        status="submission_rejected",
        source="native",
        created_at=created_at,
    )
    repo._insert("orders", repo._stamp_provenance(order.model_dump()))


def _claim(position_id: str):
    from aletheia.execution.coordinator import _claim_close

    _row, order = _claim_close(position_id, "stop_loss", None)
    assert order is not None
    return order


def test_first_close_uses_no_retry_suffix(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _bind_open_position("pid-1")

    assert _claim("pid-1").client_order_id == "close-pid-1"


def test_second_close_after_rejected_uses_r1(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _bind_open_position("pid-1")
    _seed_close_order(
        order_id="c1",
        position_id="pid-1",
        client_order_id="close-pid-1",
        created_at="2026-08-22T12:00:00+00:00",
    )

    assert _claim("pid-1").client_order_id == "close-pid-1-r1"


def test_third_close_uses_r2(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _bind_open_position("pid-1")
    _seed_close_order(
        order_id="c1",
        position_id="pid-1",
        client_order_id="close-pid-1",
        created_at="2026-08-22T12:00:00+00:00",
    )
    _seed_close_order(
        order_id="c2",
        position_id="pid-1",
        client_order_id="close-pid-1-r1",
        created_at="2026-08-22T13:00:00+00:00",
    )

    assert _claim("pid-1").client_order_id == "close-pid-1-r2"


def test_non_sequential_gap_still_picks_max_plus_one(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _bind_open_position("pid-1")
    _seed_close_order(
        order_id="c3",
        position_id="pid-1",
        client_order_id="close-pid-1-r3",
        created_at="2026-08-22T12:00:00+00:00",
    )

    assert _claim("pid-1").client_order_id == "close-pid-1-r4"


def test_unrelated_position_ids_do_not_interfere(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _bind_open_position("pid-1")
    _seed_close_order(
        order_id="c-other",
        position_id="other-pid",
        client_order_id="close-other-pid-r5",
        created_at="2026-08-22T12:00:00+00:00",
    )

    assert _claim("pid-1").client_order_id == "close-pid-1"


def test_helper_ignores_case_mismatched_ids(tmp_path, monkeypatch):
    """A case-mismatched id is not part of the ``close-{pid}`` series.

    ``next_close_client_oid`` compares against its own lowercase prefix exactly,
    so ``CLOSE-pid-1-r1`` does not read as this position's ``close-pid-1`` base
    or ``close-pid-1-rN`` retry; the conservative fallthrough allocates a
    case-distinct ``close-pid-1-r1``. The allocated id never equals the seeded
    uppercase id, so there is no collision.
    """
    _setup_db(tmp_path, monkeypatch)
    _bind_open_position("pid-1")
    _seed_close_order(
        order_id="c-upper",
        position_id="pid-1",
        client_order_id="CLOSE-pid-1-r1",
        created_at="2026-08-22T12:00:00+00:00",
    )

    claim = _claim("pid-1")
    assert claim.client_order_id == "close-pid-1-r1"
    assert claim.client_order_id != "CLOSE-pid-1-r1"
