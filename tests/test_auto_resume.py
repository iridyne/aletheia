"""Tests for auto-resume probe loop."""
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from aletheia.agent_runtime.auto_resume import _auto_resume_loop, start_auto_resume_task


def _make_config(paused=True, auto_resume_enabled=True):
    cfg = MagicMock()
    cfg.runtime.paused = paused
    cfg.health.auto_resume_enabled = auto_resume_enabled
    cfg.health.auto_resume_interval_sec = 1
    return cfg


class TestAutoResumeLoop:
    @pytest.mark.asyncio
    async def test_exits_when_already_resumed(self):
        cfg = _make_config(paused=False)
        with patch("aletheia.agent_runtime.auto_resume.load_config", return_value=cfg), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _auto_resume_loop(interval_sec=0)

    @pytest.mark.asyncio
    async def test_exits_when_disabled(self):
        cfg = _make_config(paused=True, auto_resume_enabled=False)
        with patch("aletheia.agent_runtime.auto_resume.load_config", return_value=cfg), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await _auto_resume_loop(interval_sec=0)

    @pytest.mark.asyncio
    async def test_resumes_on_llm_available(self):
        cfg = _make_config(paused=True)
        resume_result = {"allowed": True, "reason": None, "checked": 1, "results": []}

        call_count = 0

        async def mock_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise RuntimeError("loop should have exited")

        with patch("aletheia.agent_runtime.auto_resume.load_config", return_value=cfg), \
             patch("asyncio.sleep", side_effect=mock_sleep), \
             patch(
                 "aletheia.agent_runtime.auto_resume.resume_if_llm_available",
                 new_callable=AsyncMock, return_value=resume_result,
             ) as mock_resume:
            await _auto_resume_loop(interval_sec=1)
            mock_resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_when_llm_unavailable(self):
        cfg = _make_config(paused=True)
        unavailable = {"allowed": False, "reason": "llm_unavailable", "checked": 1, "results": []}
        available = {"allowed": True, "reason": None, "checked": 1, "results": []}

        call_count = 0

        async def mock_resume(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return available
            return unavailable

        with patch("aletheia.agent_runtime.auto_resume.load_config", return_value=cfg), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch(
                 "aletheia.agent_runtime.auto_resume.resume_if_llm_available",
                 side_effect=mock_resume,
             ):
            await _auto_resume_loop(interval_sec=1)
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_handles_probe_exception_gracefully(self):
        cfg_paused = _make_config(paused=True)
        cfg_resumed = _make_config(paused=False)
        configs = [cfg_paused, cfg_resumed]

        config_idx = 0

        def get_config():
            nonlocal config_idx
            c = configs[min(config_idx, len(configs) - 1)]
            config_idx += 1
            return c

        with patch("aletheia.agent_runtime.auto_resume.load_config", side_effect=get_config), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch(
                 "aletheia.agent_runtime.auto_resume.resume_if_llm_available",
                 new_callable=AsyncMock, side_effect=RuntimeError("network down"),
             ):
            await _auto_resume_loop(interval_sec=1)


class TestStartAutoResumeTask:
    @pytest.mark.asyncio
    async def test_starts_task(self):
        import aletheia.agent_runtime.auto_resume as mod
        mod._active_task = None

        with patch.object(mod, "_auto_resume_loop", new_callable=AsyncMock):
            task = start_auto_resume_task(interval_sec=1)
            assert task is not None
            assert not task.done()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_does_not_duplicate(self):
        import aletheia.agent_runtime.auto_resume as mod

        with patch.object(mod, "_auto_resume_loop", new_callable=AsyncMock):
            task1 = start_auto_resume_task(interval_sec=1)
            task2 = start_auto_resume_task(interval_sec=1)
            assert task1 is task2
            task1.cancel()
            try:
                await task1
            except asyncio.CancelledError:
                pass
            mod._active_task = None
