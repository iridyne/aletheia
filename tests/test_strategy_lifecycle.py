"""Tests for strategy lifecycle: forward-only transitions, promotion gates."""

from datetime import datetime, timedelta

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.trading.strategy_lifecycle import (
    create_strategy, promote_strategy, FORWARD_TRANSITIONS, check_promotion_gates,
)
from aletheia.storage.repositories import get_strategy
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, Position, RiskDecision, TradeProposal
from aletheia.knowledge.manager import create_post_trade_review


def setup_test_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "knowledge" / "post_trade_reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _seed_pi_approval(strategy_id: str) -> None:
    """Plant a PI recommend_promote record so paper_running gate passes."""
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision
    pi_dec = PiDecision(
        symbol=None,
        direction=None,
        action="recommend_promote",
        conviction=0,
        reasoning="test fixture",
        proposal_id=strategy_id,
        debate_ran=False,
    )
    insert_pi_decision(pi_dec)


def test_create_strategy_in_draft(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Trend Following V1", "Momentum-based")
    assert s.state == "draft"
    record = get_strategy(s.id)
    assert record["name"] == "Trend Following V1"
    reset_db()


def test_forward_transition_works(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Test Strategy")
    result = promote_strategy(s.id, "research_note")
    assert result["success"] is True
    assert result["new_state"] == "research_note"
    record = get_strategy(s.id)
    assert record["state"] == "research_note"
    reset_db()


def test_backward_transition_blocked(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Test")
    promote_strategy(s.id, "research_note")
    result = promote_strategy(s.id, "draft")
    assert result["success"] is False
    assert "Cannot transition" in result["error"]
    reset_db()


def test_skip_transition_blocked(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Test")
    # Can't jump from draft to paper_running
    result = promote_strategy(s.id, "paper_running")
    assert result["success"] is False
    reset_db()


def test_full_lifecycle_forward(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Full Lifecycle")
    _seed_pi_approval(s.id)
    # paper_passed now runs the full promotion gate, so seed paper evidence
    # while in paper_running before the gated promotion.
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]:
        result = promote_strategy(s.id, target)
        assert result["success"] is True, f"Failed at {target}: {result}"
    _seed_strategy_evidence(s.id)
    result = promote_strategy(s.id, "paper_passed")
    assert result["success"] is True, f"Failed at paper_passed: {result}"
    record = get_strategy(s.id)
    assert record["state"] == "paper_passed"
    reset_db()


def test_promotion_gate_check_not_in_paper_running_or_passed(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Gate Test")
    result = check_promotion_gates(s.id)
    assert result["passed"] is False
    # gate now admits paper_running OR paper_passed; a draft strategy fails it.
    assert "must_be_in_paper_running_or_paper_passed_state" in str(result["failures"])
    reset_db()


def test_live_candidate_requires_paper_orders(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Paper Order Required")
    _seed_pi_approval(s.id)
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running", "paper_passed"]:
        promote_strategy(s.id, target)
    result = check_promotion_gates(s.id)
    assert result["passed"] is False
    assert "no_strategy_paper_orders" in result["failures"]
    reset_db()


def test_live_candidate_counts_only_paper_fills(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Paper Fill Isolation")
    _seed_pi_approval(s.id)
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running", "paper_passed"]:
        promote_strategy(s.id, target)

    created_at = (datetime.utcnow() - timedelta(days=15)).isoformat()
    # One paper order older than minimum runtime.
    paper_proposal = TradeProposal(
        strategy_id=s.id,
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=60000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(paper_proposal)
    decision = RiskDecision(proposal_id=paper_proposal.id, approved=True, allowed_execution_mode="paper_internal")
    repo.insert_risk_decision(decision)
    repo.insert_order(Order(
        proposal_id=paper_proposal.id,
        risk_decision_id=decision.id,
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60000,
        execution_mode="paper_internal",
        status="filled",
        created_at=created_at,
        filled_at=created_at,
    ))

    # 30 live-limited fills should not be counted for paper gate checks.
    for i in range(30):
        live_proposal = TradeProposal(
            strategy_id=s.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            price=60000 + i,
            execution_mode="bitget_live_limited",
        )
        repo.insert_trade_proposal(live_proposal)
        live_decision = RiskDecision(proposal_id=live_proposal.id, approved=True, allowed_execution_mode="bitget_live_limited")
        repo.insert_risk_decision(live_decision)
        live_order = Order(
            proposal_id=live_proposal.id,
            risk_decision_id=live_decision.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=60000 + i,
            execution_mode="bitget_live_limited",
            status="filled",
            created_at=created_at,
            filled_at=created_at,
        )
        repo.insert_order(live_order)
        repo.insert_fill(Fill(
            order_id=live_order.id,
            fill_price=60000 + i,
            fill_quantity=0.01,
            filled_at=created_at,
        ))

    # Positive paper profitability + review so only trade-count can fail.
    repo.insert_position(Position(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=60000,
        current_price=60000,
        realized_pnl=10.0,
        execution_mode="paper_internal",
        strategy_id=s.id,
        status="closed",
        opened_at=created_at,
        closed_at=created_at,
    ))
    create_post_trade_review("Post-Trade Review", "Reviewed isolated paper signal.", strategy_id=s.id)

    result = check_promotion_gates(s.id)
    assert result["passed"] is False
    assert any("insufficient_paper_trades" in failure for failure in result["failures"])
    reset_db()


def test_positive_expectancy_failure_is_non_positive(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Non-Positive Expectancy")
    _seed_pi_approval(s.id)
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running", "paper_passed"]:
        promote_strategy(s.id, target)
    created_at = (datetime.utcnow() - timedelta(days=15)).isoformat()
    for i in range(30):
        proposal = TradeProposal(
            strategy_id=s.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            price=60000 + i,
            execution_mode="paper_internal",
        )
        repo.insert_trade_proposal(proposal)
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode="paper_internal",
        )
        repo.insert_risk_decision(decision)
        order = Order(
            proposal_id=decision.proposal_id,
            risk_decision_id=decision.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=60000 + i,
            execution_mode="paper_internal",
            status="filled",
            created_at=created_at,
            filled_at=created_at,
        )
        repo.insert_order(order)
        repo.insert_fill(Fill(order_id=order.id, fill_price=60000 + i, fill_quantity=0.01, filled_at=created_at))
        repo.insert_position(Position(
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=60000 + i,
            current_price=60000 + i,
            realized_pnl=-1.0,
            execution_mode="paper_internal",
            strategy_id=s.id,
            status="closed",
            opened_at=created_at,
            closed_at=created_at,
        ))
    create_post_trade_review("Post-Trade Review", "Lossy run.", strategy_id=s.id)
    result = check_promotion_gates(s.id)
    assert result["passed"] is False
    assert any("non_positive_expectancy" in failure for failure in result["failures"])
    reset_db()


def test_live_candidate_requires_paper_passed(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Live Gate Test")
    _seed_pi_approval(s.id)
    # Advance to paper_running (the last ungated state).
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]:
        promote_strategy(s.id, target)
    # State alone is not enough; the gate (now enforced at paper_passed) needs
    # real paper evidence — without it, promotion is blocked.
    result = promote_strategy(s.id, "paper_passed")
    assert result["success"] is False
    assert "insufficient_paper_trades" in result["error"]
    reset_db()


def test_live_candidate_passes_with_required_paper_evidence(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Evidence Gate Test")
    _seed_pi_approval(s.id)
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]:
        promote_strategy(s.id, target)
    _seed_strategy_evidence(s.id)

    gate = check_promotion_gates(s.id)
    assert gate["passed"] is True, gate
    assert promote_strategy(s.id, "paper_passed")["success"] is True
    result = promote_strategy(s.id, "live_candidate")
    assert result["success"] is True
    reset_db()


def test_paper_evidence_includes_bitget_demo_modes(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Demo Evidence Test")
    _seed_pi_approval(s.id)
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running", "paper_passed"]:
        promote_strategy(s.id, target)

    created_at = (datetime.utcnow() - timedelta(days=15)).isoformat()
    for i in range(10):
        proposal = TradeProposal(
            strategy_id=s.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            price=60000 + i,
            execution_mode="bitget_demo",
        )
        repo.insert_trade_proposal(proposal)
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            violations="[]",
            allowed_execution_mode="bitget_demo",
        )
        repo.insert_risk_decision(decision)
        order = Order(
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=60000 + i,
            execution_mode="bitget_demo",
            status="filled",
            created_at=created_at,
            filled_at=created_at,
        )
        repo.insert_order(order)
        repo.insert_fill(Fill(order_id=order.id, fill_price=60000 + i, fill_quantity=0.01, filled_at=created_at))
        repo.insert_position(Position(
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=60000 + i,
            current_price=60000 + i,
            realized_pnl=1.0,
            execution_mode="bitget_demo",
            strategy_id=s.id,
            status="closed",
            opened_at=created_at,
            closed_at=created_at,
        ))

    create_post_trade_review("Post-Trade Review",
                             "Demo evidence accepted. 10 trades reviewed with mixed results; additional bitget_demo trades being seeded to meet minimum threshold.",
                             strategy_id=s.id)
    result = check_promotion_gates(s.id)
    assert result["passed"] is False
    assert any(failure.startswith("insufficient_paper_trades") for failure in result["failures"])

    for i in range(20):
        proposal = TradeProposal(
            strategy_id=s.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            price=60000 + i,
            execution_mode="bitget_demo",
        )
        repo.insert_trade_proposal(proposal)
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            violations="[]",
            allowed_execution_mode="bitget_demo",
        )
        repo.insert_risk_decision(decision)
        order = Order(
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=60000 + i,
            execution_mode="bitget_demo",
            status="filled",
            created_at=created_at,
            filled_at=created_at,
        )
        repo.insert_order(order)
        repo.insert_fill(Fill(order_id=order.id, fill_price=60000 + i, fill_quantity=0.01, filled_at=created_at))

    result = check_promotion_gates(s.id)
    assert result["passed"] is True
    reset_db()


def test_strategy_post_trade_review_required_by_strategy(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Strategy-Specific Review Required")
    _seed_pi_approval(s.id)
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]:
        promote_strategy(s.id, target)
    _seed_strategy_evidence(s.id, include_strategy_review=False)
    # Missing post-trade review blocks the gate (now enforced at paper_passed).
    result = promote_strategy(s.id, "paper_passed")
    assert result["success"] is False
    assert "post_trade_review_required" in result["error"]

    create_post_trade_review(
        "Post-Trade Review Strategy-Specific",
        "Reviewed paper evidence for this strategy. 30 trades completed with 58% win rate. "
        "Average risk-reward 1.4:1. Recommend promoting to paper_passed.",
        strategy_id=s.id,
    )
    assert promote_strategy(s.id, "paper_passed")["success"] is True
    result = promote_strategy(s.id, "live_candidate")
    assert result["success"] is True
    reset_db()


def test_live_candidate_to_live_limited_requires_live_unlock_and_permission(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Live Limited Gate Test")
    _seed_pi_approval(s.id)
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]:
        promote_strategy(s.id, target)
    _seed_strategy_evidence(s.id)
    assert promote_strategy(s.id, "paper_passed")["success"] is True
    result = promote_strategy(s.id, "live_candidate")
    assert result["success"] is True

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Observe"
    save_config(cfg)
    result = promote_strategy(s.id, "live_limited")
    assert result["success"] is False
    assert "permission_mode_not_live_promotion" in result["error"]

    cfg.agent.permission_mode = "Full"
    save_config(cfg)
    result = promote_strategy(s.id, "live_limited")
    assert result["success"] is False
    assert "live_state_not_limited" in result["error"]

    cfg.live.state = "live_limited"
    cfg.live.first_live_confirmation_done = False
    save_config(cfg)
    result = promote_strategy(s.id, "live_limited")
    assert result["success"] is False
    assert "live_first_confirmation_not_done" in result["error"]

    cfg.live.first_live_confirmation_done = True
    save_config(cfg)
    result = promote_strategy(s.id, "live_limited")
    assert result["success"] is True
    record = get_strategy(s.id)
    assert record["state"] == "live_limited"
    reset_db()


def test_live_limited_rejects_critical_risk_violations(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Live Limited Critical Risk Gate")
    _seed_pi_approval(s.id)
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]:
        promote_strategy(s.id, target)
    _seed_strategy_evidence(s.id)
    assert promote_strategy(s.id, "paper_passed")["success"] is True
    result = promote_strategy(s.id, "live_candidate")
    assert result["success"] is True

    from aletheia.storage.models import RiskDecision, TradeProposal
    proposal = TradeProposal(
        strategy_id=s.id,
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(RiskDecision(
        proposal_id=proposal.id,
        approved=False,
        violations='["strategy_not_live_eligible:paper_only"]',
    ))

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.live.state = "live_limited"
    cfg.live.first_live_confirmation_done = True
    save_config(cfg)

    result = promote_strategy(s.id, "live_limited")
    assert result["success"] is False
    assert "critical_risk_violations" in result["error"]
    reset_db()


def _seed_strategy_evidence(strategy_id: str, include_strategy_review: bool = True) -> None:
    created_at = (datetime.utcnow() - timedelta(days=15)).isoformat()
    for i in range(30):
        proposal = TradeProposal(
            strategy_id=strategy_id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            price=60000 + i,
            execution_mode="paper_internal",
        )
        repo.insert_trade_proposal(proposal)
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            violations="[]",
            allowed_execution_mode="paper_internal",
        )
        repo.insert_risk_decision(decision)
        order = Order(
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=60000 + i,
            execution_mode="paper_internal",
            status="filled",
            created_at=created_at,
            filled_at=created_at,
        )
        repo.insert_order(order)
        repo.insert_fill(Fill(order_id=order.id, fill_price=60000 + i, fill_quantity=0.01, filled_at=created_at))
        repo.insert_position(Position(
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=60000 + i,
            current_price=60000 + i,
            realized_pnl=1.0,
            execution_mode="paper_internal",
            strategy_id=strategy_id,
            status="closed",
            opened_at=created_at,
            closed_at=created_at,
        ))
    if include_strategy_review:
        create_post_trade_review(
            "Post-Trade Review",
            "Reviewed 30 paper trades for this strategy. Win rate 60%, average winner 1.5x loser. "
            "Momentum entries performed well; mean reversion exits were premature. Recommend tightening exit rules.",
            strategy_id=strategy_id,
        )
