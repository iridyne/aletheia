"""Fail-closed Risk reservations for unresolved native entry Orders.

These tests use an isolated temporary SQLite database.  They never contact an
exchange and never read or write the operator's runtime database.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_UNRESOLVED_ENTRY_STATES = (
    "submitted",
    "partial",
    "partially_filled",
    "submission_unknown",
    "materialization_missing",
)


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


def _risk_config(monkeypatch, *, demo_slots=6, live_slots=3):
    import aletheia.risk.engine as eng
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio.account_loss import AccountLossSnapshot

    cfg = AletheiaConfig()
    cfg.risk.max_open_positions_demo = demo_slots
    cfg.risk.max_open_positions_live = live_slots
    cfg.risk.max_demo_position_notional_usdt = 1_000_000.0
    cfg.risk.max_live_position_notional_usdt = 1_000_000.0
    cfg.risk.max_per_symbol_notional_usdt = 0.0
    monkeypatch.setattr(eng, "load_config", lambda: cfg)
    monkeypatch.setattr(
        eng,
        "authoritative_account_realized_loss",
        lambda _mode: AccountLossSnapshot(
            available=True,
            realized_pnl_usdt=0.0,
            loss_usdt=0.0,
            source="isolated_test",
            reason="available",
            event_count=0,
        ),
    )
    monkeypatch.setattr(
        eng,
        "_check_correlation_exposure",
        lambda *_args, **_kwargs: [],
    )
    return cfg


def _seed_entry_order(
    *,
    suffix: str,
    execution_mode: str,
    symbol: str = "BTC/USDT",
    quantity: float = 10.0,
    price: float = 10.0,
    status: str = "materialization_missing",
    fill_quantity: float | None = None,
    position_quantity: float | None = None,
    leverage: int | None = None,
):
    from tests.execution_authority import seed_order_authority
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position

    proposal, decision = seed_order_authority(
        proposal_id=f"proposal-uee-{suffix}",
        risk_decision_id=f"risk-uee-{suffix}",
        symbol=symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
        leverage=leverage,
    )
    order = Order(
        id=f"order-uee-{suffix}",
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id if execution_mode != "paper_internal" else None,
        symbol=symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
        exchange_order_id=f"exchange-uee-{suffix}",
        status=status,
    )
    repo.insert_order(order)
    if fill_quantity is not None:
        repo.insert_fill(Fill(
            id=f"fill-uee-{suffix}",
            order_id=order.id,
            fill_price=price,
            fill_quantity=fill_quantity,
            fee=0.0,
            fee_source="reconciled",
        ))
    position = None
    if position_quantity is not None:
        position = Position(
            id=f"position-uee-{suffix}",
            symbol=symbol,
            market_type="spot",
            direction="long",
            quantity=position_quantity,
            entry_price=price,
            current_price=price,
            execution_mode=execution_mode,
            proposal_id=proposal.id,
            order_id=order.id,
            status="open",
        )
        repo.insert_position(position)
        repo.update_order(order.id, position_id=position.id)
    return proposal, order, position


def _seed_pre_order_reservation(
    *,
    suffix: str,
    execution_mode: str = "paper_internal",
):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    proposal = TradeProposal(
        id=f"proposal-reservation-{suffix}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=10.0,
        price=100.0,
        leverage=5,
        execution_mode=execution_mode,
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        id=f"risk-reservation-{suffix}",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    repo.acquire_entry_risk_reservation(
        proposal_id=proposal.id,
        proposal_commit_hash=proposal.commit_hash,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        execution_mode=proposal.execution_mode,
        quantity=proposal.quantity,
        price=proposal.price,
        leverage=proposal.leverage,
    )
    return proposal, decision


def _evaluate_new_proposal(
    *,
    suffix: str,
    symbol: str,
    execution_mode: str,
    quantity: float = 0.1,
    price: float = 10.0,
):
    import aletheia.risk.engine as eng
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        id=f"proposal-new-uee-{suffix}",
        symbol=symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
    )
    repo.insert_trade_proposal(proposal)
    return eng.evaluate_proposal(proposal, decision_role="preflight")


@pytest.mark.parametrize("status", _UNRESOLVED_ENTRY_STATES)
@spec_claim(
    SpecClaim(
        "PL-UEE-01",
        "pipeline_unresolved_entry_exposure",
        "§1 unresolved entry position slots",
        "L2b",
        "Every unresolved native entry Order without an open linked Position occupies "
        "one conservative position slot, including submission_unknown.",
        status="implemented",
    )
)
def test_unresolved_entry_without_position_occupies_demo_slot(
    tmp_path, monkeypatch, status
):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=1)
    _seed_entry_order(
        suffix=status,
        execution_mode="okx_demo",
        status=status,
    )

    result = _evaluate_new_proposal(
        suffix=status,
        symbol="SOL/USDT",
        execution_mode="paper_internal",
    )

    assert "max_demo_positions_reached:1" in result.violations


@spec_claim(
    SpecClaim(
        "PL-UEE-02",
        "pipeline_unresolved_entry_exposure",
        "§2 unresolved entry symbol reservation",
        "L2b",
        "An unresolved native entry reserves its symbol in the matching live/demo "
        "pool even before a Position can be materialized.",
        status="implemented",
    )
)
def test_unresolved_entry_reserves_same_symbol_pool(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch)
    _seed_entry_order(
        suffix="same-symbol",
        execution_mode="binance_testnet",
        symbol="ETH/USDT",
        status="materialization_missing",
    )

    result = _evaluate_new_proposal(
        suffix="same-symbol",
        symbol="ETH/USDT",
        execution_mode="okx_demo",
    )

    assert "symbol_already_open:ETH/USDT" in result.violations


@spec_claim(
    SpecClaim(
        "PL-UEE-03",
        "pipeline_unresolved_entry_exposure",
        "§3 remaining live exposure",
        "L2b",
        "A partially materialized live entry counts the linked Position once plus only "
        "the Order quantity not yet represented by durable private Fills.",
        status="implemented",
    )
)
def test_partial_entry_position_and_remaining_order_are_not_double_counted(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch)
    _seed_entry_order(
        suffix="partial-live",
        execution_mode="bitget_live_limited",
        quantity=10.0,
        price=10.0,
        status="partially_filled",
        fill_quantity=4.0,
        position_quantity=4.0,
    )

    import aletheia.risk.engine as eng

    # Position exposure = 4*10; unresolved remainder = (10-4)*10.
    assert eng._current_live_exposure() == pytest.approx(100.0)


@spec_claim(
    SpecClaim(
        "PL-UEE-04",
        "pipeline_unresolved_entry_exposure",
        "§4 missing local Position",
        "L2b",
        "When durable Fills exist but their Position is missing, Risk reserves the full "
        "authoritative Order quantity so the filled part cannot disappear from exposure.",
        status="implemented",
    )
)
def test_persisted_fill_without_position_reserves_full_live_order(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch)
    _seed_entry_order(
        suffix="fill-no-position",
        execution_mode="binance_live_limited",
        quantity=10.0,
        price=10.0,
        status="materialization_missing",
        fill_quantity=4.0,
        position_quantity=None,
    )

    import aletheia.risk.engine as eng

    assert eng._current_live_exposure() == pytest.approx(100.0)


@spec_claim(
    SpecClaim(
        "PL-UEE-07",
        "pipeline_unresolved_entry_exposure",
        "§3 live exposure gate integration",
        "L2b",
        "The existing max-total-live-exposure formula consumes unresolved entry "
        "reservations without changing its configured threshold.",
        status="implemented",
    )
)
def test_unresolved_entry_is_consumed_by_existing_live_exposure_gate(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    cfg = _risk_config(monkeypatch)
    cfg.risk.max_live_limited_cap_usdt = 1_000.0
    cfg.risk.max_total_live_exposure_pct = 10.0  # unchanged formula -> 100 USDT
    _seed_entry_order(
        suffix="live-cap",
        execution_mode="bitget_live_limited",
        symbol="BTC/USDT",
        quantity=10.0,
        price=10.0,
        status="submission_unknown",
    )

    result = _evaluate_new_proposal(
        suffix="live-cap",
        symbol="SOL/USDT",
        execution_mode="bitget_live_limited",
        quantity=0.1,
        price=10.0,
    )

    assert "max_total_live_exposure_exceeded:101.0>100.0" in result.violations


@pytest.mark.parametrize(
    "reservation_kind",
    (
        pytest.param("slot", id="position-slot"),
        pytest.param("symbol", id="symbol-pool"),
        pytest.param("notional", id="live-notional"),
    ),
)
@spec_claim(
    SpecClaim(
        "PL-UEE-09",
        "pipeline_unresolved_entry_exposure",
        "§1 pending native entry reservation",
        "L2b",
        "A native exchange entry Order persists as pending before submission and must "
        "already reserve its Risk position slot, symbol pool, and notional exposure.",
        status="implemented",
    )
)
def test_pending_native_exchange_entry_reserves_risk_capacity(
    tmp_path, monkeypatch, reservation_kind
):
    _setup_db(tmp_path, monkeypatch)
    cfg = _risk_config(monkeypatch, demo_slots=1)

    if reservation_kind == "slot":
        _seed_entry_order(
            suffix="pending-slot",
            execution_mode="okx_demo",
            status="pending",
        )
        result = _evaluate_new_proposal(
            suffix="pending-slot",
            symbol="SOL/USDT",
            execution_mode="paper_internal",
        )
        assert "max_demo_positions_reached:1" in result.violations
    elif reservation_kind == "symbol":
        _seed_entry_order(
            suffix="pending-symbol",
            execution_mode="binance_testnet",
            symbol="ETH/USDT",
            status="pending",
        )
        result = _evaluate_new_proposal(
            suffix="pending-symbol",
            symbol="ETH/USDT",
            execution_mode="okx_demo",
        )
        assert "symbol_already_open:ETH/USDT" in result.violations
    else:
        cfg.risk.max_live_limited_cap_usdt = 1_000.0
        cfg.risk.max_total_live_exposure_pct = 10.0
        _seed_entry_order(
            suffix="pending-notional",
            execution_mode="bitget_live_limited",
            quantity=10.0,
            price=10.0,
            status="pending",
        )
        result = _evaluate_new_proposal(
            suffix="pending-notional",
            symbol="SOL/USDT",
            execution_mode="bitget_live_limited",
            quantity=0.1,
            price=10.0,
        )
        assert "max_total_live_exposure_exceeded:101.0>100.0" in result.violations


@spec_claim(
    SpecClaim(
        "PL-UEE-08",
        "pipeline_unresolved_entry_exposure",
        "§1 linked Position slot deduplication",
        "L2b",
        "An unresolved entry already linked to an open Position does not consume a "
        "second position slot; only its remaining notional is separately reserved.",
        status="implemented",
    )
)
def test_linked_partial_entry_does_not_consume_second_slot(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, live_slots=2)
    _seed_entry_order(
        suffix="linked-slot",
        execution_mode="okx_live_limited",
        symbol="BTC/USDT",
        quantity=10.0,
        price=10.0,
        status="partially_filled",
        fill_quantity=4.0,
        position_quantity=4.0,
    )

    result = _evaluate_new_proposal(
        suffix="linked-slot",
        symbol="SOL/USDT",
        execution_mode="okx_live_limited",
    )

    assert not any(
        violation.startswith("max_live_positions_reached")
        for violation in result.violations
    )


@pytest.mark.parametrize(
    "column,value,expected_violation",
    (
        ("price", None, "unresolved_entry_invalid_price"),
        ("quantity", 0.0, "unresolved_entry_invalid_quantity"),
    ),
)
@spec_claim(
    SpecClaim(
        "PL-UEE-05",
        "pipeline_unresolved_entry_exposure",
        "§5 malformed unresolved authority",
        "L2b",
        "Missing price or an invalid authoritative quantity on an unresolved entry "
        "produces a stable fail-closed violation instead of zero exposure.",
        status="implemented",
    )
)
def test_malformed_unresolved_entry_fails_closed(
    tmp_path, monkeypatch, column, value, expected_violation
):
    db = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch)
    _, order, _ = _seed_entry_order(
        suffix=f"invalid-{column}",
        execution_mode="okx_demo",
    )
    db.execute(f"UPDATE orders SET {column}=? WHERE id=?", (value, order.id))

    result = _evaluate_new_proposal(
        suffix=f"invalid-{column}",
        symbol="SOL/USDT",
        execution_mode="paper_internal",
    )

    assert expected_violation in result.violations


@pytest.mark.parametrize(
    "tamper",
    (
        "proposal_leverage_without_hash",
        "order_quantity",
        "order_price",
        "cross_proposal_risk_decision",
        "execution_mode_pool_escape",
    ),
)
@spec_claim(
    SpecClaim(
        "PL-UEE-10",
        "pipeline_unresolved_entry_exposure",
        "§7 unresolved entry immutable authority",
        "L2c",
        "An unresolved native entry is usable exposure authority only while its stored "
        "Proposal hash, approved authoritative RiskDecision, and exact Order snapshot "
        "still agree; tampering fails closed instead of lowering exposure.",
        status="implemented",
    )
)
def test_tampered_unresolved_entry_authority_fails_closed(
    tmp_path,
    monkeypatch,
    tamper,
):
    db = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=10)
    original_mode = (
        "okx_live_limited"
        if tamper == "execution_mode_pool_escape"
        else "okx_demo"
    )
    proposal, order, _ = _seed_entry_order(
        suffix=f"authority-{tamper}",
        execution_mode=original_mode,
        quantity=10.0,
        price=100.0,
        leverage=5,
    )

    if tamper == "proposal_leverage_without_hash":
        db.execute(
            "UPDATE trade_proposals SET leverage=1 WHERE id=?",
            (proposal.id,),
        )
    elif tamper == "order_quantity":
        db.execute("UPDATE orders SET quantity=1 WHERE id=?", (order.id,))
    elif tamper == "order_price":
        db.execute("UPDATE orders SET price=1 WHERE id=?", (order.id,))
    elif tamper == "cross_proposal_risk_decision":
        from tests.execution_authority import seed_order_authority

        _, unrelated = seed_order_authority(
            proposal_id=f"proposal-unrelated-{tamper}",
            risk_decision_id=f"risk-unrelated-{tamper}",
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=proposal.quantity,
            price=proposal.price,
            leverage=proposal.leverage,
            execution_mode=proposal.execution_mode,
        )
        db.execute(
            "UPDATE orders SET risk_decision_id=? WHERE id=?",
            (unrelated.id, order.id),
        )
    else:
        db.execute(
            "UPDATE orders SET execution_mode='paper_internal' WHERE id=?",
            (order.id,),
        )

    result = _evaluate_new_proposal(
        suffix=f"after-authority-{tamper}",
        symbol="SOL/USDT",
        execution_mode=(
            "bitget_live_limited"
            if tamper == "execution_mode_pool_escape"
            else "paper_internal"
        ),
    )

    assert "unresolved_entry_authority_invalid" in result.violations


@pytest.mark.parametrize(
    "tamper",
    (
        "quantity",
        "leverage",
        "proposal_commit_hash",
        "execution_mode",
        "risk_decision_approval",
    ),
)
@spec_claim(
    SpecClaim(
        "PL-UEE-11",
        "pipeline_unresolved_entry_exposure",
        "§8 pre-Order immutable authority",
        "L2c",
        "A pre-Order reservation must exactly match its hashed native Proposal and one "
        "approved authoritative RiskDecision, including evaluated quantity, price, "
        "leverage, market, and execution mode.",
        status="implemented",
    )
)
def test_tampered_pre_order_reservation_authority_fails_closed(
    tmp_path,
    monkeypatch,
    tamper,
):
    db = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=10)
    proposal, decision = _seed_pre_order_reservation(
        suffix=tamper,
        execution_mode=(
            "bitget_live_limited"
            if tamper == "execution_mode"
            else "paper_internal"
        ),
    )

    if tamper == "quantity":
        db.execute(
            "UPDATE entry_risk_reservations SET quantity=1 WHERE proposal_id=?",
            (proposal.id,),
        )
    elif tamper == "leverage":
        db.execute(
            "UPDATE entry_risk_reservations SET leverage=1 WHERE proposal_id=?",
            (proposal.id,),
        )
    elif tamper == "proposal_commit_hash":
        db.execute(
            "UPDATE entry_risk_reservations SET proposal_commit_hash='tampered' "
            "WHERE proposal_id=?",
            (proposal.id,),
        )
    elif tamper == "execution_mode":
        db.execute(
            "UPDATE entry_risk_reservations SET execution_mode='paper_internal' "
            "WHERE proposal_id=?",
            (proposal.id,),
        )
    else:
        db.execute(
            "UPDATE risk_decisions SET approved=0 WHERE id=?",
            (decision.id,),
        )

    result = _evaluate_new_proposal(
        suffix=f"after-reservation-{tamper}",
        symbol="SOL/USDT",
        execution_mode=(
            "okx_live_limited"
            if tamper == "execution_mode"
            else "paper_internal"
        ),
    )

    assert "entry_risk_reservation_authority_invalid" in result.violations


@spec_claim(
    SpecClaim(
        "PL-UEE-12",
        "pipeline_unresolved_entry_exposure",
        "§9 exact Position representation",
        "L2c",
        "An unresolved Order may reserve only its unfilled remainder when one exact "
        "open Position matches both backlinks and its quantity represents all durable "
        "Fills; a mismatched or undersized Position keeps the full Order reserved.",
        status="implemented",
    )
)
def test_undersized_linked_position_keeps_full_order_reserved(
    tmp_path,
    monkeypatch,
):
    db = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=10)
    _, order, position = _seed_entry_order(
        suffix="undersized-position-authority",
        execution_mode="okx_demo",
        quantity=10.0,
        price=100.0,
        fill_quantity=4.0,
        position_quantity=4.0,
    )
    assert position is not None
    db.execute("UPDATE positions SET quantity=1 WHERE id=?", (position.id,))

    import aletheia.risk.engine as engine

    reservations = engine._unresolved_entry_reservations()
    assert len(reservations) == 1
    assert reservations[0].order_id == order.id
    assert reservations[0].has_open_position is False
    assert reservations[0].reserved_quantity == pytest.approx(10.0)
    assert (
        "unresolved_entry_position_authority_invalid"
        in reservations[0].invalid_reasons
    )


@spec_claim(
    SpecClaim(
        "PL-UEE-14",
        "pipeline_unresolved_entry_exposure",
        "§9 averaging-leg Position representation",
        "L2c",
        "A partially filled averaging leg may point to a Position whose original "
        "Proposal and Order remain the first leg; the Position is valid when its "
        "quantity represents the durable Fills from every linked entry leg.",
        status="implemented",
    )
)
def test_partial_averaging_leg_uses_all_position_entry_fills(
    tmp_path,
    monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=10)
    _, _, position = _seed_entry_order(
        suffix="averaging-primary",
        execution_mode="okx_demo",
        quantity=4.0,
        price=100.0,
        status="filled",
        fill_quantity=4.0,
        position_quantity=4.0,
    )
    assert position is not None

    from tests.execution_authority import seed_order_authority
    from aletheia.risk import engine
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order

    proposal, decision = seed_order_authority(
        proposal_id="proposal-averaging-later",
        risk_decision_id="risk-averaging-later",
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        order_type="market",
        quantity=4.0,
        price=100.0,
        execution_mode=position.execution_mode,
    )
    later_order = Order(
        id="order-averaging-later",
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        position_id=position.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        status="partially_filled",
    )
    repo.insert_order(later_order)
    repo.insert_fill(Fill(
        id="fill-averaging-later",
        order_id=later_order.id,
        fill_price=proposal.price,
        fill_quantity=2.0,
        fee=0.0,
        fee_source="reconciled",
    ))
    repo.update_position(position.id, quantity=6.0)

    reservations = engine._unresolved_entry_reservations()
    assert len(reservations) == 1
    assert reservations[0].order_id == later_order.id
    assert reservations[0].has_open_position is True
    assert reservations[0].persisted_fill_quantity == pytest.approx(2.0)
    assert reservations[0].reserved_quantity == pytest.approx(2.0)
    assert reservations[0].invalid_reasons == ()


@spec_claim(
    SpecClaim(
        "PL-UEE-15",
        "pipeline_unresolved_entry_exposure",
        "§7 local paper price authority floor",
        "L2c",
        "A local paper Order may honestly store a slipped fill price, but unresolved "
        "exposure uses at least the hashed Proposal price so changing the Order price "
        "cannot lower its Risk reservation.",
        status="implemented",
    )
)
def test_paper_order_price_cannot_lower_unresolved_exposure(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=10)
    _, order, _ = _seed_entry_order(
        suffix="paper-price-floor",
        execution_mode="paper_internal",
        quantity=10.0,
        price=100.0,
    )
    db.execute("UPDATE orders SET price=1 WHERE id=?", (order.id,))

    import aletheia.risk.engine as engine

    reservations = engine._unresolved_entry_reservations()
    assert len(reservations) == 1
    assert reservations[0].price == pytest.approx(100.0)
    assert reservations[0].reserved_quantity == pytest.approx(10.0)
    assert reservations[0].invalid_reasons == ()


@spec_claim(
    SpecClaim(
        "PL-UEE-13",
        "pipeline_unresolved_entry_exposure",
        "§10 normalized quantity authority",
        "L2c",
        "A valid authoritative decision may normalize quantity away from the stored "
        "Proposal, and both its pre-Order and unresolved-Order stages count that exact "
        "evaluated quantity without inventing or dropping exposure.",
        status="implemented",
    )
)
def test_normalized_quantity_authority_counts_exact_exposure(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=10)

    from aletheia.risk import engine
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, RiskDecision, TradeProposal

    proposal = TradeProposal(
        id="normalized-authority-proposal",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=10.0,
        price=100.0,
        leverage=5,
        execution_mode="bitget_live_limited",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        id="normalized-authority-decision",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=4.0,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    repo.acquire_entry_risk_reservation(
        proposal_id=proposal.id,
        proposal_commit_hash=proposal.commit_hash,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        execution_mode=proposal.execution_mode,
        quantity=4.0,
        price=proposal.price,
        leverage=proposal.leverage,
    )

    pre_order = engine._pre_order_entry_reservations()
    assert len(pre_order) == 1
    assert pre_order[0].reserved_quantity == pytest.approx(4.0)
    assert pre_order[0].invalid_reasons == ()
    assert engine._current_live_exposure() == pytest.approx(400.0)

    repo.insert_order(Order(
        id="normalized-authority-order",
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        client_order_id=proposal.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=4.0,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        status="pending",
    ))

    unresolved = engine._unresolved_entry_reservations()
    assert len(unresolved) == 1
    assert unresolved[0].reserved_quantity == pytest.approx(4.0)
    assert unresolved[0].leverage == pytest.approx(5.0)
    assert unresolved[0].invalid_reasons == ()
    assert engine._current_live_exposure() == pytest.approx(400.0)


@spec_claim(
    SpecClaim(
        "PL-UEE-06",
        "pipeline_unresolved_entry_exposure",
        "§6 close Order exclusion",
        "L2b",
        "A pending native close Order never consumes a new entry slot or adds exposure; "
        "the still-open Position already represents the risk.",
        status="implemented",
    )
)
def test_unresolved_close_order_does_not_add_entry_exposure(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch)

    from tests.execution_authority import seed_order_authority
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Position

    proposal, _ = seed_order_authority(
        proposal_id="proposal-uee-close",
        risk_decision_id="risk-uee-close",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=5.0,
        price=10.0,
        execution_mode="bitget_live_limited",
    )
    position = Position(
        id="position-uee-close",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=5.0,
        entry_price=10.0,
        current_price=10.0,
        execution_mode="bitget_live_limited",
        proposal_id=proposal.id,
        status="open",
    )
    repo.insert_position(position)
    close_order = Order(
        id="order-uee-close",
        proposal_id=proposal.id,
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
        exchange_order_id="exchange-uee-close",
        status="materialization_missing",
    )
    repo.insert_order(close_order)
    repo.update_position(
        position.id,
        pending_close_order_id=close_order.id,
        pending_close_status="materialization_missing",
    )

    import aletheia.risk.engine as eng

    assert eng._current_live_exposure() == pytest.approx(50.0)
