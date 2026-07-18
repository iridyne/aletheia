"""AT-02 scheduler separation and restart-safe measurement checks."""

from __future__ import annotations

def _cfg(*, autonomous: bool = False, lifecycle: bool = False, paper: bool = False):
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.runtime.scheduler_enabled = True
    cfg.runtime.paused = False
    cfg.agent.autonomous_learning = autonomous
    cfg.learning.lifecycle_automation_enabled = lifecycle
    cfg.learning.paper_influence_enabled = paper
    cfg.controller.shadow_enabled = False
    cfg.historical_data.nightly_backfill_enabled = False
    cfg.historical_data.enabled = False
    cfg.historical_data.active_universe_backfill_enabled = False
    return cfg


def test_measurement_job_runs_when_autonomous_learning_is_disabled(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    import aletheia.storage.sqlite as sqlite

    monkeypatch.setattr(control, "load_config", lambda: _cfg(autonomous=False))
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    calls: list[str] = []
    scheduler._run_job_guarded("learning_contribution_evaluator", lambda: calls.append("measure"))
    scheduler._run_job_guarded("learning_lifecycle_controller", lambda: calls.append("control"))

    assert calls == ["measure"]
    assert "learning_lifecycle_controller" in control.AUTONOMOUS_LEARNING_JOBS
    assert "learning_contribution_evaluator" not in control.AUTONOMOUS_LEARNING_JOBS


def test_controller_is_skipped_for_global_integrity_or_runtime_pause(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    import aletheia.storage.sqlite as sqlite

    cfg = _cfg(autonomous=True, lifecycle=True, paper=True)
    monkeypatch.setattr(control, "load_config", lambda: cfg)
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    calls: list[str] = []
    monkeypatch.setattr(control, "global_learning_refreeze_reason", lambda: "incident")
    scheduler._run_job_guarded("learning_lifecycle_controller", lambda: calls.append("control"))
    cfg.runtime.paused = True
    monkeypatch.setattr(control, "global_learning_refreeze_reason", lambda: None)
    scheduler._run_job_guarded("learning_lifecycle_controller", lambda: calls.append("control"))

    assert calls == []


def test_scheduler_registers_separate_single_instance_learning_jobs(monkeypatch):
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg())
    monkeypatch.setattr(scheduler, "_recover_interrupted_serenity_run", lambda: [])
    scheduler._scheduler = None
    started = scheduler.init_scheduler()
    try:
        job_ids = [job.id for job in started.get_jobs()]
        evaluator = started.get_job("learning_contribution_evaluator")
        controller = started.get_job("learning_lifecycle_controller")
        assert evaluator is not None and controller is not None
        assert job_ids.count("learning_contribution_evaluator") == 1
        assert "learning_verdict_runner" not in job_ids
        assert evaluator.max_instances == controller.max_instances == 1
        assert evaluator.coalesce is True and controller.coalesce is True
    finally:
        scheduler.stop_scheduler()


def test_measurement_startup_catchup_only_claims_one_current_window(monkeypatch):
    from aletheia.learning import control

    cfg = _cfg()
    monkeypatch.setattr(control, "load_config", lambda: cfg)
    calls: list[str] = []
    monkeypatch.setattr(control, "utc_now", lambda: control.datetime(2026, 7, 15, 12, tzinfo=control.timezone.utc))

    assert control.measurement_catchup_due("learning_contribution_evaluator")
    control.record_measurement_job_success("learning_contribution_evaluator")
    assert not control.measurement_catchup_due("learning_contribution_evaluator")
    assert calls == []
