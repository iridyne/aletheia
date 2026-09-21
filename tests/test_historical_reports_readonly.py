"""Historical reports must not acquire database creation or write authority."""

import importlib
import sqlite3

import pytest


@pytest.mark.parametrize("module,entry", [
    ("debate_impact", "build_debate_impact_audit"),
    ("trade_funnel", "build_trade_funnel_audit"),
])
@pytest.mark.parametrize("exists", [False, True])
def test_historical_report_uses_only_readonly_database(tmp_path, monkeypatch, module, entry, exists):
    import aletheia.storage.sqlite as sq

    path = tmp_path / "history.db"
    if exists:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE sentinel (value TEXT)")
            connection.execute("INSERT INTO sentinel VALUES ('preserved')")
    sq.reset_db()
    sq._db_conn = None
    monkeypatch.setattr(sq, "db_path", lambda: path)
    original_get_db = sq.get_db
    observations = []

    def checked_get_db():
        connection = original_get_db()
        observations.append(connection.execute("PRAGMA query_only").fetchone()[0])
        assert observations[-1] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE sentinel SET value='must not persist'")
        return connection

    report_module = importlib.import_module(f"aletheia.analytics.{module}")
    monkeypatch.setattr(sq, "get_db", checked_get_db)
    if hasattr(report_module, "get_db"):
        monkeypatch.setattr(report_module, "get_db", checked_get_db)
    try:
        report = getattr(report_module, entry)(hours=24)
        assert isinstance(report, dict)
        assert path.exists() is exists
        if exists:
            assert observations == [1]
            with sqlite3.connect(path) as connection:
                assert connection.execute("SELECT value FROM sentinel").fetchall() == [("preserved",)]
    finally:
        sq.reset_db()
        sq._db_conn = None
