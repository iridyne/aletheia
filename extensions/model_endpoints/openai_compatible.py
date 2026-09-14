"""OpenAI-compatible model provider (chat_completions and responses protocols)."""

import asyncio
import json
import logging

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, Omit

from aletheia.net import httpx_client, raise_for_status_without_secrets
from aletheia.config.secrets import secret_fingerprint
from aletheia.model_provider.base import (
    ModelHealth,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderResponseError,
    StructuredModelRequest,
    health_from_model_catalogue,
    structured_via_complete,
)
from aletheia.model_provider.registry import validate_provider_protocol
from aletheia.model_provider.registry import provider_config_protocol

# SDK debug request logs include prompts. Content-free telemetry belongs to the
# inference harness, including when the application's root logger is verbose.
logging.getLogger("openai._base_client").setLevel(logging.WARNING)


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
    detail = " ".join(str(error.get(key) or "") for key in ("code", "type", "message", "detail")).lower()
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


def _responses_reasoning_is_unsupported(response: httpx.Response) -> bool:
    """Recognize explicit rejection of the Responses API ``reasoning`` field.

    Some OpenAI-compatible endpoints accept the Responses protocol but do not
    implement the optional ``reasoning`` object. Retry once without it rather
    than hard-failing the whole call; ``text.format`` is never touched.
    """
    return (
        _optional_request_field_is_unsupported(response, "reasoning")
        or _optional_request_field_is_unsupported(response, "reasoning.effort")
    )


def _json_object_is_unsupported(response: httpx.Response) -> bool:
    return (
        _optional_request_field_is_unsupported(response, "response_format")
        or _optional_request_field_is_unsupported(response, "json_object")
        or _optional_request_field_is_unsupported(response, "json_schema")
    )


class OpenAICompatibleProvider(ModelProvider):
    name = "openai_compatible"

    @classmethod
    def from_config(cls, config, *, api_key=None):
        from aletheia.config.secrets import get_secret

        if api_key is None and config.api_key_secret_ref:
            api_key = get_secret(config.api_key_secret_ref)
        return cls(
            base_url=config.base_url, model=config.model, api_key=api_key,
            protocol=provider_config_protocol(cls.name, config),
        )

    def __init__(
        self, base_url: str, model: str = "default", protocol: str = "chat_completions", api_key: str | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.protocol = validate_provider_protocol("openai_compatible", protocol, explicit=True)
        self.api_key = api_key
        self.credential_fingerprint = secret_fingerprint(api_key)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _sdk_client(self) -> AsyncOpenAI:
        # Borrow the loop-owned pool; only ModelProvider.aclose closes it.
        client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "",
            admin_api_key="",
            organization="",
            project="",
            webhook_secret="",
            max_retries=0,
            http_client=self._get_http_client(httpx_client),
        )
        # SDK 2.x merges OPENAI_CUSTOM_HEADERS even with explicit defaults.
        # A configured endpoint must never inherit ambient routing/credentials.
        client._custom_headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else Omit(),
            "OpenAI-Organization": Omit(),
            "OpenAI-Project": Omit(),
        }
        return client

    async def _completion_response(self, payload: dict) -> httpx.Response:
        client = self._sdk_client()
        create = (
            client.responses.with_raw_response.create
            if self.protocol == "responses"
            else client.chat.completions.with_raw_response.create
        )
        try:
            return (await create(**payload, extra_headers=client._custom_headers)).http_response
        except APIStatusError as exc:
            # Keep the response for the existing bounded optional-field retry;
            # final failures are scrubbed before leaving this adapter.
            return exc.response
        except APIConnectionError as exc:
            exc.request.headers.clear()
            error_type = httpx.ReadTimeout if isinstance(exc, APITimeoutError) else httpx.ConnectError
            raise error_type("model endpoint network request failed") from None

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
        elif request.response_format == "json_schema" and request.structured_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.structured_schema_name,
                    "strict": True,
                    "schema": request.structured_schema,
                },
            }
        resp = await self._completion_response(payload)
        for _ in range(2):
            if "reasoning_effort" in payload and _reasoning_effort_is_unsupported(resp):
                payload = dict(payload)
                payload.pop("reasoning_effort", None)
            elif payload.get("response_format") is not None and _json_object_is_unsupported(resp):
                payload = dict(payload)
                payload.pop("response_format", None)
                if request.structured_schema is not None:
                    payload["messages"] = [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                f"Respond with valid JSON matching this schema: {json.dumps(request.structured_schema)}"
                            ),
                        },
                    ]
            else:
                break
            resp = await self._completion_response(payload)
        raise_for_status_without_secrets(resp)
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
        if request.reasoning_effort is not None:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        if request.response_format == "json_object":
            payload["text"] = {"format": {"type": "json_object"}}
        elif request.response_format == "json_schema" and request.structured_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.structured_schema_name,
                    "strict": True,
                    "schema": request.structured_schema,
                }
            }
        resp = await self._completion_response(payload)
        if "reasoning" in payload and _responses_reasoning_is_unsupported(resp):
            payload = {k: v for k, v in payload.items() if k != "reasoning"}
            resp = await self._completion_response(payload)
        raise_for_status_without_secrets(resp)
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
        return await structured_via_complete(self, request, schema)

    async def list_models(self) -> list[dict]:
        """Fetch available models from the OpenAI-compatible /models endpoint."""
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
                return health_from_model_catalogue(self.name, self.model, models, allow_default=True)
            return ModelHealth(
                provider=self.name,
                available=False,
                model=self.model,
                error="model catalogue unavailable; use explicit Ping to test inference",
            )
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=str(e))
