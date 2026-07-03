"""Spec: Pipeline — Minimum Profit Loop health (end-to-end)

The minimum profit loop is the system's core revenue closed loop:
    opportunities -> paper_trader ranks -> signal_pending pi_decisions
    -> System Operator LLM decides -> trade_proposals -> risk -> orders -> positions

This spec pins the invariants that keep that loop OBSERVABLE and correctly fed,
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
        "get_cached_state() default path returns the active account's true equity "
        "(unified cache) when the active paper broker is non-Bitget — not the idle "
        "Bitget demo balance. Prevents System Operator phantom-bankruptcy skip.",
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

    # Config: exchange.mode stays "demo" (the demo/live binary used by risk #9b),
    # but the active paper broker has migrated to okx_demo.
    class _Exch:
        mode = "demo"
        paper_execution_mode = "okx_demo"

    class _Cfg:
        exchange = _Exch()

    monkeypatch.setattr(loader, "load_config", lambda: _Cfg())
    monkeypatch.setattr("aletheia.portfolio.exchange_sync.load_config", lambda: _Cfg())

    # Default path (mode=None) — what trading_context.py:61 calls.
    state = exchange_sync.get_cached_state()
    equity = state.get("equity_usdt") or state.get("total_equity_usdt")
    assert equity == pytest.approx(88687.0), (
        f"default get_cached_state() must return active-account equity, got {equity} "
        "(regression: System Operator would see phantom-bankrupt $167 and skip every signal)"
    )

    # Explicit mode="demo" (CLI bitget tools) must still get the per-mode cache.
    legacy = exchange_sync.get_cached_state(mode="demo")
    assert legacy.get("equity_usdt") == pytest.approx(167.37), (
        "explicit mode='demo' must still return the Bitget per-mode cache verbatim"
    )


@spec_claim(
    SpecClaim(
        "PL-PROFIT-02", "pipeline_profit_loop", "§balance bitget-active",
        "L2b",
        "When the active paper broker IS Bitget, default get_cached_state() keeps "
        "reading the per-mode cache (no behavior change for Bitget-native setups).",
    ),
)
def test_bitget_active_keeps_legacy_cache(tmp_path, monkeypatch):
    import json
    import aletheia.config.loader as loader
    from aletheia.portfolio import exchange_sync

    conn = _setup_db(tmp_path, monkeypatch)
    _put_setting(conn, "exchange_account_state.demo", json.dumps({
        "equity_usdt": 5000.0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }))

    class _Exch:
        mode = "demo"
        paper_execution_mode = "bitget_demo"

    class _Cfg:
        exchange = _Exch()

    monkeypatch.setattr(loader, "load_config", lambda: _Cfg())
    monkeypatch.setattr("aletheia.portfolio.exchange_sync.load_config", lambda: _Cfg())

    state = exchange_sync.get_cached_state()
    assert state.get("equity_usdt") == pytest.approx(5000.0), (
        "Bitget-active setups must still use the per-mode cache on the default path"
    )


# ---------------------------------------------------------------------------
# PL-PROFIT-03/04 — doctor profit-loop probe catches the decoupling
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


@spec_claim(
    SpecClaim(
        "PL-PROFIT-03", "pipeline_profit_loop", "§doctor stall detection",
        "L2b",
        "doctor _probe_profit_loop emits a WARN when opportunities are fresh "
        "(<60min) but no proposal has been created for >6h — the silent-stall "
        "signature that looks alive but earns nothing.",
    ),
)
def test_doctor_flags_profit_loop_stall(tmp_path, monkeypatch):
    from aletheia.diagnostics import doctor

    conn = _setup_db(tmp_path, monkeypatch)
    # Opportunities flowing, but newest proposal is 10h old → stall.
    _insert_opportunity(conn, age_minutes=5)
    _insert_proposal(conn, age_minutes=600)

    report = doctor.Report(generated_at=datetime.now(timezone.utc).isoformat())
    doctor._probe_profit_loop(report)

    rows = [c for c in report.checks if c.name == "loop.profit"]
    assert rows, "profit-loop probe produced no check"
    assert rows[0].severity == "warn", (
        f"expected WARN for stalled loop, got {rows[0].severity}: {rows[0].detail}"
    )
    assert "STALL" in rows[0].detail.upper()


@spec_claim(
    SpecClaim(
        "PL-PROFIT-04", "pipeline_profit_loop", "§doctor healthy path",
        "L2b",
        "doctor _probe_profit_loop reports ok when proposals are recent — no "
        "false alarm on a healthy loop.",
    ),
)
def test_doctor_profit_loop_healthy(tmp_path, monkeypatch):
    from aletheia.diagnostics import doctor

    conn = _setup_db(tmp_path, monkeypatch)
    _insert_opportunity(conn, age_minutes=5)
    _insert_proposal(conn, age_minutes=20)  # fresh proposal

    report = doctor.Report(generated_at=datetime.now(timezone.utc).isoformat())
    doctor._probe_profit_loop(report)

    rows = [c for c in report.checks if c.name == "loop.profit"]
    assert rows, "profit-loop probe produced no check"
    assert rows[0].severity == "ok", (
        f"expected ok for healthy loop, got {rows[0].severity}: {rows[0].detail}"
    )
