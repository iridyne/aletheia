"""Spec: Pipeline — Paper Trade (end-to-end)

E2E pipeline claims for opportunity → paper_trader → pi_decisions(signal_pending)
→ risk gate → execution chain.

NOTE: paper_trader is FROZEN. Tests use only public entry points.

Contract: pipeline_paper_trade
Claim prefix: PL-PT
"""

import uuid
from datetime import datetime, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared DB setup
# ---------------------------------------------------------------------------


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


def _insert_market_snapshot(conn, symbol: str = "BTC/USDT", price: float = 60000.0):
    """Insert a fresh market snapshot to satisfy paper_trader's price lookups."""
    snap_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots (id, symbol, market_type, price, volume_24h, "
        "change_pct_24h, snapshot_at, provider) VALUES (?,?,?,?,?,?,?,?)",
        (snap_id, symbol, "spot", price, 1_000_000.0, 0.5, now, "ccxt"),
    )
    conn.commit()
    return snap_id


# ---------------------------------------------------------------------------
# PL-PT-01  L2b  happy_path: paper_trader produces signal_pending pi_decision
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# PL-PT-02  L2b  ranker_filter: low-score opportunities don't produce signals
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# PL-PT-03  L2c  symbol_consistency: BTC/USDT flows through as canonical format
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# PL-PT-04  L2b  xfail  opportunity_id_propagation  (captures PT-2)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# PL-PT-05  L2b  xfail  signal_consumed  (captures PT-1)
# ---------------------------------------------------------------------------














@spec_claim(
    SpecClaim(
        "PL-PT-09b",
        "pipeline_paper_trade",
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
        "pipeline_paper_trade",
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














@spec_claim(
    SpecClaim(
        "PL-PT-12",
        "pipeline_paper_trade",
        "§4 approved take-profit identity",
        "L2b",
        "A paper_internal Position inherits take_profit_price exactly from its approved "
        "Proposal; the broker never adds a config-derived take-profit after approval.",
        status="implemented",
    )
)
def test_paper_position_take_profit_exactly_matches_approved_proposal(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)

    import yaml

    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"trading": {"default_take_profit_pct": 4.0}}),
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots "
        "(id,symbol,market_type,exchange,price,snapshot_at,provider) "
        "VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "BTC/USDT", "spot", "binance", 60_000.0, now, "ccxt"),
    )
    conn.commit()

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    opportunity = Opportunity(
        id="paper-tp-identity", symbol="BTC/USDT", market="spot",
        direction_bias="long", status="queued",
    )
    repo.insert_opportunity(opportunity)

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        stop_price=58_000.0,
        take_profit=63_000.0,
        risk_reward=1.5,
        execution_mode="paper_internal",
        opportunity_id=opportunity.id,
        agent_rationale="approved with an immutable take-profit",
    )

    proposal = conn.execute(
        "SELECT id, take_profit FROM trade_proposals WHERE id=?",
        (result["proposal_id"],),
    ).fetchone()
    authority = conn.execute(
        "SELECT approved FROM risk_decisions WHERE proposal_id=? "
        "AND decision_role='authoritative'",
        (result["proposal_id"],),
    ).fetchone()
    position = conn.execute(
        "SELECT take_profit_price FROM positions WHERE id=?",
        (result["position_id"],),
    ).fetchone()

    assert result["status"] == "executed"
    assert authority is not None and authority["approved"] == 1
    assert proposal["take_profit"] == 63_000.0
    assert position["take_profit_price"] == proposal["take_profit"]






@spec_claim(
    SpecClaim(
        "PL-PT-16",
        "pipeline_paper_trade",
        "§4 local market-only materialization",
        "L2b",
        "paper_internal rejects a non-market entry unless an exact durable Entry Plan authority owns it.",
        status="implemented",
    )
)
def test_paper_broker_rejects_local_limit_before_materialization(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.paper_broker import execute_paper_order
    from tests.execution_authority import seed_order_authority

    seed_order_authority(
        proposal_id="local-limit-proposal",
        risk_decision_id="local-limit-risk",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="limit",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
    )

    with pytest.raises(ValueError, match="paper_internal_market_only"):
        execute_paper_order(
            proposal_id="local-limit-proposal",
            risk_decision_id="local-limit-risk",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="limit",
            quantity=0.001,
            price=60_000.0,
            execution_mode="paper_internal",
        )

    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-PT-17",
        "pipeline_paper_trade",
        "§4 single-Proposal position lineage",
        "L2b",
        "A direct paper broker call for a different Proposal never averages into the first "
        "Position; every Position and entry Order retain one Proposal lineage.",
        status="implemented",
    )
)
def test_paper_broker_never_aggregates_different_proposals(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.paper_broker import execute_paper_order
    from tests.execution_authority import seed_order_authority

    for suffix, price in (("first", 100.0), ("second", 110.0)):
        seed_order_authority(
            proposal_id=f"cross-proposal-{suffix}",
            risk_decision_id=f"cross-risk-{suffix}",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=price,
            execution_mode="paper_internal",
            strategy_id="same-strategy",
        )
        execute_paper_order(
            proposal_id=f"cross-proposal-{suffix}",
            risk_decision_id=f"cross-risk-{suffix}",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=price,
            execution_mode="paper_internal",
            strategy_id="same-strategy",
        )

    positions = conn.execute(
        "SELECT id,proposal_id,quantity FROM positions ORDER BY rowid"
    ).fetchall()
    orders = conn.execute(
        "SELECT proposal_id,position_id FROM orders ORDER BY rowid"
    ).fetchall()
    assert [dict(row) for row in positions] == [
        {"id": positions[0]["id"], "proposal_id": "cross-proposal-first", "quantity": 1.0},
        {"id": positions[1]["id"], "proposal_id": "cross-proposal-second", "quantity": 1.0},
    ]
    assert [row["proposal_id"] for row in orders] == [
        "cross-proposal-first", "cross-proposal-second",
    ]
    assert [row["position_id"] for row in orders] == [
        positions[0]["id"], positions[1]["id"],
    ]


@spec_claim(
    SpecClaim(
        "PL-PT-18",
        "pipeline_paper_trade",
        "§4 duplicate-symbol gateway gate",
        "L2c",
        "A second normal local Proposal for the same open instrument is rejected by Risk and "
        "does not become a hidden averaging leg in the first Position.",
        status="implemented",
    )
)
def test_gateway_rejects_second_normal_proposal_without_aggregation(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots "
        "(id,symbol,market_type,exchange,price,snapshot_at,provider) "
        "VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "BTC/USDT", "spot", "binance", 100.0, now, "ccxt"),
    )
    conn.commit()

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    first_opportunity = Opportunity(
        id="gateway-first-opportunity", symbol="BTC/USDT", market="spot",
        direction_bias="long", status="queued",
    )
    second_opportunity = Opportunity(
        id="gateway-second-opportunity", symbol="BTC/USDT", market="spot",
        direction_bias="long", status="queued",
    )
    repo.insert_opportunity(first_opportunity)
    repo.insert_opportunity(second_opportunity)

    first = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
        opportunity_id=first_opportunity.id,
    )
    second = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
        opportunity_id=second_opportunity.id,
    )

    assert first["status"] == "executed", first
    assert second["status"] == "rejected", second
    position = conn.execute(
        "SELECT proposal_id,quantity FROM positions"
    ).fetchone()
    assert dict(position) == {"proposal_id": first["proposal_id"], "quantity": 1.0}
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
