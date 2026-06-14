"""OpenAI native provider — direct connection to api.openai.com."""

import json
from collections.abc import AsyncIterator

import httpx

from aletheia.model_provider.base import (
    ModelProvider, ModelRequest, StructuredModelRequest,
    ModelResponse, ModelHealth, StreamChunk,
)

BASE_URL = "https://api.openai.com/v1"

_CHAT_MODEL_PREFIXES = ("gpt-4", "gpt-3.5", "o1", "o3", "o4", "chatgpt-4o")

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

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None,
                 oauth_token: str | None = None):
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
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{BASE_URL}/chat/completions", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
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
        }
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", f"{BASE_URL}/chat/completions",
                json=payload, headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload_str = line[6:]
                    if payload_str.strip() == "[DONE]":
                        yield StreamChunk(content="", done=True)
                        break
                    data = json.loads(payload_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield StreamChunk(content=content)
                    usage = data.get("usage")
                    if usage:
                        yield StreamChunk(content="", done=True, usage=usage)

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        schema_json = json.dumps(
            schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}
        )
        messages.append({
            "role": "user",
            "content": f"Respond with valid JSON matching this schema: {schema_json}",
        })
        mr = ModelRequest(
            messages=messages, temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        resp = await self.complete(mr)
        return json.loads(resp.content)

    async def list_models(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{BASE_URL}/models", headers=self._headers()
                )
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
                models.append({
                    "slug": mid,
                    "display_name": mid,
                    "tier": _infer_tier(mid),
                    "context_window": _infer_context(mid),
                    "capabilities": _infer_capabilities(mid),
                    "created_at": created,
                })
        return sorted(models, key=lambda m: m["slug"])

    async def healthcheck(self) -> ModelHealth:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{BASE_URL}/models", headers=self._headers()
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
