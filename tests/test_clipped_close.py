"""Clipped Bitget closes: venue match-cap cancels, partial-reduce materialization.

2026-08-22: Bitget demo zero-fill cancels full-quantity market-IOC closes with
trade_too_many_times_cancel (per-order match-iteration cap). Closes now clip
their quantity (halved per venue cancel of that class), and materialization
must reduce the Position per fill evidence without closing it early or
double-counting a replayed clip.
"""

from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone


class _CloseAdapter:
    def __init__(self, captured):
        from aletheia.uta.models import UTAAccountIdentity
        self.identity = UTAAccountIdentity(
            account_id="bitget_demo", execution_mode="bitget_demo", venue="bitget",
            environment="demo", profile="bitget_uta_v3_demo", credential_generation=1,
            adapter_implementation="CCXTUTAAdapter", adapter_version="1",
        )
        self.captured = captured

    async def submit_order(self, command):
        from aletheia.uta.models import SubmissionReport, CommandEvidence
        self.captured["order"] = command
        return SubmissionReport(
            identity=self.identity, command_id=command.command_id,
            evidence=CommandEvidence.ACKNOWLEDGED, received_at=datetime.now(timezone.utc),
            client_order_id=command.client_order_id, venue_order_id="EX-NEW-1",
            profile_version="1",
        )

    async def close(self):
        pass



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
        str(tmp_path / "app.db"), check_same_thread=False, isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    jsonl._last_audit_hash = None
    run_migrations()
    monkeypatch.setattr(
        "aletheia.execution.coordinator.require_uta_mutation_qualification",
        lambda *_args, **_kwargs: None,
    )
    return conn


def _seed_position_with_claim(db, *, position_id: str, order_id: str,
                              quantity: float, market_type: str = "usdt_futures") -> None:
    from tests.execution_authority import seed_order_authority
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Position

    proposal, _decision = seed_order_authority(
        proposal_id="prop-clip",
        risk_decision_id="risk-clip",
        symbol="XRP/USDT",
        market_type=market_type,
        direction="short",
        order_type="market",
        quantity=quantity,
        price=0.992,
        execution_mode="bitget_demo",
    )
    entry = Order(proposal_id=proposal.id, risk_decision_id=_decision.id, client_order_id=proposal.id,
                  symbol="XRP/USDT", market_type=market_type, direction="short",
                  order_type="market", quantity=quantity, price=0.992,
                  execution_mode="bitget_demo", status="filled",
                  uta_account_identity_json=_CloseAdapter({}).identity.model_dump_json())
    repo.insert_order(entry)
    repo.insert_position(Position(
        order_id=entry.id,
        id=position_id, proposal_id=proposal.id, symbol="XRP/USDT",
        market_type=market_type, direction="short", quantity=quantity,
        entry_price=0.992, execution_mode="bitget_demo", status="open",
        source="native", opened_at="2026-08-22T12:00:00+00:00",
        pending_close_order_id=order_id, pending_close_status="submitted",
    ))
    repo.insert_order(Order(
        uta_account_identity_json=entry.uta_account_identity_json,
        id=order_id, proposal_id=proposal.id, client_order_id=f"close-{position_id}",
        position_id=position_id, close_reason="stop_loss", symbol="XRP/USDT",
        market_type=market_type, direction="short", order_type="market",
        quantity=quantity, price=1.48, execution_mode="bitget_demo",
        status="submitted", source="native", exchange_order_id="EX-CLIP-1",
        created_at="2026-08-22T14:30:00+00:00",
    ))


def _fill_rows(quantity: float, exchange_order_id: str = "EX-CLIP-1",
               trade_id: str | None = None, fee_coin: str = "USDT") -> list[dict]:
    return [{
        "orderId": exchange_order_id,
        "tradeId": trade_id or f"FILL-{exchange_order_id}",
        "fillPrice": "1.4800",
        "fillQuantity": f"{quantity}",
        "fee": "0.2",
        "feeCoin": fee_coin,
        "cTime": "1787409000000",
    }]


def test_clipped_close_exact_fill_reduces_position_and_releases_claim(
    tmp_path, monkeypatch,
):
    """A clip that fills exactly must reduce — not close — the Position."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    # Position 424; the clipped close order only claims 212 of it.
    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    result = materialize_bitget_close("order-clip", _fill_rows(212.0))

    assert result.state == "filled_clip_partial_position"
    position = repo._select_one("positions", "pos-clip")
    assert position["status"] == "open"
    assert abs(float(position["quantity"]) - 212.0) < 1e-9
    assert position["pending_close_order_id"] is None
    assert position["pending_close_status"] is None
    order = repo.get_order("order-clip")
    assert order["status"] == "filled"

    events = db.execute(
        "SELECT closed_qty, is_partial FROM position_close_events "
        "WHERE position_id='pos-clip'"
    ).fetchall()
    assert events and abs(events[0]["closed_qty"] - 212.0) < 1e-9
    assert events[0]["is_partial"] == 1


def test_clipped_close_replay_does_not_double_count(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    first = materialize_bitget_close("order-clip", _fill_rows(212.0))
    assert first.state == "filled_clip_partial_position"

    replay = materialize_bitget_close("order-clip", _fill_rows(212.0))
    assert replay.state == "filled"
    position = repo._select_one("positions", "pos-clip")
    assert abs(float(position["quantity"]) - 212.0) < 1e-9
    assert position["status"] == "open"


def test_clipped_close_partial_fill_keeps_claim_for_continued_polling(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    result = materialize_bitget_close("order-clip", _fill_rows(106.0))

    assert result.state == "partially_filled"
    position = repo._select_one("positions", "pos-clip")
    assert position["status"] == "open"
    assert abs(float(position["quantity"]) - 318.0) < 1e-9
    assert position["pending_close_order_id"] == "order-clip"
    assert position["pending_close_status"] == "partially_filled"
    order = repo.get_order("order-clip")
    assert order["status"] == "partially_filled"

    # Second batch: another 106 fills for the same order. Only the delta may
    # reduce the Position (318 -> 212), never the order's running total.
    result2 = materialize_bitget_close("order-clip", _fill_rows(106.0)
                                       + _fill_rows(106.0, trade_id="FILL-EX-CLIP-1b"))
    assert result2.state == "filled_clip_partial_position"
    position2 = repo._select_one("positions", "pos-clip")
    assert abs(float(position2["quantity"]) - 212.0) < 1e-9
    assert position2["pending_close_order_id"] is None
    events = db.execute(
        "SELECT closed_qty FROM position_close_events WHERE position_id='pos-clip' "
        "ORDER BY rowid"
    ).fetchall()
    assert [row["closed_qty"] for row in events] == [106.0, 106.0]

    # Replay with the same full evidence: no new fills, no further reduction.
    result3 = materialize_bitget_close(
        "order-clip", _fill_rows(106.0) + _fill_rows(106.0, trade_id="FILL-EX-CLIP-1b")
    )
    position3 = repo._select_one("positions", "pos-clip")
    assert result3.state in ("filled", "partially_filled")
    assert abs(float(position3["quantity"]) - 212.0) < 1e-9
    assert position3["status"] == "open"


def test_clipped_close_exact_fill_replay_ten_times_stays_reduced(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    first = materialize_bitget_close("order-clip", _fill_rows(212.0))
    assert first.state == "filled_clip_partial_position"
    for _ in range(10):
        replay = materialize_bitget_close("order-clip", _fill_rows(212.0))
        assert replay.state == "filled"
        position = repo._select_one("positions", "pos-clip")
        assert position["status"] == "open"
        assert abs(float(position["quantity"]) - 212.0) < 1e-9


def test_clipped_close_two_batches_100_and_112_reduce_once_to_212(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    first = materialize_bitget_close("order-clip", _fill_rows(100.0))
    assert first.state == "partially_filled"
    position = repo._select_one("positions", "pos-clip")
    assert abs(float(position["quantity"]) - 324.0) < 1e-9

    second = materialize_bitget_close(
        "order-clip",
        _fill_rows(100.0) + _fill_rows(112.0, trade_id="FILL-EX-CLIP-1b"),
    )
    assert second.state == "filled_clip_partial_position"
    position = repo._select_one("positions", "pos-clip")
    assert position["status"] == "open"
    assert abs(float(position["quantity"]) - 212.0) < 1e-9
    events = db.execute(
        "SELECT closed_qty FROM position_close_events WHERE position_id='pos-clip' "
        "ORDER BY rowid"
    ).fetchall()
    assert [row["closed_qty"] for row in events] == [100.0, 112.0]


def test_remaining_quantity_matching_fill_closes_position(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=212)

    result = materialize_bitget_close("order-clip", _fill_rows(212.0))
    assert result.state == "filled"
    position = repo._select_one("positions", "pos-clip")
    assert position["status"] == "closed"
    assert abs(float(position["quantity"]) - 212.0) < 1e-9


def test_clipped_close_survives_crash_restart_replay(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    first = materialize_bitget_close("order-clip", _fill_rows(212.0))
    assert first.state == "filled_clip_partial_position"
    restart = materialize_bitget_close("order-clip", _fill_rows(212.0))
    assert restart.state == "filled"
    position = repo._select_one("positions", "pos-clip")
    assert position["status"] == "open"
    assert abs(float(position["quantity"]) - 212.0) < 1e-9

def test_marketable_limit_close_after_venue_market_path_cancel(tmp_path, monkeypatch):
    """After a match-cap cancel the retry becomes a bounded marketable limit."""
    _setup_db(tmp_path, monkeypatch)
    from types import SimpleNamespace

    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    # Release the seeded claim so a fresh close can be claimed, and record one
    # venue match-cap cancel in the position's close history.
    db.execute(
        "UPDATE positions SET pending_close_order_id=NULL, pending_close_status=NULL "
        "WHERE id='pos-clip'"
    )
    db.execute(
        "UPDATE orders SET status='cancelled', "
        "error_reason='trade_too_many_times_cancel' WHERE id='order-clip'"
    )

    captured: dict = {}

    class _Provider:
        async def get_ticker(self, instrument):
            captured["ticker"] = instrument
            return SimpleNamespace(last_price=1.5)

    monkeypatch.setattr("aletheia.data.ccxt_provider.get_market_data_provider", lambda **_kw: _Provider())
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: _CloseAdapter(captured))

    result = coordinator.close_position("pos-clip", reason="stop_loss")

    request = captured["order"]
    assert request.order_type == "limit"
    # Short close = venue buy: bound floors onto the reference tick (1.5 has
    # one decimal → 1.515 floors to 1.5, still inside [ref, ref×1.01]).
    assert request.limit_price == 1.5
    assert request.quantity == 424.0  # marketable limit carries the full remaining
    assert captured["ticker"].symbol == "XRP/USDT"
    assert captured["ticker"].exchange == "bitget"
    assert result["closed"] is False or result.get("order_id")


def test_ticker_failure_falls_back_to_clipped_market(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from types import SimpleNamespace

    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    db.execute(
        "UPDATE positions SET pending_close_order_id=NULL, pending_close_status=NULL "
        "WHERE id='pos-clip'"
    )
    db.execute(
        "UPDATE orders SET status='cancelled', "
        "error_reason='trade_too_many_times_cancel' WHERE id='order-clip'"
    )

    captured: dict = {}

    class _Provider:
        async def get_ticker(self, instrument):
            raise RuntimeError("ticker unavailable")

    monkeypatch.setattr("aletheia.data.ccxt_provider.get_market_data_provider", lambda **_kw: _Provider())
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: _CloseAdapter(captured))

    coordinator.close_position("pos-clip", reason="stop_loss")

    request = captured["order"]
    assert request.order_type == "market"
    assert request.quantity == 212.0  # halved once by the single venue cancel
    # The claimed row was aligned to the clip actually submitted.
    row = db.execute(
        "SELECT quantity FROM orders WHERE status='submitted' "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row["quantity"] == 212.0


def test_quantize_to_reference_precision_matches_venue_ticks():
    from aletheia.execution.coordinator import _quantize_to_reference_precision

    # XRPUSDT: tick 0.0001 via the reference's own precision.
    assert _quantize_to_reference_precision(1.512273, 1.4973, up=False) == 1.5122
    assert _quantize_to_reference_precision(1.482327, 1.4973, up=True) == 1.4824
    # BTCUSDT-scale reference (one decimal).
    assert _quantize_to_reference_precision(63984.611, 63351.1, up=False) == 63984.6
    # Whole-number reference keeps whole-number ticks.
    assert _quantize_to_reference_precision(2524.5, 2500.0, up=False) == 2524.0


def test_market_close_stays_market_without_cancel_history(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from types import SimpleNamespace

    from aletheia.execution import coordinator
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    db.execute(
        "UPDATE positions SET pending_close_order_id=NULL, pending_close_status=NULL "
        "WHERE id='pos-clip'"
    )

    captured: dict = {}

    class _Provider:
        async def get_ticker(self, instrument):
            raise AssertionError("ticker must not be consulted without cancel history")

    monkeypatch.setattr("aletheia.data.ccxt_provider.get_market_data_provider", lambda **_kw: _Provider())
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: _CloseAdapter(captured))

    coordinator.close_position("pos-clip", reason="stop_loss")

    request = captured["order"]
    assert request.order_type == "market"
    assert request.limit_price is None
    assert request.quantity == 424.0


def test_clipped_close_quantity_halves_per_venue_cancel(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.coordinator import _clipped_close_quantity
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip",
                              quantity=424)
    # One raw venue match-cap cancel + one owner-authorized transcription.
    db.execute(
        "INSERT INTO orders (id, proposal_id, client_order_id, position_id, "
        "close_reason, symbol, market_type, direction, order_type, quantity, "
        "price, execution_mode, status, source, error_reason, created_at) VALUES "
        "('c1','prop-clip','close-pos-clip','pos-clip','stop_loss','XRP/USDT',"
        "'usdt_futures','short','market',424,1.5,'bitget_demo','cancelled','native',"
        "'trade_too_many_times_cancel','2026-08-22T12:47:34+00:00'),"
        "('c2','prop-clip','close-pos-clip-r1','pos-clip','stop_loss','XRP/USDT',"
        "'usdt_futures','short','market',424,1.5,'bitget_demo','cancelled','native',"
        "'operator_authorized_cleanup_trade_rate_limited','2026-08-22T14:16:49+00:00')"
    )

    assert _clipped_close_quantity("pos-clip", 424.0) == 106.0

    # Six cancels reach the 1/64 floor; a seventh cannot shrink below it.
    db.execute(
        "INSERT INTO orders (id, proposal_id, client_order_id, position_id, "
        "close_reason, symbol, market_type, direction, order_type, quantity, "
        "price, execution_mode, status, source, error_reason, created_at) VALUES "
        "('c3','prop-clip','x3','pos-clip','stop_loss','XRP/USDT',"
        "'usdt_futures','short','market',424,1.5,'bitget_demo','cancelled','native',"
        "'trade_too_many_times_cancel','2026-08-22T15:00:00+00:00'),"
        "('c4','prop-clip','x4','pos-clip','stop_loss','XRP/USDT',"
        "'usdt_futures','short','market',424,1.5,'bitget_demo','cancelled','native',"
        "'trade_too_many_times_cancel','2026-08-22T15:10:00+00:00'),"
        "('c5','prop-clip','x5','pos-clip','stop_loss','XRP/USDT',"
        "'usdt_futures','short','market',424,1.5,'bitget_demo','cancelled','native',"
        "'trade_too_many_times_cancel','2026-08-22T15:20:00+00:00'),"
        "('c6','prop-clip','x6','pos-clip','stop_loss','XRP/USDT',"
        "'usdt_futures','short','market',424,1.5,'bitget_demo','cancelled','native',"
        "'trade_too_many_times_cancel','2026-08-22T15:30:00+00:00')"
    )
    floor = round(424.0 / 64.0, 8)
    assert _clipped_close_quantity("pos-clip", 424.0) == floor

    # Cancels of other classes must not shrink the clip.
    db.execute(
        "INSERT INTO orders (id, proposal_id, client_order_id, position_id, "
        "close_reason, symbol, market_type, direction, order_type, quantity, "
        "price, execution_mode, status, source, error_reason, created_at) VALUES "
        "('c7','prop-clip','x7','pos-clip','stop_loss','XRP/USDT',"
        "'usdt_futures','short','market',424,1.5,'bitget_demo','cancelled','native',"
        "'stale_close_order_absent_unfilled','2026-08-22T15:40:00+00:00')"
    )
    assert _clipped_close_quantity("pos-clip", 424.0) == floor


def test_clipped_close_materializes_realized_pnl_and_entry_fee(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(
        db, position_id="pos-clip", order_id="order-clip", quantity=424, market_type="spot"
    )
    db.execute("UPDATE positions SET entry_fee=4.24 WHERE id='pos-clip'")
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    result = materialize_bitget_close("order-clip", _fill_rows(212.0))

    assert result.state == "filled_clip_partial_position"
    position = repo._select_one("positions", "pos-clip")
    assert position["entry_fee"] == 2.12
    event = db.execute(
        "SELECT closed_qty, exit_price, realized_pnl, entry_fee, exit_fee "
        "FROM position_close_events WHERE position_id='pos-clip'"
    ).fetchone()
    assert event["closed_qty"] == 212.0
    assert event["exit_price"] == 1.48
    assert event["entry_fee"] == 2.12
    assert event["exit_fee"] == 0.2
    assert event["realized_pnl"] == -105.776
    audit = db.execute(
        "SELECT before_state, after_state FROM audit_events "
        "WHERE event_type='position_partial_close_materialized' AND entity_id='pos-clip'"
    ).fetchone()
    assert audit is not None
    assert json.loads(audit["before_state"])["quantity"] == 424.0
    assert json.loads(audit["after_state"])["quantity"] == 212.0


def test_futures_partial_close_keeps_pnl_unavailable_without_funding(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip", quantity=424)
    db.execute("UPDATE positions SET entry_fee=4.24 WHERE id='pos-clip'")
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    result = materialize_bitget_close("order-clip", _fill_rows(212.0))

    assert result.state == "filled_clip_partial_position"
    event = db.execute(
        "SELECT realized_pnl, entry_fee, exit_fee FROM position_close_events "
        "WHERE position_id='pos-clip'"
    ).fetchone()
    assert event["realized_pnl"] is None
    assert event["entry_fee"] == 2.12
    assert event["exit_fee"] == 0.2


def test_legacy_partial_fee_gap_stays_unavailable_and_is_not_reallocated(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip", quantity=424,
                              market_type="spot")
    db.execute("UPDATE positions SET quantity=318, entry_fee=4.24 WHERE id='pos-clip'")
    db.execute("UPDATE orders SET quantity=106 WHERE id='order-clip'")
    repo.insert_position_close_event({
        "id": "legacy-partial",
        "position_id": "pos-clip",
        "symbol": "XRP/USDT",
        "direction": "short",
        "execution_mode": "bitget_demo",
        "closed_qty": 106.0,
        "realized_pnl": None,
        "entry_fee": None,
        "exit_fee": 0.1,
        "is_partial": 1,
        "closed_at": "2026-08-22T14:30:00+00:00",
    })

    result = materialize_bitget_close("order-clip", _fill_rows(106.0))

    assert result.state == "filled_clip_partial_position"
    position = repo._select_one("positions", "pos-clip")
    assert position["quantity"] == 212.0
    assert position["entry_fee"] is None
    event = db.execute(
        "SELECT realized_pnl, entry_fee FROM position_close_events "
        "WHERE position_id='pos-clip' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert event["realized_pnl"] is None
    assert event["entry_fee"] is None


def test_partial_close_without_entry_fee_blocks_daily_loss_economics(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.account_loss import _partial_close_realized_today
    from aletheia.storage import repositories as repo

    now = datetime.now(timezone.utc).isoformat()
    repo.insert_position_close_event({
        "id": "partial-missing-economics",
        "position_id": "position-missing-economics",
        "symbol": "XRP/USDT",
        "direction": "short",
        "execution_mode": "bitget_demo",
        "closed_qty": 1.0,
        "realized_pnl": None,
        "is_partial": 1,
        "closed_at": now,
    })

    assert _partial_close_realized_today("bitget_demo", now[:10] + "T00:00:00+00:00") is None


def test_partial_close_with_unpriced_fee_keeps_economics_unavailable(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    _seed_position_with_claim(db, position_id="pos-clip", order_id="order-clip", quantity=424)
    db.execute("UPDATE positions SET entry_fee=4.24 WHERE id='pos-clip'")
    db.execute("UPDATE orders SET quantity=212 WHERE id='order-clip'")

    result = materialize_bitget_close("order-clip", _fill_rows(212.0, fee_coin="BGB"))

    assert result.state == "filled_clip_partial_position"
    event = db.execute(
        "SELECT realized_pnl, exit_fee FROM position_close_events WHERE position_id='pos-clip'"
    ).fetchone()
    assert event["realized_pnl"] is None
    assert event["exit_fee"] is None
