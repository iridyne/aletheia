"""OpenAI-compatible model provider (chat_completions and responses protocols)."""

import json

from aletheia.net import httpx_client
from aletheia.model_provider.base import (
    ModelProvider, ModelRequest, StructuredModelRequest,
    ModelResponse, ModelHealth,
)


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
        async with httpx_client(timeout=60) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
            resp.raise_for_status()
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
        async with httpx_client(timeout=60) as client:
            resp = await client.post(f"{self.base_url}/responses", json=payload, headers=self._headers())
            resp.raise_for_status()
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
        schema_json = json.dumps(schema.model_json_schema() if hasattr(schema, 'model_json_schema') else {})
        messages.append({"role": "user", "content": f"Respond with valid JSON matching this schema: {schema_json}"})
        mr = ModelRequest(messages=messages, temperature=request.temperature, max_tokens=request.max_tokens)
        resp = await self.complete(mr)
        return json.loads(resp.content)

    async def list_models(self) -> list[dict]:
        """Fetch available models from the OpenAI-compatible /models endpoint."""
        try:
            async with httpx_client(timeout=4) as client:
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
            async with httpx_client(timeout=4) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
                if resp.status_code == 200:
                    models = await self.list_models()
                    slugs = [m["slug"] for m in models]
                    return ModelHealth(
                        provider=self.name, available=True, model=self.model,
                        models=slugs, model_metadata=models,
                    )
        except Exception:
            pass
        # /models not available (Databricks, some gateways) — probe with inference
        try:
            from aletheia.model_provider.base import ModelRequest
            await self.complete(ModelRequest(
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=200, temperature=0,
            ))
            return ModelHealth(provider=self.name, available=True, model=self.model, models=[], model_metadata=[])
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=str(e))
