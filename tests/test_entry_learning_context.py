from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from aletheia.learning.entry_context import build_entry_learning_context
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    Fill, Order, Position, TradeProposal, UniverseForwardOutcome, UniverseMember,
    UniverseSnapshot,
)
from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1


def _candidate(*, created_at: str, symbol: str = "BTC/USDT") -> UniverseMember:
    candidate_id = uuid4().hex[:12]
    snapshot = UniverseSnapshot(created_at=created_at, expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(snapshot)
    context = CandidateOpportunityContextV1(
        candidate_id=candidate_id,
        candidate_selection={
            "policy": "candidate_selection_utility_v1",
            "policy_epoch": "epoch-1",
            "source_record_hash": "a" * 64,
        },
        source={"detected_at": created_at, "source_cutoff_at": created_at, "feature_snapshot_id": "f1"},
        opportunity={
            "primary_mechanism": "reversal", "mechanism_version": "reversal/v1",
            "mechanism_source": "target_authority", "source_component_id": "mean_reversion",
            "direction": "long", "horizon": "4h", "economic_utility_pct": 1.0,
            "capability_status": "supported", "capability_gaps": (),
            "primary_evidence_refs": (), "secondary_mechanisms": (),
        },
        context={},
    )
    candidate = UniverseMember(
        id=candidate_id, snapshot_id=snapshot.id, asset_id="asset-btc", venue="bitget",
        symbol=symbol, market_type="perp", rank=1, total_score=90,
        scanner_family="mean_reversion", scanner_direction="long", entry_price_usd=100,
        expires_at="2099-01-01T00:00:00+00:00",
        candidate_selection_policy="candidate_selection_utility_v1",
        candidate_selection_policy_epoch="epoch-1", candidate_selection_utility_pct=1.0,
        opportunity_mechanism="reversal", candidate_opportunity_context_version=context.version,
        candidate_opportunity_context_json=context.model_dump_json(),
        candidate_opportunity_context_hash=context.context_hash,
    )
    repo.insert_candidate(candidate)
    return candidate


def _generation(*, created_at: str, symbol: str = "BTC/USDT") -> dict:
    candidate = _candidate(created_at=created_at, symbol=symbol)
    generation, _ = repo.ensure_candidate_planning_generation(
        candidate.id, now=datetime.fromisoformat(created_at)
    )
    planner_input = json.dumps({
        "inference_identity": {
            "caller": "agent_shell.entry_planner",
            "prompt_version": "v2",
            "model_provider": "test",
        },
    }, sort_keys=True, separators=(",", ":"))
    repo.execute_write(
        "UPDATE candidate_planning_generations SET producer_commit='commit-a',"
        "behavior_fingerprint='behavior-a',planner_input_json=?,planner_input_hash=? "
        "WHERE id=?",
        (
            planner_input,
            hashlib.sha256(planner_input.encode()).hexdigest(),
            generation["id"],
        ),
    )
    from aletheia.economics.context_artifacts import record_context_artifacts
    from aletheia.economics.schema import ensure_economics_schema

    call_id = f"inference-{generation['id']}"
    db = ensure_economics_schema()
    db.execute(
        "INSERT INTO llm_inference_calls ("
        "logical_call_id,schema_version,caller,prompt_template_id,prompt_version,"
        "prompt_static_sha256,rendered_prompt_sha256,context_adapter_id,tool_policy_id,"
        "allowed_tools_sha256,output_schema_id,output_schema_sha256,requested_provider,"
        "temperature,max_tokens,allow_model_fallback,status,parse_valid,schema_valid,"
        "attempt_count,retry_count,fallback_used,final_provider,final_model,started_at,"
        "finished_at,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
        "?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            call_id, "llm-inference/v1", "agent_shell.entry_planner", "entry-planner",
            "v2", "a" * 64, "b" * 64, "none", "none", "c" * 64,
            "EntryHypothesisV2", "d" * 64, "test", 0.0, 768, 0, "success",
            1, 1, 1, 0, 0, "test", "test-model", created_at, created_at, "{}",
            created_at,
        ),
    )
    db.execute(
        "INSERT INTO runtime_cost_events (id,event_type,call_id,provider,model_or_tool,"
        "attempt_index,status,started_at,finished_at,usage_json,estimated_cost_usd,"
        "currency,cost_status,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"cost-{generation['id']}", "llm", call_id, "test", "test-model", 0,
            "success", created_at, created_at, "{}", 0.01, "USD", "known", "{}",
            created_at,
        ),
    )
    db.commit()
    record_context_artifacts(
        run_id=None, caller="agent_shell.entry_planner", logical_call_id=call_id,
        symbol=symbol, observed_at=created_at,
        artifacts=[{
            "kind": "environment",
            "stable_id": f"planning-generation:{generation['id']}",
            "source": "candidate_planning_generations",
            "freshness_status": "fresh",
        }],
    )
    return repo.get_candidate_planning_generation(generation["id"])


def _context(as_of_at: str):
    return build_entry_learning_context(
        as_of_at=as_of_at, mechanism="reversal", direction="long", market_type="perp",
        policy="candidate_selection_utility_v1", policy_epoch="epoch-1",
    )


def _insert_plan(generation: dict, *, plan_id: str, status: str, mode: str) -> None:
    plan_json = json.dumps({
        "entry_profile_id": "grounded-anchor-resting-maker",
        "entry_profile_version": "v1",
    }, sort_keys=True, separators=(",", ":"))
    repo._insert("entry_plans", {
        "id": plan_id, "planning_generation_id": generation["id"],
        "candidate_id": generation["candidate_id"], "planning_key": generation["planning_key"],
        "version": "entry-plan/v2", "venue": "bitget", "symbol": "BTC/USDT",
        "market_type": "perp", "direction": "long", "execution_mode": mode,
        "plan_json": plan_json, "plan_hash": hashlib.sha256(plan_json.encode()).hexdigest(),
        "status": status, "created_at": "2026-09-01T00:01:00+00:00",
        "updated_at": "2026-09-01T00:02:00+00:00",
        "terminal_at": (
            "2026-09-01T00:02:00+00:00"
            if status in {"expired", "invalidated", "cancelled", "failed"} else None
        ),
    })
    repo.execute_write(
        "UPDATE candidate_planning_generations SET status='planned',result_kind='entry_plan',"
        "entry_plan_id=? WHERE id=?", (plan_id, generation["id"]),
    )


def _insert_forward_outcome(
    generation: dict, *, outcome_id: str, settled_at: str,
    entry_price: float = 100.0, exit_price: float = 110.0,
) -> None:
    candidate = repo.get_universe_member(generation["candidate_id"])
    repo.insert_forward_outcome(UniverseForwardOutcome(
        id=outcome_id, outcome_candidate_key=f"member:{generation['candidate_id']}",
        member_id=generation["candidate_id"], snapshot_id=candidate["snapshot_id"],
        symbol="BTC/USDT", venue="bitget", market_type="perp",
        scanner_family="mean_reversion", direction="long", horizon="4h",
        entry_price_usd=entry_price, entry_ts="2026-09-01T00:00:00+00:00",
        due_at="2026-09-01T04:00:00+00:00", settled_at=settled_at,
        exit_price_usd=exit_price, directional_return_pct=(exit_price-entry_price),
        estimated_fee_pct=0.4, estimated_spread_slippage_pct=0.6,
        estimated_funding_pct=0.0, net_return_pct=(exit_price-entry_price)-1.0,
        min_tradable_edge_pct=0.2, mfe_pct=12.0, mae_pct=-3.0,
        settlement_price_source="horizon_ohlcv", settlement_lag_sec=0.0,
        cost_model_version="test-cost/v1", status="settled",
    ))


def test_entry_learning_uses_full_no_plan_denominator_and_excludes_future_units():
    before = _generation(created_at="2026-09-01T00:00:00+00:00")
    repo.execute_write(
        "UPDATE candidate_planning_generations SET status='no_plan',result_kind='no_plan',"
        "completed_at='2026-09-01T00:01:00+00:00' WHERE id=?", (before["id"],),
    )
    _generation(created_at="2026-09-04T00:00:00+00:00", symbol="ETH/USDT")

    context = _context("2026-09-03T00:00:00+00:00")

    assert context.status == "observational"
    assert context.support == {
        "independent_n": 1, "distinct_symbols": 1, "distinct_dates": 1,
        "complete_outcome_coverage": 1.0,
        "evidence_ref_total": 3, "evidence_refs_truncated": False,
    }
    assert context.metrics["no_plan_rate"] == 1.0
    assert context.metrics["intent_to_treat_net_utility_pct"] == 0.0
    assert context.metrics["model_and_data_cost"] == 0.01


def test_entry_learning_excludes_position_outcome_settled_after_planning_cutoff():
    generation = _generation(created_at="2026-09-01T00:00:00+00:00")
    plan_id = "plan-late"
    _insert_plan(generation, plan_id=plan_id, status="filled", mode="RESTING_MAKER")
    order = Order(
        id="order-late", symbol="BTC/USDT", market_type="perp", direction="long",
        order_type="limit", quantity=1, price=100, execution_mode="paper_internal",
        entry_plan_id=plan_id, status="filled", created_at="2026-09-01T00:02:00+00:00",
        filled_at="2026-09-01T00:03:00+00:00",
    )
    repo._insert("orders", order.model_dump())
    fill = Fill(
        id="fill-late", order_id=order.id, fill_price=100, fill_quantity=1,
        filled_at="2026-09-01T00:03:00+00:00",
    )
    repo._insert("fills", fill.model_dump())
    position = Position(
        id="position-late", symbol="BTC/USDT", market_type="perp", direction="long",
        quantity=1, entry_price=100, realized_pnl=10, execution_mode="paper_internal",
        order_id=order.id, status="closed", outcome_eligible=True,
        opened_at="2026-09-01T00:03:00+00:00", closed_at="2026-09-04T00:00:00+00:00",
    )
    repo._insert("positions", position.model_dump())
    _insert_forward_outcome(
        generation, outcome_id="outcome-late",
        settled_at="2026-09-04T00:00:00+00:00",
    )

    context = _context("2026-09-03T00:00:00+00:00")

    assert context.status == "unavailable"
    assert "entry_learning_fixed_horizon_outcome_unavailable" in context.blocking_reasons
    assert "position:position-late" not in context.evidence_refs
    assert context.metrics["intent_to_treat_net_utility_pct"] is None


def test_entry_learning_uses_fixed_4h_outcome_reanchored_to_actual_fill():
    generation = _generation(created_at="2026-09-01T00:00:00+00:00")
    _insert_plan(generation, plan_id="plan-open", status="filled", mode="RESTING_MAKER")
    order = Order(
        id="order-open", symbol="BTC/USDT", market_type="perp", direction="long",
        order_type="limit", quantity=1, price=105, execution_mode="paper_internal",
        entry_plan_id="plan-open", status="filled", created_at="2026-09-01T00:02:00+00:00",
        filled_at="2026-09-01T00:03:00+00:00",
    )
    repo._insert("orders", order.model_dump())
    repo._insert("fills", Fill(
        id="fill-open", order_id=order.id, fill_price=105, fill_quantity=1,
        filled_at="2026-09-01T00:03:00+00:00",
    ).model_dump())
    repo._insert("positions", Position(
        id="position-open", symbol="BTC/USDT", market_type="perp", direction="long",
        quantity=1, entry_price=105, realized_pnl=0, execution_mode="paper_internal",
        order_id=order.id, status="open", outcome_eligible=True,
        opened_at="2026-09-01T00:03:00+00:00",
    ).model_dump())
    _insert_forward_outcome(
        generation, outcome_id="outcome-open",
        settled_at="2026-09-01T04:01:00+00:00",
    )

    context = _context("2026-09-03T00:00:00+00:00")

    assert context.status == "observational"
    assert context.metrics["intent_to_treat_net_utility_pct"] == pytest.approx(
        (110 - 105) / 105 * 100 - 1.0
    )
    assert context.metrics["mfe_pct"] == pytest.approx((112 - 105) / 105 * 100)
    assert context.metrics["mae_p95_pct"] == pytest.approx((105 - 97) / 105 * 100)
    assert "universe-forward-outcome:outcome-open" in context.evidence_refs


def test_entry_learning_triggered_proposal_failure_is_not_counted_as_no_trigger():
    generation = _generation(created_at="2026-09-01T00:00:00+00:00")
    _insert_plan(generation, plan_id="plan-triggered", status="failed", mode="TRIGGERED")
    repo._insert("trade_proposals", TradeProposal(
        id="proposal-triggered", symbol="BTC/USDT", market_type="perp", direction="long",
        entry_plan_id="plan-triggered", status="rejected",
        created_at="2026-09-01T00:01:30+00:00",
    ).model_dump())

    context = _context("2026-09-03T00:00:00+00:00")

    assert context.status == "observational"
    assert context.metrics["no_trigger_rate"] == 0.0
    assert context.metrics["no_fill_rate"] == 1.0
    assert "trade-proposal:proposal-triggered" in context.evidence_refs


def test_entry_learning_bounds_large_evidence_cohorts_without_losing_denominator():
    for index in range(22):
        generation = _generation(
            created_at=f"2026-09-01T00:{index:02d}:00+00:00",
            symbol=f"ASSET{index}/USDT",
        )
        repo.execute_write(
            "UPDATE candidate_planning_generations SET status='no_plan',result_kind='no_plan',"
            "completed_at=? WHERE id=?",
            (f"2026-09-01T00:{index:02d}:30+00:00", generation["id"]),
        )

    context = _context("2026-09-03T00:00:00+00:00")

    assert context.status == "observational"
    assert context.support["independent_n"] == 22
    assert context.support["evidence_ref_total"] == 66
    assert context.support["evidence_refs_truncated"] is True
    assert len(context.evidence_refs) == 64


def test_entry_learning_does_not_read_a_no_plan_completed_after_cutoff():
    generation = _generation(created_at="2026-09-01T00:00:00+00:00")
    repo.execute_write(
        "UPDATE candidate_planning_generations SET status='no_plan',result_kind='no_plan',"
        "completed_at='2026-09-04T00:00:00+00:00' WHERE id=?",
        (generation["id"],),
    )

    context = _context("2026-09-03T00:00:00+00:00")

    assert context.status == "unavailable"
    assert "entry_learning_planning_outcome_after_cutoff" in context.blocking_reasons
    assert context.metrics["no_plan_rate"] == 0.0


def test_entry_learning_fails_closed_when_exact_inference_cost_is_missing():
    generation = _generation(created_at="2026-09-01T00:00:00+00:00")
    repo.execute_write(
        "UPDATE candidate_planning_generations SET status='no_plan',result_kind='no_plan',"
        "completed_at='2026-09-01T00:01:00+00:00' WHERE id=?", (generation["id"],),
    )
    repo.execute_write(
        "DELETE FROM runtime_cost_events WHERE call_id=?",
        (f"inference-{generation['id']}",),
    )

    context = _context("2026-09-03T00:00:00+00:00")

    assert context.status == "unavailable"
    assert "entry_learning_model_data_cost_unavailable" in context.blocking_reasons
    assert context.metrics["model_and_data_cost"] is None


def test_entry_learning_treatment_freezes_epoch_and_changes_only_context_presence():
    from types import SimpleNamespace

    from aletheia.learning.entry_context import (
        ENTRY_LEARNING_INTERFERENCE_KEY,
        EntryLearningTreatmentAdapter,
    )

    adapter = EntryLearningTreatmentAdapter()
    context = {
        "planning_generation_id": "generation-1",
        "mechanism": "reversal",
        "direction": "long",
        "market_type": "perp",
        "candidate_selection_policy": "candidate_selection_utility_v1",
        "candidate_selection_policy_epoch": "epoch-1",
    }
    manifest = SimpleNamespace(
        eligible_population={key: value for key, value in context.items() if key != "planning_generation_id"},
        interference_key=ENTRY_LEARNING_INTERFERENCE_KEY,
        behavior_policy_epoch_ref="epoch-1",
        deterministic_baseline={"include_learning_context": False},
        control_payload={"include_learning_context": False},
        treatment_payload={"include_learning_context": True},
    )

    assert adapter.eligible(manifest, context) is True
    assert adapter.apply_payload(
        manifest.deterministic_baseline, manifest.treatment_payload, context,
    ) == {"include_learning_context": True}
    manifest.behavior_policy_epoch_ref = "epoch-2"
    assert adapter.eligible(manifest, context) is False
