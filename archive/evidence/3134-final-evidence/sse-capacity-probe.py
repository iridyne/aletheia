"""Bounded, read-only SSE capacity probe; prepared only, never self-authorizes runtime use."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from contextlib import suppress
from typing import Any

import httpx


COUNTERS = (
    "max_connections",
    "stream_max_connections",
    "stream_admissions",
    "pending_headers",
    "streaming",
    "non_sse",
    "active_requests",
    "truncated",
)
STREAM_PATH = "/api/operator/events/stream?limit=0"


def _safe_status(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in (401, 403):
        raise RuntimeError(f"authorization_required:{response.status_code}")
    elapsed = response.elapsed.total_seconds()
    if response.status_code != 200 or elapsed > 2.0:
        raise RuntimeError("finite_probe_failed")
    return {"status": 200, "elapsed": elapsed}


async def _infra(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    response = await client.get(url, timeout=2.0)
    if response.status_code in (401, 403):
        raise RuntimeError(f"authorization_required:{response.status_code}")
    if response.status_code != 200:
        return {"status": response.status_code}
    payload = response.json()
    counters = payload.get("proxy_capacity", {}) if isinstance(payload, dict) else {}
    return {"status": 200, "proxy_capacity": {k: counters.get(k) for k in COUNTERS}}


async def _holder(client: httpx.AsyncClient, base: str, opened: asyncio.Event) -> None:
    async with asyncio.timeout(15.0):
        async with client.stream("GET", base + STREAM_PATH, timeout=httpx.Timeout(2.0, read=15.0)) as response:
            if response.status_code in (401, 403):
                raise RuntimeError(f"authorization_required:{response.status_code}")
            if response.status_code != 200:
                raise RuntimeError(f"stream_admission:{response.status_code}")
            opened.set()
            await asyncio.sleep(15.0)


async def _probe(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    def budget() -> None:
        if time.monotonic() - started > 30.0:
            raise RuntimeError("experiment_deadline_exceeded")
    timeout = httpx.Timeout(2.0, read=15.0)
    async with httpx.AsyncClient(base_url=args.base, timeout=timeout) as client:
        baseline = await _infra(client, "/_infra/status")
        counters = baseline.get("proxy_capacity", {})
        if counters.get("stream_admissions") != 0 or counters.get("streaming") != 0:
            raise RuntimeError("baseline_stream_ownership_nonzero")
        tasks: list[asyncio.Task[None]] = []
        result = {}
        try:
            opened = [asyncio.Event() for _ in range(10)]
            for event in opened:
                tasks.append(asyncio.create_task(_holder(client, args.base, event)))
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in opened)), timeout=3.0)
            saturated = await _infra(client, "/_infra/status")
            if saturated.get("proxy_capacity", {}).get("stream_admissions") != 10 or saturated.get("proxy_capacity", {}).get("streaming") != 10:
                raise RuntimeError("ten_streams_not_admitted")
            probes = {}
            for path in ("/api/status", "/api/ready", "/_infra/readyz"):
                response = await client.get(path, timeout=2.0)
                probes[path] = _safe_status(response)
            backend = await client.get("http://127.0.0.1:8788/api/ready", timeout=2.0)
            probes["backend:/api/ready"] = _safe_status(backend)
            backend_infra = await client.get("http://127.0.0.1:8788/_infra/readyz", timeout=2.0)
            probes["backend:/_infra/readyz"] = _safe_status(backend_infra)
            async with client.stream("GET", args.base + STREAM_PATH, timeout=httpx.Timeout(2.0, read=2.0)) as eleventh:
                if eleventh.status_code in (401, 403):
                    raise RuntimeError(f"authorization_required:{eleventh.status_code}")
                body = await eleventh.aread()
                try:
                    contract = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    contract = {}
                if eleventh.status_code != 503 or eleventh.headers.get("retry-after") != "3" or eleventh.headers.get("x-aletheia-degraded") != "frontdoor_stream_capacity_exhausted" or contract.get("reason") != "frontdoor_stream_capacity_exhausted" or contract.get("retry_after_sec") != 3:
                    raise RuntimeError("eleventh_stream_contract_failed")
            tasks[0].cancel()
            with suppress(asyncio.CancelledError):
                await tasks[0]
            await asyncio.sleep(0.2)
            nine = await _infra(client, "/_infra/status")
            if nine.get("proxy_capacity", {}).get("stream_admissions") != 9:
                raise RuntimeError("cancel_did_not_release_one_slot")
            replacement_opened = asyncio.Event()
            replacement = asyncio.create_task(_holder(client, args.base, replacement_opened))
            tasks.append(replacement)
            await asyncio.wait_for(replacement_opened.wait(), timeout=3.0)
            ten = await _infra(client, "/_infra/status")
            if ten.get("proxy_capacity", {}).get("stream_admissions") != 10:
                raise RuntimeError("replacement_did_not_restore_capacity")
            budget()
            result.update(baseline=baseline, saturated=saturated, released=nine,
                          replacement=ten, eleventh_status=503, retry_after_sec=3,
                          probes=probes, elapsed=time.monotonic() - started)
            return result
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            final = None
            for _ in range(10):
                final = await _infra(client, "/_infra/status")
                if final.get("proxy_capacity", {}).get("stream_admissions") == 0:
                    break
                await asyncio.sleep(0.1)
            if final.get("proxy_capacity", {}).get("stream_admissions") != 0:
                raise RuntimeError("owned_streams_not_cleaned_up")
            result['final'] = final


async def bounded(args):
    async with asyncio.timeout(30):
        return await _probe(args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(bounded(args)), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
