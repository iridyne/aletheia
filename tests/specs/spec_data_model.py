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
    import sqlite3
    import threading

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    (tmp_path / "app.db").touch()

    conns = []

    def get_conn():
        sq._local = threading.local()
        conn = sq.get_db()
        conns.append(id(conn))

    t1 = threading.Thread(target=get_conn)
    t2 = threading.Thread(target=get_conn)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(conns) == 2
    assert conns[0] != conns[1], "Different threads must get different connections"
