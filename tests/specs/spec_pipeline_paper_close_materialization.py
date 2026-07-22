"""Local-paper close execution and atomic materialization contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


SLIPPAGE_BPS = 10.0
FEE_RATE = 0.0006


@pytest.fixture
def anyio_backend():
    return "asyncio"


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


def _config():
    return SimpleNamespace(
        trading=SimpleNamespace(
            slippage_bps=SLIPPAGE_BPS,
            execution_fee_pct=FEE_RATE * 100.0,
            default_stop_loss_pct=2.0,
            default_take_profit_pct=4.0,
            max_position_hold_hours=0,
            trail_enabled=False,
            exchange_tpsl_enabled=False,
            market_aware_exits=SimpleNamespace(enabled=False),
        ),
        serenity=SimpleNamespace(max_hold_days=0),
        exchange=SimpleNamespace(paper_execution_mode="paper_internal"),
        market_data=SimpleNamespace(ccxt_exchange="binance"),
    )


def _patch_config(monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.portfolio import position_manager

    cfg = _config()
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(position_manager, "load_config", lambda: cfg)
    return cfg


def _seed_open_position(
    *,
    direction: str,
    stop_price: float,
    take_profit_price: float,
    symbol: str = "BTC/USDT",
    market_type: str = "spot",
):
    from aletheia.portfolio.paper_account import record_position_opened
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position

    entry_order = Order(
        source="adopted",
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        status="filled",
    )
    repo.insert_order(entry_order)
    repo.insert_fill(Fill(
        order_id=entry_order.id,
        fill_price=100.0,
        fill_quantity=1.0,
        fee=100.0 * FEE_RATE,
    ))
    position = Position(
        symbol=entry_order.symbol,
        market_type=entry_order.market_type,
        direction=entry_order.direction,
        quantity=entry_order.quantity,
        entry_price=100.0,
        current_price=100.0,
        execution_mode=entry_order.execution_mode,
        order_id=entry_order.id,
        entry_fee=100.0 * FEE_RATE,
        entry_price_source="local_fill",
        entry_fill_verified=True,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
    )
    repo.insert_position(position)
    record_position_opened(position.id)
    return entry_order, position


def _seed_market_snapshot(
    *,
    symbol: str,
    price: float,
    market_type: str = "spot",
    exchange: str = "binance",
    snapshot_at: str | None = None,
):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot

    repo.insert_market_snapshot(MarketSnapshot(
        symbol=symbol,
        market_type=market_type,
        exchange=exchange,
        price=price,
        snapshot_at=snapshot_at or datetime.now(timezone.utc).isoformat(),
    ))


def _close_chain(conn, position_id: str):
    order = conn.execute(
        "SELECT * FROM orders WHERE position_id=? ORDER BY rowid",
        (position_id,),
    ).fetchone()
    fill = None
    if order is not None:
        fill = conn.execute(
            "SELECT * FROM fills WHERE order_id=? ORDER BY rowid",
            (order["id"],),
        ).fetchone()
    return order, fill


@spec_claim(
    SpecClaim(
        "PL-PCM-00",
        "pipeline_paper_close_materialization",
        "§0 authoritative local entry evidence",
        "L2b",
        "A Position opened from a committed paper Fill explicitly records that its "
        "entry price is backed by the local-fill authority.",
            status="implemented",
    )
)
def test_paper_entry_stamps_local_fill_authority(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.storage import repositories as repo
    from tests.execution_authority import seed_order_authority

    seed_order_authority(
        proposal_id="proposal-paper-entry-proof",
        risk_decision_id="risk-paper-entry-proof",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )
    opened = execute_paper_order(
        proposal_id="proposal-paper-entry-proof",
        risk_decision_id="risk-paper-entry-proof",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )

    position = repo._select_one("positions", opened["position_id"])
    order = repo.get_order(opened["order_id"])
    assert position["entry_price_source"] == "local_fill"
    assert position["entry_fill_verified"] == 1
    assert order["position_id"] == position["id"]


@spec_claim(
    SpecClaim(
        "PL-PCM-03",
        "pipeline_paper_close_materialization",
        "§0 atomic local entry",
        "L2c",
        "A paper entry commits Order, Fill, Position, and the local account event as "
        "one fact; a ledger failure leaves none of the execution projection behind.",
        status="implemented",
    )
)
def test_paper_entry_rolls_back_execution_chain_when_ledger_write_fails(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.portfolio import paper_account
    from tests.execution_authority import seed_order_authority

    seed_order_authority(
        proposal_id="proposal-paper-entry-atomic",
        risk_decision_id="risk-paper-entry-atomic",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )
    monkeypatch.setattr(
        paper_account,
        "record_position_opened",
        lambda _position_id: (_ for _ in ()).throw(
            RuntimeError("forced paper entry ledger failure")
        ),
    )

    with pytest.raises(RuntimeError, match="forced paper entry ledger failure"):
        execute_paper_order(
            proposal_id="proposal-paper-entry-atomic",
            risk_decision_id="risk-paper-entry-atomic",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
        )

    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-PCM-04",
        "pipeline_paper_close_materialization",
        "§0 committed close audit handoff",
        "L2c",
        "A JSONL audit sink failure after commit cannot turn a real close into a "
        "reported materialization failure; the durable database audit still records it.",
        status="implemented",
    )
)
def test_committed_paper_close_survives_jsonl_audit_sink_failure(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.execution.paper_broker import close_paper_position
    from aletheia.files import jsonl
    from aletheia.storage import repositories as repo

    _, position = _seed_open_position(
        direction="long", stop_price=95.0, take_profit_price=110.0
    )
    monkeypatch.setattr(
        jsonl,
        "write_audit",
        lambda _event: (_ for _ in ()).throw(RuntimeError("audit sink down")),
    )

    result = close_paper_position(
        position.id, mark_price=110.0, reason="take_profit"
    )

    stored = repo._select_one("positions", position.id)
    close_order, close_fill = _close_chain(conn, position.id)
    assert result["closed"] is True
    assert stored["status"] == "closed"
    assert close_order["status"] == "filled"
    assert close_fill is not None
    assert any(
        event["event_type"] == "position_closed"
        and event["entity_id"] == position.id
        for event in repo.list_audit_events(limit=20)
    )


@spec_claim(
    SpecClaim(
        "PL-PCM-05",
        "pipeline_paper_close_materialization",
        "§0 manual dashboard close routing",
        "L2c",
        "A human bulk-close request ignores the stale Position projection, uses the "
        "fresh exact-instrument mark for its adverse simulated Fill, and persists the "
        "human/dashboard actor instead of bypassing execution.",
        status="implemented",
    )
)
def test_manual_bulk_close_routes_through_paper_execution_chain(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.server.routes import settings
    from aletheia.storage import repositories as repo

    _, position = _seed_open_position(
        direction="long", stop_price=95.0, take_profit_price=120.0
    )
    # Position.current_price is a display/projection value, not close authority.
    repo.update_position(position.id, current_price=110.0)
    _seed_market_snapshot(symbol="BTC/USDT", price=105.0)
    monkeypatch.setattr(settings, "_require_local_paper_mode", lambda: None)
    monkeypatch.setattr(
        settings, "_audit_paper_account_change", lambda *args, **kwargs: None
    )

    result = settings._close_local_positions(None)

    stored = repo._select_one("positions", position.id)
    close_order, close_fill = _close_chain(conn, position.id)
    assert result["closed_position_ids"] == [position.id]
    assert result["failed"] == []
    assert stored["status"] == "closed"
    assert close_order["close_reason"] == "manual_bulk_close"
    assert close_fill["fill_price"] == pytest.approx(104.895)
    close_event = next(
        event
        for event in repo.list_audit_events(limit=20)
        if event["event_type"] == "position_closed"
        and event["entity_id"] == position.id
    )
    assert close_event["actor"] == "human"
    assert close_event["source"] == "dashboard"


@spec_claim(
    SpecClaim(
        "PL-PCM-12",
        "pipeline_paper_close_materialization",
        "§0 single local close mark authority",
        "L2c",
        "The central single-Position router never lets fallback_exit_price authorize "
        "a paper Fill; it resolves the fresh exact venue/symbol/market mark itself.",
        status="implemented",
    )
)
def test_single_local_close_ignores_fallback_price_and_resolves_fresh_mark(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.portfolio.position_manager import close_position_routed

    _, position = _seed_open_position(
        direction="long",
        stop_price=95.0,
        take_profit_price=120.0,
        symbol="SOL/USDT",
    )
    _seed_market_snapshot(symbol="SOL/USDT", price=105.0)

    result = close_position_routed(
        position.id,
        "manual",
        fallback_exit_price=999.0,
        decision_actor="human",
        decision_source="dashboard",
    )

    close_order, close_fill = _close_chain(conn, position.id)
    assert result["closed"] is True
    assert close_order["close_reason"] == "manual"
    assert close_fill["fill_price"] == pytest.approx(104.895)


@spec_claim(
    SpecClaim(
        "PL-PCM-13",
        "pipeline_paper_close_materialization",
        "§0 all-or-none bulk close mark preflight",
        "L2c",
        "A dashboard bulk close preflights a fresh exact mark for every candidate. "
        "If any instrument is missing, the API returns 409 and materializes no close "
        "Order, Fill, Position outcome, or feedback job for the whole batch.",
        status="implemented",
    )
)
def test_manual_bulk_close_missing_exact_mark_rejects_whole_batch_without_facts(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from fastapi import HTTPException

    from aletheia.server.routes import settings

    _, btc = _seed_open_position(
        direction="long",
        stop_price=95.0,
        take_profit_price=120.0,
        symbol="BTC/USDT",
    )
    _, eth = _seed_open_position(
        direction="long",
        stop_price=195.0,
        take_profit_price=240.0,
        symbol="ETH/USDT",
    )
    _seed_market_snapshot(symbol="BTC/USDT", price=105.0, market_type="spot")
    # A fresh same-symbol perp row must not authorize the ETH spot close.
    _seed_market_snapshot(symbol="ETH/USDT", price=205.0, market_type="perp")
    monkeypatch.setattr(settings, "_require_local_paper_mode", lambda: None)

    with pytest.raises(HTTPException) as raised:
        settings._close_local_positions(None)

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "bulk_close_mark_preflight_failed"
    assert raised.value.detail["closed_position_ids"] == []
    assert {item["position_id"] for item in raised.value.detail["failed"]} == {
        eth.id,
    }
    stored = conn.execute(
        "SELECT id,status,closed_at,outcome_eligible,outcome_exclusion_reason "
        "FROM positions WHERE id IN (?,?) ORDER BY id",
        (btc.id, eth.id),
    ).fetchall()
    assert all(row["status"] == "open" for row in stored)
    assert all(row["closed_at"] is None for row in stored)
    assert all(row["outcome_eligible"] == 0 for row in stored)
    assert all(row["outcome_exclusion_reason"] == "unclassified" for row in stored)
    assert conn.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id IN (?,?) "
        "AND close_reason IS NOT NULL",
        (btc.id, eth.id),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM fills f JOIN orders o ON o.id=f.order_id "
        "WHERE o.position_id IN (?,?) AND o.close_reason IS NOT NULL",
        (btc.id, eth.id),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM outcome_hook_jobs WHERE position_id IN (?,?)",
        (btc.id, eth.id),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-PCM-14",
        "pipeline_paper_close_materialization",
        "§0 successful bulk close mark preflight",
        "L2c",
        "Once every candidate has a fresh exact mark, a dashboard bulk close routes "
        "each Position through real paper materialization and preserves the human/"
        "dashboard actor on every close fact.",
        status="implemented",
    )
)
def test_manual_bulk_close_all_fresh_marks_materializes_every_close_with_actor(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.server.routes import settings
    from aletheia.storage import repositories as repo

    _, btc = _seed_open_position(
        direction="long",
        stop_price=95.0,
        take_profit_price=120.0,
        symbol="BTC/USDT",
    )
    _, eth = _seed_open_position(
        direction="long",
        stop_price=195.0,
        take_profit_price=240.0,
        symbol="ETH/USDT",
    )
    _seed_market_snapshot(symbol="BTC/USDT", price=105.0)
    _seed_market_snapshot(symbol="ETH/USDT", price=205.0)
    monkeypatch.setattr(settings, "_require_local_paper_mode", lambda: None)
    monkeypatch.setattr(
        settings, "_audit_paper_account_change", lambda *args, **kwargs: None
    )

    result = settings._close_local_positions(None)

    assert set(result["closed_position_ids"]) == {btc.id, eth.id}
    assert result["failed"] == []
    close_fills = {
        row["position_id"]: row["fill_price"]
        for row in conn.execute(
            "SELECT o.position_id,f.fill_price FROM orders o "
            "JOIN fills f ON f.order_id=o.id "
            "WHERE o.position_id IN (?,?) AND o.close_reason='manual_bulk_close'",
            (btc.id, eth.id),
        ).fetchall()
    }
    assert close_fills == pytest.approx({
        btc.id: 104.895,
        eth.id: 204.795,
    })
    close_events = [
        event for event in repo.list_audit_events(limit=50)
        if event["event_type"] == "position_closed"
        and event["entity_id"] in {btc.id, eth.id}
    ]
    assert len(close_events) == 2
    assert all(event["actor"] == "human" for event in close_events)
    assert all(event["source"] == "dashboard" for event in close_events)


@spec_claim(
    SpecClaim(
        "PL-PCM-15",
        "pipeline_paper_close_materialization",
        "§0 honest partial bulk close",
        "L2c",
        "A non-price failure after successful bulk preflight is returned as an error "
        "that names both the durably closed and failed Positions; a partial batch is "
        "never reported as a successful close-all.",
        status="implemented",
    )
)
def test_manual_bulk_close_execution_failure_reports_closed_and_failed_ids(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from fastapi import HTTPException

    from aletheia.portfolio import position_manager
    from aletheia.server.routes import settings

    _, btc = _seed_open_position(
        direction="long",
        stop_price=95.0,
        take_profit_price=120.0,
        symbol="BTC/USDT",
    )
    _, eth = _seed_open_position(
        direction="long",
        stop_price=195.0,
        take_profit_price=240.0,
        symbol="ETH/USDT",
    )
    _seed_market_snapshot(symbol="BTC/USDT", price=105.0)
    _seed_market_snapshot(symbol="ETH/USDT", price=205.0)
    real_close = position_manager._close_local_paper_with_authorized_mark

    def fail_one(position_id, mark_price, **kwargs):
        if position_id == eth.id:
            raise RuntimeError("materialization failed")
        return real_close(position_id, mark_price, **kwargs)

    monkeypatch.setattr(
        position_manager,
        "_close_local_paper_with_authorized_mark",
        fail_one,
    )
    monkeypatch.setattr(settings, "_require_local_paper_mode", lambda: None)
    monkeypatch.setattr(
        settings, "_audit_paper_account_change", lambda *args, **kwargs: None
    )

    with pytest.raises(HTTPException) as raised:
        settings._close_local_positions(None)

    assert raised.value.status_code == 500
    assert raised.value.detail["code"] == "bulk_close_partial_failure"
    assert raised.value.detail["closed_position_ids"] == [btc.id]
    assert raised.value.detail["failed"] == [{
        "position_id": eth.id,
        "symbol": "ETH/USDT",
        "market_type": "spot",
        "code": "close_failed",
        "error_type": "RuntimeError",
        "message": "materialization failed",
    }]
    states = {
        row["id"]: row["status"]
        for row in conn.execute(
            "SELECT id,status FROM positions WHERE id IN (?,?)",
            (btc.id, eth.id),
        ).fetchall()
    }
    assert states == {btc.id: "closed", eth.id: "open"}


@spec_claim(
    SpecClaim(
        "PL-PCM-01",
        "pipeline_paper_close_materialization",
        "§1 observed mark and adverse close slippage",
        "L2b",
        "A paper stop/TP uses its trigger only to decide, then simulates the close "
        "from the observed mark with directionally adverse configured slippage and "
        "persists one exact close Order/Fill chain.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    "direction,stop_price,take_profit_price,observed_mark,reason,expected_fill",
    (
        ("long", 95.0, 120.0, 90.0, "stop_loss", 89.91),
        ("short", 110.0, 90.0, 85.0, "take_profit", 85.085),
    ),
)
def test_stop_or_tp_materializes_one_close_from_observed_mark(
    tmp_path,
    monkeypatch,
    direction,
    stop_price,
    take_profit_price,
    observed_mark,
    reason,
    expected_fill,
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.portfolio import position_manager
    from aletheia.files import jsonl
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    entry_order, position = _seed_open_position(
        direction=direction,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
    )
    cash_before = repo.get_paper_account_cash_balance()
    monkeypatch.setattr(
        position_manager,
        "get_fresh_price_for_protective_close",
        lambda _symbol, _market_type, *, venue: observed_mark,
    )
    audit_observations: list[dict] = []

    def _capture_audit(event, *, mirror_db=True):
        audit_observations.append({
            "event": event,
            "mirror_db": mirror_db,
            "in_transaction": get_db().in_transaction,
            "position_status": repo._select_one("positions", position.id)["status"],
            "cash_settled": repo.has_paper_account_position_event(
                "position_closed", position.id,
            ),
        })

    monkeypatch.setattr(jsonl, "write_audit", _capture_audit)

    result = position_manager.evaluate_open_positions()

    stored = repo._select_one("positions", position.id)
    close_order, close_fill = _close_chain(conn, position.id)
    assert result["closed"] == 1
    assert stored["status"] == "closed"
    assert stored["close_reason"] == reason
    assert stored["current_price"] == pytest.approx(expected_fill)
    assert stored["order_id"] == entry_order.id
    assert close_order is not None
    assert close_order["client_order_id"] == f"close-{position.id}"
    assert close_order["position_id"] == position.id
    assert close_order["close_reason"] == reason
    assert close_order["status"] == "filled"
    assert close_order["price"] == pytest.approx(expected_fill)
    assert close_fill is not None
    assert close_fill["fill_price"] == pytest.approx(expected_fill)
    assert close_fill["fill_quantity"] == pytest.approx(position.quantity)
    expected_fee = expected_fill * position.quantity * FEE_RATE
    assert close_fill["fee"] == pytest.approx(expected_fee)
    assert close_fill["filled_at"] == close_order["filled_at"]
    assert stored["exit_fee"] == pytest.approx(expected_fee)
    evidence = json.loads(stored["reconciliation_evidence"])
    assert evidence["fill_id"] == close_fill["id"]
    assert repo.get_paper_account_cash_balance() == pytest.approx(
        cash_before + stored["realized_pnl"]
    )
    close_audits = [
        item for item in audit_observations
        if item["event"].get("event_type") == "position_closed"
    ]
    assert len(close_audits) == 1
    assert close_audits[0]["mirror_db"] is False
    assert close_audits[0]["in_transaction"] is False
    assert close_audits[0]["position_status"] == "closed"
    assert close_audits[0]["cash_settled"] is True

    from aletheia.execution.paper_broker import close_paper_position

    duplicate = close_paper_position(
        position.id,
        mark_price=observed_mark,
        reason=reason,
    )
    assert duplicate["already_closed"] is True
    assert conn.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=?", (position.id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],),
    ).fetchone()[0] == 1


@spec_claim(
    SpecClaim(
        "PL-PCM-02",
        "pipeline_paper_close_materialization",
        "§2 atomic local close commit",
        "L2c",
        "Paper Position, close Order, Fill, and cash settlement commit together; "
        "materialization or settlement failure leaves all four unchanged and emits "
        "no close audit, notification, or outcome feedback.",
        status="implemented",
    )
)
@pytest.mark.parametrize("failure_point", ("fill", "settlement"))
def test_paper_close_failure_rolls_back_all_state_and_side_effects(
    tmp_path, monkeypatch, failure_point,
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.analytics import shadow_trades
    from aletheia.execution import paper_broker
    from aletheia.files import jsonl
    import aletheia.notifications as notifications_module
    from aletheia.portfolio import paper_account, pnl
    from aletheia.storage import repositories as repo

    entry_order, position = _seed_open_position(
        direction="long",
        stop_price=95.0,
        take_profit_price=120.0,
    )
    cash_before = repo.get_paper_account_cash_balance()
    audits: list[dict] = []
    notifications: list[dict] = []
    feedback: list[dict] = []
    monkeypatch.setattr(jsonl, "write_audit", lambda event: audits.append(event))
    monkeypatch.setattr(
        notifications_module,
        "notify_operator",
        lambda **event: notifications.append(event),
    )
    monkeypatch.setattr(
        shadow_trades,
        "resolve_shadow_trades_for_position",
        lambda row: feedback.append(row),
    )
    monkeypatch.setattr(pnl, "_btc_price_at_close", lambda: None)

    def _fail(*_args, **_kwargs):
        raise RuntimeError(f"forced {failure_point} failure")

    if failure_point == "fill":
        monkeypatch.setattr(paper_broker, "insert_fill", _fail)
    else:
        monkeypatch.setattr(paper_account, "settle_position_closed", _fail)

    with pytest.raises(RuntimeError, match=f"forced {failure_point} failure"):
        paper_broker.close_paper_position(
            position.id,
            mark_price=90.0,
            reason="stop_loss",
        )

    stored = repo._select_one("positions", position.id)
    close_order, close_fill = _close_chain(conn, position.id)
    assert stored["status"] == "open"
    assert stored["order_id"] == entry_order.id
    assert close_order is None
    assert close_fill is None
    assert repo.get_paper_account_cash_balance() == pytest.approx(cash_before)
    assert not repo.has_paper_account_position_event("position_closed", position.id)
    assert audits == []
    assert notifications == []
    assert feedback == []


@spec_claim(
    SpecClaim(
        "PL-PCM-06",
        "pipeline_paper_close_materialization",
        "§3 unknown futures funding containment",
        "L2c",
        "A local-paper futures Position may close its execution lifecycle without "
        "funding evidence, but cannot settle cash or become an eligible Outcome by "
        "implicitly treating unknown funding as zero.  The portfolio shows a partial "
        "sum (with complete=False and unavailable_count>0) rather than blocking the "
        "entire display.",
            status="implemented",
    )
)
def test_unknown_paper_perp_funding_closes_without_zero_equivalent_cash_settlement(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.analytics.outcome_integrity import assess_position_closure
    from aletheia.execution.paper_broker import (
        close_paper_position,
        execute_paper_order,
    )
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, RiskDecision, TradeProposal

    # Seed only the immutable upstream authority. Order, Fill, Position, and both
    # paper-account events must be produced (or withheld) by the real broker paths.
    opportunity = Opportunity(
        symbol="BTC/USDT",
        market="perp",
        direction_bias="long",
        thesis="unknown funding must remain economically unavailable",
        source_agent="market_observer",
        scanner_family="trend",
    )
    repo.insert_opportunity(opportunity)
    proposal = TradeProposal(
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        leverage=2,
        status="approved",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)

    opened = execute_paper_order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        opportunity_id=opportunity.id,
        leverage=proposal.leverage,
    )
    opened_position = repo._select_one("positions", opened["position_id"])
    assert opened_position["funding_cost"] is None
    assert opened_position["funding_source"] == "unavailable"
    cash_before = repo.get_paper_account_cash_balance()

    result = close_paper_position(
        opened["position_id"],
        mark_price=110.0,
        reason="take_profit",
    )

    stored = repo._select_one("positions", opened["position_id"])
    close_order = conn.execute(
        "SELECT * FROM orders WHERE id=?", (result["order_id"],),
    ).fetchone()
    close_fill = conn.execute(
        "SELECT * FROM fills WHERE id=?", (result["fill_id"],),
    ).fetchone()

    assert result["closed"] is True
    assert close_order is not None
    assert close_order["position_id"] == stored["id"]
    assert close_order["close_reason"] == "take_profit"
    assert close_order["status"] == "filled"
    assert close_fill is not None
    assert close_fill["order_id"] == close_order["id"]
    assert close_fill["fill_quantity"] == pytest.approx(proposal.quantity)
    assert stored["status"] == "closed"
    assert stored["funding_cost"] is None
    assert stored["funding_source"] == "unavailable"
    assert stored["realized_pnl"] is None
    assert not repo.has_paper_account_position_event(
        "position_closed", stored["id"],
    )
    assert repo.get_paper_account_cash_balance() == pytest.approx(cash_before)
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "incomplete_funding_economics"
    assert result["realized_pnl"] is None
    assert result["outcome_eligible"] is False
    assert result["outcome_exclusion_reason"] == "incomplete_funding_economics"
    assessment = assess_position_closure(stored)
    assert assessment.eligible is False
    assert assessment.reason == "incomplete_funding_economics"
    assert repo.list_outcome_hook_jobs(position_id=stored["id"]) == []

    # economics_complete=False signals the unsettled funding leg for monitoring;
    # it must NOT zero out available_balance_usdt — the known cash is spendable
    # even while the funding delta is still in flight (#316).
    from aletheia.portfolio.paper_account import get_paper_account
    from aletheia.portfolio.pnl import compute_portfolio

    account = get_paper_account(event_limit=100)
    assert account["economics_complete"] is False
    assert account["unsettled_closed_positions_count"] == 1
    # Actual cash is returned regardless of economics_complete (#316 fix).
    assert account["available_balance_usdt"] == account["cash_balance_usdt"]
    assert account["equity_usdt"] == pytest.approx(account["cash_balance_usdt"])

    portfolio = compute_portfolio(execution_mode="paper_internal")
    # Method B: partial sums shown with complete=False; no complete closes → 0.0
    assert portfolio["realized_pnl"] == pytest.approx(0.0)
    assert portfolio["realized_pnl_complete"] is False
    assert portfolio["realized_pnl_unavailable_count"] == 1
    assert portfolio["today_realized_pnl"] == pytest.approx(0.0)
    assert portfolio["today_realized_pnl_complete"] is False
    assert portfolio["today_realized_pnl_unavailable_count"] == 1
    assert portfolio["today_pnl"] == pytest.approx(0.0)
    # Per-symbol: still None when ALL closes for that symbol are unavailable
    assert portfolio["today_pnl_by_symbol"][proposal.symbol] is None


@spec_claim(
    SpecClaim(
        "PL-PCM-07",
        "pipeline_paper_close_materialization",
        "§3 legacy unknown-funding containment",
        "L2c",
        "A legacy local-paper futures close whose numeric PnL omitted unknown "
        "funding remains economically unavailable and cannot restore spendable "
        "cash or numeric portfolio totals.",
        status="implemented",
    )
)
def test_legacy_numeric_paper_perp_close_with_unknown_funding_stays_unavailable(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from datetime import datetime, timedelta, timezone

    from aletheia.portfolio.paper_account import (
        get_paper_account,
        record_position_opened,
        settle_position_closed,
    )
    from aletheia.portfolio.pnl import compute_portfolio
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    closed_at = datetime.now(timezone.utc)
    entry_fee = 0.06
    exit_fee = 0.066
    legacy_recorded_pnl = 10.0 - entry_fee - exit_fee
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        unrealized_pnl=0.0,
        realized_pnl=legacy_recorded_pnl,
        execution_mode="paper_internal",
        status="closed",
        close_reason="take_profit",
        close_category="strategy",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        exit_price_source="local_fill",
        exit_fill_verified=True,
        outcome_eligible=False,
        outcome_exclusion_reason="incomplete_funding_economics",
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        funding_cost=None,
        funding_source="unavailable",
        opened_at=opened_at.isoformat(),
        closed_at=closed_at.isoformat(),
    )
    repo.insert_position(position)

    # Reproduce the pre-fix ledger shape: the incomplete numeric result was
    # already posted to cash even though the funding leg had no authority.
    record_position_opened(position.id)
    settle_position_closed(position.id, legacy_recorded_pnl)
    assert repo.has_paper_account_position_event(
        "position_closed", position.id,
    )

    account = get_paper_account(event_limit=100)
    assert account["economics_complete"] is False
    assert account["unsettled_closed_positions_count"] == 1
    # Actual cash is returned regardless of economics_complete (#316 fix).
    assert account["available_balance_usdt"] == pytest.approx(account["cash_balance_usdt"])
    assert account["equity_usdt"] == pytest.approx(account["cash_balance_usdt"])

    portfolio = compute_portfolio(execution_mode="paper_internal")
    # Method B: partial sums shown; no complete closes → 0.0
    assert portfolio["realized_pnl"] == pytest.approx(0.0)
    assert portfolio["realized_pnl_complete"] is False
    assert portfolio["realized_pnl_unavailable_count"] == 1
    assert portfolio["today_realized_pnl"] == pytest.approx(0.0)
    assert portfolio["today_realized_pnl_complete"] is False
    assert portfolio["today_realized_pnl_unavailable_count"] == 1
    assert portfolio["today_pnl"] == pytest.approx(0.0)
    assert portfolio["today_pnl_by_symbol"][position.symbol] is None


@spec_claim(
    SpecClaim(
        "PL-PCM-08",
        "pipeline_paper_close_materialization",
        "§3 unbounded account completeness",
        "L2c",
        "Paper-account authority and portfolio totals inspect the complete close "
        "history; an old incomplete close cannot disappear behind a display limit.",
        status="implemented",
    )
)
def test_old_incomplete_close_beyond_ten_thousand_rows_still_freezes_account(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from datetime import datetime, timezone

    from aletheia.portfolio.paper_account import get_paper_account, set_cash_balance
    from aletheia.portfolio.pnl import compute_portfolio

    closed_at = datetime.now(timezone.utc).isoformat()
    set_cash_balance(200.0, note="history-limit-regression")
    with conn:
        conn.execute(
            "INSERT INTO positions "
            "(id,symbol,market_type,direction,quantity,entry_price,current_price,"
            "unrealized_pnl,realized_pnl,execution_mode,status,entry_fee,exit_fee,"
            "funding_cost,funding_source,opened_at,closed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old-incomplete-futures-close", "BTC/USDT", "usdt_futures",
                "long", 1.0, 100.0, 110.0, 0.0, 9.8, "paper_internal",
                "closed", 0.1, 0.1, None, "unavailable", closed_at, closed_at,
            ),
        )
        conn.executemany(
            "INSERT INTO positions "
            "(id,symbol,market_type,direction,quantity,entry_price,current_price,"
            "unrealized_pnl,realized_pnl,execution_mode,status,entry_fee,exit_fee,"
            "funding_cost,funding_source,opened_at,closed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"new-complete-spot-{index}", "ETH/USDT", "spot", "long",
                    1.0, 100.0, 100.0, 0.0, 0.0, "paper_internal", "closed",
                    0.0, 0.0, 0.0, "not_applicable", closed_at, closed_at,
                )
                for index in range(10_000)
            ],
        )

    account = get_paper_account(event_limit=0)
    assert account["economics_complete"] is False
    assert account["unsettled_closed_positions_count"] == 1
    # Actual cash is returned regardless of economics_complete (#316 fix).
    assert account["available_balance_usdt"] == pytest.approx(account["cash_balance_usdt"])
    assert account["equity_usdt"] == pytest.approx(account["cash_balance_usdt"])

    portfolio = compute_portfolio(execution_mode="paper_internal")
    # Method B: 10,000 complete closes (0.0 each) + 1 unavailable → partial sum = 0.0
    assert portfolio["realized_pnl"] == pytest.approx(0.0)
    assert portfolio["realized_pnl_complete"] is False
    assert portfolio["realized_pnl_unavailable_count"] == 1


@spec_claim(
    SpecClaim(
        "PL-PCM-10",
        "pipeline_paper_close_materialization",
        "§3 unbounded open collateral",
        "L2c",
        "Paper-account spendable cash reserves collateral for every open local Position; "
        "the 10,001st Position cannot disappear behind a display limit.",
        status="implemented",
    )
)
def test_paper_account_reserves_every_open_local_position(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.portfolio.paper_account import get_paper_account, set_cash_balance

    opened_at = "2026-07-14T00:00:00+00:00"
    set_cash_balance(10_001.0, note="open-collateral-window-regression")
    conn.executemany(
        "INSERT INTO positions "
        "(id,symbol,market_type,direction,quantity,entry_price,current_price,"
        "execution_mode,status,opened_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                f"open-local-{index}", "BTC/USDT", "spot", "long",
                1.0, 1.0, 1.0, "paper_internal", "open", opened_at,
            )
            for index in range(10_001)
        ],
    )

    account = get_paper_account(event_limit=0)

    assert account["open_positions_count"] == 10_001
    assert account["reserved_margin_usdt"] == pytest.approx(10_001.0)
    assert account["available_balance_usdt"] == pytest.approx(0.0)


@spec_claim(
    SpecClaim(
        "PL-PCM-11",
        "pipeline_paper_close_materialization",
        "§0 complete manual bulk close",
        "L2c",
        "A human close-all request enumerates every open local Position before routing "
        "each one through the paper broker; no 10,001st Position is silently left open.",
        status="implemented",
    )
)
def test_manual_bulk_close_enumerates_every_open_local_position(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.portfolio import position_manager
    from aletheia.server.routes import settings

    opened_at = "2026-07-14T00:00:00+00:00"
    expected_ids = {f"bulk-local-{index}" for index in range(10_001)}
    conn.executemany(
        "INSERT INTO positions "
        "(id,symbol,market_type,direction,quantity,entry_price,current_price,"
        "execution_mode,status,opened_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                position_id, "BTC/USDT", "spot", "long", 1.0, 1.0, 1.0,
                "paper_internal", "open", opened_at,
            )
            for position_id in expected_ids
        ]
        + [
            (
                "external-open-position", "ETH/USDT", "spot", "long", 1.0,
                1.0, 1.0, "okx_demo", "open", opened_at,
            )
        ],
    )
    routed_ids: list[str] = []

    def fake_close(position_id, _mark_price, **_kwargs):
        routed_ids.append(position_id)
        return {"position_id": position_id, "closed": True}

    monkeypatch.setattr(settings, "_require_local_paper_mode", lambda: None)
    monkeypatch.setattr(
        settings, "_audit_paper_account_change", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        position_manager,
        "get_fresh_price_for_protective_close",
        lambda _symbol, _market_type, *, venue: 1.0,
    )
    monkeypatch.setattr(
        position_manager,
        "_close_local_paper_with_authorized_mark",
        fake_close,
    )

    result = settings._close_local_positions(None)

    assert set(routed_ids) == expected_ids
    assert set(result["closed_position_ids"]) == expected_ids
    assert result["requested_count"] == 10_001
    assert result["failed"] == []
    assert "external-open-position" not in routed_ids


@spec_claim(
    SpecClaim(
        "PL-PCM-09",
        "pipeline_paper_close_materialization",
        "§3 honest position read models",
        "L2c",
        "Positions API and CSV export mask legacy numeric futures PnL when its "
        "funding economics cannot be reproduced.",
        status="implemented",
    )
)
@pytest.mark.anyio
async def test_positions_api_and_export_mask_legacy_unknown_funding_pnl(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    import csv
    import io

    from httpx import ASGITransport, AsyncClient

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        id="legacy-api-futures-close",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        unrealized_pnl=0.0,
        realized_pnl=9.8,
        execution_mode="paper_internal",
        status="closed",
        close_reason="take_profit",
        entry_fee=0.1,
        exit_fee=0.1,
        funding_cost=None,
        funding_source="unavailable",
    )
    repo.insert_position(position)

    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        positions_response = await client.get("/api/positions")
        export_response = await client.get("/api/trades/export?fmt=csv")

    assert positions_response.status_code == 200
    api_row = next(
        row for row in positions_response.json() if row["id"] == position.id
    )
    assert api_row["realized_pnl"] is None
    assert api_row["realized_pnl_complete"] is False
    assert api_row["realized_pnl_unavailable_reason"] == (
        "incomplete_funding_economics"
    )

    assert export_response.status_code == 200
    csv_row = next(csv.DictReader(io.StringIO(export_response.text)))
    assert csv_row["position_id"] == position.id
    assert csv_row["realized_pnl"] == ""
    assert csv_row["realized_pnl_complete"] == "False"
    assert csv_row["realized_pnl_unavailable_reason"] == (
        "incomplete_funding_economics"
    )


# ---------------------------------------------------------------------------
# Terminal `funding_permanently_unrecoverable` state
# (FUND-B commits aa23804e + d20973d6; audit Q3-1 + Q3-2)
# ---------------------------------------------------------------------------


def _seed_stuck_paper_close(
    *,
    position_id: str,
    symbol: str,
    closed_at,
    outcome_exclusion_reason: str = "incomplete_funding_economics",
) -> None:
    """Insert a closed paper_internal usdt_futures row whose funding
    economics never settled — the exact shape a settler retry loop
    or a give-up transition operates on."""
    from datetime import timedelta
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    if isinstance(closed_at, str):
        parsed_closed_at = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
    else:
        parsed_closed_at = closed_at
    opened_at = parsed_closed_at - timedelta(hours=1)
    repo.insert_position(Position(
        id=position_id,
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.5,
        unrealized_pnl=0.0,
        realized_pnl=None,
        execution_mode="paper_internal",
        status="closed",
        close_reason="max_hold",
        close_category="strategy",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        exit_price_source="local_fill",
        exit_fill_verified=True,
        outcome_eligible=False,
        outcome_exclusion_reason=outcome_exclusion_reason,
        entry_fee=0.06,
        exit_fee=0.06,
        funding_cost=None,
        funding_source="unavailable",
        opened_at=opened_at.isoformat(),
        closed_at=parsed_closed_at.isoformat(),
    ))


@spec_claim(
    SpecClaim(
        "PL-PCM-16",
        "pipeline_paper_close_materialization",
        "§4 settler give-up transition",
        "L2c",
        "paper_funding_settler transitions unsettled paper_internal futures "
        "closes to `outcome_exclusion_reason=funding_permanently_unrecoverable` "
        "after the age threshold, writes an audit event, and NEVER "
        "fabricates a realized_pnl number.  Second-run idempotency: a "
        "row already terminal is not rewritten.",
        status="implemented",
    )
)
def test_paper_funding_settler_transitions_to_terminal_after_giveup_window(
    tmp_path, monkeypatch,
):
    """Q3-1: age-based give-up + audit event + idempotency."""
    _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    import asyncio
    from datetime import timedelta
    from aletheia.portfolio import paper_account as pa
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    # A close 25h old — one hour past the 24h give-up threshold — with
    # unavailable funding is the classic "collector had a gap and the
    # history endpoint cannot rescue close_anchor" scenario.
    close_time = datetime.now(timezone.utc) - timedelta(hours=25)
    _seed_stuck_paper_close(
        position_id="pos-terminal-1",
        symbol="FIL/USDT",
        closed_at=close_time,
    )

    result = asyncio.run(pa.settle_unsettled_paper_closes())

    assert result["settled"] == 0
    assert result["still_pending"] == 0
    assert result.get("gave_up", 0) == 1
    assert result["errors"] == []

    stored = repo._select_one("positions", "pos-terminal-1")
    assert stored["outcome_exclusion_reason"] == "funding_permanently_unrecoverable"
    # CRITICAL: realized_pnl remains NULL — the settler MUST NOT fabricate
    # a number for a close whose economics are architecturally unknown.
    assert stored["realized_pnl"] is None
    assert stored["outcome_eligible"] == 0
    assert stored["funding_source"] == "unavailable"
    assert stored["funding_cost"] is None

    # Audit trail: exactly one `paper_position_funding_gave_up` event.
    events = get_db().execute(
        "SELECT event_type FROM audit_events "
        "WHERE entity_type='position' AND entity_id=? AND source='paper_funding_settler'",
        ("pos-terminal-1",),
    ).fetchall()
    give_up_events = [e for e in events if e["event_type"] == "paper_position_funding_gave_up"]
    assert len(give_up_events) == 1

    # Idempotency: second settler run must NOT rewrite the row or emit
    # another audit event (the query excludes terminal rows so it never
    # even reaches them).
    result2 = asyncio.run(pa.settle_unsettled_paper_closes())
    assert result2["settled"] == 0
    assert result2.get("gave_up", 0) == 0
    assert result2["still_pending"] == 0

    events_after = get_db().execute(
        "SELECT event_type FROM audit_events "
        "WHERE entity_type='position' AND entity_id=? AND event_type='paper_position_funding_gave_up'",
        ("pos-terminal-1",),
    ).fetchall()
    assert len(events_after) == 1


@spec_claim(
    SpecClaim(
        "PL-PCM-17",
        "pipeline_paper_close_materialization",
        "§4 hero unblocks once all unsettled are terminal",
        "L2c",
        "Once every unsettled paper_internal futures close has reached the "
        "terminal `funding_permanently_unrecoverable` state, the paper "
        "account view reports `economics_complete=True` and "
        "`compute_portfolio` publishes a numeric `total_value_usdt`, "
        "without fabricating any realized PnL number.  A single row "
        "regressing to the retrying state re-blocks the hero.",
        status="implemented",
    )
)
def test_overview_hero_unblocks_when_all_unsettled_closes_are_terminal(
    tmp_path, monkeypatch,
):
    """Q3-2: the exact user-reported symptom regression — hero '— USDT'."""
    _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from datetime import timedelta
    from aletheia.portfolio.paper_account import (
        FUNDING_PERMANENTLY_UNRECOVERABLE,
        get_paper_account,
    )
    from aletheia.portfolio.pnl import compute_portfolio
    from aletheia.storage import repositories as repo

    # Seed three closes already in the terminal state — mirroring the
    # ALLO/CL/FIL production incident.
    close_time = datetime.now(timezone.utc) - timedelta(hours=30)
    for i, symbol in enumerate(("FIL/USDT", "CL/USDT", "ALLO/USDT")):
        _seed_stuck_paper_close(
            position_id=f"pos-terminal-{i}",
            symbol=symbol,
            closed_at=close_time,
            outcome_exclusion_reason=FUNDING_PERMANENTLY_UNRECOVERABLE,
        )

    account = get_paper_account(event_limit=0)
    # Terminal rows must NOT count as in-flight — this is the fix that
    # d20973d6 pinned. If a future refactor makes `_unsettled_local_closes`
    # naive again, `economics_complete` becomes False and hero freezes.
    assert account["economics_complete"] is True
    assert account["unsettled_closed_positions_count"] == 0
    assert account["equity_usdt"] is not None
    assert account["available_balance_usdt"] > 0.0

    portfolio = compute_portfolio(execution_mode="paper_internal")
    # The user-reported symptom: total_value_usdt must NOT be None.
    assert portfolio["total_value_usdt"] is not None
    assert portfolio["total_value_usdt"] > 0.0
    assert portfolio["account_equity_usdt"] is not None

    # Honest partial reporting: the Realized tile still shows the three
    # rows as unverifiable. `funding_permanently_unrecoverable` is a
    # terminal *classification* — not a fabricated number.
    assert portfolio["all_realized_pnl_unavailable_count"] == 3
    assert portfolio["all_realized_pnl_complete"] is False

    # Negative case: swap ONE terminal row back to the in-flight retry
    # state. `economics_complete` must return False and the hero must
    # re-freeze. This pins the direction of the fix — if the guard were
    # inverted, the negative case would also unblock and mask the bug.
    repo.update_position(
        "pos-terminal-1",
        outcome_exclusion_reason="incomplete_funding_economics",
    )
    account_after = get_paper_account(event_limit=0)
    assert account_after["economics_complete"] is False
    assert account_after["unsettled_closed_positions_count"] == 1
    # equity_usdt reflects actual cash even when economics_complete=False (#316 fix)
    assert account_after["equity_usdt"] == pytest.approx(account_after["cash_balance_usdt"])

    portfolio_after = compute_portfolio(execution_mode="paper_internal")
    assert portfolio_after["total_value_usdt"] is None


@spec_claim(
    SpecClaim(
        "PL-PCM-18",
        "pipeline_paper_close_materialization",
        "atomic paper funding settlement",
        "L2c",
        "A recovered paper_internal funding result commits positions.realized_pnl "
        "and exactly one paper_account_events.position_closed row atomically; a "
        "cash-ledger write failure leaves the Position economics unsettled.",
        status="implemented",
    )
)
def test_recovered_funding_economics_roll_back_when_cash_ledger_write_fails(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    _patch_config(monkeypatch)

    from aletheia.portfolio import paper_account as pa
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    pa.ensure_paper_account()
    _seed_stuck_paper_close(
        position_id="pos-atomic-funding",
        symbol="BTC/USDT",
        closed_at=datetime.now(timezone.utc),
    )
    original_insert = repo.insert_paper_account_event

    def fail_close_event(event):
        if event.action == "position_closed":
            raise RuntimeError("cash ledger unavailable")
        return original_insert(event)

    monkeypatch.setattr(repo, "insert_paper_account_event", fail_close_event)

    with pytest.raises(RuntimeError, match="cash ledger unavailable"):
        pa._settle_position_economics(
            "pos-atomic-funding",
            realized_pnl=0.25,
            funding_cost=0.05,
            outcome_eligible=True,
            outcome_exclusion_reason="",
        )

    stored = repo._select_one("positions", "pos-atomic-funding")
    assert stored["realized_pnl"] is None
    assert stored["funding_cost"] is None
    assert stored["funding_source"] == "unavailable"
    ledger_count = get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events "
        "WHERE action='position_closed' AND position_id=?",
        ("pos-atomic-funding",),
    ).fetchone()[0]
    assert ledger_count == 0
