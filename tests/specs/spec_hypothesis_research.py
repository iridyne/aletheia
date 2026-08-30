"""Hypothesis Research Evidence v1 and learned-guard research gate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "LE-B32", "learning_evolution", "§9.8 hypothesis research evidence", "L2b",
    "Explicit research-ready Learning hypotheses require exact source and outcome "
    "identities, one scoped explanation, bounded alternatives, recorded or explicitly "
    "missing T0 context, and a future falsification condition.",
    status="implemented",
))
def test_research_evidence_contract_rejects_incomplete_or_unbounded_payloads():
    from aletheia.learning.hypotheses import (
        HypothesisResearchEvidenceV1,
        nominate_learning_hypothesis,
    )
    from tests.specs.spec_learning_attribution import _ready_research

    ready = _ready_research("case-contract")
    assert HypothesisResearchEvidenceV1.model_validate(ready).research_status == "ready"

    no_alternative = dict(ready, competing_explanations=[])
    with pytest.raises(ValidationError, match="competing explanations"):
        HypothesisResearchEvidenceV1.model_validate(no_alternative)

    too_many = dict(ready, competing_explanations=["a", "b", "c", "d"])
    with pytest.raises(ValidationError):
        HypothesisResearchEvidenceV1.model_validate(too_many)

    no_outcome = json.loads(json.dumps(ready))
    no_outcome["observation"]["outcome_identities"] = []
    with pytest.raises(ValidationError, match="exact outcome identities"):
        HypothesisResearchEvidenceV1.model_validate(no_outcome)

    rejected = json.loads(json.dumps(ready))
    rejected["research_status"] = "rejected"
    rejected["red_team_summary"]["decision"] = "rejected"
    with pytest.raises(ValueError, match="not ready for nomination"):
        nominate_learning_hypothesis(
            path_id="ranker_learned_guard",
            scope={"guard_id": "research-rejected"},
            action={"type": "candidate_guard"},
            source_type="guard_candidate",
            source_artifact_ids=["case-contract"],
            source_cohort="research-contract:v1",
            consumer="paper_trader_ranker",
            target_horizon="4h",
            evidence_summary=rejected,
            require_research_evidence=True,
        )


@spec_claim(SpecClaim(
    "LE-B33", "learning_evolution", "§9.8 missing-context gate", "L2b",
    "Unavailable exact T0 evidence remains explicit and returns insufficient_context "
    "without calling the red-team reviewer or persisting a guard candidate, hypothesis, "
    "experiment, exposure, verdict, or consumer value.",
    status="implemented",
))
async def test_missing_t0_context_fails_closed_without_research_or_authority(monkeypatch):
    from aletheia.learning import guard_routing, hypothesis_research
    from aletheia.learning.hypotheses import HypothesisCounterfactualCheckV1
    from aletheia.learning.ledger import list_records
    from tests.specs.spec_learning_attribution import _ready_research

    monkeypatch.setattr(
        hypothesis_research,
        "_assemble_exact_context",
        lambda _ids, _horizon: (
            {}, ["exact_recorded_t0_context_unavailable"], [], [],
        ),
    )

    async def review_must_not_run(**_kwargs):
        raise AssertionError("red-team review must not run without exact T0 context")

    monkeypatch.setattr(hypothesis_research, "llm_structured", review_must_not_run)
    research = await hypothesis_research.research_learning_hypothesis(
        candidate_explanation="A broad momentum block may improve 4h net utility.",
        source_artifact_ids=["case-missing"],
        evidence={"case_count": 1, "outcome_counts": {"loss": 1}},
        proposed_scope={"symbol": ["BTC/USDT"], "scanner_family": ["momentum"]},
        target_horizon="4h",
    )
    assert research.research_status == "insufficient_context"
    assert research.relevant_context.recorded_at_t0 == {}
    assert "exact_recorded_t0_context_unavailable" in research.relevant_context.missing

    monkeypatch.setattr(
        guard_routing,
        "create_guard_candidate",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("insufficient research must not create a guard candidate")
        ),
    )
    routed = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={
            "factor_keys": ["momentum:BTC/USDT:long:medium_confidence:not_late"],
            "scanner_families": ["momentum"],
        },
        source_id="case-missing",
        research_evidence=research,
    )
    assert routed["reason"] == "research_insufficient_context"

    rejected = _ready_research("case-rejected")
    rejected["research_status"] = "rejected"
    rejected["red_team_summary"]["decision"] = "rejected"
    rejected_route = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={
            "factor_keys": ["momentum:BTC/USDT:long:medium_confidence:not_late"],
            "scanner_families": ["momentum"],
        },
        source_id="case-rejected",
        research_evidence=rejected,
    )
    assert rejected_route["reason"] == "research_rejected"
    for table in (
        "learning_hypotheses", "learning_experiments", "learning_exposures",
        "learning_contribution_verdicts",
    ):
        assert list_records(table) == []

    cases = [
        {
            "source_artifact_id": f"case-{index}",
            "position_id": f"position-{index}",
            "opportunity_id": "",
            "realized_pnl_pct": -1.0,
            "opened_at": "2026-08-16T00:00:00+00:00",
        }
        for index in (1, 2)
    ]
    mode = "partial"

    def assembled(_ids, _horizon):
        regime_values = [{"source_artifact_id": "case-1", "value": "risk_off"}]
        if mode == "full":
            regime_values.append({"source_artifact_id": "case-2", "value": "risk_off"})
        return (
            {
                "symbol": [
                    {"source_artifact_id": "case-1", "value": "BTC/USDT"},
                    {"source_artifact_id": "case-2", "value": "BTC/USDT"},
                ],
                "regime": regime_values,
            },
            (["t0_context_missing:regime:case-2"] if mode == "partial" else []),
            ["position:position-1", "position:position-2"],
            cases,
        )

    async def narrowed_review(*, user, **_kwargs):
        payload = json.loads(user)
        if mode == "partial":
            assert "regime" not in payload["allowed_material_context_keys"]
        return {
            "competing_explanations": ["The recorded regime may explain the pattern."],
            "material_confounders_or_missing": [],
            "smallest_falsification_test": "Compare conditional treatment with control.",
            "decision": "narrowed",
            "narrowed_candidate_explanation": (
                "A broad momentum block may improve 4h net utility."
                if mode == "full"
                else "Under risk_off, the momentum block may improve 4h net utility."
            ),
            "material_context_keys": ["regime"],
            "reasoning": "The factor-only explanation is incomplete.",
        }, None

    monkeypatch.setattr(hypothesis_research, "_assemble_exact_context", assembled)
    monkeypatch.setattr(
        hypothesis_research,
        "_bounded_counterfactual_checks",
        lambda *_args: [HypothesisCounterfactualCheckV1(
            check_type="exact_fixture",
            source_identity="filter_candidate_outcome:selected",
            comparison_identity="filter_candidate_outcome:control",
            evidence={"fixture": True},
        )],
    )
    monkeypatch.setattr(hypothesis_research, "llm_structured", narrowed_review)
    partial = await hypothesis_research.research_learning_hypothesis(
        candidate_explanation="A broad momentum block may improve 4h net utility.",
        source_artifact_ids=["case-1", "case-2"],
        evidence={"case_count": 2},
        proposed_scope={"symbol": ["BTC/USDT"], "scanner_family": ["momentum"]},
        target_horizon="4h",
    )
    assert partial.research_status == "insufficient_context"
    assert "invalid_material_context_key:regime" in partial.missing_evidence

    mode = "full"
    unchanged = await hypothesis_research.research_learning_hypothesis(
        candidate_explanation="A broad momentum block may improve 4h net utility.",
        source_artifact_ids=["case-1", "case-2"],
        evidence={"case_count": 2},
        proposed_scope={"symbol": ["BTC/USDT"], "scanner_family": ["momentum"]},
        target_horizon="4h",
    )
    assert unchanged.research_status == "insufficient_context"
    assert "narrowed_candidate_explanation_unchanged" in unchanged.missing_evidence

    bounded = hypothesis_research._insufficient_evidence(
        observation=research.observation,
        candidate_explanation=research.candidate_explanation,
        recorded={},
        missing=[f"missing:{index}" for index in range(30)],
        reason="Bounded failure evidence.",
    )
    assert len(bounded.relevant_context.missing) == 20
    assert bounded.relevant_context.missing[-1] == "additional_missing_context_entries:11"


@spec_claim(SpecClaim(
    "LE-B35", "learning_evolution", "§9.8 source/T0 identity", "L2b",
    "Path-aware research binds every declared source to exactly one canonical T0 envelope.",
    status="implemented",
))
def test_path_aware_research_rejects_equal_count_unrelated_t0_sources():
    from aletheia.learning.hypothesis_research import _assemble_t0_envelope_context
    from tests.specs.spec_learning_t0_foundation import _ranker_envelope

    envelope = _ranker_envelope()
    recorded, missing, outcomes, cases = _assemble_t0_envelope_context(
        path_id="ranker_case_score",
        consumer="paper ranker",
        source_artifact_ids=["unrelated-source"],
        envelopes=[envelope],
    )
    assert recorded == {}
    assert outcomes == []
    assert cases == []
    assert "source_t0_identity_mismatch:decision-1" in missing
    assert "source_t0_identity_mismatch:unrelated-source" in missing

    recorded, missing, outcomes, cases = _assemble_t0_envelope_context(
        path_id="ranker_case_score",
        consumer="paper ranker",
        source_artifact_ids=["decision-1"],
        envelopes=[envelope],
    )
    assert recorded
    assert not any(reason.startswith("source_t0_identity_mismatch:") for reason in missing)
    assert outcomes == ["position-1"]
    assert cases[0]["source_artifact_id"] == "decision-1"

    recorded, missing, outcomes, cases = _assemble_t0_envelope_context(
        path_id="ranker_case_score",
        consumer="paper ranker",
        source_artifact_ids=["decision-1"],
        envelopes=[envelope, envelope.model_copy()],
    )
    assert recorded == {}
    assert outcomes == []
    assert cases == []
    assert "source_t0_identity_mismatch:decision-1" in missing


@spec_claim(SpecClaim(
    "LE-B34", "learning_evolution", "§9.8 path-aware causal research", "L2c",
    "A deterministic closed-trade replay rooted in Knowledge Markdown remains "
    "insufficient until a path-specific canonical T0 envelope exists; it creates "
    "no guard candidate, hypothesis, experiment, exposure, verdict, or authority.",
    status="implemented",
))
async def test_legacy_trade_case_replay_requires_canonical_t0_envelope(monkeypatch):
    from aletheia.knowledge.manager import create_trade_case_study
    from aletheia.learning import guard_routing, hypothesis_research
    from aletheia.learning.ledger import list_records
    from aletheia.learning.registry import get_learning_path
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import PiDecision, Position, ShadowTrade
    from tests.shadow_outcome_fixture import insert_canonical_outcome_for_shadow
    from tests.test_outcome_closure import _insert_entry_chain

    case_ids = []
    opportunity_ids = []
    for index in range(4):
        position_id = f"causal-loss-position-{index}"
        opportunity_id, proposal_id, order_id = _insert_entry_chain(
            position_id=position_id,
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=10.0,
            price=100.0,
            execution_mode="paper_internal",
            scanner_family="momentum",
            opened_at="2026-08-16T00:00:00+00:00",
        )
        opportunity_ids.append(opportunity_id)
        repo.update_opportunity(
            opportunity_id,
            regime_tag="risk_off",
            funding_rate=0.001,
            open_interest=1_000_000.0,
            oi_delta_1h=-12.0,
            key_observations=json.dumps({"volatility": "high"}),
        )
        repo.update_trade_proposal(
            proposal_id,
            agent_rationale="Oversold momentum suggested a mean-reversion long.",
        )
        repo.insert_pi_decision(PiDecision(
            id=f"causal-loss-decision-{index}",
            symbol="BTC/USDT",
            direction="long",
            market_type="usdt_futures",
            action="open_position",
            reasoning="RSI was oversold while the recorded regime was risk_off.",
            bear_case="OI contraction could dominate the oversold signal.",
            key_assumptions=json.dumps(["Mean reversion persists for 4h"]),
            evidence_manifest_json=json.dumps({"opportunity_id": opportunity_id}),
            proposal_id=proposal_id,
            opportunity_id=opportunity_id,
            signal_source="market_observer",
            created_at="2026-08-16T00:00:00+00:00",
        ))
        repo.insert_position(Position(
            id=position_id,
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=10.0,
            entry_price=100.0,
            current_price=98.0,
            realized_pnl=-20.0,
            execution_mode="paper_internal",
            status="closed",
            source="native",
            opened_at="2026-08-16T00:00:00+00:00",
            closed_at="2026-08-16T04:00:00+00:00",
            close_reason="stop_loss",
            close_category="strategy",
            entry_price_source="local_fill",
            entry_fill_verified=True,
            exit_price_source="local_fill",
            exit_fill_verified=True,
            outcome_eligible=True,
            outcome_exclusion_reason="eligible",
            outcome_semantics_version="oi-v1",
            opportunity_id=opportunity_id,
            proposal_id=proposal_id,
            order_id=order_id,
            regime_tag="risk_off",
            entry_market_summary="Recorded risk_off regime with high volatility.",
            entry_fee=0.0,
            exit_fee=0.0,
            funding_cost=0.0,
            funding_source="local_simulation",
        ))
        case = create_trade_case_study(
            f"Deterministic oversold loss {index}",
            (
                f"Exact closed position {index} lost 2% after an oversold momentum long. "
                "The decision-time record also contained a risk-off regime and a 12% "
                "one-hour contraction in open interest; later facts are excluded."
            ),
            metadata={
                "position_id": position_id,
                "outcome": "loss",
                "realized_pnl_pct": -2.0,
                "factor_key": "momentum:BTC/USDT:long:medium_confidence:not_late",
                "scanner_family": "momentum",
            },
        )
        assert case is not None
        case_ids.append(case.id)

    opportunity_id = opportunity_ids[0]

    batch_id = "causal-replay-batch"
    repo.insert_shadow_trade(ShadowTrade(
        id="causal-shadow-selected",
        batch_id=batch_id,
        opportunity_id=opportunity_id,
        symbol="BTC/USDT",
        venue="bitget",
        market_type="usdt_futures",
        direction="long",
        scanner_family="momentum",
        rank=1,
        entry_price=100.0,
        outcome_candidate_key=f"opportunity:{opportunity_id}",
        was_selected=True,
        created_at="2026-08-16T00:00:00+00:00",
    ))
    insert_canonical_outcome_for_shadow(
        "causal-shadow-selected", horizon="4h", net_return_pct=-2.0,
    )
    for rank in range(2, 6):
        repo.insert_shadow_trade(ShadowTrade(
            id=f"causal-shadow-alt-{rank}",
            batch_id=batch_id,
            opportunity_id=f"alternative-{rank}",
            symbol=f"ALT{rank}/USDT",
            venue="bitget",
            market_type="usdt_futures",
            direction="long",
            scanner_family="momentum",
            rank=rank,
            entry_price=100.0,
            outcome_candidate_key=f"opportunity:alternative-{rank}",
            was_selected=False,
            created_at="2026-08-16T00:00:00+00:00",
        ))
        insert_canonical_outcome_for_shadow(
            f"causal-shadow-alt-{rank}", horizon="4h", net_return_pct=1.0,
        )

    review_calls = []

    async def narrow_review(*, user, **_kwargs):
        payload = json.loads(user)
        review_calls.append(payload)
        assert len(payload["counterfactual_checks"]) == 3
        assert payload["recorded_t0_context"]["regime"][0]["value"] == "risk_off"
        assert (
            payload["recorded_t0_context"]["funding_oi"][0]["value"]["oi_delta_1h"]
            == -12.0
        )
        assert "catalyst_news" not in payload["recorded_t0_context"]
        assert any(
            item.startswith("t0_context_missing:catalyst_news:")
            for item in payload["explicitly_missing_context"]
        )
        return {
            "competing_explanations": [
                "The recorded risk-off regime and OI contraction explain the losses."
            ],
            "material_confounders_or_missing": [],
            "smallest_falsification_test": (
                "Prospectively compare the exact conditional guard with control for 4h."
            ),
            "decision": "narrowed",
            "narrowed_candidate_explanation": (
                "Under recorded risk_off and OI contraction context, the scoped momentum "
                "guard may improve net candidate utility over 4h."
            ),
            "material_context_keys": ["regime", "funding_oi"],
            "reasoning": "The broad factor-only explanation omitted exact T0 confounders.",
        }, None

    monkeypatch.setattr(hypothesis_research, "llm_structured", narrow_review)
    monkeypatch.setattr(
        guard_routing,
        "create_guard_candidate",
        lambda **_: SimpleNamespace(id="causal-guard-candidate"),
    )
    evidence = {
        "case_count": 4,
        "outcome_counts": {"loss": 4},
        "factor_keys": ["momentum:BTC/USDT:long:medium_confidence:not_late"],
        "supporting_source_ids": case_ids,
        "scanner_families": ["momentum"],
    }
    routed = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence=evidence,
        source_id=case_ids[0],
    )
    assert routed["action"] == "guard_shadow_unavailable"
    assert routed["reason"] == "research_insufficient_context"
    assert routed["research_status"] == "insufficient_context"
    assert review_calls == []

    stored = routed["research_evidence"]
    assert stored["contract_version"] == "hypothesis-research-evidence/v1"
    assert stored["relevant_context"]["recorded_at_t0"] == {}
    assert "canonical_t0_envelope_unavailable" in stored["relevant_context"]["missing"]

    repeated = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence=evidence,
        source_id=case_ids[0],
        research_evidence=stored,
    )
    assert repeated["reason"] == "research_insufficient_context"
    assert list_records("learning_hypotheses") == []
    assert list_records("learning_experiments") == []
    assert list_records("learning_exposures") == []
    assert list_records("learning_contribution_verdicts") == []

    from aletheia.analytics.filter_evidence import record_filter_decision

    for index, source_opportunity_id in enumerate(opportunity_ids):
        record_filter_decision(
            candidate_key=f"opportunity:{source_opportunity_id}",
            candidate_kind="opportunity",
            filter_name="guard_candidate_block",
            stage="paper_ranker",
            decision="passed",
            reason_code="no_active_guard_block",
            symbol="BTC/USDT",
            market_type="usdt_futures",
            run_id="canonical-ranker-run",
            opportunity_id=source_opportunity_id,
            universe_snapshot_id="canonical-universe",
            universe_member_id=f"canonical-member-{index}",
            venue="bitget",
            direction="long",
            scanner_family="momentum",
            regime_tag="risk_off",
            inputs={"candidate_score": 1.0, "active_guard_ids": []},
            parameters={"guard_contract": "candidate-guard/v1"},
            evaluated_at="2026-08-16T00:00:00+00:00",
            outcome_candidate_key=f"opportunity:{source_opportunity_id}",
            entry_price_usd=None if index == 0 else 100.0,
            entry_spread_bps=None if index == 0 else 1.0,
            funding_rate_at_entry=None if index == 0 else 0.001,
            entry_ts=(
                None if index == 0 else "2026-08-16T00:00:00+00:00"
            ),
            raise_on_error=True,
        )

    t0_envelopes = guard_routing._build_ranker_guard_t0_envelopes(case_ids)
    assert len(t0_envelopes) == len(case_ids)
    for envelope in t0_envelopes:
        liquidity = next(
            domain for domain in envelope.domains
            if domain.domain == "liquidity_cost"
        )
        assert liquidity.state == "available"
        assert liquidity.source_refs[0].startswith("filter_candidate_outcome:")
        assert liquidity.source_hashes
    mismatched = t0_envelopes[0].model_copy(update={
        "anchor_entity_id": t0_envelopes[1].anchor_entity_id,
        "anchor_at": t0_envelopes[1].anchor_at,
    })
    recorded, missing, outcomes, cases = (
        hypothesis_research._assemble_t0_envelope_context(
            path_id="ranker_learned_guard",
            consumer="paper ranker",
            source_artifact_ids=[case_ids[0]],
            envelopes=[mismatched],
        )
    )
    assert recorded == {}
    assert outcomes == []
    assert cases == []
    assert any(reason.startswith("source_t0_identity_mismatch:") for reason in missing)

    async def ready_review(*, user, **_kwargs):
        payload = json.loads(user)
        assert set(payload["recorded_t0_context"]) >= {
            "candidate", "population", "features", "market_identity",
            "regime", "liquidity_cost", "ordering_policy", "guard_manifest",
        }
        return {
            "competing_explanations": [
                "The loss cluster may reflect the recorded risk-off regime."
            ],
            "material_confounders_or_missing": [],
            "smallest_falsification_test": (
                "Prospectively compare the bounded guard with contemporaneous control."
            ),
            "decision": "ready",
            "narrowed_candidate_explanation": None,
            "material_context_keys": [],
            "reasoning": "Canonical decision-time facts support a bounded Shadow test.",
        }, None

    monkeypatch.setattr(hypothesis_research, "llm_structured", ready_review)
    wired = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence=evidence,
        source_id=case_ids[0],
    )
    assert not wired.get("research_evidence", {}).get("missing_evidence"), wired
    assert wired["action"] == "guard_shadow_candidate", wired
    assert wired["research_status"] == "ready"
    assert wired["hypothesis_status"] == "proposed"
    assert list_records("learning_hypotheses")

    ufc = get_learning_path("universe_family_calibration")
    learned_guard = get_learning_path("ranker_learned_guard")
    assert ufc.behavior_contract_version == "universe-family-calibration/v11"
    assert ufc.measurement_supported is True
    assert learned_guard.measurement_supported is False

    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "DELETE FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? AND horizon='4h'",
        (f"opportunity:{opportunity_id}",),
    )
    db.commit()

    async def review_without_exact_outcome_must_not_run(**_kwargs):
        raise AssertionError("red-team review must not run without exact outcome evidence")

    monkeypatch.setattr(
        hypothesis_research,
        "llm_structured",
        review_without_exact_outcome_must_not_run,
    )
    missing_outcome = await hypothesis_research.research_learning_hypothesis(
        candidate_explanation="A broad momentum block may improve 4h net utility.",
        source_artifact_ids=[case_ids[0]],
        evidence={"case_count": 1},
        proposed_scope={"symbol": ["BTC/USDT"], "scanner_family": ["momentum"]},
        target_horizon="4h",
    )
    assert missing_outcome.research_status == "insufficient_context"
    assert "exact_counterfactual_evidence_unavailable" in missing_outcome.missing_evidence
    insert_canonical_outcome_for_shadow(
        "causal-shadow-selected", horizon="4h", net_return_pct=-2.0,
    )

    repo.insert_shadow_trade(ShadowTrade(
        id="causal-shadow-later-selected",
        batch_id="later-causal-replay-batch",
        opportunity_id=opportunity_id,
        symbol="BTC/USDT",
        venue="bitget",
        market_type="usdt_futures",
        direction="long",
        scanner_family="momentum",
        rank=1,
        entry_price=100.0,
        outcome_candidate_key=f"opportunity:{opportunity_id}",
        was_selected=True,
        created_at="2026-08-16T01:00:00+00:00",
    ))
    assert hypothesis_research._bounded_counterfactual_checks(
        [{"opportunity_id": opportunity_id, "opened_at": "2026-08-16T00:00:00+00:00"}],
        3,
        "4h",
    ) == []

    repo.update_position(
        "causal-loss-position-0",
        closed_at="2026-08-16T01:00:00+00:00",
    )
    horizon_mismatch = await hypothesis_research.research_learning_hypothesis(
        candidate_explanation="A broad momentum block may improve 4h net utility.",
        source_artifact_ids=[case_ids[0]],
        evidence={"case_count": 1},
        proposed_scope={"symbol": ["BTC/USDT"], "scanner_family": ["momentum"]},
        target_horizon="4h",
    )
    assert horizon_mismatch.research_status == "insufficient_context"
    assert f"outcome_horizon_mismatch:{case_ids[0]}" in horizon_mismatch.missing_evidence
