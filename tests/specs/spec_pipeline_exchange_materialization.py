"""Exchange-fill materialization and Order authority contracts.

These tests use only local SQLite state and mocked Bitget private history to
prove that an exchange-confirmed fill becomes one durable, replay-safe chain.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import sqlite3
import threading

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


def _patch_bitget_history(monkeypatch, fill_rows, *, open_ids=()):
    from aletheia.exchanges.bitget import client as bitget_client

    async def _open_orders(self, market_type="spot"):
        return [{"orderId": order_id} for order_id in open_ids]

    async def _fills(self, symbol=None, market_type="spot"):
        return list(fill_rows)

    async def _order_fills(
        self, *, order_id, symbol, market_type="usdt_futures"
    ):
        return [
            row for row in fill_rows
            if str(row.get("orderId") or "") == str(order_id)
        ]

    monkeypatch.setattr(
        bitget_client.BitgetClient,
        "get_open_orders",
        _open_orders,
    )
    monkeypatch.setattr(bitget_client.BitgetClient, "get_fills", _fills)
    monkeypatch.setattr(
        bitget_client.BitgetClient,
        "get_order_fills",
        _order_fills,
    )


def _private_fill(
    exchange_order_id: str | None,
    trade_id: str,
    *,
    price: float | None,
    quantity: float | None,
    fee: float | None,
    filled_at_ms: int,
    fee_currency: str = "USDT",
) -> dict:
    row = {
        "orderId": exchange_order_id,
        "tradeId": trade_id,
        "cTime": str(filled_at_ms),
        "feeCoin": fee_currency,
    }
    if price is not None:
        row["fillPrice"] = str(price)
    if quantity is not None:
        row["fillQuantity"] = str(quantity)
    if fee is not None:
        row["fee"] = str(fee)
    return row


def _seed_native_chain(
    *,
    exchange_order_id: str,
    quantity: float = 0.75,
    requested_price: float = 100.0,
    order_status: str = "materialization_missing",
    proposal_status: str = "materialization_missing",
    market_type: str = "spot",
    leverage: int | None = None,
    stop_price: float | None = None,
    take_profit: float | None = None,
    expected_horizon_hours: float | None = None,
    stop_source: str | None = None,
    trade_school: str = "short_term",
):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        Opportunity, Order, RiskDecision, SerenityOpportunity, SerenityTheme, TradeProposal,
    )

    if trade_school == "serenity":
        theme = SerenityTheme(name="spec-test-theme", status="active")
        repo.insert_serenity_theme(theme)
        opportunity = SerenityOpportunity(
            theme_id=theme.id,
            symbol="BTC/USDT",
            direction="long",
            thesis="mocked serenity private-fill materialization",
        )
        repo.insert_serenity_opportunity(opportunity)
    else:
        opportunity = Opportunity(
            symbol="BTC/USDT",
            market=market_type,
            direction_bias="long",
            thesis="mocked private-fill materialization",
            source_agent="spec",
        )
        repo.insert_opportunity(opportunity)
    proposal = TradeProposal(
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type=market_type,
        direction="long",
        order_type="limit",
        quantity=quantity,
        price=requested_price,
        execution_mode="bitget_live_limited",
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        expected_horizon_hours=expected_horizon_hours,
        stop_source=stop_source,
        trade_school=trade_school,
        status=proposal_status,
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        risk_score=0.0,
        allowed_execution_mode="bitget_live_limited",
        proposal_execution_mode="bitget_live_limited",
        decision_role="authoritative",
        evaluated_quantity=quantity,
        proposal_commit_hash=proposal.commit_hash,
        message="Approved",
    )
    repo.insert_risk_decision(decision)
    order = Order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=quantity,
        price=requested_price,
        execution_mode=proposal.execution_mode,
        exchange_order_id=exchange_order_id,
        client_order_id=proposal.id,
        status=order_status,
    )
    repo.insert_order(order)
    return opportunity, proposal, decision, order


@spec_claim(
    SpecClaim(
        "PL-XM-09",
        "pipeline_exchange_materialization",
        "§9 delayed futures position parity",
        "L2b",
        "A futures Position created from delayed private-fill evidence preserves the "
        "authoritative leverage and the same liquidation-price estimate as the immediate "
        "broker path.",
        status="implemented",
    )
)
def test_delayed_futures_fill_preserves_leverage_and_liquidation_estimate(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.position_enrichment import liquidation_price_for
    from aletheia.execution.reconciliation import reconcile

    fill_rows = [
        _private_fill(
            "EX-XM-FUTURES",
            "BG-XM-FUTURES-1",
            price=60_100.0,
            quantity=0.01,
            fee=0.3606,
            filled_at_ms=1_784_050_000_000,
        )
    ]
    _patch_bitget_history(monkeypatch, fill_rows)
    _, proposal, _, order = _seed_native_chain(
        exchange_order_id="EX-XM-FUTURES",
        quantity=0.01,
        requested_price=60_000.0,
        market_type="usdt_futures",
        leverage=5,
        stop_price=59_000.0,
        take_profit=62_000.0,
    )

    result = reconcile()
    position = dict(
        db.execute(
            "SELECT * FROM positions WHERE order_id=?", (order.id,)
        ).fetchone()
    )

    assert result["filled"] == 1
    assert position["leverage"] == 5
    assert position["liquidation_price"] == pytest.approx(
        liquidation_price_for(60_100.0, proposal.direction, 5, proposal.symbol)
    )
    assert position["stop_price"] == pytest.approx(59_000.0)
    assert position["take_profit_price"] == pytest.approx(62_000.0)


@spec_claim(
    SpecClaim(
        "PL-XM-10",
        "pipeline_exchange_materialization",
        "§10 old private-fill recovery",
        "L2b",
        "When an unresolved Order is older than Bitget's unfiltered private-fill page, "
        "reconciliation retries the read for that Order's exchange symbol and still "
        "materializes the proven Fill and Position without resubmission.",
        status="implemented",
    )
)
def test_symbol_scoped_history_recovers_fill_missing_from_default_page(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.exchanges.bitget import client as bitget_client

    private_fill = _private_fill(
        "EX-XM-AGED-OUT",
        "BG-XM-AGED-OUT-1",
        price=104.0,
        quantity=0.75,
        fee=0.0468,
        filled_at_ms=1_784_075_000_000,
    )
    requested_symbols: list[str | None] = []

    async def _open_orders(self, market_type="spot"):
        return []

    async def _fills(self, symbol=None, market_type="spot"):
        requested_symbols.append(symbol)
        return [private_fill] if symbol == "BTCUSDT" else []

    monkeypatch.setattr(bitget_client.BitgetClient, "get_open_orders", _open_orders)
    monkeypatch.setattr(bitget_client.BitgetClient, "get_fills", _fills)
    _, _, _, order = _seed_native_chain(exchange_order_id="EX-XM-AGED-OUT")

    result = reconcile()

    assert result["filled"] == 1
    assert requested_symbols == [None, "BTCUSDT"]
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 1


@spec_claim(
    SpecClaim(
        "PL-XM-11",
        "pipeline_exchange_materialization",
        "§11 fee-currency economics",
        "L2b",
        "A private fee charged in the base asset remains stored in its original currency "
        "on the Fill but is converted at the actual fill price before it contributes to "
        "the Position's quote-currency entry cost.",
        status="implemented",
    )
)
def test_base_asset_fee_is_converted_before_position_cost(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile

    fill_rows = [
        _private_fill(
            "EX-XM-BASE-FEE",
            "BG-XM-BASE-FEE-1",
            price=100.0,
            quantity=0.75,
            fee=0.001,
            fee_currency="BTC",
            filled_at_ms=1_784_080_000_000,
        )
    ]
    _patch_bitget_history(monkeypatch, fill_rows)
    _, _, _, order = _seed_native_chain(exchange_order_id="EX-XM-BASE-FEE")

    result = reconcile()
    fill = dict(
        db.execute("SELECT * FROM fills WHERE order_id=?", (order.id,)).fetchone()
    )
    position = dict(
        db.execute(
            "SELECT * FROM positions WHERE order_id=?", (order.id,)
        ).fetchone()
    )

    assert result["filled"] == 1
    assert fill["fee"] == pytest.approx(0.001)
    assert fill["fee_currency"] == "BTC"
    assert position["entry_fee"] == pytest.approx(0.1)


@spec_claim(
    SpecClaim(
        "PL-XM-01",
        "pipeline_exchange_materialization",
        "§1 confirmed fill materialization",
        "L2b",
        "A complete Bitget private fill atomically materializes exactly one real Fill and "
        "Position, then marks Order filled and Proposal executed while preserving the "
        "Proposal, authoritative RiskDecision, Opportunity, instrument, and execution mode.",
        status="implemented",
    )
)
def test_complete_private_fill_atomically_materializes_causal_chain(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    fill_rows = [
        _private_fill(
            "EX-XM-FULL",
            "BG-XM-FULL-1",
            price=101.25,
            quantity=0.75,
            fee=0.045,
            filled_at_ms=1_784_000_000_000,
        )
    ]
    _patch_bitget_history(monkeypatch, fill_rows)
    opportunity, proposal, decision, order = _seed_native_chain(
        exchange_order_id="EX-XM-FULL",
        requested_price=99.0,
    )

    result = reconcile()
    stored_order = repo.get_order(order.id)
    stored_proposal = repo.get_trade_proposal(proposal.id)
    fills = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM fills WHERE order_id=? ORDER BY rowid", (order.id,)
        ).fetchall()
    ]
    positions = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM positions WHERE order_id=? ORDER BY rowid", (order.id,)
        ).fetchall()
    ]

    assert result["filled"] == 1
    assert stored_order["status"] == "filled"
    assert stored_proposal["status"] == "executed"
    assert len(fills) == 1
    assert len(positions) == 1
    fill = fills[0]
    position = positions[0]
    assert fill["fill_price"] == pytest.approx(101.25)
    assert fill["fill_price"] != pytest.approx(order.price)
    assert fill["fill_quantity"] == pytest.approx(0.75)
    assert fill["fee"] == pytest.approx(0.045)
    assert fill["fee_source"] == "reconciled"
    assert stored_order["proposal_id"] == proposal.id
    assert stored_order["risk_decision_id"] == decision.id
    assert position["proposal_id"] == proposal.id
    assert position["order_id"] == order.id
    assert position["opportunity_id"] == opportunity.id
    assert position["symbol"] == proposal.symbol
    assert position["market_type"] == proposal.market_type
    assert position["direction"] == proposal.direction
    assert position["execution_mode"] == proposal.execution_mode
    assert position["quantity"] == pytest.approx(fill["fill_quantity"])
    assert position["entry_price"] == pytest.approx(fill["fill_price"])
    assert position["take_profit_price"] is None

    # Atomicity: if Position persistence fails after private fill evidence is
    # accepted, no partial Fill/status transition may escape the transaction.
    _, rollback_proposal, _, rollback_order = _seed_native_chain(
        exchange_order_id="EX-XM-ROLLBACK",
        quantity=0.5,
        requested_price=88.0,
    )
    # Keep this case on the new-Position branch. Compatible open entry legs are
    # intentionally averaged into one Position in production, which would turn
    # the trigger below into an UPDATE test instead of the intended INSERT
    # rollback proof.
    repo.update_position(position["id"], status="closed")
    fill_rows.append(
        _private_fill(
            "EX-XM-ROLLBACK",
            "BG-XM-ROLLBACK-1",
            price=89.5,
            quantity=0.5,
            fee=0.02685,
            filled_at_ms=1_784_000_001_000,
        )
    )
    db.execute(
        f"""
        CREATE TRIGGER fail_xm_position_insert
        BEFORE INSERT ON positions
        WHEN NEW.order_id = '{rollback_order.id}'
        BEGIN
            SELECT RAISE(ABORT, 'forced materialization rollback');
        END
        """
    )
    try:
        reconcile()
    except sqlite3.DatabaseError:
        pass

    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (rollback_order.id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (rollback_order.id,)
    ).fetchone()[0] == 0
    assert repo.get_order(rollback_order.id)["status"] == "materialization_missing"
    assert repo.get_trade_proposal(rollback_proposal.id)["status"] == (
        "materialization_missing"
    )

    # A fill status without private price/quantity/fee evidence must not fall
    # back to the request and fabricate economics.
    db.execute("DROP TRIGGER fail_xm_position_insert")
    _, missing_proposal, _, missing_order = _seed_native_chain(
        exchange_order_id="EX-XM-NO-ECONOMICS",
        quantity=0.25,
        requested_price=77.0,
    )
    fill_rows.append(
        _private_fill(
            "EX-XM-NO-ECONOMICS",
            "BG-XM-NO-ECONOMICS-1",
            price=None,
            quantity=None,
            fee=None,
            filled_at_ms=1_784_000_002_000,
        )
    )
    reconcile()
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (missing_order.id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (missing_order.id,)
    ).fetchone()[0] == 0
    assert repo.get_order(missing_order.id)["status"] == "materialization_missing"
    assert repo.get_trade_proposal(missing_proposal.id)["status"] == (
        "materialization_missing"
    )


@spec_claim(
    SpecClaim(
        "PL-XM-02",
        "pipeline_exchange_materialization",
        "§2 partial-fill convergence",
        "L2b",
        "A partially materialized Order consumes only the missing private-fill delta; "
        "local Fill and Position quantities exactly converge to exchange history and a "
        "repeated reconciliation is idempotent.",
        status="implemented",
    )
)
def test_partial_local_chain_adds_only_exchange_delta_and_replay_is_idempotent(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Position

    fill_rows = [
        _private_fill(
            "EX-XM-PARTIAL",
            "BG-XM-PARTIAL-1",
            price=100.0,
            quantity=0.4,
            fee=0.04,
            filled_at_ms=1_784_100_000_000,
        ),
        _private_fill(
            "EX-XM-PARTIAL",
            "BG-XM-PARTIAL-2",
            price=110.0,
            quantity=0.6,
            fee=0.066,
            filled_at_ms=1_784_100_001_000,
        ),
    ]
    _patch_bitget_history(monkeypatch, fill_rows)
    opportunity, proposal, _, order = _seed_native_chain(
        exchange_order_id="EX-XM-PARTIAL",
        quantity=1.0,
        requested_price=99.0,
        order_status="partially_filled",
        proposal_status="partially_filled",
    )
    repo.insert_fill(
        Fill(
            id="BG-XM-PARTIAL-1",
            order_id=order.id,
            fill_price=100.0,
            fill_quantity=0.4,
            fee=0.04,
            fee_source="reconciled",
            filled_at=datetime.fromtimestamp(
                1_784_100_000_000 / 1000,
                tz=timezone.utc,
            ).isoformat(),
        )
    )
    position = Position(
        proposal_id=proposal.id,
        order_id=order.id,
        opportunity_id=opportunity.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        quantity=0.4,
        entry_price=100.0,
        current_price=100.0,
        entry_fee=0.04,
        execution_mode=proposal.execution_mode,
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        opened_at=datetime.fromtimestamp(
            1_784_100_000_000 / 1000,
            tz=timezone.utc,
        ).isoformat(),
    )
    repo.insert_position(position)

    first = reconcile()
    first_fills = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM fills WHERE order_id=? ORDER BY rowid", (order.id,)
        ).fetchall()
    ]
    first_position = dict(
        db.execute("SELECT * FROM positions WHERE id=?", (position.id,)).fetchone()
    )

    assert first["filled"] == 1
    assert len(first_fills) == 2
    assert sum(row["fill_quantity"] for row in first_fills) == pytest.approx(1.0)
    delta = [row for row in first_fills if row["fill_quantity"] == pytest.approx(0.6)]
    assert len(delta) == 1
    assert delta[0]["fill_price"] == pytest.approx(110.0)
    assert delta[0]["fee"] == pytest.approx(0.066)
    assert delta[0]["fee_source"] == "reconciled"
    assert first_position["quantity"] == pytest.approx(1.0)
    assert first_position["entry_price"] == pytest.approx(106.0)
    assert first_position["entry_fee"] == pytest.approx(0.106)
    assert first_position["entry_fill_verified"] == 1
    assert first_position["entry_price_source"] == "broker_fill"
    assert first_position["opened_at"] == datetime.fromtimestamp(
        1_784_100_000_000 / 1000,
        tz=timezone.utc,
    ).isoformat()
    assert repo.get_order(order.id)["status"] == "filled"
    assert repo.get_trade_proposal(proposal.id)["status"] == "executed"

    second = reconcile()
    second_fills = db.execute(
        "SELECT * FROM fills WHERE order_id=? ORDER BY rowid", (order.id,)
    ).fetchall()
    second_position = dict(
        db.execute("SELECT * FROM positions WHERE id=?", (position.id,)).fetchone()
    )
    assert second["filled"] == 0
    assert len(second_fills) == 2
    assert sum(row["fill_quantity"] for row in second_fills) == pytest.approx(1.0)
    assert second_position["quantity"] == pytest.approx(1.0)
    assert second_position["entry_price"] == pytest.approx(106.0)
    assert second_position["entry_fee"] == pytest.approx(0.106)


@spec_claim(
    SpecClaim(
        "PL-XM-12",
        "pipeline_exchange_materialization",
        "§2 private-fill identity is indivisible",
        "L2b",
        "A remote private Fill is never split into a synthetic delta identity. "
        "If whole unseen Fill rows cannot exactly explain the missing quantity, "
        "materialization fails closed instead of creating a replay-unstable Fill.",
        status="implemented",
    )
)
def test_private_fill_delta_rejects_splitting_one_remote_fill():
    from aletheia.execution.exchange_materialization import (
        MaterializationIntegrityError,
        PrivateFill,
        _tail_delta,
    )

    remote_fill = PrivateFill(
        fill_id="remote-fill-indivisible",
        exchange_order_id="remote-order-indivisible",
        client_order_id="client-order-indivisible",
        price=100.0,
        quantity=1.0,
        fee=0.1,
        fee_currency="USDT",
        filled_at="2026-07-13T00:00:00+00:00",
        filled_at_ms=1_784_000_000_000,
    )

    with pytest.raises(
        MaterializationIntegrityError,
        match="whole private fills cannot explain",
    ):
        _tail_delta([remote_fill], 0.4)


@spec_claim(
    SpecClaim(
        "PL-XM-03",
        "pipeline_exchange_materialization",
        "§3 unbounded unresolved working set",
        "L2b",
        "Reconciliation scans an old unresolved core Order even when more than 500 newer "
        "terminal Orders exist, and matching private evidence reaches the same durable "
        "materialization path.",
        status="implemented",
    )
)
def test_old_unresolved_order_is_materialized_beyond_500_newer_orders(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    fill_rows = [
        _private_fill(
            "EX-XM-OLD",
            "BG-XM-OLD-1",
            price=102.0,
            quantity=0.75,
            fee=0.0459,
            filled_at_ms=1_784_200_000_000,
        )
    ]
    _patch_bitget_history(monkeypatch, fill_rows)
    _, proposal, _, old_order = _seed_native_chain(exchange_order_id="EX-XM-OLD")

    db.executemany(
        """
        INSERT INTO orders (
            id, symbol, market_type, direction, order_type, quantity, price,
            execution_mode, status, source, created_at
        ) VALUES (?, 'ETH/USDT', 'spot', 'long', 'limit', 0.01, 3000,
                  'paper_internal', 'filled', 'adopted', ?)
        """,
        [
            (f"newer-terminal-{index}", f"2099-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00")
            for index in range(501)
        ],
    )

    result = reconcile()

    assert result["checked"] == 1
    assert result["filled"] == 1
    assert repo.get_order(old_order.id)["status"] == "filled"
    assert repo.get_trade_proposal(proposal.id)["status"] == "executed"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (old_order.id,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (old_order.id,)
    ).fetchone()[0] == 1


@spec_claim(
    SpecClaim(
        "PL-XM-04",
        "pipeline_exchange_materialization",
        "§4 linear Proposal hash chain",
        "L2c",
        "Two concurrent Proposal inserts cannot both become children of the same hash "
        "parent, including when callers supply self-consistent hashes computed from a "
        "stale parent tip.",
        status="implemented",
    )
)
def test_two_thread_prehashed_proposal_insert_cannot_fork_parent(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal
    from aletheia.storage.sqlite import close_db

    monkeypatch.setattr(runtime_session, "current_provenance", lambda: {})
    root = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.1,
        price=100.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(root)

    candidates = [
        TradeProposal(
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            quantity=0.1 + index,
            price=3000.0 + index,
            execution_mode="paper_internal",
            parent_hash=root.commit_hash,
        )
        for index in range(2)
    ]
    for candidate in candidates:
        candidate.commit_hash = repo._compute_proposal_hash(candidate)

    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def _insert(candidate):
        try:
            barrier.wait(timeout=5)
            repo.insert_trade_proposal(candidate)
        except BaseException as exc:  # worker failures are asserted below
            errors.append(exc)
        finally:
            close_db()

    threads = [
        threading.Thread(target=_insert, args=(candidate,))
        for candidate in candidates
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    rows = [
        dict(row)
        for row in db.execute(
            "SELECT id, parent_hash, commit_hash FROM trade_proposals "
            "WHERE id IN (?, ?)",
            (candidates[0].id, candidates[1].id),
        ).fetchall()
    ]
    assert len(rows) + len(errors) == 2
    assert len(rows) >= 1
    parent_counts = Counter(row["parent_hash"] for row in rows)
    assert all(count == 1 for count in parent_counts.values())

    # Every accepted row must be reachable as a single linear continuation of
    # root; a safely rejected stale contender is also acceptable.
    remaining = {row["id"]: row for row in rows}
    tip = root.commit_hash
    while remaining:
        children = [row for row in remaining.values() if row["parent_hash"] == tip]
        assert len(children) == 1
        child = children[0]
        tip = child["commit_hash"]
        remaining.pop(child["id"])


@pytest.mark.parametrize(
    "invalid_field",
    [
        "rejected_authoritative",
        "approved_preflight",
        "evaluated_quantity_mismatch",
        "execution_mode_mismatch",
        "proposal_hash_mismatch",
        "missing_authoritative",
    ],
)
@spec_claim(
    SpecClaim(
        "PL-XM-05",
        "pipeline_exchange_materialization",
        "§5 native Order authority",
        "L2b",
        "A native Order insert fails closed unless its RiskDecision is approved, "
        "authoritative, and matches the Proposal commit hash, evaluated quantity, and "
        "allowed execution mode.",
        status="implemented",
    )
)
def test_native_order_insert_rejects_invalid_authoritative_snapshot(
    tmp_path, monkeypatch, invalid_field
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, RiskDecision, TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="limit",
        quantity=0.75,
        price=100.0,
        execution_mode="bitget_live_limited",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=invalid_field != "rejected_authoritative",
        risk_score=0.0,
        allowed_execution_mode=(
            "paper_internal"
            if invalid_field == "execution_mode_mismatch"
            else proposal.execution_mode
        ),
        proposal_execution_mode=proposal.execution_mode,
        decision_role=(
            "preflight" if invalid_field == "approved_preflight" else "authoritative"
        ),
        evaluated_quantity=(
            0.5 if invalid_field == "evaluated_quantity_mismatch" else proposal.quantity
        ),
        proposal_commit_hash=(
            "stale-proposal-hash"
            if invalid_field == "proposal_hash_mismatch"
            else proposal.commit_hash
        ),
        message="invalid authority fixture",
    )
    repo.insert_risk_decision(decision)
    order = Order(
        proposal_id=proposal.id,
        risk_decision_id=(
            None if invalid_field == "missing_authoritative" else decision.id
        ),
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        client_order_id=proposal.id,
        status="pending",
        source="native",
    )

    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.insert_order(order)

    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE id=?", (order.id,)
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-XM-06",
        "pipeline_exchange_materialization",
        "§6 submission identity recovery",
        "L2b",
        "A submission_unknown entry with no exchange order id uses its durable client "
        "order id to recover the venue id from private history and materialize the same "
        "authorized Fill and Position without resubmission.",
        status="implemented",
    )
)
def test_submission_unknown_recovers_exchange_id_from_client_oid(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    fill = _private_fill(
        "EX-XM-RECOVERED",
        "BG-XM-RECOVERED-1",
        price=103.0,
        quantity=0.75,
        fee=0.04635,
        filled_at_ms=1_784_300_000_000,
    )
    fill["clientOid"] = ""
    _patch_bitget_history(monkeypatch, [fill])
    _, proposal, _, order = _seed_native_chain(
        exchange_order_id=None,
        order_status="submission_unknown",
        proposal_status="submission_unknown",
    )
    fill["clientOid"] = proposal.id

    result = reconcile()

    stored_order = repo.get_order(order.id)
    assert result["filled"] == 1
    assert stored_order["exchange_order_id"] == "EX-XM-RECOVERED"
    assert stored_order["client_order_id"] == proposal.id
    assert stored_order["status"] == "filled"
    assert repo.get_trade_proposal(proposal.id)["status"] == "executed"
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM positions WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 1


def _seed_pending_close(
    *,
    exchange_order_id: str,
    quantity: float = 1.0,
    trade_school: str = "short_term",
):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position

    _, proposal, _, entry_order = _seed_native_chain(
        exchange_order_id="EX-XM-ENTRY",
        quantity=quantity,
        requested_price=100.0,
        order_status="filled",
        proposal_status="executed",
        trade_school=trade_school,
    )
    repo.insert_fill(
        Fill(
            id="BG-XM-ENTRY-1",
            order_id=entry_order.id,
            fill_price=100.0,
            fill_quantity=quantity,
            fee=0.06,
            fee_source="reconciled",
            filled_at="2026-07-13T00:00:00+00:00",
        )
    )
    position = Position(
        proposal_id=proposal.id,
        order_id=entry_order.id,
        opportunity_id=proposal.opportunity_id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        quantity=quantity,
        entry_price=100.0,
        current_price=100.0,
        entry_fee=0.06,
        execution_mode=proposal.execution_mode,
        trade_school=trade_school,
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        opened_at="2026-07-13T00:00:00+00:00",
    )
    repo.insert_position(position)
    repo.update_order(entry_order.id, position_id=position.id)
    close_order = Order(
        proposal_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type="market",
        quantity=quantity,
        price=110.0,
        execution_mode=proposal.execution_mode,
        exchange_order_id=exchange_order_id,
        client_order_id=f"close-{position.id}",
        position_id=position.id,
        close_reason="take_profit",
        status="submitted",
    )
    repo.insert_order(close_order)
    repo.update_position(
        position.id,
        pending_close_order_id=close_order.id,
        pending_close_status="submitted",
        pending_close_requested_at="2026-07-13T00:00:00+00:00",
    )
    return proposal, entry_order, position, close_order


@spec_claim(
    SpecClaim(
        "PL-XM-07",
        "pipeline_exchange_materialization",
        "§7 asynchronous close materialization",
        "L2b",
        "A complete private fill for a pending close writes actual close Fill economics, "
        "closes exactly its linked Position, clears pending-close state, and is replay-safe.",
        status="implemented",
    )
)
def test_complete_private_close_fill_closes_linked_position_once(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    fill_rows = [
        _private_fill(
            "EX-XM-CLOSE",
            "BG-XM-CLOSE-1",
            price=110.0,
            quantity=1.0,
            fee=0.2,
            filled_at_ms=1_784_400_000_000,
        )
    ]
    _patch_bitget_history(monkeypatch, fill_rows)
    _, _, position, close_order = _seed_pending_close(
        exchange_order_id="EX-XM-CLOSE",
        trade_school="serenity",
    )
    feedback: dict[str, object] = {}
    from aletheia.portfolio import pnl

    def _capture_feedback(row, realized_pnl, close_reason):
        feedback.update(
            row=dict(row),
            realized_pnl=realized_pnl,
            close_reason=close_reason,
        )

    monkeypatch.setattr(pnl, "_close_serenity_feedback", _capture_feedback)

    first = reconcile()
    stored_order = repo.get_order(close_order.id)
    stored_position = dict(
        db.execute("SELECT * FROM positions WHERE id=?", (position.id,)).fetchone()
    )
    close_fills = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM fills WHERE order_id=?", (close_order.id,)
        ).fetchall()
    ]

    assert first["filled"] == 1
    assert stored_order["status"] == "filled"
    assert stored_order["position_id"] == position.id
    assert len(close_fills) == 1
    assert close_fills[0]["fill_price"] == pytest.approx(110.0)
    assert close_fills[0]["fill_quantity"] == pytest.approx(1.0)
    assert close_fills[0]["fee"] == pytest.approx(0.2)
    assert close_fills[0]["fee_source"] == "reconciled"
    assert stored_position["status"] == "closed"
    assert stored_position["close_reason"] == "take_profit"
    assert stored_position["pending_close_order_id"] is None
    assert stored_position["pending_close_status"] is None
    assert stored_position["exit_fee"] == pytest.approx(0.2)
    assert stored_position["realized_pnl"] == pytest.approx(9.74)
    assert stored_position["close_category"] == "strategy"
    assert stored_position["exit_price_source"] == "broker_fill"
    assert stored_position["exit_fill_verified"] == 1
    assert stored_position["outcome_eligible"] == 1
    assert stored_position["outcome_exclusion_reason"] == "eligible"
    assert feedback["realized_pnl"] == pytest.approx(9.74)
    assert feedback["close_reason"] == "take_profit"
    expected_closed_at = datetime.fromtimestamp(
        1_784_400_000_000 / 1000,
        tz=timezone.utc,
    ).isoformat()
    assert stored_position["closed_at"] == expected_closed_at
    assert feedback["row"]["closed_at"] == expected_closed_at
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows

    attribution = build_trade_attribution_rows(limit=10)
    assert [row["position_id"] for row in attribution] == [position.id]

    second = reconcile()
    assert second["filled"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order.id,)
    ).fetchone()[0] == 1


@spec_claim(
    SpecClaim(
        "PL-XM-08",
        "pipeline_exchange_materialization",
        "§8 partial close honesty",
        "L2b",
        "A partial private close fill remains partially_filled, records only actual "
        "Fill quantity, and keeps the Position open with an explicit pending close.",
        status="implemented",
    )
)
def test_partial_private_close_fill_keeps_position_open(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    fill_rows = [
        _private_fill(
            "EX-XM-CLOSE-PARTIAL",
            "BG-XM-CLOSE-PARTIAL-1",
            price=108.0,
            quantity=0.4,
            fee=0.02592,
            filled_at_ms=1_784_500_000_000,
        )
    ]
    _patch_bitget_history(
        monkeypatch, fill_rows, open_ids=("EX-XM-CLOSE-PARTIAL",)
    )
    _, _, position, close_order = _seed_pending_close(
        exchange_order_id="EX-XM-CLOSE-PARTIAL"
    )

    result = reconcile()
    stored_order = repo.get_order(close_order.id)
    stored_position = dict(
        db.execute("SELECT * FROM positions WHERE id=?", (position.id,)).fetchone()
    )

    assert result["filled"] == 0
    assert stored_order["status"] == "partially_filled"
    assert stored_order["filled_at"] is None
    assert stored_position["status"] == "open"
    assert stored_position["quantity"] == pytest.approx(1.0)
    assert stored_position["pending_close_order_id"] == close_order.id
    assert stored_position["pending_close_status"] == "partially_filled"
    assert stored_position["closed_at"] is None
    close_fill = dict(
        db.execute(
            "SELECT * FROM fills WHERE order_id=?", (close_order.id,)
        ).fetchone()
    )
    assert close_fill["fill_quantity"] == pytest.approx(0.4)
    assert close_fill["fee_source"] == "reconciled"


@spec_claim(
    SpecClaim(
        "PL-XM-13",
        "pipeline_exchange_materialization",
        "§13 linked Position identity",
        "L2b",
        "A private entry Fill may update an existing Position only when the durable "
        "Order backlink names the same instrument, direction, and exchange account.",
        status="implemented",
    )
)
def test_entry_backlink_cannot_attach_private_fill_to_another_account_position(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import (
        MaterializationIntegrityError,
        materialize_bitget_entry,
    )
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    _, _, _, order = _seed_native_chain(
        exchange_order_id="EX-XM-WRONG-BACKLINK",
        quantity=0.25,
        requested_price=100.0,
    )
    wrong_position = Position(
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=2.0,
        entry_price=50.0,
        current_price=50.0,
        execution_mode="okx_demo",
    )
    repo.insert_position(wrong_position)
    repo.update_order(order.id, position_id=wrong_position.id)
    fill_rows = [
        _private_fill(
            "EX-XM-WRONG-BACKLINK",
            "BG-XM-WRONG-BACKLINK-1",
            price=101.0,
            quantity=0.25,
            fee=0.01515,
            filled_at_ms=1_784_600_000_000,
        )
    ]

    with pytest.raises(MaterializationIntegrityError, match="linked Position"):
        materialize_bitget_entry(order.id, fill_rows)

    stored_position = dict(
        db.execute(
            "SELECT * FROM positions WHERE id=?", (wrong_position.id,)
        ).fetchone()
    )
    assert stored_position["quantity"] == pytest.approx(2.0)
    assert stored_position["entry_price"] == pytest.approx(50.0)
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (order.id,)
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-XM-14",
        "pipeline_exchange_materialization",
        "§14 exit-intent stamped on new exchange position",
        "L2b",
        "When a Proposal carries expected_horizon_hours + stop_source, the Position "
        "created by private-fill materialization inherits both fields and exit_plan_version=1. "
        "Fixes #171.",
        status="implemented",
    )
)
def test_exit_intent_stamped_on_new_exchange_position(tmp_path, monkeypatch):
    """#171: exit intent (horizon, stop provenance, version) must survive the async fill path."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.reconciliation import reconcile
    from aletheia.storage import repositories as repo

    fill_rows = [
        _private_fill(
            "EX-XM-EXIT-INTENT",
            "BG-XM-EXIT-1",
            price=100.0,
            quantity=0.5,
            fee=0.03,
            filled_at_ms=1_784_000_000_000,
        )
    ]
    _patch_bitget_history(monkeypatch, fill_rows)
    _, proposal, _, order = _seed_native_chain(
        exchange_order_id="EX-XM-EXIT-INTENT",
        quantity=0.5,
        expected_horizon_hours=24.0,
        stop_source="operator",
    )
    reconcile()

    row = dict(
        repo.get_db().execute(
            "SELECT * FROM positions WHERE proposal_id=?", (proposal.id,)
        ).fetchone()
    )
    assert row["expected_horizon_hours"] == pytest.approx(24.0), (
        "expected_horizon_hours missing on exchange position"
    )
    assert row["stop_source"] == "operator", "stop_source missing on exchange position"
    assert row["exit_plan_version"] == 1, "exit_plan_version not stamped on exchange position"


@spec_claim(
    SpecClaim(
        "PL-XM-15",
        "pipeline_exchange_materialization",
        "§15 cross-school averaging rejected",
        "L2b",
        "A second-leg private fill from a different trade_school must NOT merge into an "
        "existing open Position — it creates a new Position instead. Fixes #172.",
        status="implemented",
    )
)
def test_cross_school_averaging_creates_new_position(tmp_path, monkeypatch):
    """#172: serenity leg must not silently merge into a short_term position."""
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_entry
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Position

    # Seed a pre-existing open short_term position from a first leg.
    _, first_proposal, _, first_order = _seed_native_chain(
        exchange_order_id="EX-XM-SCHOOL-FIRST",
        quantity=0.5,
        trade_school="short_term",
    )
    first_fill = Fill(
        id="BG-XM-SCHOOL-FIRST-1",
        order_id=first_order.id,
        fill_price=100.0,
        fill_quantity=0.5,
        fee=0.03,
        fee_source="reconciled",
        filled_at="2026-07-20T00:00:00+00:00",
    )
    repo.insert_fill(first_fill)
    short_term_position = Position(
        proposal_id=first_proposal.id,
        order_id=first_order.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.5,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="bitget_live_limited",
        trade_school="short_term",
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        opened_at="2026-07-20T00:00:00+00:00",
    )
    repo.insert_position(short_term_position)
    repo.update_order(first_order.id, position_id=short_term_position.id)
    repo.update_trade_proposal(first_proposal.id, status="executed")

    # Second leg — same symbol/direction/mode but serenity school.
    _, second_proposal, _, second_order = _seed_native_chain(
        exchange_order_id="EX-XM-SCHOOL-SECOND",
        quantity=0.3,
        trade_school="serenity",
        expected_horizon_hours=168.0,
    )
    fill_rows = [
        _private_fill(
            "EX-XM-SCHOOL-SECOND",
            "BG-XM-SCHOOL-SECOND-1",
            price=102.0,
            quantity=0.3,
            fee=0.018,
            filled_at_ms=1_784_100_000_000,
        )
    ]
    result = materialize_bitget_entry(second_order.id, fill_rows)
    assert result.state == "filled"

    # The serenity leg must have created a NEW position, not merged.
    positions = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM positions WHERE symbol='BTC/USDT' ORDER BY rowid"
        ).fetchall()
    ]
    assert len(positions) == 2, (
        f"expected 2 positions (one per school) but got {len(positions)}: "
        f"{[p['trade_school'] for p in positions]}"
    )
    schools = {p["trade_school"] for p in positions}
    assert schools == {"short_term", "serenity"}, f"unexpected schools: {schools}"

    # The short_term position must not have been touched.
    assert next(p for p in positions if p["trade_school"] == "short_term")["quantity"] == pytest.approx(0.5)
    # The serenity position carries the operator's horizon intent.
    serenity_pos = next(p for p in positions if p["trade_school"] == "serenity")
    assert serenity_pos["expected_horizon_hours"] == pytest.approx(168.0)
    assert serenity_pos["exit_plan_version"] == 1
