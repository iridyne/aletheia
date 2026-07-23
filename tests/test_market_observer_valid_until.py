"""Behavioral tests for Issue #181: valid_until on Market Observer Opportunities.

Every Opportunity emitted by _analyze_symbol() must have a parseable deadline.
When a Universe member expiry is passed via member_expires_at, it is used verbatim.
When not passed, the scanner family TTL table provides the deadline.
"""

from __future__ import annotations

import sqlite3

import pytest


def _prepare_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()


def _make_prefetched():
    from aletheia.agents.market_observer import _SymbolPrefetch

    return _SymbolPrefetch(
        context_str="BTC/USDT: price=50000 24h_change=2%",
        debug={},
        pre_filter={
            "passed": False,
            "rsi": None,
            "atr_pct": None,
            "keyword_hits": [],
            "matched_rules": [],
        },
        signal_score=0.0,
        funding_rate_val=0.01,
        open_interest_val=1e9,
        oi_delta_val=0.05,
    )


def _patch_mocks(monkeypatch):
    import aletheia.agents.market_observer as mo
    import aletheia.learning.prompt_bundles as pb

    async def _fake_llm(*a, **kw):
        return {
            "direction_bias": "long",
            "confidence": "high",
            "confidence_score": 70,
            "rationale": "momentum up",
            "key_observations": ["RSI overbought"],
        }, None

    monkeypatch.setattr(mo, "llm_structured", _fake_llm)
    monkeypatch.setattr(mo, "record_step", lambda *a, **kw: None)
    monkeypatch.setattr(mo, "log_tool", lambda *a, **kw: None)
    monkeypatch.setattr(pb, "authorize_observer_learning_bundle", lambda *a, **kw: ("", None))


async def test_member_expires_at_used_verbatim(tmp_path, monkeypatch):
    """member_expires_at is threaded through to valid_until without modification."""
    _prepare_db(tmp_path, monkeypatch)
    _patch_mocks(monkeypatch)

    from aletheia.agents.market_observer import _analyze_symbol
    from aletheia.storage.sqlite import get_db

    member_expiry = "2026-07-22T23:00:00+00:00"
    opp_id, err = await _analyze_symbol(
        symbol="BTC/USDT",
        market_type="futures",
        permission_mode="Observe",
        run_id="test-run-valid-until-001",
        linked_strategy_id=None,
        memory_block="",
        scanner_family="momentum",
        member_expires_at=member_expiry,
        force_full=True,
        _prefetched=_make_prefetched(),
    )

    assert err is None, f"unexpected error: {err}"
    assert opp_id is not None, "no opportunity was created"

    row = get_db().execute(
        "SELECT valid_until FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    assert row is not None, "opportunity not found in DB"
    assert row["valid_until"] == member_expiry, (
        f"expected valid_until={member_expiry!r}, got {row['valid_until']!r}"
    )


async def test_momentum_family_ttl_used_when_no_member_expiry(tmp_path, monkeypatch):
    """No member_expires_at + scanner_family=momentum → valid_until ≈ now + 1800s."""
    from datetime import datetime, timezone, timedelta

    _prepare_db(tmp_path, monkeypatch)
    _patch_mocks(monkeypatch)

    from aletheia.agents.market_observer import _analyze_symbol
    from aletheia.storage.sqlite import get_db

    before = datetime.now(timezone.utc)
    opp_id, err = await _analyze_symbol(
        symbol="ETH/USDT",
        market_type="futures",
        permission_mode="Observe",
        run_id="test-run-valid-until-002",
        linked_strategy_id=None,
        memory_block="",
        scanner_family="momentum",
        force_full=True,
        _prefetched=_make_prefetched(),
    )
    after = datetime.now(timezone.utc)

    assert err is None, f"unexpected error: {err}"
    assert opp_id is not None, "no opportunity was created"

    row = get_db().execute(
        "SELECT valid_until FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    assert row is not None, "opportunity not found in DB"
    assert row["valid_until"] is not None, "valid_until must always be set"

    valid_until_dt = datetime.fromisoformat(row["valid_until"])
    if valid_until_dt.tzinfo is None:
        valid_until_dt = valid_until_dt.replace(tzinfo=timezone.utc)

    # momentum TTL = 1800s; allow ±2s for test execution time
    assert before + timedelta(seconds=1798) <= valid_until_dt <= after + timedelta(seconds=1802), (
        f"valid_until {valid_until_dt.isoformat()} not in expected range "
        f"[{(before + timedelta(seconds=1798)).isoformat()}, "
        f"{(after + timedelta(seconds=1802)).isoformat()}]"
    )


async def test_unknown_family_falls_back_to_3600s_ttl(tmp_path, monkeypatch):
    """An unrecognised scanner_family falls back to the 3600s default TTL."""
    from datetime import datetime, timezone, timedelta

    _prepare_db(tmp_path, monkeypatch)
    _patch_mocks(monkeypatch)

    from aletheia.agents.market_observer import _analyze_symbol
    from aletheia.storage.sqlite import get_db

    before = datetime.now(timezone.utc)
    opp_id, err = await _analyze_symbol(
        symbol="SOL/USDT",
        market_type="futures",
        permission_mode="Observe",
        run_id="test-run-valid-until-003",
        linked_strategy_id=None,
        memory_block="",
        scanner_family="not_in_ttl_table",
        force_full=True,
        _prefetched=_make_prefetched(),
    )
    after = datetime.now(timezone.utc)

    assert err is None, f"unexpected error: {err}"
    assert opp_id is not None, "no opportunity was created"

    row = get_db().execute(
        "SELECT valid_until FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    assert row is not None, "opportunity not found in DB"
    assert row["valid_until"] is not None, "valid_until must always be set"

    valid_until_dt = datetime.fromisoformat(row["valid_until"])
    if valid_until_dt.tzinfo is None:
        valid_until_dt = valid_until_dt.replace(tzinfo=timezone.utc)

    # default TTL = 3600s; allow ±2s for test execution time
    assert before + timedelta(seconds=3598) <= valid_until_dt <= after + timedelta(seconds=3602), (
        f"valid_until {valid_until_dt.isoformat()} not in expected range for default 3600s TTL"
    )


async def test_valid_until_is_a_parseable_iso_timestamp(tmp_path, monkeypatch):
    """valid_until stored on every emitted Opportunity must be a parseable ISO-8601 timestamp."""
    from datetime import datetime

    _prepare_db(tmp_path, monkeypatch)
    _patch_mocks(monkeypatch)

    from aletheia.agents.market_observer import _analyze_symbol
    from aletheia.storage.sqlite import get_db

    opp_id, err = await _analyze_symbol(
        symbol="AVAX/USDT",
        market_type="futures",
        permission_mode="Observe",
        run_id="test-run-valid-until-004",
        linked_strategy_id=None,
        memory_block="",
        scanner_family="core",
        force_full=True,
        _prefetched=_make_prefetched(),
    )

    assert opp_id is not None, f"no opportunity created — error: {err}"
    row = get_db().execute(
        "SELECT valid_until FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    assert row is not None
    # Must not raise
    datetime.fromisoformat(row["valid_until"])
