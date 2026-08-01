"""Databricks Model Serving and AI Gateway provider.

The adapter keeps Databricks-specific URL construction out of the generic
OpenAI/Anthropic forms. It supports workspace serving endpoints and the
OpenAI/Anthropic Unity AI Gateway service routes with a Databricks bearer token.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from aletheia.net import httpx_client, raise_for_status_without_secrets
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.base import ModelHealth, ModelResponse


DATABRICKS_PROTOCOLS = {
    "serving_endpoint",
    "chat_completions",
    "responses",
    "anthropic_messages",
}


def _normalize_workspace_url(raw: str) -> str:
    """Reduce any Databricks URL to its workspace root (scheme://host)."""
    parsed = urlparse(raw.strip())
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_workspace_url(raw: str) -> str:
    """Return a normalized safe workspace origin or raise ``ValueError``.

    Databricks credentials must never be sent over plaintext to a remote host.
    HTTP remains available only for explicit loopback development endpoints.
    """
    if not raw:
        raise ValueError("Databricks workspace_url is required")
    parsed = urlparse(raw.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Databricks workspace_url must be an absolute HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Databricks workspace_url must use HTTPS for non-loopback hosts")
    return f"{parsed.scheme}://{parsed.netloc}"


class DatabricksProvider(OpenAICompatibleProvider):
    """Provider for a Databricks workspace and one endpoint/model selection."""

    name = "databricks"

    def __init__(
        self,
        workspace_url: str,
        model: str = "default",
        protocol: str = "serving_endpoint",
        api_key: str | None = None,
    ):
        if protocol not in DATABRICKS_PROTOCOLS:
            raise ValueError(f"Unsupported Databricks protocol: {protocol}")

        workspace_url = validate_workspace_url(workspace_url)
        if protocol == "serving_endpoint":
            base_url = f"{workspace_url}/serving-endpoints/{model}/invocations"
            parent_protocol = "chat_completions"
        elif protocol == "anthropic_messages":
            base_url = f"{workspace_url}/ai-gateway/anthropic"
            parent_protocol = "chat_completions"
        else:
            base_url = f"{workspace_url}/ai-gateway/openai/v1"
            parent_protocol = protocol

        super().__init__(
            base_url=base_url,
            model=model,
            protocol=parent_protocol,
            api_key=api_key,
        )
        self.workspace_url = workspace_url
        self.databricks_protocol = protocol

    async def list_models(self) -> list[dict]:
        """Fetch serving endpoints or the selected AI Gateway catalogue."""
        if self.databricks_protocol == "serving_endpoint":
            url = f"{self.workspace_url}/api/2.0/serving-endpoints"
        elif self.databricks_protocol == "anthropic_messages":
            url = f"{self.workspace_url}/ai-gateway/anthropic/v1/models"
        else:
            url = f"{self.workspace_url}/ai-gateway/openai/v1/models"

        try:
            client = self._get_http_client(httpx_client)
            async with asyncio.timeout(4):
                resp = await client.get(url, headers=self._databricks_headers())
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception:
            return []

        if self.databricks_protocol != "serving_endpoint":
            items = data if isinstance(data, list) else data.get("data") or []
            models = []
            for item in items:
                slug = item.get("id") if isinstance(item, dict) else item
                if slug:
                    models.append({"slug": str(slug), "display_name": str(slug)})
            return models

        models = []
        for endpoint in data.get("endpoints") or []:
            name = endpoint.get("name")
            if not name:
                continue
            state = endpoint.get("state", {}).get("ready", "")
            display = name if state == "READY" else f"{name} ({state.lower()})"
            models.append(
                {
                    "slug": name,
                    "display_name": display,
                    "ready_state": state or "UNKNOWN",
                }
            )
        return models

    async def healthcheck(self) -> ModelHealth:
        try:
            models = await self.list_models()
            if models:
                slugs = [model["slug"] for model in models]
                selected = next(
                    (model for model in models if model["slug"] == self.model),
                    None,
                )
                if self.databricks_protocol == "serving_endpoint" and self.model != "default" and selected is None:
                    return ModelHealth(
                        provider=self.name,
                        available=False,
                        model=self.model,
                        models=slugs,
                        model_metadata=models,
                        error="configured serving endpoint not found",
                    )
                if selected and selected.get("ready_state") not in (None, "READY"):
                    return ModelHealth(
                        provider=self.name,
                        available=False,
                        model=self.model,
                        models=slugs,
                        model_metadata=models,
                        error=f"endpoint state: {selected['ready_state'].lower()}",
                    )
                return ModelHealth(
                    provider=self.name,
                    available=True,
                    model=self.model,
                    models=slugs,
                    model_metadata=models,
                )
            return ModelHealth(
                provider=self.name,
                available=False,
                model=self.model,
                error="Databricks model catalogue unavailable; verify the endpoint directly",
            )
        except Exception as exc:
            return ModelHealth(provider=self.name, available=False, error=str(exc))

    def _databricks_headers(self, *, anthropic: bool = False) -> dict:
        headers = self._headers()
        if anthropic:
            headers["anthropic-version"] = "2023-06-01"
        return headers

    async def _complete_responses(self, request) -> ModelResponse:
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        client = self._get_http_client(httpx_client)
        resp = await client.post(
            f"{self.workspace_url}/ai-gateway/openai/v1/responses",
            json={
                "model": self.model,
                "input": messages,
                "max_output_tokens": request.max_tokens,
            },
            headers=self._databricks_headers(),
        )
        raise_for_status_without_secrets(resp)
        data = resp.json()
        content = ""
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    content += block.get("text", "")
        return ModelResponse(
            content=content or data.get("output_text", ""),
            model=data.get("model", self.model),
            usage=data.get("usage", {}),
            raw=data,
        )

    async def _complete_chat(self, request) -> ModelResponse:
        if self.databricks_protocol == "anthropic_messages":
            return await self._complete_anthropic(request)

        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        payload = {
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if self.databricks_protocol != "serving_endpoint":
            payload["model"] = self.model
            url = f"{self.workspace_url}/ai-gateway/openai/v1/chat/completions"
        else:
            url = f"{self.workspace_url}/serving-endpoints/{self.model}/invocations"

        client = self._get_http_client(httpx_client)
        resp = await client.post(
            url,
            json=payload,
            headers=self._databricks_headers(),
        )
        raise_for_status_without_secrets(resp)
        data = resp.json()
        choice = data["choices"][0]
        return ModelResponse(
            content=choice["message"]["content"],
            model=data.get("model", self.model),
            usage=data.get("usage", {}),
            raw=data,
        )

    async def _complete_anthropic(self, request) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
        }
        if request.system:
            payload["system"] = request.system
        client = self._get_http_client(httpx_client)
        resp = await client.post(
            f"{self.workspace_url}/ai-gateway/anthropic/v1/messages",
            json=payload,
            headers=self._databricks_headers(anthropic=True),
        )
        raise_for_status_without_secrets(resp)
        data = resp.json()
        content = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return ModelResponse(
            content=content,
            model=data.get("model", self.model),
            usage=data.get("usage", {}),
            raw=data,
        )
