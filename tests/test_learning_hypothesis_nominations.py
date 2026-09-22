"""Append-only nomination lineage around canonical Learning hypotheses."""

import json
import sqlite3

import pytest

from tests.specs.spec_learning_attribution import _ready_research


def _envelope(source_bundle_hash: str, *, status: str = "ready"):
    from extensions.learning.hypotheses import (
        HypothesisDraftV1,
        HypothesisGenerationManifestV1,
        HypothesisNominationEnvelopeV1,
        HypothesisResearchEvidenceV1,
        hypothesis_fingerprint,
    )
    from extensions.learning.registry import get_learning_path
    from extensions.learning.t0_context import T0FoundationAssessmentV1

    research = _ready_research("case-1")
    research["research_status"] = status
    research["red_team_summary"]["decision"] = status
    evidence = HypothesisResearchEvidenceV1.model_validate(research)
    path = get_learning_path("ranker_case_score")
    scope = {"symbol": "BTC/USDT", "direction": "long"}
    treatment = {"type": "score_adjustment", "delta": -1.0}
    fingerprint = hypothesis_fingerprint(
        path_id=path.path_id,
        scope=scope,
        action=treatment,
        target_horizon="4h",
        source_cohort="cohort-1",
        behavior_contract_version=path.behavior_contract_version,
    )
    return HypothesisNominationEnvelopeV1(
        research_evidence=evidence,
        t0_foundation=T0FoundationAssessmentV1(
            temporal_integrity=True,
            provenance_integrity=True,
            required_domain_coverage=1.0,
            cohort_required_domain_coverage=1.0,
            root_outcome_dedup_ok=True,
            population_context_ok=True,
            challenge_separation_state="disjoint",
            model_hindsight_risk="low",
            status="eligible_for_hypothesis_research",
            reasons=[],
        ),
        draft=HypothesisDraftV1(
            path_id=path.path_id,
            consumer=path.consumer,
            source_type="reflection",
            source_artifact_ids=["case-1"],
            source_cohort="cohort-1",
            scope=scope,
            baseline_action={"type": "score_adjustment", "delta": 0.0},
            treatment_action=treatment,
            primary_metric=path.metric_version,
            outcome_contract_version=path.outcome_contract_version,
            target_horizon="4h",
            effect_threshold=0.01,
            harm_budget={"max_loss_rate_increase": 0.15},
            falsifier="Treatment fails to improve cost-aware utility.",
            overlap_group=path.overlap_group,
            behavior_fingerprint=fingerprint,
            status="compilable",
            reasons=[],
        ),
        generation_manifest=HypothesisGenerationManifestV1(
            method="reflection",
            producer="test",
            producer_version="v1",
            source_bundle_hash=source_bundle_hash,
            parent_hypothesis_ids=[],
            agent_run_ids=[],
            tool_run_ids=[],
            estimated_cost_usd=0.01,
        ),
    )


def test_exact_retry_is_idempotent_and_new_evidence_reuses_behavior_identity():
    from extensions.learning.hypotheses import publish_hypothesis_nomination
    from extensions.learning.ledger import list_nominations

    first = publish_hypothesis_nomination(_envelope("bundle-a"))
    retry = publish_hypothesis_nomination(_envelope("bundle-a"))
    newer = publish_hypothesis_nomination(_envelope("bundle-b"))

    assert retry.nomination_id == first.nomination_id
    assert newer.nomination_id != first.nomination_id
    assert newer.hypothesis_id == first.hypothesis_id
    matching = [row for row in list_nominations(limit=10) if row["hypothesis_id"] == first.hypothesis_id]
    assert len(matching) == 2
    assert {row["source_bundle_hash"] for row in matching} == {"bundle-a", "bundle-b"}


def test_rejected_attempt_is_retained_without_hypothesis_or_experiment():
    from extensions.learning.hypotheses import publish_hypothesis_nomination
    from aletheia.storage.sqlite import get_db

    nomination = publish_hypothesis_nomination(_envelope("bundle-rejected", status="rejected"))

    assert nomination.publication_status == "rejected"
    assert nomination.hypothesis_id is None
    assert json.loads(nomination.envelope_json)["research_evidence"]["research_status"] == "rejected"
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 0


def test_nomination_history_rejects_update_and_delete():
    from extensions.learning.hypotheses import publish_hypothesis_nomination
    from aletheia.storage.sqlite import get_db

    nomination = publish_hypothesis_nomination(_envelope("bundle-immutable"))
    db = get_db()
    with pytest.raises(sqlite3.IntegrityError, match="history is immutable"):
        db.execute(
            "UPDATE learning_hypothesis_nominations SET research_status=? "
            "WHERE nomination_id=?",
            ("rejected", nomination.nomination_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="history is retained"):
        db.execute(
            "DELETE FROM learning_hypothesis_nominations WHERE nomination_id=?",
            (nomination.nomination_id,),
        )
    row = db.execute(
        "SELECT research_status FROM learning_hypothesis_nominations "
        "WHERE nomination_id=?",
        (nomination.nomination_id,),
    ).fetchone()
    assert row["research_status"] == nomination.research_status
