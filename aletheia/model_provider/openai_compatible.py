"""OpenAI-compatible model provider (chat_completions and responses protocols)."""

import asyncio
import json

import httpx

from aletheia.net import httpx_client, raise_for_status_without_secrets
from aletheia.model_provider.base import (
    ModelHealth,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    StructuredModelRequest,
    model_json_schema_payload,
)

# Backwards-compatible alias — the implementation now lives in aletheia.net so every
# provider shares one credential-scrubbing path (error-leak 2.7).
_raise_without_secrets = raise_for_status_without_secrets


def _extract_text_content(raw) -> str:
    """Normalize message content: some models (e.g. Qwen3.5 thinking mode) return
    content as a list of blocks [{type:"reasoning",...},{type:"text",text:"..."}]
    instead of a plain string. Extract the text block(s)."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        text_parts = [block.get("text", "") for block in raw if block.get("type") == "text"]
        if text_parts:
            return "".join(text_parts)
        # No text block (token budget exhausted on reasoning) — return empty so
        # callers treat it as an incomplete/empty response rather than raw JSON.
        return ""
    return str(raw) if raw else ""


class OpenAICompatibleProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str = "default", protocol: str = "chat_completions", api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.protocol = protocol
        self.api_key = api_key

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self.protocol == "responses":
            return await self._complete_responses(request)
        return await self._complete_chat(request)

    async def _complete_chat(self, request: ModelRequest) -> ModelResponse:
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        client = self._get_http_client(httpx_client)
        resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
        _raise_without_secrets(resp)
        data = resp.json()
        choice = data["choices"][0]
        raw_content = choice["message"]["content"]
        content = _extract_text_content(raw_content)
        return ModelResponse(
            content=content,
            model=data.get("model", self.model),
            usage=data.get("usage", {}),
            raw=data,
        )

    async def _complete_responses(self, request: ModelRequest) -> ModelResponse:
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        payload = {
            "model": self.model,
            "input": messages,
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
        }
        client = self._get_http_client(httpx_client)
        resp = await client.post(f"{self.base_url}/responses", json=payload, headers=self._headers())
        _raise_without_secrets(resp)
        data = resp.json()
        content = ""
        for item in data.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        content += c.get("text", "")
        return ModelResponse(
            content=content or data.get("output_text", ""),
            model=data.get("model", self.model),
            usage=data.get("usage", {}),
            raw=data,
        )

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        schema_json = json.dumps(model_json_schema_payload(schema))
        messages.append({"role": "user", "content": f"Respond with valid JSON matching this schema: {schema_json}"})
        mr = ModelRequest(messages=messages, temperature=request.temperature, max_tokens=request.max_tokens)
        resp = await self.complete(mr)
        return json.loads(resp.content)

    async def list_models(self) -> list[dict]:
        """Fetch available models from the OpenAI-compatible /models endpoint."""
        try:
            client = self._get_http_client(httpx_client)
            async with asyncio.timeout(4):
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception:
            return []
        models = []
        if isinstance(data, list):
            items = data
        else:
            items = data.get("data") or []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                mid = item["id"]
                models.append({"slug": mid, "display_name": mid})
            elif isinstance(item, str):
                models.append({"slug": item, "display_name": item})
        return models

    async def healthcheck(self) -> ModelHealth:
        try:
            models = await self.list_models()
            if models:
                slugs = [m["slug"] for m in models]
                return ModelHealth(
                    provider=self.name, available=True, model=self.model,
                    models=slugs, model_metadata=models,
                )
            return ModelHealth(
                provider=self.name,
                available=False,
                model=self.model,
                error="model catalogue unavailable; use explicit Ping to test inference",
            )
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=str(e))
