"""Focused regressions for runtime/self-heal fixes (batch B6).

#145 the connectivity auto-resume task must be scheduled on a loop that SURVIVES
     (the persistent uvicorn loop), not the ephemeral per-job loop.
#146 connectivity recovery must not clear the shared `paused` flag while the LLM
     is still down (fail-safe: stays paused).
"""


def test_choose_resume_scheduling_prefers_persistent_loop():
    from aletheia.agent_runtime.connectivity import _choose_resume_scheduling

    class _L:
        def __init__(self, running):
            self._r = running

        def is_running(self):
            return self._r

    persistent, ephemeral = _L(True), _L(True)
    # Called from the ephemeral per-job loop with a distinct running persistent loop
    # captured → schedule cross-loop so the resume task survives.
    assert _choose_resume_scheduling(persistent, ephemeral) == "threadsafe"
    # Already on the persistent loop (startup gate) → create task directly.
    assert _choose_resume_scheduling(persistent, persistent) == "create_task"
    # No captured loop but on some loop → best-effort create on it.
    assert _choose_resume_scheduling(None, ephemeral) == "create_task"
    # No loop at all → nothing to schedule on.
    assert _choose_resume_scheduling(None, None) == "none"
    # Captured loop not running → fall back to the current loop.
    assert _choose_resume_scheduling(_L(False), ephemeral) == "create_task"


def test_connectivity_resume_defers_when_llm_down(monkeypatch):
    import asyncio
    import aletheia.agent_runtime.connectivity as conn
    from aletheia.config.schema import AletheiaConfig

    async def _llm_down(cfg, max_retries=1):
        return {"any_usable": False, "checked": 1, "results": []}

    monkeypatch.setattr(
        "aletheia.agent_runtime.startup_gate.probe_llm_providers_at_startup", _llm_down
    )

    cfg = AletheiaConfig()
    cfg.runtime.paused = True
    cfg.health.require_llm_at_startup = True

    asyncio.run(conn._resume_from_network(cfg))
    # #146: exchange came back but the LLM is still down → stay paused (do not un-pause
    # a system the LLM gate still requires paused).
    assert cfg.runtime.paused is True
