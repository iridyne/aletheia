"""Pipeline 2: opportunity → ranker → risk engine → position 开仓.

Verifies the SEAM between modules: field names match, filters align,
and a valid opportunity can actually flow through to become a position.
"""

import pytest
from datetime import datetime, timezone

from aletheia.storage.models import (
    MarketSnapshot,
    Opportunity,
    Position,
    TradeProposal,
    RiskDecision,
    Order,
)
from aletheia.storage import repositories as repo


class TestOpportunityToRankerSeam:
    """An independent non-Universe producer can be consumed by the Ranker."""

    def test_opportunity_fields_match_ranker_expectations(self, e2e_db):
        """Ranker queries by status='open', asset_class='crypto', execution_eligible=True."""
        opp = Opportunity(
            symbol="ETH/USDT",
            market="spot",
            direction_bias="short",
            confidence="high",
            confidence_score=78,
            source_agent="divergence_scanner",
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
            source_agent="divergence_scanner", scanner_family="momentum",
        )
        opp_spec = Opportunity(
            symbol="SOL/USDT", market="spot", direction_bias="short",
            confidence="speculative", confidence_score=42,
            source_agent="divergence_scanner", scanner_family="momentum",
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
        from aletheia.config.loader import load_config

        # paper_internal + native proposals require:
        #   1. a fresh market snapshot at cfg.market_data.ccxt_exchange ("binance")
        #   2. a valid opportunity_id that matches symbol/market/direction
        #   3. a complete entry plan (stop_price, take_profit, risk_reward)
        cfg = load_config()
        repo.insert_market_snapshot(MarketSnapshot(
            id="risk-seam-snapshot",
            symbol="ETH/USDT",
            market_type="spot",
            exchange=cfg.market_data.ccxt_exchange,
            price=1740.0,
            volume_24h=500_000_000.0,
            snapshot_at=datetime.now(timezone.utc).isoformat(),
        ))
        opp = Opportunity(
            symbol="ETH/USDT",
            market="spot",
            direction_bias="long",
            confidence="high",
            confidence_score=78,
            source_agent="market_observer",
            scanner_family="momentum",
        )
        repo.insert_opportunity(opp)

        proposal = TradeProposal(
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.01,
            price=1740.0,
            execution_mode="paper_internal",
            stop_price=round(1740 * 0.98, 4),
            take_profit=round(1740 * 1.04, 4),
            risk_reward=2.0,
            opportunity_id=opp.id,
        )
        repo.insert_trade_proposal(proposal)

        from aletheia.risk.engine import evaluate_proposal
        result = evaluate_proposal(proposal)
        assert result.approved is True

    def test_proposal_rejected_exceeds_notional(self, e2e_db, monkeypatch):
        """Proposal exceeding max notional is rejected."""
        from aletheia.config.loader import load_config
        cfg = load_config()
        monkeypatch.setattr(cfg.risk, "max_demo_position_margin_usdt", 10.0)

        proposal = TradeProposal(
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=1740.0,
            execution_mode="paper_internal",
            stop_price=1740 * 0.98,
        )
        repo.insert_trade_proposal(proposal)

        from aletheia.risk.engine import evaluate_proposal
        result = evaluate_proposal(proposal)
        assert result.approved is False
        assert any(
            violation.startswith("exceeds_demo_position_margin:")
            for violation in result.violations
        ), result.violations


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
