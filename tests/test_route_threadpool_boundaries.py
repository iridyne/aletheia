"""Hot blocking routes must be delegated to FastAPI's sync threadpool."""

from __future__ import annotations

import inspect


def test_hot_sqlite_and_config_routes_are_sync_handlers():
    from aletheia.server.routes import data, health, model, settings

    handlers = [
        data.get_agent_runs,
        data.get_agent_run,
        data.get_agent_run_steps,
        data.get_agent_run_tool_calls,
        data.start_agent_run,
        health.health,
        health.status,
        health.stability,
        model.get_model_config,
        model.get_provider_types,
        settings.get_settings,
    ]

    assert all(not inspect.iscoroutinefunction(handler) for handler in handlers)
