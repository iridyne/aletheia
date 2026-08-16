"""Scoped compression for large JSON responses.

Only completed JSON bodies are compressed.  SSE, downloads, WebSockets, and
already encoded responses pass through untouched, so the middleware cannot
silently buffer the dashboard's streaming contracts.
"""

from __future__ import annotations

import gzip


_MINIMUM_BYTES = 1024


def _accepts_gzip(headers: list[tuple[bytes, bytes]]) -> bool:
    for name, value in headers:
        if name.lower() == b"accept-encoding":
            for token in value.decode("latin-1").split(","):
                encoding, _, params = token.strip().lower().partition(";")
                if encoding != "gzip":
                    continue
                quality = 1.0
                for parameter in params.split(";"):
                    key, _, raw = parameter.strip().partition("=")
                    if key == "q":
                        try:
                            quality = float(raw)
                        except ValueError:
                            quality = 0.0
                return quality > 0
    return False


class JsonCompressionMiddleware:
    """Compress eligible JSON responses without touching other body types."""

    def __init__(self, app, *, minimum_size: int = _MINIMUM_BYTES, compresslevel: int = 6):
        self.app = app
        self.minimum_size = max(0, int(minimum_size))
        self.compresslevel = max(1, min(int(compresslevel), 9))

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_headers = list(scope.get("headers") or [])
        response_start: dict | None = None
        body: list[bytes] = []
        buffering = False

        async def send_wrapper(message: dict) -> None:
            nonlocal response_start, buffering
            message_type = message.get("type")
            if message_type == "http.response.start":
                headers = list(message.get("headers") or [])
                header_map = {key.lower(): value for key, value in headers}
                content_type = header_map.get(b"content-type", b"").lower()
                already_encoded = b"content-encoding" in header_map
                buffering = (
                    message.get("status", 500) not in {204, 304}
                    and scope.get("method", "GET").upper() != "HEAD"
                    and content_type.startswith(b"application/json")
                    and not already_encoded
                    and b"text/event-stream" not in content_type
                    and _accepts_gzip(request_headers)
                )
                response_start = dict(message, headers=headers)
                if not buffering:
                    await send(message)
                return

            if message_type != "http.response.body" or not buffering:
                await send(message)
                return

            body.append(message.get("body", b""))
            if message.get("more_body"):
                return

            raw = b"".join(body)
            headers = list(response_start["headers"] if response_start else [])
            if len(raw) >= self.minimum_size:
                encoded = gzip.compress(raw, compresslevel=self.compresslevel, mtime=0)
                headers = [
                    (key, value)
                    for key, value in headers
                    if key.lower() not in {b"content-length", b"content-encoding"}
                ]
                headers.append((b"content-encoding", b"gzip"))
                headers = _with_vary(headers, b"Accept-Encoding")
                raw = encoded
            await send({**(response_start or {"type": "http.response.start", "status": 200}), "headers": headers})
            await send({"type": "http.response.body", "body": raw, "more_body": False})

        await self.app(scope, receive, send_wrapper)


def _with_vary(headers: list[tuple[bytes, bytes]], value: bytes) -> list[tuple[bytes, bytes]]:
    result: list[tuple[bytes, bytes]] = []
    found = False
    for key, current in headers:
        if key.lower() != b"vary":
            result.append((key, current))
            continue
        found = True
        values = [part.strip() for part in current.split(b",") if part.strip()]
        if value.lower() not in {part.lower() for part in values}:
            values.append(value)
        result.append((key, b", ".join(values)))
    if not found:
        result.append((b"vary", value))
    return result
