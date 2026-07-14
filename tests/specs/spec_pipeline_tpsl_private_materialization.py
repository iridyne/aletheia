"""Bitget TPSL trigger-to-private-fill materialization contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


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


def _seed_position(execution_mode: str, *, suffix: str, plan_ids: dict):
    from tests.execution_authority import seed_order_authority
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position

    proposal, decision = seed_order_authority(
        proposal_id=f"proposal-tpsl-{suffix}",
        risk_decision_id=f"risk-tpsl-{suffix}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60_000.0,
        execution_mode=execution_mode,
        leverage=5,
    )
    entry_order = Order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=execution_mode,
        exchange_order_id=f"ENTRY-TPSL-{suffix}",
        status="filled",
        filled_at="2026-07-13T00:00:00+00:00",
    )
    repo.insert_order(entry_order)
    repo.insert_fill(Fill(
        id=f"ENTRY-FILL-TPSL-{suffix}",
        order_id=entry_order.id,
        fill_price=60_000.0,
        fill_quantity=0.01,
        fee=0.1,
        fee_source="reconciled",
        filled_at="2026-07-13T00:00:00+00:00",
    ))
    position = Position(
        proposal_id=proposal.id,
        order_id=entry_order.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        quantity=0.01,
        entry_price=60_000.0,
        current_price=60_000.0,
        entry_fee=0.1,
        execution_mode=execution_mode,
        leverage=5,
        entry_fill_verified=True,
        entry_price_source="broker_fill",
        exchange_tpsl_order_id=json.dumps(plan_ids),
        opened_at="2026-07-13T00:00:00+00:00",
    )
    repo.insert_position(position)
    repo.update_order(entry_order.id, position_id=position.id)
    repo.update_trade_proposal(proposal.id, status="executed")
    return position


def _private_close_fill(exchange_order_id: str) -> dict:
    return {
        "orderId": exchange_order_id,
        "tradeId": f"FILL-{exchange_order_id}",
        "fillPrice": "59000.0",
        "fillQuantity": "0.01",
        "fee": "0.2",
        "feeCoin": "USDT",
        "cTime": "1784050000000",
    }


@spec_claim(
    SpecClaim(
        "PL-TPSL-01",
        "pipeline_tpsl_private_materialization",
        "§1 demo trigger bridge",
        "L2b",
        "An exact executed demo TPSL plan registers its executeOrderId as a native "
        "close Order and closes only from exact private Fill evidence.",
        status="implemented",
    )
)
def test_demo_tpsl_uses_execute_order_private_fill_not_plan_price(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_demo", suffix="demo", plan_ids={"sl": "PLAN-SL", "tp": "PLAN-TP"}
    )
    calls: list[tuple] = []

    class _Client:
        def __init__(self, mode):
            calls.append(("client", mode))

        async def get_plan_order_history(self, *, order_id, **kwargs):
            calls.append(("plan", order_id))
            if order_id == "PLAN-SL":
                return [{
                    "orderId": "PLAN-SL",
                    "planStatus": "executed",
                    "executeOrderId": "EXEC-SL",
                    # These plan-level prices must never become close economics.
                    "executePrice": "1.0",
                    "triggerPrice": "2.0",
                }]
            return [{
                "orderId": "PLAN-TP",
                "planStatus": "not_trigger",
                "executeOrderId": "",
            }]

        async def get_order_fills(self, *, order_id, symbol, market_type):
            calls.append(("fills", order_id, symbol, market_type))
            return [_private_close_fill(order_id)]

        async def place_order(self, *args, **kwargs):
            raise AssertionError("TPSL observation must never place an order")

        async def cancel_order(self, *args, **kwargs):
            raise AssertionError("TPSL observation must never cancel an order")

        async def close(self):
            calls.append(("close",))

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    scheduler._tpsl_fill_check_body()

    stored_position = repo._select_one("positions", position.id)
    close_orders = db.execute(
        "SELECT * FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchall()
    assert len(close_orders) == 1
    close_order = close_orders[0]
    close_fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()
    assert close_order["exchange_order_id"] == "EXEC-SL"
    assert close_order["status"] == "filled"
    assert close_order["close_reason"] == "stop_loss"
    assert close_fill["fill_price"] == pytest.approx(59_000.0)
    assert close_fill["fee"] == pytest.approx(0.2)
    assert stored_position["status"] == "closed"
    assert stored_position["current_price"] == pytest.approx(59_000.0)
    assert stored_position["closed_at"] == datetime.fromtimestamp(
        1_784_050_000, tz=timezone.utc
    ).isoformat()
    assert ("client", "demo") in calls
    assert ("fills", "EXEC-SL", "BTCUSDT", "usdt_futures") in calls

    scheduler._tpsl_fill_check_body()
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 1


@spec_claim(
    SpecClaim(
        "PL-TPSL-02",
        "pipeline_tpsl_private_materialization",
        "§2 live observed-trigger convergence",
        "L2c",
        "An exact executed live TPSL plan converges the local Position only from its "
        "unique executeOrderId and exact private fills, without any trading write call.",
        status="implemented",
    )
)
def test_live_tpsl_converges_from_exact_private_fills_without_trading_calls(
    tmp_path, monkeypatch,
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_live_limited",
        suffix="live",
        plan_ids={"sl": "PLAN-LIVE-SL"},
    )
    calls: list[tuple] = []

    class _Client:
        def __init__(self, mode):
            calls.append(("client", mode))

        async def get_plan_order_history(self, *, order_id, **kwargs):
            calls.append(("plan", order_id))
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-LIVE-SL",
            }]

        async def get_order_fills(self, *, order_id, symbol, market_type):
            calls.append(("fills", order_id, symbol, market_type))
            return [_private_close_fill(order_id)]

        async def place_order(self, *args, **kwargs):
            raise AssertionError("TPSL observation must never place an order")

        async def place_tpsl_order(self, *args, **kwargs):
            raise AssertionError("TPSL observation must never place a TPSL order")

        async def cancel_order(self, *args, **kwargs):
            raise AssertionError("TPSL observation must never cancel an order")

        async def cancel_plan_order(self, *args, **kwargs):
            raise AssertionError("TPSL observation must never cancel a TPSL plan")

        async def close(self):
            calls.append(("close",))

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    scheduler._tpsl_fill_check_body()

    stored_position = repo._select_one("positions", position.id)
    close_orders = db.execute(
        "SELECT * FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchall()
    assert len(close_orders) == 1
    close_order = close_orders[0]
    close_fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()
    assert close_order["execution_mode"] == "bitget_live_limited"
    assert close_order["exchange_order_id"] == "EXEC-LIVE-SL"
    assert close_order["status"] == "filled"
    assert close_order["close_reason"] == "stop_loss"
    assert close_fill["fill_price"] == pytest.approx(59_000.0)
    assert close_fill["fee"] == pytest.approx(0.2)
    assert stored_position["status"] == "closed"
    assert stored_position["current_price"] == pytest.approx(59_000.0)
    assert calls == [
        ("client", "live"),
        ("plan", "PLAN-LIVE-SL"),
        ("fills", "EXEC-LIVE-SL", "BTCUSDT", "usdt_futures"),
        ("close",),
    ]

    scheduler._tpsl_fill_check_body()
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "history",
    (
        pytest.param(
            [{"orderId": "PLAN-X", "planStatus": "executed"}],
            id="missing-execute-order-id",
        ),
        pytest.param(
            [{
                "orderId": "OTHER-PLAN",
                "planStatus": "executed",
                "executeOrderId": "EXEC-X",
            }],
            id="wrong-plan-id",
        ),
        pytest.param(
            [{
                "orderId": "PLAN-X",
                "status": "executed",
                "executeOrderId": "EXEC-X",
            }],
            id="legacy-status-field-not-authority",
        ),
        pytest.param(
            [
                {
                    "orderId": "PLAN-X",
                    "planStatus": "executed",
                    "executeOrderId": "EXEC-X-1",
                },
                {
                    "orderId": "PLAN-X",
                    "planStatus": "executed",
                    "executeOrderId": "EXEC-X-2",
                },
            ],
            id="ambiguous-execute-order-id",
        ),
    ),
)
@spec_claim(
    SpecClaim(
        "PL-TPSL-03",
        "pipeline_tpsl_private_materialization",
        "§3 incomplete trigger identity",
        "L2c",
        "Missing, wrong, legacy, or ambiguous TPSL execution identity leaves the "
        "Position open and creates no close Order.",
        status="implemented",
    )
)
def test_incomplete_or_ambiguous_plan_identity_stays_open(
    tmp_path, monkeypatch, history
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_demo", suffix="invalid", plan_ids={"sl": "PLAN-X"}
    )

    class _Client:
        def __init__(self, mode):
            assert mode == "demo"

        async def get_plan_order_history(self, **kwargs):
            return history

        async def get_order_fills(self, **kwargs):
            raise AssertionError("invalid plan identity must not query fills")

        async def close(self):
            pass

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    scheduler._tpsl_fill_check_body()

    assert repo._select_one("positions", position.id)["status"] == "open"
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-TPSL-04",
        "pipeline_tpsl_private_materialization",
        "§4 private query failure",
        "L2c",
        "After an exact demo trigger is claimed, unavailable or incomplete private "
        "fills keep the Position open and the close Order unresolved; ordinary "
        "reconciliation later retries that exact executeOrderId and closes only from "
        "complete private evidence.",
        status="implemented",
    )
)
def test_private_fill_query_failure_keeps_claim_open(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_demo", suffix="query-fail", plan_ids={"tp": "PLAN-TP-X"}
    )
    fills_available = False
    exact_fill_calls: list[tuple[str, str, str]] = []

    class _Client:
        def __init__(self, mode):
            assert mode == "demo"

        async def get_plan_order_history(self, **kwargs):
            return [{
                "orderId": "PLAN-TP-X",
                "planStatus": "executed",
                "executeOrderId": "EXEC-TP-X",
            }]

        async def get_order_fills(self, **kwargs):
            exact_fill_calls.append((
                kwargs["order_id"], kwargs["symbol"], kwargs["market_type"]
            ))
            if not fills_available:
                raise RuntimeError("private history incomplete")
            return [_private_close_fill(kwargs["order_id"])]

        async def get_open_orders(self, **kwargs):
            return []

        async def get_fills(self, **kwargs):
            # The recovery proof is intentionally absent from generic latest-fill
            # history; reconciliation must use the exact executeOrderId reader.
            return []

        async def close(self):
            pass

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    scheduler._tpsl_fill_check_body()

    stored = repo._select_one("positions", position.id)
    close_order = db.execute(
        "SELECT * FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()
    assert stored["status"] == "open"
    assert stored["pending_close_order_id"] == close_order["id"]
    assert close_order["exchange_order_id"] == "EXEC-TP-X"
    assert close_order["status"] == "materialization_missing"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 0

    fills_available = True
    from aletheia.execution.reconciliation import reconcile

    result = reconcile()
    recovered_position = repo._select_one("positions", position.id)
    recovered_order = repo.get_order(close_order["id"])
    recovered_fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()

    assert result["filled"] == 1
    assert recovered_position["status"] == "closed"
    assert recovered_order["status"] == "filled"
    assert recovered_fill["fill_price"] == pytest.approx(59_000.0)
    assert exact_fill_calls == [
        ("EXEC-TP-X", "BTCUSDT", "usdt_futures"),
        ("EXEC-TP-X", "BTCUSDT", "usdt_futures"),
    ]


@spec_claim(
    SpecClaim(
        "PL-TPSL-05",
        "pipeline_tpsl_private_materialization",
        "§5 scheduler partial-failure health",
        "L2c",
        "During layered pause, one TPSL private-history failure remains visible in "
        "scheduler health without blocking another plan from materializing; a later "
        "fully successful pass clears the consecutive failure count.",
        status="implemented",
    )
)
def test_scheduler_tpsl_partial_failure_stays_red_and_recovers_while_paused(
    monkeypatch,
):
    from aletheia.agent_runtime import scheduler
    from aletheia.execution import exchange_materialization
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.learning import control
    from aletheia.storage import repositories, sqlite

    positions = [
        {
            "id": "pos-fail",
            "symbol": "BTC/USDT",
            "market_type": "usdt_futures",
            "execution_mode": "bitget_demo",
            "exchange_tpsl_order_id": {"sl": "PLAN-FAIL"},
        },
        {
            "id": "pos-ok",
            "symbol": "ETH/USDT",
            "market_type": "usdt_futures",
            "execution_mode": "bitget_demo",
            "exchange_tpsl_order_id": {"tp": "PLAN-OK"},
        },
    ]
    fail_private_query = True
    plan_calls: list[str] = []
    materialized: list[str] = []
    failures: list[tuple[str, str]] = []

    class _Client:
        def __init__(self, mode):
            assert mode == "demo"

        async def get_plan_order_history(self, *, order_id, **_kwargs):
            plan_calls.append(order_id)
            if order_id == "PLAN-FAIL" and fail_private_query:
                raise RuntimeError("private plan history unavailable")
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": f"EXEC-{order_id}",
            }]

        async def get_order_fills(self, **_kwargs):
            return [{"tradeId": "private-fill"}]

        async def close(self):
            return None

    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=True)),
    )
    monkeypatch.setattr(repositories, "list_all_open_positions", lambda: positions)
    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "_claim_observed_tpsl_close",
        lambda position_id, **_kwargs: ({"id": f"close-{position_id}"}, True),
    )

    def materialize(close_order_id, _fills):
        materialized.append(close_order_id)
        return SimpleNamespace(state="filled")

    monkeypatch.setattr(
        exchange_materialization, "materialize_bitget_close", materialize
    )
    monkeypatch.setattr(
        scheduler, "_run_sync_with_timeout", lambda _job, fn: fn()
    )
    monkeypatch.setattr(control, "is_autonomous_learning_job", lambda _job: False)
    monkeypatch.setattr(control, "record_learning_job_success", lambda _job: None)
    monkeypatch.setattr(
        control,
        "record_learning_job_failure",
        lambda job_id, error: failures.append((job_id, error)),
    )
    monkeypatch.setattr(sqlite, "rollback_if_in_transaction", lambda: False)
    monkeypatch.setattr(sqlite, "close_db", lambda: None)

    scheduler._consecutive_failures.clear()
    first = scheduler._run_job_guarded(
        "tpsl_fill_check", scheduler._run_tpsl_fill_check
    )

    assert plan_calls == ["PLAN-FAIL", "PLAN-OK"]
    assert materialized == ["close-pos-ok"]
    assert first["failed_count"] == 1
    assert len(first["materialized"]) == 1
    assert scheduler._consecutive_failures["tpsl_fill_check"] == 1
    assert failures == [
        ("tpsl_fill_check", "tpsl_private_reconciliation_failed")
    ]

    fail_private_query = False
    positions[:] = [positions[1]]
    second = scheduler._run_job_guarded(
        "tpsl_fill_check", scheduler._run_tpsl_fill_check
    )

    assert second["failed_count"] == 0
    assert scheduler._consecutive_failures["tpsl_fill_check"] == 0
