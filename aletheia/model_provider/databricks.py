"""Databricks Model Serving provider.

Uses the OpenAI-compatible chat/completions endpoint exposed by Databricks
serving endpoints. Auth is a Personal Access Token (PAT) sent as Bearer token.

Endpoint pattern: {workspace_url}/serving-endpoints/{endpoint_name}/invocations
The model field doubles as the endpoint_name for URL construction.
"""

import httpx

from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.base import ModelHealth


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
        base_url = f"{workspace_url.rstrip('/')}/serving-endpoints/{model}/invocations"
        super().__init__(base_url=base_url, model=model, protocol="chat_completions", api_key=api_key)
        self.workspace_url = workspace_url.rstrip("/")

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
        payload = {
            "messages": messages,
            "temperature": request.temperature,
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
