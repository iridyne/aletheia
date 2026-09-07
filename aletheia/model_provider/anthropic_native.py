"""Anthropic native provider — direct connection to api.anthropic.com."""

import asyncio
import json
from collections.abc import AsyncIterator

from aletheia.net import httpx_client, raise_for_status_without_secrets
from aletheia.model_provider.base import (
    ModelHealth,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    StructuredModelRequest,
    structured_via_complete,
    health_from_catalogue_probe,
)

BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"

_TIER_MAP = {"opus": "flagship", "sonnet": "standard", "haiku": "fast"}
_CONTEXT_WINDOWS = {"opus": 1_000_000, "sonnet": 1_000_000, "haiku": 200_000}
_CAPABILITIES = ["vision", "tool_use", "streaming"]


def _infer_tier(model_id: str) -> str:
    for key, tier in _TIER_MAP.items():
        if key in model_id:
            return tier
    return "standard"


def _infer_context(model_id: str) -> int | None:
    for key, ctx in _CONTEXT_WINDOWS.items():
        if key in model_id:
            return ctx
    return None


class AnthropicNativeProvider(ModelProvider):
    name = "anthropic_native"

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None, oauth_token: str | None = None):
        self.model = model
        self.api_key = api_key
        self.oauth_token = oauth_token

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "anthropic-version": API_VERSION,
        }
        if self.oauth_token:
            h["Authorization"] = f"Bearer {self.oauth_token}"
        elif self.api_key:
            h["x-api-key"] = self.api_key
        return h

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload: dict = {
            "model": self.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.system:
            payload["system"] = request.system
        if request.response_format == "json_schema" and request.structured_schema is not None:
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request.structured_schema,
                }
            }
        client = self._get_http_client(httpx_client)
        resp = await client.post(f"{BASE_URL}/v1/messages", json=payload, headers=self._headers())
        raise_for_status_without_secrets(resp)
        data = resp.json()
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
        return ModelResponse(
            content=content,
            model=data.get("model", self.model),
            usage=data.get("usage", {}),
            raw=data,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        payload: dict = {
            "model": self.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": True,
        }
        if request.system:
            payload["system"] = request.system
        client = self._get_http_client(httpx_client)
        usage: dict = {}
        actual_model = self.model
        async with client.stream(
            "POST",
            f"{BASE_URL}/v1/messages",
            json=payload,
            headers=self._headers(),
        ) as resp:
            raise_for_status_without_secrets(resp)
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                event_type = data.get("type", "")
                if event_type == "message_start":
                    message = data.get("message") or {}
                    actual_model = message.get("model") or actual_model
                    usage.update(message.get("usage") or {})
                elif event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield StreamChunk(content=delta.get("text", ""), model=actual_model)
                elif event_type == "message_stop":
                    yield StreamChunk(content="", done=True, usage=usage or None, model=actual_model)
                elif event_type == "message_delta":
                    usage.update(data.get("usage") or {})

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        return await structured_via_complete(self, request, schema)

    async def list_models(self) -> list[dict]:
        try:
            client = self._get_http_client(httpx_client)
            async with asyncio.timeout(10):
                resp = await client.get(f"{BASE_URL}/v1/models", headers=self._headers())
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception:
            return []
        models = []
        for item in data.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                mid = item["id"]
                display = item.get("display_name", mid)
                models.append(
                    {
                        "slug": mid,
                        "display_name": display,
                        "tier": _infer_tier(mid),
                        "context_window": _infer_context(mid),
                        "capabilities": list(_CAPABILITIES),
                        "created_at": item.get("created_at"),
                    }
                )
        return models

    async def healthcheck(self) -> ModelHealth:
        return await health_from_catalogue_probe(self)
