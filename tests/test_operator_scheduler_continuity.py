"""Startup catch-up and durable deduplication for the Operator loop."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aletheia.storage import repositories as repo
from aletheia.storage.models import KnowledgeItem


def _assessment(created_at: str) -> None:
    repo.insert_knowledge_item(KnowledgeItem(
        item_type="operator_assessment",
        title="test assessment",
        created_at=created_at,
    ))


def test_operator_assessment_due_without_history():
    from aletheia.agent_runtime.scheduler import _operator_assessment_due

    assert _operator_assessment_due(datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc))


def test_operator_assessment_recent_is_not_due():
    from aletheia.agent_runtime.scheduler import _operator_assessment_due

    now = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    _assessment((now - timedelta(hours=2)).isoformat())

    assert not _operator_assessment_due(now)


def test_operator_assessment_due_after_jitter_tolerant_window():
    from aletheia.agent_runtime.scheduler import _operator_assessment_due

    now = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    _assessment((now - timedelta(hours=3, minutes=46)).isoformat())

    assert _operator_assessment_due(now)


def test_operator_assessment_bad_timestamp_fails_open():
    from aletheia.agent_runtime.scheduler import _operator_assessment_due

    _assessment("not-a-time")

    assert _operator_assessment_due(datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc))


def test_operator_assessment_future_timestamp_fails_open():
    from aletheia.agent_runtime.scheduler import _operator_assessment_due

    now = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    _assessment((now + timedelta(hours=1)).isoformat())

    assert _operator_assessment_due(now)


def test_scheduler_wrapper_skips_recent_assessment(monkeypatch):
    import aletheia.agent_runtime.scheduler as scheduler

    cfg = SimpleNamespace(
        runtime=SimpleNamespace(paused=False),
        operator=SimpleNamespace(loop_enabled=True),
        agent=SimpleNamespace(permission_mode="Paper"),
    )
    called: list[str] = []
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)
    monkeypatch.setattr(scheduler, "_operator_assessment_due", lambda: False)
    monkeypatch.setattr(
        scheduler,
        "_run_sync_with_timeout",
        lambda *_args, **_kwargs: called.append("run"),
    )

    scheduler._run_operator_loop()

    assert called == []


def test_scheduler_wrapper_handles_empty_result(monkeypatch):
    import aletheia.agent_runtime.scheduler as scheduler

    cfg = SimpleNamespace(
        runtime=SimpleNamespace(paused=False),
        operator=SimpleNamespace(loop_enabled=True),
        agent=SimpleNamespace(permission_mode="Paper"),
    )
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)
    monkeypatch.setattr(scheduler, "_operator_assessment_due", lambda: True)
    monkeypatch.setattr(scheduler, "_run_sync_with_timeout", lambda *_args, **_kwargs: None)

    scheduler._run_operator_loop()
