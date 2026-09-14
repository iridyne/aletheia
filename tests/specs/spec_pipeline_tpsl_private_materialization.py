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
from tests.fill_reports import reports


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


def _private_close_report(exchange_order_id: str, identity):
    return reports([_private_close_fill(exchange_order_id)], identity=identity)[0]


def _patch_configured_reader(monkeypatch, *identities):
    """Set the reader identity the TPSL bridge must authenticate as.

    The observer builds its Bitget client from current configuration, so every
    exercise of the real claim path has to declare which account that reader is;
    otherwise its assertions pass vacuously through the identity gate.
    """
    import aletheia.uta.manager as uta_manager
    from aletheia.exchanges import composition

    monkeypatch.setattr(uta_manager, "_native_protection_reader_factory", composition.create_native_protection_reader)
    monkeypatch.setattr(uta_manager, "_native_protection_modes", composition.NATIVE_PROTECTION_EXECUTION_MODES)

    monkeypatch.setattr(
        uta_manager,
        "build_uta_registry",
        lambda *_a, **_k: SimpleNamespace(configured_identities=lambda: identities),
    )


def _patch_evidence_reader(monkeypatch, fill_rows, *, calls=None, available=None):
    """Patch the identity-enforcing UTA reads the observer and reconciliation use.

    The observer proves the execute Order belongs to the verified account through
    ``get_order``, which is exact and window-free; ``patch_private_history``
    asserts ``expected_account`` on every read, so these tests prove that binding
    rather than assuming it. ``get_fills`` is still what reconciliation uses, and
    its preconditions are mirrored below so no claim about fill visibility passes
    vacuously. ``available`` fails the windowed read on demand.
    """
    from datetime import datetime, timedelta, timezone

    from aletheia.execution import reconciliation
    from aletheia.uta.profiles.base import CCXTProfile
    from tests.specs.uta_private_history import patch_private_history

    patch_private_history(monkeypatch, reconciliation, fill_rows, calls=calls)
    verified = reconciliation.query_uta

    async def _gated(execution_mode, method, query, *, expected_account=None):
        # Mirror query_uta: the adapter is built from current configuration and
        # must equal expected_account, so a rotated reader is refused before any
        # evidence is served. Modelling it here keeps the refusal a production
        # error rather than a bare assert inside the shared fake.
        if expected_account is not None and expected_account != _identity(
            execution_mode
        ):
            raise ValueError("uta_account_identity_mismatch")
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


@pytest.mark.parametrize("report_kind", ["exact", "wrong_account", "wrong_plan", "raw"])
def test_scheduler_native_port_validates_discovery_before_claim(tmp_path, monkeypatch, report_kind):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo
    from aletheia.uta import manager
    from aletheia.uta.models import NativeProtectionHistory

    identity = _identity("bitget_demo")
    position = _seed_position("bitget_demo", suffix="port", plan_ids={"sl": "PLAN"})
    _patch_configured_reader(monkeypatch, identity)
    proofs = []
    _patch_evidence_reader(monkeypatch, [_private_close_fill("EXEC")], calls=proofs)
    fill = _private_close_report("EXEC", identity)
    calls = []

    class Reader:
        async def get_plan_order_history(self, *, order_id):
            if report_kind == "raw":
                return [{"orderId": order_id, "executeOrderId": "EXEC"}]
            return NativeProtectionHistory(
                identity=_identity("bitget_live_limited") if report_kind == "wrong_account" else identity,
                plan_order_id="OTHER" if report_kind == "wrong_plan" else order_id,
                execute_order_ids=("EXEC",),
            )

        async def get_order_fill_reports(self, **kwargs):
            calls.append(kwargs)
            return (fill,)

        async def close(self):
            calls.append("closed")

    reader = Reader()
    reader.identity = identity
    monkeypatch.setattr(manager, "_native_protection_reader_factory", lambda *_a, **_k: reader)

    def forbidden_client(*_a, **_k):
        pytest.fail("Core constructed a concrete client")

    monkeypatch.setattr(bitget_client, "BitgetClient", forbidden_client)
    result = scheduler._tpsl_fill_check_body()
    assert calls[-1] == "closed"
    if report_kind == "exact":
        assert result["failed_count"] == 0
        assert repo._select_one("positions", position.id)["status"] == "closed"
        assert _account_proofs(proofs) == [("EXEC", "usdt_futures", True)]
        assert calls[0]["symbol"] == "BTC/USDT"
        assert calls[0]["identity"] == identity
    else:
        assert result["failed_count"] == 1
        assert repo._select_one("positions", position.id)["status"] == "open"
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
        assert proofs == []
        assert calls == ["closed"]


def _account_proofs(calls) -> list[tuple[str, str, bool]]:
    """(venue_order_id, market_type, window_free) of every exact-order proof.

    ``window_free`` records that no history bound was needed: ``get_order`` only
    demands one when ``venue_order_id`` is absent, which is the whole reason the
    proof can be exact for a Position of any age.
    """
    return [
        (
            str(query.venue_order_id),
            query.scope.instrument.market_type,
            query.scope.start_at is None,
        )
        for method, _mode, query in calls
        if method == "get_order" and query.venue_order_id
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

        async def get_order_fill_reports(self, *, order_id, symbol, market_type, identity):
            calls.append(("fills", order_id, symbol, market_type))
            return [_private_close_report(order_id, identity)]

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
    assert ("fills", "EXEC-SL", "BTCUSDT", "usdt_futures") in calls
    # The economics came from the exact-order endpoint, but only after an
    # identity-enforced, window-free proof that the Order is this account's.
    assert _account_proofs(uta_calls) == [("EXEC-SL", "usdt_futures", True)]

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

        async def get_order_fill_reports(self, *, order_id, symbol, market_type, identity):
            calls.append(("fills", order_id, symbol, market_type))
            return [_private_close_report(order_id, identity)]

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
    assert _account_proofs(uta_calls) == [("EXEC-LIVE-SL", "usdt_futures", True)]

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

        async def get_order_fill_reports(self, **kwargs):
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
    exact_fill_calls: list[tuple[str, str, str]] = []
    _patch_evidence_reader(
        monkeypatch, [_private_close_fill("EXEC-TP-X")], calls=observer_reads
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

        async def get_order_fill_reports(self, **kwargs):
            exact_fill_calls.append((
                kwargs["order_id"], kwargs["symbol"], kwargs["market_type"]
            ))
            if not fills_available:
                raise RuntimeError("private history incomplete")
            return [_private_close_report(kwargs["order_id"], kwargs["identity"])]

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
    # The account proof succeeded; only the economics were unavailable.
    assert _account_proofs(observer_reads) == [("EXEC-TP-X", "usdt_futures", True)]
    assert exact_fill_calls == [("EXEC-TP-X", "BTCUSDT", "usdt_futures")]

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

        async def get_order_fill_reports(self, *, identity, **_kwargs):
            return [_private_close_report("private-order", identity)]

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
    _patch_evidence_reader(
        monkeypatch,
        [_private_close_fill("EXEC-PLAN-FAIL"), _private_close_fill("EXEC-PLAN-OK")],
    )
    monkeypatch.setattr(
        exchange_materialization,
        "claim_observed_tpsl_close",
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
    _patch_evidence_reader(monkeypatch, [_private_close_fill("EXEC-ROT-SL")])

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

        async def get_order_fill_reports(self, *, order_id, symbol, market_type, identity):
            calls.append(("fills", order_id))
            if not fills_available:
                raise RuntimeError("private fills unavailable")
            return [_private_close_report(order_id, identity)]

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
    _patch_evidence_reader(monkeypatch, [_private_close_fill("EXEC-LEG-SL")])

    class _Client:
        def __init__(self, mode, **_kwargs):
            pass

        async def get_plan_order_history(self, *, order_id, **_kwargs):
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-LEG-SL",
            }]

        async def get_order_fill_reports(self, *, order_id, identity, **_kwargs):
            if not fills_available:
                raise RuntimeError("private history incomplete")
            return [_private_close_report(order_id, identity)]

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
        "§7 order membership proof",
        "L2c",
        "The execute Order's membership of the verified account is proven through an "
        "identity-bound exact read before anything durable is written or any economics "
        "are requested; an unproven Order leaves no claim, and an account with no "
        "configured identity is reported without touching the venue at all.",
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

        async def get_order_fill_reports(self, *, order_id, identity, **_kwargs):
            venue_reads.append("fills")
            return [_private_close_report(order_id, identity)]

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

    # Configured, but the verified account does not own that execute Order: the
    # economics are never read and nothing is materialized. This is the guard the
    # raw exact-order endpoint cannot provide on its own.
    reads: list[tuple] = []
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))
    _patch_evidence_reader(monkeypatch, [_private_close_fill("SOMEONE-ELSE")],
                           calls=reads)
    summary = scheduler._tpsl_fill_check_body()

    assert _account_proofs(reads) == [("EXEC-UNC-SL", "usdt_futures", True)]
    assert "fills" not in venue_reads
    assert summary["failed_count"] == 1
    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "open"
    # Nothing durable: a claim here would set pending_close_order_id and make
    # ordinary closes report already_pending on a Position that is still open.
    assert stored["pending_close_order_id"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()[0] == 0

    # Once the proof succeeds, the exact-order endpoint supplies the economics.
    reads.clear()
    venue_reads.clear()
    _patch_evidence_reader(
        monkeypatch, [_private_close_fill("EXEC-UNC-SL")], calls=reads
    )
    summary = scheduler._tpsl_fill_check_body()

    assert summary["failed_count"] == 0
    assert venue_reads == ["client", "plan", "fills", "close"]
    assert _account_proofs(reads) == [("EXEC-UNC-SL", "usdt_futures", True)]
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
    raw_fill_reads: list[str] = []

    class _Client:
        def __init__(self, mode, **_kwargs):
            pass

        async def get_plan_order_history(self, *, order_id, **_kwargs):
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-BND-SL",
            }]

        async def get_order_fill_reports(self, *, order_id, identity, **_kwargs):
            raw_fill_reads.append(order_id)
            return [_private_close_report(order_id, identity)]

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
    # Both reads are order-scoped and neither needs a history window: the proof
    # because get_order is exact when venue_order_id is present, the economics
    # because the venue's per-order fills endpoint takes no time bound at all.
    # That is what makes a Position of any age materializable.
    assert _account_proofs(reads) == [("EXEC-BND-SL", "usdt_futures", True)]
    assert raw_fill_reads == ["EXEC-BND-SL"]

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

    # A configuration that moves *during* the economics read is the one case the
    # pre-claim proof structurally cannot catch: by then the claim legitimately
    # exists, so the write boundary is the last chance to refuse.
    rotate_on_discovery = False
    third = _seed_position(
        "bitget_demo", suffix="bounded-3", plan_ids={"sl": "PLAN-BND3-SL"}
    )
    _patch_configured_reader(monkeypatch, entry_identity)
    _patch_evidence_reader(monkeypatch, [_private_close_fill("EXEC-BND3-SL")])
    rotate_on_fill_read = True

    class _RotatingOnFills(_Client):
        async def get_plan_order_history(self, *, order_id, **_kwargs):
            # Only this Position triggers; the others must not be dragged into
            # a claim by a fake that answers for every plan alike.
            if order_id != "PLAN-BND3-SL":
                return [{"orderId": order_id, "planStatus": "not_trigger"}]
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-BND3-SL",
            }]

        async def get_order_fill_reports(self, *, order_id, identity, **_kwargs):
            rows = [_private_close_report(order_id, identity)]
            if rotate_on_fill_read:
                _patch_configured_reader(monkeypatch, rotated_identity)
            return rows

    monkeypatch.setattr(bitget_client, "BitgetClient", _RotatingOnFills)
    summary = scheduler._tpsl_fill_check_body()

    third_pending = repo._select_one("positions", third.id)["pending_close_order_id"]
    assert third_pending is not None
    assert repo._select_one("positions", third.id)["status"] == "open"
    assert repo.get_order(third_pending)["status"] == "materialization_missing"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (third_pending,)
    ).fetchone()[0] == 0
    assert summary["failed_count"] >= 1

    # Stable configuration converges both the ordinary way.
    rotate_on_fill_read = False
    _patch_configured_reader(monkeypatch, entry_identity)
    _patch_evidence_reader(
        monkeypatch,
        [
            _private_close_fill("EXEC-BND2-SL"),
            _private_close_fill("EXEC-BND3-SL"),
        ],
    )

    class _StableClient(_Client):
        async def get_plan_order_history(self, *, order_id, **_kwargs):
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": order_id.replace("PLAN-", "EXEC-"),
            }]

        async def get_order_fill_reports(self, *, order_id, identity, **_kwargs):
            return [_private_close_report(order_id, identity)]

    monkeypatch.setattr(bitget_client, "BitgetClient", _StableClient)
    summary = scheduler._tpsl_fill_check_body()

    assert summary["failed_count"] == 0
    assert repo._select_one("positions", second.id)["status"] == "closed"
    assert repo._select_one("positions", third.id)["status"] == "closed"


@spec_claim(
    SpecClaim(
        "PL-TPSL-10",
        "pipeline_tpsl_private_materialization",
        "§7 age-independent exact reads",
        "L2c",
        "A Position open far longer than the venue's private-history retention still "
        "converges, because both the account proof and the close economics are "
        "order-scoped reads that take no history window; Position age cannot block "
        "materialization, and an unproven Order still yields nothing.",
        status="implemented",
    )
)
def test_position_far_older_than_retention_still_converges(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.storage import repositories as repo
    from aletheia.uta.profiles.base import CCXTProfile

    now = datetime.now(timezone.utc)
    retention = CCXTProfile.private_history_retention_days
    # Twice the retention window: any windowed read would refuse this outright.
    ancient = (now - timedelta(days=retention * 2)).isoformat()

    position = _seed_position(
        "bitget_demo",
        suffix="ancient",
        plan_ids={"sl": "PLAN-OLD-SL"},
        opened_at=ancient,
    )
    reads: list[tuple] = []
    raw_fill_reads: list[str] = []
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))
    _patch_evidence_reader(
        monkeypatch, [_private_close_fill("EXEC-OLD-SL")], calls=reads
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

        async def get_order_fill_reports(self, *, order_id, identity, **_kwargs):
            raw_fill_reads.append(order_id)
            return [_private_close_report(order_id, identity)]

        async def close(self):
            return None

    monkeypatch.setattr(bitget_client, "BitgetClient", _Client)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)),
    )

    summary = scheduler._tpsl_fill_check_body()

    # Age is irrelevant: neither read carries a history bound to fall outside.
    assert summary["failed_count"] == 0
    assert _account_proofs(reads) == [("EXEC-OLD-SL", "usdt_futures", True)]
    assert raw_fill_reads == ["EXEC-OLD-SL"]
    assert utc(ancient) < now - timedelta(days=retention)

    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "closed"
    close_order = db.execute(
        "SELECT * FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()
    assert close_order["status"] == "filled"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 1

    # Age does not weaken the membership proof either: an equally ancient Position
    # whose execute Order the verified account does not own materializes nothing.
    other = _seed_position(
        "bitget_demo",
        suffix="ancient-unproven",
        plan_ids={"sl": "PLAN-OLD2-SL"},
        opened_at=ancient,
    )
    reads.clear()
    raw_fill_reads.clear()
    _patch_evidence_reader(
        monkeypatch, [_private_close_fill("NOT-OURS")], calls=reads
    )

    class _OtherClient(_Client):
        async def get_plan_order_history(self, *, order_id, **_kwargs):
            return [{
                "orderId": order_id,
                "planStatus": "executed",
                "executeOrderId": "EXEC-OLD2-SL",
            }]

    monkeypatch.setattr(bitget_client, "BitgetClient", _OtherClient)
    summary = scheduler._tpsl_fill_check_body()

    assert summary["failed_count"] == 1
    assert raw_fill_reads == []
    stored_other = repo._select_one("positions", other.id)
    assert stored_other["status"] == "open"
    assert stored_other["pending_close_order_id"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (other.id,),
    ).fetchone()[0] == 0
