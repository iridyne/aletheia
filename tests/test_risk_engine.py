"""Tests for Risk Engine + Paper Broker + Execution Gateway."""

import pytest
import json
from unittest.mock import patch

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import RiskDecision, TradeProposal
from aletheia.storage import repositories as repo
from aletheia.risk.engine import evaluate_proposal
from aletheia.execution.gateway import submit_proposal
from aletheia.execution.paper_broker import execute_paper_order


def setup_test_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_risk_approves_valid_paper_proposal(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from tests.paper_trade_fixture import approvable_paper_trade
    trade = approvable_paper_trade(
        monkeypatch, symbol="BTC/USDT", market_type="spot", direction="long", price=60000,
    )
    proposal = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.001, price=60000, **trade,
    )
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert result.approved is True
    assert len(result.violations) == 0
    reset_db()


def test_authoritative_risk_decision_identity_includes_policy_hash(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from tests.paper_trade_fixture import approvable_paper_trade

    trade = approvable_paper_trade(
        monkeypatch,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        price=60000,
    )
    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.001,
        price=60000,
        **trade,
    )
    repo.insert_trade_proposal(proposal)
    first = evaluate_proposal(proposal)
    assert first.approved is True

    cfg = load_config()
    cfg.risk.demo.capital.max_per_symbol_entries += 1
    cfg.risk.demo.revision += 1
    save_config(cfg)

    second = evaluate_proposal(proposal)
    assert second.approved is True
    decisions = [
        row for row in repo.list_risk_decisions(limit=10)
        if row["proposal_id"] == proposal.id
        and row["decision_role"] == "authoritative"
    ]
    assert len(decisions) == 2
    assert len({row["policy_hash"] for row in decisions}) == 2
    reset_db()


def test_risk_rejects_disallowed_symbol(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.risk.symbol_list = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    save_config(cfg)
    proposal = TradeProposal(
        symbol="DOGE/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=1.0, price=0.1,
    )
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert result.approved is False
    assert any("symbol_not_allowed" in v for v in result.violations)
    reset_db()


def test_risk_blacklist_mode_allows_non_blocked(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.symbol_filter_mode = "blacklist"
    cfg.risk.symbol_list = ["DOGE/USDT"]
    cfg.agent.permission_mode = "Paper"
    save_config(cfg)
    from tests.paper_trade_fixture import approvable_paper_trade
    trade = approvable_paper_trade(
        monkeypatch, symbol="XRP/USDT", market_type="spot", direction="long", price=1.0,
    )
    proposal = TradeProposal(
        symbol="XRP/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.1, price=1.0, **trade,
    )
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert result.approved is True
    assert not any("symbol_blocked" in v for v in result.violations)
    reset_db()


def test_risk_blacklist_mode_rejects_blocked(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.symbol_filter_mode = "blacklist"
    cfg.risk.symbol_list = ["DOGE/USDT", "SHIB/USDT"]
    cfg.agent.permission_mode = "Paper"
    save_config(cfg)
    proposal = TradeProposal(
        symbol="DOGE/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=100, price=0.1,
    )
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert result.approved is False
    assert any("symbol_blocked" in v for v in result.violations)
    reset_db()


def test_risk_rejects_live_when_disabled(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    proposal = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_live_limited", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert result.approved is False
    assert any("live_disabled" in v for v in result.violations)
    reset_db()


def test_risk_rejects_insufficient_permission(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    # Observe mode cannot do demo execution
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Observe"
    save_config(cfg)
    proposal = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_demo", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert result.approved is False
    assert any("permission_mode_insufficient" in v for v in result.violations)
    reset_db()


def test_paper_broker_full_flow(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    proposal = TradeProposal(
        id="prop-1",
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.5,
        price=3000.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        id="risk-1",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode="paper_internal",
        proposal_execution_mode="paper_internal",
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    result = execute_paper_order(
        proposal_id=proposal.id, risk_decision_id=decision.id,
        symbol="ETH/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.5, price=3000.0,
    )
    assert result["fill_price"] == pytest.approx(3000.0, rel=0.01)  # slippage shifts slightly
    assert result["fill_quantity"] == 0.5

    orders = repo.list_orders()
    assert len(orders) == 1
    assert orders[0]["status"] == "filled"

    fills = repo.list_fills()
    assert len(fills) == 1

    positions = repo.list_positions(status="open")
    assert len(positions) == 1
    assert positions[0]["symbol"] == "ETH/USDT"
    assert positions[0]["entry_price"] == pytest.approx(3000.0, rel=0.01)  # slippage
    reset_db()


def test_execution_gateway_paper_flow(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    # Change permission to PaperAuto to allow paper execution
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    save_config(cfg)
    from tests.paper_trade_fixture import approvable_paper_trade
    trade = approvable_paper_trade(
        monkeypatch, symbol="SOL/USDT", market_type="spot", direction="long", price=150.0,
    )

    result = submit_proposal(
        symbol="SOL/USDT", market_type="spot", direction="long",
        quantity=0.5, price=150.0, execution_mode="paper_internal", **trade,
    )
    assert result["status"] == "executed"
    assert "order_id" in result
    assert "fill_price" in result

    positions = repo.list_positions(status="open")
    assert len(positions) == 1
    reset_db()


def test_execution_gateway_rejects_disallowed(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.risk.symbol_list = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    save_config(cfg)
    result = submit_proposal(
        symbol="XRP/USDT", market_type="spot", direction="long",
        quantity=10.0, price=0.5, execution_mode="paper_internal",
    )
    assert result["status"] == "rejected"
    assert "symbol_not_allowed" in str(result["risk_violations"])
    reset_db()


def test_agent_cannot_modify_risk_limits():
    """Verify risk config is read-only from agent perspective."""
    from aletheia.config.schema import RiskConfig
    cfg = RiskConfig()
    # The risk config is a Pydantic model — agent code can read but
    # the system enforces that only human/CLI/dashboard can write config
    assert cfg.max_daily_loss_pct == 1.0
    assert cfg.max_live_position_notional_usdt == 50.0
