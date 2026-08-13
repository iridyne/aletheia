"""B2 candidate-conditioned evidence contract for System Operator inputs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def _setup(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)


def _insert_b2_candidate(*, index: int, epoch: str, bucket: str, net: float) -> None:
    from aletheia.storage.models import FilterCandidateOutcome, FilterDecision
    from aletheia.storage.repositories import (
        insert_filter_candidate_outcomes_batch,
        insert_filter_decision,
    )

    now = datetime.now(timezone.utc)
    opportunity_id = f"b2-opp-{index}"
    decision_key = f"pi_decision:b2-{index}"
    common = {
        "opportunity_id": opportunity_id,
        "venue": "binance",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "direction": "long",
        "scanner_family": "momentum",
    }
    # This is the durable proof that the candidate passed Ranker and was placed
    # in the PI queue; B2 must not learn from merely observed candidates.
    insert_filter_decision(FilterDecision(
        id=f"enqueue-{index}", dedup_key=f"enqueue-{index}",
        candidate_key=f"opportunity:{opportunity_id}", candidate_kind="opportunity",
        filter_name="signal_enqueue", stage="paper_trader_enqueue",
        decision="passed", reason_code="signal_enqueued", filter_version="test-ranker-v1",
        outcome_candidate_key=f"opportunity:{opportunity_id}", evaluated_at=now.isoformat(),
        **common,
    ))
    insert_filter_decision(FilterDecision(
        id=f"assessment-{index}", dedup_key=f"assessment-{index}",
        candidate_key=decision_key, candidate_kind="pi_decision",
        filter_name="operator_action_assessment", stage="system_operator",
        decision="passed", reason_code="operator_model_trade", filter_version="b2-test-v1",
        outcome_candidate_key=decision_key, evaluated_at=now.isoformat(),
        input_snapshot=json.dumps({
            "b2_evidence": {
                "version": "b2", "policy_epoch_id": epoch,
                "selection_tier": "ranker_enqueued", "score_bucket": bucket,
            },
        }),
        **common,
    ))
    insert_filter_candidate_outcomes_batch([FilterCandidateOutcome(
        id=f"outcome-{index}", outcome_candidate_key=decision_key,
        venue="binance", symbol="BTC/USDT", market_type="perp", direction="long",
        scanner_family="momentum", horizon="24h", entry_price_usd=100.0,
        entry_ts=(now - timedelta(hours=25)).isoformat(),
        due_at=(now - timedelta(hours=1)).isoformat(), settled_at=now.isoformat(),
        net_return_pct=net, min_tradable_edge_pct=0.05, cost_model_version="cost-v1",
        status="settled",
    )])


def test_b2_evidence_is_neutral_when_current_epoch_has_no_matured_cohort(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.analytics.pi_evidence import build_b2_evidence_summary

    summary = build_b2_evidence_summary({
        "opportunity_id": "current", "scanner_family": "momentum",
        "direction": "long", "market_type": "usdt_futures", "trade_score": 51.0,
    }, policy_epoch_id="b2-current")

    assert summary["status"] == "insufficient_evidence"
    assert summary["n_total"] == 0
    assert summary["n_net"] == 0
    assert summary["directional_verdict"] is None


def test_b2_evidence_excludes_other_epochs_and_score_buckets(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.analytics.pi_evidence import build_b2_evidence_summary

    for index in range(20):
        _insert_b2_candidate(index=index, epoch="b2-current", bucket="high_score", net=1.0)
    _insert_b2_candidate(index=90, epoch="b2-older", bucket="high_score", net=-50.0)
    _insert_b2_candidate(index=91, epoch="b2-current", bucket="medium_score", net=-50.0)

    summary = build_b2_evidence_summary({
        "opportunity_id": "current", "scanner_family": "momentum",
        "direction": "long", "market_type": "usdt_futures", "trade_score": 51.0,
    }, policy_epoch_id="b2-current")

    assert summary["selection_tier"] == "ranker_enqueued"
    assert summary["score_bucket"] == "high_score"
    assert summary["n_total"] == 20
    assert summary["n_net"] == 20
    assert summary["outcome_coverage"] == 1.0
    assert summary["status"] == "qualified_positive"
    assert summary["directional_verdict"] == "positive"
    assert summary["avg_net_pct"] == 1.0


def test_b2_summary_is_identical_for_assessment_bull_and_bear(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_decision import (
        _build_assessment_prompt,
        _build_bear_prompt,
        _build_bull_prompt,
    )
    from aletheia.analytics.pi_evidence import render_b2_evidence_summary

    summary = {
        "version": "b2", "status": "insufficient_evidence",
        "policy_epoch_id": "b2-current", "selection_tier": "ranker_enqueued",
        "score_bucket": "high_score", "n_total": 0, "n_net": 0,
        "outcome_coverage": 0.0, "directional_verdict": None,
    }
    block = render_b2_evidence_summary(summary)
    ctx = {
        "layer1": {
            "features": [{"symbol": "BTC/USDT", "rsi_14": 55, "atr_pct": 2.0}],
            "opportunities": [{
                "symbol": "BTC/USDT", "market": "usdt_futures", "direction_bias": "long",
                "confidence_score": 55, "scanner_family": "momentum",
                "family_edge": {"n": 999, "win_rate": 1.0, "avg_return": -99.0},
                "thesis": "legacy aggregate must not render",
            }],
        },
        "layer2": {"pending_signals": [{
            "symbol": "BTC/USDT", "direction": "long", "market_type": "usdt_futures",
            "opportunity_id": "current", "signal_source": "market_observer",
            "b2_evidence": summary,
        }]},
        "layer3": {},
    }

    assessment = _build_assessment_prompt(ctx)
    bull = _build_bull_prompt("BTC/USDT", "long", ctx)
    bear = _build_bear_prompt("BTC/USDT", "long", ctx)

    assert block in assessment
    assert block in bull
    assert block in bear
    assert "999T" not in assessment
    assert "-99.00%avg_net" not in assessment


def test_negative_b2_outcome_opposes_short_candidate_without_implying_bearish_price():
    from aletheia.analytics.pi_evidence import render_b2_evidence_summary

    block = render_b2_evidence_summary({
        "status": "qualified_negative", "directional_verdict": "negative",
        "n_total": 21, "n_net": 20, "outcome_coverage": 0.9524,
        "avg_net_pct": -2.958808, "shrunk_avg_net_pct": -1.479404,
    })

    assert "candidate outcomes were unprofitable and oppose this candidate" in block
    assert "negative means losing net outcome, not bearish price direction" in block


def test_assessment_prompt_does_not_treat_negative_funding_as_short_support():
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt

    prompt = _build_assessment_prompt({"layer1": {}, "layer2": {}, "layer3": {}})

    assert "FR<-0.01%: shorts pay longs; crowded shorts" in prompt
    assert "supports a contrarian long, opposes a short" in prompt
