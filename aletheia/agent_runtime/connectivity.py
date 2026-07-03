"""Exchange connectivity monitor with auto-pause/resume.

Mirrors the LLM startup gate pattern: when the exchange API becomes unreachable,
pause the scheduler (trading is impossible without market data anyway) and start
an independent asyncio.Task that probes until connectivity returns, then resumes.

This module is NOT frozen — it monitors infrastructure, not AI decisions.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("aletheia.connectivity")

_consecutive_failures: int = 0
_consecutive_successes: int = 0
_last_probe_result: dict | None = None
_active_resume_task: asyncio.Task | None = None

_EXCHANGE_ENDPOINTS: dict[str, str] = {
    "okx": "https://www.okx.com/api/v5/public/time",
    "bitget": "https://api.bitget.com/api/v2/public/time",
    "binance": "https://api.binance.com/api/v3/ping",
}


async def probe_exchange(cfg=None) -> dict:
    """Lightweight connectivity probe to the configured exchange's public API.

    Returns {reachable: bool, latency_ms: float|None, error: str|None, exchange: str}
    """
    if cfg is None:
        from aletheia.config.loader import load_config
        cfg = load_config()

    exchange_name = cfg.exchange.name
    url = _EXCHANGE_ENDPOINTS.get(exchange_name)
    if not url:
        return {"reachable": True, "latency_ms": None, "error": None, "exchange": exchange_name}

    try:
        from aletheia.net import httpx_client
        t0 = time.monotonic()
        async with httpx_client(timeout=8) as client:
            r = await client.get(url)
            latency = (time.monotonic() - t0) * 1000
            reachable = r.status_code == 200
            return {
                "reachable": reachable,
                "latency_ms": round(latency, 1),
                "error": None if reachable else f"HTTP {r.status_code}",
                "exchange": exchange_name,
            }
    except Exception as e:
        return {
            "reachable": False,
            "latency_ms": None,
            "error": str(e)[:200],
            "exchange": exchange_name,
        }


def probe_exchange_sync(cfg=None) -> dict:
    """Synchronous wrapper for use in compute_health() (called from scheduler thread)."""
    import asyncio as _asyncio
    coro = probe_exchange(cfg)
    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(coro)
    # Already on an event loop — run in a thread
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_asyncio.run, coro).result(timeout=12)


def get_status() -> dict:
    """Return current connectivity state for API consumers."""
    return {
        "consecutive_failures": _consecutive_failures,
        "consecutive_successes": _consecutive_successes,
        "last_probe": _last_probe_result,
        "resume_task_active": _active_resume_task is not None and not _active_resume_task.done(),
    }


async def run_connectivity_check() -> dict:
    """Called by scheduler job every N seconds. Tracks failures and triggers pause.

    Returns the probe result augmented with action taken.
    """
    global _consecutive_failures, _consecutive_successes, _last_probe_result

    from aletheia.config.loader import load_config
    cfg = load_config()

    result = await probe_exchange(cfg)
    _last_probe_result = {**result, "ts": time.time()}

    if result["reachable"]:
        _consecutive_failures = 0
        _consecutive_successes += 1
        return {**result, "action": None}

    _consecutive_successes = 0
    _consecutive_failures += 1
    logger.warning(
        "exchange probe failed (%d consecutive): %s",
        _consecutive_failures, result.get("error", "unknown"),
    )

    threshold = cfg.health.exchange_probe_failure_threshold
    if _consecutive_failures >= threshold and not cfg.runtime.paused:
        await _pause_for_network(cfg, result)
        return {**result, "action": "paused"}

    return {**result, "action": None}


async def _pause_for_network(cfg, probe_result: dict) -> None:
    """Pause the trading loop due to exchange unreachable. Start auto-resume."""
    global _active_resume_task

    from aletheia.agent_runtime.scheduler import pause_scheduler
    from aletheia.config.loader import save_config
    from aletheia.infra.events import emit_infra_event
    from aletheia.files.jsonl import write_audit

    logger.warning(
        "Exchange %s unreachable for %d consecutive probes — pausing scheduler",
        cfg.exchange.name, _consecutive_failures,
    )

    pause_scheduler()
    cfg.runtime.paused = True
    save_config(cfg)

    emit_infra_event(
        severity="critical",
        source="connectivity_monitor",
        code="exchange_unreachable_paused",
        message=f"Exchange {cfg.exchange.name} unreachable ({_consecutive_failures} probes) — trading paused",
        entity_id=cfg.exchange.name,
    )
    write_audit({
        "actor": "system",
        "source": "connectivity_monitor",
        "event_type": "exchange_connectivity_paused",
        "entity_type": "gateway",
        "entity_id": "gateway",
        "message": f"Paused: {cfg.exchange.name} unreachable — {probe_result.get('error', '')}",
    })

    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            "Exchange Connectivity Lost",
            f"Exchange {cfg.exchange.name} unreachable — trading paused. Auto-resume active.",
            level="critical",
        )
    except Exception:
        pass

    # Start auto-resume loop (independent of paused scheduler)
    if _active_resume_task is None or _active_resume_task.done():
        interval = cfg.health.exchange_auto_resume_interval_sec
        _active_resume_task = asyncio.get_running_loop().create_task(
            _exchange_auto_resume_loop(interval),
            name="exchange_auto_resume",
        )


async def _exchange_auto_resume_loop(interval_sec: int = 60) -> None:
    """Probe exchange until connectivity returns, then resume scheduler."""
    global _consecutive_failures, _consecutive_successes

    successes_needed = 2  # default, overridden from config below

    while True:
        await asyncio.sleep(interval_sec)

        from aletheia.config.loader import load_config
        cfg = load_config()

        if not cfg.runtime.paused:
            logger.info("exchange_auto_resume: system already resumed — exiting")
            return

        successes_needed = cfg.health.exchange_auto_resume_success_threshold

        result = await probe_exchange(cfg)
        if result["reachable"]:
            _consecutive_successes += 1
            _consecutive_failures = 0
            logger.info(
                "exchange_auto_resume: probe OK (%d/%d)",
                _consecutive_successes, successes_needed,
            )
            if _consecutive_successes >= successes_needed:
                await _resume_from_network(cfg)
                return
        else:
            _consecutive_successes = 0
            _consecutive_failures += 1
            logger.info(
                "exchange_auto_resume: still unreachable (%s), retry in %ds",
                result.get("error", "?"), interval_sec,
            )


async def _resume_from_network(cfg) -> None:
    """Resume scheduler after exchange connectivity is restored."""
    from aletheia.agent_runtime.scheduler import resume_scheduler
    from aletheia.config.loader import save_config
    from aletheia.infra.events import emit_infra_event
    from aletheia.files.jsonl import write_audit

    cfg.runtime.paused = False
    save_config(cfg)
    resume_scheduler()

    emit_infra_event(
        severity="info",
        source="connectivity_monitor",
        code="exchange_connectivity_resumed",
        message=f"Exchange {cfg.exchange.name} reachable again — trading resumed",
        entity_id=cfg.exchange.name,
    )
    write_audit({
        "actor": "system",
        "source": "connectivity_monitor",
        "event_type": "exchange_connectivity_resumed",
        "entity_type": "gateway",
        "entity_id": "gateway",
        "message": f"Resumed: {cfg.exchange.name} connectivity restored",
    })

    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            "Exchange Connectivity Restored",
            f"Exchange {cfg.exchange.name} reachable — trading resumed.",
            level="info",
        )
    except Exception:
        pass

    logger.info("exchange_auto_resume: connectivity restored — scheduler resumed")


async def startup_exchange_gate(cfg=None) -> None:
    """Post-startup exchange probe. If unreachable, pause immediately.

    Called from app.py lifespan after yield (same pattern as LLM startup gate).
    """
    if cfg is None:
        from aletheia.config.loader import load_config
        cfg = load_config()

    global _last_probe_result, _consecutive_failures, _consecutive_successes
    result = await probe_exchange(cfg)
    _last_probe_result = {**result, "ts": time.time()}
    if result["reachable"]:
        _consecutive_failures = 0
        _consecutive_successes += 1
        logger.info("startup exchange gate: %s reachable (%.0fms)", cfg.exchange.name, result.get("latency_ms", 0))
        return

    logger.warning("startup exchange gate: %s unreachable — will pause", cfg.exchange.name)
    _consecutive_failures = cfg.health.exchange_probe_failure_threshold
    await _pause_for_network(cfg, result)
