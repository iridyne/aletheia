"""Content-free diagnostics for structured inference failures.

Fixtures are synthetic and contain no production response, provider identity,
prompt, endpoint, model name, or credential material.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from aletheia.model_provider.base import ModelResponse
from aletheia.storage.sqlite import get_db


class _OperatorDecisionSchema:
    @staticmethod
    def model_json_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["trade", "skip"]},
                "conviction": {"type": "integer", "minimum": 0, "maximum": 100},
                "reasoning": {"type": "string"},
                "bear_case": {"type": "string", "minLength": 1},
            },
            "required": ["action", "conviction", "reasoning", "bear_case"],
        }


async def _run_failed_structured_fixture(content: str) -> tuple[str, dict]:
    from aletheia.agents import helpers

    async def complete(*_args, **_kwargs):
        return ModelResponse(
            content=content,
            raw={"usage": {"completion_tokens": 20}},
        )

    with (
        patch.object(helpers, "complete_with_fallback", complete),
        patch.object(helpers, "get_provider", lambda _name: object()),
        patch.object(helpers, "_record_llm"),
    ):
        data, error = await helpers.llm_structured(
            "synthetic system",
            "synthetic user",
            schema=_OperatorDecisionSchema,
            caller="system_operator.assess_opportunity",
        )

    assert data is None
    assert error is not None
    row = get_db().execute(
        "SELECT * FROM llm_inference_calls "
        "WHERE caller='system_operator.assess_opportunity' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    return error, dict(row)


@pytest.mark.asyncio
async def test_schema_failure_persists_only_bounded_content_free_diagnostics():
    secret_marker = "SYNTHETIC_VALUE_MUST_NOT_PERSIST"
    content = json.dumps(
        {
            "action": "skip",
            "conviction": secret_marker,
            "reasoning": secret_marker,
            "bear_case": secret_marker,
        }
    )

    error, row = await _run_failed_structured_fixture(content)
    metadata = json.loads(row["metadata_json"])

    assert error == "structured_schema_invalid: wrong_type"
    assert metadata["structured_failure"] == {
        "response_shape": "json_object",
        "schema_error_code": "wrong_type",
    }
    assert secret_marker not in json.dumps(row, ensure_ascii=False)
    outcomes = get_db().execute(
        "SELECT outcome FROM llm_structured_attempt_outcomes "
        "WHERE logical_call_id=? ORDER BY structured_attempt_index",
        (row["logical_call_id"],),
    ).fetchall()
    assert [item["outcome"] for item in outcomes] == ["schema_invalid"] * 3


@pytest.mark.asyncio
async def test_parse_failure_classifies_text_without_retaining_it():
    secret_marker = "SYNTHETIC_PROSE_MUST_NOT_PERSIST"

    error, row = await _run_failed_structured_fixture(secret_marker)
    metadata = json.loads(row["metadata_json"])

    assert error == "structured_parse_error: no valid JSON object found in response"
    assert metadata["structured_failure"] == {
        "response_shape": "non_json_text",
    }
    assert secret_marker not in json.dumps(row, ensure_ascii=False)
    outcomes = get_db().execute(
        "SELECT outcome FROM llm_structured_attempt_outcomes "
        "WHERE logical_call_id=? ORDER BY structured_attempt_index",
        (row["logical_call_id"],),
    ).fetchall()
    assert [item["outcome"] for item in outcomes] == ["parse_error"] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected_shape", "expected_error_prefix"),
    [
        (
            json.dumps(
                {
                    "object": "chat.completion",
                    "choices": ["SYNTHETIC_NESTED_VALUE_MUST_NOT_PERSIST"],
                }
            ),
            "openai_chat_envelope",
            "structured_schema_invalid",
        ),
        (
            json.dumps(
                {
                    "object": "response",
                    "output": ["SYNTHETIC_NESTED_VALUE_MUST_NOT_PERSIST"],
                }
            ),
            "openai_responses_envelope",
            "structured_schema_invalid",
        ),
        (
            json.dumps(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": ["SYNTHETIC_NESTED_VALUE_MUST_NOT_PERSIST"],
                }
            ),
            "anthropic_message_envelope",
            "structured_schema_invalid",
        ),
        (
            json.dumps("SYNTHETIC_JSON_STRING_MUST_NOT_PERSIST"),
            "json_string",
            "structured_parse_error",
        ),
    ],
    ids=["chat-envelope", "responses-envelope", "anthropic-envelope", "json-string"],
)
async def test_known_shapes_are_classified_but_not_normalized(
    content: str,
    expected_shape: str,
    expected_error_prefix: str,
):
    error, row = await _run_failed_structured_fixture(content)
    metadata = json.loads(row["metadata_json"])

    assert error.startswith(expected_error_prefix)
    assert metadata["structured_failure"]["response_shape"] == expected_shape
    assert "SYNTHETIC_" not in json.dumps(row, ensure_ascii=False)


def test_durable_diagnostic_allowlist_drops_arbitrary_keys_and_values():
    from aletheia.economics.inference_calls import _safe_failure_diagnostics

    assert _safe_failure_diagnostics(
        {
            "response_shape": "SYNTHETIC_UNREGISTERED_SHAPE",
            "schema_error_code": "SYNTHETIC_UNREGISTERED_ERROR",
            "response_text": "SYNTHETIC_BODY",
            "parsed_root_keys": "SYNTHETIC_KEYS",
        }
    ) == {}
