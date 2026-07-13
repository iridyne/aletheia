"""Missing contracts for the Risk -> Execution -> Position handoff.

These claims intentionally remain strict xfails until the owner authorizes the
smallest production repair across the change-controlled Risk and Execution
modules.  Each test reproduces an observed failure mode without touching Live or
an exchange network.
"""

from __future__ import annotations

from types import SimpleNamespace
import sqlite3
import threading

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _missing(reason: str):
    return pytest.mark.xfail(strict=True, reason=reason)


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
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
    run_migrations()
    return conn


@_missing("Risk/Execution repair pending: stored Proposal execution intent is still mutable")
@spec_claim(
    SpecClaim(
        "PL-RX-01",
        "pipeline_risk_execution_integrity",
        "§1 immutable proposal intent",
        "L2b",
        "Executing a stored Proposal with a different requested execution mode is rejected "
        "before Risk/Execution, and no hash-covered Proposal field is rewritten.",
        status="missing",
    )
)
def test_existing_proposal_mode_mismatch_does_not_rewrite_intent(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    before = dict(db.execute(
        "SELECT * FROM trade_proposals WHERE id=?", (proposal.id,)
    ).fetchone())

    execution_calls: list[str] = []
    monkeypatch.setattr(
        gateway,
        "_execute_proposal",
        lambda candidate: execution_calls.append(candidate.execution_mode)
        or {"status": "executed", "proposal_id": candidate.id},
    )

    result = gateway.execute_existing_proposal(
        proposal.id, execution_mode="bitget_demo"
    )
    after = dict(db.execute(
        "SELECT * FROM trade_proposals WHERE id=?", (proposal.id,)
    ).fetchone())

    assert after["execution_mode"] == before["execution_mode"] == "paper_internal"
    assert after["commit_hash"] == before["commit_hash"]
    assert execution_calls == []
    assert result["status"] == "rejected"
    assert result["reason_code"] == "execution_mode_mismatch"


@_missing("Risk/Execution repair pending: venue-normalized quantity still rewrites Proposal")
@spec_claim(
    SpecClaim(
        "PL-RX-02",
        "pipeline_risk_execution_integrity",
        "§1 immutable evaluated snapshot",
        "L2b",
        "Venue quantity normalization is evaluated and submitted, but the original Proposal "
        "quantity and its commit hash remain unchanged.",
        status="missing",
    )
)
def test_normalized_quantity_uses_snapshot_without_mutating_proposal(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import bitget_broker, gateway
    from aletheia.risk.engine import RiskCheckResult
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.0011,
        price=60_000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_trade_proposal(proposal)
    original_hash = proposal.commit_hash

    evaluated_quantities: list[float | None] = []

    def _approved(candidate, **_kwargs):
        evaluated_quantities.append(candidate.quantity)
        return RiskCheckResult(
            approved=True,
            allowed_execution_mode="bitget_demo",
            message="Approved",
            decision_id="risk-authoritative",
        )

    sent_quantities: list[float] = []

    def _execute_live_order(**kwargs):
        sent_quantities.append(kwargs["quantity"])
        return {
            "order_id": "order-normalized",
            "exchange_order_id": "exchange-normalized",
            "position_id": None,
            "fill_price": kwargs["price"],
        }

    monkeypatch.setattr(gateway, "evaluate_proposal", _approved)
    monkeypatch.setattr(
        gateway,
        "_prepare_exchange_quantity",
        lambda *_args, **_kwargs: (0.003, None),
    )
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *_args: None)
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)
    monkeypatch.setattr(bitget_broker, "execute_live_order", _execute_live_order)

    result = gateway._execute_proposal(proposal)
    stored = dict(db.execute(
        "SELECT * FROM trade_proposals WHERE id=?", (proposal.id,)
    ).fetchone())

    assert result["status"] == "executed"
    assert evaluated_quantities[-1] == pytest.approx(0.003)
    assert sent_quantities == [pytest.approx(0.003)]
    assert stored["quantity"] == pytest.approx(0.0011)
    assert stored["commit_hash"] == original_hash
    assert repo._compute_proposal_hash(TradeProposal.model_validate(stored)) == original_hash


@_missing("Risk repair pending: RiskDecision has no authoritative/preflight snapshot contract")
@spec_claim(
    SpecClaim(
        "PL-RX-03",
        "pipeline_risk_execution_integrity",
        "§2 authoritative risk decision",
        "L2b",
        "A native Order can consume one idempotent authoritative RiskDecision; preflight "
        "checks are labeled separately and the decision records the evaluated quantity and "
        "original Proposal commit hash.",
        status="missing",
    )
)
def test_risk_decision_roles_snapshot_and_authoritative_idempotency(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.0011,
        price=60_000.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)

    preflight = evaluate_proposal(proposal, decision_role="preflight")
    evaluated = proposal.model_copy(update={"quantity": 0.003})
    authoritative = evaluate_proposal(evaluated, decision_role="authoritative")
    retry = evaluate_proposal(evaluated, decision_role="authoritative")

    rows = [dict(row) for row in db.execute(
        "SELECT * FROM risk_decisions WHERE proposal_id=? ORDER BY rowid",
        (proposal.id,),
    ).fetchall()]
    authoritative_rows = [
        row for row in rows if row["decision_role"] == "authoritative"
    ]

    assert preflight.decision_id != authoritative.decision_id
    assert retry.decision_id == authoritative.decision_id
    assert len(authoritative_rows) == 1
    assert authoritative_rows[0]["evaluated_quantity"] == pytest.approx(0.003)
    assert authoritative_rows[0]["proposal_commit_hash"] == proposal.commit_hash
    stored_quantity = db.execute(
        "SELECT quantity FROM trade_proposals WHERE id=?", (proposal.id,)
    ).fetchone()[0]
    assert stored_quantity == pytest.approx(0.0011)


@_missing("Execution repair pending: execute_existing_proposal has no atomic claim")
@spec_claim(
    SpecClaim(
        "PL-RX-04",
        "pipeline_risk_execution_integrity",
        "§3 execution claim",
        "L2b",
        "Two concurrent executions of one pending Proposal can enter the side-effecting "
        "pipeline only once; the other caller receives an already-claimed terminal response.",
        status="missing",
    )
)
def test_concurrent_execute_existing_proposal_enters_pipeline_once(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)

    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    calls: list[str] = []
    calls_lock = threading.Lock()

    def _side_effecting_pipeline(candidate):
        with calls_lock:
            calls.append(candidate.id)
            call_number = len(calls)
        if call_number == 1:
            first_entered.set()
            release_first.wait(timeout=2)
        else:
            second_entered.set()
        return {"status": "executed", "proposal_id": candidate.id}

    monkeypatch.setattr(gateway, "_execute_proposal", _side_effecting_pipeline)
    results: list[dict] = []
    errors: list[BaseException] = []

    def _run():
        try:
            results.append(gateway.execute_existing_proposal(proposal.id))
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    first = threading.Thread(target=_run)
    second = threading.Thread(target=_run)
    first.start()
    assert first_entered.wait(timeout=2), "first execution never entered the pipeline"
    second.start()
    second_entered.wait(timeout=0.5)
    release_first.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not errors
    assert len(calls) == 1
    assert len(results) == 2
    assert {result["status"] for result in results} <= {
        "executed", "already_claimed", "already_executed"
    }


@_missing("Execution repair pending: failures before the outer try abandon Proposal as pending")
@pytest.mark.parametrize(
    "failure_point",
    ("load_config", "risk", "quantity_constraints", "symbol_availability"),
)
@spec_claim(
    SpecClaim(
        "PL-RX-05",
        "pipeline_risk_execution_integrity",
        "§4 terminal failure state",
        "L2b",
        "A failure during config, Risk, quantity-rule, or symbol prechecks returns a sanitized "
        "error, marks the Proposal terminal, creates no Order, and persists a stable audit reason.",
        status="missing",
    )
)
def test_pre_order_failure_is_terminal_and_sanitized(
    tmp_path, monkeypatch, failure_point
):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import gateway
    from aletheia.config.schema import AletheiaConfig

    def _raise_secret(*_args, **_kwargs):
        raise RuntimeError("token=must-not-be-persisted")

    monkeypatch.setattr(gateway, "load_config", lambda: AletheiaConfig())
    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda _proposal, **_kwargs: SimpleNamespace(
            approved=True,
            allowed_execution_mode="bitget_demo",
            decision_id="risk-preorder",
            violations=[],
            message="Approved",
        ),
    )
    monkeypatch.setattr(
        gateway,
        "_prepare_exchange_quantity",
        lambda _mode, _symbol, _market, quantity, _price: (quantity, None),
    )
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *_args: None)
    if failure_point == "load_config":
        monkeypatch.setattr(gateway, "load_config", _raise_secret)
    elif failure_point == "risk":
        monkeypatch.setattr(gateway, "evaluate_proposal", _raise_secret)
    elif failure_point == "quantity_constraints":
        monkeypatch.setattr(gateway, "_prepare_exchange_quantity", _raise_secret)
    else:
        monkeypatch.setattr(gateway, "_check_symbol_availability", _raise_secret)

    result = gateway.submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
    )
    proposal = dict(db.execute(
        "SELECT * FROM trade_proposals ORDER BY rowid DESC LIMIT 1"
    ).fetchone())

    assert result["status"] == "error"
    assert result["reason_code"] == "execution_precheck_failed"
    assert "must-not-be-persisted" not in result.get("message", "")
    assert proposal["status"] == "error"
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE proposal_id=?", (proposal["id"],)
    ).fetchone()[0] == 0
    audit = db.execute(
        "SELECT message FROM audit_events WHERE entity_id=? "
        "AND event_type='execution_error' ORDER BY rowid DESC LIMIT 1",
        (proposal["id"],),
    ).fetchone()
    assert audit is not None
    assert "execution_precheck_failed" in audit["message"]
    assert "must-not-be-persisted" not in audit["message"]


INVALID_NATIVE_PROPOSALS = (
    pytest.param(
        {"symbol": "BTCUSDT"}, "invalid_symbol_format", id="noncanonical-symbol"
    ),
    pytest.param(
        {"market_type": "margin"}, "invalid_market_type", id="unknown-market"
    ),
    pytest.param(
        {"direction": "hold"}, "invalid_direction", id="unknown-direction"
    ),
    pytest.param(
        {"order_type": "iceberg"}, "invalid_order_type", id="unknown-order-type"
    ),
    pytest.param(
        {"quantity": 0.0}, "invalid_quantity", id="zero-quantity"
    ),
    pytest.param(
        {"quantity": -0.001}, "invalid_quantity", id="negative-quantity"
    ),
    pytest.param(
        {"price": 0.0}, "invalid_price", id="zero-price"
    ),
    pytest.param(
        {"price": -100.0}, "invalid_price", id="negative-price"
    ),
    pytest.param(
        {"execution_mode": "mystery"},
        "invalid_execution_mode",
        id="unknown-execution-mode",
    ),
)


@_missing("Risk/Execution repair pending: malformed native Proposal inputs can execute")
@pytest.mark.parametrize("override, expected_violation", INVALID_NATIVE_PROPOSALS)
@spec_claim(
    SpecClaim(
        "PL-RX-06",
        "pipeline_risk_execution_integrity",
        "§5 fail-closed input boundary",
        "L2b",
        "Non-canonical symbols, unknown native enums, and non-positive quantity/price are "
        "rejected before Risk/Order/Fill/Position side effects.",
        status="missing",
    )
)
def test_invalid_native_proposal_fails_closed(
    tmp_path, monkeypatch, override, expected_violation
):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import gateway

    monkeypatch.setattr(gateway, "_capture_attribution_snapshot_json", lambda _symbol: None)
    payload = {
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "order_type": "market",
        "quantity": 0.001,
        "price": 60_000.0,
        "execution_mode": "paper_internal",
    }
    payload.update(override)

    result = gateway.submit_proposal(**payload)
    proposal_id = result["proposal_id"]

    assert result["status"] == "rejected"
    assert expected_violation in result["risk_violations"]
    assert db.execute(
        "SELECT status FROM trade_proposals WHERE id=?", (proposal_id,)
    ).fetchone()[0] == "rejected"
    decisions = db.execute(
        "SELECT approved, violations FROM risk_decisions WHERE proposal_id=?",
        (proposal_id,),
    ).fetchall()
    assert len(decisions) == 1
    assert decisions[0]["approved"] == 0
    assert expected_violation in decisions[0]["violations"]
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE proposal_id=?", (proposal_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE proposal_id=?", (proposal_id,)
    ).fetchone()[0] == 0


@_missing("Execution repair pending: open-order absence is still guessed to mean filled")
@spec_claim(
    SpecClaim(
        "PL-RX-07",
        "pipeline_risk_execution_integrity",
        "§6 evidence-based reconciliation",
        "L2b",
        "A submitted Order absent from the exchange open-order list remains unresolved "
        "unless real fill evidence exists; reconciliation never invents filled state.",
        status="missing",
    )
)
def test_reconciliation_absence_without_fill_evidence_stays_unresolved(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    async def _no_open_orders(self, market_type="spot"):
        return []

    async def _no_fills(self, symbol=None, market_type="spot"):
        return []

    monkeypatch.setattr(bitget_client.BitgetClient, "get_open_orders", _no_open_orders)
    monkeypatch.setattr(bitget_client.BitgetClient, "get_fills", _no_fills)
    order = Order(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_live_limited",
        exchange_order_id="EX-ABSENT-NO-FILL",
        status="submitted",
    )
    repo.insert_order(order)

    result = reconcile()
    stored = dict(db.execute("SELECT * FROM orders WHERE id=?", (order.id,)).fetchone())

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert stored["status"] == "submitted"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    audit = db.execute(
        "SELECT event_type FROM audit_events WHERE entity_id=? ORDER BY rowid DESC LIMIT 1",
        (order.id,),
    ).fetchone()
    assert audit is not None
    assert audit["event_type"] == "order_reconciliation_inconclusive"


@_missing("Execution repair pending: partially_filled is not in the unresolved state set")
@spec_claim(
    SpecClaim(
        "PL-RX-08",
        "pipeline_risk_execution_integrity",
        "§6 partial-fill continuity",
        "L2b",
        "The broker-emitted partially_filled state remains unresolved and is scanned by "
        "reconciliation instead of falling out of the execution lifecycle.",
        status="missing",
    )
)
def test_partially_filled_order_remains_in_reconciliation_working_set(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.exchanges.bitget import client as bitget_client
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    async def _still_open(self, market_type="spot"):
        return [{"orderId": "EX-PARTIAL"}]

    monkeypatch.setattr(bitget_client.BitgetClient, "get_open_orders", _still_open)
    order = Order(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="limit",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_live_limited",
        exchange_order_id="EX-PARTIAL",
        status="partially_filled",
    )
    repo.insert_order(order)

    result = reconcile()
    stored = repo.get_order(order.id)

    assert result["checked"] == 1
    assert result["filled"] == 0
    assert stored["status"] == "partially_filled"


@_missing("Execution repair pending: Bitget margin preparation can abandon a pending Order")
@spec_claim(
    SpecClaim(
        "PL-RX-09",
        "pipeline_risk_execution_integrity",
        "§7 pre-submit terminal state",
        "L2b",
        "A margin-preparation failure happens before exchange submission and leaves the local "
        "Order in an explicit sanitized error state, never pending.",
        status="missing",
    )
)
def test_bitget_margin_preparation_failure_does_not_leave_pending_order(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import bitget_broker
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        leverage=2,
        execution_mode="bitget_demo",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode="bitget_demo",
        proposal_execution_mode="bitget_demo",
    )
    repo.insert_risk_decision(decision)

    monkeypatch.setattr(
        bitget_broker, "BitgetClient", lambda *args, **kwargs: SimpleNamespace()
    )

    def _margin_unavailable(*_args, **_kwargs):
        raise RuntimeError("private-detail-must-not-persist")

    monkeypatch.setattr(bitget_broker, "_ensure_mix_margin", _margin_unavailable)

    with pytest.raises(RuntimeError):
        bitget_broker.execute_live_order(
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=proposal.quantity,
            price=proposal.price,
            leverage=proposal.leverage,
            execution_mode="bitget_demo",
        )

    stored = dict(db.execute(
        "SELECT * FROM orders WHERE proposal_id=?", (proposal.id,)
    ).fetchone())
    assert stored["status"] == "error"
    assert stored["error_reason"]
    assert "private-detail-must-not-persist" not in stored["error_reason"]
    assert stored["exchange_order_id"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (stored["id"],)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE proposal_id=?", (proposal.id,)
    ).fetchone()[0] == 0


CLOSE_TIMEOUT_CASES = (
    pytest.param(
        "aletheia.execution.bitget_broker",
        "BitgetClient",
        "close_demo_position",
        "bitget_demo",
        id="bitget",
    ),
    pytest.param(
        "aletheia.execution.okx_broker",
        "OKXClient",
        "close_okx_position",
        "okx_demo",
        id="okx",
    ),
    pytest.param(
        "aletheia.execution.binance_broker",
        "BinanceClient",
        "close_testnet_position",
        "binance_testnet",
        id="binance",
    ),
)


@_missing("Execution repair pending: close timeout is treated as retryable error")
@pytest.mark.parametrize(
    "broker_module, client_name, close_name, execution_mode", CLOSE_TIMEOUT_CASES
)
@spec_claim(
    SpecClaim(
        "PL-RX-10",
        "pipeline_risk_execution_integrity",
        "§8 ambiguous close submission",
        "L2b",
        "A close-order timeout is submission_unknown, remains retry-suppressing, and a "
        "second close request cannot reach the exchange again.",
        status="missing",
    )
)
def test_close_timeout_is_unknown_and_suppresses_duplicate_submission(
    tmp_path,
    monkeypatch,
    broker_module,
    client_name,
    close_name,
    execution_mode,
):
    import importlib

    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls: list[str] = []

    class _TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        async def place_order(self, order):
            calls.append(order.client_oid)
            raise TimeoutError("private-timeout-detail")

        async def close(self):
            pass

    broker = importlib.import_module(broker_module)
    monkeypatch.setattr(broker, client_name, _TimeoutClient)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)

    try:
        getattr(broker, close_name)(position.id, reason="stop_loss")
    except TimeoutError:
        pass

    after_first = repo._select_one("positions", position.id)
    orders = repo.list_orders(limit=20)
    assert after_first["pending_close_status"] == "submission_unknown"
    assert len(orders) == 1
    assert orders[0]["status"] == "submission_unknown"
    assert "private-timeout-detail" not in (orders[0]["error_reason"] or "")

    second = getattr(broker, close_name)(position.id, reason="stop_loss")

    assert second["closed"] is False
    assert second["already_pending"] is True
    assert calls == [f"close-{position.id}"]
    assert len(repo.list_orders(limit=20)) == 1
