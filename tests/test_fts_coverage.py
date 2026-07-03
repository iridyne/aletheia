"""Tests for FTS5 experience-loop coverage: reindex_all and startup indexing."""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.knowledge.manager import (
    create_research_note,
    create_post_trade_review,
    create_trade_case_study,
)
from aletheia.knowledge.retrieval import reindex_all, retrieve_lessons


def setup_test_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for d in ("knowledge/research_notes", "knowledge/post_trade_reviews",
              "knowledge/trade_case_studies", "logs"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_high_confidence_items_indexed_at_creation(tmp_path, monkeypatch):
    """Items created with high_confidence are immediately in FTS."""
    setup_test_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    db = sq.get_db()

    create_research_note("BTC momentum study",
                         "BTC shows strong momentum divergence on the 4h chart with RSI overbought while price action consolidates below resistance.",
                         confidence="high_confidence")
    count = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
    assert count == 1
    reset_db()


def test_medium_confidence_items_not_indexed(tmp_path, monkeypatch):
    """Items with medium_confidence must NOT enter FTS (quality gate)."""
    setup_test_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    db = sq.get_db()

    create_post_trade_review("Review fallback", "LLM unavailable fallback text",
                             confidence="medium_confidence")
    count = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
    assert count == 0
    reset_db()


def test_reindex_all_backfills_missing(tmp_path, monkeypatch):
    """reindex_all() recovers items that missed initial indexing."""
    setup_test_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    db = sq.get_db()

    # Create items normally (they get indexed)
    create_trade_case_study("Case A", "ETH breakout above key resistance with strong volume confirmation; entry at breakout candle, stop below support, target at prior highs.",
                            confidence="high_confidence")
    create_trade_case_study("Case B", "SOL mean reversion trade from oversold RSI on daily chart; position sized conservatively given macro uncertainty and thin liquidity.",
                            confidence="high_confidence")

    # Simulate historical gap: delete FTS entries directly
    db.execute("DELETE FROM knowledge_fts")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0] == 0

    # reindex_all should restore full coverage
    count = reindex_all()
    assert count == 2
    assert db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0] == 2
    reset_db()


def test_retrieve_lessons_returns_results_after_reindex(tmp_path, monkeypatch):
    """End-to-end: create → reindex → retrieve finds lessons."""
    setup_test_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    db = sq.get_db()

    create_trade_case_study("Momentum breakout ETH",
                            "ETH broke above 200MA with volume confirmation. "
                            "Entry at breakout, stop placed below support level.",
                            confidence="high_confidence")

    # Clear FTS to simulate the historical gap
    db.execute("DELETE FROM knowledge_fts")
    db.commit()

    # Verify retrieval returns nothing before reindex
    lessons = retrieve_lessons("ETH momentum breakout")
    assert len(lessons) == 0

    # After reindex, retrieval works
    reindex_all()
    lessons = retrieve_lessons("ETH momentum breakout")
    assert len(lessons) >= 1
    assert "ETH" in lessons[0].snippet or "ETH" in lessons[0].title
    reset_db()


def test_retrieve_lessons_structured_filters_trade_case_metadata(tmp_path, monkeypatch):
    """Symbol/direction/scanner filters must narrow broad FTS matches."""
    setup_test_env(tmp_path, monkeypatch)

    create_trade_case_study(
        "Case: BTC/USDT long (stop_loss)",
        "BTC/USDT momentum setup failed after entry and hit stop loss. The trade "
        "should teach the learning loop about this exact symbol, long direction, "
        "and momentum scanner family.",
        metadata={
            "symbol": "BTC/USDT",
            "direction": "long",
            "scanner_family": "momentum",
            "outcome": "loss",
        },
        confidence="high_confidence",
    )
    create_trade_case_study(
        "Case: SOL/USDT long (stop_loss)",
        "SOL/USDT momentum setup failed after entry and hit stop loss. The trade "
        "should teach the learning loop about this exact symbol, long direction, "
        "and momentum scanner family.",
        metadata={
            "symbol": "SOL/USDT",
            "direction": "long",
            "scanner_family": "momentum",
            "outcome": "loss",
        },
        confidence="high_confidence",
    )
    create_trade_case_study(
        "Case: BTC/USDT short (stop_loss)",
        "BTC/USDT momentum setup failed after short entry and hit stop loss. The trade "
        "should teach the learning loop about this exact symbol, short direction, "
        "and momentum scanner family.",
        metadata={
            "symbol": "BTC/USDT",
            "direction": "short",
            "scanner_family": "momentum",
            "outcome": "loss",
        },
        confidence="high_confidence",
    )

    broad = retrieve_lessons("momentum stop loss", item_types=["trade_case_study"], k=10)
    assert len(broad) == 3

    btc_long = retrieve_lessons(
        "momentum stop loss",
        item_types=["trade_case_study"],
        symbol="BTCUSDT",
        direction="long",
        scanner_family="momentum",
        k=10,
    )
    assert len(btc_long) == 1
    assert "BTC/USDT long" in btc_long[0].title
    assert btc_long[0].outcome == "loss"

    btc_long_reversion = retrieve_lessons(
        "momentum stop loss",
        item_types=["trade_case_study"],
        symbol="BTC/USDT",
        direction="long",
        scanner_family="mean_reversion",
        k=10,
    )
    assert btc_long_reversion == []
    reset_db()


def test_doctor_fts_probe(tmp_path, monkeypatch):
    """Doctor FTS coverage probe reports correct severity."""
    setup_test_env(tmp_path, monkeypatch)

    # No items yet → ok
    from aletheia.diagnostics.doctor import _probe_fts_coverage, Report
    report = Report()
    _probe_fts_coverage(report)
    assert report.checks[-1].severity == "ok"
    assert "no eligible" in report.checks[-1].detail

    # Create items with full coverage → ok
    create_research_note("Test note",
                         "Market structure analysis: BTC consolidating near support with declining volume suggests accumulation phase before next move.",
                         confidence="high_confidence")
    report = Report()
    _probe_fts_coverage(report)
    assert report.checks[-1].severity == "ok"
    assert "1/1" in report.checks[-1].detail

    # Simulate gap: delete FTS → coverage drops to 0%
    import aletheia.storage.sqlite as sq
    db = sq.get_db()
    db.execute("DELETE FROM knowledge_fts")
    db.commit()
    report = Report()
    _probe_fts_coverage(report)
    assert report.checks[-1].severity == "blocker"
    assert "0/1" in report.checks[-1].detail
    reset_db()
