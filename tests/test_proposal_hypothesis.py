from __future__ import annotations

import json

import pytest

from aletheia.storage.models import Opportunity, TradeProposal
from aletheia.storage.proposal_hypothesis import (
    evaluate_hypothesis_prefix,
    freeze_position_hypothesis,
)


@pytest.fixture(autouse=True)
def _database(tmp_path, monkeypatch):
    from aletheia.config import loader
    from aletheia.storage import sqlite as sqlite_store
    from aletheia.storage.migrations import apply_migrations

    sqlite_store.reset_db()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    apply_migrations()
    yield
    sqlite_store.reset_db()
    loader._invalidate_config_cache()


def _opportunity() -> Opportunity:
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    db.execute(
        "INSERT INTO universe_registry "
        "(id,venue,market_type,symbol,status) VALUES "
        "('asset-hypothesis','bitget','perp','BTC/USDT','active')"
    )
    db.execute("INSERT INTO universe_snapshots (id,status) VALUES ('snapshot-hypothesis','active')")
    db.execute(
        "INSERT INTO universe_members "
        "(id,snapshot_id,asset_id,symbol,market_type,rank,total_score,expires_at) "
        "VALUES ('member-hypothesis','snapshot-hypothesis','asset-hypothesis',"
        "'BTC/USDT','perp',1,90,datetime('now','+1 day'))"
    )
    opportunity = Opportunity(
        id="opportunity-hypothesis",
        symbol="BTC/USDT",
        market="usdt_futures",
        direction_bias="long",
        thesis="breakout should hold above support",
        universe_member_id="member-hypothesis",
    )
    repo.insert_opportunity(opportunity)
    return opportunity


def test_native_entry_freezes_versioned_hypothesis_before_hash_and_persistence():
    from aletheia.storage import repositories as repo

    opportunity = _opportunity()
    proposal = TradeProposal(
        id="proposal-hypothesis",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        price=100.0,
        stop_price=95.0,
        take_profit=110.0,
        risk_reward=2.0,
        expected_horizon_hours=24.0,
        attribution_snapshot=json.dumps(
            {
                "supporting_signals": ["trend_slope"],
                "invalidation_conditions": [{"kind": "price_at_or_below", "threshold": 97.0}],
            }
        ),
    )

    repo.insert_trade_proposal(proposal)

    stored = repo.get_trade_proposal(proposal.id)
    assert stored is not None
    snapshot = json.loads(stored["attribution_snapshot"])
    hypothesis = snapshot["position_hypothesis"]
    assert hypothesis["contract_version"] == "position_hypothesis/v1"
    assert hypothesis["thesis"] == "breakout should hold above support"
    assert hypothesis["expected_direction"] == "long"
    assert hypothesis["expected_horizon_hours"] == 24.0
    assert hypothesis["initial_risk"]["quote_amount"] == 10.0
    assert hypothesis["market_identity"] == {
        "venue": "bitget",
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
    }
    assert proposal.commit_hash is not None
    assert stored["commit_hash"] == proposal.commit_hash

def test_claim_freezes_exact_in_flight_decision_inputs_before_pi_row_exists():
    from aletheia.agent_shell.reflection import claim_decision
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import PiDecision

    opportunity = _opportunity()
    repo.transition_open_opportunity_to_queued(opportunity.id)
    repo.insert_pi_decision(
        PiDecision(
            action="signal_pending",
            opportunity_id=opportunity.id,
            signal_source="market_observer",
            symbol=opportunity.symbol,
            direction="long",
            market_type="usdt_futures",
        )
    )
    proposal = TradeProposal(
        id="proposal-in-flight-decision",
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        price=100.0,
        stop_price=95.0,
    )

    claimed, created = claim_decision(
        {
            "action": "trade",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "usdt_futures",
            "opportunity_id": opportunity.id,
            "signal_source": "market_observer",
            "proposal_id": proposal.id,
            "proposal_status": "submitting",
            "reasoning": "in-flight rationale",
            "bear_case": "support may fail",
            "key_assumptions": ["volume persists"],
            "trade_motivation": {
                "thesis": "breakout continuation",
                "supporting_evidence": ["volume"],
                "invalidation_conditions": [],
            },
        },
        proposal=proposal,
    )

    assert created is True
    assert claimed["proposal_id"] == proposal.id
    snapshot = json.loads(repo.get_trade_proposal(proposal.id)["attribution_snapshot"])
    context = snapshot["position_hypothesis"]["opening_decision_context"]
    assert context["reasoning"] == "in-flight rationale"
    assert context["bear_case"] == "support may fail"
    assert context["key_assumptions"] == ["volume persists"]



def test_historical_or_non_entry_proposal_is_not_backfilled():
    from aletheia.storage import repositories as repo

    proposal = TradeProposal(
        id="proposal-adopted",
        source="adopted",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        price=100.0,
    )

    repo.insert_trade_proposal(proposal)

    stored = repo.get_trade_proposal(proposal.id)
    assert stored is not None
    assert stored["attribution_snapshot"] is None
    assert evaluate_hypothesis_prefix(None, completed_bars=[]) == "not_recorded"


def test_prefix_evaluation_never_uses_unprovided_future_bar():
    hypothesis = {
        "contract_version": "position_hypothesis/v1",
        "invalidation_conditions": [{"kind": "price_at_or_below", "threshold": 97.0}],
    }
    early_prefix = [{"close": 101.0}]

    assert evaluate_hypothesis_prefix(hypothesis, completed_bars=early_prefix) == "confirming"
    assert evaluate_hypothesis_prefix(hypothesis, completed_bars=[*early_prefix, {"close": 96.0}]) == "invalidated"
    assert (
        evaluate_hypothesis_prefix(
            hypothesis,
            completed_bars=early_prefix,
            signal_prefix={"future_loss": -100.0},
        )
        == "confirming"
    )


def test_unknown_input_is_not_evaluable_and_unknown_vocabulary_fails_closed():
    signal_hypothesis = {
        "contract_version": "position_hypothesis/v1",
        "invalidation_conditions": [{"kind": "signal_at_or_below", "signal": "trend", "threshold": 0.0}],
    }
    assert evaluate_hypothesis_prefix(signal_hypothesis, completed_bars=[{"close": 100.0}]) == "not_evaluable"

    with pytest.raises(ValueError, match="unsupported hypothesis invalidation kind"):
        evaluate_hypothesis_prefix(
            {
                "contract_version": "position_hypothesis/v1",
                "invalidation_conditions": [{"kind": "free_text"}],
            },
            completed_bars=[{"close": 100.0}],
        )


def test_frozen_hypothesis_defaults_to_machine_evaluable_stop_condition():
    from aletheia.storage import repositories as repo

    opportunity = _opportunity()
    proposal = TradeProposal(
        id="proposal-stop-condition",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        price=100.0,
        stop_price=95.0,
        take_profit=110.0,
        risk_reward=2.0,
        attribution_snapshot="{}",
    )
    repo.insert_trade_proposal(proposal)
    stored = repo.get_trade_proposal(proposal.id)
    hypothesis = json.loads(stored["attribution_snapshot"])["position_hypothesis"]
    assert hypothesis["invalidation_conditions"] == [
        {"kind": "price_at_or_below", "threshold": 95.0}
    ]
    assert (
        evaluate_hypothesis_prefix(
            hypothesis, completed_bars=[{"close": 94.0}]
        )
        == "invalidated"
    )


def test_caller_supplied_hypothesis_must_match_all_proposal_identity_and_geometry():
    from aletheia.storage import repositories as repo

    opportunity = _opportunity()
    proposal = TradeProposal(
        id="proposal-supplied-hypothesis",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        price=100.0,
        stop_price=95.0,
        take_profit=110.0,
        risk_reward=2.0,
        expected_horizon_hours=24.0,
        attribution_snapshot="{}",
    )
    freeze_position_hypothesis(proposal, db=repo.get_db())
    snapshot = json.loads(proposal.attribution_snapshot)
    snapshot["position_hypothesis"]["target_price"] = 111.0
    proposal.attribution_snapshot = json.dumps(snapshot)

    with pytest.raises(ValueError, match="conflicts with Proposal identity"):
        freeze_position_hypothesis(proposal, db=repo.get_db())


def test_caller_supplied_hypothesis_rejects_incorrect_quote_amount():
    from aletheia.storage import repositories as repo

    opportunity = _opportunity()
    proposal = TradeProposal(
        id="proposal-invalid-quote-risk",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        price=100.0,
        stop_price=95.0,
        attribution_snapshot="{}",
    )
    freeze_position_hypothesis(proposal, db=repo.get_db())
    snapshot = json.loads(proposal.attribution_snapshot)
    snapshot["position_hypothesis"]["initial_risk"]["quote_amount"] = 9.0
    proposal.attribution_snapshot = json.dumps(snapshot)

    with pytest.raises(ValueError, match="risk quote amount"):
        freeze_position_hypothesis(proposal, db=repo.get_db())
