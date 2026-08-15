import json

import pytest
from httpx import ASGITransport, AsyncClient


def _app(content_type: str, body: bytes, *, status: int = 200, extra_headers=None):
    from aletheia.server.compression import JsonCompressionMiddleware

    async def endpoint(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode()), *(extra_headers or [])],
        })
        await send({"type": "http.response.body", "body": body})

    return JsonCompressionMiddleware(endpoint, minimum_size=64)


@pytest.mark.anyio
async def test_large_json_is_compressed_and_small_json_is_not():
    body = json.dumps({"items": ["x" * 20] * 20}).encode()
    async with AsyncClient(transport=ASGITransport(app=_app("application/json", body)), base_url="http://test") as client:
        compressed = await client.get("/", headers={"Accept-Encoding": "gzip"})
    async with AsyncClient(transport=ASGITransport(app=_app("application/json", b'{"ok":true}')), base_url="http://test") as client:
        small = await client.get("/", headers={"Accept-Encoding": "gzip"})

    assert compressed.headers["content-encoding"] == "gzip"
    assert compressed.headers["vary"] == "Accept-Encoding"
    assert json.loads(compressed.content) == json.loads(body)
    assert "content-encoding" not in small.headers


@pytest.mark.anyio
async def test_streaming_and_already_encoded_json_are_not_recompressed():
    body = b"{" + b'"item":"' + b"x" * 200 + b'"}'
    async def stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": body, "more_body": True})
        await send({"type": "http.response.body", "body": b"\n"})

    from aletheia.server.compression import JsonCompressionMiddleware

    async def encoded_endpoint(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json"), (b"content-encoding", b"identity")]})
        await send({"type": "http.response.body", "body": body})

    async with AsyncClient(transport=ASGITransport(app=JsonCompressionMiddleware(stream, minimum_size=1)), base_url="http://test") as client:
        sse = await client.get("/", headers={"Accept-Encoding": "gzip"})
    async with AsyncClient(transport=ASGITransport(app=JsonCompressionMiddleware(encoded_endpoint, minimum_size=1)), base_url="http://test") as client:
        encoded = await client.get("/", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in sse.headers
    assert sse.content.endswith(b"\n")
    assert encoded.headers["content-encoding"] == "identity"
