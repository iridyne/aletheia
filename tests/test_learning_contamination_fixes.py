"""Focused regressions for learning-loop contamination/dead-feedback fixes (B5a).

#157 render_calibration_for_pi must not crash on a numeric ECE (broken f-string).
#159 guard promotion/effectiveness must drop synthetic-close + pre-clean-window rows.
"""


def test_render_calibration_does_not_crash_on_numeric_ece():
    from aletheia.analytics.confidence_calibration import render_calibration_for_pi
    out = render_calibration_for_pi({"ece": 0.123, "ece_grade": "B", "total_executed": 12})
    assert "Conviction Calibration" in out
    assert "0.123" in out                       # numeric ECE renders (previously raised ValueError)
    assert "0.000" in render_calibration_for_pi({"ece": 0.0, "total_executed": 5})
    assert "?" in render_calibration_for_pi({"ece": None, "total_executed": 0})   # missing → "?"


def test_guard_clean_rows_drops_synthetic_and_pre_floor():
    from aletheia.analytics.guard_effectiveness import _clean_rows
    rows = [
        {"close_reason": "stop_loss", "closed_at": "2026-07-01T00:00:00+00:00", "realized_pnl": -5},      # genuine, post-floor
        {"close_reason": "reconciled_gone", "closed_at": "2026-07-01T00:00:00+00:00", "realized_pnl": 0}, # synthetic → drop
        {"close_reason": "take_profit", "closed_at": "2026-06-01T00:00:00+00:00", "realized_pnl": 5},     # pre-floor → drop
        {"close_reason": None, "closed_at": "2026-07-02T00:00:00+00:00", "realized_pnl": 3},              # genuine (null reason), post-floor
    ]
    kept = _clean_rows(rows)
    reasons = [r["close_reason"] for r in kept]
    assert "reconciled_gone" not in reasons     # synthetic dropped
    assert "take_profit" not in reasons          # pre-floor dropped
    assert "stop_loss" in reasons and None in reasons
    assert len(kept) == 2


def test_memo_scoring_fades_with_age(monkeypatch):
    """#131: a stale memo must move ranker scoring far less than a fresh one."""
    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.regime.detector import MarketRegime
    from datetime import datetime, timezone, timedelta

    rule = {"regime": "bull", "direction": "long", "edge": "strong"}

    def _memo(days_old):
        gen = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        return {"regime_playbook": [rule], "symbol_affinity": [], "anti_patterns": [], "generated_at": gen}

    regime = MarketRegime(state="bull", confidence=0.8)

    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: _memo(0))
    fresh = ranker._memo_scoring("BTC/USDT", "long", regime)
    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: _memo(30))
    stale = ranker._memo_scoring("BTC/USDT", "long", regime)

    assert fresh > 5                    # fresh "strong" rule adds ~+12
    assert 0 <= stale < fresh / 2       # 30-day-old memo (7-day half-life) is heavily faded


def test_case_builder_windows_on_close_time():
    """#16: the case-builder window filters on CLOSE time, not open time."""
    from aletheia.analytics.trade_case_builder import _closed_after
    from datetime import datetime, timezone, timedelta

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
    from aletheia.agents.lesson_reviewer import _evidence_block
    ev = {
        "aggregate": {"total": 100, "win_rate": 45.0, "avg_return": 0.1},
        "symbol": None,
        "scanner_family": "momentum",
        "scanner_family_stats": {"total": 20, "win_rate": 30.0, "avg_return": -0.5},
    }
    block = _evidence_block(ev)
    assert "Scanner family momentum" in block
    assert "30.0" in block


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


def test_applied_lesson_demoted_on_contradictory_evidence(tmp_path, monkeypatch):
    """#130: the overturn pass re-reviews APPLIED lessons and demotes a failing one.

    Uses the deterministic-demote path (symbol win-rate < 35% over >=10 settled
    outcomes), so no LLM is needed.
    """
    import asyncio
    sq = _reviewer_db(tmp_path, monkeypatch)
    from aletheia.learning.lessons import LessonStore
    from aletheia.agents.lesson_reviewer import run_lesson_reviewer

    store = LessonStore()
    les = store.create("block BTC/USDT longs", source_type="test", source_id="t",
                       category="risk", severity="high",
                       recommendation={"action": "block_symbol", "symbol": "BTC/USDT", "direction": "long"})
    store.accept(les.lesson_id)
    store.mark_applied(les.lesson_id, "pv-test")
    assert store.get(les.lesson_id).status == "applied"

    # 10 settled 24h BTC/USDT long outcomes, 30% win rate (< 35% demote floor).
    db = sq.get_db()
    for i in range(10):
        ret = 2.0 if i < 3 else -2.0
        db.execute(
            "INSERT INTO universe_forward_outcomes "
            "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, "
            " entry_price_usd, entry_ts, due_at, settled_at, forward_return_pct, "
            " gross_return_pct, directional_return_pct, net_return_pct, "
            " min_tradable_edge_pct, cost_model_version, status) "
            "VALUES (?, 'm1', 's1', 'BTC/USDT', 'momentum', 'long', '24h', "
            " 100.0, datetime('now','-2 days'), datetime('now','-1 day'), datetime('now'), "
            " ?, ?, ?, ?, 0.05, 'v1', 'settled')",
            (f"fo-{i}", ret, ret, ret, ret - 0.2),
        )
    db.commit()

    asyncio.run(run_lesson_reviewer(run_id="test"))
    assert store.get(les.lesson_id).status == "rejected"   # applied lesson overturned


def test_lesson_evidence_ignores_gross_only_history(tmp_path, monkeypatch):
    """Legacy price moves without costs must not promote or overturn lessons."""
    sq = _reviewer_db(tmp_path, monkeypatch)
    from aletheia.agents.lesson_reviewer import _fetch_evidence
    from aletheia.learning.lessons import LessonStore

    lesson = LessonStore().create(
        "BTC/USDT gross-only history",
        source_type="test",
        source_id="gross-only",
        category="entry_timing",
        severity="warning",
    )
    db = sq.get_db()
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, forward_return_pct, status) "
        "VALUES ('gross-only', 'm', 's', 'BTC/USDT', 'momentum', 'long', '24h', 100, "
        "datetime('now','-2 days'), datetime('now','-1 day'), datetime('now'), 9, 'settled')"
    )
    db.commit()

    evidence = _fetch_evidence(lesson)
    assert evidence["aggregate"]["total"] == 0
