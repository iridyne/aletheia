"""Mechanical Learning B proof; fake providers never establish effectiveness."""

from __future__ import annotations


async def _make_async_provider(provider):
    """Helper for tests: wrap sync mock provider in async."""
    return provider


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
_TECHNICAL_FIXTURE = (
    Path(__file__).parent.parent
    / "docs"
    / "research"
    / "llm_harness"
    / "fixtures"
    / "technical_context_market_observer_v1.json"
)


def test_technical_fixture_contains_only_production_rendered_block() -> None:
    fixture = json.loads(_TECHNICAL_FIXTURE.read_text(encoding="utf-8"))
    technical = fixture["technical_context"]

    assert technical.endswith("\n")
    assert "deterministic derived facts" not in technical


class _PairProvider(ModelProvider):
    name = "fixture-provider"
    model = "fixed-fixture-model"

    def __init__(self):
        self.reasoning_efforts = []

    async def complete(self, request):
        self.reasoning_efforts.append(request.reasoning_effort)
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


class _CapturingPairProvider(_PairProvider):
    def __init__(self):
        super().__init__()
        self.requests: dict[str, list[str]] = {}

    async def complete(self, request):
        caller = request.inference.caller.caller_id
        rendered = "\n".join(str(item.get("content") or "") for item in request.messages)
        self.requests.setdefault(caller, []).append(rendered)
        return await super().complete(request)


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
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(provider)),
        patch.object(helpers, "complete_with_fallback", _direct_complete),
    ):
        report = await effectiveness.run_learning_b_pair(
            fixture_path=_FIXTURE,
            review_output=review_path,
        )

    assert report["status"] == "awaiting_owner_review"
    assert report["effectiveness_conclusion"] is None
    assert report["metrics"]["valid_pair"] is True
    assert provider.reasoning_efforts == ["none", "none"]
    assert {
        arm["total_tokens"] for arm in report["metrics"]["arms"].values()
    } == {140}
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


@pytest.mark.asyncio
async def test_technical_context_pair_reuses_harness_and_stays_content_free(
    tmp_path, monkeypatch
):
    from aletheia.agents import helpers
    from aletheia.economics import context_effectiveness as effectiveness

    provider = _PairProvider()
    review_path = tmp_path / "technical-review.json"
    monkeypatch.setattr(
        effectiveness,
        "_provider_preflight",
        lambda _name: ("fixture-provider", None),
    )
    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(provider)),
        patch.object(helpers, "complete_with_fallback", _direct_complete),
    ):
        report = await effectiveness.run_technical_context_pair(
            fixture_path=_TECHNICAL_FIXTURE,
            review_output=review_path,
        )

    assert report["status"] == "awaiting_owner_review"
    assert report["evaluation_pair_id"].startswith("technical_context_")
    assert report["metrics"]["valid_pair"] is True
    assert provider.reasoning_efforts == ["none", "none"]
    packet = json.loads(review_path.read_text(encoding="utf-8"))
    assert packet["protocol"] == "technical-context-owner-blind-review-v1"
    assert packet["rubric_version"] == "technical-context-owner-v1"

    db = get_db()
    calls = db.execute(
        "SELECT caller,context_adapter_id,metadata_json FROM llm_inference_calls WHERE "
        "json_extract(metadata_json,'$.evaluation_pair_id')=?",
        (report["evaluation_pair_id"],),
    ).fetchall()
    assert {row["caller"] for row in calls} == {
        "evaluation.technical_context.control",
        "evaluation.technical_context.treatment",
    }
    adapter_by_caller = {row["caller"]: row["context_adapter_id"] for row in calls}
    assert adapter_by_caller == {
        "evaluation.technical_context.control": "none",
        "evaluation.technical_context.treatment": "technical-context-v1",
    }
    artifacts = db.execute(
        "SELECT evaluation_arm,kind,stable_id,rendered_chars "
        "FROM llm_context_artifacts WHERE evaluation_pair_id=? "
        "AND stable_id LIKE 'technical-context-v1:fixture:%'",
        (report["evaluation_pair_id"],),
    ).fetchall()
    assert {row["evaluation_arm"] for row in artifacts} == {"control", "treatment"}
    assert {row["kind"] for row in artifacts} == {"environment"}
    assert sorted(row["rendered_chars"] for row in artifacts)[0] == 0

    scores_path = tmp_path / "technical-scores.json"
    control_label = report["metrics"]["arms"]["control"]["blind_label"]
    treatment_label = report["metrics"]["arms"]["treatment"]["blind_label"]
    score_payload = {
        "rubric_version": "technical-context-owner-v1",
        "scores": {
            control_label: {
                "quality_score": 3,
                "grounded_use": "not_applicable",
                "context_harm": False,
            },
            treatment_label: {
                "quality_score": 5,
                "grounded_use": "yes",
                "context_harm": True,
            },
        },
    }
    scores_path.write_text(json.dumps(score_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="pair identity mismatch"):
        effectiveness.record_learning_b_review(
            pair_id=report["evaluation_pair_id"], scores_path=scores_path
        )
    wrong_rubric = {**score_payload, "rubric_version": "learning-b-owner-v1"}
    scores_path.write_text(json.dumps(wrong_rubric), encoding="utf-8")
    with pytest.raises(ValueError, match="rubric version mismatch"):
        effectiveness.record_technical_context_review(
            pair_id=report["evaluation_pair_id"], scores_path=scores_path
        )
    scores_path.write_text(json.dumps(score_payload), encoding="utf-8")
    review = effectiveness.record_technical_context_review(
        pair_id=report["evaluation_pair_id"], scores_path=scores_path
    )
    assert review["status"] == "review_recorded"
    assert review["verdict"] == "control_better"
    assert review["gate_failures"] == ["treatment_context_harm"]
    stored_rubric = db.execute(
        "SELECT rubric_version FROM llm_context_pair_reviews WHERE evaluation_pair_id=?",
        (report["evaluation_pair_id"],),
    ).fetchone()[0]
    assert stored_rubric == "technical-context-owner-v1"
    assert effectiveness.summarize_learning_b_reviews()["reviewed_pairs"] == 0

    db.execute(
        "DELETE FROM runtime_cost_events WHERE call_id IN (?,?)",
        (
            report["metrics"]["arms"]["control"]["logical_call_id"],
            report["metrics"]["arms"]["treatment"]["logical_call_id"],
        ),
    )
    missing_telemetry = effectiveness._pair_metrics(report["evaluation_pair_id"])
    assert missing_telemetry["valid_pair"] is False
    assert set(missing_telemetry["invalid_reasons"]) == {
        "control_missing_attempt_telemetry",
        "treatment_missing_attempt_telemetry",
    }


@pytest.mark.asyncio
async def test_context_ablation_omits_only_named_modality(tmp_path, monkeypatch):
    from aletheia.agents import helpers
    from aletheia.economics import context_effectiveness as effectiveness

    fixture_path = tmp_path / "ablation.json"
    fixture_path.write_text(
        json.dumps(
            {
                "fixture_id": "context-ablation-news-v1",
                "symbol": "BTC/USDT",
                "market_context": "TECHNICAL_BLOCK\nLEARNING_BLOCK",
                "modality_context": "NEWS_BLOCK",
            }
        ),
        encoding="utf-8",
    )
    provider = _CapturingPairProvider()
    monkeypatch.setattr(
        effectiveness,
        "_provider_preflight",
        lambda _name: ("fixture-provider", None),
    )
    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(provider)),
        patch.object(helpers, "complete_with_fallback", _direct_complete),
    ):
        report = await effectiveness.run_context_ablation_pair(
            fixture_path=fixture_path,
            modality_id="news",
            review_output=tmp_path / "blind-ablation.json",
        )

    assert report["status"] == "awaiting_owner_review"
    assert report["modality_id"] == "news"
    control = next(
        item
        for item in provider.requests["evaluation.context_ablation.control"]
        if "TECHNICAL_BLOCK" in item
    )
    treatment = next(
        item
        for item in provider.requests["evaluation.context_ablation.treatment"]
        if "TECHNICAL_BLOCK" in item
    )
    assert "NEWS_BLOCK" not in control
    assert "NEWS_BLOCK" in treatment

    rows = get_db().execute(
        "SELECT evaluation_arm,rendered_chars FROM llm_context_artifacts "
        "WHERE evaluation_pair_id=? AND stable_id LIKE '%:news'",
        (report["evaluation_pair_id"],),
    ).fetchall()
    assert {row["evaluation_arm"]: row["rendered_chars"] for row in rows} == {
        "control": 0,
        "treatment": len("NEWS_BLOCK"),
    }

    scores_path = tmp_path / "ablation-scores.json"
    scores = {
        report["metrics"]["arms"]["control"]["blind_label"]: {
            "quality_score": 3,
            "grounded_use": "not_applicable",
            "context_harm": False,
        },
        report["metrics"]["arms"]["treatment"]["blind_label"]: {
            "quality_score": 4,
            "grounded_use": "yes",
            "context_harm": False,
        },
    }
    scores_path.write_text(
        json.dumps(
            {
                "rubric_version": "context-ablation-owner-v1",
                "scores": scores,
            }
        ),
        encoding="utf-8",
    )
    review = effectiveness.record_context_ablation_review(
        pair_id=report["evaluation_pair_id"], scores_path=scores_path
    )
    assert review["verdict"] == "treatment_better"
    assert review["modality_id"] == "news"
