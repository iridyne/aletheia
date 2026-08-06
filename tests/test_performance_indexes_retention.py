"""Hot-query plan and bounded-retention regression evidence."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import get_db, reset_db


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    run_migrations()
    return get_db()


def _plan(db, sql, params=()) -> str:
    return " ".join(row[3] for row in db.execute(f"EXPLAIN QUERY PLAN {sql}", params))


def test_hot_queries_use_matching_indexes(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)

    assert "idx_agent_runs_started_at" in _plan(
        db,
        "SELECT id FROM agent_runs WHERE started_at < ? ORDER BY started_at LIMIT 1000",
        ("2026-01-01",),
    )
    assert "idx_agent_run_steps_run_created" in _plan(
        db,
        "SELECT * FROM agent_run_steps WHERE run_id=? ORDER BY created_at",
        ("run",),
    )
    assert "idx_agent_tool_calls_run_created" in _plan(
        db,
        "SELECT * FROM agent_tool_calls WHERE run_id=? ORDER BY created_at",
        ("run",),
    )
    assert "idx_funding_symbol_exchange_observed" in _plan(
        db,
        "SELECT * FROM funding_rate_snapshots "
        "WHERE symbol=? AND exchange=? ORDER BY observed_at DESC LIMIT 1",
        ("BTC/USDT", "okx"),
    )
    assert "idx_oi_symbol_exchange_observed" in _plan(
        db,
        "SELECT * FROM open_interest_snapshots "
        "WHERE symbol=? AND exchange=? ORDER BY observed_at DESC LIMIT 1",
        ("BTC/USDT", "okx"),
    )
    assert "idx_filter_decision_batches_evaluated" in _plan(
        db,
        "SELECT rowid FROM filter_decision_batches WHERE evaluated_at < ?",
        ("2026-01-01",),
    )
    assert "idx_filter_candidate_outcomes_entry" in _plan(
        db,
        "SELECT rowid FROM filter_candidate_outcomes WHERE entry_ts < ?",
        ("2026-01-01",),
    )
    assert "idx_market_ohlcv_timeframe_ts" in _plan(
        db,
        "SELECT exchange,symbol,market_type,timeframe,ts FROM market_ohlcv "
        "WHERE timeframe=? AND ts < ?",
        ("1m", 1),
    )
    assert "idx_opportunities_status_created" in _plan(
        db,
        "SELECT id FROM opportunities WHERE status='open' "
        "AND julianday(created_at)>=julianday(?) ORDER BY julianday(created_at)",
        ("2026-01-01",),
    )
    assert "idx_filter_decisions_opportunity_evaluated" in _plan(
        db,
        "SELECT run_id,evaluated_at FROM filter_decisions WHERE opportunity_id=? "
        "AND julianday(evaluated_at)>=julianday(?) "
        "ORDER BY julianday(evaluated_at) DESC,rowid DESC LIMIT 1",
        ("opportunity", "2026-01-01"),
    )
    assert "idx_filter_decisions_opportunity_run" in _plan(
        db,
        "SELECT filter_name FROM filter_decisions "
        "WHERE opportunity_id=? AND run_id=? ORDER BY stage,sequence_no,rowid",
        ("opportunity", "run"),
    )
    reset_db()


def test_sqlite_transaction_timing_can_be_enabled_without_logging_values(
    tmp_path, monkeypatch, caplog,
):
    db = _setup_db(tmp_path, monkeypatch)
    monkeypatch.setenv("ALETHEIA_SQLITE_TIMING", "1")

    with caplog.at_level(logging.INFO, logger="aletheia.storage.sqlite"):
        with repo.transaction():
            db.execute(
                "INSERT INTO market_snapshots(id,symbol,market_type,price,snapshot_at) "
                "VALUES(?,?,?,?,?)",
                ("timing-row", "SENSITIVE/SYMBOL", "spot", 100.0, "2026-01-01"),
            )

    messages = [record.getMessage() for record in caplog.records]
    assert any("sqlite_write_transaction wait_ms=" in message for message in messages)
    assert all("SENSITIVE/SYMBOL" not in message for message in messages)
    reset_db()


def test_retention_deletes_in_bounded_batches_and_records_metrics(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    db.executemany(
        "INSERT INTO market_snapshots(id,symbol,market_type,price,snapshot_at) "
        "VALUES(?,?,?,?,?)",
        [(f"m{i}", "BTC/USDT", "spot", 100.0, "2020-01-01") for i in range(5)],
    )
    before = repo.retention_metrics_snapshot().get("market_snapshots", {}).get("batches", 0)

    deleted = repo._delete_by_rowid_batches(
        "market_snapshots",
        "snapshot_at < ?",
        ("2021-01-01",),
        batch_size=2,
    )

    metrics = repo.retention_metrics_snapshot()["market_snapshots"]
    assert deleted == 5
    assert metrics["batches"] - before == 3
    assert metrics["deleted"] >= 5
    assert metrics["max_batch_ms"] >= 0
    reset_db()


def test_ohlcv_rolling_prune_waits_for_margin(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    monkeypatch.setattr(repo, "_OHLCV_PRUNE_MARGIN", 1)
    candles = [
        SimpleNamespace(
            timestamp=i,
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=10.0,
        )
        for i in range(4)
    ]

    repo.upsert_ohlcv_candles(
        "BTC/USDT",
        "1m",
        candles,
        exchange="okx",
        market_type="spot",
        window=2,
    )

    rows = db.execute(
        "SELECT ts FROM market_ohlcv ORDER BY ts"
    ).fetchall()
    assert [row[0] for row in rows] == [2, 3]
    reset_db()
