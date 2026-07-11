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
        with patch.object(helpers, "get_provider", lambda n: object()):
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
        with patch.object(helpers, "get_provider", lambda n: object()):
            await helpers.llm_complete("sys", "user")

    assert captured["provider_name"] is None


@pytest.mark.asyncio
async def test_llm_complete_preserves_empty_exception_type():
    """TimeoutError has an empty str(); diagnostics must still retain its type."""
    from aletheia.agents import helpers

    async def timeout(*_args, **_kwargs):
        raise TimeoutError()

    with patch.object(helpers, "complete_with_fallback", timeout):
        with patch.object(helpers, "get_provider", lambda _name: object()):
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
        with patch.object(helpers, "get_provider", lambda n: object()):
            data, err = await helpers.llm_structured(
                "sys", "user", schema=DummySchema, provider="deep_provider_xyz",
            )

    assert err is None
    assert data == {"value": 42}
    assert captured["provider_name"] == "deep_provider_xyz"
