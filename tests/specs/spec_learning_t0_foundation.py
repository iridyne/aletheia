"""Executable causal-boundary claims for the Learning T0 foundation."""

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "learning_contract"


def _domain(
    name: str,
    *,
    received_at: str = "2026-01-01T00:00:00+00:00",
    state: str = "available",
    source_class: str = "observed_fact",
):
    from aletheia.learning.t0_context import T0ContextDomainV1

    return T0ContextDomainV1(
        domain=name,
        state=state,
        source_class=source_class,
        values={"value": 1} if state == "available" else {},
        source_refs=[f"source:{name}"],
        source_hashes=[name],
        source_observed_at="2025-12-31T23:59:00+00:00",
        received_at=received_at,
        calculation_version=None,
        unavailable_reason=(
            None if state in {"available", "not_applicable"} else "not available"
        ),
    )


def _ranker_envelope(*, domains=None, population_identity: str | None = "population-1"):
    from aletheia.learning.t0_context import T0ContextEnvelopeV1

    required = [
        "candidate", "population", "features", "market_identity",
        "regime", "liquidity_cost", "ordering_policy",
    ]
    return T0ContextEnvelopeV1(
        contract_version="learning-t0-context/v1",
        path_id="ranker_case_score",
        consumer="paper ranker",
        consumer_stage="ranker_evaluation",
        t0_anchor_kind="filter_decision",
        anchor_entity_id="decision-1",
        anchor_at="2026-01-01T00:00:00+00:00",
        capture_mode="captured_at_t0",
        runtime_session_id="runtime-1",
        producer_commit="commit-1",
        behavior_fingerprint="behavior-1",
        policy_epoch="epoch-1",
        required_domains=required,
        recommended_domains=[],
        domains=domains or [_domain(name) for name in required],
        root_causal_identity="position-1",
        population_identity=population_identity,
        observed_world_hash="world-1",
        completeness_reasons=[],
    )


@spec_claim(SpecClaim(
    "LT0-S01", _CONTRACT, "Treatment-specific T0", "L2a",
    "Downstream Operator reasoning cannot enter an upstream Ranker T0 envelope.",
    status="implemented",
))
def test_operator_reasoning_cannot_enter_ranker_t0():
    from aletheia.learning.t0_context import assess_t0_foundation

    envelope = _ranker_envelope(domains=[
        *[_domain(name) for name in _ranker_envelope().required_domains],
        _domain("operator_reasoning"),
    ])
    assessment = assess_t0_foundation(envelope)
    assert assessment.temporal_integrity is False
    assert assessment.status == "advisory_only"
    assert "downstream_stage_leakage:operator_reasoning" in assessment.reasons


@spec_claim(SpecClaim(
    "LT0-S02", _CONTRACT, "Receive-time integrity", "L2a",
    "A fact received after the treatment boundary is future information.",
    status="implemented",
))
def test_received_after_boundary_is_future_information():
    from aletheia.learning.t0_context import assess_t0_foundation

    domains = [_domain(name) for name in _ranker_envelope().required_domains]
    domains[0] = _domain("candidate", received_at="2026-01-01T00:00:01+00:00")
    assessment = assess_t0_foundation(_ranker_envelope(domains=domains))
    assert assessment.temporal_integrity is False
    assert assessment.status == "advisory_only"
    assert "future_information:candidate" in assessment.reasons


@spec_claim(SpecClaim(
    "LT0-S03", _CONTRACT, "Root economic identity", "L2a",
    "Multiple cognitive artifacts from one Position remain one economic observation.",
    status="implemented",
))
def test_position_artifacts_deduplicate_to_one_root_observation():
    from aletheia.learning.memory_assets import MemoryAssetRefV1, unique_root_observations

    assets = [
        MemoryAssetRefV1(
            asset_id=f"asset-{kind}",
            asset_type=kind,
            evidence_class="NARRATIVE",
            canonical_source_ref=f"knowledge:{kind}",
            root_outcome_identity="position-1",
            observed_at="2026-01-02T00:00:00+00:00",
            source_hash=kind,
        )
        for kind in ("case", "review", "memo", "lesson", "reflection")
    ]
    assert unique_root_observations(assets) == ("position-1",)


@spec_claim(SpecClaim(
    "LT0-S04", _CONTRACT, "Population-relative T0", "L2a",
    "A Ranker hypothesis without contemporaneous candidate population cannot auto-nominate.",
    status="implemented",
))
def test_ranker_without_population_context_is_advisory_only():
    from aletheia.learning.t0_context import assess_t0_foundation

    assessment = assess_t0_foundation(_ranker_envelope(population_identity=None))
    assert assessment.population_context_ok is False
    assert assessment.status == "advisory_only"
    assert "population_context_missing" in assessment.reasons


@spec_claim(SpecClaim(
    "LT0-S05", _CONTRACT, "Canonical anchor authority", "L2a",
    "Knowledge Markdown cannot redefine the canonical causal anchor.",
    status="implemented",
))
def test_knowledge_artifact_cannot_be_causal_anchor():
    from aletheia.learning.t0_context import assess_t0_foundation

    envelope = _ranker_envelope().model_copy(update={"t0_anchor_kind": "knowledge_item"})
    assessment = assess_t0_foundation(envelope)
    assert assessment.provenance_integrity is False
    assert "knowledge_artifact_cannot_anchor_t0" in assessment.reasons


@spec_claim(SpecClaim(
    "LT0-S06", _CONTRACT, "Retention honesty", "L2a",
    "Captured bounded projections replay after source pruning; reconstruction reports pruned.",
    status="implemented",
))
def test_retention_replay_uses_captured_projection_or_reports_pruned():
    from aletheia.learning.t0_context import replay_t0_envelope

    captured = _ranker_envelope()
    assert replay_t0_envelope(
        captured, unavailable_source_refs={"source:candidate"},
    ) == captured

    reconstructed = captured.model_copy(update={"capture_mode": "reconstructed_exact"})
    replayed = replay_t0_envelope(
        reconstructed, unavailable_source_refs={"source:candidate"},
    )
    assert replayed.capture_mode == "reconstructed_partial"
    assert next(item for item in replayed.domains if item.domain == "candidate").state == "pruned"


@spec_claim(SpecClaim(
    "LT0-S07", _CONTRACT, "Fact/belief separation", "L2a",
    "Narrative or model belief cannot satisfy a required observed-fact domain.",
    status="implemented",
))
def test_narrative_belief_cannot_satisfy_required_fact():
    from aletheia.learning.t0_context import assess_t0_foundation

    domains = [_domain(name) for name in _ranker_envelope().required_domains]
    domains[0] = _domain("candidate", source_class="model_belief")
    assessment = assess_t0_foundation(_ranker_envelope(domains=domains))
    assert assessment.required_domain_coverage < 1
    assert "belief_cannot_satisfy_required_fact:candidate" in assessment.reasons


@spec_claim(SpecClaim(
    "LT0-S08", _CONTRACT, "Explicit domain state", "L2a",
    "Semantically valid not-applicable differs from missing/unavailable.",
    status="implemented",
))
def test_not_applicable_domain_satisfies_declared_contract():
    from aletheia.learning.t0_context import assess_t0_foundation

    envelope = _ranker_envelope().model_copy(update={
        "required_domains": [*_ranker_envelope().required_domains, "funding"],
        "domains": [
            *_ranker_envelope().domains,
            _domain("funding", state="not_applicable"),
        ],
    })
    assert assess_t0_foundation(envelope).required_domain_coverage == 1.0


@spec_claim(SpecClaim(
    "LT0-S09", _CONTRACT, "Challenge separation", "L2a",
    "Reused discovery roots are surfaced instead of independent challenge support.",
    status="implemented",
))
def test_duplicate_root_blocks_automatic_research_readiness():
    from aletheia.learning.t0_context import assess_t0_foundation

    assessment = assess_t0_foundation(
        _ranker_envelope(),
        root_outcome_dedup_ok=False,
        challenge_separation_state="overlap_detected",
    )
    assert assessment.status == "advisory_only"
    assert "root_outcome_duplicate" in assessment.reasons


@spec_claim(SpecClaim(
    "LT0-S10", _CONTRACT, "Hypothesis complexity budget", "L2a",
    "More than three material context dimensions remains research-too-complex/advisory.",
    status="implemented",
))
def test_material_scope_over_budget_is_advisory():
    from aletheia.learning.t0_context import assess_t0_foundation

    assessment = assess_t0_foundation(
        _ranker_envelope(),
        material_context_keys=["a", "b", "c", "d"],
    )
    assert assessment.status == "advisory_only"
    assert "research_too_complex" in assessment.reasons


@spec_claim(SpecClaim(
    "LT0-S11", _CONTRACT, "Trajectory T0", "L2a",
    "An exit hypothesis binds one path-decision instant and excludes later path facts.",
    status="implemented",
))
def test_exit_trajectory_excludes_later_path_facts():
    from aletheia.learning.t0_context import assess_t0_foundation

    envelope = _ranker_envelope(domains=[
        _domain(
            "position_state",
            received_at="2026-01-01T00:00:01+00:00",
        ),
    ]).model_copy(update={
        "path_id": "exit_policy_advice",
        "consumer": "analytics API",
        "consumer_stage": "position_management_decision",
        "t0_anchor_kind": "position_path_observation",
        "required_domains": ["position_state"],
        "population_identity": None,
    })
    assessment = assess_t0_foundation(envelope)
    assert assessment.status == "advisory_only"
    assert "future_information:position_state" in assessment.reasons


@spec_claim(SpecClaim(
    "LT0-S12", _CONTRACT, "Legacy reconstruction honesty", "L2a",
    "Legacy context is exact only when every source is immutable and time-valid.",
    status="implemented",
))
def test_reconstructed_partial_cannot_auto_nominate():
    from aletheia.learning.t0_context import assess_t0_foundation

    envelope = _ranker_envelope().model_copy(update={
        "capture_mode": "reconstructed_partial",
    })
    assessment = assess_t0_foundation(envelope)
    assert assessment.status == "advisory_only"
    assert "reconstructed_partial" in assessment.reasons
