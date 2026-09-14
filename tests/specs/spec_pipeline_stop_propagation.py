"""Spec: Pipeline — Protective Stop / Take-Profit Propagation (end-to-end)

Regression lock for the EC-stop bug: the three real-exchange paths
(okx/binance/bitget) received `stop_price` / `take_profit_price` but historically
dropped them before Position materialization, so demo/live positions opened with
stop_price=None and silently fell back to the default-% stop in position_manager.
The AI's intended protective levels were discarded between proposal and position.

These tests assert the 1-to-1 contract end-to-end:

    proposal.stop_price  ──gateway──>  UTA acknowledgement
                          ──private fill reconciliation──>
                          positions.stop_price (EXACT match)

Coverage uses the same coordinator for okx_demo, binance_testnet, and Bitget.
Only normalized UTA submission/private-history reports are isolated; the
order→fill→position DB chain and approved protection propagation run for real.

Contract: pipeline_stop_propagation
Claim prefix: PL-STOP
"""

import sqlite3

import pytest

from tests.execution_authority import seed_order_authority
from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared isolated DB + UTA boundary setup
# ---------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
    """Wire a fresh isolated SQLite DB into aletheia storage for one test."""
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
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    run_migrations()
    # Gateway cases in this module exercise an already-connected OKX demo
    # account.  Account-leverage Risk now rejects when named equity is unknown,
    # so make that external cache assumption explicit instead of falling back to
    # configured starting capital.
    import aletheia.portfolio.exchange_sync as exchange_sync

    monkeypatch.setattr(
        exchange_sync,
        "get_unified_cached_state",
        lambda: {
            "age_seconds": 1.0,
            "status": "healthy",
            "accounts": [
                {"broker_id": "okx_demo", "equity_usdt": 100_000.0},
            ],
            "total_equity_usdt": 100_000.0,
        },
    )
    return conn


def _stub_uta_execution(monkeypatch):
    """Isolate account qualification and venue rules; no credential access."""
    from aletheia.execution import coordinator, gateway, order_constraints
    monkeypatch.setattr(order_constraints, "prepare_exchange_quantity", lambda mode, symbol, market, qty, price: qty)
    monkeypatch.setattr(gateway, "_prepare_exchange_quantity", lambda mode, symbol, market, qty, price: (qty, None))
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *_args: None)


_BROKERS = ["okx_demo", "binance_testnet", "bitget_live_limited"]


def _patch_filled_place_order(monkeypatch, fill_price):
    from datetime import datetime, timezone
    from tests.uta.test_execution_coordinator import _Adapter
    from aletheia.uta.models import SubmissionReport, CommandEvidence, OrderStatusReport, OrderLifecycle, UTACapabilities

    sequence = 0
    captured = []

    async def submit(command):
        nonlocal sequence
        captured.append(command)
        sequence += 1
        now = datetime.now(timezone.utc)
        return SubmissionReport(identity=command.expected_account, command_id=command.command_id,
            evidence=CommandEvidence.ACKNOWLEDGED, received_at=now,
            client_order_id=command.client_order_id, venue_order_id=f"EX-STOP-{sequence}", profile_version="1",
            order=OrderStatusReport(identity=command.expected_account, instrument=command.instrument,
                side=command.side, order_type=command.order_type, original_quantity=command.quantity,
                filled_quantity=command.quantity, price=str(fill_price), state=OrderLifecycle.FILLED, received_at=now))

    def adapter(mode):
        instance = _Adapter(submit, mode)
        instance.capabilities = UTACapabilities(source="isolated_test")
        return instance

    monkeypatch.setattr("aletheia.execution.coordinator.create_uta_adapter", adapter)
    return captured


def _patch_private_fill_history(monkeypatch, *, fill_price: float, fee_rate: float = 0.0006, executed_at=None):
    """Normalized private reports scoped to each persisted Order identity."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from aletheia.execution import reconciliation
    from aletheia.storage import repositories as repo
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import FillReport, FillListReport, OrderListReport, OrderQueryReport, FundingLedgerReport
    from tests.uta.test_execution_coordinator import _identity

    modes = ("okx_demo", "binance_testnet", "bitget_demo", "okx_live_limited", "binance_live_limited", "bitget_live_limited")
    monkeypatch.setattr(reconciliation, "build_uta_registry", lambda *args: SimpleNamespace(
        configured_identities=lambda: tuple(_identity(mode) for mode in modes)))

    async def query(mode, method, request, *, expected_account):
        assert expected_account == _identity(mode)
        now = datetime.now(timezone.utc)
        envelope = dict(identity=expected_account, completeness="complete", scope=request.scope,
                        received_at=now, profile_version="1")
        if method == "get_open_orders":
            return OrderListReport(**envelope)
        if method == "get_order":
            return OrderQueryReport(**envelope)
        if method == "get_funding_ledger":
            return FundingLedgerReport(**envelope)
        assert method == "get_fills"
        fills = []
        for order in repo.list_orders(limit=1000):
            if order["execution_mode"] != mode or not order["exchange_order_id"]:
                continue
            if request.venue_order_id and request.venue_order_id != order["exchange_order_id"]:
                continue
            if request.client_order_id and request.client_order_id != order["client_order_id"]:
                continue
            instrument = instrument_from_local(order["symbol"], order["market_type"])
            if request.scope.instrument and request.scope.instrument != instrument:
                continue
            fills.append(FillReport(identity=expected_account, instrument=instrument,
                venue_order_id=order["exchange_order_id"], client_order_id=order["client_order_id"],
                venue_fill_id=f"PRIVATE-{mode}-{order["exchange_order_id"]}",
                side="buy" if order["direction"] == "long" else "sell", quantity=str(order["quantity"]),
                price=str(fill_price), fee=str(fill_price * order["quantity"] * fee_rate), fee_currency="USDT",
                executed_at=executed_at or datetime.fromtimestamp(1784050000, tz=timezone.utc), received_at=now))
        return FillListReport(**envelope, fills=tuple(fills))

    monkeypatch.setattr(reconciliation, "query_uta", query)


def _materialize_exchange_result(result: dict) -> str:
    """Return the Position id after exact private-fill reconciliation."""
    if result.get("position_id"):
        return str(result["position_id"])
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    reconcile()
    order = repo.get_order(result["order_id"])
    assert order is not None and order["status"] == "filled"
    assert order.get("position_id")
    return str(order["position_id"])


# ---------------------------------------------------------------------------
# PL-STOP-01  L2b  private fill persists stop_price + take_profit_price (×3 brokers)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-STOP-01", "pipeline_stop_propagation", "§1 open_persists_stop", "L2b",
        "Order-matched private fill materialization persists the Proposal stop_price "
        "AND take_profit onto the new Position row (okx/binance/bitget) — not None",
    ),
)
@pytest.mark.parametrize("exec_mode", _BROKERS)
def test_broker_open_persists_stop_and_tp(
    tmp_path, monkeypatch, exec_mode
):
    _setup_db(tmp_path, monkeypatch)
    _stub_uta_execution(monkeypatch)
    _patch_filled_place_order(monkeypatch, fill_price=60000.0)
    _patch_private_fill_history(monkeypatch, fill_price=60000.0)

    from aletheia.storage import repositories as repo

    from aletheia.execution import coordinator as broker
    label = exec_mode
    proposal_id = f"p-stop-{label}"
    risk_decision_id = f"rd-stop-{label}"
    seed_order_authority(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="short",
        order_type="market",
        quantity=0.01,
        price=60000.0,
        execution_mode=exec_mode,
        stop_price=61200.0,
        take_profit=57600.0,
    )
    result = broker.execute_order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="short",
        order_type="market",
        quantity=0.01,
        price=60000.0,
        execution_mode=exec_mode,
        leverage=10,
        stop_price=61200.0,           # short → stop above entry
        take_profit_price=57600.0,    # short → TP below entry
    )

    position_id = _materialize_exchange_result(result)

    pos = repo._select_one("positions", position_id)
    assert pos is not None, f"{label}: position row must exist"
    assert pos["stop_price"] == 61200.0, (
        f"{label}: stop_price dropped on the floor "
        f"(got {pos['stop_price']!r}) — EC-stop regression"
    )
    assert pos["take_profit_price"] == 57600.0, (
        f"{label}: take_profit_price dropped (got {pos['take_profit_price']!r})"
    )


# ---------------------------------------------------------------------------
# PL-STOP-02  L2b  averaging-in tightens (never loosens) the stop (×3 brokers)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-STOP-02", "pipeline_stop_propagation", "§2 average_in_tightens", "L2b",
        "A second fill into the same position keeps the MORE protective stop: "
        "for a short, the lower stop wins (okx/binance/bitget)",
    ),
)
@pytest.mark.parametrize("exec_mode", _BROKERS)
def test_broker_average_in_tightens_stop(
    tmp_path, monkeypatch, exec_mode
):
    _setup_db(tmp_path, monkeypatch)
    _stub_uta_execution(monkeypatch)
    _patch_filled_place_order(monkeypatch, fill_price=60000.0)
    _patch_private_fill_history(monkeypatch, fill_price=60000.0)

    from aletheia.storage import repositories as repo

    from aletheia.execution import coordinator as broker
    label = exec_mode

    # First fill: short with a loose stop at 61500.
    first_proposal_id = f"p-avg1-{label}"
    first_risk_decision_id = f"rd-avg1-{label}"
    seed_order_authority(
        proposal_id=first_proposal_id,
        risk_decision_id=first_risk_decision_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="short",
        order_type="market",
        quantity=0.01,
        price=60000.0,
        execution_mode=exec_mode,
        stop_price=61500.0,
    )
    r1 = broker.execute_order(
        proposal_id=first_proposal_id, risk_decision_id=first_risk_decision_id,
        symbol="BTC/USDT", market_type="usdt_futures", direction="short",
        order_type="market", quantity=0.01, price=60000.0,
        execution_mode=exec_mode, leverage=10, stop_price=61500.0,
    )
    position_id = _materialize_exchange_result(r1)
    # Second fill into the same position with a tighter (lower) short stop at 60800.
    second_proposal_id = f"p-avg2-{label}"
    second_risk_decision_id = f"rd-avg2-{label}"
    seed_order_authority(
        proposal_id=second_proposal_id,
        risk_decision_id=second_risk_decision_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="short",
        order_type="market",
        quantity=0.01,
        price=60000.0,
        execution_mode=exec_mode,
        stop_price=60800.0,
    )
    r2 = broker.execute_order(
        proposal_id=second_proposal_id, risk_decision_id=second_risk_decision_id,
        symbol="BTC/USDT", market_type="usdt_futures", direction="short",
        order_type="market", quantity=0.01, price=60000.0,
        execution_mode=exec_mode, leverage=10, stop_price=60800.0,
    )
    _materialize_exchange_result(r2)

    pos = repo._select_one("positions", position_id)
    assert pos["quantity"] == pytest.approx(0.02), f"{label}: should have averaged in"
    assert pos["stop_price"] == 60800.0, (
        f"{label}: averaging in must keep the tighter short stop "
        f"(expected 60800.0, got {pos['stop_price']!r})"
    )


# ---------------------------------------------------------------------------
# PL-STOP-03  L2b  full gateway path: proposal.stop_price → position.stop_price (okx_demo)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-STOP-03", "pipeline_stop_propagation", "§3 gateway_end_to_end", "L2b",
        "submit_proposal(okx_demo, stop_price=S) → approved → broker → "
        "the opened position has stop_price == S and a derived take_profit_price",
    ),
)
def test_gateway_demo_proposal_propagates_stop(tmp_path, monkeypatch):
    """The real live path in production. okx_demo is NOT live, so the risk engine's
    live-only stale-data gate is skipped; default config (permission=Paper) approves
    a small demo proposal."""
    _setup_db(tmp_path, monkeypatch)
    _stub_uta_execution(monkeypatch)
    _patch_filled_place_order(monkeypatch, fill_price=1551.57)
    _patch_private_fill_history(monkeypatch, fill_price=1551.57,)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db
    from aletheia.config.loader import load_config
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot

    entry = 1551.57
    stop = round(entry * 1.02, 8)  # short stop ~2% above entry
    take_profit = round(entry * 0.96, 8)

    # Demo path enforces a < 1800s market-snapshot freshness check (CR-RE).
    insert_market_snapshot(MarketSnapshot(
        symbol="ETH/USDT", market_type="usdt_futures", exchange="okx", price=entry,
    ))

    result = submit_proposal(
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=0.01,
        price=entry,
        stop_price=stop,
        take_profit=take_profit,
        leverage=5,
        execution_mode="okx_demo",
    )

    if result.get("status") != "materialization_missing":
        pytest.fail(
            f"okx_demo proposal did not enter private-fill handoff: "
            f"{result.get('status')} / {result.get('risk_violations')}"
        )

    position_id = _materialize_exchange_result(result)
    db = get_db()
    pos = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()

    assert pos is not None, "position row must exist after gateway execution"
    assert pos["stop_price"] == stop, (
        f"gateway dropped the proposal stop: expected {stop}, got {pos['stop_price']!r}"
    )
    assert pos["take_profit_price"] == pytest.approx(take_profit), (
        f"proposal TP missing/wrong: expected {take_profit}, got {pos['take_profit_price']!r}"
    )


# ---------------------------------------------------------------------------
# PL-STOP-04  L2c  invariant scan: no open demo/live position with a NULL stop
#              when its originating proposal carried a stop_price
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-STOP-04", "pipeline_stop_propagation", "§4 no_silent_drop", "L2c",
        "After driving proposals with stops through the gateway, every resulting open "
        "position whose proposal had a stop_price must itself have a non-null stop_price",
    ),
)
def test_no_open_position_silently_loses_proposal_stop(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _stub_uta_execution(monkeypatch)
    _patch_filled_place_order(monkeypatch, fill_price=100.0)
    _patch_private_fill_history(monkeypatch, fill_price=100.0,)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot

    # Drive a few demo proposals, each carrying a protective stop.
    for i, (sym, direction, stop) in enumerate([
        ("BTC/USDT", "short", 102.0),
        ("SOL/USDT", "long", 98.0),
        ("XRP/USDT", "short", 103.0),
    ]):
        # Demo path enforces market-snapshot freshness (< 1800s).
        insert_market_snapshot(MarketSnapshot(
            symbol=sym, market_type="usdt_futures", exchange="okx", price=100.0,
        ))
        result = submit_proposal(
            symbol=sym, market_type="usdt_futures", direction=direction,
            quantity=0.01,
            price=100.0,
            leverage=1,
            stop_price=stop,
            take_profit=95.0 if direction == "short" else 105.0,
            execution_mode="okx_demo",
        )
        _materialize_exchange_result(result)

    db = get_db()
    # Join open positions back to their originating proposal; flag any that lost the stop.
    rows = db.execute(
        """
        SELECT p.id AS pid, p.symbol, p.stop_price AS pos_stop, tp.stop_price AS prop_stop
        FROM positions p
        JOIN trade_proposals tp ON p.proposal_id = tp.id
        WHERE p.status = 'open' AND tp.stop_price IS NOT NULL
        """
    ).fetchall()

    assert rows, "expected at least one open position linked to a stop-bearing proposal"
    offenders = [dict(r) for r in rows if r["pos_stop"] is None]
    assert not offenders, (
        f"positions silently lost their proposal stop_price: {offenders}"
    )
