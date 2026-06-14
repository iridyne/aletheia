"""Anthropic native provider — direct connection to api.anthropic.com."""

import json
from collections.abc import AsyncIterator

import httpx

from aletheia.model_provider.base import (
    ModelProvider, ModelRequest, StructuredModelRequest,
    ModelResponse, ModelHealth, StreamChunk,
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

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None,
                 oauth_token: str | None = None):
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
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{BASE_URL}/v1/messages", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
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
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", f"{BASE_URL}/v1/messages",
                json=payload, headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = json.loads(line[6:])
                    event_type = data.get("type", "")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield StreamChunk(content=delta.get("text", ""))
                    elif event_type == "message_stop":
                        yield StreamChunk(content="", done=True)
                    elif event_type == "message_delta":
                        usage = data.get("usage")
                        if usage:
                            yield StreamChunk(content="", done=True, usage=usage)

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        messages = list(request.messages)
        schema_json = json.dumps(
            schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}
        )
        messages.append({
            "role": "user",
            "content": f"Respond with valid JSON matching this schema: {schema_json}",
        })
        mr = ModelRequest(
            messages=messages,
            system=request.system,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        resp = await self.complete(mr)
        return json.loads(resp.content)

    async def list_models(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{BASE_URL}/v1/models", headers=self._headers()
                )
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
                models.append({
                    "slug": mid,
                    "display_name": display,
                    "tier": _infer_tier(mid),
                    "context_window": _infer_context(mid),
                    "capabilities": list(_CAPABILITIES),
                    "created_at": item.get("created_at"),
                })
        return models

    async def healthcheck(self) -> ModelHealth:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{BASE_URL}/v1/models", headers=self._headers()
                )
                if resp.status_code == 200:
                    models = await self.list_models()
                    slugs = [m["slug"] for m in models]
                    return ModelHealth(
                        provider=self.name, available=True,
                        model=self.model, models=slugs,
                        model_metadata=models,
                    )
                return ModelHealth(
                    provider=self.name, available=False,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=str(e))
