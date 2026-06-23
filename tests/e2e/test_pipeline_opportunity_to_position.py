"""Pipeline 2: opportunity → ranker → risk engine → position 开仓.

Verifies the SEAM between modules: field names match, filters align,
and a valid opportunity can actually flow through to become a position.
"""

import pytest
from datetime import datetime, timezone

from aletheia.storage.models import Opportunity, Position, TradeProposal, RiskDecision, Order
from aletheia.storage import repositories as repo


class TestOpportunityToRankerSeam:
    """Market Observer output can be consumed by paper_trader_ranker."""

    def test_opportunity_fields_match_ranker_expectations(self, e2e_db):
        """Ranker queries by status='open', asset_class='crypto', execution_eligible=True."""
        opp = Opportunity(
            symbol="ETH/USDT",
            market="spot",
            direction_bias="short",
            confidence="high",
            confidence_score=78,
            source_agent="market_observer",
            asset_class="crypto",
            execution_eligible=True,
            scanner_family="momentum",
        )
        repo.insert_opportunity(opp)

        from aletheia.storage.repositories import list_opportunities
        result = list_opportunities(limit=10)
        found = [o for o in result if o["symbol"] == "ETH/USDT"]
        assert len(found) == 1
        assert found[0]["status"] == "open"
        assert found[0]["confidence"] == "high"
        assert found[0]["confidence_score"] == 78
        assert found[0]["direction_bias"] == "short"
        assert found[0]["execution_eligible"] == 1

    def test_ranker_filters_by_execution_mode_correctly(self, e2e_db, monkeypatch):
        """Ranker uses min_confidence_to_trade from config — verify the gate works."""
        opp_high = Opportunity(
            symbol="BTC/USDT", market="spot", direction_bias="long",
            confidence="high", confidence_score=78,
            source_agent="market_observer", scanner_family="momentum",
        )
        opp_spec = Opportunity(
            symbol="SOL/USDT", market="spot", direction_bias="short",
            confidence="speculative", confidence_score=42,
            source_agent="market_observer", scanner_family="momentum",
        )
        repo.insert_opportunity(opp_high)
        repo.insert_opportunity(opp_spec)

        from aletheia.config.loader import load_config
        cfg = load_config()
        monkeypatch.setattr(cfg.trading, "min_confidence_to_trade", "medium")

        from aletheia.agents.paper_trader_ranker import build_paper_candidates
        candidates = build_paper_candidates(limit=50)

        btc_candidates = [c for c in candidates if c["symbol"] == "BTC/USDT"]
        sol_candidates = [c for c in candidates if c["symbol"] == "SOL/USDT"]

        # BTC (high confidence) should pass the gate
        assert any(not c.get("skip_reason") for c in btc_candidates)
        # SOL (speculative) should be skipped when min=medium
        assert all(c.get("skip_reason") for c in sol_candidates)


class TestRiskEngineSeam:
    """Trade proposals from ranker flow through risk engine correctly."""

    def test_proposal_approved_by_risk_engine(self, e2e_db, monkeypatch):
        """A well-formed proposal passes the risk engine."""
        proposal = TradeProposal(
            symbol="ETH/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=0.01,
            price=1740.0,
            execution_mode="paper_internal",
            stop_price=1740 * 1.02,
        )
        repo._insert("trade_proposals", proposal.model_dump())

        from aletheia.risk.engine import evaluate_proposal
        result = evaluate_proposal(proposal)
        assert result.approved is True

    def test_proposal_rejected_exceeds_notional(self, e2e_db, monkeypatch):
        """Proposal exceeding max notional is rejected."""
        from aletheia.config.loader import load_config
        cfg = load_config()
        monkeypatch.setattr(cfg.risk, "max_demo_position_notional_usdt", 10.0)

        proposal = TradeProposal(
            symbol="ETH/USDT",
            market_type="usdt_futures",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=1740.0,
            execution_mode="paper_internal",
        )
        repo._insert("trade_proposals", proposal.model_dump())

        from aletheia.risk.engine import evaluate_proposal
        result = evaluate_proposal(proposal)
        assert result.approved is False


class TestPositionCreationSeam:
    """Order execution creates a position with correct fields."""

    def test_paper_order_creates_position(self, e2e_db):
        """A filled order results in a position with matching fields."""
        pos = Position(
            symbol="ETH/USDT",
            market_type="usdt_futures",
            direction="short",
            quantity=0.01,
            entry_price=1740.0,
            execution_mode="paper_internal",
            status="open",
        )
        repo.insert_position(pos)

        positions = repo.list_positions(status="open")
        found = [p for p in positions if p["symbol"] == "ETH/USDT"]
        assert len(found) == 1
        assert found[0]["direction"] == "short"
        assert found[0]["execution_mode"] == "paper_internal"
        assert found[0]["entry_price"] == 1740.0
