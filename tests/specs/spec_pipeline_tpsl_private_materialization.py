"""Bitget TPSL trigger-to-private-fill materialization contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from aletheia.uta.compat import utc
from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.specs.uta_private_history import _identity


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


def _seed_position(
    execution_mode: str,
    *,
    suffix: str,
    plan_ids: dict,
    opened_at: str = "2026-07-13T00:00:00+00:00",
):
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
        uta_account_identity_json=_identity(execution_mode).model_dump_json(),
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
        opened_at=opened_at,
    )
    repo.insert_position(position)
    repo.update_order_metadata(entry_order.id, position_id=position.id)
    repo.update_trade_proposal(proposal.id, status="executed")
    return position


def _private_close_fill(exchange_order_id: str, *, filled_at_ms: int = 1_784_050_000_000) -> dict:
    return {
        "orderId": exchange_order_id,
        "tradeId": f"FILL-{exchange_order_id}",
        "fillPrice": "59000.0",
        "fillQuantity": "0.01",
        "fee": "0.2",
        "feeCoin": "USDT",
        "cTime": str(filled_at_ms),
    }


def _patch_configured_reader(monkeypatch, *identities):
    """Set the reader identity the TPSL bridge must authenticate as.

    The observer builds its Bitget client from current configuration, so every
    exercise of the real claim path has to declare which account that reader is;
    otherwise its assertions pass vacuously through the identity gate.
    """
    import aletheia.uta.manager as uta_manager

    monkeypatch.setattr(
        uta_manager,
        "build_uta_registry",
        lambda *_a, **_k: SimpleNamespace(configured_identities=lambda: identities),
    )


def _patch_evidence_reader(monkeypatch, fill_rows, *, calls=None, available=None):
    """Patch the identity-enforcing UTA read the observer uses for close evidence.

    Close fills no longer come from the raw Bitget client: it re-reads its
    credentials on every request, so no snapshot of the configured identity can
    bind what it actually signs with. ``patch_private_history`` asserts
    ``expected_account`` on each read, so these tests also prove that binding.
    ``available`` makes the exact-order read fail on demand without disturbing
    the identity assertion.
    """
    from datetime import datetime, timedelta, timezone

    from aletheia.execution import reconciliation
    from aletheia.uta.profiles.base import CCXTProfile
    from tests.specs.uta_private_history import patch_private_history

    patch_private_history(monkeypatch, reconciliation, fill_rows, calls=calls)
    verified = reconciliation.query_uta

    async def _gated(execution_mode, method, query, *, expected_account=None):
        # Mirror CCXTUTAAdapter.get_fills' preconditions. Without them the fake
        # answers COMPLETE for a scope the real adapter would refuse, and every
        # claim about which fills are visible passes vacuously.
        if method == "get_fills":
            if query.scope.start_at is None:
                raise RuntimeError("private_fill_history_start_required")
            retention = timedelta(days=CCXTProfile.private_history_retention_days)
            if query.scope.start_at < datetime.now(timezone.utc) - retention:
                raise RuntimeError("private_fill_history_retention_expired")
        # Delegate first so the attempt is recorded and the identity assertion
        # still runs; only then refuse to hand the evidence back.
        report = await verified(
            execution_mode, method, query, expected_account=expected_account
        )
        if method == "get_fills" and query.scope.start_at is not None:
            # `since` is passed to the venue, so a fill before the bound is not
            # returned. Modelling that is what makes the bound itself testable.
            report = report.model_copy(
                update={
                    "fills": tuple(
                        fill for fill in report.fills
                        if fill.executed_at >= query.scope.start_at
                    )
                }
            )
        if (
            available is not None
            and method == "get_fills"
            and query.venue_order_id
            and not available()
        ):
            raise RuntimeError("private history incomplete")
        return report

    monkeypatch.setattr(reconciliation, "query_uta", _gated)


def _exact_fill_reads(calls) -> list[tuple[str, str]]:
    """(venue_order_id, market_type) of every exact-order private fill read."""
    return [
        (str(query.venue_order_id), query.scope.instrument.market_type)
        for method, _mode, query in calls
        if method == "get_fills" and query.venue_order_id
    ]


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
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))
    calls: list[tuple] = []

    class _Client:
        def __init__(self, mode, **_kwargs):
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

        async def get_order_fills(self, **_kwargs):
            raise AssertionError(
                "close evidence must not come from the unverified raw client"
            )

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
    uta_calls: list[tuple] = []
    _patch_evidence_reader(
        monkeypatch, [_private_close_fill("EXEC-SL")], calls=uta_calls
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
    assert _exact_fill_reads(uta_calls) == [("EXEC-SL", "usdt_futures")]

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
    _patch_configured_reader(monkeypatch, _identity("bitget_live_limited"))
    uta_calls: list[tuple] = []
    _patch_evidence_reader(
        monkeypatch, [_private_close_fill("EXEC-LIVE-SL")], calls=uta_calls
    )
    calls: list[tuple] = []

    class _Client:
        def __init__(self, mode, **_kwargs):
            calls.append(("client", mode))

        async def get_plan_order_history(self, *, order_id, **kwargs):
            calls.append(("plan", order_id))
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-LIVE-SL",
            }]

        async def get_order_fills(self, **_kwargs):
            raise AssertionError(
                "close evidence must not come from the unverified raw client"
            )

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
        ("close",),
    ]
    assert _exact_fill_reads(uta_calls) == [("EXEC-LIVE-SL", "usdt_futures")]

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
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))

    class _Client:
        def __init__(self, mode, **_kwargs):
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
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))
    fills_available = False
    observer_reads: list[tuple] = []
    _patch_evidence_reader(
        monkeypatch,
        [_private_close_fill("EXEC-TP-X")],
        calls=observer_reads,
        available=lambda: fills_available,
    )

    class _Client:
        def __init__(self, mode, **_kwargs):
            assert mode == "demo"

        async def get_plan_order_history(self, **kwargs):
            return [{
                "orderId": "PLAN-TP-X",
                "planStatus": "executed",
                "executeOrderId": "EXEC-TP-X",
            }]

        async def get_order_fills(self, **_kwargs):
            raise AssertionError(
                "close evidence must not come from the unverified raw client"
            )

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
    assert _exact_fill_reads(observer_reads) == [("EXEC-TP-X", "usdt_futures")]

    fills_available = True
    from aletheia.execution import reconciliation
    from tests.specs.uta_private_history import patch_private_history

    uta_calls = []
    patch_private_history(
        monkeypatch,
        reconciliation,
        [_private_close_fill("EXEC-TP-X")],
        calls=uta_calls,
    )
    monkeypatch.setattr(
        reconciliation,
        "build_uta_registry",
        lambda: SimpleNamespace(configured_identities=lambda: (_identity("bitget_demo"),)),
    )

    # Reopen durable state; recovery must not depend on the observing connection.
    import aletheia.storage.sqlite as sq

    sq.reset_db()
    db = sq.get_db()

    result = reconciliation.reconcile()
    recovered_position = repo._select_one("positions", position.id)
    recovered_order = repo.get_order(close_order["id"])
    recovered_fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()

    assert result["filled"] == 1
    assert recovered_position["status"] == "closed"
    assert recovered_order["status"] == "filled"
    assert recovered_fill["fill_price"] == pytest.approx(59_000.0)
    assert [
        query.venue_order_id
        for method, _, query in uta_calls
        if method == "get_fills" and query.venue_order_id
    ] == ["EXEC-TP-X"]


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
            "opened_at": "2026-07-13T00:00:00+00:00",
        },
        {
            "id": "pos-ok",
            "symbol": "ETH/USDT",
            "market_type": "usdt_futures",
            "execution_mode": "bitget_demo",
            "exchange_tpsl_order_id": {"tp": "PLAN-OK"},
            "opened_at": "2026-07-13T00:00:00+00:00",
        },
    ]
    fail_private_query = True
    plan_calls: list[str] = []
    materialized: list[str] = []
    failures: list[tuple[str, str]] = []

    class _Client:
        def __init__(self, mode, **_kwargs):
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
            raise AssertionError(
                "close evidence must not come from the unverified raw client"
            )

        async def close(self):
            return None

    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=True)),
    )
    monkeypatch.setattr(repositories, "list_all_open_positions", lambda: positions)
    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))
    _patch_evidence_reader(monkeypatch, [])
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


@spec_claim(
    SpecClaim(
        "PL-TPSL-06",
        "pipeline_tpsl_private_materialization",
        "§6 observing-reader identity proof",
        "L2c",
        "When the configured Bitget reader identity differs from the entry Order's "
        "frozen identity, the TPSL observer claims nothing, reads no private fills "
        "and materializes nothing — for a fresh observation and for one whose close "
        "claim is already pending.",
        status="implemented",
    )
)
def test_rotated_reader_identity_never_stamps_entry_identity(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_demo", suffix="rotate", plan_ids={"sl": "PLAN-ROT-SL"}
    )
    entry_identity = _identity("bitget_demo")
    # Same physical account, later credential generation: a credential rotation,
    # an account config generation bump, a classic_v2/uta_v3 profile switch or a
    # plain ccxt upgrade each produce exactly this.
    rotated_identity = entry_identity.model_copy(
        update={"credential_generation": 7}
    )
    calls: list[tuple] = []
    fills_available = False

    _patch_configured_reader(monkeypatch, rotated_identity)

    class _Client:
        def __init__(self, mode, **_kwargs):
            calls.append(("client", mode))

        async def get_plan_order_history(self, *, order_id, **_kwargs):
            calls.append(("plan", order_id))
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-ROT-SL",
            }]

        async def get_order_fills(self, *, order_id, symbol, market_type):
            calls.append(("fills", order_id))
            if not fills_available:
                raise RuntimeError("private fills unavailable")
            return [_private_close_fill(order_id)]

        async def place_order(self, *args, **kwargs):
            raise AssertionError("TPSL observation must never place an order")

        async def close(self):
            calls.append(("close",))

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    # Phase 1 — a mismatched reader may not create the claim at all, and must not
    # read private fill evidence it would then attribute to the entry identity.
    summary = scheduler._tpsl_fill_check_body()

    assert repo._select_one("positions", position.id)["status"] == "open"
    assert repo._select_one("positions", position.id)["pending_close_order_id"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()[0] == 0
    assert summary["failed_count"] == 1
    assert summary["errors"][0]["position_id"] == position.id
    assert [c for c in calls if c[0] == "fills"] == []

    # Phase 2 — a claim proven under the entry identity must not be materialized
    # by a later reader whose configured identity no longer matches it.
    _patch_configured_reader(monkeypatch, entry_identity)
    summary = scheduler._tpsl_fill_check_body()
    pending = repo._select_one("positions", position.id)["pending_close_order_id"]
    assert pending is not None
    assert repo.get_order(pending)["status"] == "materialization_missing"

    _patch_configured_reader(monkeypatch, rotated_identity)
    fills_available = True
    calls.clear()
    summary = scheduler._tpsl_fill_check_body()

    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "open"
    assert stored["pending_close_order_id"] == pending
    assert repo.get_order(pending)["status"] == "materialization_missing"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (pending,)
    ).fetchone()[0] == 0
    assert summary["failed_count"] == 1
    assert [c for c in calls if c[0] == "fills"] == []


@spec_claim(
    SpecClaim(
        "PL-TPSL-07",
        "pipeline_tpsl_private_materialization",
        "§7 resumed claim carrier proof",
        "L2c",
        "A close claim written before the account proof existed carries no identity, "
        "so a later observation must refuse to advance it rather than file evidence "
        "under absent provenance; the same refusal covers a claim carrying a foreign "
        "identity.",
        status="implemented",
    )
)
def test_pre_proof_pending_claim_is_never_advanced(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_demo", suffix="legacy-claim", plan_ids={"sl": "PLAN-LEG-SL"}
    )
    entry_identity = _identity("bitget_demo")
    _patch_configured_reader(monkeypatch, entry_identity)
    fills_available = False
    _patch_evidence_reader(
        monkeypatch,
        [_private_close_fill("EXEC-LEG-SL")],
        available=lambda: fills_available,
    )

    class _Client:
        def __init__(self, mode, **_kwargs):
            pass

        async def get_plan_order_history(self, *, order_id, **_kwargs):
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-LEG-SL",
            }]

        async def get_order_fills(self, **_kwargs):
            raise AssertionError(
                "close evidence must not come from the unverified raw client"
            )

        async def close(self):
            return None

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    # Claim it the ordinary way, with the exact-order evidence read unavailable,
    # so the Position keeps an unresolved pending claim.
    scheduler._tpsl_fill_check_body()
    pending = repo._select_one("positions", position.id)["pending_close_order_id"]
    assert pending is not None
    assert repo.get_order(pending)["status"] == "materialization_missing"

    # Reproduce the row shape this account proof did not exist for. Migration v73
    # is nullable_no_backfill, so every claim written before it carries NULL here.
    # This restores a legacy carrier; it manufactures no fill, close or outcome.
    db.execute(
        "UPDATE orders SET uta_account_identity_json=NULL WHERE id=?", (pending,)
    )
    assert repo.get_order(pending)["uta_account_identity_json"] is None

    fills_available = True
    summary = scheduler._tpsl_fill_check_body()

    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "open"
    assert stored["pending_close_order_id"] == pending
    assert repo.get_order(pending)["status"] == "materialization_missing"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (pending,)
    ).fetchone()[0] == 0
    assert summary["failed_count"] == 1
    assert summary["errors"][0]["position_id"] == position.id

    # A carrier that parses but names another account is refused the same way.
    db.execute(
        "UPDATE orders SET uta_account_identity_json=? WHERE id=?",
        (
            entry_identity.model_copy(
                update={"credential_generation": 11}
            ).model_dump_json(),
            pending,
        ),
    )
    summary = scheduler._tpsl_fill_check_body()

    assert repo._select_one("positions", position.id)["status"] == "open"
    assert repo.get_order(pending)["status"] == "materialization_missing"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (pending,)
    ).fetchone()[0] == 0
    assert summary["failed_count"] == 1

    # Restoring the proven carrier lets the ordinary path converge — the refusal
    # is about provenance, not a dead end.
    db.execute(
        "UPDATE orders SET uta_account_identity_json=? WHERE id=?",
        (entry_identity.model_dump_json(), pending),
    )
    summary = scheduler._tpsl_fill_check_body()

    assert summary["failed_count"] == 0
    assert repo._select_one("positions", position.id)["status"] == "closed"
    assert repo.get_order(pending)["status"] == "filled"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (pending,)
    ).fetchone()[0] == 1


@spec_claim(
    SpecClaim(
        "PL-TPSL-08",
        "pipeline_tpsl_private_materialization",
        "§7 evidence reader binding",
        "L2c",
        "Close evidence is read through the identity-bound UTA projection, never the "
        "raw client whose credentials are re-read per request, and an account with no "
        "configured identity is reported without any venue read or claim.",
        status="implemented",
    )
)
def test_evidence_read_is_identity_bound_and_unconfigured_account_reads_nothing(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_demo", suffix="unconfigured", plan_ids={"sl": "PLAN-UNC-SL"}
    )
    venue_reads: list[str] = []

    class _Client:
        def __init__(self, mode, **_kwargs):
            venue_reads.append("client")

        async def get_plan_order_history(self, *, order_id, **_kwargs):
            venue_reads.append("plan")
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-UNC-SL",
            }]

        async def get_order_fills(self, **_kwargs):
            raise AssertionError(
                "close evidence must not come from the unverified raw client"
            )

        async def close(self):
            venue_reads.append("close")

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    # No configured account for this mode: nothing may be read or claimed, and the
    # job must still go red rather than report a quiet success.
    _patch_configured_reader(monkeypatch)
    summary = scheduler._tpsl_fill_check_body()

    assert venue_reads == []
    assert summary["failed_count"] == 1
    assert summary["errors"][0]["error_type"] == "uta_account_identity_unavailable"
    assert repo._select_one("positions", position.id)["status"] == "open"
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()[0] == 0

    # With the account configured, the evidence read carries expected_account into
    # every UTA query — patch_private_history asserts exactly that — and the raw
    # client is left with plan discovery only.
    reads: list[tuple] = []
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))
    _patch_evidence_reader(
        monkeypatch, [_private_close_fill("EXEC-UNC-SL")], calls=reads
    )
    summary = scheduler._tpsl_fill_check_body()

    assert summary["failed_count"] == 0
    assert venue_reads == ["client", "plan", "close"]
    assert _exact_fill_reads(reads) == [("EXEC-UNC-SL", "usdt_futures")]
    assert repo._select_one("positions", position.id)["status"] == "closed"


@spec_claim(
    SpecClaim(
        "PL-TPSL-09",
        "pipeline_tpsl_private_materialization",
        "§7 bounded history and pre-claim reader recheck",
        "L2c",
        "The close evidence read carries a history start no later than the Position's "
        "opening, because an unbounded scope is only ever PARTIAL; and a configuration "
        "that moved after plan discovery leaves no durable claim behind.",
        status="implemented",
    )
)
def test_evidence_scope_is_bounded_and_rotation_after_discovery_claims_nothing(
    tmp_path, monkeypatch
):
    from datetime import datetime

    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo
    from aletheia.uta.compat import utc

    position = _seed_position(
        "bitget_demo", suffix="bounded", plan_ids={"sl": "PLAN-BND-SL"}
    )
    entry_identity = _identity("bitget_demo")
    rotated_identity = entry_identity.model_copy(
        update={"credential_generation": 5}
    )
    reads: list[tuple] = []
    _patch_configured_reader(monkeypatch, entry_identity)
    _patch_evidence_reader(
        monkeypatch, [_private_close_fill("EXEC-BND-SL")], calls=reads
    )

    class _Client:
        def __init__(self, mode, **_kwargs):
            pass

        async def get_plan_order_history(self, *, order_id, **_kwargs):
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-BND-SL",
            }]

        async def get_order_fills(self, **_kwargs):
            raise AssertionError(
                "close evidence must not come from the unverified raw client"
            )

        async def close(self):
            return None

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    summary = scheduler._tpsl_fill_check_body()

    assert summary["failed_count"] == 0
    assert repo._select_one("positions", position.id)["status"] == "closed"

    # The scope must be bounded, and no later than the Position opened: the exit
    # fill cannot precede the entry, and an unbounded scope is only ever PARTIAL.
    fill_scopes = [
        query.scope for method, _mode, query in reads
        if method == "get_fills" and query.venue_order_id
    ]
    assert len(fill_scopes) == 1
    assert isinstance(fill_scopes[0].start_at, datetime)
    # Exactly the entry, not merely no later than it. Widening to the retention
    # floor for a Position that opened well inside the window would scan ~90 days
    # of history for nothing, and reaching the venue's page bound is itself
    # reported as PARTIAL -- the same failure this bound exists to avoid.
    assert fill_scopes[0].start_at == utc(position.opened_at)

    # A configuration that moves between plan discovery and the claim must leave
    # nothing durable: a stamped claim carrying a foreign executeOrderId could
    # never be advanced or cleared by any later pass.
    second = _seed_position(
        "bitget_demo", suffix="bounded-2", plan_ids={"sl": "PLAN-BND2-SL"}
    )
    rotate_on_discovery = True

    class _RotatingClient(_Client):
        async def get_plan_order_history(self, *, order_id, **_kwargs):
            history = [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-BND2-SL",
            }]
            if rotate_on_discovery:
                _patch_configured_reader(monkeypatch, rotated_identity)
            return history

    monkeypatch.setattr(bitget_client, "BitgetClient", _RotatingClient)
    summary = scheduler._tpsl_fill_check_body()

    stored = repo._select_one("positions", second.id)
    assert stored["status"] == "open"
    assert stored["pending_close_order_id"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (second.id,),
    ).fetchone()[0] == 0
    assert summary["failed_count"] == 1

    # Stable configuration converges it the ordinary way.
    rotate_on_discovery = False
    _patch_configured_reader(monkeypatch, entry_identity)
    _patch_evidence_reader(monkeypatch, [_private_close_fill("EXEC-BND2-SL")])
    summary = scheduler._tpsl_fill_check_body()

    assert summary["failed_count"] == 0
    assert repo._select_one("positions", second.id)["status"] == "closed"


@spec_claim(
    SpecClaim(
        "PL-TPSL-10",
        "pipeline_tpsl_private_materialization",
        "§7 retention-clamped evidence window",
        "L2c",
        "A Position open longer than the venue's private-history retention still "
        "converges from a trigger inside that window, because the read asks for the "
        "widest window the venue serves instead of failing closed; a fill genuinely "
        "outside retention stays evidence_missing and never becomes proof of absence.",
        status="implemented",
    )
)
def test_position_older_than_retention_still_converges_from_a_recent_trigger(
    tmp_path, monkeypatch
):
    from datetime import datetime, timedelta, timezone

    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo
    from aletheia.uta.profiles.base import CCXTProfile

    now = datetime.now(timezone.utc)
    retention = CCXTProfile.private_history_retention_days
    # Older than the whole retention window: opened_at alone would be refused.
    ancient = (now - timedelta(days=retention + 110)).isoformat()
    recent_trigger_ms = int((now - timedelta(days=2)).timestamp() * 1000)
    unreachable_trigger_ms = int((now - timedelta(days=retention + 40)).timestamp() * 1000)

    position = _seed_position(
        "bitget_demo",
        suffix="ancient",
        plan_ids={"sl": "PLAN-OLD-SL"},
        opened_at=ancient,
    )
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))
    reads: list[tuple] = []
    _patch_evidence_reader(
        monkeypatch,
        [_private_close_fill("EXEC-OLD-SL", filled_at_ms=unreachable_trigger_ms)],
        calls=reads,
    )

    class _Client:
        def __init__(self, mode, **_kwargs):
            pass

        async def get_plan_order_history(self, *, order_id, **_kwargs):
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-OLD-SL",
            }]

        async def get_order_fills(self, **_kwargs):
            raise AssertionError(
                "close evidence must not come from the unverified raw client"
            )

        async def close(self):
            return None

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    # The read is attempted, not refused: the bound is clamped into retention.
    summary = scheduler._tpsl_fill_check_body()

    fill_scopes = [
        query.scope for method, _mode, query in reads
        if method == "get_fills" and query.venue_order_id
    ]
    assert len(fill_scopes) == 1
    assert fill_scopes[0].start_at >= now - timedelta(days=retention)
    assert fill_scopes[0].start_at > utc(ancient)

    # A fill genuinely outside retention is unavailable, never absent: the claim
    # stays unresolved and the Position stays open.
    pending = repo._select_one("positions", position.id)["pending_close_order_id"]
    assert pending is not None
    assert repo.get_order(pending)["status"] == "materialization_missing"
    assert repo._select_one("positions", position.id)["status"] == "open"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (pending,)
    ).fetchone()[0] == 0
    assert repo._select_one("positions", position.id)["closed_at"] is None

    # The same ancient Position converges once the trigger itself is inside the
    # window the venue serves. This is the case failing closed would have lost.
    _patch_evidence_reader(
        monkeypatch,
        [_private_close_fill("EXEC-OLD-SL", filled_at_ms=recent_trigger_ms)],
    )
    summary = scheduler._tpsl_fill_check_body()

    assert summary["failed_count"] == 0
    assert repo._select_one("positions", position.id)["status"] == "closed"
    assert repo.get_order(pending)["status"] == "filled"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (pending,)
    ).fetchone()[0] == 1
