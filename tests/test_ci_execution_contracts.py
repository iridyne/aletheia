from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite_store
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sqlite_store.reset_db()
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    monkeypatch.setattr(sqlite_store, "_db_conn", conn)
    run_migrations()
    return conn


def test_valid_local_paper_entry_proves_causal_chain_idempotency_and_fee_source(
    tmp_path, monkeypatch,
) -> None:
    """A CI contract must fail, not skip, when the real paper path cannot execute.

    This deliberately supplies every current fail-closed prerequisite: exact
    Opportunity identity, exact fresh market mark, complete entry geometry and a
    small position below the demo cap. The test therefore proves the contracts
    that older conditional-skip tests could silently stop exercising.
    """
    _setup_db(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config, save_config
    from aletheia.execution.gateway import execute_existing_proposal, submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot, Opportunity

    cfg = load_config().model_copy(deep=True)
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    monkeypatch.setattr(
        "aletheia.execution.paper_broker._btc_price_now",
        lambda: 60_000.0,
    )
    monkeypatch.setattr(
        "aletheia.notifications.notify_trade_fill",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda **_kwargs: None,
    )

    opportunity = Opportunity(
        id="ci-execution-contract-opportunity",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        thesis="deterministic CI execution contract",
        confidence="high",
        confidence_score=90,
        source_agent="market_observer",
        scanner_family="ci_contract",
    )
    repo.insert_opportunity(opportunity)
    repo.insert_market_snapshot(MarketSnapshot(
        id="ci-execution-contract-mark",
        symbol=opportunity.symbol,
        market_type="spot",
        exchange=cfg.market_data.ccxt_exchange,
        price=60_000.0,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    ))

    opened = submit_proposal(
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
        opportunity_id=opportunity.id,
        agent_rationale="CI contract evidence",
    )

    assert opened["status"] == "executed", opened

    proposal = repo.get_trade_proposal(opened["proposal_id"])
    order = repo.get_order(opened["order_id"])
    fill = next(
        row for row in repo.list_fills(limit=10)
        if row["id"] == opened["fill_id"]
    )
    position = repo._select_one("positions", opened["position_id"])
    authoritative = [
        row for row in repo.list_risk_decisions(limit=10)
        if row["proposal_id"] == opened["proposal_id"]
        and row["decision_role"] == "authoritative"
    ]

    assert proposal is not None
    assert order is not None
    assert position is not None
    assert len(authoritative) == 1
    assert authoritative[0]["approved"] == 1
    assert order["proposal_id"] == proposal["id"]
    assert order["risk_decision_id"] == authoritative[0]["id"]
    assert fill["order_id"] == order["id"]
    assert fill["fee_source"] == "estimated"
    assert position["proposal_id"] == proposal["id"]
    assert position["order_id"] == order["id"]
    assert position["opportunity_id"] == opportunity.id

    order_count_before = len([
        row for row in repo.list_orders(limit=20)
        if row["proposal_id"] == proposal["id"]
    ])
    replay = execute_existing_proposal(
        proposal["id"],
        execution_mode="paper_internal",
    )
    order_count_after = len([
        row for row in repo.list_orders(limit=20)
        if row["proposal_id"] == proposal["id"]
    ])

    assert replay["status"] == "already_executed"
    assert order_count_before == 1
    assert order_count_after == order_count_before
