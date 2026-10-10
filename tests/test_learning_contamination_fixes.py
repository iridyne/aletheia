"""Focused regressions for learning-loop contamination/dead-feedback fixes (B5a).

#159 guard promotion/effectiveness must drop synthetic-close + pre-clean-window rows.
"""

from datetime import datetime

from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ISO

_ANCHOR = datetime.fromisoformat(CLEAN_WINDOW_ANCHOR_ISO)


def test_guard_clean_rows_drops_synthetic_and_pre_floor():
    """Post-floor and pre-floor are derived, not dated.

    These rows were pinned to 2026-07-12 (labelled post-floor) and 2026-06-01
    (pre-floor). Once the clean-window floor advanced to 2026-09-23 both were
    pre-floor, so every row dropped and the test read as a filter regression while
    the filter was doing exactly its job.
    """
    from datetime import timedelta

    from extensions.analytics.guard_effectiveness import _clean_rows

    post_floor = (_ANCHOR + timedelta(hours=8)).isoformat()
    post_floor_later = (_ANCHOR + timedelta(hours=8, minutes=1)).isoformat()
    pre_floor = (_ANCHOR - timedelta(days=100)).isoformat()
    rows = [
        {"close_reason": "stop_loss", "closed_at": post_floor, "realized_pnl": -5},        # genuine, post-floor
        {"close_reason": "reconciled_gone", "closed_at": post_floor, "realized_pnl": 0},   # synthetic → drop
        {"close_reason": "take_profit", "closed_at": pre_floor, "realized_pnl": 5},        # pre-floor → drop
        {"close_reason": None, "closed_at": post_floor_later, "realized_pnl": 3},          # genuine (null reason), post-floor
    ]
    kept = _clean_rows(rows)
    reasons = [r["close_reason"] for r in kept]
    assert "reconciled_gone" not in reasons     # synthetic dropped
    assert "take_profit" not in reasons          # pre-floor dropped
    assert "stop_loss" in reasons and None in reasons
    assert len(kept) == 2




def test_case_builder_windows_on_close_time():
    """#16: the case-builder window filters on CLOSE time, not open time."""
    from datetime import datetime, timedelta, timezone

    from extensions.analytics.trade_case_builder import _closed_after

    cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600  # last 24h
    recent_close = {"opened_at": "2026-06-01T00:00:00+00:00",
                    "closed_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    old_close = {"opened_at": "2026-06-01T00:00:00+00:00",
                 "closed_at": "2026-06-02T00:00:00+00:00"}
    assert _closed_after(recent_close, cutoff) is True    # opened long ago, closed recently → captured
    assert _closed_after(old_close, cutoff) is False
    assert _closed_after({"closed_at": None}, cutoff) is False


def _reviewer_db(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    return sq


def test_oldest_of_many_applied_lessons_is_still_overturned(tmp_path, monkeypatch):
    """#2865: Risk enforces every applied lesson, so overturn must reach the oldest.

    With 101 applied lessons the newest-100 review set never re-checked the oldest
    one, yet Risk kept enforcing it.
    """
    import asyncio

    sq = _reviewer_db(tmp_path, monkeypatch)
    import extensions.research.lesson_reviewer as reviewer
    from extensions.learning.lessons import LessonStore

    store = LessonStore()

    def applied(summary):
        lesson = store.create(summary, source_type="test", source_id="t", category="risk", severity="high")
        store.accept(lesson.lesson_id)
        store.mark_applied(lesson.lesson_id, "pv-test")
        return lesson

    oldest = applied("block BTC/USDT longs")
    db = sq.get_db()
    db.execute("UPDATE ps_lessons SET created_at = '2026-09-24T00:00:00+00:00' WHERE lesson_id = ?",
               (oldest.lesson_id,))
    db.commit()
    for index in range(100):
        applied(f"block COIN{index}/USDT shorts")

    async def judge(lesson, evidence, run_id):
        return ("reject" if lesson.lesson_id == oldest.lesson_id else "defer"), "", False

    monkeypatch.setattr(reviewer, "_fetch_evidence", lambda lesson: {"total": reviewer._MIN_SOURCE_TRADES})
    monkeypatch.setattr(reviewer, "_judge", judge)
    asyncio.run(reviewer.run_lesson_reviewer(run_id="test"))

    reviewed = store.get(oldest.lesson_id)
    assert reviewed is not None and reviewed.status == "rejected"


def test_rejudge_window_reaches_every_lesson_within_bounded_runs():
    from datetime import timedelta

    from extensions.research import lesson_reviewer

    ids = [f"lesson-{index}" for index in range(lesson_reviewer._MAX_REJUDGED_PER_RUN * 2 + 1)]
    day = datetime(2026, 10, 9)
    runs = -(-len(ids) // lesson_reviewer._MAX_REJUDGED_PER_RUN)
    reached: set[str] = set()
    for offset in range(runs):
        window = lesson_reviewer._rejudge_window(ids, day + timedelta(days=offset))
        assert len(window) == lesson_reviewer._MAX_REJUDGED_PER_RUN
        reached |= window
    assert reached == set(ids)
    assert lesson_reviewer._rejudge_window(ids[:5], day) == set(ids[:5])


def test_lesson_reviewer_passes_reach_every_proposed_lesson(tmp_path, monkeypatch):
    """Deferred lessons stay proposed, so a fixed oldest-first head starved the rest."""
    import asyncio

    _reviewer_db(tmp_path, monkeypatch)
    import extensions.research.lesson_reviewer as reviewer
    from extensions.learning.lessons import LessonStore

    store = LessonStore()
    ids = {
        store.create(f"lesson {i}", source_type="test", source_id=str(i)).lesson_id
        for i in range(reviewer._MAX_PROPOSED_PER_RUN + 1)
    }
    seen: set[str] = set()

    def no_evidence(lesson):
        seen.add(lesson.lesson_id)
        return {"total": 0}

    monkeypatch.setattr(reviewer, "_fetch_evidence", no_evidence)
    asyncio.run(reviewer.run_lesson_reviewer(run_id="pass-1"))
    asyncio.run(reviewer.run_lesson_reviewer(run_id="pass-2"))
    assert seen == ids
