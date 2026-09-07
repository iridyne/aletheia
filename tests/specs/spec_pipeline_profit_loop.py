"""Spec: Pipeline — Minimum Profit Loop front-half health

This file covers the candidate-to-position front half of the minimum profit loop:
    opportunities -> paper_trader ranks -> signal_pending pi_decisions
    -> System Operator LLM decides -> trade_proposals -> risk -> orders -> positions

Close/accounting/Outcome and measured Learning influence have separate executable
contracts; a fresh Proposal here is not evidence that the economic feedback loop
is complete or positive. This spec pins the invariants that keep this front half
OBSERVABLE and correctly fed,
so a silent stall surfaces in tests + `aletheia doctor` instead of reaching the
user. Born from the 2026-06-23 incident: the System Operator was fed a phantom $167
balance (wrong broker cache) and skipped every signal for 20h while the observe
stage kept producing opportunities — the system looked alive but earned nothing.

Contract: pipeline_profit_loop
Claim prefix: PL-PROFIT
"""

import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared DB setup
# ---------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
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
    return conn


def _put_setting(conn, key: str, value: str):
    conn.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# PL-PROFIT-01 — the root-cause invariant: trading context sees the REAL account
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PROFIT-01", "pipeline_profit_loop", "§balance multi-broker default",
        "L2b",
        "The unified cache reports external account equity, never the retired Bitget cache.",
    ),
)
def test_default_cached_state_reflects_active_broker(tmp_path, monkeypatch):
    import json
    import aletheia.config.loader as loader
    from aletheia.portfolio import exchange_sync

    conn = _setup_db(tmp_path, monkeypatch)

    # Legacy bitget "demo" cache holds an idle small balance.
    _put_setting(conn, "exchange_account_state.demo", json.dumps({
        "equity_usdt": 167.37,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }))
    # Unified (multi-broker) cache holds the true active total across brokers.
    _put_setting(conn, "exchange_account_state.unified", json.dumps({
        "total_equity_usdt": 88687.0,
        "total_available_usdt": 79500.0,
        "accounts": [
            {"broker_id": "bitget_demo", "equity_usdt": 167.37},
            {"broker_id": "okx_demo", "equity_usdt": 88519.63},
        ],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }))

    state = exchange_sync.get_unified_cached_state()
    from aletheia.portfolio.exchange_sync import account_equity_value

    equity = account_equity_value(state)
    assert equity == pytest.approx(88687.0), (
        f"unified cache must return account equity, got {equity} "
        "(regression: System Operator would see phantom-bankrupt $167 and skip every signal)"
    )



@spec_claim(
    SpecClaim(
        "PL-PROFIT-02", "pipeline_profit_loop", "§balance bitget-active",
        "L2b",
        "Bitget uses UTA only; a retired cache cannot supply account evidence.",
    ),
)
def test_bitget_active_rejects_legacy_cache(tmp_path, monkeypatch):
    import json
    import aletheia.config.loader as loader
    from aletheia.portfolio import exchange_sync

    conn = _setup_db(tmp_path, monkeypatch)
    _put_setting(conn, "exchange_account_state.demo", json.dumps({
        "equity_usdt": 5000.0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }))

    assert exchange_sync.get_unified_cached_state() is None


# ---------------------------------------------------------------------------
# PL-PROFIT-03/04 — doctor front-half probe catches the decoupling
# ---------------------------------------------------------------------------


def _insert_opportunity(conn, age_minutes: float):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    conn.execute(
        "INSERT INTO opportunities(id, symbol, market, direction_bias, status, "
        "execution_eligible, asset_class, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "BTC/USDT", "usdt_futures", "long", "open", 1, "crypto", ts),
    )
    conn.commit()


def _insert_proposal(conn, age_minutes: float):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    conn.execute(
        "INSERT INTO trade_proposals(id, symbol, market_type, direction, order_type, "
        "quantity, price, execution_mode, status, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "BTC/USDT", "usdt_futures", "long", "market",
         0.001, 50000.0, "okx_demo", "executed", ts),
    )
    conn.commit()
