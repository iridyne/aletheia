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
    ProviderResponseError,
    StructuredModelRequest,
    model_json_schema_payload,
)
from aletheia.model_provider.registry import validate_provider_protocol

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


def _extract_chat_completion_text(data: object) -> str:
    """Read chat-completion text from an OpenAI-shaped JSON body.

    SenseNova GLM-5.2 has returned HTTP 200 JSON without ``choices``. Raising
    ``ProviderResponseError`` lets request-level fallback try the next model
    instead of aborting the chain on a raw KeyError.
    """
    if not isinstance(data, dict):
        raise ProviderResponseError("openai_chat_response_not_object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderResponseError("openai_chat_missing_choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProviderResponseError("openai_chat_invalid_choice")
    message = choice.get("message")
    if isinstance(message, dict):
        return _extract_text_content(message.get("content"))
    text = choice.get("text")
    if isinstance(text, str) and text:
        return text
    raise ProviderResponseError("openai_chat_missing_message")


def _optional_request_field_is_unsupported(response: httpx.Response, field_name: str) -> bool:
    """Recognize only explicit request-field incompatibility responses.

    Some OpenAI-compatible endpoints do not implement optional fields such as
    ``reasoning_effort`` or ``response_format``. Retrying a read-only completion
    without that field is safe, but a generic 400/422 must retain its original
    failure semantics.
    """
    if response.status_code not in {400, 422}:
        return False
    try:
        data = response.json()
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    error = data.get("error")
    if not isinstance(error, dict):
        error = data
    needle = str(field_name or "").strip().lower()
    if not needle:
        return False
    parameter = str(error.get("param") or "").strip().lower()
    if parameter == needle:
        return True
    detail = " ".join(
        str(error.get(key) or "")
        for key in ("code", "type", "message", "detail")
    ).lower()
    if needle not in detail:
        return False
    return any(
        marker in detail
        for marker in (
            "unknown field",
            "unknown parameter",
            "unsupported",
            "not support",
            "unrecognized",
            "extra fields not permitted",
        )
    )


def _reasoning_effort_is_unsupported(response: httpx.Response) -> bool:
    return _optional_request_field_is_unsupported(response, "reasoning_effort")


def _json_object_is_unsupported(response: httpx.Response) -> bool:
    return _optional_request_field_is_unsupported(
        response, "response_format"
    ) or _optional_request_field_is_unsupported(response, "json_object")


class OpenAICompatibleProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str = "default", protocol: str = "chat_completions", api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.protocol = validate_provider_protocol(
            "openai_compatible", protocol, explicit=True
        )
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
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        client = self._get_http_client(httpx_client)
        resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
        for _ in range(2):
            if "reasoning_effort" in payload and _reasoning_effort_is_unsupported(resp):
                payload = dict(payload)
                payload.pop("reasoning_effort", None)
            elif payload.get("response_format") is not None and _json_object_is_unsupported(resp):
                payload = dict(payload)
                payload.pop("response_format", None)
            else:
                break
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
        _raise_without_secrets(resp)
        data = resp.json()
        content = _extract_chat_completion_text(data)
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
