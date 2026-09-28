"""Structured calls preserve explicit routing and bounded schema repair."""

from __future__ import annotations

import json

import pytest
from tests.helpers.model_http import model_http
from pydantic import BaseModel

from aletheia.model_provider.base import (
    ModelRequest,
    ModelResponse,
    portable_json_schema_payload,
)

def test_entry_no_plan_nullable_enum_matches_provider_schema():
    from aletheia.agents.helpers import _validate_json_schema_value
    from aletheia.trading.entry_analysis import EntryHypothesis

    payload = EntryHypothesis(
        decision="NO_PLAN", reason_code="INSUFFICIENT_EVIDENCE",
        mechanism="continuation", direction="long", risk_tier="NONE", horizon="NONE",
    ).model_dump(mode="json")
    for schema in (EntryHypothesis.model_json_schema(), portable_json_schema_payload(EntryHypothesis)):
        _validate_json_schema_value(payload, schema, root=schema)
        with pytest.raises(ValueError):
            _validate_json_schema_value({**payload, "capture_priority": "INVALID"}, schema, root=schema)
        with pytest.raises((TypeError, ValueError)):
            _validate_json_schema_value({**payload, "direction": None}, schema, root=schema)


class FakeProvider:
    name = "openai_compatible"

    def __init__(self, *, model="deepseek-v4-flash", fail_models=None):
        self.model = model
        self.fail_models = fail_models or {}
        self.calls: list[str] = []

    async def complete(self, request):  # noqa: ARG002
        self.calls.append(self.model)
        err = self.fail_models.get(self.model)
        if err:
            if isinstance(err, BaseException):
                raise err
            raise RuntimeError(err)
        return ModelResponse(content=f"ok:{self.model}", model=self.model, raw={"ok": True})

    async def list_models(self):
        return []


def _patch_candidates(monkeypatch, models):
    from aletheia.infra.events import clear_infra_events
    from aletheia.model_provider import fallback

    async def fake_candidates(_provider_name, _provider):
        return list(models)

    monkeypatch.setattr(fallback, "_candidate_models", fake_candidates)
    fallback.reset_model_health()
    clear_infra_events()


class _ObjectSchema:
    @staticmethod
    def model_json_schema():
        return {"type": "object", "properties": {"value": {"type": "integer"}}}


@pytest.mark.real_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["agent_shell.entry_planner", "news_analyzer.analysis"])
async def test_structured_call_does_not_inject_unconfigured_endpoint(tmp_path, monkeypatch, caller):
    from aletheia.agents import helpers
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.model_provider import fallback

    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    cfg = AletheiaConfig()
    cfg.models.providers = {
        "selected": ProviderConfig(type="openai_compatible", enabled=True, model="primary"),
        "unselected": ProviderConfig(type="openai_compatible", enabled=True, model="glm-5.2"),
    }
    monkeypatch.setattr(fallback, "load_config", lambda: cfg)
    fallback.reset_model_health()
    provider = FakeProvider(model="primary", fail_models={"primary": "429 capacity"})
    resolved = []

    async def selected(name):
        assert name == "selected"
        return provider

    def unconfigured(name):
        resolved.append(name)
        raise AssertionError("unconfigured endpoint must not be resolved")

    monkeypatch.setattr(helpers, "get_provider_async", selected)
    monkeypatch.setattr(fallback.provider_router, "get_provider", unconfigured)
    data, error = await helpers.llm_structured(
        "sys", "user", schema=_ObjectSchema, provider="selected", caller=caller,
    )
    assert data is None
    assert error.startswith("llm_unavailable:")
    assert provider.calls == ["primary"]
    assert resolved == []


@pytest.mark.asyncio
async def test_complete_with_fallback_tries_flash_first_for_trade_json(monkeypatch):
    _patch_candidates(monkeypatch, ["deepseek-v4-flash", "glm-5.2"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(model="deepseek-v4-flash")
    resp = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            response_format="json_schema",
        ),
        provider=provider,
        provider_name="sensenova",
    )

    assert resp.content == "ok:deepseek-v4-flash"
    assert provider.calls == ["deepseek-v4-flash"]
    assert resp.raw["aletheia_model_fallback"]["requested_model"] == "sensenova/deepseek-v4-flash"


@pytest.mark.asyncio
async def test_complete_with_fallback_keeps_configured_primary_for_text(monkeypatch):
    _patch_candidates(monkeypatch, ["deepseek-v4-flash", "glm-5.2"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(model="deepseek-v4-flash")
    resp = await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
        provider_name="sensenova",
    )

    assert resp.content == "ok:deepseek-v4-flash"
    assert provider.calls == ["deepseek-v4-flash"]


@pytest.mark.asyncio
async def test_complete_with_fallback_skips_excluded_flash_model(monkeypatch):
    _patch_candidates(monkeypatch, ["deepseek-v4-flash", "glm-5.2"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(model="deepseek-v4-flash")
    resp = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            exclude_models=("deepseek-v4-flash",),
        ),
        provider=provider,
        provider_name="sensenova",
    )

    assert resp.content == "ok:glm-5.2"
    assert provider.calls == ["glm-5.2"]


@pytest.mark.asyncio
async def test_complete_with_fallback_falls_back_to_glm_when_flash_unavailable(monkeypatch):
    _patch_candidates(monkeypatch, ["deepseek-v4-flash", "glm-5.2"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(
        model="deepseek-v4-flash",
        fail_models={"deepseek-v4-flash": "429 Too Many Requests"},
    )
    resp = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            response_format="json_schema",
        ),
        provider=provider,
        provider_name="sensenova",
    )

    assert resp.content == "ok:glm-5.2"
    assert provider.calls == ["deepseek-v4-flash", "glm-5.2"]


@pytest.mark.asyncio
async def test_complete_with_fallback_reaches_operator_configured_deepseek_last(monkeypatch):
    _patch_candidates(
        monkeypatch,
        ["llama-4-maverick", "@deepseek:deepseek-v4-flash"],
    )
    from aletheia.model_provider import fallback

    free = FakeProvider(
        model="llama-4-maverick",
        fail_models={"llama-4-maverick": "429 Too Many Requests"},
    )
    deepseek = FakeProvider(model="deepseek-v4-flash")
    monkeypatch.setattr(
        fallback.provider_router,
        "get_provider",
        lambda name: {"deepseek": deepseek}[name],
    )

    response = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            response_format="json_schema",
        ),
        provider=free,
        provider_name="databricks_free",
    )

    assert response.content == "ok:deepseek-v4-flash"
    assert free.calls == ["llama-4-maverick"]
    assert deepseek.calls == ["deepseek-v4-flash"]
    assert response.raw["aletheia_model_fallback"]["final_model"] == "deepseek-v4-flash"

@pytest.mark.asyncio
async def test_llm_structured_requests_json_schema_and_skips_all_failed_models(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.agents import helpers

    captured = []
    responses = iter(
        [
            ModelResponse(
                content="not json",
                model="llama-4-maverick",
                raw={
                    "usage": {"completion_tokens": 2},
                    "aletheia_model_fallback": {
                        "final_model": "llama-4-maverick",
                        "attempted_models": ["qwen35-122b-a10b", "llama-4-maverick"],
                    },
                },
            ),
            ModelResponse(
                content='{"value": 7}',
                model="glm-5.2",
                raw={"usage": {"completion_tokens": 5}},
            ),
        ]
    )

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured.append(request)
        return next(responses)

    async def fake_get_provider_async(_name):
        return object()

    monkeypatch.setattr(helpers, "complete_with_fallback", fake_complete_with_fallback)
    monkeypatch.setattr(helpers, "get_provider", lambda _name: object())
    monkeypatch.setattr(helpers, "get_provider_async", fake_get_provider_async)
    data, err = await helpers.llm_structured(
        "sys",
        "user",
        schema=_ObjectSchema,
        caller="agent_shell.entry_planner",
    )

    assert err is None
    assert data == {"value": 7}
    assert len(captured) == 2
    assert all(request.response_format == "json_schema" for request in captured)
    assert all(request.structured_schema == {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "additionalProperties": False,
        "required": ["value"],
    } for request in captured)
    assert captured[0].exclude_models == ()
    assert captured[1].exclude_models == ("llama-4-maverick", "qwen35-122b-a10b")


@pytest.mark.asyncio
async def test_llm_structured_news_keeps_default_model_order_but_still_uses_json_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.agents import helpers

    captured = []

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured.append(request)
        return ModelResponse(
            content='{"value": 1}',
            model="deepseek-v4-flash",
            raw={"usage": {"completion_tokens": 3}},
        )

    async def fake_get_provider_async(_name):
        return object()

    monkeypatch.setattr(helpers, "complete_with_fallback", fake_complete_with_fallback)
    monkeypatch.setattr(helpers, "get_provider", lambda _name: object())
    monkeypatch.setattr(helpers, "get_provider_async", fake_get_provider_async)
    data, err = await helpers.llm_structured(
        "sys",
        "user",
        schema=_ObjectSchema,
        caller="news_analyzer.analysis",
    )

    assert err is None
    assert data == {"value": 1}
    assert captured[0].response_format == "json_schema"
    assert captured[0].structured_schema["required"] == ["value"]

@pytest.mark.asyncio
async def test_llm_structured_repair_attempt_uses_a_nonce_and_new_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.agents import helpers

    captured = []
    responses = iter(
        [
            ModelResponse(content="not json", model="m1", raw={"usage": {}}),
            ModelResponse(content='{"value": 3}', model="m2", raw={"usage": {}}),
        ]
    )

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured.append(request)
        return next(responses)

    monkeypatch.setattr(helpers, "complete_with_fallback", fake_complete_with_fallback)

    async def fake_get_provider_async(_name):
        return object()

    monkeypatch.setattr(helpers, "get_provider_async", fake_get_provider_async)
    data, err = await helpers.llm_structured("sys", "user", schema=_ObjectSchema)

    assert err is None
    assert data == {"value": 3}
    assert len(captured) == 2
    assert captured[0].messages != captured[1].messages
    assert captured[1].messages[-1]["content"].startswith("[repair:")

@pytest.mark.asyncio
async def test_llm_structured_repairs_semantic_rejection_with_same_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.agents import helpers
    from aletheia.economics import inference_calls

    captured = []
    outcomes = []
    responses = iter([
        ModelResponse(content='{"value": 1}', model="m1", raw={"usage": {}}),
        ModelResponse(content='{"value": 2}', model="m1", raw={"usage": {}}),
    ])

    async def complete(request, **_kwargs):
        captured.append(request)
        return next(responses)

    async def provider(_name):
        return object()

    monkeypatch.setattr(helpers, "complete_with_fallback", complete)
    monkeypatch.setattr(helpers, "get_provider_async", provider)
    monkeypatch.setattr(
        inference_calls, "record_structured_attempt_outcome",
        lambda _call_id, attempt, outcome: outcomes.append((attempt, outcome)) or True,
    )

    data, error = await helpers.llm_structured(
        "sys", "user", schema=_ObjectSchema,
        semantic_validator=lambda item: "value_not_grounded" if item["value"] == 1 else None,
    )

    assert (data, error) == ({"value": 2}, None)
    assert [request.exclude_models for request in captured] == [(), ()]
    repair = captured[1].messages[-1]["content"]
    assert repair.startswith("[repair:")
    assert "value_not_grounded" in repair
    assert outcomes == [(0, "structured_semantic_invalid"), (1, "accepted")]


@pytest.mark.asyncio
async def test_llm_structured_semantic_exhaustion_keeps_parse_and_schema_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.agents import helpers

    calls = []
    finished = []

    async def complete(request, **_kwargs):
        calls.append(request)
        return ModelResponse(content='{"value": 1}', model="m1", raw={"usage": {}})

    async def provider(_name):
        return object()

    monkeypatch.setattr(helpers, "complete_with_fallback", complete)
    monkeypatch.setattr(helpers, "get_provider_async", provider)
    monkeypatch.setattr(
        helpers, "_record_call_finished",
        lambda _call, **kwargs: finished.append(kwargs),
    )

    data, error = await helpers.llm_structured(
        "sys", "user", schema=_ObjectSchema,
        semantic_validator=lambda _item: "fact_ref_not_supplied",
    )

    assert data is None
    assert error == "structured_semantic_invalid:fact_ref_not_supplied"
    assert len(calls) == 2
    assert finished[-1]["status"] == "error"
    assert finished[-1]["parse_valid"] is True
    assert finished[-1]["schema_valid"] is True


@pytest.mark.asyncio
async def test_llm_structured_propagates_semantic_callback_exception_without_retry(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.agents import helpers

    calls = 0

    async def complete(_request, **_kwargs):
        nonlocal calls
        calls += 1
        return ModelResponse(content='{"value": 1}', model="m1", raw={"usage": {}})

    async def provider(_name):
        return object()

    def broken(_item):
        raise RuntimeError("validator_bug")

    monkeypatch.setattr(helpers, "complete_with_fallback", complete)
    monkeypatch.setattr(helpers, "get_provider_async", provider)

    with pytest.raises(RuntimeError, match="validator_bug"):
        await helpers.llm_structured(
            "sys", "user", schema=_ObjectSchema, semantic_validator=broken,
        )
    assert calls == 1




class _FakeHttpResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.url = "http://model.test/v1/chat/completions"
        self.request = type("Req", (), {"headers": {}})()

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


@pytest.mark.asyncio
async def test_openai_compatible_sends_json_object_response_format(monkeypatch):
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    payloads = []

    class FakeClient:
        async def post(self, url, json=None, headers=None):  # noqa: ARG002
            payloads.append(json)
            return _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": true}'}}],
                    "model": "glm-5.2",
                    "usage": {},
                },
            )

        async def aclose(self):
            return None

    with model_http(post=FakeClient().post):
        provider = OpenAICompatibleProvider(base_url="http://model.test/v1", model="glm-5.2")
        resp = await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "x"}],
                response_format="json_object",
            )
        )
        assert resp.content == '{"ok": true}'
        assert payloads[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_compatible_sends_strict_json_schema(monkeypatch):
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    payloads = []
    schema = {
        "type": "object",
        "properties": {"decision": {"type": "string", "enum": ["ENTRY_PLAN", "NO_PLAN"]}},
        "required": ["decision"],
        "additionalProperties": False,
    }

    class FakeClient:
        async def post(self, url, json=None, headers=None):  # noqa: ARG002
            payloads.append(json)
            return _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"decision": "NO_PLAN"}'}}],
                    "model": "m1",
                    "usage": {},
                },
            )

        async def aclose(self):
            return None

    with model_http(post=FakeClient().post):
        provider = OpenAICompatibleProvider(base_url="http://model.test/v1", model="m1")
        await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "x"}],
                response_format="json_schema",
                structured_schema=schema,
                structured_schema_name="plan_intent",
            )
        )

        assert payloads[0]["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "plan_intent",
                "strict": True,
                "schema": schema,
            },
        }


@pytest.mark.asyncio
async def test_openai_compatible_responses_preserves_strict_json_schema(monkeypatch):
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    payloads = []

    class FakeClient:
        async def post(self, url, json=None, headers=None):  # noqa: ARG002
            assert url.endswith("/responses")
            payloads.append(json)
            return _FakeHttpResponse(
                200,
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"decision":"NO_PLAN"}'}],
                        }
                    ],
                    "model": "m1",
                    "usage": {},
                },
            )

        async def aclose(self):
            return None

    schema = {
        "type": "object",
        "properties": {"decision": {"type": "string"}},
        "required": ["decision"],
        "additionalProperties": False,
    }
    with model_http(post=FakeClient().post):
        provider = OpenAICompatibleProvider(
            base_url="http://model.test/v1",
            model="m1",
            protocol="responses",
        )
        response = await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "x"}],
                response_format="json_schema",
                structured_schema=schema,
                structured_schema_name="plan_intent",
            )
        )

        assert response.content == '{"decision":"NO_PLAN"}'
        assert payloads[0]["text"] == {
            "format": {
                "type": "json_schema",
                "name": "plan_intent",
                "strict": True,
                "schema": schema,
            }
        }


@pytest.mark.asyncio
async def test_anthropic_compatible_sends_strict_json_schema(monkeypatch):
    from extensions.model_endpoints.anthropic_compatible import AnthropicCompatibleProvider

    payloads = []
    schema = {
        "type": "object",
        "properties": {"decision": {"type": "string"}},
        "required": ["decision"],
        "additionalProperties": False,
    }

    class FakeClient:
        async def post(self, url, json=None, headers=None):  # noqa: ARG002
            payloads.append(json)
            return _FakeHttpResponse(
                200,
                {
                    "content": [{"type": "text", "text": '{"decision": "NO_PLAN"}'}],
                    "model": "claude",
                    "usage": {},
                },
            )

        async def aclose(self):
            return None

    with model_http(provider="anthropic_compatible", post=FakeClient().post):
        provider = AnthropicCompatibleProvider(base_url="http://model.test", model="claude")
        await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "x"}],
                response_format="json_schema",
                structured_schema=schema,
            )
        )

        assert payloads[0]["output_config"] == {
            "format": {"type": "json_schema", "schema": schema}
        }


@pytest.mark.asyncio
async def test_openai_compatible_retries_without_json_object_when_unsupported(monkeypatch):
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    payloads = []

    class FakeClient:
        async def post(self, url, json=None, headers=None):  # noqa: ARG002
            payloads.append(json)
            if len(payloads) == 1:
                return _FakeHttpResponse(
                    400,
                    {
                        "error": {
                            "param": "response_format",
                            "message": "response_format is unsupported",
                        }
                    },
                )
            return _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": true}'}}],
                    "model": "m1",
                    "usage": {},
                },
            )

        async def aclose(self):
            return None

    with model_http(post=FakeClient().post):
        provider = OpenAICompatibleProvider(base_url="http://model.test/v1", model="m1")
        resp = await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "x"}],
                response_format="json_object",
            )
        )
        assert resp.content == '{"ok": true}'
        assert payloads[0]["response_format"] == {"type": "json_object"}
        assert "response_format" not in payloads[1]


@pytest.mark.asyncio
async def test_openai_compatible_retries_without_json_schema_when_unsupported(monkeypatch):
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider

    payloads = []
    schema = {
        "type": "object",
        "properties": {"decision": {"type": "string"}},
        "required": ["decision"],
        "additionalProperties": False,
    }

    class FakeClient:
        async def post(self, url, json=None, headers=None):  # noqa: ARG002
            payloads.append(json)
            if len(payloads) == 1:
                return _FakeHttpResponse(
                    400,
                    {
                        "error": {
                            "param": "json_schema",
                            "message": "json_schema is unsupported",
                        }
                    },
                )
            return _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"decision": "NO_PLAN"}'}}],
                    "model": "m1",
                    "usage": {},
                },
            )

        async def aclose(self):
            return None

    with model_http(post=FakeClient().post):
        provider = OpenAICompatibleProvider(base_url="http://model.test/v1", model="m1")
        resp = await provider.complete(
            ModelRequest(
                messages=[{"role": "user", "content": "x"}],
                response_format="json_schema",
                structured_schema=schema,
            )
        )

        assert resp.content == '{"decision": "NO_PLAN"}'
        assert payloads[0]["response_format"]["type"] == "json_schema"
        assert "response_format" not in payloads[1]
        assert payloads[1]["messages"][-1]["content"].startswith(
            "Respond with valid JSON matching this schema:"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("content,raw,usage,reason,tokens", [
    ("broken", {"usage": {"completion_tokens": 95}}, {}, "unknown", 95),
    ("broken", {"choices": [{"finish_reason": "length"}], "usage": {"completion_tokens": 95}}, {"output_tokens": 12}, "length", 12),
    ("", {"stop_reason": "max_tokens"}, {"output_tokens": 100}, "max_tokens", 100),
    ("broken", {"incomplete_details": {"reason": "max_output_tokens"}}, {}, "incomplete", 0),
    ("broken", {"choices": [{"finish_reason": "private-provider-text"}]}, {}, "unknown", 0),
])
async def test_structured_failure_retains_content_free_completion_state(
    tmp_path, monkeypatch, content, raw, usage, reason, tokens,
):
    from aletheia.agents import helpers
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    calls = []

    async def complete(request, **kwargs):
        calls.append(request)
        return ModelResponse(
            content=content, model="test", usage=usage, raw=raw,
            finish_reason=reason, output_tokens=tokens,
        )

    async def provider(name):
        return object()

    monkeypatch.setattr(helpers, "complete_with_fallback", complete)
    monkeypatch.setattr(helpers, "get_provider_async", provider)
    data, error = await helpers.llm_structured("sys", "user", schema=_ObjectSchema, max_tokens=100)
    assert data is None and error.startswith("structured_")
    assert len(calls) == 2
    from aletheia.economics.schema import ensure_economics_schema
    row = ensure_economics_schema().execute(
        "SELECT metadata_json FROM llm_inference_calls WHERE logical_call_id=?",
        (calls[-1].inference.logical_call_id,),
    ).fetchone()
    diagnostics = json.loads(row[0])["structured_failure"]
    assert diagnostics["finish_reason"] == reason
    assert diagnostics["output_tokens"] == str(tokens)
    assert diagnostics["output_limit"] == "100"
    assert "private-provider-text" not in json.dumps(diagnostics)
