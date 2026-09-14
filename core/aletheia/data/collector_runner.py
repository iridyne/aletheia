"""Standalone data-collector subprocess — supervised by _gateway_runner.py.

Runs the primary data_collectors (every 2 min) and context data_collector_context
(every 10 min) jobs in a tight loop.  Spawned and monitored by
_gateway_runner.run_supervised(); killed cleanly when the supervisor exits.

The supervisor sets ALETHEIA_COLLECTOR_PROCESS=1 in the uvicorn child's
environment so APScheduler's equivalents become no-ops — this process owns the
collection cadence while uvicorn is managed by the same supervisor.
"""
from __future__ import annotations

import logging
import os
import signal
import time

logger = logging.getLogger(__name__)

_CORE_INTERVAL_SEC = 120.0    # same cadence as the APScheduler data_collectors job
_CONTEXT_INTERVAL_SEC = 600.0  # same cadence as data_collector_context


def main() -> None:
    from aletheia.logging_config import configure_logging
    configure_logging()
    from aletheia.config.loader import bootstrap
    bootstrap()  # ensure data dirs and config exist

    # This process is a separate composition entrypoint, not the API process.
    try:
        from extensions.macro import register_collector
        register_collector()
    except ModuleNotFoundError:
        logger.warning("Optional Macro collector unavailable: module missing")

    try:
        from extensions.fred import register_collector
        register_collector()
    except ModuleNotFoundError:
        logger.warning("Optional FRED collector unavailable: module missing")

    try:
        from extensions.coinalyze import register_collector
        register_collector()
    except ModuleNotFoundError:
        logger.warning("Optional Coinalyze collector unavailable: module missing")

    try:
        from extensions.volume_scanner import register_collector
        register_collector()
    except ModuleNotFoundError:
        logger.warning("Optional volume scanner unavailable: module missing")

    try:
        from extensions.defillama import register_defillama_collector
        register_defillama_collector()
    except ModuleNotFoundError:
        logger.warning("Optional DefiLlama collector unavailable: module missing")

    try:
        from extensions.coingecko import register_top_collector
        register_top_collector()
    except ModuleNotFoundError:
        logger.warning("Optional CoinGecko Top collector unavailable: module missing")

    try:
        from extensions.hyperliquid import register_collector
        register_collector()
    except ModuleNotFoundError:
        logger.warning("Optional Hyperliquid collector unavailable: module missing")

    try:
        from extensions.deribit import register_collector
        register_collector()
    except ModuleNotFoundError:
        logger.warning("Optional Deribit collector unavailable: module missing")

    try:
        from extensions.market_data import register_market_data
    except ModuleNotFoundError:
        logger.warning("Optional public spot fallbacks unavailable: module missing")
    else:
        register_market_data()

    # Honour SIGTERM cleanly so the supervisor's kill_pid_tree gets a tidy exit.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))

    from aletheia.agent_runtime.scheduler import (
        _run_data_collectors,
        _run_data_collector_context,
    )

    context_last: float = 0.0
    logger.info("data-collector process started (pid=%d)", os.getpid())

    while True:
        tick_start = time.monotonic()

        try:
            _run_data_collectors()
        except Exception as exc:
            logger.warning("data_collectors tick failed: %s", exc)

        if time.monotonic() - context_last >= _CONTEXT_INTERVAL_SEC:
            try:
                _run_data_collector_context()
                context_last = time.monotonic()
            except Exception as exc:
                logger.warning("data_collector_context tick failed: %s", exc)

        elapsed = time.monotonic() - tick_start
        time.sleep(max(0.0, _CORE_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    main()
