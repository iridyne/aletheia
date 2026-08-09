"""Executable regressions for the Proposal -> Risk -> Position state handoff.

Every behavioral test uses a temporary SQLite database and no exchange network.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

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


def _risk_config(monkeypatch, *, demo_slots: int = 1):
    import aletheia.execution.gateway as gateway
    import aletheia.risk.engine as engine
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio.account_loss import AccountLossSnapshot

    cfg = AletheiaConfig()
    cfg.risk.max_open_positions_demo = demo_slots
    cfg.risk.max_open_positions_live = demo_slots
    cfg.risk.max_demo_position_notional_usdt = 1_000_000.0
    cfg.risk.max_live_position_notional_usdt = 1_000_000.0
    cfg.risk.max_per_symbol_notional_usdt = 0.0
    cfg.risk.symbol_list = []
    cfg.trading.max_position_hold_hours = 0
    monkeypatch.setattr(engine, "load_config", lambda: cfg)
    monkeypatch.setattr(gateway, "load_config", lambda: cfg)
    monkeypatch.setattr(
        engine,
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
        engine,
        "_check_correlation_exposure",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(engine, "_portfolio_drawdown_scale", lambda _mode: 1.0)
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 100.0,
    )
    return cfg


@spec_claim(
    SpecClaim(
        "PL-MSI-01",
        "pipeline_middle_state_integrity",
        "§1 cross-Proposal risk reservation",
        "L2b",
        "Two distinct Proposals evaluated concurrently against one remaining slot cannot "
        "both receive authoritative approval before either Order exists.",
        status="implemented",
    )
)
def test_distinct_proposals_cannot_oversubscribe_one_risk_slot(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=1)

    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, TradeProposal

    proposals = [
        TradeProposal(
            id=f"reservation-race-{index}",
            opportunity_id=f"reservation-race-opportunity-{index}",
            strategy_id=f"reservation-strategy-{index}",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=100.0,
            stop_price=99.0,
            take_profit=102.0,
            risk_reward=2.0,
            execution_mode="paper_internal",
        )
        for index in range(2)
    ]
    for proposal in proposals:
        repo.insert_opportunity(Opportunity(
            id=proposal.opportunity_id,
            symbol=proposal.symbol,
            market=proposal.market_type,
            direction_bias=proposal.direction,
            status="queued",
        ))
        repo.insert_trade_proposal(proposal)

    simultaneous_start = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def execute(proposal_id: str) -> None:
        try:
            simultaneous_start.wait(timeout=5)
            result = gateway.execute_existing_proposal(proposal_id)
            with result_lock:
                results.append(result)
        except BaseException as exc:  # pragma: no cover - assertion reports it
            with result_lock:
                errors.append(exc)

    workers = [threading.Thread(target=execute, args=(p.id,)) for p in proposals]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not errors
    assert all(not worker.is_alive() for worker in workers)
    assert sorted(result["status"] for result in results) == ["executed", "rejected"]
    assert conn.execute(
        "SELECT COUNT(*) FROM risk_decisions "
        "WHERE decision_role='authoritative' AND approved=1"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM entry_risk_reservations").fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-MSI-09",
        "pipeline_middle_state_integrity",
        "§1 crash-safe risk reservation",
        "L2b",
        "An authoritative approval remains a durable Risk allowance before Order "
        "materialization, so a worker crash cannot reopen the same capacity.",
        status="implemented",
    )
)
def test_authoritative_reservation_blocks_capacity_before_order(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=1)

    import aletheia.risk.engine as engine
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, TradeProposal

    approved_opportunity = Opportunity(
        id="crash-reserved-opportunity",
        symbol="BTC/USDT", market="spot", direction_bias="long", status="queued",
    )
    repo.insert_opportunity(approved_opportunity)
    approved = TradeProposal(
        id="crash-reserved-proposal",
        opportunity_id=approved_opportunity.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
        status="executing",
    )
    repo.insert_trade_proposal(approved)
    result = engine.evaluate_proposal(approved, decision_role="authoritative")
    assert result.approved is True
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert repo.get_entry_risk_reservation(approved.id) is not None

    candidate_opportunity = Opportunity(
        id="after-crash-opportunity",
        symbol="ETH/USDT", market="spot", direction_bias="long", status="queued",
    )
    repo.insert_opportunity(candidate_opportunity)
    candidate = TradeProposal(
        id="after-crash-proposal",
        opportunity_id=candidate_opportunity.id,
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(candidate)
    blocked = engine.evaluate_proposal(candidate, decision_role="preflight")
    assert "max_demo_positions_reached:1" in blocked.violations


@spec_claim(
    SpecClaim(
        "PL-MSI-14",
        "pipeline_middle_state_integrity",
        "§1 complete leverage reservation",
        "L2c",
        "Account leverage includes every pre-Order reservation and unresolved Order, "
        "using the leverage and market identity committed by the Proposal.",
        status="implemented",
    )
)
def test_account_leverage_counts_pre_order_and_unresolved_entries(
    tmp_path,
    monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    cfg = _risk_config(monkeypatch, demo_slots=10)
    cfg.risk.max_account_leverage_demo = 3.0
    # This test isolates the leverage-ratio boundary. Its deliberately large
    # notional must not be rejected first by the independent total-exposure gate.
    cfg.risk.demo.capital.max_total_exposure_pct = 1_000.0
    cfg.agent.permission_mode = "Full"

    import aletheia.risk.engine as engine
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, TradeProposal

    monkeypatch.setattr(engine, "_account_equity", lambda *_args: 1_000.0)
    monkeypatch.setattr(
        engine,
        "_cached_available_balance_usdt",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        engine,
        "_market_data_age_seconds",
        lambda *_args, **_kwargs: 0.0,
    )

    first = TradeProposal(
        id="leverage-pre-order",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=20.0,
        price=100.0,
        leverage=1,
        execution_mode="okx_demo",
        status="executing",
    )
    repo.insert_trade_proposal(first)
    first_result = engine.evaluate_proposal(first, decision_role="authoritative")
    assert first_result.approved is True
    reservation = repo.get_entry_risk_reservation(first.id)
    assert reservation is not None
    assert reservation["market_type"] == "usdt_futures"
    assert reservation["leverage"] == pytest.approx(1.0)

    second = TradeProposal(
        id="leverage-after-pre-order",
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=20.0,
        price=100.0,
        leverage=1,
        execution_mode="okx_demo",
    )
    repo.insert_trade_proposal(second)
    blocked_by_reservation = engine.evaluate_proposal(
        second,
        decision_role="preflight",
    )
    assert "account_leverage_ratio:4.00x>3.0x" in blocked_by_reservation.violations

    decision = repo.get_authoritative_risk_decision(
        first.id,
        first.commit_hash,
        first.quantity,
    )
    assert decision is not None
    repo.insert_order(Order(
        id="leverage-unresolved-order",
        proposal_id=first.id,
        risk_decision_id=decision["id"],
        client_order_id=first.id,
        symbol=first.symbol,
        market_type=first.market_type,
        direction=first.direction,
        order_type=first.order_type,
        quantity=first.quantity,
        price=first.price,
        execution_mode=first.execution_mode,
        status="pending",
    ))
    assert conn.execute(
        "SELECT COUNT(*) FROM entry_risk_reservations r "
        "WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.proposal_id=r.proposal_id)"
    ).fetchone()[0] == 0

    blocked_by_order = engine.evaluate_proposal(second, decision_role="preflight")
    assert "account_leverage_ratio:4.00x>3.0x" in blocked_by_order.violations


@spec_claim(
    SpecClaim(
        "PL-MSI-15",
        "pipeline_middle_state_integrity",
        "§1 paper dispatch current Risk",
        "L2c",
        "A paper Proposal approved before a crash must pass a fresh preflight before "
        "dispatch, so a newly-active kill switch cannot be bypassed by replay.",
        status="implemented",
    )
)
def test_paper_replay_rechecks_kill_switch_before_dispatch(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    cfg = _risk_config(monkeypatch, demo_slots=2)

    from aletheia.execution import gateway
    import aletheia.risk.engine as engine
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, TradeProposal

    opportunity = Opportunity(
        id="paper-stale-authority-opportunity",
        symbol="BTC/USDT", market="spot", direction_bias="long", status="queued",
    )
    repo.insert_opportunity(opportunity)
    proposal = TradeProposal(
        id="paper-stale-authority",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    approved = engine.evaluate_proposal(proposal, decision_role="authoritative")
    assert approved.approved is True
    assert repo.get_entry_risk_reservation(proposal.id) is not None

    cfg.runtime.kill_switch_active = True
    replay = gateway.execute_existing_proposal(proposal.id)

    assert replay["status"] == "rejected"
    assert "kill_switch_active" in replay["risk_violations"]
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-MSI-10",
        "pipeline_middle_state_integrity",
        "§1 terminal reservation cleanup",
        "L2b",
        "A known terminal pre-Order rejection releases its Risk allowance, while "
        "crash/ambiguous states remain conservatively reserved.",
        status="implemented",
    )
)
def test_known_pre_order_terminal_rejection_releases_reservation(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=1)

    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, TradeProposal

    opportunity = Opportunity(
        id="terminal-reservation-release-opportunity",
        symbol="BTC/USDT", market="spot", direction_bias="long", status="queued",
    )
    repo.insert_opportunity(opportunity)
    proposal = TradeProposal(
        id="terminal-reservation-release",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    monkeypatch.setattr(
        gateway,
        "_check_symbol_availability",
        lambda *_args, **_kwargs: "deterministic_symbol_unavailable",
    )

    result = gateway.execute_existing_proposal(proposal.id)
    assert result["status"] == "rejected"
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert repo.get_entry_risk_reservation(proposal.id) is None


@spec_claim(
    SpecClaim(
        "PL-MSI-20",
        "pipeline_middle_state_integrity",
        "§1 restart-safe terminal reservation cleanup",
        "L2c",
        "A known rejected/error/executed Proposal with no Order cannot keep consuming a "
        "Risk slot after best-effort reservation cleanup fails; ambiguous or unprovable "
        "rows remain fail-closed.",
        status="implemented",
    )
)
def test_terminal_no_order_reservation_is_inactive_after_cleanup_failure(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=1)

    from aletheia.execution import gateway
    import aletheia.risk.engine as engine
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, TradeProposal

    terminal_opportunity = Opportunity(
        id="terminal-cleanup-failed-opportunity",
        symbol="BTC/USDT", market="spot", direction_bias="long", status="queued",
    )
    repo.insert_opportunity(terminal_opportunity)
    terminal = TradeProposal(
        id="terminal-reservation-cleanup-failed",
        opportunity_id=terminal_opportunity.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(terminal)
    monkeypatch.setattr(
        gateway,
        "_check_symbol_availability",
        lambda *_args, **_kwargs: "deterministic_symbol_unavailable",
    )
    monkeypatch.setattr(
        gateway,
        "release_entry_risk_reservation",
        lambda _proposal_id: (_ for _ in ()).throw(
            sqlite3.OperationalError("one-time reservation cleanup failure")
        ),
    )

    rejected = gateway.execute_existing_proposal(terminal.id)
    assert rejected["status"] == "rejected"
    assert repo.get_trade_proposal(terminal.id)["status"] == "rejected"
    assert repo.get_entry_risk_reservation(terminal.id) is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM orders WHERE proposal_id=?", (terminal.id,)
    ).fetchone()[0] == 0

    candidate_opportunity = Opportunity(
        id="after-terminal-cleanup-opportunity",
        symbol="ETH/USDT", market="spot", direction_bias="long", status="queued",
    )
    repo.insert_opportunity(candidate_opportunity)
    candidate = TradeProposal(
        id="after-terminal-cleanup-failure",
        opportunity_id=candidate_opportunity.id,
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(candidate)
    preflight = engine.evaluate_proposal(candidate, decision_role="preflight")

    assert repo.list_active_entry_risk_reservations_for_risk() == []
    assert "max_demo_positions_reached:1" not in preflight.violations
    assert preflight.approved is True


@spec_claim(
    SpecClaim(
        "PL-MSI-02",
        "pipeline_middle_state_integrity",
        "§2 complete open-position working set",
        "L2b",
        "Safety and lifecycle loops see every open Position beyond the legacy 50-row "
        "display default, and paper aggregation can find an older Position only for "
        "the same immutable Proposal lineage.",
        status="implemented",
    )
)
def test_open_position_working_set_and_paper_merge_are_unbounded(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import paper_broker
    from aletheia.portfolio import position_manager
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position, TradeProposal

    proposal = TradeProposal(
        id="target-proposal",
        symbol="MERGE/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=10.0,
        execution_mode="paper_internal",
        status="executed",
    )
    repo.insert_trade_proposal(proposal)

    target = Position(
        id="old-compatible-position",
        symbol="MERGE/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=10.0,
        execution_mode="paper_internal",
        strategy_id="target-strategy",
        proposal_id=proposal.id,
    )
    repo.insert_position(target)
    for index in range(500):
        repo.insert_position(Position(
            id=f"newer-position-{index}",
            symbol=f"COIN{index}/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=10.0,
            execution_mode="paper_internal",
            strategy_id=f"strategy-{index}",
        ))

    all_open = repo.list_all_open_positions()
    assert len(all_open) == 501
    assert target.id in {row["id"] for row in all_open}
    matched = paper_broker._find_open_position(
        target.symbol,
        target.market_type,
        target.direction,
        target.execution_mode,
        target.strategy_id,
        target.proposal_id,
    )
    assert matched is not None
    assert matched["id"] == target.id

    adjusted = position_manager.adjust_stop(target.id, 9.0)
    assert adjusted["updated"] is True
    assert repo.get_position(target.id)["stop_price"] == pytest.approx(9.0)


@spec_claim(
    SpecClaim(
        "PL-MSI-03",
        "pipeline_middle_state_integrity",
        "§2 complete market watch set",
        "L2b",
        "The market collector never drops an existing open Position merely because "
        "the dynamic observation cap has been reached.",
        status="implemented",
    )
)
def test_market_collection_keeps_every_open_position_instrument(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    import aletheia.trading.market_data as market_data

    for index in range(61):
        repo.insert_position(Position(
            id=f"watch-position-{index}",
            symbol=f"WATCH{index}/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=10.0,
            execution_mode="paper_internal",
        ))

    cfg = AletheiaConfig()
    monkeypatch.setattr(market_data, "observed_instruments_from_config", lambda _cfg: [])
    monkeypatch.setattr(repo, "get_active_universe_members", lambda limit: [])

    instruments = market_data._build_dynamic_instruments(cfg)
    symbols = {instrument.symbol for instrument in instruments}
    assert len(symbols) == 61
    assert {f"WATCH{index}/USDT" for index in range(61)} <= symbols


@spec_claim(
    SpecClaim(
        "PL-MSI-12",
        "pipeline_middle_state_integrity",
        "§2 complete instrument identity",
        "L2b",
        "Open spot and futures Positions sharing one canonical symbol each keep their "
        "own market-data instrument instead of collapsing to a symbol-only key.",
        status="implemented",
    )
)
def test_market_collection_keeps_same_symbol_spot_and_futures(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    import aletheia.trading.market_data as market_data

    for market_type in ("spot", "usdt_futures"):
        repo.insert_position(Position(
            id=f"same-symbol-{market_type}",
            symbol="BTC/USDT",
            market_type=market_type,
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
        ))

    cfg = AletheiaConfig()
    monkeypatch.setattr(market_data, "observed_instruments_from_config", lambda _cfg: [])
    monkeypatch.setattr(repo, "get_active_universe_members", lambda limit: [])

    instruments = market_data._build_dynamic_instruments(cfg)
    assert {(item.symbol, item.market_type) for item in instruments} == {
        ("BTC/USDT", "spot"),
        ("BTC/USDT", "perp"),
    }


@spec_claim(
    SpecClaim(
        "PL-MSI-04",
        "pipeline_middle_state_integrity",
        "§3 immutable native protection",
        "L2b",
        "Enabling trailing stops after approval cannot create a stop for a native "
        "Proposal whose approved stop was explicitly NULL.",
        status="implemented",
    )
)
def test_trailing_does_not_invent_native_null_stop(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position, TradeProposal
    import aletheia.portfolio.position_manager as manager

    cfg = AletheiaConfig()
    cfg.trading.trail_enabled = True
    cfg.trading.trail_pct = 1.0
    cfg.trading.breakeven_trigger_pct = 999.0
    cfg.trading.default_stop_loss_pct = 0.0
    cfg.trading.default_take_profit_pct = 0.0
    cfg.trading.max_position_hold_hours = 0
    cfg.trading.market_aware_exits.enabled = False
    monkeypatch.setattr(manager, "load_config", lambda: cfg)
    monkeypatch.setattr(
        manager,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 100.0,
    )

    proposal = TradeProposal(
        id="native-null-stop-proposal",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        stop_price=None,
        take_profit=None,
        status="executed",
    )
    repo.insert_trade_proposal(proposal)
    position = Position(
        id="native-null-stop-position",
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        quantity=1.0,
        entry_price=100.0,
        execution_mode=proposal.execution_mode,
        proposal_id=proposal.id,
        source="native",
        stop_price=None,
    )
    repo.insert_position(position)

    result = manager.evaluate_open_positions()
    stored = conn.execute(
        "SELECT stop_price,status FROM positions WHERE id=?", (position.id,)
    ).fetchone()
    assert result["checked"] == 1
    assert stored["status"] == "open"
    assert stored["stop_price"] is None


@spec_claim(
    SpecClaim(
        "PL-MSI-05",
        "pipeline_middle_state_integrity",
        "§4 exchange testnet freshness",
        "L2b",
        "Binance testnet is exchange-backed and rejects a native entry when no fresh "
        "market snapshot exists, matching Bitget and OKX demo behavior.",
        status="implemented",
    )
)
def test_binance_testnet_requires_fresh_market_data(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _risk_config(monkeypatch, demo_slots=5)

    import aletheia.risk.engine as engine
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    monkeypatch.setattr(engine, "_market_data_age_seconds", lambda *_args, **_kwargs: None)
    proposal = TradeProposal(
        id="binance-testnet-stale",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=100.0,
        execution_mode="binance_testnet",
    )
    repo.insert_trade_proposal(proposal)

    result = engine.evaluate_proposal(proposal, decision_role="preflight")
    assert "stale_market_data_demo:age=None" in result.violations


@spec_claim(
    SpecClaim(
        "PL-MSI-06",
        "pipeline_middle_state_integrity",
        "§5 producer identity helpers",
        "L2b",
        "Trade opportunity reads exclude predictions, and divergence source-version "
        "identity resolves the earliest row across every lifecycle state.",
        status="implemented",
    )
)
def test_trade_opportunity_and_divergence_identity_helpers(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    rows = [
        Opportunity(
            id="crypto-open",
            symbol="BTC/USDT",
            market="spot",
            status="open",
            asset_class="crypto",
        ),
        Opportunity(
            id="equity-open",
            symbol="AAPL/USDT",
            market="spot",
            status="open",
            asset_class="equity",
        ),
        Opportunity(
            id="prediction-open",
            symbol="EVENT/USDT",
            market="prediction",
            status="open",
            asset_class="prediction",
        ),
        Opportunity(
            id="closed-crypto",
            symbol="ETH/USDT",
            market="spot",
            status="reviewed",
            asset_class="crypto",
        ),
        Opportunity(
            id="divergence-first",
            symbol="SOL/USDT",
            market="spot",
            status="reviewed",
            source_agent="divergence_scanner",
            metadata=json.dumps({"source_version": "div-v1"}),
            created_at="2026-01-01T00:00:00+00:00",
        ),
        Opportunity(
            id="divergence-later",
            symbol="SOL/USDT",
            market="spot",
            status="open",
            source_agent="divergence_scanner",
            metadata=json.dumps({"source_version": "div-v1"}),
            created_at="2026-01-02T00:00:00+00:00",
        ),
    ]
    for row in rows:
        repo.insert_opportunity(row)

    assert {row["id"] for row in repo.list_open_trade_opportunities(limit=20)} == {
        "crypto-open",
        "equity-open",
        "divergence-later",
    }
    found = repo.find_divergence_opportunity_by_source_version("div-v1")
    assert found is not None
    assert found["id"] == "divergence-first"


@spec_claim(
    SpecClaim(
        "PL-MSI-18",
        "pipeline_middle_state_integrity",
        "§5 atomic Opportunity producer identity",
        "L2c",
        "Concurrent observer and universe producers for one symbol, snapshot, and market "
        "resolve one durable Opportunity owner instead of inserting duplicate lifecycles.",
        status="implemented",
    )
)
def test_snapshot_opportunity_identity_claim_is_atomic(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity
    from aletheia.storage.sqlite import get_db

    barrier = threading.Barrier(2)
    results: list[tuple[str, bool]] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def produce(index: int) -> None:
        try:
            candidate = Opportunity(
                id=f"snapshot-race-{index}",
                symbol="BTC/USDT",
                market="spot",
                direction_bias="long",
                universe_snapshot_id="snapshot-race-owner",
                source_agent=(
                    "market_observer" if index == 0 else "universe_scanner"
                ),
            )
            barrier.wait(timeout=5)
            owner, created = repo.insert_opportunity_once_by_snapshot(candidate)
            with result_lock:
                results.append((str(owner["id"]), created))
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_lock:
                errors.append(exc)

    threads = [threading.Thread(target=produce, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    rows = get_db().execute(
        "SELECT id FROM opportunities WHERE symbol='BTC/USDT' "
        "AND universe_snapshot_id='snapshot-race-owner' AND market='spot'"
    ).fetchall()
    assert not errors
    assert len(results) == 2
    assert sum(created for _owner, created in results) == 1
    assert len({owner for owner, _created in results}) == 1
    assert [row["id"] for row in rows] == [results[0][0]]
    owner_id = results[0][0]
    assert repo.transition_open_opportunity_to_queued(owner_id) is True
    assert repo.update_open_opportunity(
        owner_id,
        thesis="late observer output must not rewrite a queued owner",
        direction_bias="short",
    ) is False
    queued = repo.get_opportunity(owner_id)
    assert queued["status"] == "queued"
    assert queued["thesis"] != "late observer output must not rewrite a queued owner"
    assert queued["direction_bias"] == "long"
    import inspect
    from aletheia.agents import market_observer
    from aletheia.universe import signal_emitter

    assert "insert_opportunity_once_by_snapshot(" in inspect.getsource(
        market_observer
    )
    assert "update_open_opportunity(" in inspect.getsource(market_observer)
    assert "insert_opportunity_once_by_snapshot(" in inspect.getsource(
        signal_emitter
    )


@spec_claim(
    SpecClaim(
        "PL-MSI-19",
        "pipeline_middle_state_integrity",
        "§5 atomic divergence source identity",
        "L2c",
        "Concurrent divergence retries for one immutable prediction source version resolve "
        "one durable Opportunity owner across every lifecycle state.",
        status="implemented",
    )
)
def test_divergence_source_version_identity_claim_is_atomic(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity
    from aletheia.storage.sqlite import get_db

    barrier = threading.Barrier(2)
    results: list[tuple[str, bool]] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def produce(index: int) -> None:
        try:
            candidate = Opportunity(
                id=f"divergence-race-{index}",
                symbol="SOL/USDT",
                market="spot",
                direction_bias="long",
                source_agent="divergence_scanner",
                metadata=json.dumps({"source_version": "divergence-race-v1"}),
            )
            barrier.wait(timeout=5)
            owner, created = repo.insert_divergence_opportunity_once(
                candidate, source_version="divergence-race-v1"
            )
            with result_lock:
                results.append((str(owner["id"]), created))
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_lock:
                errors.append(exc)

    threads = [threading.Thread(target=produce, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    rows = get_db().execute(
        "SELECT id FROM opportunities WHERE source_agent='divergence_scanner' "
        "AND json_extract(metadata, '$.source_version')='divergence-race-v1'"
    ).fetchall()
    assert not errors
    assert len(results) == 2
    assert sum(created for _owner, created in results) == 1
    assert len({owner for owner, _created in results}) == 1
    assert [row["id"] for row in rows] == [results[0][0]]
    import inspect
    from aletheia.agents import divergence_scanner

    assert "insert_divergence_opportunity_once(" in inspect.getsource(
        divergence_scanner
    )


@spec_claim(
    SpecClaim(
        "PL-MSI-07",
        "pipeline_middle_state_integrity",
        "§6 atomic Serenity queue handoff",
        "L2b",
        "A Serenity theme and every exact pending PI carrier commit atomically, remain "
        "idempotent on retry, and reject an empty handoff.",
        status="implemented",
    )
)
def test_enqueue_serenity_theme_for_pi_is_atomic_and_idempotent(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityOpportunity, SerenityTheme

    theme = SerenityTheme(id="theme-atomic", name="Atomic handoff")
    repo.insert_serenity_theme(theme)
    opportunities = [
        SerenityOpportunity(
            id=f"serenity-atomic-{index}",
            theme_id=theme.id,
            symbol=f"SER{index}/USDT",
            direction="long",
        )
        for index in range(2)
    ]
    for opportunity in opportunities:
        repo.insert_serenity_opportunity(opportunity)

    with pytest.raises(ValueError):
        repo.enqueue_serenity_theme_for_pi(theme.id, [])

    ids = [opportunity.id for opportunity in opportunities]
    assert repo.enqueue_serenity_theme_for_pi(theme.id, ids) == ids
    assert repo.enqueue_serenity_theme_for_pi(theme.id, ids) == ids
    carriers = [
        row for row in repo.list_pi_decisions(limit=20)
        if row.get("signal_source") == "serenity"
    ]
    assert len(carriers) == 2
    assert {row["opportunity_id"] for row in carriers} == set(ids)
    assert repo.get_serenity_theme(theme.id)["status"] == "promoted"


@spec_claim(
    SpecClaim(
        "PL-MSI-08",
        "pipeline_middle_state_integrity",
        "§6 atomic Serenity rollback",
        "L2b",
        "A failure while inserting one Serenity pending carrier rolls back prior carriers, "
        "opportunity intents, and theme promotion together.",
        status="implemented",
    )
)
def test_enqueue_serenity_theme_for_pi_rolls_back_every_row(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityOpportunity, SerenityTheme

    theme = SerenityTheme(id="theme-rollback", name="Rollback handoff")
    repo.insert_serenity_theme(theme)
    ids = ["serenity-rollback-0", "serenity-rollback-1"]
    for index, opportunity_id in enumerate(ids):
        repo.insert_serenity_opportunity(SerenityOpportunity(
            id=opportunity_id,
            theme_id=theme.id,
            symbol=f"ROLL{index}/USDT",
            direction="long",
        ))

    real_insert = repo.insert_pi_decision
    calls = 0

    def fail_second(decision):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("deterministic carrier failure")
        return real_insert(decision)

    monkeypatch.setattr(repo, "insert_pi_decision", fail_second)
    with pytest.raises(RuntimeError, match="deterministic carrier failure"):
        repo.enqueue_serenity_theme_for_pi(theme.id, ids)

    assert repo.get_serenity_theme(theme.id)["status"] == "active"
    assert {
        row["id"]: row["action_intent"]
        for row in repo.list_serenity_opportunities(theme_id=theme.id, limit=10)
    } == {opportunity_id: "watch" for opportunity_id in ids}
    assert [
        row for row in repo.list_pi_decisions(limit=20)
        if row.get("signal_source") == "serenity"
    ] == []


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    (
        ("symbol", "WRONG/USDT"),
        ("direction", "short"),
        ("market_type", None),
    ),
)
@spec_claim(
    SpecClaim(
        "PL-MSI-13",
        "pipeline_middle_state_integrity",
        "§6 exact Serenity carrier identity",
        "L2b",
        "A wrong or legacy-partial pending carrier cannot promote a Serenity theme, "
        "and the handoff never hides the conflict behind a second carrier.",
        status="implemented",
    )
)
def test_enqueue_serenity_rejects_mismatched_existing_carrier(
    tmp_path,
    monkeypatch,
    field,
    wrong_value,
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import PiDecision, SerenityOpportunity, SerenityTheme

    theme = SerenityTheme(id=f"theme-wrong-{field}", name="Wrong carrier")
    repo.insert_serenity_theme(theme)
    opportunity = SerenityOpportunity(
        id=f"serenity-wrong-{field}",
        theme_id=theme.id,
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
    )
    repo.insert_serenity_opportunity(opportunity)
    carrier_identity = {
        "symbol": opportunity.symbol,
        "direction": opportunity.direction,
        "market_type": opportunity.market_type,
    }
    carrier_identity[field] = wrong_value
    repo.insert_pi_decision(PiDecision(
        opportunity_id=opportunity.id,
        signal_source="serenity",
        action="signal_pending",
        **carrier_identity,
    ))

    with pytest.raises(ValueError, match="conflicts with opportunity identity"):
        repo.enqueue_serenity_theme_for_pi(theme.id, [opportunity.id])

    assert repo.get_serenity_theme(theme.id)["status"] == "active"
    assert repo.get_serenity_opportunity(opportunity.id)["action_intent"] == "watch"
    carriers = [
        row
        for row in repo.list_pi_decisions(limit=20)
        if row.get("opportunity_id") == opportunity.id
    ]
    assert len(carriers) == 1


@spec_claim(
    SpecClaim(
        "PL-MSI-11",
        "pipeline_middle_state_integrity",
        "§1 durable reservation schema",
        "L2a",
        "The pre-Order Risk allowance has an additive durable table and is registered "
        "in the data-model reference.",
        status="implemented",
    )
)
def test_entry_risk_reservation_schema_is_registered(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(entry_risk_reservations)").fetchall()
    }
    assert columns == {
        "proposal_id",
        "proposal_commit_hash",
        "symbol",
        "market_type",
        "execution_mode",
        "quantity",
        "price",
        "leverage",
        "created_at",
    }
    root = Path(__file__).resolve().parents[2]
    assert "`entry_risk_reservations`" in (
        root / "docs/design/data/data_model.md"
    ).read_text(encoding="utf-8")


@spec_claim(
    SpecClaim(
        "PL-MSI-16",
        "pipeline_middle_state_integrity",
        "§1 reservation upgrade continuity",
        "L2c",
        "Schema upgrade backfills only provable approved, non-terminal, no-Order "
        "authorities; incomplete legacy identity remains unreserved and fail-closed.",
        status="implemented",
    )
)
def test_reservation_migration_backfills_only_provable_authority(
    tmp_path,
    monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import RiskDecision, TradeProposal

    proposal = TradeProposal(
        id="migration-provable-authority",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        price=100.0,
        leverage=4,
        execution_mode="paper_internal",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(RiskDecision(
        id="migration-provable-decision",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    ))

    # An old row with no commit identity cannot be promoted into authority by migration.
    conn.execute(
        "INSERT INTO trade_proposals "
        "(id,symbol,market_type,direction,order_type,quantity,price,execution_mode,"
        "status,source,commit_hash) VALUES "
        "('migration-unknown','ETH/USDT','usdt_futures','long','market',1,100,"
        "'paper_internal','executing','native',NULL)"
    )
    conn.execute(
        "INSERT INTO risk_decisions "
        "(id,proposal_id,approved,allowed_execution_mode,proposal_execution_mode,"
        "decision_role,evaluated_quantity,proposal_commit_hash) VALUES "
        "('migration-unknown-decision','migration-unknown',1,'paper_internal',"
        "'paper_internal','authoritative',1,NULL)"
    )
    conn.execute("DROP TABLE entry_risk_reservations")
    run_migrations()

    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM entry_risk_reservations ORDER BY proposal_id"
        ).fetchall()
    ]
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == proposal.id
    assert rows[0]["proposal_commit_hash"] == proposal.commit_hash
    assert rows[0]["market_type"] == proposal.market_type
    assert rows[0]["leverage"] == pytest.approx(4.0)


@spec_claim(
    SpecClaim(
        "PL-MSI-17",
        "pipeline_middle_state_integrity",
        "§1 normalized reservation upgrade continuity",
        "L2c",
        "An older reservation whose quantity was venue-normalized remains provable "
        "when it equals the authoritative RiskDecision evaluated quantity, even when "
        "the original hashed Proposal committed a different requested quantity.",
        status="implemented",
    )
)
def test_reservation_migration_preserves_normalized_evaluated_quantity(
    tmp_path,
    monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import RiskDecision, TradeProposal

    proposal = TradeProposal(
        id="migration-normalized-authority",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=10.0,
        price=100.0,
        leverage=4,
        execution_mode="paper_internal",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(RiskDecision(
        id="migration-normalized-decision",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=9.0,
        proposal_commit_hash=proposal.commit_hash,
    ))

    conn.execute("DROP TABLE entry_risk_reservations")
    conn.execute(
        """
        CREATE TABLE entry_risk_reservations (
            proposal_id TEXT PRIMARY KEY,
            proposal_commit_hash TEXT NOT NULL,
            symbol TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "INSERT INTO entry_risk_reservations "
        "(proposal_id,proposal_commit_hash,symbol,execution_mode,quantity,price) "
        "VALUES (?,?,?,?,?,?)",
        (
            proposal.id,
            proposal.commit_hash,
            proposal.symbol,
            proposal.execution_mode,
            9.0,
            proposal.price,
        ),
    )

    run_migrations()

    reservation = repo.get_entry_risk_reservation(proposal.id)
    assert reservation is not None
    assert reservation["quantity"] == pytest.approx(9.0)
    assert reservation["market_type"] == proposal.market_type
    assert reservation["leverage"] == pytest.approx(4.0)


def test_safety_loops_do_not_use_the_truncated_open_position_reader():
    root = Path(__file__).resolve().parents[2]
    paths = (
        "aletheia/risk/engine.py",
        "aletheia/portfolio/position_manager.py",
        "aletheia/portfolio/position_reconciler.py",
        "aletheia/data/price_feed.py",
        "aletheia/trading/market_data.py",
        "aletheia/agent_runtime/scheduler.py",
        "aletheia/execution/paper_broker.py",
        "aletheia/trust/guard_chain.py",
    )
    for relative in paths:
        source = (root / relative).read_text(encoding="utf-8")
        assert 'list_positions(status="open")' not in source, relative
