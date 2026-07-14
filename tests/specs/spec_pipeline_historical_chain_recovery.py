"""Fail-closed recovery for historical exchange-chain bookkeeping gaps.

These contracts cover two local inconsistencies only:

* a Bitget demo native entry already marked ``filled`` but missing its durable
  Fill/Position chain; and
* a Position already closed from verified broker fills while stale pending-close
  bookkeeping remains.

Recovery is read-only at the venue.  No test double permits order placement or
cancellation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

import pytest

from tests.execution_authority import seed_order_authority
from tests.specs.spec_meta import SpecClaim, spec_claim


_FILL_TIME_MS = 1_784_500_000_000
_FILL_TIME = datetime.fromtimestamp(
    _FILL_TIME_MS / 1000, tz=timezone.utc
).isoformat()


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    jsonl._last_audit_hash = None
    run_migrations()
    return conn


def _private_fill(
    order_id: str,
    *,
    fill_id: str = "REMOTE-HISTORICAL-FILL",
    quantity: float = 1.0,
    price: float = 60_100.0,
    fee: float | None = 0.2,
    filled_at_ms: int = _FILL_TIME_MS,
) -> dict:
    row = {
        "orderId": order_id,
        "tradeId": fill_id,
        "fillPrice": str(price),
        "fillQuantity": str(quantity),
        "feeCoin": "USDT",
        "cTime": str(filled_at_ms),
    }
    if fee is not None:
        row["fee"] = str(fee)
    return row


def _seed_entry(
    *,
    suffix: str,
    status: str = "filled",
    quantity: float = 1.0,
):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    proposal, decision = seed_order_authority(
        proposal_id=f"proposal-historical-{suffix}",
        risk_decision_id=f"risk-historical-{suffix}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=quantity,
        price=60_000.0,
        execution_mode="bitget_demo",
        leverage=5,
        stop_price=58_000.0,
        take_profit=64_000.0,
    )
    order = Order(
        id=f"order-historical-{suffix}",
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=quantity,
        price=proposal.price,
        execution_mode="bitget_demo",
        exchange_order_id=f"EX-HISTORICAL-{suffix}",
        status=status,
        filled_at=_FILL_TIME if status == "filled" else None,
    )
    repo.insert_order(order)
    return proposal, decision, order


def _patch_bitget_client(monkeypatch, rows, calls):
    from aletheia.exchanges.bitget import client as bitget_client

    class _ReadOnlyClient:
        def __init__(self, mode):
            calls.append(("client", mode))
            assert mode == "demo"

        async def get_order_fills(self, *, order_id, symbol, market_type):
            calls.append(("exact_fills", order_id, symbol, market_type))
            return [
                dict(row) for row in rows
                if str(row.get("orderId") or "") == str(order_id)
            ]

        async def get_open_orders(self, *, market_type="spot"):
            calls.append(("open_orders", market_type))
            return []

        async def get_fills(self, *, symbol=None, market_type="spot"):
            calls.append(("fills", symbol, market_type))
            return [dict(row) for row in rows]

        async def place_order(self, *args, **kwargs):
            raise AssertionError("historical recovery must never place an order")

        async def place_tpsl_order(self, *args, **kwargs):
            raise AssertionError("historical recovery must never place TPSL")

        async def cancel_order(self, *args, **kwargs):
            raise AssertionError("historical recovery must never cancel an order")

        async def cancel_plan_order(self, *args, **kwargs):
            raise AssertionError("historical recovery must never cancel TPSL")

    monkeypatch.setattr(bitget_client, "BitgetClient", _ReadOnlyClient)


@spec_claim(
    SpecClaim(
        "PL-HCR-01",
        "pipeline_historical_chain_recovery",
        "§1 exact filled-gap recovery",
        "L2b",
        "A Bitget demo native entry already marked filled is selected separately and "
        "recovers its missing Fill/Position chain only from exact complete private "
        "order fills; unrelated terminal Orders are never added to the normal scan.",
        status="implemented",
    )
)
def test_exact_filled_gap_recovers_without_scanning_all_terminal_orders(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation
    from aletheia.storage import repositories as repo

    _, _, order = _seed_entry(suffix="recover")
    # More than the historical repository list limit, all deliberately ineligible:
    # adopted rows are observations, never native authority to materialize.
    db.executemany(
        """
        INSERT INTO orders (
            id, symbol, market_type, direction, order_type, quantity, price,
            execution_mode, exchange_order_id, status, source, created_at
        ) VALUES (?, 'ETH/USDT', 'usdt_futures', 'long', 'market', 1, 3000,
                  'bitget_demo', ?, 'filled', 'adopted', ?)
        """,
        [
            (
                f"unrelated-terminal-{index}",
                f"EX-UNRELATED-{index}",
                f"2099-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
            )
            for index in range(501)
        ],
    )
    rows = [_private_fill(order.exchange_order_id)]
    calls = []
    _patch_bitget_client(monkeypatch, rows, calls)
    monkeypatch.setattr(reconciliation, "write_audit", lambda event: None)

    result = reconciliation.reconcile()

    stored_order = repo.get_order(order.id)
    fills = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (order.id,)
    ).fetchall()
    positions = db.execute(
        "SELECT * FROM positions WHERE order_id=?", (order.id,)
    ).fetchall()
    assert result["filled_gap_checked"] == 1
    assert result["filled_gap_recovered"] == 1
    assert result["checked"] == 1
    assert stored_order["status"] == "filled"
    assert len(fills) == 1
    assert fills[0]["fill_price"] == pytest.approx(60_100.0)
    assert fills[0]["fee"] == pytest.approx(0.2)
    assert fills[0]["fee_source"] == "reconciled"
    assert len(positions) == 1
    assert positions[0]["entry_fill_verified"] == 1
    assert positions[0]["entry_price_source"] == "broker_fill"
    assert calls == [
        ("client", "demo"),
        ("exact_fills", order.exchange_order_id, "BTCUSDT", "usdt_futures"),
    ]


@spec_claim(
    SpecClaim(
        "PL-HCR-02",
        "pipeline_historical_chain_recovery",
        "§2 authority and exposure fail closed",
        "L2c",
        "A filled historical gap with missing current authority or any compatible open "
        "Position remains inconclusive and is never grandfathered or averaged.",
        status="implemented",
    )
)
@pytest.mark.parametrize("conflict", ("missing_authority", "compatible_position"))
def test_filled_gap_rejects_missing_authority_or_compatible_open_position(
    tmp_path, monkeypatch, conflict
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    proposal, _, order = _seed_entry(suffix=conflict)
    if conflict == "missing_authority":
        db.execute(
            "UPDATE orders SET risk_decision_id=NULL WHERE id=?", (order.id,)
        )
    else:
        repo.insert_position(Position(
            id="compatible-existing-position",
            proposal_id=proposal.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            quantity=1.0,
            entry_price=59_000.0,
            current_price=59_000.0,
            execution_mode="bitget_demo",
            entry_fee=0.1,
            entry_fill_verified=True,
            entry_price_source="broker_fill",
        ))

    calls = []
    _patch_bitget_client(
        monkeypatch, [_private_fill(order.exchange_order_id)], calls
    )
    events = []
    monkeypatch.setattr(reconciliation, "write_audit", events.append)

    result = reconciliation.reconcile()

    assert result["filled_gap_recovered"] == 0
    assert result["filled_gap_inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "filled"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0
    assert not [call for call in calls if call[0] == "exact_fills"]
    expected_reason = {
        "missing_authority": "filled_gap_authority_missing",
        "compatible_position": "filled_gap_compatible_open_position",
    }[conflict]
    assert any(event.get("reason") == expected_reason for event in events)


@spec_claim(
    SpecClaim(
        "PL-HCR-03",
        "pipeline_historical_chain_recovery",
        "§3 complete private evidence",
        "L2c",
        "A historical filled gap remains untouched when exact private rows do not "
        "completely prove quantity, fee, and exchange fill time.",
        status="implemented",
    )
)
@pytest.mark.parametrize("defect", ("partial_quantity", "missing_fee", "missing_time"))
def test_filled_gap_rejects_incomplete_exact_private_evidence(
    tmp_path, monkeypatch, defect
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation
    from aletheia.storage import repositories as repo

    _, _, order = _seed_entry(suffix=defect)
    changes = {
        "partial_quantity": {"quantity": 0.4},
        "missing_fee": {"fee": None},
        "missing_time": {"filled_at_ms": 0},
    }[defect]
    rows = [_private_fill(order.exchange_order_id, **changes)]
    calls = []
    _patch_bitget_client(monkeypatch, rows, calls)
    monkeypatch.setattr(reconciliation, "write_audit", lambda event: None)

    result = reconciliation.reconcile()

    assert result["filled_gap_recovered"] == 0
    assert result["filled_gap_inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "filled"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0


def test_filled_gap_recovery_rolls_back_fill_when_position_write_fails(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import exchange_materialization, reconciliation
    from aletheia.storage import repositories as repo

    proposal, _, order = _seed_entry(suffix="atomic-rollback")
    calls = []
    _patch_bitget_client(
        monkeypatch,
        [_private_fill(order.exchange_order_id, fill_id="REMOTE-ROLLBACK")],
        calls,
    )
    monkeypatch.setattr(reconciliation, "write_audit", lambda event: None)

    def _fail_position_write(position):
        raise RuntimeError("injected Position write failure")

    monkeypatch.setattr(
        exchange_materialization.repo,
        "insert_position",
        _fail_position_write,
    )

    result = reconciliation.reconcile()

    assert result["filled_gap_recovered"] == 0
    assert result["filled_gap_inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "filled"
    assert repo.get_trade_proposal(proposal.id)["status"] == "approved"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0


def _seed_closed_pending(*, suffix: str):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position

    proposal, decision = seed_order_authority(
        proposal_id=f"proposal-closed-pending-{suffix}",
        risk_decision_id=f"risk-closed-pending-{suffix}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=60_000.0,
        execution_mode="bitget_demo",
        leverage=5,
    )
    entry_order = Order(
        id=f"entry-closed-pending-{suffix}",
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=1.0,
        price=proposal.price,
        execution_mode="bitget_demo",
        exchange_order_id=f"EX-ENTRY-CLOSED-{suffix}",
        status="filled",
        filled_at="2026-07-13T00:00:00+00:00",
    )
    repo.insert_order(entry_order)
    repo.insert_fill(Fill(
        id=f"private-fill:bitget_demo:ENTRY-{suffix}",
        order_id=entry_order.id,
        fill_price=60_000.0,
        fill_quantity=1.0,
        fee=0.1,
        fee_source="reconciled",
        filled_at="2026-07-13T00:00:00+00:00",
    ))
    position = Position(
        id=f"position-closed-pending-{suffix}",
        proposal_id=proposal.id,
        order_id=entry_order.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        quantity=1.0,
        entry_price=60_000.0,
        current_price=60_100.0,
        execution_mode="bitget_demo",
        entry_fee=0.1,
        entry_fill_verified=True,
        entry_price_source="broker_fill",
        opened_at="2026-07-13T00:00:00+00:00",
    )
    repo.insert_position(position)
    repo.update_order(entry_order.id, position_id=position.id)
    close_order = Order(
        id=f"close-closed-pending-{suffix}",
        proposal_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type="market",
        quantity=1.0,
        price=60_100.0,
        execution_mode="bitget_demo",
        exchange_order_id=f"EX-CLOSE-CLOSED-{suffix}",
        client_order_id=f"close-{position.id}",
        position_id=position.id,
        close_reason="take_profit",
        status="filled",
        filled_at=_FILL_TIME,
    )
    repo.insert_order(close_order)
    durable_fill_id = f"private-fill:bitget_demo:CLOSE-{suffix}"
    repo.insert_fill(Fill(
        id=durable_fill_id,
        order_id=close_order.id,
        fill_price=60_100.0,
        fill_quantity=1.0,
        fee=0.2,
        fee_currency="USDT",
        fee_source="reconciled",
        filled_at=_FILL_TIME,
    ))
    evidence = {
        "source": "bitget_private_close_fills",
        "source_endpoint": "private_fills",
        "broker_id": "bitget_demo",
        "account_environment": "demo",
        "execution_mode": "bitget_demo",
        "exchange_order_id": close_order.exchange_order_id,
        "client_order_id": close_order.client_order_id,
        "fill_ids": [f"CLOSE-{suffix}"],
        "durable_fill_ids": [durable_fill_id],
        "fill_quantity": 1.0,
        "fee_in_quote": 0.2,
        "fills": [{
            "fill_id": f"CLOSE-{suffix}",
            "remote_fill_id": f"CLOSE-{suffix}",
            "durable_fill_id": durable_fill_id,
            "price": 60_100.0,
            "quantity": 1.0,
            "fee": 0.2,
            "fee_currency": "USDT",
            "filled_at": _FILL_TIME,
        }],
    }
    repo.update_position(
        position.id,
        status="closed",
        close_reason="take_profit",
        closed_at=_FILL_TIME,
        current_price=60_100.0,
        realized_pnl=99.7,
        exit_fee=0.2,
        exit_fill_verified=True,
        exit_price_source="broker_fill",
        close_category="strategy",
        reconciliation_evidence=json.dumps(evidence, sort_keys=True),
        pending_close_order_id=close_order.id,
        pending_close_status="materialization_missing",
        pending_close_requested_at="2026-07-13T00:01:00+00:00",
    )
    return position, close_order


def test_entry_gap_selector_excludes_durable_close_identity(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position, close_order = _seed_closed_pending(suffix="close-identity")
    # Model the exact corruption the entry recovery must not reinterpret: an old
    # close lost close_reason and its local Fill/pending marker, but retains the
    # broker's durable close-{position_id} identity and Position backlink.
    db.execute("DELETE FROM fills WHERE order_id=?", (close_order.id,))
    db.execute(
        "UPDATE orders SET close_reason=NULL WHERE id=?", (close_order.id,)
    )
    repo.update_position(
        position.id,
        pending_close_order_id=None,
        pending_close_status=None,
        pending_close_requested_at=None,
    )

    class _NoVenueClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a historical close must not enter entry recovery")

    monkeypatch.setattr(bitget_client, "BitgetClient", _NoVenueClient)
    monkeypatch.setattr(reconciliation, "write_audit", lambda event: None)

    result = reconciliation.reconcile()

    assert result["filled_gap_checked"] == 0
    assert result["pending_close_cleared"] == 0
    assert repo.get_order(close_order.id)["status"] == "filled"


@spec_claim(
    SpecClaim(
        "PL-HCR-04",
        "pipeline_historical_chain_recovery",
        "§4 stale pending-close cleanup",
        "L2b",
        "A verified closed Bitget demo Position clears only stale pending-close fields "
        "when its filled close Order, bidirectional link, exact local Fill quantity, "
        "broker price source, and private evidence all agree.",
        status="implemented",
    )
)
def test_verified_closed_position_clears_stale_pending_locally(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position, close_order = _seed_closed_pending(suffix="clear")

    class _NoVenueClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("local pending cleanup must not construct a venue client")

    monkeypatch.setattr(bitget_client, "BitgetClient", _NoVenueClient)
    monkeypatch.setattr(reconciliation, "write_audit", lambda event: None)

    first = reconciliation.reconcile()
    stored = repo._select_one("positions", position.id)

    assert first["pending_close_cleared"] == 1
    assert first["pending_close_cleared_ids"] == [position.id]
    assert stored["status"] == "closed"
    assert stored["pending_close_order_id"] is None
    assert stored["pending_close_status"] is None
    assert stored["pending_close_requested_at"] is None
    assert repo.get_order(close_order.id)["status"] == "filled"

    second = reconciliation.reconcile()
    assert second["pending_close_cleared"] == 0


@spec_claim(
    SpecClaim(
        "PL-HCR-06",
        "pipeline_historical_chain_recovery",
        "§6 durable pending cleanup audit",
        "L2c",
        "Verified pending-close cleanup and its authoritative audit commit together; "
        "a secondary JSONL sink outage cannot turn a committed cleanup into a retry.",
        status="implemented",
    )
)
def test_closed_pending_cleanup_survives_jsonl_audit_outage(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position, _ = _seed_closed_pending(suffix="audit-outage")

    class _NoVenueClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("local pending cleanup must not construct a venue client")

    monkeypatch.setattr(bitget_client, "BitgetClient", _NoVenueClient)
    monkeypatch.setattr(
        reconciliation,
        "write_audit",
        lambda event: (_ for _ in ()).throw(OSError("audit sink unavailable")),
    )

    result = reconciliation.reconcile()

    assert result["pending_close_cleared_ids"] == [position.id]
    assert repo._select_one("positions", position.id)["pending_close_order_id"] is None
    assert any(
        event["event_type"] == "position_pending_close_reconciled"
        and event["entity_id"] == position.id
        for event in repo.list_audit_events(limit=20)
    )


@pytest.mark.parametrize(
    "defect",
    (
        "missing_backlink",
        "quantity_mismatch",
        "unverified",
        "wrong_price_source",
        "incomplete_evidence",
        "fee_summary_mismatch",
    ),
)
def test_closed_pending_cleanup_rejects_incomplete_local_chain(
    tmp_path, monkeypatch, defect
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position, close_order = _seed_closed_pending(suffix=defect)
    if defect == "missing_backlink":
        repo.update_order(close_order.id, position_id=None)
    elif defect == "quantity_mismatch":
        repo.update_order(close_order.id, quantity=0.5)
    elif defect == "unverified":
        repo.update_position(position.id, exit_fill_verified=False)
    elif defect == "wrong_price_source":
        repo.update_position(position.id, exit_price_source="unavailable")
    elif defect == "incomplete_evidence":
        stored = repo._select_one("positions", position.id)
        evidence = json.loads(stored["reconciliation_evidence"])
        evidence.pop("fills")
        repo.update_position(
            position.id,
            reconciliation_evidence=json.dumps(evidence, sort_keys=True),
        )
    else:
        # Keep the Position and evidence summary mutually consistent while the
        # detailed private Fill still proves the real 0.2 USDT fee. Cleanup must
        # derive the summary from those details, not trust two matching totals.
        stored = repo._select_one("positions", position.id)
        evidence = json.loads(stored["reconciliation_evidence"])
        evidence["fee_in_quote"] = 0.3
        repo.update_position(
            position.id,
            exit_fee=0.3,
            reconciliation_evidence=json.dumps(evidence, sort_keys=True),
        )

    class _NoVenueClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("rejected local cleanup must not access the venue")

    monkeypatch.setattr(bitget_client, "BitgetClient", _NoVenueClient)
    monkeypatch.setattr(reconciliation, "write_audit", lambda event: None)

    result = reconciliation.reconcile()
    stored = repo._select_one("positions", position.id)

    assert result["pending_close_cleared"] == 0
    assert stored["pending_close_order_id"] == close_order.id
    assert stored["pending_close_status"] == "materialization_missing"


@spec_claim(
    SpecClaim(
        "PL-HCR-05",
        "pipeline_historical_chain_recovery",
        "§5 real partial replay",
        "L2c",
        "Replaying the same partial private evidence through reconciliation invokes the "
        "materializer again but creates no duplicate Fill and does not change Position "
        "quantity or fees.",
        status="implemented",
    )
)
def test_same_partial_private_evidence_replays_through_materializer_twice(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation
    from aletheia.storage import repositories as repo

    _, _, order = _seed_entry(
        suffix="partial-replay", status="materialization_missing", quantity=1.0
    )
    rows = [_private_fill(
        order.exchange_order_id,
        fill_id="REMOTE-PARTIAL-REPLAY",
        quantity=0.4,
        fee=0.08,
    )]
    calls = []
    _patch_bitget_client(monkeypatch, rows, calls)
    monkeypatch.setattr(reconciliation, "write_audit", lambda event: None)

    first = reconciliation.reconcile()
    first_position = repo._select_one(
        "positions", repo.get_order(order.id)["position_id"]
    )
    second = reconciliation.reconcile()
    second_position = repo._select_one("positions", first_position["id"])
    fills = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (order.id,)
    ).fetchall()

    assert first["partially_filled"] == 1
    assert first["materialized_fills"] == 1
    assert second["partially_filled"] == 1
    assert second["materialized_fills"] == 0
    assert repo.get_order(order.id)["status"] == "partially_filled"
    assert len(fills) == 1
    assert fills[0]["fill_quantity"] == pytest.approx(0.4)
    assert fills[0]["fee"] == pytest.approx(0.08)
    assert first_position["quantity"] == pytest.approx(0.4)
    assert second_position["quantity"] == pytest.approx(0.4)
    assert first_position["entry_fee"] == pytest.approx(0.08)
    assert second_position["entry_fee"] == pytest.approx(0.08)
    assert len([call for call in calls if call[0] == "exact_fills"]) == 2
