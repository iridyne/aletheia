from datetime import datetime, timezone


def test_equity_observer_session_follows_eastern_dst_and_bounds():
    from aletheia.agent_runtime.scheduler import _equity_observer_in_session

    # Standard time: 14:30 UTC is 09:30 ET; 14:00 UTC is pre-open.
    assert _equity_observer_in_session(datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc))
    assert not _equity_observer_in_session(datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc))
    assert _equity_observer_in_session(datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc))
    assert not _equity_observer_in_session(datetime(2026, 1, 2, 21, 1, tzinfo=timezone.utc))

    # Daylight time: 13:30 UTC is 09:30 ET; 20:00 UTC is 16:00 ET.
    assert _equity_observer_in_session(datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc))
    assert not _equity_observer_in_session(datetime(2026, 8, 3, 13, 0, tzinfo=timezone.utc))
    assert _equity_observer_in_session(datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc))
    assert not _equity_observer_in_session(datetime(2026, 8, 3, 20, 1, tzinfo=timezone.utc))
    assert not _equity_observer_in_session(datetime(2026, 8, 8, 13, 30, tzinfo=timezone.utc))
