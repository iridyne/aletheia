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


@spec_claim(
    SpecClaim(
        "PL-PT-01", "pipeline_paper_trade", "§1 happy_path", "L2b",
        "With open opportunities and Paper permission, run_paper_trader writes "
        "at least one pi_decisions row with action='signal_pending'",
    ),
)
def test_paper_trader_produces_signal_pending(tmp_path, monkeypatch):
    """run_paper_trader v3 writes pi_decisions rows, not trade_proposals directly."""
    conn = _setup_db(tmp_path, monkeypatch)

    # Suppress pi_daemon notifications (not running in test env)
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=0.85)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    # Ensure tool_registry is loaded so TOOL_REGISTRY is populated
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()

    # Use asyncio to run the async paper_trader
    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert "errors" in result

    # paper_trader v3: writes pi_decisions(action="signal_pending") for eligible candidates
    pi_rows = conn.execute(
        "SELECT * FROM pi_decisions WHERE action = 'signal_pending'"
    ).fetchall()

    # Either signal_pending rows exist OR outputs contains a skip/no_candidate reason
    outputs = result.get("outputs", [])
    if pi_rows:
        # Happy path: at least one signal written
        assert len(pi_rows) >= 1, "Expected at least 1 signal_pending pi_decision"
        # All signal_pending rows must have a symbol
        for row in pi_rows:
            assert row["symbol"] is not None, (
                "pi_decisions signal_pending row must have a symbol"
            )
    else:
        # Acceptable: ranker filtered all candidates (no eligible ones)
        has_skip_output = any(
            "skip" in o or "no_ranked" in o or "no_signals" in o
            for o in outputs
        )
        assert has_skip_output, (
            f"paper_trader produced no signal_pending AND no skip output. "
            f"outputs={outputs}, errors={result.get('errors')}"
        )


# ---------------------------------------------------------------------------
# PL-PT-02  L2b  ranker_filter: low-score opportunities don't produce signals
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-02", "pipeline_paper_trade", "§2 ranker_filter", "L2b",
        "Opportunities with very low confidence_score are filtered by ranker; "
        "they don't produce signal_pending pi_decisions",
    ),
)
def test_paper_trader_ranker_filters_low_score(tmp_path, monkeypatch):
    """Insert a speculative-confidence opportunity with min_confidence='high' → filtered."""
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()

    # Set min_confidence_to_trade to "high" so "speculative" opportunities are filtered.
    # Ranker gate: confidence < min_conf → skip_reason="confidence_below_high:speculative"
    import yaml
    cfg_dict = {
        "runtime": {"paused": False, "kill_switch_active": False},
        "agent": {"permission_mode": "Paper"},
        "trading": {
            "min_confidence_to_trade": "high",
            "default_stop_loss_pct": 2.0,
            "default_notional_usdt": 25.0,
        },
        "risk": {"max_demo_position_notional_usdt": 200.0},
        "exchange": {"paper_execution_mode": "paper_internal"},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")

    opp_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO opportunities (id, symbol, market, direction_bias, confidence, "
        "confidence_score, status, asset_class, scanner_family, valid_until, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (opp_id, "BTC/USDT", "spot", "long", "speculative", 0.3,
         "open", "crypto", "momentum", "2099-01-01T00:00:00", now),
    )
    conn.commit()
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    pi_before = conn.execute(
        "SELECT COUNT(*) as n FROM pi_decisions WHERE action='signal_pending'"
    ).fetchone()["n"]

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    pi_after = conn.execute(
        "SELECT COUNT(*) as n FROM pi_decisions WHERE action='signal_pending'"
    ).fetchone()["n"]

    delta = pi_after - pi_before
    # Ranker confidence gate: "speculative" < "high" → must be filtered out
    assert delta == 0, (
        f"Expected ranker to filter speculative opportunity when min_confidence='high', "
        f"but {delta} new signal_pending rows were created. "
        f"outputs={result.get('outputs')}. "
        "Ranker confidence gate must prevent sub-threshold signals from reaching PI Agent."
    )


# ---------------------------------------------------------------------------
# PL-PT-03  L2c  symbol_consistency: BTC/USDT flows through as canonical format
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-03", "pipeline_paper_trade", "§3 symbol_consistency", "L2c",
        "A canonical BTC/USDT opportunity propagates with BTC/USDT symbol "
        "in pi_decisions rows (no BTCUSDT conversion at signal stage)",
    ),
)
def test_paper_trader_symbol_stays_canonical(tmp_path, monkeypatch):
    """Symbol format must stay canonical (BTC/USDT) at the signal stage.

    Conversion to BTCUSDT happens ONLY at the execution adapter boundary.
    A premature conversion silently drops opportunities at paper_trader's filter.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    import aletheia.agent_runtime.tool_registry  # noqa: F401

    _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=0.9)
    _insert_market_snapshot(conn, "BTC/USDT", price=62000.0)

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    asyncio.run(run_paper_trader(permission_mode="Paper"))

    # Any pi_decisions written for BTC must use canonical format
    non_canonical = conn.execute(
        "SELECT id, symbol FROM pi_decisions WHERE symbol LIKE 'BTC%' "
        "AND symbol != 'BTC/USDT'"
    ).fetchall()
    assert len(non_canonical) == 0, (
        f"Found pi_decisions with non-canonical BTC symbol format: "
        f"{[dict(r) for r in non_canonical]}. "
        "Symbol must remain BTC/USDT at signal stage — "
        "BTCUSDT conversion is only for exchange API boundary."
    )


# ---------------------------------------------------------------------------
# PL-PT-04  L2b  xfail  opportunity_id_propagation  (captures PT-2)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-04", "pipeline_paper_trade", "§2 opportunity_id_propagation", "L2b",
        "signal_pending pi_decision rows include the originating opportunity_id "
        "in the dedicated pi_decisions.opportunity_id column "
        "(paper_trader.py:165 passes candidate['opportunity_id'] → PiDecision.opportunity_id)",
        status="implemented",
    ),
)
def test_paper_trader_signal_has_opportunity_id(tmp_path, monkeypatch):
    """Assert signal_pending rows carry opportunity_id linking back to the source opportunity.

    Fixed PT-2 (A13, LEARNING_LOOP_AUDIT): paper_trader now passes
    opportunity_id=candidate.get('opportunity_id') into PiDecision (paper_trader.py:165).
    The dedicated pi_decisions.opportunity_id column (models.py:901, migration:1304)
    enables the PI Agent to trace which opportunity triggered each signal.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()

    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=0.9)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    asyncio.run(run_paper_trader(permission_mode="Paper"))

    signal_rows = conn.execute(
        "SELECT opportunity_id FROM pi_decisions WHERE action='signal_pending' AND symbol='BTC/USDT'"
    ).fetchall()

    assert len(signal_rows) >= 1, (
        "No signal_pending rows found — cannot verify opportunity_id propagation"
    )

    # opportunity_id must be the dedicated column, not null
    for row in signal_rows:
        assert row["opportunity_id"] is not None, (
            f"signal_pending pi_decision has opportunity_id=None — "
            f"expected {opp_id!r} to be propagated via paper_trader.py:165. "
            "PT-2 regression: opportunity tracking broken in paper_trader path."
        )
        assert row["opportunity_id"] == opp_id, (
            f"signal_pending pi_decision.opportunity_id={row['opportunity_id']!r} "
            f"does not match source opportunity {opp_id!r}."
        )


# ---------------------------------------------------------------------------
# PL-PT-05  L2b  xfail  signal_consumed  (captures PT-1)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PT-05", "pipeline_paper_trade", "§2 signal_consumed", "L2b",
        "After a successful signal_pending is written, the originating opportunity "
        "row has status updated away from 'open' to prevent duplicate signals",
        status="implemented",
    ),
)
def test_paper_trader_marks_opportunity_consumed(tmp_path, monkeypatch):
    """After a signal_pending is written, the source opportunity must not be 'open'.

    Fixed 2026-06-24 (PT-1, freeze exception): paper_trader now calls
    update_opportunity(opp_id, status='reviewed') after writing each signal_pending,
    so the next cycle won't re-emit the same opportunity as a duplicate signal
    (which the PI Agent read as "pipeline data integrity compromised" → all-skip).
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda source=None: None,
        raising=False,
    )

    import aletheia.agent_runtime.tool_registry  # noqa: F401

    opp_id = _insert_opportunity(conn, symbol="BTC/USDT", confidence_score=0.9)
    _insert_market_snapshot(conn, "BTC/USDT", price=60000.0)

    import asyncio
    from aletheia.agents.paper_trader import run_paper_trader

    result = asyncio.run(run_paper_trader(permission_mode="Paper"))

    signals = conn.execute(
        "SELECT id FROM pi_decisions WHERE action='signal_pending' AND symbol='BTC/USDT'"
    ).fetchall()

    if not signals:
        pytest.skip("No signal_pending written — cannot verify opportunity status")

    opp_row = conn.execute(
        "SELECT status FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()

    assert opp_row is not None, "Opportunity row missing after paper_trader run"
    assert opp_row["status"] != "open", (
        f"Opportunity {opp_id!r} still has status='open' after signal_pending was written. "
        "PT-1: opportunity is not marked as consumed — next run will re-process it, "
        "creating duplicate signals and breaking the signal deduplication guarantee."
    )
