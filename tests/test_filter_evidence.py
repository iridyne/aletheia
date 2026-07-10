"""Focused behavior tests for Opportunity filter evidence."""

import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest


def test_directional_decision_registers_one_shared_outcome_per_horizon():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    key = "opportunity:filter-outcome-registration"
    common = dict(
        candidate_key=key,
        candidate_kind="opportunity",
        stage="paper_ranker",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="short",
        scanner_family="momentum",
        entry_price_usd=100.0,
        entry_spread_bps=6.0,
        funding_rate_at_entry=0.0005,
    )
    record_filter_decision(
        filter_name="direction_present", decision="passed",
        reason_code="direction_available", **common,
    )
    record_filter_decision(
        filter_name="ranker_confidence_gate", decision="rejected",
        reason_code="confidence_below", **common,
    )

    rows = get_db().execute(
        "SELECT * FROM filter_candidate_outcomes WHERE outcome_candidate_key=?",
        (key,),
    ).fetchall()
    assert {row["horizon"] for row in rows} == {"15m", "1h", "4h", "24h"}
    assert {row["market_type"] for row in rows} == {"perp"}
    assert {row["direction"] for row in rows} == {"short"}


def test_filter_outcome_settlement_uses_net_economics(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome, ShadowTrade
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    now = datetime.now(timezone.utc)
    entry = now - timedelta(hours=25)
    outcome = FilterCandidateOutcome(
        outcome_candidate_key="opportunity:settle-filter-net",
        venue="okx",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        scanner_family="momentum",
        horizon="24h",
        entry_price_usd=100.0,
        entry_spread_bps=10.0,
        entry_ts=entry.isoformat(),
        due_at=(entry + timedelta(hours=24)).isoformat(),
    )
    repo.insert_filter_candidate_outcomes_batch([outcome])
    shadow = ShadowTrade(
        opportunity_id="settle-filter-net",
        batch_id="batch-net",
        symbol="BTC/USDT",
        venue="okx",
        market_type="spot",
        direction="long",
        entry_price=100.0,
        outcome_candidate_key=outcome.outcome_candidate_key,
    )
    repo.insert_shadow_trade(shadow)

    async def observations(rows, *, now):
        return {
            outcome.id: {
                "exit_price": 102.0,
                "range_high": 103.0,
                "range_low": 99.0,
                "price_source": "horizon_ohlcv",
                "price_quality_ok": True,
                "settlement_lag_sec": 30.0,
            }
        }

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=1)) == 1
    row = get_db().execute(
        "SELECT * FROM filter_candidate_outcomes WHERE id=?", (outcome.id,)
    ).fetchone()
    assert row["status"] == "settled"
    assert row["gross_return_pct"] == pytest.approx(2.0)
    assert row["net_return_pct"] == pytest.approx(1.68)
    assert row["mfe_pct"] == pytest.approx(3.0)
    assert row["mae_pct"] == pytest.approx(-1.0)
    shadow_row = get_db().execute(
        "SELECT * FROM shadow_trades WHERE id=?", (shadow.id,)
    ).fetchone()
    assert shadow_row["pnl_pct"] == pytest.approx(2.0)
    assert shadow_row["net_pnl_pct"] == pytest.approx(1.68)
    assert shadow_row["resolved_at"]


def test_filter_snapshot_drops_sensitive_keys_and_truncates_text():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    decision_id = record_filter_decision(
        candidate_key="opportunity:sanitized-filter",
        candidate_kind="opportunity",
        filter_name="ranker_confidence_gate",
        stage="paper_ranker",
        decision="rejected",
        reason_code="confidence_below",
        symbol="ETH/USDT",
        inputs={
            "confidence": "speculative",
            "api_key": "must-not-persist",
            "raw_context": "must-not-persist",
            "note": "x" * 700,
        },
    )
    row = get_db().execute(
        "SELECT input_snapshot FROM filter_decisions WHERE id=?", (decision_id,)
    ).fetchone()
    payload = json.loads(row["input_snapshot"])
    assert payload["confidence"] == "speculative"
    assert "api_key" not in payload
    assert "raw_context" not in payload
    assert len(payload["note"]) == 500


def test_filter_decision_batch_is_complete_counted_and_idempotent():
    from aletheia.analytics.filter_evidence import (
        decode_filter_decision_batch,
        record_filter_decision_batch,
    )
    from aletheia.storage.sqlite import get_db

    facts = [
        {
            "candidate_key": "asset:btc-spot",
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "decision": "passed",
            "reason_code": "ticker_available",
        },
        {
            "candidate_key": "asset:eth-perp",
            "symbol": "ETH/USDT",
            "market_type": "perp",
            "decision": "rejected",
            "reason_code": "ticker_unavailable",
        },
        {
            "candidate_key": "asset:missing",
            "symbol": "",
            "market_type": "spot",
            "decision": "not_applicable",
            "reason_code": "identity_missing",
        },
    ]
    kwargs = dict(
        run_id="scanner-run-batch",
        candidate_kind="universe_asset",
        filter_name="ticker_availability",
        stage="ticker_prefilter",
        candidate_facts=facts,
        parameters={"venue": "okx"},
    )

    first = record_filter_decision_batch(**kwargs)
    second = record_filter_decision_batch(**kwargs)
    rows = get_db().execute(
        "SELECT * FROM filter_decision_batches WHERE run_id=?",
        (kwargs["run_id"],),
    ).fetchall()

    assert first == second
    assert len(rows) == 1
    assert rows[0]["candidate_count"] == 3
    assert rows[0]["passed_count"] == 1
    assert rows[0]["rejected_count"] == 1
    assert rows[0]["not_applicable_count"] == 1
    assert rows[0]["error_count"] == 0
    assert decode_filter_decision_batch(rows[0]["candidate_facts"]) == facts


def test_filter_decision_batch_compacts_repeated_candidate_keys():
    from aletheia.analytics.filter_evidence import (
        decode_filter_decision_batch,
        record_filter_decision_batch,
    )
    from aletheia.storage.sqlite import get_db

    facts = [
        {
            "candidate_key": f"asset:{index}",
            "symbol": f"ASSET{index}/USDT",
            "market_type": "spot",
            "decision": "passed",
            "reason_code": "ticker_available",
        }
        for index in range(100)
    ]
    facts[0]["optional_value"] = None
    facts[1]["inputs"] = {"quote_volume_24h": 10_000_000.0}
    record_filter_decision_batch(
        run_id="scanner-run-compact",
        candidate_kind="universe_asset",
        filter_name="ticker_availability",
        stage="ticker_prefilter",
        candidate_facts=facts,
    )
    row = get_db().execute(
        "SELECT candidate_facts FROM filter_decision_batches WHERE run_id=?",
        ("scanner-run-compact",),
    ).fetchone()
    naive = json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    assert decode_filter_decision_batch(row["candidate_facts"]) == facts
    assert len(row["candidate_facts"]) < len(naive) * 0.7


def test_ranker_trace_stops_at_first_rejection(monkeypatch):
    from aletheia.agents.paper_trader_ranker import _evaluate_candidate
    from aletheia.config.loader import load_config

    cfg = load_config()
    candidate = _evaluate_candidate(
        {
            "id": "opp-low-confidence",
            "symbol": "BTC/USDT",
            "market": "spot",
            "asset_class": "crypto",
            "status": "open",
            "execution_eligible": True,
            "direction_bias": "long",
            "confidence": "speculative",
        },
        cfg,
        datetime.now(timezone.utc).isoformat(),
        lambda symbol: True,
        "medium",
        "paper_internal",
    )

    names = [step["filter_name"] for step in candidate["filter_trace"]]
    assert names == [
        "execution_eligibility",
        "direction_present",
        "ranker_confidence_gate",
    ]
    assert candidate["filter_trace"][-1]["decision"] == "rejected"


def test_controller_only_flags_overrestriction_after_paired_gates(monkeypatch):
    from aletheia.analytics import filter_controller

    common = {
        "filter_name": "ranker_confidence_gate",
        "filter_version": "v1-test",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "4h",
        "n_total": 25,
        "n_net": 20,
        "n": 20,
        "outcome_coverage": 0.8,
    }
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_stats",
        lambda: [
            {**common, "decision": "passed", "shrunk_avg_net": -0.10},
            {**common, "decision": "rejected", "shrunk_avg_net": 0.20},
        ],
    )

    rec = next(
        row for row in filter_controller.compute_filter_feedback()
        if row.filter_name == "ranker_confidence_gate"
    )
    assert rec.verdict == "overrestrictive_suspect"
    assert rec.suggested_direction == "review_loosen"
    assert rec.passed_n == 20
    assert rec.rejected_n == 20


def test_controller_persists_new_observational_verdict_through_legacy_schema(monkeypatch):
    from aletheia.analytics import filter_controller
    from aletheia.analytics.filter_controller import FilterRecommendation
    from aletheia.storage.sqlite import get_db

    recommendation = FilterRecommendation(
        filter_name="family_ttl",
        scanner_family="momentum",
        stage="ttl_expired",
        current_value="1800s",
        verdict="overrestrictive_suspect",
        suggested_direction="review_loosen",
        error_signal=0.2,
        n_samples=25,
        reasoning="observational only",
        direction="long",
        market_type="perp",
        horizon="4h",
        filter_version="v1-test",
        passed_n=25,
        rejected_n=25,
        passed_shrunk_avg_net=-0.1,
        rejected_shrunk_avg_net=0.1,
        outcome_coverage=0.8,
    )
    monkeypatch.setattr(
        filter_controller,
        "compute_filter_feedback",
        lambda min_samples=20: [recommendation],
    )

    result = filter_controller.run_controller_observe()
    row = get_db().execute(
        "SELECT * FROM controller_recommendations WHERE run_id=?",
        (result["run_id"],),
    ).fetchone()

    assert result["recommendations_written"] == 1
    assert row["verdict"] == "insufficient_data"
    assert row["suggested_direction"] == "n/a"
    assert row["evidence_verdict"] == "overrestrictive_suspect"
    assert row["evidence_direction"] == "review_loosen"
    assert row["filter_version"] == "v1-test"
    assert row["direction"] == "long"
    assert row["market_type"] == "perp"
    assert row["horizon"] == "4h"


def test_shadow_batch_includes_actual_selection_beyond_top_n(monkeypatch):
    from aletheia.analytics.shadow_trades import persist_shadow_batch
    from aletheia.storage import repositories as repo

    candidates = [
        {
            "opportunity_id": f"opportunity-{index}",
            "symbol": f"ASSET{index}/USDT",
            "market_type": "spot",
            "direction": "long",
            "trade_score": 100 - index,
        }
        for index in range(6)
    ]
    monkeypatch.setattr(
        "aletheia.analytics.shadow_trades._current_price",
        lambda *args, **kwargs: 100.0,
    )

    persist_shadow_batch(candidates, [candidates[5]], top_n=5)
    rows = repo.list_shadow_trades(limit=20)

    assert len(rows) == 6
    selected = next(row for row in rows if row["was_selected"])
    assert selected["opportunity_id"] == "opportunity-5"
    assert selected["rank"] == 6


def test_direction_overheat_rejection_is_added_to_operator_trace(monkeypatch):
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        submit_decision_as_proposal,
    )

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=70,
        reasoning="test",
        market_type="spot",
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision._proposal_direction_overheated",
        lambda *args, **kwargs: "long share too high",
    )

    result = asyncio.run(submit_decision_as_proposal(decision, "paper_internal"))

    assert result["reason"] == "direction_overheated"
    assert decision.filter_trace[-1]["filter_name"] == "direction_bias_brake"
    assert decision.filter_trace[-1]["decision"] == "rejected"
