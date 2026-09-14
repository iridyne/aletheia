"""Anthropic-compatible model provider."""

import asyncio
import logging

import httpx
from anthropic import APIConnectionError, APIStatusError, APITimeoutError, AsyncAnthropic, Omit

from aletheia.net import httpx_client, raise_for_status_without_secrets
from aletheia.model_provider.base import (
    ModelHealth,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    StructuredModelRequest,
    health_from_model_catalogue,
    structured_via_complete,
)
from aletheia.model_provider.registry import validate_provider_protocol
from aletheia.model_provider.http_runtime import http_response_facts

# SDK debug request logs contain prompts; the harness owns content-free telemetry.
logging.getLogger("anthropic._base_client").setLevel(logging.WARNING)


class AnthropicCompatibleProvider(ModelProvider):
    name = "anthropic_compatible"

    def __init__(
        self,
        base_url: str,
        model: str = "default",
        api_key: str | None = None,
        auth_scheme: str = "x-api-key",
        protocol: str = "anthropic_messages",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.protocol = validate_provider_protocol(
            "anthropic_compatible", protocol, explicit=True
        )
        # Native Anthropic uses the x-api-key header. Gateway-fronted Anthropic
        # endpoints (e.g. Databricks AI Gateway /ai-gateway/anthropic, which issues
        # a `dapi…` PAT) authenticate with `Authorization: Bearer` instead and reject
        # x-api-key with 401. Make the scheme selectable; default preserves native.
        self.auth_scheme = auth_scheme

    def _sdk_client(self) -> AsyncAnthropic:
        # Explicit empty credentials disable SDK environment/profile resolution;
        # Omit below prevents empty or fabricated authentication headers.
        client = AsyncAnthropic(
            base_url=self.base_url,
            api_key=self.api_key if self.auth_scheme != "bearer" and self.api_key else "",
            auth_token=self.api_key if self.auth_scheme == "bearer" and self.api_key else "",
            webhook_key="",
            max_retries=0,
            http_client=self._get_http_client(httpx_client),
        )
        # SDK 0.x merges ANTHROPIC_CUSTOM_HEADERS despite explicit defaults.
        client._custom_headers = {
            "Authorization": f"Bearer {self.api_key}" if self.auth_scheme == "bearer" and self.api_key else Omit(),
            "X-Api-Key": self.api_key if self.auth_scheme != "bearer" and self.api_key else Omit(),
        }
        return client

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload: dict = {
            "model": self.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
        }
        # `temperature` is omitted for gateway-fronted (bearer) endpoints. Some
        # Databricks-hosted models (e.g. claude-opus-4-8) hard-reject temperature
        # with HTTP 400 ("temperature is deprecated for this model"), and Aletheia
        # agents request temperature=0 for deterministic structured output — which
        # would fail every call. Direct
        # Anthropic (x-api-key) still honours temperature.
        if self.auth_scheme != "bearer":
            payload["temperature"] = request.temperature
        if request.system:
            payload["system"] = request.system
        if request.response_format == "json_schema" and request.structured_schema is not None:
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request.structured_schema,
                }
            }
        client = self._sdk_client()
        try:
            resp = (await client.messages.with_raw_response.create(
                **payload, extra_headers=client._custom_headers,
            )).http_response
        except APIStatusError as exc:
            resp = exc.response
        except APIConnectionError as exc:
            exc.request.headers.clear()
            error_type = httpx.ReadTimeout if isinstance(exc, APITimeoutError) else httpx.ConnectError
            raise error_type("model endpoint network request failed") from None
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
            **http_response_facts(data),
        )

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        return await structured_via_complete(self, request, schema)

    async def list_models(self) -> list[dict]:
        """Fetch available models from the gateway's /v1/models endpoint."""
        try:
            client = self._sdk_client()
            async with asyncio.timeout(4):
                resp = (await client.models.with_raw_response.list(extra_headers=client._custom_headers)).http_response
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
                return health_from_model_catalogue(
                    self.name, self.model, models, allow_default=True
                )
            return ModelHealth(
                provider=self.name,
                available=False,
                model=self.model,
                error="model catalogue unavailable; use explicit Ping to test inference",
            )
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=str(e))
