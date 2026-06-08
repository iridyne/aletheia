"""Databricks Model Serving provider.

Uses the OpenAI-compatible chat/completions endpoint exposed by Databricks
serving endpoints. Auth is a Personal Access Token (PAT) sent as Bearer token.

Endpoint pattern: {workspace_url}/serving-endpoints/{endpoint_name}/invocations
The model field doubles as the endpoint_name for URL construction.
"""

from urllib.parse import urlparse

import httpx

from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.base import ModelHealth


def _normalize_workspace_url(raw: str) -> str:
    """Reduce any Databricks URL to its workspace root (scheme://host).

    Users commonly paste the URL they already have working elsewhere — e.g. the
    AI-Gateway URL ``https://<host>/ai-gateway/anthropic`` from their Claude/agent
    config, or a full ``…/serving-endpoints/<name>/invocations`` URL. A Databricks
    workspace root never carries a path, and this provider builds the
    ``/serving-endpoints/...`` and ``/api/2.0/...`` paths itself, so any path the
    user supplied is spurious and would 404. Strip it to the bare host.
    """
    parsed = urlparse(raw.strip())
    return f"{parsed.scheme}://{parsed.netloc}"


class DatabricksProvider(OpenAICompatibleProvider):
    name = "databricks"

    def __init__(self, workspace_url: str, model: str = "default",
                 api_key: str | None = None):
        if not workspace_url:
            raise ValueError("Databricks workspace_url is required")
        if not workspace_url.startswith(("https://", "http://")):
            raise ValueError(
                f"workspace_url must start with https:// (got: {workspace_url[:40]})"
            )
        workspace_url = _normalize_workspace_url(workspace_url)
        base_url = f"{workspace_url}/serving-endpoints/{model}/invocations"
        super().__init__(base_url=base_url, model=model, protocol="chat_completions", api_key=api_key)
        self.workspace_url = workspace_url

    async def healthcheck(self) -> ModelHealth:
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                resp = await client.get(
                    f"{self.workspace_url}/api/2.0/serving-endpoints",
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    return ModelHealth(provider=self.name, available=True, model=self.model)
                return ModelHealth(provider=self.name, available=False,
                                   error=f"HTTP {resp.status_code}")
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=str(e))

    async def _complete_chat(self, request) -> "ModelResponse":
        from aletheia.model_provider.base import ModelResponse
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        # NOTE: `temperature` is deliberately omitted. Some Databricks-hosted models
        # (e.g. claude-opus-4-8) hard-reject the temperature parameter with HTTP 400,
        # while others accept it. No Aletheia caller overrides the default temperature,
        # so dropping it lets every serving endpoint use its own default and keeps the
        # provider compatible across the full model catalogue.
        payload = {
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.workspace_url}/serving-endpoints/{self.model}/invocations",
                json=payload, headers=self._headers(),
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
