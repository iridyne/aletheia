from datetime import datetime, timezone
from types import SimpleNamespace


def _next_fire(now: datetime):
    from aletheia.agent_runtime.scheduler import _equity_observer_trigger

    return _equity_observer_trigger().get_next_fire_time(None, now)


def test_equity_observer_trigger_follows_eastern_dst_and_bounds():
    # Standard time: 09:30 ET is 14:30 UTC; 16:00 ET is 21:00 UTC.
    assert _next_fire(datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)).astimezone(
        timezone.utc
    ) == datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    assert _next_fire(datetime(2026, 1, 2, 21, 1, tzinfo=timezone.utc)).astimezone(
        timezone.utc
    ) == datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)

    # Daylight time: 09:30 ET is 13:30 UTC; 16:00 ET is 20:00 UTC.
    assert _next_fire(datetime(2026, 8, 3, 13, 0, tzinfo=timezone.utc)).astimezone(
        timezone.utc
    ) == datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
    assert _next_fire(datetime(2026, 8, 3, 19, 31, tzinfo=timezone.utc)).astimezone(
        timezone.utc
    ) == datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
    assert _next_fire(datetime(2026, 8, 8, 13, 30, tzinfo=timezone.utc)) is not None


def test_equity_observer_does_not_add_a_wall_clock_misfire_gate(monkeypatch):
    from aletheia.agent_runtime import scheduler

    calls = []
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(paused=False),
            market_data=SimpleNamespace(observed_equities=["SPY"]),
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "_run_async_with_timeout",
        lambda *args: calls.append(args),
    )
    monkeypatch.setattr(scheduler, "_async_run", lambda *_args: None)

    scheduler._run_equity_observer()

    assert calls
