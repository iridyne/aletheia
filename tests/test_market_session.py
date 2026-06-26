"""Unit tests for the trading-session classifier.

Pure function, no DB/network — these pin the session boundaries so the Round-2
wiring (when the EXPERIMENT_PROTOCOL freeze lifts) injects correct facts.
"""

from datetime import datetime, timezone

import pytest

from aletheia.trading.market_session import classify_session, render_for_prompt


def _utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


# 2026-06-22 is a Monday; 2026-06-27 a Saturday, 2026-06-28 a Sunday.
@pytest.mark.parametrize(
    "dt,expected_label,expected_liquidity",
    [
        (_utc(2026, 6, 22, 3), "asian", "low"),       # Mon 03:00
        (_utc(2026, 6, 22, 9), "european", "medium"), # Mon 09:00
        (_utc(2026, 6, 22, 14), "overlap", "high"),   # Mon 14:00
        (_utc(2026, 6, 22, 18), "us", "high"),        # Mon 18:00
        (_utc(2026, 6, 22, 22), "late_us", "low"),    # Mon 22:00
        (_utc(2026, 6, 27, 14), "weekend", "low"),    # Sat 14:00 (would be overlap on a weekday)
        (_utc(2026, 6, 28, 18), "weekend", "low"),    # Sun 18:00
    ],
)
def test_session_label_and_liquidity(dt, expected_label, expected_liquidity):
    s = classify_session(dt)
    assert s.label == expected_label
    assert s.liquidity == expected_liquidity


def test_band_boundaries_are_half_open():
    """Each band is [start, end): the hour that starts a band belongs to it."""
    assert classify_session(_utc(2026, 6, 22, 0)).label == "asian"       # 00:00 → asian
    assert classify_session(_utc(2026, 6, 22, 7)).label == "european"    # 07:00 → european
    assert classify_session(_utc(2026, 6, 22, 13)).label == "overlap"    # 13:00 → overlap
    assert classify_session(_utc(2026, 6, 22, 16)).label == "us"         # 16:00 → us
    assert classify_session(_utc(2026, 6, 22, 21)).label == "late_us"    # 21:00 → late_us
    assert classify_session(_utc(2026, 6, 22, 23)).label == "late_us"    # 23:00 → late_us


def test_us_equities_open_window():
    """NYSE cash session = Mon-Fri 14:30-21:00 UTC (codebase convention)."""
    assert classify_session(_utc(2026, 6, 22, 14, 29)).us_equities_open is False  # 14:29 closed
    assert classify_session(_utc(2026, 6, 22, 14, 30)).us_equities_open is True   # 14:30 open
    assert classify_session(_utc(2026, 6, 22, 20, 59)).us_equities_open is True   # 20:59 open
    assert classify_session(_utc(2026, 6, 22, 21, 0)).us_equities_open is False   # 21:00 closed
    # Weekend never counts as equities-open even inside the clock window.
    assert classify_session(_utc(2026, 6, 27, 15)).us_equities_open is False


def test_asian_session_note_matches_user_framing():
    """The Asian-session note must surface the 'US closed / low liquidity / be
    careful' framing the feature is designed around."""
    s = classify_session(_utc(2026, 6, 22, 3))
    assert s.us_equities_open is False
    low = s.note.lower()
    assert "asian" in s.name.lower()
    assert "closed" in low and ("mean-revert" in low or "range" in low)


def test_naive_datetime_treated_as_utc():
    naive = datetime(2026, 6, 22, 3)  # no tzinfo
    assert classify_session(naive).label == "asian"


def test_render_for_prompt_is_compact_and_factual():
    text = render_for_prompt(_utc(2026, 6, 22, 3))
    assert "Asian" in text
    assert "liquidity=low" in text
    assert "US equities closed" in text
    # Two lines: header + note. Cheap on prompt tokens.
    assert text.count("\n") == 1


def test_default_now_runs_without_args():
    """No-arg call uses the live clock and must not raise."""
    s = classify_session()
    assert s.label in {"asian", "european", "overlap", "us", "late_us", "weekend"}
    assert s.liquidity in {"high", "medium", "low"}
