"""Anthropic-compatible model provider."""

import json
import httpx

from aletheia.model_provider.base import (
    ModelProvider, ModelRequest, StructuredModelRequest,
    ModelResponse, ModelHealth,
)


class AnthropicCompatibleProvider(ModelProvider):
    name = "anthropic_compatible"

    def __init__(self, base_url: str, model: str = "default", api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.api_key:
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
            resp = await client.post(f"{self.base_url}/v1/messages", json=payload, headers=self._headers())
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

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        messages = list(request.messages)
        schema_json = json.dumps(schema.model_json_schema() if hasattr(schema, 'model_json_schema') else {})
        messages.append({"role": "user", "content": f"Respond with valid JSON matching this schema: {schema_json}"})
        mr = ModelRequest(
            messages=messages, system=request.system,
            temperature=request.temperature, max_tokens=request.max_tokens,
        )
        resp = await self.complete(mr)
        return json.loads(resp.content)

    async def healthcheck(self) -> ModelHealth:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
                if resp.status_code == 200:
                    return ModelHealth(provider=self.name, available=True, model=self.model)
                return ModelHealth(provider=self.name, available=False, error=f"HTTP {resp.status_code}")
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=str(e))
