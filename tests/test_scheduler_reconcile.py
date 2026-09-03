"""Tests for scheduler reconciliation functions added in issue-403.

Covers: reconcile_historical_data_jobs, reschedule_news_fetch,
reconcile_filter_controller, reschedule_telegram_summary, and the
corresponding settings route hooks.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _hist_cfg(
    nightly_enabled=True,
    nightly_hour=2,
    enabled=True,
    active_enabled=True,
    active_interval=10,
):
    return SimpleNamespace(
        nightly_backfill_enabled=nightly_enabled,
        nightly_backfill_hour_cst=nightly_hour,
        enabled=enabled,
        active_universe_backfill_enabled=active_enabled,
        active_universe_backfill_interval_minutes=active_interval,
    )


class FakeJob:
    def __init__(self):
        self.trigger = None
        self.next_run_time = object()


class FakeScheduler:
    """Minimal APScheduler stub for reconciliation tests."""

    def __init__(self, jobs=None):
        self._jobs = jobs or {}
        self.added = []
        self.removed = []
        self.rescheduled = []
        self.modified = []
        self.paused = []
        self.timezone = None

    def get_job(self, job_id):
        return self._jobs.get(job_id)

    def get_jobs(self):
        return list(self._jobs.values())

    def add_job(self, func, trigger, id, **kwargs):
        job = FakeJob()
        job.trigger = trigger
        job.id = id
        job.kwargs = kwargs
        self._jobs[id] = job
        self.added.append(id)

    def remove_job(self, job_id):
        self._jobs.pop(job_id, None)
        self.removed.append(job_id)

    def reschedule_job(self, job_id, trigger, **kwargs):
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"no job {job_id!r}")
        job.trigger = trigger
        job.next_run_time = object()
        job.kwargs = kwargs
        self.rescheduled.append((job_id, trigger, kwargs))

    def modify_job(self, job_id, **kwargs):
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"no job {job_id!r}")
        for field, value in kwargs.items():
            setattr(job, field, value)
        self.modified.append((job_id, kwargs))

    def pause_job(self, job_id):
        self._jobs[job_id].next_run_time = None
        self.paused.append(job_id)


# ─── reconcile_historical_data_jobs ──────────────────────────────────────────


def test_historical_nightly_off_to_on_adds_job(monkeypatch):
    from aletheia.agent_runtime import scheduler

    fs = FakeScheduler(jobs={})
    monkeypatch.setattr(scheduler, "_scheduler", fs)

    cfg = _hist_cfg(nightly_enabled=True, nightly_hour=3)
    result = scheduler.reconcile_historical_data_jobs(cfg)

    assert result is True
    assert "ohlcv_nightly_backfill" in fs.added
    assert fs._jobs["ohlcv_nightly_backfill"].kwargs["hour"] == 3
    assert fs._jobs["ohlcv_nightly_backfill"].kwargs["executor"] == "maintenance_io"


def test_historical_nightly_on_to_off_removes_job(monkeypatch):
    from aletheia.agent_runtime import scheduler

    existing = FakeJob()
    fs = FakeScheduler(jobs={"ohlcv_nightly_backfill": existing})
    monkeypatch.setattr(scheduler, "_scheduler", fs)

    cfg = _hist_cfg(nightly_enabled=False)
    result = scheduler.reconcile_historical_data_jobs(cfg)

    assert result is True
    assert "ohlcv_nightly_backfill" in fs.removed
    assert "ohlcv_nightly_backfill" not in fs._jobs


def test_historical_nightly_hour_change_reschedules(monkeypatch):
    from aletheia.agent_runtime import scheduler

    existing = FakeJob()
    fs = FakeScheduler(jobs={"ohlcv_nightly_backfill": existing})
    monkeypatch.setattr(scheduler, "_scheduler", fs)

    cfg = _hist_cfg(nightly_enabled=True, nightly_hour=4)
    result = scheduler.reconcile_historical_data_jobs(cfg)

    assert result is True
    assert fs.rescheduled[0] == ("ohlcv_nightly_backfill", "cron", {"hour": 4, "minute": 30, "timezone": "Asia/Shanghai"})


def test_historical_active_off_to_on_adds_job(monkeypatch):
    from aletheia.agent_runtime import scheduler

    fs = FakeScheduler(jobs={})
    monkeypatch.setattr(scheduler, "_scheduler", fs)

    cfg = _hist_cfg(active_enabled=True, active_interval=20)
    result = scheduler.reconcile_historical_data_jobs(cfg)

    assert result is True
    assert "ohlcv_active_backfill" in fs.added
    assert fs._jobs["ohlcv_active_backfill"].kwargs["minutes"] == 20
    assert fs._jobs["ohlcv_active_backfill"].kwargs["executor"] == "maintenance_io"


def test_historical_active_on_to_off_removes_job(monkeypatch):
    from aletheia.agent_runtime import scheduler

    existing = FakeJob()
    fs = FakeScheduler(jobs={"ohlcv_active_backfill": existing})
    monkeypatch.setattr(scheduler, "_scheduler", fs)

    cfg = _hist_cfg(active_enabled=False)
    result = scheduler.reconcile_historical_data_jobs(cfg)

    assert result is True
    assert "ohlcv_active_backfill" in fs.removed


def test_historical_active_interval_change_reschedules(monkeypatch):
    from aletheia.agent_runtime import scheduler

    existing = FakeJob()
    fs = FakeScheduler(jobs={"ohlcv_active_backfill": existing})
    monkeypatch.setattr(scheduler, "_scheduler", fs)

    cfg = _hist_cfg(active_enabled=True, active_interval=30)
    result = scheduler.reconcile_historical_data_jobs(cfg)

    assert result is True
    assert fs.rescheduled[0] == ("ohlcv_active_backfill", "interval", {"minutes": 30})


def test_historical_reconcile_no_scheduler_returns_false(monkeypatch):
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.reconcile_historical_data_jobs(_hist_cfg()) is False


def test_historical_reconcile_exception_returns_false(monkeypatch):
    from aletheia.agent_runtime import scheduler

    class BrokenScheduler(FakeScheduler):
        def get_job(self, job_id):
            raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "_scheduler", BrokenScheduler())
    assert scheduler.reconcile_historical_data_jobs(_hist_cfg()) is False


# ─── reschedule_news_fetch ────────────────────────────────────────────────────


def test_reschedule_news_fetch_applies_new_interval(monkeypatch):
    from aletheia.agent_runtime import scheduler

    existing = FakeJob()
    fs = FakeScheduler(jobs={"news_fetch": existing})
    monkeypatch.setattr(scheduler, "_scheduler", fs)

    assert scheduler.reschedule_news_fetch(30) is True
    assert fs.rescheduled[0] == ("news_fetch", "interval", {"minutes": 30})


def test_reschedule_news_fetch_absent_job_returns_false(monkeypatch):
    from aletheia.agent_runtime import scheduler

    fs = FakeScheduler(jobs={})
    monkeypatch.setattr(scheduler, "_scheduler", fs)
    assert scheduler.reschedule_news_fetch(30) is False


def test_reschedule_news_fetch_no_scheduler_returns_false(monkeypatch):
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.reschedule_news_fetch(30) is False


# ─── reconcile_filter_controller ─────────────────────────────────────────────


def test_filter_controller_off_to_on_adds_job(monkeypatch):
    from aletheia.agent_runtime import scheduler

    fs = FakeScheduler(jobs={})
    monkeypatch.setattr(scheduler, "_scheduler", fs)
    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", set())

    result = scheduler.reconcile_filter_controller(shadow_enabled=True, observe_interval_hours=4)

    assert result is True
    assert "filter_controller_observe" in fs.added
    assert fs._jobs["filter_controller_observe"].kwargs["hours"] == 4
    assert "filter_controller_observe" not in fs.paused


def test_filter_controller_off_to_on_pauses_when_layered(monkeypatch):
    from aletheia.agent_runtime import scheduler

    fs = FakeScheduler(jobs={})
    monkeypatch.setattr(scheduler, "_scheduler", fs)
    paused_set = {"some_other_job"}
    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", paused_set)

    result = scheduler.reconcile_filter_controller(shadow_enabled=True, observe_interval_hours=4)

    assert result is True
    assert "filter_controller_observe" in fs.paused
    assert "filter_controller_observe" in paused_set


def test_filter_controller_shadow_disabled_keeps_paused_main_job(monkeypatch):
    from aletheia.agent_runtime import scheduler

    existing = FakeJob()
    fs = FakeScheduler(jobs={"filter_controller_observe": existing})
    monkeypatch.setattr(scheduler, "_scheduler", fs)
    paused_set = {"filter_controller_observe"}
    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", paused_set)
    monkeypatch.setattr(
        "aletheia.observation_lab.execution_owner.is_worker_owner",
        lambda: False,
    )

    result = scheduler.reconcile_filter_controller(shadow_enabled=False, observe_interval_hours=4)

    assert result is True
    assert "filter_controller_observe" not in fs.removed
    assert existing.next_run_time is None
    assert "filter_controller_observe" in paused_set


def test_filter_controller_interval_change_reschedules(monkeypatch):
    from aletheia.agent_runtime import scheduler

    existing = FakeJob()
    fs = FakeScheduler(jobs={"filter_controller_observe": existing})
    monkeypatch.setattr(scheduler, "_scheduler", fs)
    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", set())

    result = scheduler.reconcile_filter_controller(shadow_enabled=True, observe_interval_hours=8)

    assert result is True
    assert fs.rescheduled[0] == ("filter_controller_observe", "interval", {"hours": 8})


def test_filter_controller_no_scheduler_returns_false(monkeypatch):
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.reconcile_filter_controller(True, 4) is False


# ─── reschedule_telegram_summary ─────────────────────────────────────────────


def test_reschedule_telegram_summary_applies_new_hour(monkeypatch):
    from aletheia.agent_runtime import scheduler

    existing = FakeJob()
    fs = FakeScheduler(jobs={"daily_summary": existing})
    monkeypatch.setattr(scheduler, "_scheduler", fs)

    assert scheduler.reschedule_telegram_summary(20) is True
    assert fs.rescheduled[0] == (
        "daily_summary", "cron", {"hour": 20, "minute": 0, "timezone": "Asia/Shanghai"},
    )


def test_reschedule_telegram_summary_absent_job_returns_false(monkeypatch):
    from aletheia.agent_runtime import scheduler

    fs = FakeScheduler(jobs={})
    monkeypatch.setattr(scheduler, "_scheduler", fs)
    assert scheduler.reschedule_telegram_summary(20) is False


def test_reschedule_telegram_summary_no_scheduler_returns_false(monkeypatch):
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.reschedule_telegram_summary(20) is False


def test_trigger_changes_preserve_layered_pause(monkeypatch):
    from aletheia.agent_runtime import scheduler

    job_ids = {
        "filter_controller_observe",
        "daily_summary",
        "universe_refresh",
    }
    jobs = {job_id: FakeJob() for job_id in job_ids}
    for job in jobs.values():
        job.next_run_time = None
    fs = FakeScheduler(jobs=jobs)
    monkeypatch.setattr(scheduler, "_scheduler", fs)
    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", set(job_ids))
    monkeypatch.setattr(
        "aletheia.observation_lab.execution_owner.is_worker_owner",
        lambda: False,
    )

    assert scheduler.reconcile_filter_controller(True, 8) is True
    assert scheduler.reschedule_telegram_summary(20) is True
    assert scheduler.reschedule_universe_refresh(30) is True

    assert all(job.next_run_time is None for job in jobs.values())
    assert scheduler._selectively_paused_jobs == job_ids


def test_trigger_reschedule_is_atomic_with_final_pause_resume(monkeypatch):
    from aletheia.agent_runtime import pause_ownership, scheduler

    condition = threading.Condition()
    state = {"started": False, "resume_done": False}

    class GateLock:
        def __enter__(self):
            with condition:
                state["started"] = True
                condition.notify_all()
                condition.wait_for(lambda: state["resume_done"])

        def __exit__(self, *_args):
            return False

    class RaceScheduler(FakeScheduler):
        def get_job(self, job_id):
            snapshot = self._jobs.get(job_id)
            with condition:
                state["started"] = True
                condition.notify_all()
                condition.wait_for(lambda: state["resume_done"])
            return snapshot

    paused_job = FakeJob()
    paused_job.next_run_time = None
    fs = RaceScheduler(jobs={"universe_refresh": paused_job})
    paused = {"universe_refresh"}
    monkeypatch.setattr(scheduler, "_scheduler", fs)
    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", paused)
    monkeypatch.setattr(pause_ownership, "_pause_transition_lock", GateLock())

    result = []
    worker = threading.Thread(
        target=lambda: result.append(scheduler.reschedule_universe_refresh(30))
    )
    worker.start()
    with condition:
        assert condition.wait_for(lambda: state["started"], timeout=1)
        running_job = FakeJob()
        fs._jobs["universe_refresh"] = running_job
        paused.clear()
        state["resume_done"] = True
        condition.notify_all()
    worker.join(timeout=1)

    assert result == [True]
    assert fs._jobs["universe_refresh"].next_run_time is not None
    assert paused == set()


# ─── Settings route integration ──────────────────────────────────────────────


def _make_cfg():
    from unittest.mock import MagicMock
    cfg = MagicMock()
    cfg.historical_data.model_dump.return_value = {
        "nightly_backfill_enabled": False,
        "nightly_backfill_hour_cst": 2,
        "enabled": True,
        "active_universe_backfill_enabled": True,
        "active_universe_backfill_interval_minutes": 10,
    }
    cfg.historical_data.nightly_backfill_enabled = False
    cfg.historical_data.nightly_backfill_hour_cst = 2
    cfg.historical_data.enabled = True
    cfg.historical_data.active_universe_backfill_enabled = True
    cfg.historical_data.active_universe_backfill_interval_minutes = 10
    cfg.news.model_dump.return_value = {
        "enabled": True,
        "fetch_interval_minutes": 15,
        "retention_days": 30,
    }
    cfg.news.fetch_interval_minutes = 15
    cfg.controller.model_dump.return_value = {
        "shadow_enabled": False,
        "observe_interval_hours": 4,
    }
    cfg.controller.shadow_enabled = False
    cfg.controller.observe_interval_hours = 4
    cfg.telegram.model_dump.return_value = {
        "enabled": False,
        "daily_summary_hour_cst": 8,
    }
    cfg.telegram.daily_summary_hour_cst = 8
    return cfg


def test_historical_data_settings_route_calls_reconcile(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.files import jsonl
    from aletheia.server.routes import settings

    cfg = _make_cfg()
    reconciled = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda c: None)
    monkeypatch.setattr(jsonl, "write_audit", lambda _e: None)
    monkeypatch.setattr(
        scheduler, "reconcile_historical_data_jobs",
        lambda hcfg: reconciled.append(hcfg) or True,
    )

    result = asyncio.run(settings.update_historical_data_config(
        settings.HistoricalDataConfigUpdate(nightly_backfill_enabled=True),
    ))

    assert reconciled == [cfg.historical_data]
    assert result["rescheduled"] is True


def test_historical_data_settings_route_no_scheduler_fields_skips(monkeypatch):
    from aletheia.files import jsonl
    from aletheia.server.routes import settings

    cfg = _make_cfg()
    reconciled = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda c: None)
    monkeypatch.setattr(jsonl, "write_audit", lambda _e: None)

    # A field not in the scheduler-relevant set should skip reconciliation
    result = asyncio.run(settings.update_historical_data_config(
        settings.HistoricalDataConfigUpdate(),
    ))

    assert reconciled == []
    assert result["rescheduled"] is False


def test_news_settings_route_calls_reschedule(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.files import jsonl
    from aletheia.server.routes import settings

    cfg = _make_cfg()
    calls = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda c: None)
    monkeypatch.setattr(jsonl, "write_audit", lambda _e: None)
    monkeypatch.setattr(
        scheduler, "reschedule_news_fetch",
        lambda interval: calls.append(interval) or True,
    )
    cfg.news.fetch_interval_minutes = 30

    result = asyncio.run(settings.update_news_config(
        settings.NewsConfigUpdate(fetch_interval_minutes=30),
    ))

    assert calls == [30]
    assert result["rescheduled"] is True


def test_controller_settings_route_calls_reconcile(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.files import jsonl
    from aletheia.server.routes import settings

    cfg = _make_cfg()
    calls = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda c: None)
    monkeypatch.setattr(jsonl, "write_audit", lambda _e: None)
    monkeypatch.setattr(
        scheduler, "reconcile_filter_controller",
        lambda enabled, hours: calls.append((enabled, hours)) or True,
    )
    cfg.controller.shadow_enabled = True
    cfg.controller.observe_interval_hours = 6

    result = asyncio.run(settings.update_controller_config(
        settings.ControllerConfigUpdate(shadow_enabled=True),
    ))

    assert calls == [(True, 6)]
    assert result["rescheduled"] is True


def test_telegram_settings_route_calls_reschedule(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.files import jsonl
    from aletheia.server.routes import settings

    cfg = _make_cfg()
    calls = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda c: None)
    monkeypatch.setattr(jsonl, "write_audit", lambda _e: None)
    monkeypatch.setattr(
        scheduler, "reschedule_telegram_summary",
        lambda hour: calls.append(hour) or True,
    )
    cfg.telegram.daily_summary_hour_cst = 20

    result = asyncio.run(settings.update_telegram_config(
        settings.TelegramConfigUpdate(daily_summary_hour_cst=20),
    ))

    assert calls == [20]
    assert result["rescheduled"] is True


def test_telegram_settings_route_same_hour_skips_reschedule(monkeypatch):
    from aletheia.files import jsonl
    from aletheia.server.routes import settings

    cfg = _make_cfg()
    calls = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda c: None)
    monkeypatch.setattr(jsonl, "write_audit", lambda _e: None)
    # Same hour as old value (8) — should not reschedule
    cfg.telegram.daily_summary_hour_cst = 8

    result = asyncio.run(settings.update_telegram_config(
        settings.TelegramConfigUpdate(daily_summary_hour_cst=8),
    ))

    assert calls == []
    assert result["rescheduled"] is False
