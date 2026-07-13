"""Local-paper close execution and atomic materialization contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace
import sqlite3

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


SLIPPAGE_BPS = 10.0
FEE_RATE = 0.0006


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
):
    from aletheia.portfolio.paper_account import record_position_opened
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position

    entry_order = Order(
        source="adopted",
        symbol="BTC/USDT",
        market_type="spot",
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
        "A human bulk-close request uses the paper broker's adverse simulated Fill "
        "and persists the human/dashboard actor instead of bypassing execution.",
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
    repo.update_position(position.id, current_price=110.0)
    monkeypatch.setattr(settings, "_require_local_paper_mode", lambda: None)
    monkeypatch.setattr(
        settings, "_audit_paper_account_change", lambda *args, **kwargs: None
    )

    result = settings._close_local_positions(None)

    stored = repo._select_one("positions", position.id)
    close_order, close_fill = _close_chain(conn, position.id)
    assert result["closed_position_ids"] == [position.id]
    assert stored["status"] == "closed"
    assert close_order["close_reason"] == "manual_bulk_close"
    assert close_fill["fill_price"] == pytest.approx(109.89)
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
    monkeypatch.setattr(position_manager, "_latest_price", lambda _symbol: observed_mark)
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
