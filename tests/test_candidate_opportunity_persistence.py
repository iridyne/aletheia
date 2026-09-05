"""Admission evidence stays truthful across Candidate persistence and planning."""
from datetime import datetime

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import UniverseMember, UniverseSnapshot
from aletheia.universe.candidate_opportunity import (
    CandidateOpportunityContextV1,
    target_candidate_opportunity_context,
)


AT = "2026-09-05T00:00:00+00:00"


def _context():
    return target_candidate_opportunity_context(
        candidate_id="startup-candidate", policy_epoch="startup-epoch",
        source_record_hash="a" * 64, detected_at=AT,
        decision={
            "version": "candidate-selection-utility/v2",
            "primary_mechanism": "continuation", "primary_direction": "long",
            "economic_utility_pct": None, "admission_basis": "startup",
            "startup_reason": "no_history",
            "opportunity_evidence": {
                "source_cutoff_at": AT, "feature_snapshot_id": "snapshot-facts",
                "mechanisms": [{
                    "mechanism_id": "continuation", "mechanism_version": "continuation/v1",
                    "source_component_id": "momentum", "pessimistic_strength": 72,
                    "capability_gaps": [], "evidence_refs": [],
                }],
            },
        },
    )


def _member(context):
    return UniverseMember(
        id=context.candidate_id, snapshot_id="startup-snapshot", asset_id="btc",
        venue="bitget", symbol="BTC/USDT", market_type="perp", rank=1,
        total_score=72, scanner_family="momentum", scanner_direction="long",
        expires_at="2099-01-01T00:00:00+00:00", entry_price_usd=100,
        candidate_selection_policy="candidate_selection_utility_v1",
        candidate_selection_policy_epoch="startup-epoch",
        candidate_selection_utility_pct=context.opportunity["economic_utility_pct"],
        opportunity_mechanism="continuation", opportunity_mechanism_version="continuation/v1",
        opportunity_source_component="momentum", opportunity_source_cutoff_at=AT,
        opportunity_evidence_hash="b" * 64,
        candidate_opportunity_context_version=context.version,
        candidate_opportunity_context_json=context.model_dump_json(),
        candidate_opportunity_context_hash=context.context_hash,
    )


def test_startup_persists_null_utility_and_can_own_planning_generation():
    context = _context()
    member = _member(context)
    repo.insert_universe_snapshot(UniverseSnapshot(id=member.snapshot_id, created_at=AT))
    repo.insert_candidate(member)
    persisted = repo.get_candidate(member.id)
    assert persisted["candidate_selection_utility_pct"] is None
    restored = CandidateOpportunityContextV1.model_validate_json(
        persisted["candidate_opportunity_context_json"]
    )
    assert restored.context_hash == context.context_hash
    assert restored.opportunity["admission_basis"] == "startup"
    assert restored.opportunity["startup_reason"] == "no_history"
    generation, _ = repo.ensure_candidate_planning_generation(
        member.id, now=datetime.fromisoformat(AT)
    )
    assert generation["last_candidate_id"] == member.id
    assert generation["opportunity_mechanism"] == "continuation"
    assert generation["direction"] == "long"


@pytest.mark.parametrize("change", [
    {"admission_basis": None}, {"admission_basis": "measured"},
    {"startup_reason": "tail_harm"}, {"startup_reason": "overdue_settlement"},
    {"economic_utility_pct": 0}, {"capability_gaps": ["missing_facts"]},
    {"capability_status": "observe_only"}, {"pessimistic_strength": float("nan")},
])
def test_null_utility_requires_valid_startup_evidence(change):
    payload = _context().model_dump()
    payload["context_hash"] = ""
    payload["opportunity"].update(change)
    with pytest.raises(ValueError):
        CandidateOpportunityContextV1.model_validate(payload)


def test_startup_preserves_hash_epoch_and_member_identity_checks():
    context = _context()
    payload = context.model_dump()
    payload["opportunity"]["startup_reason"] = "child_sample_insufficient"
    with pytest.raises(ValueError, match="hash_mismatch"):
        CandidateOpportunityContextV1.model_validate(payload)
    payload["context_hash"] = ""
    payload["candidate_selection"]["policy_epoch"] = None
    with pytest.raises(ValueError, match="epoch_missing"):
        CandidateOpportunityContextV1.model_validate(payload)
    member = _member(context).model_dump()
    for change in ({"candidate_selection_utility_pct": 1}, {"scanner_direction": "short"},
                   {"opportunity_source_component": "mean_reversion"}):
        with pytest.raises(ValueError, match="identity mismatch"):
            UniverseMember.model_validate(member | change)


def test_historical_measured_context_hash_is_not_rewritten():
    payload = _context().model_dump()
    payload["context_hash"] = ""
    payload["candidate_selection"].pop("decision_version")
    for field in ("admission_basis", "startup_reason", "pessimistic_strength"):
        payload["opportunity"].pop(field)
    payload["opportunity"]["economic_utility_pct"] = 1.5
    historical = CandidateOpportunityContextV1.model_validate(payload)
    serialized = historical.model_dump_json()
    restored = CandidateOpportunityContextV1.model_validate_json(serialized)
    assert restored.model_dump_json() == serialized
    assert "admission_basis" not in restored.opportunity
    assert _member(restored).candidate_selection_utility_pct == 1.5
