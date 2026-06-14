"""Unit tests for aletheia.analytics.trade_attribution."""
from __future__ import annotations

from unittest.mock import patch

from aletheia.analytics.trade_attribution import (
    build_trade_attribution_rows,
    confidence_bucket,
    factor_key,
    late_entry_bucket,
    _compute_holding_hours,
)


# ---------------------------------------------------------------------------
# confidence_bucket
# ---------------------------------------------------------------------------

def test_confidence_bucket_score_very_high():
    assert confidence_bucket(85, None) == "very_high"


def test_confidence_bucket_score_high():
    assert confidence_bucket(70, None) == "high"


def test_confidence_bucket_score_medium():
    assert confidence_bucket(55, None) == "medium"


def test_confidence_bucket_score_low():
    assert confidence_bucket(30, None) == "low"


def test_confidence_bucket_label_high():
    assert confidence_bucket(None, "high") == "high"


def test_confidence_bucket_label_medium():
    assert confidence_bucket(None, "medium") == "medium"


def test_confidence_bucket_label_fallback():
    assert confidence_bucket(None, "speculative") == "low"
    assert confidence_bucket(None, None) == "low"


# ---------------------------------------------------------------------------
# late_entry_bucket
# ---------------------------------------------------------------------------

def test_late_entry_bucket_values():
    assert late_entry_bucket(None) == "unknown"
    assert late_entry_bucket(25.0) == "very_late"
    assert late_entry_bucket(15.0) == "late"
    assert late_entry_bucket(5.0) == "mild"
    assert late_entry_bucket(0.0) == "clean"


# ---------------------------------------------------------------------------
# factor_key
# ---------------------------------------------------------------------------

def test_factor_key_full():
    row = {
        "scanner_family": "momentum",
        "symbol": "BTC/USDT",
        "direction": "long",
        "confidence_score": 75,
        "confidence": "high",
        "late_entry_penalty": 5.0,
    }
    key = factor_key(row)
    assert key == "momentum:BTC/USDT:long:high:mild"


def test_factor_key_missing_fields():
    row = {}
    key = factor_key(row)
    assert key == "unknown:unknown:unknown:low:unknown"


# ---------------------------------------------------------------------------
# _compute_holding_hours
# ---------------------------------------------------------------------------

def test_holding_hours_valid():
    hours = _compute_holding_hours(
        "2026-06-14T10:00:00+00:00",
        "2026-06-14T14:30:00+00:00",
    )
    assert hours == 4.5


def test_holding_hours_none():
    assert _compute_holding_hours(None, "2026-06-14T14:30:00+00:00") is None
    assert _compute_holding_hours("2026-06-14T10:00:00+00:00", None) is None


# ---------------------------------------------------------------------------
# build_trade_attribution_rows
# ---------------------------------------------------------------------------

def test_build_rows_with_full_chain():
    """Closed position with full causal chain produces complete row."""
    fake_position = {
        "id": "pos-1", "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": "long", "execution_mode": "bitget_demo", "strategy_id": "strat-1",
        "proposal_id": "prop-1", "order_id": "ord-1",
        "entry_price": "50000", "current_price": "51000",
        "quantity": "0.001", "realized_pnl": "1.0",
        "close_reason": "take_profit", "status": "closed",
        "opened_at": "2026-06-14T10:00:00+00:00",
        "closed_at": "2026-06-14T14:00:00+00:00",
    }
    fake_proposal = {"id": "prop-1", "opportunity_id": "opp-1"}
    fake_opportunity = {
        "id": "opp-1", "source_agent": "market_observer",
        "scanner_family": "momentum", "confidence": "high",
        "confidence_score": 80, "status": "proposed",
        "universe_member_id": "um-1",
    }
    fake_strategy = {"id": "strat-1", "name": "test", "parameters": "{}"}
    fake_universe_member = {
        "id": "um-1", "rank": 3, "total_score": 72.0,
        "opportunity_score": 60.0, "tradability_score": 80.0,
        "late_entry_penalty": 5.0, "crowding_risk": 10.0,
        "risk_flags": '["late_entry"]',
    }
    fake_risk_decision = {"id": "rd-1", "proposal_id": "prop-1", "approved": True, "violations": "[]"}

    with patch("aletheia.analytics.trade_attribution.repo") as mock_repo:
        mock_repo.list_positions.return_value = [fake_position]
        mock_repo.get_trade_proposal.return_value = fake_proposal
        mock_repo.get_order.return_value = {"id": "ord-1"}
        mock_repo.get_opportunity.return_value = fake_opportunity
        mock_repo.get_strategy.return_value = fake_strategy
        mock_repo.get_universe_member.return_value = fake_universe_member
        mock_repo.list_risk_decisions.return_value = [fake_risk_decision]

        rows = build_trade_attribution_rows(limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["position_id"] == "pos-1"
    assert row["symbol"] == "BTC/USDT"
    assert row["direction"] == "long"
    assert row["scanner_family"] == "momentum"
    assert row["universe_rank"] == 3
    assert row["tradability_score"] == 80.0
    assert row["risk_flags"] == ["late_entry"]
    assert row["risk_approved"] is True
    assert row["holding_hours"] == 4.0
    assert row["realized_pnl_pct"] > 0


def test_build_rows_missing_opportunity():
    """Position without opportunity link still produces a row with None fields."""
    fake_position = {
        "id": "pos-2", "symbol": "ETH/USDT", "market_type": "usdt_futures",
        "direction": "short", "execution_mode": "paper_internal",
        "strategy_id": None, "proposal_id": None, "order_id": None,
        "entry_price": "3000", "current_price": "2900",
        "quantity": "0.01", "realized_pnl": "1.0",
        "close_reason": "manual", "status": "closed",
        "opened_at": "2026-06-14T10:00:00+00:00",
        "closed_at": "2026-06-14T12:00:00+00:00",
    }

    with patch("aletheia.analytics.trade_attribution.repo") as mock_repo:
        mock_repo.list_positions.return_value = [fake_position]
        mock_repo.get_trade_proposal.return_value = None
        mock_repo.get_order.return_value = None
        mock_repo.get_opportunity.return_value = None
        mock_repo.get_strategy.return_value = None
        mock_repo.get_universe_member.return_value = None
        mock_repo.list_risk_decisions.return_value = []

        rows = build_trade_attribution_rows(limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["position_id"] == "pos-2"
    assert row["opportunity_id"] is None
    assert row["scanner_family"] is None
    assert row["universe_rank"] is None
    assert row["risk_approved"] is None


def test_realized_pnl_pct_calculation():
    """Verify realized_pnl_pct = pnl / (entry * quantity) * 100."""
    fake_position = {
        "id": "pos-3", "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": "long", "execution_mode": "bitget_demo",
        "strategy_id": None, "proposal_id": None, "order_id": None,
        "entry_price": "50000", "current_price": "50500",
        "quantity": "0.01", "realized_pnl": "5.0",
        "close_reason": "take_profit", "status": "closed",
        "opened_at": "2026-06-14T10:00:00+00:00",
        "closed_at": "2026-06-14T11:00:00+00:00",
    }

    with patch("aletheia.analytics.trade_attribution.repo") as mock_repo:
        mock_repo.list_positions.return_value = [fake_position]
        mock_repo.get_trade_proposal.return_value = None
        mock_repo.list_risk_decisions.return_value = []

        rows = build_trade_attribution_rows(limit=10)

    row = rows[0]
    # pnl_pct = 5.0 / (50000 * 0.01) * 100 = 1.0%
    assert row["realized_pnl_pct"] == 1.0
