"""Executable contracts for the Risk -> Execution -> Position handoff.

Each test reproduces a repaired failure mode without touching Live or an
exchange network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
import sqlite3
import threading

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


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


@spec_claim(
    SpecClaim(
        "PL-RX-01",
        "pipeline_risk_execution_integrity",
        "§1 immutable proposal intent",
        "L2b",
        "Executing a stored Proposal with a different requested execution mode is rejected "
        "before Risk/Execution, and no hash-covered Proposal field is rewritten.",
        status="implemented",
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


@spec_claim(
    SpecClaim(
        "PL-RX-02",
        "pipeline_risk_execution_integrity",
        "§1 immutable evaluated snapshot",
        "L2b",
        "Venue quantity normalization is evaluated and submitted, but the original Proposal "
        "quantity and its commit hash remain unchanged.",
        status="implemented",
    )
)
def test_normalized_quantity_uses_snapshot_without_mutating_proposal(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import bitget_broker, gateway
    from aletheia.risk.engine import RiskCheckResult
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        Fill,
        Order,
        Position,
        RiskDecision,
        TradeProposal,
    )

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
    repo.insert_risk_decision(
        RiskDecision(
            id="risk-authoritative",
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode=proposal.execution_mode,
            proposal_execution_mode=proposal.execution_mode,
            decision_role="authoritative",
            evaluated_quantity=0.003,
            proposal_commit_hash=proposal.commit_hash,
        )
    )

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
        repo.insert_order(
            Order(
                id="order-normalized",
                proposal_id=proposal.id,
                risk_decision_id="risk-authoritative",
                client_order_id=proposal.id,
                position_id="position-normalized",
                symbol=proposal.symbol,
                market_type=proposal.market_type,
                direction=proposal.direction,
                order_type=proposal.order_type,
                quantity=kwargs["quantity"],
                price=kwargs["price"],
                execution_mode=proposal.execution_mode,
                exchange_order_id="exchange-normalized",
                status="filled",
            )
        )
        repo.insert_fill(
            Fill(
                id="fill-normalized",
                order_id="order-normalized",
                fill_price=kwargs["price"],
                fill_quantity=kwargs["quantity"],
            )
        )
        repo.insert_position(
            Position(
                id="position-normalized",
                proposal_id=proposal.id,
                order_id="order-normalized",
                symbol=proposal.symbol,
                market_type=proposal.market_type,
                direction=proposal.direction,
                quantity=kwargs["quantity"],
                entry_price=kwargs["price"],
                execution_mode=proposal.execution_mode,
            )
        )
        return {
            "order_id": "order-normalized",
            "exchange_order_id": "exchange-normalized",
            "order_status": "filled",
            "fill_id": "fill-normalized",
            "position_id": "position-normalized",
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


@spec_claim(
    SpecClaim(
        "PL-RX-03",
        "pipeline_risk_execution_integrity",
        "§2 authoritative risk decision",
        "L2b",
        "A native Order can consume one idempotent authoritative RiskDecision; preflight "
        "checks are labeled separately and the decision records the evaluated quantity and "
        "original Proposal commit hash.",
        status="implemented",
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


@spec_claim(
    SpecClaim(
        "PL-RX-04",
        "pipeline_risk_execution_integrity",
        "§3 execution claim",
        "L2b",
        "Two concurrent executions of one pending Proposal can enter the side-effecting "
        "pipeline only once; the other caller receives an already-claimed terminal response.",
        status="implemented",
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

    def _side_effecting_pipeline(candidate, **_kwargs):
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
        status="implemented",
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


@pytest.mark.parametrize("override, expected_violation", INVALID_NATIVE_PROPOSALS)
@spec_claim(
    SpecClaim(
        "PL-RX-06",
        "pipeline_risk_execution_integrity",
        "§5 fail-closed input boundary",
        "L2b",
        "Non-canonical symbols, unknown native enums, and non-positive quantity/price are "
        "rejected before Risk/Order/Fill/Position side effects.",
        status="implemented",
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


@spec_claim(
    SpecClaim(
        "PL-RX-07",
        "pipeline_risk_execution_integrity",
        "§6 evidence-based reconciliation",
        "L2b",
        "A submitted Order absent from the exchange open-order list remains unresolved "
        "unless real fill evidence exists; reconciliation never invents filled state.",
        status="implemented",
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
        source="adopted",
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


@spec_claim(
    SpecClaim(
        "PL-RX-08",
        "pipeline_risk_execution_integrity",
        "§6 partial-fill continuity",
        "L2b",
        "The broker-emitted partially_filled state remains unresolved and is scanned by "
        "reconciliation instead of falling out of the execution lifecycle.",
        status="implemented",
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

    async def _no_fills(self, symbol=None, market_type="spot"):
        return []

    monkeypatch.setattr(bitget_client.BitgetClient, "get_open_orders", _still_open)
    monkeypatch.setattr(bitget_client.BitgetClient, "get_fills", _no_fills)
    order = Order(
        source="adopted",
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


@spec_claim(
    SpecClaim(
        "PL-RX-09",
        "pipeline_risk_execution_integrity",
        "§7 pre-submit terminal state",
        "L2b",
        "A margin-preparation failure happens before exchange submission and leaves the local "
        "Order in an explicit sanitized error state, never pending.",
        status="implemented",
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
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
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
        status="implemented",
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


@spec_claim(
    SpecClaim(
        "PL-RX-11",
        "pipeline_risk_execution_integrity",
        "§9 native paper adjacent-chain proof",
        "L2b",
        "One native paper Proposal reaches exactly one authoritative RiskDecision and one "
        "Order/Fill/Position chain with immutable intent and complete causal identity.",
        status="implemented",
    )
)
def test_native_paper_chain_consumes_one_authoritative_decision(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot, Opportunity, TradeProposal

    repo.insert_market_snapshot(MarketSnapshot(
        symbol="BTC/USDT", market_type="spot", exchange="binance", price=60_000.0,
    ))

    opportunity = Opportunity(
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        thesis="sanitized production-shaped chain replay",
        confidence="high_confidence",
        confidence_score=82,
        source_agent="spec_replay",
        status="queued",
    )
    repo.insert_opportunity(opportunity)

    result = submit_proposal(
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        stop_price=58_000.0,
        take_profit=64_000.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
        strategy_id="strategy-rx-11",
        opportunity_id=opportunity.id,
        agent_rationale="sanitized replay rationale",
    )

    assert result["status"] == "executed"
    proposal = dict(db.execute(
        "SELECT * FROM trade_proposals WHERE id=?", (result["proposal_id"],)
    ).fetchone())
    decisions = [dict(row) for row in db.execute(
        "SELECT * FROM risk_decisions WHERE proposal_id=? ORDER BY rowid",
        (proposal["id"],),
    ).fetchall()]
    authoritative = [
        row for row in decisions if row["decision_role"] == "authoritative"
    ]
    assert len(authoritative) == 1

    order = dict(db.execute(
        "SELECT * FROM orders WHERE id=?", (result["order_id"],)
    ).fetchone())
    fill = dict(db.execute(
        "SELECT * FROM fills WHERE id=?", (result["fill_id"],)
    ).fetchone())
    position = dict(db.execute(
        "SELECT * FROM positions WHERE id=?", (result["position_id"],)
    ).fetchone())
    decision = authoritative[0]

    assert proposal["status"] == "executed"
    assert proposal["execution_mode"] == "paper_internal"
    assert repo._compute_proposal_hash(TradeProposal.model_validate(proposal)) == proposal["commit_hash"]
    assert decision["approved"] == 1
    assert decision["evaluated_quantity"] == pytest.approx(proposal["quantity"])
    assert decision["proposal_commit_hash"] == proposal["commit_hash"]
    assert order["proposal_id"] == proposal["id"]
    assert order["risk_decision_id"] == decision["id"]
    assert order["status"] == "filled"
    assert fill["order_id"] == order["id"]
    assert position["proposal_id"] == proposal["id"]
    assert position["order_id"] == order["id"]
    assert position["opportunity_id"] == opportunity.id
    for row in (proposal, order, position):
        assert row["symbol"] == "BTC/USDT"
        assert row["market_type"] == "spot"
        assert row["direction"] == "long"
        assert row["execution_mode"] == "paper_internal"


@spec_claim(
    SpecClaim(
        "PL-RX-12",
        "pipeline_risk_execution_integrity",
        "§10 persisted Proposal hash truth",
        "L2c",
        "A Proposal commit hash is computed over the exact immutable row persisted after "
        "runtime provenance is stamped, so current-runtime rows verify from storage.",
        status="implemented",
    )
)
def test_proposal_hash_covers_persisted_runtime_provenance(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.agent_runtime import runtime_session
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    monkeypatch.setattr(
        runtime_session,
        "current_provenance",
        lambda: {
            "runtime_session_id": "runtime-rx-12",
            "producer_commit": "commit-rx-12",
            "behavior_fingerprint": "fingerprint-rx-12",
        },
    )
    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)

    stored = dict(db.execute(
        "SELECT * FROM trade_proposals WHERE id=?", (proposal.id,)
    ).fetchone())
    stored_model = TradeProposal.model_validate(stored)

    assert stored["runtime_session_id"] == "runtime-rx-12"
    assert proposal.runtime_session_id == stored["runtime_session_id"]
    assert proposal.producer_commit == stored["producer_commit"]
    assert proposal.behavior_fingerprint == stored["behavior_fingerprint"]
    assert repo._compute_proposal_hash(stored_model) == stored["commit_hash"]


@spec_claim(
    SpecClaim(
        "PL-RX-13",
        "pipeline_risk_execution_integrity",
        "§10 authoritative integrity gate",
        "L2b",
        "Authoritative Risk fails closed before an Order when the stored Proposal no longer "
        "matches its commit hash, and the gateway records a stable terminal reason.",
        status="implemented",
    )
)
def test_authoritative_risk_rejects_tampered_stored_proposal(tmp_path, monkeypatch):
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
        attribution_snapshot="{}",
    )
    repo.insert_trade_proposal(proposal)
    db.execute(
        "UPDATE trade_proposals SET quantity=? WHERE id=?",
        (0.002, proposal.id),
    )

    result = gateway.execute_existing_proposal(proposal.id)
    stored = repo.get_trade_proposal(proposal.id)

    assert result["status"] == "error"
    assert result["reason_code"] == "proposal_integrity_failed"
    assert stored["status"] == "error"
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE proposal_id=?", (proposal.id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM risk_decisions WHERE proposal_id=? "
        "AND decision_role='authoritative'",
        (proposal.id,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-RX-14",
        "pipeline_risk_execution_integrity",
        "§10 no late Proposal mutation",
        "L2b",
        "Executing a stored Proposal without an attribution snapshot never writes a new "
        "hash-covered snapshot after commit; missing replay context remains honestly missing.",
        status="implemented",
    )
)
def test_execution_does_not_backfill_hash_covered_attribution_snapshot(
    tmp_path, monkeypatch
):
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
        attribution_snapshot=None,
    )
    repo.insert_trade_proposal(proposal)
    original_hash = proposal.commit_hash
    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda _proposal, *, decision_role: SimpleNamespace(
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_id="risk-rx-14",
            violations=[],
            message="Approved",
        ),
    )
    monkeypatch.setattr(
        gateway,
        "_capture_attribution_snapshot_json",
        lambda _symbol: json.dumps({"regime_tag": "late-context"}),
    )
    monkeypatch.setattr(
        gateway,
        "execute_paper_order",
        lambda **_kwargs: {
            "order_id": "order-rx-14",
            "fill_id": "fill-rx-14",
            "position_id": "position-rx-14",
        },
    )
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)

    result = gateway._execute_proposal(proposal)
    stored = dict(db.execute(
        "SELECT * FROM trade_proposals WHERE id=?", (proposal.id,)
    ).fetchone())

    assert result["status"] == "executed"
    assert stored["attribution_snapshot"] is None
    assert stored["commit_hash"] == original_hash
    assert repo._compute_proposal_hash(TradeProposal.model_validate(stored)) == original_hash


@spec_claim(
    SpecClaim(
        "PL-RX-15",
        "pipeline_risk_execution_integrity",
        "§11 honest asynchronous execution state",
        "L2b",
        "An exchange Order accepted but not filled leaves its Proposal submitted, not "
        "executed; executed is reserved for a durably materialized Fill/Position chain.",
        status="implemented",
    )
)
def test_submitted_exchange_order_does_not_mark_proposal_executed(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import bitget_broker, gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, RiskDecision, TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(
        RiskDecision(
            id="risk-authoritative",
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode=proposal.execution_mode,
            proposal_execution_mode=proposal.execution_mode,
            decision_role="authoritative",
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
        )
    )
    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda _proposal, *, decision_role: SimpleNamespace(
            approved=True,
            allowed_execution_mode="bitget_demo",
            decision_id=f"risk-{decision_role}",
            violations=[],
            message="Approved",
        ),
    )
    monkeypatch.setattr(
        gateway, "_prepare_exchange_quantity", lambda *_args: (0.001, None)
    )
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *_args: None)
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)
    def _accepted_order(**_kwargs):
        repo.insert_order(
            Order(
                id="order-rx-15",
                proposal_id=proposal.id,
                risk_decision_id="risk-authoritative",
                client_order_id=proposal.id,
                symbol=proposal.symbol,
                market_type=proposal.market_type,
                direction=proposal.direction,
                order_type=proposal.order_type,
                quantity=proposal.quantity or 0.0,
                price=proposal.price,
                execution_mode=proposal.execution_mode,
                exchange_order_id="exchange-rx-15",
                status="submitted",
            )
        )
        return {
            "order_id": "order-rx-15",
            "exchange_order_id": "exchange-rx-15",
            "order_status": "submitted",
            "fill_id": None,
            "position_id": None,
            "fill_price": None,
            "fill_quantity": 0.0,
        }

    monkeypatch.setattr(
        bitget_broker,
        "execute_live_order",
        _accepted_order,
    )

    result = gateway._execute_proposal(proposal)
    stored = dict(db.execute(
        "SELECT * FROM trade_proposals WHERE id=?", (proposal.id,)
    ).fetchone())

    assert result["status"] == "submitted"
    assert stored["status"] == "submitted"
    assert result["exchange_order_id"] == "exchange-rx-15"


@pytest.mark.parametrize(
    "broker_module,client_name,close_name,execution_mode",
    (
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
    ),
)
@spec_claim(
    SpecClaim(
        "PL-RX-16",
        "pipeline_risk_execution_integrity",
        "§12 pre-submission close failure",
        "L2b",
        "A close whose exchange client cannot initialize is recorded as a known "
        "pre-submission error and does not leave a retry-suppressing pending claim.",
        status="implemented",
    )
)
def test_close_client_initialization_failure_does_not_strand_pending_claim(
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

    broker = importlib.import_module(broker_module)

    def _client_initialization_failed(*_args, **_kwargs):
        raise RuntimeError("private-client-initialization-detail")

    monkeypatch.setattr(broker, client_name, _client_initialization_failed)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=60_000.0,
        current_price=60_100.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)

    with pytest.raises(RuntimeError, match="private-client-initialization-detail"):
        getattr(broker, close_name)(position.id, reason="stop_loss")

    first_order = repo.list_orders(limit=10)[0]
    stored_position = repo._select_one("positions", position.id)
    assert first_order["status"] == "error"
    assert (
        first_order["error_reason"]
        == "close_client_initialization_failed_before_submission"
    )
    assert "private-client" not in first_order["error_reason"]
    assert stored_position["pending_close_status"] == "error"

    with pytest.raises(RuntimeError, match="private-client-initialization-detail"):
        getattr(broker, close_name)(position.id, reason="stop_loss")
    assert len(repo.list_orders(limit=10)) == 2


@spec_claim(
    SpecClaim(
        "PL-RX-17",
        "pipeline_risk_execution_integrity",
        "§12 pre-submission entry failure",
        "L2b",
        "A Bitget entry whose client cannot initialize is recorded as a known "
        "pre-submission error and remains safe to retry without exchange ambiguity.",
        status="implemented",
    )
)
def test_bitget_entry_client_initialization_failure_is_not_submission_unknown(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import bitget_broker
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="limit",
        quantity=0.01,
        price=60_000.0,
        execution_mode="bitget_demo",
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

    def _client_initialization_failed(*_args, **_kwargs):
        raise RuntimeError("private-client-initialization-detail")

    monkeypatch.setattr(
        bitget_broker, "BitgetClient", _client_initialization_failed
    )
    kwargs = {
        "proposal_id": proposal.id,
        "risk_decision_id": decision.id,
        "symbol": proposal.symbol,
        "market_type": proposal.market_type,
        "direction": proposal.direction,
        "order_type": proposal.order_type,
        "quantity": proposal.quantity,
        "price": proposal.price,
        "execution_mode": proposal.execution_mode,
        "leverage": 10,
    }

    with pytest.raises(RuntimeError, match="private-client-initialization-detail"):
        bitget_broker.execute_live_order(**kwargs)

    first_order = repo.list_orders(limit=10)[0]
    assert first_order["status"] == "error"
    assert (
        first_order["error_reason"]
        == "order_client_initialization_failed_before_submission"
    )
    assert "private-client" not in first_order["error_reason"]

    with pytest.raises(RuntimeError, match="private-client-initialization-detail"):
        bitget_broker.execute_live_order(**kwargs)
    assert len(repo.list_orders(limit=10)) == 2


@spec_claim(
    SpecClaim(
        "PL-RX-18",
        "pipeline_risk_execution_integrity",
        "§10 evaluated Proposal snapshot identity",
        "L2b",
        "Authoritative Risk permits venue quantity normalization only. Any other "
        "hash-covered difference between the evaluated object and stored Proposal "
        "fails closed before an authoritative RiskDecision or Order is persisted.",
        status="implemented",
    )
)
def test_authoritative_risk_rejects_every_non_quantity_hash_field_mismatch(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)

    from aletheia.risk.engine import ProposalIntegrityError, evaluate_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    mutations = {
        "runtime_session_id": "altered-runtime-session",
        "producer_commit": "altered-producer-commit",
        "behavior_fingerprint": "altered-behavior-fingerprint",
        "id": None,
        "strategy_id": "altered-strategy",
        "opportunity_id": "altered-opportunity",
        "symbol": "ETH/USDT",
        "market_type": "usdt_futures",
        "direction": "short",
        "order_type": "limit",
        "price": 61_000.0,
        "leverage": 2,
        "stop_price": 59_000.0,
        "take_profit": 63_000.0,
        "risk_reward": 2.0,
        "execution_mode": "bitget_demo",
        "agent_rationale": "altered rationale",
        "attribution_snapshot": '{"regime_tag":"altered"}',
        "source": "adopted",
        "trade_school": "serenity",
        "created_at": "2099-01-01T00:00:00+00:00",
        "parent_hash": "altered-parent-hash",
        "stop_source": "altered-stop-source",
        "expected_horizon_hours": 48.0,
    }
    model_fields = set(
        TradeProposal(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.001,
            price=60_000.0,
        ).model_dump(exclude=repo._PROPOSAL_HASH_EXCLUDES)
    )
    assert set(mutations) == model_fields - {"quantity"}

    accepted_fields: list[str] = []
    unexpected_errors: dict[str, str] = {}
    proposal_ids: list[str] = []
    for field, altered_value in mutations.items():
        proposal = TradeProposal(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60_000.0,
            execution_mode="paper_internal",
        )
        repo.insert_trade_proposal(proposal)
        proposal_ids.append(proposal.id)
        if field == "id":
            altered_value = f"{proposal.id}-altered"
        evaluated = proposal.model_copy(update={field: altered_value})

        try:
            evaluate_proposal(evaluated, decision_role="authoritative")
        except ProposalIntegrityError:
            continue
        except Exception as exc:  # fail-closed must use the stable integrity error
            unexpected_errors[field] = type(exc).__name__
        else:
            accepted_fields.append(field)

    placeholders = ",".join("?" for _ in proposal_ids)
    authoritative_count = db.execute(
        f"SELECT COUNT(*) FROM risk_decisions WHERE decision_role='authoritative' "
        f"AND proposal_id IN ({placeholders})",
        proposal_ids,
    ).fetchone()[0]
    order_count = db.execute(
        f"SELECT COUNT(*) FROM orders WHERE proposal_id IN ({placeholders})",
        proposal_ids,
    ).fetchone()[0]

    assert (
        accepted_fields,
        unexpected_errors,
        authoritative_count,
        order_count,
    ) == ([], {}, 0, 0)


@spec_claim(
    SpecClaim(
        "PL-RX-19",
        "pipeline_risk_execution_integrity",
        "§12 close post-durable audit isolation",
        "L2c",
        "After a close Order and Position carry a retry-suppressing exchange state, an "
        "audit sink failure cannot hide that truth or permit a second close submission.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    "broker_module,client_name,close_name,execution_mode",
    CLOSE_TIMEOUT_CASES,
)
@pytest.mark.parametrize(
    ("exchange_outcome", "expected_status"),
    [
        ("filled", "materialization_missing"),
        ("timeout", "submission_unknown"),
    ],
)
def test_close_post_durable_audit_failure_preserves_state_and_suppresses_retry(
    tmp_path,
    monkeypatch,
    caplog,
    broker_module,
    client_name,
    close_name,
    execution_mode,
    exchange_outcome,
    expected_status,
):
    import importlib

    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import post_commit_audit
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    calls: list[str] = []

    class _CloseClient:
        def __init__(self, *args, **kwargs):
            pass

        async def place_order(self, order):
            calls.append(order.client_oid)
            if exchange_outcome == "timeout":
                raise TimeoutError("exchange close acknowledgement unknown")
            return SimpleNamespace(
                order_id=f"venue-close-{exchange_outcome}",
                status="filled",
            )

        async def close(self):
            pass

    def fail_secondary_audit(_event):
        raise RuntimeError("token=must-not-leak-from-close-audit-sink")

    broker = importlib.import_module(broker_module)
    monkeypatch.setattr(broker, client_name, _CloseClient)
    monkeypatch.setattr(post_commit_audit.jsonl, "write_audit", fail_secondary_audit)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=60_000.0,
        current_price=60_100.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)

    if exchange_outcome == "timeout":
        with pytest.raises(TimeoutError):
            getattr(broker, close_name)(position.id, reason="stop_loss")
    else:
        result = getattr(broker, close_name)(position.id, reason="stop_loss")
        assert result["status"] == expected_status
        assert result["closed"] is False

    stored_position = repo._select_one("positions", position.id)
    orders = repo.list_orders(limit=20)
    assert stored_position["pending_close_status"] == expected_status
    assert len(orders) == 1
    assert orders[0]["status"] == expected_status
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text

    replay = getattr(broker, close_name)(position.id, reason="stop_loss")
    assert replay["closed"] is False
    assert replay["already_pending"] is True
    assert calls == [f"close-{position.id}"]
    assert len(repo.list_orders(limit=20)) == 1


@spec_claim(
    SpecClaim(
        "PL-RX-20",
        "pipeline_risk_execution_integrity",
        "§12 entry post-durable audit isolation",
        "L2c",
        "Every exchange broker preserves its durable accepted/unresolved entry Order when the "
        "secondary audit sink fails, and replay cannot submit the same Proposal again.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("broker_module", "client_name", "execution_mode"),
    [
        (
            "aletheia.execution.bitget_broker",
            "BitgetClient",
            "bitget_demo",
        ),
        (
            "aletheia.execution.okx_broker",
            "OKXClient",
            "okx_demo",
        ),
        (
            "aletheia.execution.binance_broker",
            "BinanceClient",
            "binance_testnet",
        ),
    ],
)
@pytest.mark.parametrize(
    ("exchange_outcome", "expected_status"),
    [
        ("filled", "materialization_missing"),
        ("timeout", "submission_unknown"),
    ],
)
def test_entry_post_durable_audit_failure_preserves_order_and_suppresses_retry(
    tmp_path,
    monkeypatch,
    caplog,
    broker_module,
    client_name,
    execution_mode,
    exchange_outcome,
    expected_status,
):
    import importlib

    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import post_commit_audit
    from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    calls: list[str] = []

    class _EntryClient:
        def __init__(self, *args, **kwargs):
            pass

        async def place_order(self, order):
            calls.append(order.client_oid)
            if exchange_outcome == "timeout":
                raise TimeoutError("exchange entry acknowledgement unknown")
            return SimpleNamespace(
                order_id=f"venue-entry-{exchange_outcome}",
                status="filled",
            )

        async def close(self):
            pass

    def fail_secondary_audit(_event):
        raise RuntimeError("token=must-not-leak-from-entry-audit-sink")

    broker = importlib.import_module(broker_module)
    monkeypatch.setattr(broker, client_name, _EntryClient)
    monkeypatch.setattr(post_commit_audit.jsonl, "write_audit", fail_secondary_audit)
    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60_000.0,
        execution_mode=execution_mode,
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
    kwargs = {
        "proposal_id": proposal.id,
        "risk_decision_id": decision.id,
        "symbol": proposal.symbol,
        "market_type": proposal.market_type,
        "direction": proposal.direction,
        "order_type": proposal.order_type,
        "quantity": proposal.quantity,
        "price": proposal.price,
        "execution_mode": proposal.execution_mode,
        "leverage": 10,
    }

    if exchange_outcome == "timeout":
        with pytest.raises(SubmissionUnknownError):
            broker.execute_live_order(**kwargs)
    else:
        result = broker.execute_live_order(**kwargs)
        assert result["order_status"] == expected_status

    orders = repo.list_orders(limit=20)
    assert len(orders) == 1
    assert orders[0]["status"] == expected_status
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text

    replay_error = (
        PostSubmitDBError
        if expected_status == "materialization_missing"
        else SubmissionUnknownError
    )
    with pytest.raises(replay_error):
        broker.execute_live_order(**kwargs)
    assert calls == [proposal.id]
    assert len(repo.list_orders(limit=20)) == 1


@spec_claim(
    SpecClaim(
        "PL-RX-21",
        "pipeline_risk_execution_integrity",
        "§12 overfilled close retry suppression",
        "L2c",
        "An overfilled close claim is a permanent manual-reconciliation state: every broker "
        "and the position manager must suppress another close submission.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    "broker_module,client_name,close_name,execution_mode",
    CLOSE_TIMEOUT_CASES,
)
def test_overfilled_close_claim_never_reaches_exchange_again(
    tmp_path,
    monkeypatch,
    broker_module,
    client_name,
    close_name,
    execution_mode,
):
    import importlib

    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio import position_manager
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Position

    exchange_calls: list[str] = []

    class _MustNotReachExchange:
        def __init__(self, *args, **kwargs):
            exchange_calls.append("client_init")

        async def place_order(self, order):
            exchange_calls.append(order.client_oid)
            raise AssertionError("overfilled close claim reached exchange")

        async def close(self):
            pass

    broker = importlib.import_module(broker_module)
    monkeypatch.setattr(broker, client_name, _MustNotReachExchange)
    position = Position(
        id="overfilled-close-position",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=60_000.0,
        current_price=60_100.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(position)
    close_order = Order(
        id="overfilled-close-order",
        client_order_id=f"close-{position.id}",
        position_id=position.id,
        close_reason="stop_loss",
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        order_type="market",
        quantity=position.quantity,
        price=position.current_price,
        execution_mode=position.execution_mode,
        exchange_order_id="venue-overfilled-close",
        status="overfilled",
    )
    repo.insert_order(close_order)
    repo.update_position(
        position.id,
        pending_close_order_id=close_order.id,
        pending_close_status="overfilled",
    )

    result = getattr(broker, close_name)(position.id, reason="stop_loss")

    stored_position = repo._select_one("positions", position.id)
    orders = repo.list_orders(limit=20)
    assert result["already_pending"] is True
    assert result["status"] == "overfilled"
    assert exchange_calls == []
    assert len(orders) == 1
    assert orders[0]["id"] == close_order.id
    assert orders[0]["status"] == "overfilled"
    assert stored_position["pending_close_order_id"] == close_order.id
    assert stored_position["pending_close_status"] == "overfilled"
    assert {
        "pending",
        "submitted",
        "partial",
        "partially_filled",
        "submission_unknown",
        "materialization_missing",
        "overfilled",
    }.issubset(position_manager._OPEN_CLOSE_STATES)
