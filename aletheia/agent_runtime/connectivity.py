"""Exchange connectivity monitor with auto-pause/resume.

Mirrors the LLM startup gate pattern: when the exchange API becomes unreachable,
pause the scheduler (trading is impossible without market data anyway) and start
an independent asyncio.Task that probes until connectivity returns, then resumes.

This module is NOT frozen — it monitors infrastructure, not AI decisions.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time

logger = logging.getLogger("aletheia.connectivity")

_consecutive_failures: int = 0
_consecutive_successes: int = 0
_last_probe_result: dict | None = None
_last_external_probe_result: dict | None = None
_external_outage_started_at: float | None = None
_external_last_recovery_at: float | None = None
_active_resume_task = None  # asyncio.Task | concurrent.futures.Future | None
_uvicorn_loop: asyncio.AbstractEventLoop | None = None  # persistent loop, captured at startup (#145)

_EXCHANGE_ENDPOINTS: dict[str, str] = {
    "okx": "https://www.okx.com/api/v5/public/time",
    "bitget": "https://api.bitget.com/api/v2/public/time",
    "binance": "https://api.binance.com/api/v3/ping",
}
_EXTERNAL_PROBE_ENDPOINTS = (
    "https://api.coingecko.com/api/v3/ping",
    "https://www.cloudflare.com/cdn-cgi/trace",
)
_EXTERNAL_RECOVERY_GRACE_SEC = 20 * 60


def set_uvicorn_loop(loop) -> None:
    """Capture the persistent uvicorn event loop so the auto-resume task can be
    scheduled onto it (#145). The periodic connectivity check runs on an ephemeral
    per-job loop that is torn down as soon as the job returns, so a resume task
    created there would be destroyed pending and never fire — only a startup-time
    pause (already on the uvicorn loop) auto-resumed."""
    global _uvicorn_loop
    _uvicorn_loop = loop


async def stop_exchange_auto_resume_task() -> None:
    """Stop any exchange recovery loop, including a cross-thread future."""
    global _active_resume_task, _uvicorn_loop
    task = _active_resume_task
    _active_resume_task = None
    _uvicorn_loop = None
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        if isinstance(task, asyncio.Future):
            await task
        else:
            await asyncio.wrap_future(task)
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        pass
    except Exception as exc:
        logger.debug("exchange_auto_resume: task ended during shutdown: %s", exc)


def _choose_resume_scheduling(uvicorn_loop, running_loop) -> str:
    """Decide how to schedule the auto-resume loop so it survives (#145).

    - "threadsafe": a distinct, running persistent loop is captured — schedule onto
      it from this (ephemeral/worker) loop via run_coroutine_threadsafe.
    - "create_task": we are already on a persistent loop (startup gate) or no loop
      was captured — create the task on the current loop.
    - "none": no loop available at all.
    """
    if uvicorn_loop is not None and uvicorn_loop is not running_loop \
            and getattr(uvicorn_loop, "is_running", lambda: False)():
        return "threadsafe"
    if running_loop is not None:
        return "create_task"
    return "none"


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


async def probe_external_network() -> dict:
    """Probe internet/proxy egress independently from any configured exchange.

    Any non-5xx HTTP response proves that DNS, TCP/TLS, and the configured egress
    path work. The second endpoint is tried only when the first cannot establish
    that fact, keeping the normal probe to one lightweight request.
    """
    from aletheia.net import httpx_client

    started = time.monotonic()
    last_error_code: str | None = None
    try:
        async with asyncio.timeout(5):
            async with httpx_client(timeout=5, retry=0) as client:
                for url in _EXTERNAL_PROBE_ENDPOINTS:
                    try:
                        response = await client.get(url)
                        if response.status_code < 500:
                            return {
                                "reachable": True,
                                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                                "error_code": None,
                            }
                        last_error_code = f"HTTP_{response.status_code}"
                    except Exception as exc:  # noqa: BLE001 - record only the safe type name
                        last_error_code = type(exc).__name__
    except Exception as exc:  # noqa: BLE001 - status uses type only, never raw URL/error
        last_error_code = type(exc).__name__
    return {
        "reachable": False,
        "latency_ms": None,
        "error_code": last_error_code or "external_probe_failed",
    }


async def _probe_exchange_and_external(cfg) -> tuple[dict, dict]:
    exchange_result, external_result = await asyncio.gather(
        probe_exchange(cfg),
        probe_external_network(),
    )
    # A successful exchange response itself proves external egress even if a
    # neutral probe endpoint happens to be unavailable.
    if exchange_result["reachable"] and not external_result["reachable"]:
        external_result = {
            "reachable": True,
            "latency_ms": exchange_result.get("latency_ms"),
            "error_code": None,
        }
    return exchange_result, external_result


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


def _record_probe_result(result: dict, *, observed_at: float | None = None) -> dict:
    """Record the exchange probe without changing its pause/resume semantics."""
    global _consecutive_failures, _consecutive_successes, _last_probe_result

    observed_at = time.time() if observed_at is None else observed_at
    _last_probe_result = {**result, "ts": observed_at}
    if result["reachable"]:
        _consecutive_failures = 0
        _consecutive_successes += 1
    else:
        _consecutive_successes = 0
        _consecutive_failures += 1
    return _last_probe_result


def _record_external_probe_result(
    result: dict,
    *,
    observed_at: float | None = None,
) -> dict:
    """Record independent internet egress and emit only root-cause transitions."""
    global _last_external_probe_result
    global _external_outage_started_at, _external_last_recovery_at

    observed_at = time.time() if observed_at is None else observed_at
    previous_reachable = (
        _last_external_probe_result.get("reachable")
        if _last_external_probe_result is not None
        else None
    )
    _last_external_probe_result = {**result, "ts": observed_at}
    if result["reachable"]:
        if previous_reachable is False:
            _external_last_recovery_at = observed_at
            from aletheia.infra.events import emit_infra_event

            emit_infra_event(
                severity="info",
                source="connectivity_monitor",
                code="external_connectivity_restored",
                message="External connectivity is restored; dependent jobs will retry automatically.",
                action="No restart is required; wait for the next scheduled data refresh.",
                entity_id="external_network",
                dedupe_sec=300,
            )
    else:
        if previous_reachable is not False:
            _external_outage_started_at = observed_at
            from aletheia.infra.events import emit_infra_event

            emit_infra_event(
                severity="warning",
                source="connectivity_monitor",
                code="external_connectivity_unavailable",
                message="External network or the configured proxy is unavailable.",
                action="Check the network connection and local proxy process first.",
                entity_id="external_network",
                dedupe_sec=300,
            )
    return _last_external_probe_result


def _external_connectivity_status(*, now: float, stale_after_sec: int) -> dict:
    probe = _last_external_probe_result
    common = {
        "outage_started_at": _external_outage_started_at,
        "last_recovery_at": _external_last_recovery_at,
        "stale_after_sec": stale_after_sec,
    }
    if probe is None or now - float(probe.get("ts") or 0) > stale_after_sec:
        return {
            **common,
            "state": "unknown",
            "severity": "info",
            "priority": 3,
            "message": "External connectivity has not been checked recently.",
            "action": "Wait for the next connectivity probe.",
            "observed_at": None,
        }

    observed_at = datetime.fromtimestamp(float(probe["ts"]), tz=timezone.utc).isoformat()
    if not probe["reachable"]:
        return {
            **common,
            "state": "unavailable",
            "severity": "warning",
            "priority": 1,
            "message": "External network or the configured proxy is unavailable.",
            "action": "Check the network connection and local proxy process first.",
            "observed_at": observed_at,
        }
    if (
        _external_last_recovery_at is not None
        and now - _external_last_recovery_at <= _EXTERNAL_RECOVERY_GRACE_SEC
    ):
        return {
            **common,
            "state": "recovering",
            "severity": "info",
            "priority": 2,
            "message": "External connectivity is restored; dependent jobs are retrying.",
            "action": "No restart is required; wait for the next scheduled data refresh.",
            "observed_at": observed_at,
        }
    return {
        **common,
        "state": "reachable",
        "severity": "ok",
        "priority": 4,
        "message": "External connectivity is available.",
        "action": None,
        "observed_at": observed_at,
    }


def get_status() -> dict:
    """Return current connectivity state for API consumers."""
    try:
        from aletheia.config.loader import load_config

        interval_sec = int(load_config().health.exchange_probe_interval_sec)
    except Exception:
        interval_sec = 60
    stale_after_sec = max(30, interval_sec * 3)
    return {
        "consecutive_failures": _consecutive_failures,
        "consecutive_successes": _consecutive_successes,
        "last_probe": _last_probe_result,
        "external_connectivity": _external_connectivity_status(
            now=time.time(),
            stale_after_sec=stale_after_sec,
        ),
        "resume_task_active": _active_resume_task is not None and not _active_resume_task.done(),
    }


async def run_connectivity_check() -> dict:
    """Called by scheduler job every N seconds. Tracks failures and triggers pause.

    Returns the probe result augmented with action taken.
    """
    from aletheia.config.loader import load_config
    cfg = load_config()

    result, external_result = await _probe_exchange_and_external(cfg)
    _record_probe_result(result)
    _record_external_probe_result(external_result)

    if result["reachable"]:
        return {**result, "action": None}

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
    cfg.runtime.paused_by_connectivity = True
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

    # Start auto-resume loop on a loop that SURVIVES (#145). The periodic connectivity
    # check runs on an ephemeral per-job loop torn down right after the job returns, so
    # asyncio.get_running_loop().create_task() here would orphan the resume task. Schedule
    # onto the captured persistent uvicorn loop when we are not already running on it.
    if _active_resume_task is None or _active_resume_task.done():
        interval = cfg.health.exchange_auto_resume_interval_sec
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        mode = _choose_resume_scheduling(_uvicorn_loop, running)
        if mode == "threadsafe":
            _active_resume_task = asyncio.run_coroutine_threadsafe(
                _exchange_auto_resume_loop(interval), _uvicorn_loop,
            )
        elif mode == "create_task":
            _active_resume_task = running.create_task(
                _exchange_auto_resume_loop(interval), name="exchange_auto_resume",
            )
        else:
            logger.error("connectivity auto-resume: no event loop available to schedule resume task")


async def _exchange_auto_resume_loop(interval_sec: int = 60) -> None:
    """Probe exchange until connectivity returns, then resume scheduler."""
    global _consecutive_failures, _consecutive_successes

    successes_needed = 2  # default, overridden from config below
    loop_failures = 0
    last_escalation_at = 0

    while True:
        await asyncio.sleep(interval_sec)

        from aletheia.config.loader import load_config
        cfg = load_config()

        if not cfg.runtime.paused:
            logger.info("exchange_auto_resume: system already resumed — exiting")
            return

        successes_needed = cfg.health.exchange_auto_resume_success_threshold
        stall_threshold = cfg.health.exchange_stall_threshold

        result, external_result = await _probe_exchange_and_external(cfg)
        _record_probe_result(result)
        _record_external_probe_result(external_result)
        if result["reachable"]:
            loop_failures = 0
            logger.info(
                "exchange_auto_resume: probe OK (%d/%d)",
                _consecutive_successes, successes_needed,
            )
            if _consecutive_successes >= successes_needed:
                await _resume_from_network(cfg)
                return
        else:
            loop_failures += 1

            if loop_failures >= stall_threshold and (
                loop_failures - last_escalation_at >= stall_threshold
            ):
                _escalate_exchange_stall(cfg, result, loop_failures)
                last_escalation_at = loop_failures
            else:
                logger.info(
                    "exchange_auto_resume: still unreachable (%s), retry in %ds",
                    result.get("error", "?"), interval_sec,
                )


def _escalate_exchange_stall(cfg, last_result: dict, consecutive: int) -> None:
    """Notify operator about exchange connectivity stall."""
    error = last_result.get("error", "unknown")
    exchange = cfg.exchange.name
    message = (
        f"Exchange {exchange} unreachable for {consecutive} consecutive probes "
        f"({consecutive}min+). Last error: {error}. "
        f"Check network and proxy settings in Settings -> Network."
    )
    logger.warning("exchange_auto_resume stall: %s", message)
    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            "Exchange Auto-Resume Stalled",
            message,
            source="connectivity_monitor",
            event_type=None,
            audit=False,
            error_log=True,
        )
    except Exception:
        pass
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "system",
            "source": "connectivity_monitor",
            "event_type": "exchange_auto_resume_stall_escalated",
            "entity_type": "gateway",
            "entity_id": "gateway",
            "message": message,
            "metadata": {"exchange": exchange, "consecutive_failures": consecutive},
        })
    except Exception:
        pass


async def _resume_from_network(cfg) -> None:
    """Release the connectivity-owned pause after exchange recovery."""
    from aletheia.config.loader import load_config

    # Reload immediately before deciding: this task can outlive the outage that
    # created it, and a different gate/operator may now own the shared pause.
    cfg = load_config()
    if not cfg.runtime.paused_by_connectivity:
        logger.info("exchange_auto_resume: pause is not connectivity-owned — exiting")
        return

    # #146: the paused flag is shared with the LLM startup gate. If the LLM is still
    # unavailable, do NOT clear paused on exchange recovery — that would un-pause a
    # system the LLM gate still requires paused, and the LLM auto-resume loop would
    # then exit early seeing paused=False. Stay paused; the LLM auto-resume clears it
    # once the LLM is back (exchange is already up by then). Fail-safe: on any probe
    # error we proceed (pre-existing behaviour), never blocking a legitimate resume.
    try:
        if cfg.health.require_llm_at_startup:
            from aletheia.agent_runtime.startup_gate import probe_llm_providers_at_startup
            _llm = await probe_llm_providers_at_startup(cfg, max_retries=1)
            if not _llm.get("any_usable"):
                logger.info("exchange reachable again but LLM still unavailable — staying paused")
                return
    except Exception:
        pass  # intentional: a probe failure must not block a legitimate resume

    from aletheia.agent_runtime.scheduler import resume_scheduler
    from aletheia.config.loader import save_config
    from aletheia.infra.events import emit_infra_event
    from aletheia.files.jsonl import write_audit

    # Re-read after the asynchronous LLM probe so a newer pause cannot be
    # released by this older recovery task.
    cfg = load_config()
    if not cfg.runtime.paused_by_connectivity:
        logger.info("exchange_auto_resume: pause ownership changed — exiting")
        return
    cfg.runtime.paused = False
    cfg.runtime.paused_by_connectivity = False
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

    result, external_result = await _probe_exchange_and_external(cfg)
    _record_probe_result(result)
    _record_external_probe_result(external_result)
    if result["reachable"]:
        logger.info("startup exchange gate: %s reachable (%.0fms)", cfg.exchange.name, result.get("latency_ms") or 0)
        return

    logger.warning("startup exchange gate: %s unreachable — will pause", cfg.exchange.name)
    # Startup is an immediate gate, so preserve the historical threshold value
    # used by pause/audit messaging even though only one probe has run.
    global _consecutive_failures
    _consecutive_failures = cfg.health.exchange_probe_failure_threshold
    await _pause_for_network(cfg, result)
