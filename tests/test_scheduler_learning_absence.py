"""Missing optional control cannot grant autonomous Learning execution."""

import pytest

pytestmark = pytest.mark.usefixtures("installed_learning_consumers")


@pytest.fixture
def missing_learning_control(monkeypatch, installed_learning_consumers):
    from aletheia.agent_runtime import scheduler
    for name in ("_learning_job_gate", "_learning_job_skipped", "_learning_job_started",
                 "_learning_job_succeeded", "_learning_job_failed"):
        monkeypatch.setattr(scheduler, name, None)


@pytest.mark.parametrize("missing_control", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_learning_gate_controls_real_producer(monkeypatch, request, missing_control, enabled):
    from aletheia.agent_runtime import scheduler
    from extensions.research import risk_critic
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage import repositories, sqlite

    cfg = load_config()
    cfg.agent.autonomous_learning = enabled
    cfg.agent.permission_mode = "Observe"
    cfg.runtime.paused = False
    save_config(cfg)
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    calls = []
    provider = object()
    monkeypatch.setattr(risk_critic, "get_provider_for_tier", lambda tier: provider)

    async def complete(**kwargs):
        assert kwargs["provider"] is provider
        calls.append("model")
        return "Position sizing crept up after wins; cap risk per trade to limit drawdown clustering.", None

    monkeypatch.setattr(risk_critic, "llm_complete", complete)
    if missing_control:
        request.getfixturevalue("missing_learning_control")
    scheduler._guarded("risk_critic", scheduler._run_risk_critic)()
    notes = repositories.list_knowledge_items(item_type="research_note")
    expected = (["model"], 1) if enabled and not missing_control else ([], 0)
    assert (calls, len(notes)) == expected


@pytest.mark.parametrize("job_id", [
    "dreaming_daily", "lesson_reviewer", "insight_synthesizer",
    "confidence_calibration", "post_trade_reviewer", "risk_critic",
    "guard_promotion", "filter_controller_observe", "persona_rollout",
    "learning_startup_catchup",
])
def test_missing_control_blocks_every_learning_job(monkeypatch, missing_learning_control, job_id):
    from aletheia.agent_runtime import scheduler

    calls = []
    monkeypatch.setitem(scheduler._consecutive_failures, job_id, 0)
    scheduler._run_job_guarded(job_id, lambda: calls.append(job_id))
    assert calls == []
    assert scheduler._consecutive_failures[job_id] == 1


@pytest.mark.parametrize("job_id", ["core_health_probe", "outcome_settler"])
def test_missing_control_preserves_core_and_measurement_jobs(missing_learning_control, job_id):
    from aletheia.agent_runtime import scheduler

    assert scheduler._run_job_guarded(job_id, lambda: "ok") == "ok"
