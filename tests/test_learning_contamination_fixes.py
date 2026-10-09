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


def test_evidence_block_includes_scanner_family():
    """#99: block_scanner lessons must surface a scanner-family evidence slice to the judge."""
    from extensions.research.lesson_reviewer import _evidence_block
    ev = {
        "aggregate": {"total": 100, "win_rate": 45.0, "avg_return": 0.1},
        "symbol": None,
        "scanner_family": "momentum",
        "scanner_family_stats": {"total": 20, "win_rate": 30.0, "avg_return": -0.5},
    }
    block = _evidence_block(ev)
    assert "Scanner family momentum" in block
    assert "30.0" in block


def test_lesson_reviewer_zero_win_rate_demotes_exact_scope():
    """A literal 0% win rate must not be mistaken for a missing value."""
    from extensions.research.lesson_reviewer import _deterministic_demote

    assert _deterministic_demote({
        "exact_scope": {"total": 10, "win_rate": 0.0, "avg_return": -2.0},
    }) is True


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


def _insert_failing_btc_long_outcomes(db) -> None:
    """10 settled 24h BTC/USDT long outcomes at a 30% win rate (< 35% demote floor).

    Timestamps sit after the clean-window anchor; earlier rows are excluded as dirty.
    """
    from datetime import timedelta

    entry_at = (_ANCHOR + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    due_at = (_ANCHOR + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    settled_at = (_ANCHOR + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(10):
        ret = 2.0 if i < 3 else -2.0
        db.execute(
            "INSERT INTO filter_candidate_outcomes "
                "(id, outcome_candidate_key, symbol, venue, scanner_family, direction, horizon, "
                " market_type, entry_price_usd, exit_price_usd, entry_ts, due_at, settled_at, "
                " settlement_price_source, settlement_lag_sec, "
                " gross_return_pct, directional_return_pct, net_return_pct, "
                " estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, "
                " min_tradable_edge_pct, cost_model_version, status) "
                "VALUES (?, ?, 'BTC/USDT', 'bitget', 'momentum', 'long', '24h', "
                " 'spot', 100.0, ?, ?, ?, ?, "
                " 'horizon_ohlcv', 0, ?, ?, ?, 0.1, 0.1, 0, 0.05, 'v1', 'settled')",
                (f"fo-{i}", f"candidate-fo-{i}", 100.0 * (1.0 + ret / 100.0),
                 entry_at, due_at, settled_at,
                 ret, ret, ret - 0.2),
        )
    db.commit()


def test_applied_lesson_demoted_on_contradictory_evidence(tmp_path, monkeypatch):
    """#130: the overturn pass re-reviews APPLIED lessons and demotes a failing one.

    Uses the deterministic-demote path (symbol win-rate < 35% over >=10 settled
    outcomes), so no LLM is needed. Fixtures are anchored to the clean-window
    floor — rows with entry_ts before the anchor are excluded as "dirty" and the
    demote-by-evidence path never fires.
    """
    import asyncio
    from datetime import timedelta

    sq = _reviewer_db(tmp_path, monkeypatch)
    from extensions.research.lesson_reviewer import run_lesson_reviewer
    from extensions.learning.lessons import LessonStore

    store = LessonStore()
    les = store.create("block BTC/USDT longs", source_type="test", source_id="t",
                       category="risk", severity="high",
                       recommendation={"action": "block_symbol", "symbol": "BTC/USDT", "direction": "long"})
    store.accept(les.lesson_id)
    store.mark_applied(les.lesson_id, "pv-test")
    assert store.get(les.lesson_id).status == "applied"

    # 10 settled 24h BTC/USDT long outcomes, 30% win rate (< 35% demote floor).
    # Post-anchor timestamps: the clean-window anchor advanced after these rows
    # were originally written (when `datetime('now','-2 days')` was well after the
    # v1 anchor), so they silently crossed the v2 floor and all rows were excluded
    # as "dirty_or_missing_entry_time" — the evidence query saw 0 total, and the
    # overturn pass deferred instead of demoting. Anchoring them post-floor fixed.
    _insert_failing_btc_long_outcomes(sq.get_db())

    asyncio.run(run_lesson_reviewer(run_id="test"))
    assert store.get(les.lesson_id).status == "rejected"   # applied lesson overturned


def test_lesson_evidence_ignores_gross_only_history(tmp_path, monkeypatch):
    """Legacy price moves without costs must not promote or overturn lessons."""
    sq = _reviewer_db(tmp_path, monkeypatch)
    from extensions.research.lesson_reviewer import _fetch_evidence
    from extensions.learning.lessons import LessonStore

    lesson = LessonStore().create(
        "BTC/USDT gross-only history",
        source_type="test",
        source_id="gross-only",
        category="entry_timing",
        severity="warning",
        evidence={
            "supporting_factor_keys": [
                "momentum:BTC/USDT:long:medium_confidence:clean",
            ],
        },
    )
    db = sq.get_db()
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, symbol, venue, market_type, scanner_family, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, status) "
        "VALUES ('gross-only', 'gross-only', 'BTC/USDT', 'bitget', 'spot', 'momentum', 'long', '24h', 100, "
        "datetime('now','-2 days'), datetime('now','-1 day'), datetime('now'), 'settled')"
    )
    db.commit()

    evidence = _fetch_evidence(lesson)
    assert evidence["aggregate"]["total"] == 0
    assert evidence["exact_scope_dimensions"] == {
        "symbol": "BTC/USDT", "direction": "long", "scanner_family": "momentum",
    }


def test_lesson_evidence_uses_complete_rows_at_one_cutoff(tmp_path, monkeypatch):
    """All evidence slices assess the same complete outcome snapshot.

    "inside-window" and "temporal" sit shortly after the clean-window anchor, so
    they clear the entry-time floor; "before-window" sits 30 real days back, well
    before the anchor, so it is excluded by the query's rolling window before it
    ever reaches the floor check (that exclusion is what this test asserts on).
    """
    from datetime import datetime, timedelta, timezone

    sq = _reviewer_db(tmp_path, monkeypatch)
    from extensions.research.lesson_reviewer import _fetch_evidence
    from aletheia.analytics import outcome_semantics as outcomes
    from extensions.learning.lessons import LessonStore

    lesson = LessonStore().create(
        "BTC/USDT momentum longs",
        source_type="test",
        source_id="temporal-evidence",
        category="entry_timing",
        severity="warning",
        evidence={
            "supporting_factor_keys": [
                "momentum:BTC/USDT:long:medium_confidence:clean",
            ],
        },
    )
    db = sq.get_db()
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, symbol, venue, scanner_family, direction, horizon, "
        "entry_price_usd, exit_price_usd, entry_ts, due_at, settled_at, gross_return_pct, "
        "directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, "
        "net_return_pct, min_tradable_edge_pct, cost_model_version, settlement_price_source, settlement_lag_sec, status) "
        "VALUES ('temporal', 'candidate-temporal', 'BTC/USDT', 'bitget', 'momentum', 'long', "
        "'24h', 100, 101, ?, ?, ?, 1, 1, 0.1, 0.1, 0, 0.8, 0.05, 'v1', 'horizon_ohlcv', 0, 'settled')",
        (
            (_ANCHOR + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            (_ANCHOR + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            (_ANCHOR + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    boundary = datetime.now(timezone.utc) - timedelta(days=30)
    rows = (
        # Excluded by the 30-day rolling window itself, ahead of the floor check.
        ("before-window", boundary - timedelta(hours=24, minutes=6),
         boundary - timedelta(minutes=6), boundary - timedelta(minutes=5)),
        # Post-anchor, so it clears the entry-time floor and is within 30 days of now.
        ("inside-window", _ANCHOR + timedelta(hours=4),
         _ANCHOR + timedelta(hours=5), _ANCHOR + timedelta(hours=6)),
    )
    for row_id, entry_at, due_at, settled_at in rows:
        db.execute(
            "INSERT INTO filter_candidate_outcomes "
            "(id, outcome_candidate_key, symbol, venue, scanner_family, direction, horizon, "
            "entry_price_usd, exit_price_usd, entry_ts, due_at, settled_at, gross_return_pct, "
            "directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, "
            "net_return_pct, min_tradable_edge_pct, cost_model_version, settlement_price_source, settlement_lag_sec, status) "
            "VALUES (?, ?, 'BTC/USDT', 'bitget', 'momentum', 'long', '24h', "
            "100, 101, ?, ?, ?, 1, 1, 0.1, 0.1, 0, 0.8, 0.05, 'v1', 'horizon_ohlcv', 0, 'settled')",
            (
                row_id,
                f"candidate-{row_id}",
                entry_at.strftime("%Y-%m-%d %H:%M:%S"),
                due_at.strftime("%Y-%m-%d %H:%M:%S"),
                settled_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
    db.commit()

    original = outcomes.assess_candidate_outcome
    assessed: list[tuple[dict, object]] = []

    def _record(row, *, as_of_at=None):
        assessed.append((dict(row), as_of_at))
        return original(row, as_of_at=as_of_at)

    monkeypatch.setattr(outcomes, "assess_candidate_outcome", _record)

    evidence = _fetch_evidence(lesson)

    assert evidence["exact_scope"]["total"] == 2
    assert assessed
    assert "before-window" not in {row.get("id") for row, _ in assessed}
    assert "inside-window" in {row.get("id") for row, _ in assessed}
    assert all(row.get("due_at") and row.get("settled_at") for row, _ in assessed)
    cutoffs = {as_of_at for _, as_of_at in assessed}
    assert len(cutoffs) == 1
    assert next(iter(cutoffs)).tzinfo is timezone.utc


def test_oldest_of_many_applied_lessons_is_still_overturned(tmp_path, monkeypatch):
    """#2865: Risk enforces every applied lesson, so overturn must reach the oldest.

    With 101 applied lessons the newest-100 review set never re-checked the oldest
    one, yet Risk kept enforcing it.
    """
    import asyncio

    sq = _reviewer_db(tmp_path, monkeypatch)
    from extensions.research.lesson_reviewer import run_lesson_reviewer
    from extensions.learning.lessons import LessonStore

    store = LessonStore()

    def applied(summary, recommendation):
        lesson = store.create(summary, source_type="test", source_id="t",
                              category="risk", severity="high", recommendation=recommendation)
        store.accept(lesson.lesson_id)
        store.mark_applied(lesson.lesson_id, "pv-test")
        return lesson

    oldest = applied("block BTC/USDT longs",
                     {"action": "block_symbol", "symbol": "BTC/USDT", "direction": "long"})
    db = sq.get_db()
    db.execute("UPDATE ps_lessons SET created_at = '2026-09-24T00:00:00+00:00' WHERE lesson_id = ?",
               (oldest.lesson_id,))
    db.commit()
    for index in range(100):
        applied(f"block COIN{index}/USDT shorts",
                {"action": "block_symbol", "symbol": f"COIN{index}/USDT", "direction": "short"})
    _insert_failing_btc_long_outcomes(db)

    asyncio.run(run_lesson_reviewer(run_id="test"))

    reviewed = store.get(oldest.lesson_id)
    assert reviewed is not None
    assert reviewed.status == "rejected"


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
        return {"exact_scope": {"total": 0}}

    monkeypatch.setattr(reviewer, "_fetch_evidence", no_evidence)
    asyncio.run(reviewer.run_lesson_reviewer(run_id="pass-1"))
    asyncio.run(reviewer.run_lesson_reviewer(run_id="pass-2"))
    assert seen == ids
