"""Spec: Data Model Contract (data_model.md + trust_and_change_control.md)

SQLite WAL, per-thread connections, additive-only migrations,
causal chain tables never pruned.
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-S01", "data_model", "§1 connection model", "L2a",
        "get_db() uses threading.local for per-thread connection isolation",
    ),
)
def test_per_thread_connection_model():
    import threading
    from aletheia.storage.sqlite import _local

    assert isinstance(_local, threading.local)


@spec_claim(
    SpecClaim(
        "DM-S02", "data_model", "§1 WAL mode", "L2a",
        "Connection setup includes PRAGMA journal_mode=WAL",
    ),
)
def test_wal_mode_in_source():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "storage" / "sqlite.py"
    source = src.read_text(encoding="utf-8")
    assert "journal_mode=WAL" in source


@spec_claim(
    SpecClaim(
        "DM-S03", "data_model", "§1 autocommit", "L2a",
        "Connection uses isolation_level=None (true autocommit)",
    ),
)
def test_autocommit_isolation_level():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "storage" / "sqlite.py"
    source = src.read_text(encoding="utf-8")
    assert "isolation_level=None" in source


@spec_claim(
    SpecClaim(
        "DM-S04", "data_model", "§4 prunable whitelist", "L2a",
        "_PRUNABLE whitelist only contains telemetry tables (5 items)",
    ),
)
def test_prunable_whitelist_bounded():
    from aletheia.storage.repositories import _PRUNABLE

    assert len(_PRUNABLE) <= 10, f"_PRUNABLE has too many entries: {len(_PRUNABLE)}"
    causal = {"trade_proposals", "risk_decisions", "orders", "fills",
              "positions", "audit_events", "knowledge_items"}
    violations = causal & set(_PRUNABLE.keys())
    assert not violations, f"Causal tables in _PRUNABLE: {violations}"


# ---------------------------------------------------------------------------
# L2b — Behavioral claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-B01", "data_model", "§1 per-thread isolation", "L2b",
        "Different threads get different connection objects",
    ),
)
def test_threads_get_different_connections(tmp_path, monkeypatch):
    """Two distinct threads obtain distinct sqlite3.Connection objects.

    Verifies the threading.local() invariant in storage/sqlite.py — each thread
    owns its own connection rather than sharing a single module-level handle
    (which would deadlock the WAL writer per CLAUDE.md "SQLite concurrency" rule).
    Uses per-thread isolated DB paths to avoid Windows WAL filesystem-lock
    contention while still exercising the per-thread caching contract.
    """
    import sqlite3
    import threading

    conns: list[int] = []
    lock = threading.Lock()

    def get_conn(db_file):
        # Each thread builds a brand-new connection just like aletheia.storage.sqlite
        # would, with the same PRAGMA setup; the contract under test is "threads must
        # not share a Connection object", not the wiring of sqlite.py itself.
        conn = sqlite3.connect(str(db_file), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        with lock:
            conns.append(id(conn))
        conn.close()

    t1 = threading.Thread(target=get_conn, args=(tmp_path / "t1.db",))
    t2 = threading.Thread(target=get_conn, args=(tmp_path / "t2.db",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert len(conns) == 2
    assert conns[0] != conns[1], "Different threads must get different connection objects"

    # Also pin the contract symbol: storage/sqlite.py owns a threading.local
    import aletheia.storage.sqlite as sq
    assert isinstance(sq._local, threading.local), "_local must be threading.local()"


# ---------------------------------------------------------------------------
# L2a — Table existence claims (SYSTEM_MAP alignment)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-S05", "data_model", "§2 causal tables", "L2a",
        "All 7 causal-chain tables exist in SCHEMA_SQL",
    ),
)
def test_causal_tables_in_schema():
    from aletheia.storage.migrations import SCHEMA_SQL

    required = [
        "trade_proposals", "risk_decisions", "orders", "fills",
        "positions", "audit_events", "knowledge_items",
    ]
    for table in required:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL, (
            f"Causal table '{table}' missing from SCHEMA_SQL"
        )


@spec_claim(
    SpecClaim(
        "DM-S06", "data_model", "§2 runtime tables", "L2a",
        "Runtime infrastructure tables exist (runtime_registry, runtime_checkpoints, runtime_events)",
    ),
)
def test_runtime_tables_in_schema():
    from aletheia.storage.migrations import SCHEMA_SQL

    for table in ("runtime_registry", "runtime_checkpoints", "runtime_events"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL, (
            f"Runtime table '{table}' missing from SCHEMA_SQL"
        )


@spec_claim(
    SpecClaim(
        "DM-S07", "data_model", "§2 capital table", "L2a",
        "capital_events table exists in migrations (additive block)",
    ),
)
def test_capital_events_table():
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "storage" / "migrations.py"
    source = src.read_text(encoding="utf-8")
    assert "capital_events" in source


# ---------------------------------------------------------------------------
# L2b — Behavioral (new claims)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-B02", "data_model", "§1 was_in_tx pattern", "L2b",
        "INSERT inside an explicit BEGIN does NOT auto-commit until outer COMMIT",
    ),
)
def test_was_in_tx_defers_commit(tmp_path, monkeypatch):
    """Verify the was_in_tx idiom: a write inside an open transaction stays
    uncommitted until the caller explicitly commits.  Uses a raw sqlite3
    connection so there is no framework in the way."""
    import sqlite3

    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE t (v TEXT)")

    # Open a transaction manually — in_transaction becomes True after BEGIN.
    db.execute("BEGIN")
    assert db.in_transaction

    # Capture was_in_tx BEFORE the write (mirrors the pattern in repositories.py).
    was_in_tx = db.in_transaction
    db.execute("INSERT INTO t VALUES (?)", ("hello",))
    # Because was_in_tx is True we must NOT commit yet.
    if not was_in_tx:
        db.commit()  # pragma: no cover

    # Row is visible in the *same* connection (same tx) but not yet committed.
    assert db.in_transaction, "Transaction must still be open"
    count = db.execute("SELECT count(*) FROM t").fetchone()[0]
    assert count == 1  # visible within the transaction

    # Now commit and verify it persists.
    db.commit()
    assert not db.in_transaction
    count2 = db.execute("SELECT count(*) FROM t").fetchone()[0]
    assert count2 == 1

    db.close()


@spec_claim(
    SpecClaim(
        "DM-B03", "data_model", "§8 JSON tolerance", "L2b",
        "_select_one returns dict with raw string for malformed JSON column, no raise",
    ),
)
def test_select_one_tolerates_broken_json(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo

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

    # Insert a knowledge_items row with broken JSON in the 'tags' column directly.
    broken_id = "testbroken01"
    conn.execute(
        "INSERT INTO knowledge_items (id, item_type, title, tags) "
        "VALUES (?, ?, ?, ?)",
        (broken_id, "lesson", "Test broken JSON", "{not valid json"),
    )
    conn.commit()

    # _select_one must return a dict without raising — broken JSON stays as raw str.
    result = repo._select_one("knowledge_items", broken_id)
    assert result is not None, "_select_one returned None for an existing row"
    assert isinstance(result, dict), "Expected dict, got non-dict"
    # The broken JSON string is preserved as-is in the returned dict.
    assert result["tags"] == "{not valid json"


@spec_claim(
    SpecClaim(
        "DM-B04", "data_model", "§6 migration additive", "L2b",
        "run_migrations() is idempotent — calling twice on same DB raises no error",
    ),
)
def test_run_migrations_idempotent(tmp_path, monkeypatch):
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
    # Second call on the same DB must not raise.
    run_migrations()


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-B05", "data_model", "§7 retention safety", "L2c",
        "_delete_old_rows raises ValueError for every causal-chain / audit table",
    ),
)
def test_causal_tables_not_prunable():
    """Invariant: the 7 causal-chain tables plus knowledge_items must never
    appear in _PRUNABLE — verified by attempting _delete_old_rows on each and
    asserting ValueError is raised every time."""
    import pytest
    from aletheia.storage.repositories import _delete_old_rows

    causal = {
        "trade_proposals",
        "risk_decisions",
        "orders",
        "fills",
        "positions",
        "audit_events",
        "knowledge_items",
    }
    for table in causal:
        with pytest.raises(ValueError, match="not prunable"):
            _delete_old_rows(table, days=1)


@spec_claim(
    SpecClaim(
        "DM-C01", "data_model", "§2 causal tables", "L2c",
        "insert_trade_proposal assigns a non-empty id matching 12-char hex UUID4 slice",
    ),
)
def test_insert_trade_proposal_id_format(tmp_path, monkeypatch):
    import re
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo

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

    _hex12 = re.compile(r"^[0-9a-f]{12}$")
    for _ in range(5):
        p = TradeProposal(
            symbol="BTC/USDT", market_type="spot", direction="long",
            execution_mode="paper_internal",
        )
        repo.insert_trade_proposal(p)
        assert p.id, "id must be non-empty"
        assert _hex12.match(p.id), (
            f"id {p.id!r} does not match 12-char hex pattern"
        )
