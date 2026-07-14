"""Spec: Pipeline — Protective Stop / Take-Profit Propagation (end-to-end)

Regression lock for the EC-stop bug: the three real-exchange paths
(okx/binance/bitget) received `stop_price` / `take_profit_price` but historically
dropped them before Position materialization, so demo/live positions opened with
stop_price=None and silently fell back to the default-% stop in position_manager.
The AI's intended protective levels were discarded between proposal and position.

These tests assert the 1-to-1 contract end-to-end:

    proposal.stop_price  ──gateway──>  broker acknowledgement
                          ──private fill reconciliation──>
                          positions.stop_price (EXACT match)

Coverage is intentionally broker-PARALLEL: the same claim is verified against
okx_demo, binance_testnet, AND bitget so a future copy-paste into any one broker
cannot silently re-drop the stop. Only the exchange HTTP boundary is mocked
(place_order / private history); the order→fill→position DB chain runs for real.

Contract: pipeline_stop_propagation
Claim prefix: PL-STOP
"""

import sqlite3

import pytest

from tests.execution_authority import seed_order_authority
from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared DB + secrets setup
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


def _stub_exchange_secrets(monkeypatch):
    """Make every exchange client constructible without real credentials."""
    import aletheia.config.secrets as secrets
    monkeypatch.setattr(secrets, "get_secret", lambda *a, **k: "test-secret")
    # Clients import get_secret into their own module namespace.
    for modpath in (
        "aletheia.exchanges.okx.client",
        "aletheia.exchanges.binance.client",
        "aletheia.exchanges.bitget.client",
    ):
        try:
            mod = __import__(modpath, fromlist=["get_secret"])
            monkeypatch.setattr(mod, "get_secret", lambda *a, **k: "test-secret", raising=False)
        except Exception:
            pass
    # Skip the external-API order-constraints fetch in CI/offline environments.
    # The gateway calls prepare_exchange_quantity before executing any real-exchange
    # proposal; without network access it raises ConstraintUnavailable → rejected.
    # Tests already mock place_order; returning the quantity unchanged is correct
    # because the tests assert post-fill DB state, not size normalisation.
    import aletheia.execution.order_constraints as _oc
    monkeypatch.setattr(_oc, "prepare_exchange_quantity", lambda _mode, _sym, _mtype, qty, _price: qty)
    import aletheia.execution.gateway as _gw
    monkeypatch.setattr(_gw, "_prepare_exchange_quantity", lambda _mode, _sym, _mtype, qty, _price: (qty, None))
    # Also prevent the OKX demo symbol-list refresh from hitting the network.
    import aletheia.execution.okx_broker as _okx
    monkeypatch.setattr(_okx, "_get_demo_symbols", lambda *a, **k: None)


# Each broker: (label, module path, OrderResponse class path, default execution_mode)
_BROKERS = [
    ("okx", "aletheia.execution.okx_broker", "aletheia.exchanges.okx.client", "okx_demo"),
    ("binance", "aletheia.execution.binance_broker", "aletheia.exchanges.binance.client", "binance_testnet"),
    ("bitget", "aletheia.execution.bitget_broker", "aletheia.exchanges.bitget.client", "bitget_live_limited"),
]


def _patch_filled_place_order(monkeypatch, client_module_path, fill_price):
    """Patch submission acknowledgement; private fills remain a separate fact."""
    client_mod = __import__(client_module_path, fromlist=["OrderResponse"])
    OrderResponse = client_mod.OrderResponse

    # Find the client class (OKXClient / BinanceClient / BitgetClient).
    client_cls = next(
        getattr(client_mod, n)
        for n in dir(client_mod)
        if n.endswith("Client") and isinstance(getattr(client_mod, n), type)
    )

    sequence = {"value": 0}

    async def fake_place_order(self, order):
        sequence["value"] += 1
        return OrderResponse(
            order_id=f"EX-STOP-{sequence['value']}",
            client_oid=getattr(order, "client_oid", None),
            status="filled",
            fill_price=fill_price,
            filled_quantity=order.quantity,
        )

    async def fake_close(self):
        return None

    monkeypatch.setattr(client_cls, "place_order", fake_place_order)
    monkeypatch.setattr(client_cls, "close", fake_close, raising=False)


def _private_fill_row(
    order: dict,
    *,
    fill_price: float,
    fee_rate: float,
) -> dict:
    return {
        "orderId": order["exchange_order_id"],
        "tradeId": (
            f"PRIVATE-{order['execution_mode']}-{order['exchange_order_id']}"
        ),
        "clientOid": order.get("client_order_id"),
        "fillPrice": str(fill_price),
        "fillQuantity": str(order["quantity"]),
        "fee": str(float(fill_price) * float(order["quantity"]) * fee_rate),
        "feeCoin": "USDT",
        "cTime": "1784050000000",
    }


def _patch_private_fill_history(
    monkeypatch,
    broker_module_path: str,
    client_module_path: str,
    *,
    fill_price: float,
    fee_rate: float = 0.0006,
):
    """Expose exact per-fill evidence after the submission handoff."""
    broker = __import__(broker_module_path, fromlist=["fetch_private_fill_evidence"])
    client_mod = __import__(client_module_path, fromlist=["BitgetClient"])

    if hasattr(broker, "fetch_private_fill_evidence"):
        async def _evidence(order):
            return [
                _private_fill_row(
                    order,
                    fill_price=fill_price,
                    fee_rate=fee_rate,
                )
            ]

        monkeypatch.setattr(broker, "fetch_private_fill_evidence", _evidence)

    if hasattr(client_mod, "BitgetClient"):
        client_cls = client_mod.BitgetClient

        async def _open_orders(self, **kwargs):
            return []

        async def _fills(self, **kwargs):
            from aletheia.execution.reconciliation import _list_unresolved_core_orders

            execution_mode = (
                "bitget_demo" if getattr(self, "mode", "live") == "demo"
                else "bitget_live_limited"
            )
            return [
                _private_fill_row(
                    order,
                    fill_price=fill_price,
                    fee_rate=fee_rate,
                )
                for order in _list_unresolved_core_orders()
                if order.get("execution_mode") == execution_mode
                and order.get("exchange_order_id")
            ]

        async def _order_fills(self, *, order_id, **kwargs):
            from aletheia.storage import repositories as repo

            order = next(
                (
                    row for row in repo.list_orders(limit=1000)
                    if str(row.get("exchange_order_id") or "") == str(order_id)
                ),
                None,
            )
            return (
                [
                    _private_fill_row(
                        order,
                        fill_price=fill_price,
                        fee_rate=fee_rate,
                    )
                ]
                if order is not None
                else []
            )

        monkeypatch.setattr(client_cls, "get_open_orders", _open_orders)
        monkeypatch.setattr(client_cls, "get_fills", _fills)
        monkeypatch.setattr(client_cls, "get_order_fills", _order_fills)


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
@pytest.mark.parametrize("label,broker_path,client_path,exec_mode", _BROKERS)
def test_broker_open_persists_stop_and_tp(
    tmp_path, monkeypatch, label, broker_path, client_path, exec_mode
):
    _setup_db(tmp_path, monkeypatch)
    _stub_exchange_secrets(monkeypatch)
    _patch_filled_place_order(monkeypatch, client_path, fill_price=60000.0)
    _patch_private_fill_history(
        monkeypatch, broker_path, client_path, fill_price=60000.0
    )

    from aletheia.storage import repositories as repo

    broker = __import__(broker_path, fromlist=["execute_live_order"])
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
    result = broker.execute_live_order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="short",
        order_type="market",
        quantity=0.01,
        price=60000.0,
        execution_mode=exec_mode,
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
@pytest.mark.parametrize("label,broker_path,client_path,exec_mode", _BROKERS)
def test_broker_average_in_tightens_stop(
    tmp_path, monkeypatch, label, broker_path, client_path, exec_mode
):
    _setup_db(tmp_path, monkeypatch)
    _stub_exchange_secrets(monkeypatch)
    _patch_filled_place_order(monkeypatch, client_path, fill_price=60000.0)
    _patch_private_fill_history(
        monkeypatch, broker_path, client_path, fill_price=60000.0
    )

    from aletheia.storage import repositories as repo

    broker = __import__(broker_path, fromlist=["execute_live_order"])

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
    r1 = broker.execute_live_order(
        proposal_id=first_proposal_id, risk_decision_id=first_risk_decision_id,
        symbol="BTC/USDT", market_type="usdt_futures", direction="short",
        order_type="market", quantity=0.01, price=60000.0,
        execution_mode=exec_mode, stop_price=61500.0,
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
    r2 = broker.execute_live_order(
        proposal_id=second_proposal_id, risk_decision_id=second_risk_decision_id,
        symbol="BTC/USDT", market_type="usdt_futures", direction="short",
        order_type="market", quantity=0.01, price=60000.0,
        execution_mode=exec_mode, stop_price=60800.0,
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
    _stub_exchange_secrets(monkeypatch)
    _patch_filled_place_order(monkeypatch, "aletheia.exchanges.okx.client", fill_price=1551.57)
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.okx_broker",
        "aletheia.exchanges.okx.client",
        fill_price=1551.57,
    )

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db
    from aletheia.config.loader import load_config
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot

    entry = 1551.57
    stop = round(entry * 1.02, 8)  # short stop ~2% above entry
    take_profit = round(entry * 0.96, 8)

    # Demo path enforces a < 1800s market-snapshot freshness check (CR-RE).
    insert_market_snapshot(MarketSnapshot(symbol="ETH/USDT", market_type="usdt_futures", price=entry))

    result = submit_proposal(
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=0.01,
        price=entry,
        stop_price=stop,
        take_profit=take_profit,
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
    _stub_exchange_secrets(monkeypatch)
    _patch_filled_place_order(monkeypatch, "aletheia.exchanges.okx.client", fill_price=100.0)
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.okx_broker",
        "aletheia.exchanges.okx.client",
        fill_price=100.0,
    )

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
        insert_market_snapshot(MarketSnapshot(symbol=sym, market_type="usdt_futures", price=100.0))
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
