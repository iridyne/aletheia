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


def test_risk_route_handlers_are_sync():
    from aletheia.server.routes import risk

    handlers = [
        risk.get_risk_boundaries,
        risk.kill_switch_status,
        risk.brakes_status,
        risk.risk_status,
        risk.risk_summary,
        risk.set_kill_switch,
        risk.set_mode_brake,
        risk.account_leverage,
    ]
    assert all(not inspect.iscoroutinefunction(h) for h in handlers)


def test_prediction_route_handlers_are_sync():
    from aletheia.server.routes import prediction

    handlers = [
        prediction.get_prediction_opportunities,
        prediction.get_prediction_opportunity,
        prediction.get_prediction_status,
        prediction.toggle_prediction,
        prediction.get_prediction_markets,
        prediction.get_prediction_runs,
        prediction.get_prediction_events,
        prediction.get_prediction_event_detail,
        prediction.get_prediction_market_detail,
        prediction.get_prediction_market_claims,
        prediction.get_all_prediction_claims,
        prediction.get_prediction_preferences,
        prediction.update_prediction_preferences,
        prediction.trigger_prediction_refresh,
    ]
    assert all(not inspect.iscoroutinefunction(h) for h in handlers)


def test_persistence_gateway_and_lesson_handlers_are_sync():
    from aletheia.server.routes import persistence

    handlers = [
        persistence.get_gateway_status,
        persistence.list_gateway_sessions,
        persistence.list_reconciliation_reports,
        persistence.list_events,
        persistence.list_orders,
        persistence.list_positions,
        persistence.list_lessons,
        persistence.get_lesson,
        persistence.accept_lesson,
        persistence.reject_lesson,
        persistence.archive_lesson,
        persistence.list_skills,
        persistence.list_policies,
        persistence.diff_policy,
        persistence.get_policy,
        persistence.list_policy_versions,
    ]
    assert all(not inspect.iscoroutinefunction(h) for h in handlers)


def test_capital_route_handlers_are_sync():
    from aletheia.server.routes import capital

    handlers = [
        capital.get_net_deposits,
        capital.list_events,
        capital.create_event,
    ]
    assert all(not inspect.iscoroutinefunction(h) for h in handlers)
