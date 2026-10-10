"""Missing optional control cannot grant autonomous Learning execution."""

import pytest

pytestmark = pytest.mark.usefixtures("installed_learning_consumers")


@pytest.fixture
def missing_learning_control(monkeypatch, installed_learning_consumers):
    from aletheia.agent_runtime import scheduler
    for name in ("_learning_job_gate", "_learning_job_skipped", "_learning_job_started",
                 "_learning_job_succeeded", "_learning_job_failed"):
        monkeypatch.setattr(scheduler, name, None)


@pytest.mark.parametrize("job_id", [
    "dreaming_daily", "lesson_reviewer", "insight_synthesizer",
    "confidence_calibration", "post_trade_reviewer",
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
