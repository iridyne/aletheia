"""Mechanical Learning B proof; fake providers never establish effectiveness."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aletheia.model_provider.base import ModelProvider, ModelResponse
from aletheia.storage.sqlite import get_db

_FIXTURE = (
    Path(__file__).parent.parent
    / "docs"
    / "research"
    / "llm_harness"
    / "fixtures"
    / "learning_b_market_observer_v1.json"
)


class _PairProvider(ModelProvider):
    name = "fixture-provider"
    model = "fixed-fixture-model"

    async def complete(self, request):
        content = {
            "direction_bias": "long",
            "confidence": "medium",
            "confidence_score": 63,
            "rationale": "fixture-only response",
            "key_observations": ["RSI 68.1", "OI +6.4%"],
        }
        return ModelResponse(
            content=json.dumps(content),
            model=self.model,
            usage={"input_tokens": 100, "output_tokens": 40},
            raw={"usage": {"input_tokens": 100, "output_tokens": 40}},
        )


async def _direct_complete(request, *, provider=None, provider_name=None):
    return await provider.complete(request)


@pytest.mark.asyncio
async def test_learning_b_pair_is_blinded_linked_and_aggregate_only(tmp_path, monkeypatch):
    from aletheia.agents import helpers
    from aletheia.economics import context_effectiveness as effectiveness

    provider = _PairProvider()
    review_path = tmp_path / "blind-review.json"
    monkeypatch.setattr(
        effectiveness,
        "_provider_preflight",
        lambda _name: ("fixture-provider", None),
    )
    with (
        patch.object(helpers, "get_provider", lambda _name: provider),
        patch.object(helpers, "complete_with_fallback", _direct_complete),
    ):
        report = await effectiveness.run_learning_b_pair(
            fixture_path=_FIXTURE,
            review_output=review_path,
        )

    assert report["status"] == "awaiting_owner_review"
    assert report["effectiveness_conclusion"] is None
    assert report["metrics"]["valid_pair"] is True
    assert review_path.stat().st_mode & 0o777 == 0o600
    packet = json.loads(review_path.read_text(encoding="utf-8"))
    assert [item["label"] for item in packet["presentations"]] == ["A", "B"]
    assert all("arm" not in item for item in packet["presentations"])

    db = get_db()
    logical = db.execute(
        "SELECT COUNT(*) FROM llm_inference_calls WHERE "
        "json_extract(metadata_json,'$.evaluation_pair_id')=?",
        (report["evaluation_pair_id"],),
    ).fetchone()[0]
    artifacts = db.execute(
        "SELECT evaluation_arm,kind,rendered_chars,logical_call_id "
        "FROM llm_context_artifacts WHERE evaluation_pair_id=?",
        (report["evaluation_pair_id"],),
    ).fetchall()
    assert logical == 2
    assert len(artifacts) == 4
    assert {row["evaluation_arm"] for row in artifacts} == {"control", "treatment"}
    assert all(row["logical_call_id"] for row in artifacts)
    stored = "\n".join(
        str(row[0])
        for row in db.execute(
            "SELECT metadata_json FROM llm_inference_calls WHERE "
            "json_extract(metadata_json,'$.evaluation_pair_id')=?",
            (report["evaluation_pair_id"],),
        ).fetchall()
    )
    assert "fixture-only response" not in stored

    scores_path = tmp_path / "scores.json"
    scores_path.write_text(
        json.dumps(
            {
                "reviewer": "owner",
                "rubric_version": "learning-b-owner-v1",
                "scores": {
                    "A": {
                        "quality_score": 3,
                        "grounded_use": "not_applicable",
                        "context_harm": False,
                    },
                    "B": {
                        "quality_score": 4,
                        "grounded_use": "yes",
                        "context_harm": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    review = effectiveness.record_learning_b_review(
        pair_id=report["evaluation_pair_id"], scores_path=scores_path
    )
    assert review["status"] == "review_recorded"
    assert db.execute("SELECT COUNT(*) FROM llm_context_pair_reviews").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_learning_b_reports_provider_block_without_fake_conclusion(tmp_path, monkeypatch):
    from aletheia.economics import context_effectiveness as effectiveness

    monkeypatch.setattr(
        effectiveness,
        "_provider_preflight",
        lambda _name: (None, "provider_not_configured"),
    )
    review_path = tmp_path / "must-not-exist.json"
    report = await effectiveness.run_learning_b_pair(
        fixture_path=_FIXTURE,
        review_output=review_path,
    )
    assert report == {
        "status": "blocked",
        "blocker": "provider_not_configured",
        "fixture_id": "learning-b-market-observer-v1",
        "effectiveness_conclusion": None,
    }
    assert not review_path.exists()


def test_learning_b_refuses_raw_review_packet_inside_repository():
    from aletheia.economics.context_effectiveness import _review_path

    with pytest.raises(ValueError, match="outside the repository"):
        _review_path(_FIXTURE.parent / "raw-output.json")
