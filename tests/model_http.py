"""In-memory HTTP boundary for real OpenAI SDK adapter tests."""

import json
from contextlib import contextmanager
from unittest.mock import patch

import httpx


@contextmanager
def openai_http(*, post=None, get=None):
    async def handle(request):
        if request.method == "POST" and post is not None:
            response = await post(
                str(request.url), json=json.loads(request.content), headers=request.headers,
            )
        elif request.method == "GET" and get is not None:
            response = await get(str(request.url), headers=request.headers)
        else:
            raise AssertionError(f"Unexpected model request: {request.method} {request.url}")
        return httpx.Response(response.status_code, json=response.json(), request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=300)
    with patch("aletheia.model_provider.openai_compatible.httpx_client", return_value=client):
        yield client
