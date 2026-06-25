"""Tests for exchange connectivity probe and auto-pause/resume logic."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def test_probe_exchange_returns_expected_shape():
    """probe_exchange returns dict with reachable, latency_ms, error, exchange."""
    from aletheia.agent_runtime.connectivity import probe_exchange
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.exchange.name = "okx"

    # Don't mock HTTP — just verify return shape. Network may or may not work.
    result = asyncio.run(probe_exchange(cfg))

    assert "reachable" in result
    assert "exchange" in result
    assert result["exchange"] == "okx"
    assert isinstance(result["reachable"], bool)
    if result["reachable"]:
        assert result["latency_ms"] is not None
    else:
        assert result["error"] is not None


def test_config_schema_has_exchange_probe_fields():
    """HealthConfig has the 4 exchange connectivity fields."""
    from aletheia.config.schema import HealthConfig

    h = HealthConfig()
    assert h.exchange_probe_interval_sec == 30
    assert h.exchange_probe_failure_threshold == 3
    assert h.exchange_auto_resume_interval_sec == 60
    assert h.exchange_auto_resume_success_threshold == 2


def test_connectivity_module_get_status():
    """get_status returns structured dict."""
    from aletheia.agent_runtime.connectivity import get_status

    s = get_status()
    assert "consecutive_failures" in s
    assert "consecutive_successes" in s
    assert "resume_task_active" in s


def test_exchange_endpoints_has_common_exchanges():
    """_EXCHANGE_ENDPOINTS covers OKX, Bitget, Binance."""
    from aletheia.agent_runtime.connectivity import _EXCHANGE_ENDPOINTS

    assert "okx" in _EXCHANGE_ENDPOINTS
    assert "bitget" in _EXCHANGE_ENDPOINTS
    assert "binance" in _EXCHANGE_ENDPOINTS


def test_ccxt_timeout_options():
    """ccxt_timeout_options returns a valid timeout dict."""
    from aletheia.net import ccxt_timeout_options

    opts = ccxt_timeout_options()
    assert opts == {"timeout": 15000}


def test_aiohttp_session_has_timeout():
    """aiohttp_session() includes a ClientTimeout by default."""
    import aiohttp
    from aletheia.net import aiohttp_session

    async def _check():
        session = aiohttp_session()
        try:
            assert session.timeout is not None
            assert session.timeout.total == 15
            assert session.timeout.connect == 8
        finally:
            await session.close()

    asyncio.run(_check())


def test_scheduler_registers_connectivity_monitor():
    """The connectivity_monitor job ID is in _ASYNC_JOB_TIMEOUT_SEC."""
    from aletheia.agent_runtime.scheduler import _ASYNC_JOB_TIMEOUT_SEC

    assert "connectivity_monitor" in _ASYNC_JOB_TIMEOUT_SEC


def test_health_uses_generic_exchange_probe():
    """compute_health uses exchange_unreachable (not okx_unreachable)."""
    import inspect
    from aletheia.agent_runtime import health

    src = inspect.getsource(health.compute_health)
    assert "exchange_unreachable" in src
    assert "okx_unreachable" not in src
