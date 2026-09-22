"""Real TCP/httpx pools, with only an in-process ASGI Frontdoor."""

import asyncio

import httpx
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_sse_pool_isolated_and_admission_reusable(tmp_path, monkeypatch):
    from aletheia.gateway import frontdoor

    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(frontdoor, "_STREAM_MAX_CONNECTIONS", 2)
    monkeypatch.setattr(frontdoor, "_PROXY_MAX_CONNECTIONS", 2)
    monkeypatch.setattr(frontdoor, "_stream_client", None)
    monkeypatch.setattr(frontdoor, "_remote_auth_check", lambda _: None)
    monkeypatch.setattr(frontdoor, "emit_infra_event", lambda **_: None)
    monkeypatch.setattr(frontdoor, "list_infra_events", lambda **_: [])
    tasks = set()
    seen = []

    async def backend(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            path = head.split(b" ")[1]
            seen.append(head)
            if path.startswith((b"/api/operator/events/stream", b"/api/dev/reload/events", b"/api/negotiated")):
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n5\r\n:ok\n\n\r\n")
                await writer.drain()
                await reader.read()
            else:
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: 11\r\n\r\n{"ok":true}')
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            tasks.discard(task)

    server = await asyncio.start_server(backend, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    (tmp_path / "index.html").touch()
    app = frontdoor.create_app(static_dir=tmp_path, backend_url=url)
    streams = []
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=2), timeout=1, trust_env=False) as finite, frontdoor._get_stream_client() as streaming:
        monkeypatch.setattr(frontdoor, "_proxy_client", finite)
        monkeypatch.setattr(frontdoor, "_stream_client", streaming)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://frontdoor", trust_env=False) as client:
                async def snapshot():
                    return (await client.get("/_infra/status")).json()["proxy_capacity"]

                async def wait_streams(count):
                    async with asyncio.timeout(2):
                        while (await snapshot())["streaming"] != count:
                            await asyncio.sleep(0.01)

                # Neither known stream needs an Accept header to claim stream capacity.
                for path in ("operator/events/stream", "dev/reload/events"):
                    streams.append(asyncio.create_task(client.get(f"/api/{path}", headers={"Authorization": "test-only"})))
                await wait_streams(2)
                assert (await snapshot())["stream_admissions"] == 2
                async with asyncio.timeout(0.5):
                    finite_response, ready, infra, excess = await asyncio.gather(
                        client.get("/api/status"), client.get("/api/ready"), client.get("/_infra/readyz"),
                        client.get("/api/negotiated", headers={"Accept": "text/event-stream"}),
                    )
                assert finite_response.status_code == ready.status_code == infra.status_code == 200
                assert excess.status_code == 503
                assert excess.json()["reason"] == "frontdoor_stream_capacity_exhausted"
                assert excess.headers["Retry-After"] == "3"
                assert not any(b"/api/negotiated" in head for head in seen)
                assert any(b"authorization: test-only" in head.lower() for head in seen)
                cancelled = streams.pop()
                cancelled.cancel()
                await asyncio.gather(cancelled, return_exceptions=True)
                await wait_streams(1)
                streams.append(asyncio.create_task(client.get("/api/negotiated", headers={"Accept": "text/event-stream"})))
                await wait_streams(2)
                assert any(b"/api/negotiated" in head for head in seen)
                for task in streams:
                    task.cancel()
                await asyncio.gather(*streams, return_exceptions=True)
                assert (await snapshot())["active_requests"] == 0
        finally:
            for task in streams:
                task.cancel()
            await asyncio.gather(*streams, return_exceptions=True)
            server.close()
            await server.wait_closed()
            remaining = list(tasks)
            for task in remaining:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)
    assert not tasks
