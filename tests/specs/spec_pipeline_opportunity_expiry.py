"""Opportunity expiry and rejection evidence remain atomic."""

import uuid
from datetime import datetime, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _setup_db(tmp_path, monkeypatch):
    """Wire a fresh isolated SQLite DB into aletheia storage for one test."""
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._config_cache = None
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    return conn


def _insert_opportunity(conn, symbol: str = "BTC/USDT", confidence_score: float = 0.8,
                         direction_bias: str = "long", status: str = "open") -> str:
    """Insert an opportunity row and return its id."""
    opp_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO opportunities (id, symbol, market, direction_bias, confidence, "
        "confidence_score, status, asset_class, scanner_family, valid_until, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (opp_id, symbol, "spot", direction_bias, "medium", confidence_score,
         status, "crypto", "momentum", "2099-01-01T00:00:00", now),
    )
    conn.commit()
    return opp_id


@spec_claim(
    SpecClaim(
        "PL-PT-09b",
        "pipeline_opportunity_expiry",
        "§2 opportunity expiry evidence",
        "L2b",
        "Open Opportunity TTL expiry and its durable ttl_expired rejection fact are "
        "one transaction; an evidence write failure leaves the Opportunity open for retry.",
        status="implemented",
    ),
)
def test_open_opportunity_ttl_expiry_is_atomic_with_rejection_fact(
    tmp_path, monkeypatch
):
    import sqlite3

    conn = _setup_db(tmp_path, monkeypatch)
    opp_id = _insert_opportunity(conn, symbol="ETH/USDT", confidence_score=85)
    conn.execute(
        "UPDATE opportunities SET created_at=?, valid_until=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", "2020-01-01T00:30:00+00:00", opp_id),
    )
    conn.commit()

    from aletheia.storage.repositories import expire_stale_opportunities

    assert expire_stale_opportunities(ttl_hours=1) == 1
    row = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,)
    ).fetchone()
    evidence = conn.execute(
        "SELECT decision, reason_code FROM filter_decisions "
        "WHERE opportunity_id=? AND filter_name='opportunity_ttl'",
        (opp_id,),
    ).fetchone()
    assert row["status"] == "expired"
    assert tuple(evidence) == ("rejected", "ttl_expired")

    retry_id = _insert_opportunity(conn, symbol="SOL/USDT", confidence_score=85)
    conn.execute(
        "UPDATE opportunities SET created_at=?, valid_until=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", "2020-01-01T00:30:00+00:00", retry_id),
    )
    conn.execute(
        "CREATE TRIGGER reject_ttl_evidence BEFORE INSERT ON filter_decisions "
        "WHEN NEW.filter_name='opportunity_ttl' BEGIN "
        "SELECT RAISE(ABORT, 'forced ttl evidence failure'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced ttl evidence failure"):
        expire_stale_opportunities(ttl_hours=1)
    retry = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (retry_id,)
    ).fetchone()
    assert retry["status"] == "open"


@spec_claim(
    SpecClaim(
        "PL-PT-09c",
        "pipeline_opportunity_expiry",
        "§2 nested opportunity expiry transaction",
        "L2b",
        "Historical open Opportunity TTL expiry uses a nested savepoint so a caller "
        "that catches evidence failure cannot commit a status-only lifecycle change.",
        status="implemented",
    ),
)
def test_ttl_expiry_savepoint_is_atomic_inside_caller_transaction(
    tmp_path, monkeypatch
):
    """A caller that catches expiry failure cannot commit a status-only write."""
    import sqlite3
    source_status = "open"

    conn = _setup_db(tmp_path, monkeypatch)
    opp_id = _insert_opportunity(
        conn,
        symbol="DOGE/USDT",
        confidence_score=85,
        status=source_status,
    )
    conn.execute(
        "UPDATE opportunities SET created_at=?, valid_until=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", "2020-01-01T00:30:00+00:00", opp_id),
    )
    conn.execute(
        "CREATE TRIGGER reject_nested_ttl_evidence "
        "BEFORE INSERT ON filter_decisions "
        "WHEN NEW.filter_name='opportunity_ttl' BEGIN "
        "SELECT RAISE(ABORT, 'forced nested ttl evidence failure'); END"
    )
    conn.commit()

    from aletheia.storage.repositories import (
        expire_stale_opportunities,
        transaction,
    )

    expire = expire_stale_opportunities
    with transaction():
        try:
            expire()
        except sqlite3.IntegrityError as exc:
            assert "forced nested ttl evidence failure" in str(exc)
        else:
            pytest.fail("fixture must reject the ttl evidence insert")

    status = conn.execute(
        "SELECT status FROM opportunities WHERE id=?", (opp_id,)
    ).fetchone()["status"]
    evidence_count = conn.execute(
        "SELECT COUNT(*) FROM filter_decisions "
        "WHERE opportunity_id=? AND filter_name='opportunity_ttl'",
        (opp_id,),
    ).fetchone()[0]
    assert status == source_status
    assert evidence_count == 0
