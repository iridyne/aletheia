"""Focused regression for #161 — helpers thread tier provider name to fallback.

`llm_complete(provider="deep_provider_name", ...)` used to resolve the provider
object via get_provider() but hand only that object to complete_with_fallback.
complete_with_fallback then computed:
    resolved_provider_name = provider_name or cfg.models.default_provider
and — because helpers never passed provider_name — always resolved to
`default_provider`, so:
  (a) cooldown state for the tier's models was being keyed under the
      DEFAULT provider's name (deep-tier failures never tripped their own
      model's cooldown)
  (b) candidate_models() was resolved against default_provider's config,
      not the tier's

This test locks in that helpers now pass provider_name= alongside provider=.
"""

from __future__ import annotations


async def _make_async_provider(provider):
    """Helper for tests: wrap sync mock provider in async."""
    return provider


from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_llm_complete_threads_provider_name_to_complete_with_fallback():
    """#161: caller passes provider="deep_provider_xyz"; helpers must forward
    that exact string as provider_name= to complete_with_fallback (not None)."""
    from aletheia.model_provider.base import ModelResponse
    from aletheia.agents import helpers

    captured: dict = {}

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured["provider_name"] = provider_name
        captured["provider_object"] = provider
        return ModelResponse(content="ok", raw={})

    with patch.object(helpers, "complete_with_fallback", fake_complete_with_fallback):
        with patch.object(helpers, "get_provider_async", lambda n: _make_async_provider(object())):
            content, err = await helpers.llm_complete(
                "sys", "user", provider="deep_provider_xyz",
            )

    assert err is None
    assert content == "ok"
    assert captured["provider_name"] == "deep_provider_xyz", (
        f"helpers must forward the tier provider name; got {captured['provider_name']!r}"
    )


@pytest.mark.asyncio
async def test_llm_complete_forwards_none_when_caller_did_not_specify():
    """#161 corollary: when the caller omits provider, we still forward
    provider_name=None so complete_with_fallback falls back to default_provider
    (its existing behaviour, which is correct in that case)."""
    from aletheia.model_provider.base import ModelResponse
    from aletheia.agents import helpers

    captured: dict = {}

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured["provider_name"] = provider_name
        return ModelResponse(content="ok", raw={})

    with patch.object(helpers, "complete_with_fallback", fake_complete_with_fallback):
        with patch.object(helpers, "get_provider_async", lambda n: _make_async_provider(object())):
            await helpers.llm_complete("sys", "user")

    assert captured["provider_name"] is None


@pytest.mark.asyncio
async def test_llm_complete_preserves_empty_exception_type():
    """TimeoutError has an empty str(); diagnostics must still retain its type."""
    from aletheia.agents import helpers

    async def timeout(*_args, **_kwargs):
        raise TimeoutError()

    with patch.object(helpers, "complete_with_fallback", timeout):
        with patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(object())):
            content, err = await helpers.llm_complete("sys", "user")

    assert content is None
    assert err == "llm_unavailable: TimeoutError"


@pytest.mark.asyncio
async def test_llm_structured_threads_provider_name_to_complete_with_fallback():
    """#161: llm_structured must apply the same fix — its loop calls
    complete_with_fallback and must forward provider_name too."""
    import json
    from aletheia.model_provider.base import ModelResponse
    from aletheia.agents import helpers
    from pydantic import BaseModel

    class DummySchema(BaseModel):
        value: int

    captured: dict = {}

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured["provider_name"] = provider_name
        return ModelResponse(content=json.dumps({"value": 42}), raw={"usage": {"completion_tokens": 5}})

    with patch.object(helpers, "complete_with_fallback", fake_complete_with_fallback):
        with patch.object(helpers, "get_provider_async", lambda n: _make_async_provider(object())):
            data, err = await helpers.llm_structured(
                "sys", "user", schema=DummySchema, provider="deep_provider_xyz",
            )

    assert err is None
    assert data == {"value": 42}
    assert captured["provider_name"] == "deep_provider_xyz"


@pytest.mark.asyncio
async def test_llm_structured_threads_explicit_reasoning_effort_to_every_attempt():
    from aletheia.agents import helpers
    from aletheia.model_provider.base import ModelResponse

    class ObjectSchema:
        @staticmethod
        def model_json_schema():
            return {"type": "object", "properties": {"value": {"type": "integer"}}}

    captured = []
    responses = iter(
        [
            ModelResponse(content="not json", raw={"usage": {"completion_tokens": 2}}),
            ModelResponse(content='{"value": 42}', raw={"usage": {"completion_tokens": 5}}),
        ]
    )

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured.append(request)
        return next(responses)

    with patch.object(helpers, "complete_with_fallback", fake_complete_with_fallback):
        with patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(object())):
            data, err = await helpers.llm_structured(
                "sys",
                "user",
                schema=ObjectSchema,
                reasoning_effort="none",
            )

    assert err is None
    assert data == {"value": 42}
    assert len(captured) == 2
    assert all(request.reasoning_effort == "none" for request in captured)
    assert captured[0].inference is captured[1].inference


@pytest.mark.asyncio
async def test_llm_structured_opt_in_defaults_survive_provider_failure():
    """Opt-in non-critical callers receive marked defaults even on provider failure."""
    from aletheia.agents import helpers

    class FallbackSchema:
        @staticmethod
        def model_json_schema():
            return {
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "items": {"type": "array"},
                },
            }

    async def fail(*_args, **_kwargs):
        raise ConnectionError("offline")

    with patch.object(helpers, "complete_with_fallback", fail), \
         patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(object())), \
         patch.object(helpers, "_record_llm"):
        data, err = await helpers.llm_structured(
            "sys", "user", schema=FallbackSchema, fallback_to_defaults=True,
        )

    assert data == {"count": 0, "items": []}
    assert err == "llm_unavailable: ConnectionError: offline(fallback)"


@pytest.mark.asyncio
async def test_llm_structured_repairs_valid_non_object_json():
    """A valid JSON list is still invalid for the object schema and must be retried."""
    from aletheia.agents import helpers
    from aletheia.model_provider.base import ModelResponse

    class ObjectSchema:
        @staticmethod
        def model_json_schema():
            return {"type": "object", "properties": {"value": {"type": "integer"}}}

    responses = iter([
        ModelResponse(content="[]", raw={"usage": {"completion_tokens": 1}}),
        ModelResponse(content='{"value": 9}', raw={"usage": {"completion_tokens": 3}}),
    ])
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return next(responses)

    with patch.object(helpers, "complete_with_fallback", complete), \
         patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(object())), \
         patch.object(helpers, "_record_llm"):
        data, err = await helpers.llm_structured("sys", "user", schema=ObjectSchema)

    assert data == {"value": 9}
    assert err is None
    assert calls == 2
