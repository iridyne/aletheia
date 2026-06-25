"""Integration tests for the auto-resume probe loop.

The loop's whole job is to recover the system from a paused state: probe the LLM
periodically and, on success, drive the REAL resume path (persist paused=False,
resume scheduler, audit). These tests therefore mock ONLY the network boundary
(`probe_llm_providers_at_startup`) and let `resume_if_llm_available` run for real,
asserting on the PERSISTED config + audit log — the actual recovery behavior, not
just that a mock was called.

Pattern mirrors test_startup_gate.py (real config/DB, fake provider probe).
"""

import asyncio
import sqlite3

import pytest

from aletheia.config.schema import AletheiaConfig, ProviderConfig


def setup_env(tmp_path, monkeypatch):
    """Point DATA_DIR/CONFIG_PATH at tmp and init the DB so write_audit/save_config work."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _save_paused_cfg(auto_resume_enabled=True, require_llm=True):
    """Persist a paused config to disk (the loop reads from disk each iteration)."""
    from aletheia.config.loader import save_config
    cfg = AletheiaConfig()
    cfg.runtime.paused = True
    cfg.health.auto_resume_enabled = auto_resume_enabled
    cfg.health.auto_resume_interval_sec = 1
    cfg.health.require_llm_at_startup = require_llm
    cfg.models.providers = {"anthropic_compatible": ProviderConfig(type="anthropic_compatible", enabled=True)}
    cfg.models.default_provider = "anthropic_compatible"
    save_config(cfg)
    return cfg


def _bounded_sleep(monkeypatch, max_iters):
    """Patch asyncio.sleep to a no-op that aborts a runaway loop instead of hanging.

    The loop must exit via its real conditions within max_iters; if it doesn't,
    the test fails loudly (raised out of the un-try/except'd sleep) rather than
    hanging the suite.
    """
    state = {"n": 0}

    async def _sleep(_):
        state["n"] += 1
        if state["n"] > max_iters:
            raise AssertionError(f"auto_resume loop did not exit within {max_iters} iterations")

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return state


def _patch_probe(monkeypatch, sequence):
    """Patch the LLM probe (network boundary) to return verdicts from `sequence`.

    Each call pops the next verdict; the last one repeats. A verdict is a bool
    (usable?) turned into the dict shape probe_llm_providers_at_startup returns.
    Returns a counter dict so tests can assert how many times the probe ran.
    """
    from aletheia.agent_runtime import resume_gate
    calls = {"n": 0}

    async def fake_probe(_cfg):
        i = min(calls["n"], len(sequence) - 1)
        usable = sequence[i]
        calls["n"] += 1
        return {
            "any_usable": usable,
            "checked": 1,
            "results": [{"provider": "anthropic_compatible", "usable": usable,
                         "error": None if usable else "connection refused"}],
        }

    monkeypatch.setattr(resume_gate, "probe_llm_providers_at_startup", fake_probe)
    return calls


def _audit_events(event_type):
    from aletheia.files.jsonl import read_jsonl
    return [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == event_type]


# ---------------------------------------------------------------------------
# 1. LLM recovers on first probe → real resume: paused flips to False on disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resumes_when_llm_recovers(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_paused_cfg()
    _bounded_sleep(monkeypatch, max_iters=5)
    calls = _patch_probe(monkeypatch, [True])

    from aletheia.agent_runtime.auto_resume import _auto_resume_loop
    from aletheia.config.loader import load_config

    await _auto_resume_loop(interval_sec=1)

    # The REAL resume path ran and persisted the new state to disk.
    assert load_config().runtime.paused is False
    assert calls["n"] == 1  # probed once, succeeded, exited


# ---------------------------------------------------------------------------
# 2. Stays paused while LLM down, resumes once it recovers; blocked audits written
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stays_paused_until_llm_recovers(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_paused_cfg()
    _bounded_sleep(monkeypatch, max_iters=10)
    # Unavailable for the first two probes, then usable on the third.
    calls = _patch_probe(monkeypatch, [False, False, True])

    from aletheia.agent_runtime.auto_resume import _auto_resume_loop
    from aletheia.config.loader import load_config

    await _auto_resume_loop(interval_sec=1)

    # Resumed only after recovery.
    assert load_config().runtime.paused is False
    assert calls["n"] == 3
    # The two failed probes each wrote a gateway_resume_blocked audit event.
    blocked = _audit_events("gateway_resume_blocked")
    assert len(blocked) == 2


# ---------------------------------------------------------------------------
# 3. Manual resume detected → loop exits WITHOUT probing or touching state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exits_on_manual_resume_without_probing(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    cfg = _save_paused_cfg()
    # Operator resumed manually between scheduling and first probe.
    cfg.runtime.paused = False
    from aletheia.config.loader import save_config
    save_config(cfg)
    _bounded_sleep(monkeypatch, max_iters=5)
    calls = _patch_probe(monkeypatch, [True])

    from aletheia.agent_runtime.auto_resume import _auto_resume_loop
    await _auto_resume_loop(interval_sec=1)

    # Loop saw paused=False at the top and exited — never probed.
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# 4. auto_resume disabled → loop exits, system STAYS paused (no recovery)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disabled_stays_paused(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_paused_cfg(auto_resume_enabled=False)
    _bounded_sleep(monkeypatch, max_iters=5)
    calls = _patch_probe(monkeypatch, [True])

    from aletheia.agent_runtime.auto_resume import _auto_resume_loop
    from aletheia.config.loader import load_config

    await _auto_resume_loop(interval_sec=1)

    # Disabled → exited before probing; system remains paused (no auto-recovery).
    assert calls["n"] == 0
    assert load_config().runtime.paused is True


# ---------------------------------------------------------------------------
# 5. LLM gate disabled by config → resume without probing the network at all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resumes_without_probe_when_gate_disabled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_paused_cfg(require_llm=False)
    _bounded_sleep(monkeypatch, max_iters=5)
    calls = _patch_probe(monkeypatch, [False])  # would block IF probed

    from aletheia.agent_runtime.auto_resume import _auto_resume_loop
    from aletheia.config.loader import load_config

    await _auto_resume_loop(interval_sec=1)

    # require_llm_at_startup=False → resume_if_llm_available short-circuits to
    # allowed without ever probing, and persists the resume.
    assert load_config().runtime.paused is False
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# 6. A probe exception is swallowed; loop survives and resumes on a later success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_exception_does_not_kill_loop(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_paused_cfg()
    _bounded_sleep(monkeypatch, max_iters=10)

    from aletheia.agent_runtime import resume_gate
    calls = {"n": 0}

    async def flaky_probe(_cfg):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down")  # transient blip
        return {"any_usable": True, "checked": 1,
                "results": [{"provider": "anthropic_compatible", "usable": True, "error": None}]}

    monkeypatch.setattr(resume_gate, "probe_llm_providers_at_startup", flaky_probe)

    from aletheia.agent_runtime.auto_resume import _auto_resume_loop
    from aletheia.config.loader import load_config

    await _auto_resume_loop(interval_sec=1)

    # First iteration raised inside resume_if_llm_available → caught by the loop's
    # try/except; second iteration succeeded and resumed.
    assert calls["n"] == 2
    assert load_config().runtime.paused is False


# ---------------------------------------------------------------------------
# Task management: real dedup behavior (not mock wiring)
# ---------------------------------------------------------------------------

class TestStartAutoResumeTask:
    @pytest.mark.asyncio
    async def test_starts_task(self):
        import aletheia.agent_runtime.auto_resume as mod
        from unittest.mock import AsyncMock, patch
        mod._active_task = None
        with patch.object(mod, "_auto_resume_loop", new_callable=AsyncMock):
            task = mod.start_auto_resume_task(interval_sec=1)
            assert task is not None
            assert not task.done()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            mod._active_task = None

    @pytest.mark.asyncio
    async def test_does_not_duplicate(self):
        import aletheia.agent_runtime.auto_resume as mod
        from unittest.mock import AsyncMock, patch
        mod._active_task = None
        with patch.object(mod, "_auto_resume_loop", new_callable=AsyncMock):
            task1 = mod.start_auto_resume_task(interval_sec=1)
            task2 = mod.start_auto_resume_task(interval_sec=1)
            assert task1 is task2  # second call returns the already-active task
            task1.cancel()
            try:
                await task1
            except asyncio.CancelledError:
                pass
            mod._active_task = None
