"""Contrastive Memory evidence remains bounded and root-deduplicated."""

import pytest
from pydantic import ValidationError


def _asset(asset_id: str, root: str):
    from extensions.learning.memory_assets import MemoryAssetRefV1

    return MemoryAssetRefV1(
        asset_id=asset_id,
        asset_type="trade_case_study",
        evidence_class="narrative",
        canonical_source_ref=f"knowledge:{asset_id}",
        root_outcome_identity=root,
        source_hash=asset_id,
    )


def _item(asset_id: str, root: str, role: str = "anchor"):
    from extensions.learning.contrastive_evidence import ContrastiveEvidenceItemV1

    return ContrastiveEvidenceItemV1(role=role, asset=_asset(asset_id, root))


def test_discovery_and_challenge_roots_are_disjoint_and_deduplicated():
    from extensions.learning.contrastive_evidence import build_contrastive_bundle

    bundle = build_contrastive_bundle(
        path_id="ranker_case_score",
        discovery_items=[_item("case", "position-1"), _item("memo", "position-1")],
        challenge_items=[_item("counterexample", "position-2", "nearest_counterexample")],
    )

    assert bundle.root_outcome_identities == ["position-1", "position-2"]
    assert bundle.challenge_separation_state == "disjoint"


def test_reused_challenge_root_is_surfaced_not_counted_as_independent():
    from extensions.learning.contrastive_evidence import build_contrastive_bundle

    bundle = build_contrastive_bundle(
        path_id="ranker_case_score",
        discovery_items=[_item("case", "position-1")],
        challenge_items=[_item("review", "position-1", "contradiction")],
    )

    assert bundle.root_outcome_identities == ["position-1"]
    assert bundle.challenge_separation_state == "overlap_detected"


def test_contrastive_bundle_rejects_more_than_twenty_assets():
    from extensions.learning.contrastive_evidence import build_contrastive_bundle

    with pytest.raises(ValidationError, match="at most 20 items"):
        build_contrastive_bundle(
            path_id="ranker_case_score",
            discovery_items=[_item(f"case-{i}", f"position-{i}") for i in range(21)],
            challenge_items=[],
        )
