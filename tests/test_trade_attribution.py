"""Unit tests for aletheia.analytics.trade_attribution."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from aletheia.analytics.outcome_integrity import _close_fill_vwap, immutable_outcome_row
from aletheia.analytics.trade_attribution import (
    _compute_holding_hours,
    build_trade_attribution_rows,
    confidence_bucket,
    factor_key,
    late_entry_bucket,
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
# _close_fill_vwap
# ---------------------------------------------------------------------------

def test_close_fill_vwap_uses_immutable_close_fills_not_current_mark():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE orders (id TEXT, position_id TEXT, close_reason TEXT);
        CREATE TABLE fills (order_id TEXT, fill_price REAL, fill_quantity REAL, filled_at TEXT);
        INSERT INTO orders VALUES ('close-1', 'pos-1', 'take_profit');
        INSERT INTO fills VALUES ('close-1', 100.0, 1.0, '2026-01-01T00:00:00+00:00');
        INSERT INTO fills VALUES ('close-1', 102.0, 1.0, '2026-01-01T00:01:00+00:00');
        """
    )

    assert _close_fill_vwap("pos-1", db=db) == 101.0
    assert _close_fill_vwap(
        "pos-1", db=db, as_of_at="2026-01-01T00:00:30+00:00"
    ) == 100.0
    projection = immutable_outcome_row(
        {"id": "pos-1", "current_price": 999.0, "unrealized_pnl": 900.0}, db=db
    )
    assert projection["current_price"] is None
    assert projection["unrealized_pnl"] is None
    assert projection["exit_price"] == 101.0


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
        "opportunity_id": "opp-1", "proposal_id": "prop-1", "order_id": "ord-1",
        "entry_price": "50000", "current_price": "999999", "exit_price": "51000",
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

    with patch("aletheia.analytics.trade_attribution.eligible_strategy_outcomes", return_value=[fake_position]), \
            patch("aletheia.analytics.trade_attribution.assess_reportable_outcome", return_value=SimpleNamespace(eligible=True)), \
            patch("aletheia.analytics.trade_attribution.repo") as mock_repo:
        mock_repo.get_trade_proposal.return_value = fake_proposal
        mock_repo.get_order.return_value = {
            "id": "ord-1", "proposal_id": "prop-1", "risk_decision_id": "rd-1",
        }
        mock_repo.get_opportunity.return_value = fake_opportunity
        mock_repo.get_strategy.return_value = fake_strategy
        mock_repo.get_universe_member.return_value = fake_universe_member
        mock_repo._select_one.return_value = fake_risk_decision

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
    assert row["exit_price"] == 51000.0
    assert row["holding_hours"] == 4.0
    assert row["realized_pnl_pct"] > 0


def test_build_rows_missing_opportunity_is_excluded():
    """A closed row without its entry lineage cannot be attributed safely."""
    fake_position = {
        "id": "pos-2", "symbol": "ETH/USDT", "market_type": "usdt_futures",
        "direction": "short", "execution_mode": "paper_internal",
        "strategy_id": None, "proposal_id": None, "order_id": None,
        "entry_price": "3000", "current_price": "2900", "exit_price": "2900",
        "quantity": "0.01", "realized_pnl": "1.0",
        "close_reason": "manual", "status": "closed",
        "opened_at": "2026-06-14T10:00:00+00:00",
        "closed_at": "2026-06-14T12:00:00+00:00",
    }

    with patch("aletheia.analytics.trade_attribution.eligible_strategy_outcomes", return_value=[fake_position]), \
            patch("aletheia.analytics.trade_attribution.assess_reportable_outcome", return_value=SimpleNamespace(eligible=True)), \
            patch("aletheia.analytics.trade_attribution.repo") as mock_repo:
        mock_repo.get_trade_proposal.return_value = None
        mock_repo.get_order.return_value = None
        mock_repo.get_opportunity.return_value = None
        mock_repo.get_strategy.return_value = None
        mock_repo.get_universe_member.return_value = None
        mock_repo.list_risk_decisions.return_value = []

        rows = build_trade_attribution_rows(limit=10)

    assert rows == []


def test_realized_pnl_pct_calculation():
    """Verify realized_pnl_pct = pnl / (entry * quantity) * 100."""
    fake_position = {
        "id": "pos-3", "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": "long", "execution_mode": "bitget_demo",
        "strategy_id": None, "opportunity_id": "opp-3",
        "proposal_id": "prop-3", "order_id": "ord-3",
        "entry_price": "50000", "current_price": "50500", "exit_price": "50500",
        "quantity": "0.01", "realized_pnl": "5.0",
        "close_reason": "take_profit", "status": "closed",
        "opened_at": "2026-06-14T10:00:00+00:00",
        "closed_at": "2026-06-14T11:00:00+00:00",
    }

    with patch("aletheia.analytics.trade_attribution.eligible_strategy_outcomes", return_value=[fake_position]), \
            patch("aletheia.analytics.trade_attribution.assess_reportable_outcome", return_value=SimpleNamespace(eligible=True)), \
            patch("aletheia.analytics.trade_attribution.repo") as mock_repo:
        mock_repo.get_trade_proposal.return_value = {
            "id": "prop-3", "opportunity_id": "opp-3",
        }
        mock_repo.get_order.return_value = {
            "id": "ord-3", "proposal_id": "prop-3", "risk_decision_id": None,
        }
        mock_repo.get_opportunity.return_value = {
            "id": "opp-3", "source_agent": "market_observer",
            "scanner_family": "momentum", "status": "proposed",
            "universe_member_id": None,
        }

        rows = build_trade_attribution_rows(limit=10)

    row = rows[0]
    # pnl_pct = 5.0 / (50000 * 0.01) * 100 = 1.0%
    assert row["realized_pnl_pct"] == 1.0


def test_attribution_uses_exact_proposal_decision_and_preserves_complete_context():
    """P1-06: no symbol/time guess and no truncation of persisted rationale."""
    long_reason = "complete PiDecision reason " + ("r" * 900)
    proposal_reason = "complete Proposal rationale " + ("p" * 700)
    fake_position = {
        "id": "pos-exact", "symbol": "BTC/USDT", "market_type": "spot",
        "direction": "long", "execution_mode": "paper_internal",
        "strategy_id": None, "opportunity_id": "opp-exact",
        "proposal_id": "prop-exact", "order_id": "ord-exact",
        "entry_price": "100", "current_price": "102", "exit_price": "102", "quantity": "1",
        "realized_pnl": "2", "status": "closed",
    }
    proposal = {
        "id": "prop-exact", "opportunity_id": "opp-exact",
        "symbol": "BTC/USDT", "direction": "long", "market_type": "spot",
        "agent_rationale": proposal_reason,
    }
    decision = {
        "id": "pi-exact", "proposal_id": "prop-exact",
        "opportunity_id": "opp-exact", "symbol": "BTC/USDT",
        "direction": "long", "market_type": "spot", "reasoning": long_reason,
        "bear_case": "complete bear case", "key_assumptions": '["assumption one"]',
        "evidence_manifest_json": '{"version":"evidence-v1","sources":["e1"]}',
        "learning_manifest_json": '{"version":"learning-v1","exposure_id":"x1"}',
    }

    with patch("aletheia.analytics.trade_attribution.eligible_strategy_outcomes", return_value=[fake_position]), \
            patch("aletheia.analytics.trade_attribution.assess_reportable_outcome", return_value=SimpleNamespace(eligible=True)), \
            patch("aletheia.analytics.trade_attribution.repo") as mock_repo:
        mock_repo.get_trade_proposal.return_value = proposal
        mock_repo.get_order.return_value = {
            "id": "ord-exact", "proposal_id": "prop-exact", "risk_decision_id": None,
        }
        mock_repo.get_opportunity.return_value = {
            "id": "opp-exact", "source_agent": "market_observer",
            "scanner_family": "momentum", "status": "reviewed",
        }
        mock_repo.get_pi_decision_for_proposal.return_value = decision

        [row] = build_trade_attribution_rows(limit=10)

    mock_repo.get_pi_decision_for_proposal.assert_called_once_with("prop-exact")
    assert row["pi_decision_reasoning"] == long_reason
    assert row["pi_decision_bear_case"] == "complete bear case"
    assert row["pi_decision_assumptions"] == ["assumption one"]
    assert row["proposal_agent_rationale"] == proposal_reason
    assert row["decision_evidence_manifest"]["sources"] == ["e1"]
    assert row["decision_learning_manifest"]["exposure_id"] == "x1"


def test_attribution_labels_legacy_decision_context_not_recorded():
    fake_position = {
        "id": "pos-legacy", "symbol": "ETH/USDT", "market_type": "spot",
        "direction": "short", "execution_mode": "paper_internal",
        "strategy_id": None, "opportunity_id": "opp-legacy",
        "proposal_id": "prop-legacy", "order_id": "ord-legacy",
        "entry_price": "100", "current_price": "99", "exit_price": "99", "quantity": "1",
        "realized_pnl": "1", "status": "closed",
    }
    with patch("aletheia.analytics.trade_attribution.eligible_strategy_outcomes", return_value=[fake_position]), \
            patch("aletheia.analytics.trade_attribution.assess_reportable_outcome", return_value=SimpleNamespace(eligible=True)), \
            patch("aletheia.analytics.trade_attribution.repo") as mock_repo:
        mock_repo.get_trade_proposal.return_value = {
            "id": "prop-legacy", "opportunity_id": "opp-legacy", "agent_rationale": None,
        }
        mock_repo.get_order.return_value = {
            "id": "ord-legacy", "proposal_id": "prop-legacy", "risk_decision_id": None,
        }
        mock_repo.get_opportunity.return_value = {
            "id": "opp-legacy", "source_agent": "market_observer", "status": "reviewed",
        }
        mock_repo.get_pi_decision_for_proposal.return_value = None

        [row] = build_trade_attribution_rows(limit=10)

    for field in (
        "pi_decision_reasoning", "pi_decision_bear_case", "pi_decision_assumptions",
        "proposal_agent_rationale", "decision_evidence_manifest",
        "decision_learning_manifest",
    ):
        assert row[field] == "not recorded"
