"""OpenAI native provider — direct connection to api.openai.com."""

import asyncio
import json
from collections.abc import AsyncIterator

from aletheia.net import httpx_client, raise_for_status_without_secrets
from aletheia.model_provider.base import (
    ModelProvider,
    ModelRequest,
    StructuredModelRequest,
    ModelResponse,
    ModelHealth,
    StreamChunk,
    structured_via_complete,
    health_from_catalogue_probe,
)

BASE_URL = "https://api.openai.com/v1"

_CHAT_MODEL_PREFIXES = ("gpt-5", "gpt-4", "gpt-3.5", "o1", "o3", "o4", "chatgpt-4o")

_TIER_RULES = [
    (("gpt-4o-mini", "gpt-4-turbo"), "standard"),
    (("gpt-4o",), "flagship"),
    (("gpt-4",), "flagship"),
    (("o1", "o3", "o4"), "reasoning"),
    (("gpt-3.5",), "fast"),
    (("chatgpt-4o",), "flagship"),
]
_CONTEXT_RULES = [
    (("gpt-4o",), 128_000),
    (("gpt-4-turbo",), 128_000),
    (("gpt-4-0125",), 128_000),
    (("gpt-4-1106",), 128_000),
    (("gpt-3.5-turbo",), 16_385),
    (("o1", "o3", "o4"), 200_000),
]


def _infer_tier(model_id: str) -> str:
    for prefixes, tier in _TIER_RULES:
        if any(model_id.startswith(p) for p in prefixes):
            return tier
    return "standard"


def _infer_context(model_id: str) -> int | None:
    for prefixes, ctx in _CONTEXT_RULES:
        if any(model_id.startswith(p) for p in prefixes):
            return ctx
    return None


def _infer_capabilities(model_id: str) -> list[str]:
    caps = ["tool_use", "streaming"]
    if model_id.startswith(("gpt-4o", "chatgpt-4o")):
        caps.append("vision")
    return caps


class OpenAINativeProvider(ModelProvider):
    name = "openai_native"

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None, oauth_token: str | None = None):
        self.model = model
        self.api_key = api_key
        self.oauth_token = oauth_token

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        token = self.oauth_token or self.api_key
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    async def complete(self, request: ModelRequest) -> ModelResponse:
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        elif request.response_format == "json_schema" and request.structured_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.structured_schema_name,
                    "strict": True,
                    "schema": request.structured_schema,
                },
            }
        client = self._get_http_client(httpx_client)
        resp = await client.post(f"{BASE_URL}/chat/completions", json=payload, headers=self._headers())
        raise_for_status_without_secrets(resp)
        data = resp.json()
        choice = data["choices"][0]
        return ModelResponse(
            content=choice["message"]["content"],
            model=data.get("model", self.model),
            usage=data.get("usage", {}),
            raw=data,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        client = self._get_http_client(httpx_client)
        usage_emitted = False
        async with client.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            json=payload,
            headers=self._headers(),
        ) as resp:
            raise_for_status_without_secrets(resp)
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload_str = line[6:]
                if payload_str.strip() == "[DONE]":
                    if not usage_emitted:
                        yield StreamChunk(content="", done=True)
                    break
                data = json.loads(payload_str)
                actual_model = data.get("model")
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield StreamChunk(content=content, model=actual_model)
                usage = data.get("usage")
                if usage:
                    usage_emitted = True
                    yield StreamChunk(content="", done=True, usage=usage, model=actual_model)

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        return await structured_via_complete(self, request, schema)

    async def list_models(self) -> list[dict]:
        try:
            client = self._get_http_client(httpx_client)
            async with asyncio.timeout(10):
                resp = await client.get(f"{BASE_URL}/models", headers=self._headers())
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception:
            return []
        models = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            mid = item.get("id", "")
            if not mid:
                continue
            if any(mid.startswith(p) for p in _CHAT_MODEL_PREFIXES):
                created = item.get("created")
                models.append(
                    {
                        "slug": mid,
                        "display_name": mid,
                        "tier": _infer_tier(mid),
                        "context_window": _infer_context(mid),
                        "capabilities": _infer_capabilities(mid),
                        "created_at": created,
                    }
                )
        return sorted(models, key=lambda m: m["slug"])

    async def healthcheck(self) -> ModelHealth:
        return await health_from_catalogue_probe(self)
