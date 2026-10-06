"""Spec: Pipeline — Position Manager (end-to-end)

E2E pipeline claims for mark-to-market, stop-loss, take-profit, max-hold exits,
ATR trailing stop, and audit persistence.

Contract: pipeline_position_manager
Claim prefix: PL-PM
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared DB setup
# ---------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
    """Wire a fresh isolated SQLite DB into aletheia storage for one test."""
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    return conn


def _insert_open_position(
    conn,
    symbol: str = "BTC/USDT",
    direction: str = "long",
    entry_price: float = 60000.0,
    quantity: float = 0.001,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    opened_at: str | None = None,
    execution_mode: str = "bitget_demo",
    market_type: str = "spot",
) -> str:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from tests.test_outcome_closure import _insert_entry_chain

    position = Position(
        symbol=symbol, market_type=market_type, direction=direction,
        quantity=quantity, entry_price=entry_price, current_price=entry_price,
        execution_mode=execution_mode, stop_price=stop_price,
        take_profit_price=take_profit_price,
        opened_at=opened_at or (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        entry_fee=0.0, entry_price_source="broker_fill", entry_fill_verified=True,
        funding_cost=0.0,
        funding_source="not_applicable" if market_type == "spot" else "broker_ledger",
    )
    position.opportunity_id, position.proposal_id, position.order_id = _insert_entry_chain(
        position_id=position.id, symbol=symbol, market_type=market_type,
        direction=direction, quantity=quantity, price=entry_price,
        execution_mode=execution_mode, opened_at=position.opened_at,
    )
    repo.insert_position(position)
    return position.id


def _prepare_close(monkeypatch, position_id, fill_price):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from tests.test_position_manager import _prepare_exchange_close

    return _prepare_exchange_close(
        monkeypatch, Position.model_validate(repo.get_position(position_id)), fill_price,
    )


def _confirm_close(conn, position_id, submitted):
    from aletheia.execution.reconciliation import reconcile

    assert len(submitted) == 1
    assert conn.execute("SELECT status FROM positions WHERE id=?", (position_id,)).fetchone()[0] == "open"
    reconcile()
    row = conn.execute("SELECT status,exit_price_source FROM positions WHERE id=?", (position_id,)).fetchone()
    assert row["status"] == "closed"
    assert row["exit_price_source"] == "broker_fill"


def _insert_snapshot(
    conn,
    symbol: str,
    price: float,
    *,
    market_type: str = "spot",
    exchange: str = "bitget",
    snapshot_at: str | None = None,
):
    snap_id = str(uuid.uuid4())
    now = snapshot_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots (id, symbol, market_type, exchange, price, "
        "volume_24h, change_pct_24h, snapshot_at, provider) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            snap_id, symbol, market_type, exchange, price,
            1_000_000.0, 0.0, now, "ccxt",
        ),
    )
    conn.commit()
    return snap_id


@spec_claim(
    SpecClaim(
        "PL-PM-10", "pipeline_position_manager", "§2 fresh typed protective mark", "L2b",
        "Protective closes require a fresh mark for the Position's exact market. "
        "A stale snapshot cannot create a close Order/Fill, and same-symbol spot "
        "and futures marks never cross-trigger each other.",
        status="implemented",
    ),
)
def test_stale_or_wrong_market_mark_never_creates_a_protective_close(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    stale_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    stale_id = _insert_open_position(
        conn,
        symbol="STALE/USDT",
        entry_price=100.0,
        stop_price=98.0,
    )
    _insert_snapshot(
        conn, "STALE/USDT", 90.0, snapshot_at=stale_at,
    )

    # Same canonical symbol, two active instruments.  Only the futures mark
    # breaches the stop; the spot mark stays safely above it.
    spot_id = _insert_open_position(
        conn,
        symbol="BTC/USDT",
        entry_price=100.0,
        stop_price=90.0,
        market_type="spot",
    )
    futures_id = _insert_open_position(
        conn,
        symbol="BTC/USDT",
        entry_price=100.0,
        stop_price=90.0,
        market_type="usdt_futures",
    )
    _insert_snapshot(conn, "BTC/USDT", 100.0, market_type="spot")
    _insert_snapshot(conn, "BTC/USDT", 80.0, market_type="perp")
    submitted = _prepare_close(monkeypatch, futures_id, 79.5)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    assert result["closed"] == 0
    _confirm_close(conn, futures_id, submitted)

    positions = {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT id,status,close_reason FROM positions WHERE id IN (?,?,?)",
            (stale_id, spot_id, futures_id),
        ).fetchall()
    }
    assert positions[stale_id]["status"] == "open"
    assert positions[spot_id]["status"] == "open"
    assert positions[futures_id]["status"] == "closed"
    assert any(
        item["position_id"] == stale_id
        and item["code"] == "protective_mark_unavailable"
        for item in result["errors"]
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (stale_id,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM fills f JOIN orders o ON o.id=f.order_id "
        "WHERE o.position_id=? AND o.close_reason IS NOT NULL",
        (stale_id,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-PM-11", "pipeline_position_manager", "§2 per-position close failure", "L2b",
        "A close exception is returned as a stable per-position error and counted; "
        "it never aborts evaluation of the remaining open Positions.",
        status="implemented",
    ),
)
@pytest.mark.parametrize("fail_all", [False, True])
def test_close_failures_are_reported_and_do_not_abort_other_positions(
    tmp_path, monkeypatch, fail_all
):
    conn = _setup_db(tmp_path, monkeypatch)
    first_id = _insert_open_position(
        conn, symbol="ETH/USDT", entry_price=100.0, stop_price=98.0,
    )
    second_id = _insert_open_position(
        conn, symbol="SOL/USDT", entry_price=100.0, stop_price=98.0,
    )
    _insert_snapshot(conn, "ETH/USDT", 90.0)
    _insert_snapshot(conn, "SOL/USDT", 90.0)

    from aletheia.portfolio import position_manager

    calls: list[str] = []

    def fake_close(position_id, *_args, **_kwargs):
        calls.append(position_id)
        if fail_all or position_id == first_id:
            raise RuntimeError("broker close failed")
        return {"closed": True}

    monkeypatch.setattr(position_manager, "close_position_routed", fake_close)
    result = position_manager.evaluate_open_positions()

    assert len(calls) == 2
    assert set(calls) == {first_id, second_id}
    expected_failures = 2 if fail_all else 1
    assert result["failed_close_count"] == expected_failures
    assert result["closed"] == (0 if fail_all else 1)
    assert len([
        item for item in result["errors"] if item["code"] == "close_failed"
    ]) == expected_failures


@spec_claim(
    SpecClaim(
        "PL-PM-12", "pipeline_position_manager", "§2 market-aware evaluation error", "L2b",
        "A real market-aware exit evaluation exception is exposed for its Position "
        "instead of being logged and reported as a healthy no-trigger cycle.",
        status="implemented",
    ),
)
def test_market_aware_evaluation_exception_is_returned_per_position(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    position_id = _insert_open_position(
        conn,
        symbol="ADA/USDT",
        entry_price=100.0,
        stop_price=50.0,
        take_profit_price=150.0,
    )
    _insert_snapshot(conn, "ADA/USDT", 100.0)

    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import position_manager

    cfg = AletheiaConfig()
    cfg.trading.market_aware_exits.enabled = True
    monkeypatch.setattr(position_manager, "load_config", lambda: cfg)

    from aletheia.portfolio.market_signal_reader import MarketSignalContext

    def exact_signal(requests, **_kwargs):
        request = requests[0]
        instrument = request.instrument
        return {
            request: MarketSignalContext(
                symbol=instrument.symbol,
                market_type=instrument.market_type,
                venue=instrument.exchange,
                direction_bias="short",
            )
        }

    monkeypatch.setattr(
        "aletheia.portfolio.market_signal_reader.get_market_signals_for_positions",
        exact_signal,
    )

    def fail_evaluation(*_args, **_kwargs):
        raise RuntimeError("market evaluator failed")

    monkeypatch.setattr(
        "aletheia.portfolio.exit_rules.evaluate_market_exit_rules",
        fail_evaluation,
    )

    result = position_manager.evaluate_open_positions()

    assert result["closed"] == 0
    assert result["failed_close_count"] == 0
    assert result["errors"] == [{
        "position_id": position_id,
        "symbol": "ADA/USDT",
        "market_type": "spot",
        "code": "market_aware_evaluation_failed",
        "error_type": "RuntimeError",
        "message": "market evaluator failed",
    }]


@spec_claim(
    SpecClaim(
        "PL-PM-13", "pipeline_position_manager", "§2 exact execution venue mark", "L2b",
        "Protective close evaluation resolves a Position's venue from execution_mode. "
        "OKX, Binance and Bitget marks never cross-trigger, regardless of the "
        "configured market-data venue.",
        status="implemented",
    ),
)
@pytest.mark.parametrize(
    "okx_price,binance_price,expected_demo_id",
    (
        (100.0, 80.0, "binance"),
        (80.0, 100.0, "okx"),
    ),
)
def test_execution_venue_marks_never_cross_trigger_external_positions(
    tmp_path, monkeypatch, okx_price, binance_price, expected_demo_id
):
    conn = _setup_db(tmp_path, monkeypatch)
    okx_id = _insert_open_position(
        conn,
        symbol="BTC/USDT",
        entry_price=100.0,
        stop_price=90.0,
        market_type="usdt_futures",
        execution_mode="okx_demo",
    )
    binance_id = _insert_open_position(
        conn,
        symbol="BTC/USDT",
        entry_price=100.0,
        stop_price=90.0,
        market_type="usdt_futures",
        execution_mode="binance_testnet",
    )
    bitget_id = _insert_open_position(
        conn,
        symbol="ETH/USDT",
        entry_price=100.0,
        stop_price=90.0,
        market_type="spot",
        execution_mode="bitget_demo",
    )
    _insert_snapshot(
        conn, "BTC/USDT", okx_price, market_type="perp", exchange="okx",
    )
    _insert_snapshot(
        conn, "BTC/USDT", binance_price, market_type="perp", exchange="binance",
    )
    # The Bitget position ignores the configured OKX venue and a breached Binance mark.
    _insert_snapshot(conn, "ETH/USDT", 100.0, exchange="bitget")
    _insert_snapshot(conn, "ETH/USDT", 80.0, exchange="okx")
    _insert_snapshot(conn, "ETH/USDT", 80.0, exchange="binance")

    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import position_manager

    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "okx"
    monkeypatch.setattr(position_manager, "load_config", lambda: cfg)
    close_calls: list[str] = []

    def fake_close(position_id, *_args, **_kwargs):
        close_calls.append(position_id)
        return {"closed": True}

    monkeypatch.setattr(position_manager, "close_position_routed", fake_close)
    result = position_manager.evaluate_open_positions()

    expected_id = okx_id if expected_demo_id == "okx" else binance_id
    assert close_calls == [expected_id]
    assert bitget_id not in close_calls
    assert result["closed"] == 1


# ---------------------------------------------------------------------------
# PL-PM-01  L2b  mark_to_market: price update → unrealized_pnl updated
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-01", "pipeline_position_manager", "§1 mark_to_market", "L2b",
        "After inserting an open position and a market snapshot at a new price, "
        "mark_positions_to_market updates current_price and unrealized_pnl; "
        "evaluation leaves the position open while neither stop nor target is reached",
    ),
)
def test_position_manager_mark_to_market(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    # Long position entered at 60000; current market = 62000 → unrealized profit
    pos_id = _insert_open_position(
        conn, symbol="BTC/USDT", direction="long",
        entry_price=60000.0, quantity=0.001,
        stop_price=55000.0,  # stop well below — should NOT trigger
        take_profit_price=70000.0,  # TP well above — should NOT trigger
    )
    _insert_snapshot(conn, "BTC/USDT", price=62000.0)

    # unrealized_pnl has exactly one source: the exchange's own mark (with
    # funding and mark-price basis). The local (price - entry) * qty estimate is
    # not used, so publish the venue view the same way the runtime cache does.
    from aletheia.portfolio import exchange_sync
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry",
        lambda: SimpleNamespace(
            configured_identities=lambda: (
                SimpleNamespace(execution_mode="bitget_demo"),
            ),
        ),
    )
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 1.0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "positions": [
            {
                "broker_id": "bitget_demo",
                "symbol": "BTC/USDT",
                "market_type": "spot",
                "side": "long",
                # 2.0 local estimate minus 0.25 funding: the venue value is the
                # fact, and the assertion below fails if the local maths returns.
                "unrealized_pnl": 1.75,
            }
        ],
    })

    from aletheia.portfolio.pnl import mark_positions_to_market
    mark_positions_to_market(persist=True)
    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    # Position should NOT have been closed
    assert result["closed"] == 0, (
        f"Position was closed unexpectedly: result={result}"
    )
    assert result["checked"] >= 1, "No positions were checked"

    pos_row = conn.execute(
        "SELECT status, current_price, unrealized_pnl, unrealized_pnl_source "
        "FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "open", (
        f"Position should remain open when price is between stop and TP. "
        f"status={pos_row['status']!r}"
    )
    assert pos_row["current_price"] == 62000.0
    assert pos_row["unrealized_pnl"] == pytest.approx(1.75)
    assert pos_row["unrealized_pnl_source"] == "exchange_exact"


# ---------------------------------------------------------------------------
# PL-PM-02  L2b  stop_loss_exit: price hits stop → position closed with stop_loss reason
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-02", "pipeline_position_manager", "§2 stop_loss_exit", "L2b",
        "When current_price <= stop_price for a long position, "
        "evaluation submits a close; private Fill reconciliation sets stop_loss and realized_pnl",
    ),
)
def test_position_manager_stop_loss_triggered(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    # Suppress notification side effects
    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    pos_id = _insert_open_position(
        conn, symbol="ETH/USDT", direction="long",
        entry_price=3000.0, quantity=0.01,
        stop_price=2900.0,  # stop at 2900
    )
    # Market price drops below stop
    _insert_snapshot(conn, "ETH/USDT", price=2850.0)

    submitted = _prepare_close(monkeypatch, pos_id, 2849.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    assert result["closed"] == 0
    _confirm_close(conn, pos_id, submitted)

    pos_row = conn.execute(
        "SELECT status, close_reason, realized_pnl FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "closed", (
        f"Position status must be 'closed' after stop_loss, got {pos_row['status']!r}"
    )
    assert pos_row["close_reason"] == "stop_loss", (
        f"Expected close_reason='stop_loss', got {pos_row['close_reason']!r}"
    )
    assert pos_row["realized_pnl"] is not None, (
        "realized_pnl must be set after stop_loss exit"
    )
    # Long entered at 3000, stopped at 2900 → negative PnL
    assert float(pos_row["realized_pnl"]) < 0, (
        f"Expected negative PnL for stop_loss on long, got {pos_row['realized_pnl']}"
    )


# ---------------------------------------------------------------------------
# PL-PM-03  L2b  take_profit_exit: price hits TP → closed with take_profit reason
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-03", "pipeline_position_manager", "§2 take_profit_exit", "L2b",
        "When current_price >= take_profit_price for a long position, "
        "evaluation submits a close; private Fill reconciliation sets take_profit and realized_pnl",
    ),
)
def test_position_manager_take_profit_triggered(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    pos_id = _insert_open_position(
        conn, symbol="SOL/USDT", direction="long",
        entry_price=150.0, quantity=1.0,
        stop_price=140.0,       # stop well below
        take_profit_price=170.0,  # TP at 170
    )
    # Market price rises above TP
    _insert_snapshot(conn, "SOL/USDT", price=175.0)

    submitted = _prepare_close(monkeypatch, pos_id, 174.5)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    assert result["closed"] == 0
    _confirm_close(conn, pos_id, submitted)

    pos_row = conn.execute(
        "SELECT status, close_reason, realized_pnl FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "closed", (
        f"Expected status='closed', got {pos_row['status']!r}"
    )
    assert pos_row["close_reason"] == "take_profit", (
        f"Expected close_reason='take_profit', got {pos_row['close_reason']!r}"
    )
    # Long entered at 150, took profit at 170 → positive PnL
    assert float(pos_row["realized_pnl"]) > 0, (
        f"Expected positive PnL for take_profit on long, got {pos_row['realized_pnl']}"
    )


@spec_claim(
    SpecClaim(
        "PL-PM-09",
        "pipeline_position_manager",
        "§2 immutable native protection authority",
        "L2b",
        "A native Proposal with no approved stop-loss or take-profit must not gain "
        "either exit later from mutable config defaults.",
        status="implemented",
    )
)
def test_native_proposal_without_protection_does_not_inherit_mutable_config(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio.position_manager import _decide_exit
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="bitget_demo",
        stop_price=None,
        take_profit=None,
        status="executed",
    )
    repo.insert_trade_proposal(proposal)
    position = {
        "id": "native-no-protection",
        "proposal_id": proposal.id,
        "source": "generated",
        "symbol": proposal.symbol,
        "direction": proposal.direction,
        "entry_price": proposal.price,
        "stop_price": None,
        "take_profit_price": None,
        "trade_school": "short_term",
    }
    cfg = AletheiaConfig()
    cfg.trading.default_stop_loss_pct = 2.0
    cfg.trading.default_take_profit_pct = 4.0
    cfg.trading.max_position_hold_hours = 0

    assert _decide_exit(position, price=97.0, cfg=cfg) is None
    assert _decide_exit(position, price=105.0, cfg=cfg) is None

    cfg.trading.default_stop_loss_pct = 20.0
    cfg.trading.default_take_profit_pct = 40.0
    assert _decide_exit(position, price=97.0, cfg=cfg) is None
    assert _decide_exit(position, price=105.0, cfg=cfg) is None


# ---------------------------------------------------------------------------
# PL-PM-04  L2b  max_hold_exit: position age > max_hold → closed with max_hold reason
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-04", "pipeline_position_manager", "§3 max_hold_exit", "L2b",
        "A position exceeding max_position_hold_hours submits an external close; "
        "private Fill reconciliation records close_reason='max_hold'",
    ),
)
def test_position_manager_max_hold_triggered(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    # Set max_position_hold_hours = 2 in config
    import aletheia.portfolio.position_manager as pm_mod
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.trading.max_position_hold_hours = 2  # 2-hour max hold
    monkeypatch.setattr(pm_mod, "load_config", lambda: cfg)

    # Position opened 3 hours ago (exceeds limit)
    old_opened = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    pos_id = _insert_open_position(
        conn, symbol="ADA/USDT", direction="long",
        entry_price=0.50, quantity=100.0,
        stop_price=0.40,
        take_profit_price=0.65,
        opened_at=old_opened,
    )
    # Market price between stop and TP (mechanical exits won't fire)
    _insert_snapshot(conn, "ADA/USDT", price=0.52)

    submitted = _prepare_close(monkeypatch, pos_id, 0.519)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    assert result["closed"] == 0
    _confirm_close(conn, pos_id, submitted)

    pos_row = conn.execute(
        "SELECT status, close_reason FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "closed", (
        f"Expected status='closed', got {pos_row['status']!r}"
    )
    assert pos_row["close_reason"] == "max_hold", (
        f"Expected close_reason='max_hold', got {pos_row['close_reason']!r}"
    )


# ---------------------------------------------------------------------------
# PL-PM-05  L2b  atr_observation
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-05", "pipeline_position_manager", "§4 atr_trailing_stop", "L2b",
        "ATR trailing resolves one causally available completed 1h observation "
        "for the Position's exact venue, symbol, and physical market",
        status="implemented",
    ),
)
def test_atr_observation_has_exact_causal_instrument_identity(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    observed = datetime(2026, 8, 11, 3, 30, tzinfo=timezone.utc)
    assets = {}

    def insert_observation(*, venue, market, atr, snapshot_at, bar_close):
        asset_id = assets.get((venue, market))
        if asset_id is None:
            asset_id = str(uuid.uuid4())
            assets[(venue, market)] = asset_id
            conn.execute(
                "INSERT INTO universe_registry "
                "(id, venue, symbol, market_type, status) VALUES (?,?,?,?,?)",
                (asset_id, venue, "BTC/USDT", market, "active"),
            )
        manifest = json.dumps({
            "1h": {
                "bar_close_ts": int(bar_close.timestamp() * 1000),
                "input_fingerprint": f"{venue}-{market}-{atr}",
            }
        })
        snapshot_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO universe_feature_snapshots "
            "(id, asset_id, ts, atr_pct, technical_algorithm_version, "
            "technical_snapshot_manifest) VALUES (?,?,?,?,?,?)",
            (
                snapshot_id,
                asset_id,
                snapshot_at.isoformat(),
                atr,
                "technical-context-v1",
                manifest,
            ),
        )
        return snapshot_id

    expected_id = insert_observation(
        venue="bitget", market="spot", atr=2.5,
        snapshot_at=observed - timedelta(minutes=5),
        bar_close=observed - timedelta(minutes=30),
    )
    insert_observation(
        venue="bitget", market="perp", atr=7.5,
        snapshot_at=observed - timedelta(minutes=2),
        bar_close=observed - timedelta(minutes=30),
    )
    insert_observation(
        venue="binance", market="spot", atr=8.5,
        snapshot_at=observed - timedelta(minutes=1),
        bar_close=observed - timedelta(minutes=30),
    )
    insert_observation(
        venue="bitget", market="spot", atr=99.9,
        snapshot_at=observed + timedelta(minutes=1),
        bar_close=observed + timedelta(minutes=30),
    )
    conn.commit()

    from aletheia.portfolio.position_manager import _get_atr_observation

    result = _get_atr_observation(
        {
            "execution_mode": "bitget_demo",
            "symbol": "BTC/USDT",
            "market_type": "spot",
        },
        SimpleNamespace(market_data=SimpleNamespace(ccxt_exchange="bitget")),
        observed_at=observed.isoformat(),
    )

    assert result is not None
    assert result["snapshot_id"] == expected_id
    assert result["atr_pct"] == 2.5
    assert result["bar_close_ms"] <= int(observed.timestamp() * 1000)
    assert result["venue"] == "bitget"
    assert result["market_type"] == "spot"


def test_atr_observation_storage_error_uses_frozen_fallback(monkeypatch):
    import sqlite3

    from aletheia.portfolio.position_manager import _get_atr_observation

    class UnavailableDb:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("aletheia.storage.sqlite.get_db", lambda: UnavailableDb())

    result = _get_atr_observation(
        {
            "execution_mode": "bitget_demo",
            "symbol": "BTC/USDT",
            "market_type": "spot",
        },
        SimpleNamespace(market_data=SimpleNamespace(ccxt_exchange="okx")),
        observed_at="2026-08-11T03:30:00+00:00",
    )

    assert result is None


# ---------------------------------------------------------------------------
# PL-PM-06  L2b  audit_events_table
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-06", "pipeline_position_manager", "§5 audit_events_table", "L2b",
        "close_position writes exactly one audit_events row with "
        "event_type='position_closed' in addition to the JSONL audit file",
        status="implemented",  # write_audit calls _write_audit_row → insert_audit_event
    ),
)
def test_close_position_writes_audit_event_row(tmp_path, monkeypatch):
    """Verify close_position persists to audit_events (not just JSONL).

    write_audit() in jsonl.py calls _write_audit_row() → insert_audit_event().
    This test confirms the SQLite path is wired for position_closed events.

    Note: PM-2 was originally reported as 'close_position only writes JSONL'.
    After code review, write_audit() does call _write_audit_row(), so this is
    expected to PASS. If it fails, the SQLite audit path is broken.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    # Reset JSONL hash state
    try:
        import aletheia.files.jsonl as jsonl_mod
        jsonl_mod._last_audit_hash = None
    except Exception:
        pass

    pos_id = _insert_open_position(
        conn, symbol="BTC/USDT", direction="long",
        entry_price=60000.0, quantity=0.001,
        stop_price=55000.0,
    )

    from aletheia.portfolio.pnl import close_position
    close_position(pos_id, exit_price=58000.0, reason="stop_loss")

    audit_rows = conn.execute(
        "SELECT * FROM audit_events WHERE event_type='position_closed' AND entity_id=?",
        (pos_id,),
    ).fetchall()

    assert len(audit_rows) == 1, (
        "Expected exactly one audit_events row with event_type='position_closed' "
        f"for position {pos_id!r}, found {len(audit_rows)}. PM-2 drift means "
        "the SQLite audit is missing or the post-commit JSONL write mirrored a duplicate."
    )


# ---------------------------------------------------------------------------
# PL-PM-07  L2c  paused_vs_protective_close  (pause blocks opening, not managing)
# ---------------------------------------------------------------------------




@spec_claim(
    SpecClaim(
        "PL-PM-07", "pipeline_position_manager", "§6 paused_vs_protective_close", "L2c",
        "runtime.paused must NOT block protective exits: the scheduler's "
        "_run_position_manager still submits a protective close when paused, and "
        "private Fill reconciliation confirms the close. "
        "Pause stops opening new risk, it never blocks managing/closing existing risk "
        "(aligned with kill_switch, which also never blocks closes — execution_contract §10).",
        status="implemented",
    ),
)
def test_paused_does_not_block_stop_loss(tmp_path, monkeypatch):
    """A paused system must still fire stop-losses. Mirrors PL-PM-02 but drives the
    scheduler entry point _run_position_manager() with runtime.paused=True.

    Before the fix (scheduler paused early-return) the position stays open (RED);
    after aligning paused to kill_switch semantics it closes (GREEN). This locks the
    invariant against re-introducing the 'pause freezes stop-losses' landmine.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    pos_id = _insert_open_position(
        conn, symbol="ETH/USDT", direction="long",
        entry_price=3000.0, quantity=0.01,
        stop_price=2900.0,
    )
    # Market price drops below the stop → a protective close MUST fire even when paused.
    _insert_snapshot(conn, "ETH/USDT", price=2850.0)

    # System is PAUSED.
    import aletheia.agent_runtime.scheduler as sched
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.runtime.paused = True
    monkeypatch.setattr(sched, "load_config", lambda: cfg)

    # Drive the scheduler's protective-loop entry point (not evaluate_open_positions
    # directly) — the paused gate lived here (scheduler.py:_run_position_manager).
    submitted = _prepare_close(monkeypatch, pos_id, 2849.0)
    sched._run_position_manager()
    _confirm_close(conn, pos_id, submitted)

    pos_row = conn.execute(
        "SELECT status, close_reason FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "closed", (
        "paused must NOT block a protective stop-loss close — pause stops opening "
        f"new risk, never managing existing risk. status={pos_row['status']!r}"
    )
    assert pos_row["close_reason"] == "stop_loss", (
        f"Expected close_reason='stop_loss', got {pos_row['close_reason']!r}"
    )


def test_scheduler_logs_safe_position_manager_error_facts(monkeypatch):
    import aletheia.agent_runtime.scheduler as sched
    import aletheia.portfolio.position_manager as position_manager

    monkeypatch.setattr(
        position_manager,
        "evaluate_open_positions",
        lambda: {
            "errors": [{
                "position_id": "pos-1",
                "code": "protective_mark_unavailable",
                "error_type": "RuntimeError",
                "message": "do-not-log-provider-detail",
            }, {
                "instrument": "binance:BTC/USDT:perp",
                "code": "protective_ohlcv_refresh_failed",
                "message": "do-not-log-provider-detail",
            }],
        },
    )
    warnings = []
    monkeypatch.setattr(sched.logger, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(sched, "_record_job_failure", lambda *args: None)

    sched._run_position_manager()

    assert warnings == [(
        "position_manager completed with %d reported errors: %s",
        2,
        [
            {
                "code": "protective_mark_unavailable",
                "position_id": "pos-1",
                "error_type": "RuntimeError",
            },
            {
                "code": "protective_ohlcv_refresh_failed",
                "instrument": "binance:BTC/USDT:perp",
            },
        ],
    )]


def test_scheduler_does_not_fail_on_pending_close_waiting_observation(monkeypatch):
    import aletheia.agent_runtime.scheduler as sched
    import aletheia.portfolio.position_manager as position_manager

    monkeypatch.setattr(
        position_manager,
        "evaluate_open_positions",
        lambda: {
            "errors": [],
            "observations": [{"code": "pending_close_protection_waiting"}],
        },
    )
    failures = []
    monkeypatch.setattr(sched, "_record_job_failure", lambda *args: failures.append(args))

    sched._run_position_manager()

    assert failures == []


@spec_claim(
    SpecClaim(
        "PL-PM-14",
        "pipeline_position_manager",
        "§2 market-aware owner-visible semantics",
        "L2a",
        "Settings describes market-aware handling as an Operator wake/review and "
        "does not claim that the evaluator directly closes positions or mutates stops; "
        "failed daemon delivery is recorded instead of counted as a successful wake.",
        status="implemented",
    ),
)
def test_market_aware_settings_describes_wake_only_authority():
    from pathlib import Path

    settings = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "settings"
        / "execution"
        / "PaperTradingSettings.tsx"
    ).read_text(encoding="utf-8")

    assert "Market-Aware Position Review" in settings
    assert "wakes the System Operator" in settings
    assert "does not close positions or mutate stops" in settings
    assert "may force-close losing positions early" not in settings

    manager = (
        Path(__file__).resolve().parents[2]
        / "core"
        / "aletheia"
        / "portfolio"
        / "position_manager.py"
    ).read_text(encoding="utf-8")
    assert 'record_error(p, "operator_wake_unavailable")' in manager
    assert 'last_market_signal_action=f"wake_failed:{action.action}"' in manager


@spec_claim(
    SpecClaim(
        "PL-PM-15", "pipeline_position_manager", "§2 pending close convergence", "L2b",
        "A stale unresolved exchange close remains retry-suppressing, emits one durable "
        "reconciliation-required incident, and never creates a replacement close Order.",
        status="implemented",
    ),
)
@pytest.mark.parametrize(
    "pending_close_status",
    ("submission_unknown", "position_absence_unverified"),
)
def test_stale_pending_close_escalates_without_creating_a_replacement(
    tmp_path, monkeypatch, pending_close_status,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.position_manager import evaluate_open_positions
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=100.0, execution_mode="bitget_demo",
        pending_close_order_id="unresolved-close",
        pending_close_status=pending_close_status,
        pending_close_requested_at=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
    )
    repo.insert_position(position)

    result = evaluate_open_positions()

    assert result["closed"] == 0
    assert any(error["code"] == "pending_close_reconciliation_required"
               for error in result["errors"])
    assert repo._select_one("positions", position.id)["pending_close_order_id"] == "unresolved-close"
    assert repo._select_all("orders", where="position_id=?", params=(position.id,), limit=10) == []
