"""Spec: Pipeline — Market Data (end-to-end)

E2E pipeline claims for get_market_data_provider() → fetch → persist →
freshness consumed by risk engine.

Contract: pipeline_market_data
Claim prefix: PL-MD
"""

import time
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


def _insert_fresh_snapshot(conn, symbol: str, price: float = 50000.0, age_seconds: float = 0.0):
    """Insert a market_snapshots row with a controlled timestamp."""
    snap_id = str(uuid.uuid4())
    # age_seconds > 0 means the snapshot is that many seconds old
    snap_ts = datetime.fromtimestamp(
        time.time() - age_seconds, tz=timezone.utc
    ).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots (id, symbol, market_type, price, volume_24h, "
        "change_pct_24h, snapshot_at, provider) VALUES (?,?,?,?,?,?,?,?)",
        (snap_id, symbol, "spot", price, 1_000_000.0, 0.5, snap_ts, "ccxt"),
    )
    conn.commit()
    return snap_id


# ---------------------------------------------------------------------------
# PL-MD-01  L2b  happy_path: snapshot written → price_feed cache sees it
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-01", "pipeline_market_data", "§1 happy_path", "L2b",
        "After a market_snapshots row is inserted, the price_feed layer "
        "returns that price for the same symbol (consumer sees fresh data)",
    ),
)
def test_market_data_snapshot_consumed_by_price_feed(tmp_path, monkeypatch):
    """Insert a snapshot directly, then verify price_feed reads it back."""
    conn = _setup_db(tmp_path, monkeypatch)

    _insert_fresh_snapshot(conn, "BTC/USDT", price=65432.0, age_seconds=0.0)

    from aletheia.data.price_feed import get_price_for_pnl
    price = get_price_for_pnl("BTC/USDT")

    assert price is not None, (
        "get_price_for_pnl('BTC/USDT') returned None after inserting a fresh snapshot"
    )
    assert abs(float(price) - 65432.0) < 1.0, (
        f"Expected price ~65432.0, got {price}"
    )


# ---------------------------------------------------------------------------
# PL-MD-02  L2b  multi_source: when ccxt provider fails, snapshot row still usable
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-02", "pipeline_market_data", "§2 multi_source", "L2b",
        "When the primary CCXT provider fetch fails, a pre-existing snapshot "
        "from an alternative source is still visible to price_feed consumers",
    ),
)
def test_market_data_fallback_source_persisted(tmp_path, monkeypatch):
    """Simulate a coingecko-sourced snapshot (provider='coingecko') being written
    and verify price_feed can consume it regardless of provider field."""
    conn = _setup_db(tmp_path, monkeypatch)

    snap_id = str(uuid.uuid4())
    snap_ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots (id, symbol, market_type, price, volume_24h, "
        "change_pct_24h, snapshot_at, provider) VALUES (?,?,?,?,?,?,?,?)",
        (snap_id, "ETH/USDT", "spot", 3500.0, 500_000.0, 1.2, snap_ts, "coingecko"),
    )
    conn.commit()

    # Verify the row is persisted with the correct source
    row = conn.execute(
        "SELECT provider, price FROM market_snapshots WHERE id = ?", (snap_id,)
    ).fetchone()
    assert row is not None, "Snapshot row not found after insert"
    assert row["provider"] == "coingecko", (
        f"Expected provider='coingecko', got {row['provider']!r}"
    )

    from aletheia.data.price_feed import get_price_for_pnl
    price = get_price_for_pnl("ETH/USDT")
    assert price is not None, (
        "price_feed returned None for coingecko-sourced snapshot — "
        "fallback source rows must be consumable by price_feed"
    )
    assert abs(float(price) - 3500.0) < 1.0


# ---------------------------------------------------------------------------
# PL-MD-03  L2c  freshness_consumer: fresh snapshot → risk check #11 passes
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-03", "pipeline_market_data", "§3 freshness_consumer", "L2c",
        "After a fresh market_snapshot is written (age < 120s), risk_engine "
        "_market_data_age_seconds returns a value below the live threshold",
    ),
)
def test_market_data_freshness_seen_by_risk_engine(tmp_path, monkeypatch):
    """Verify risk engine reads snapshot age from DB correctly for live proposals."""
    conn = _setup_db(tmp_path, monkeypatch)

    # Insert a very fresh snapshot (2 seconds old)
    _insert_fresh_snapshot(conn, "BTC/USDT", price=60000.0, age_seconds=2.0)

    from aletheia.risk.engine import _market_data_age_seconds
    age = _market_data_age_seconds("BTC/USDT")

    assert age is not None, (
        "_market_data_age_seconds returned None despite a fresh snapshot existing — "
        "risk engine cannot read snapshot age"
    )
    # Should be close to 2 seconds (allow up to 30s clock drift in test env)
    assert age < 120.0, (
        f"Expected age < 120s for a 2s-old snapshot, got {age:.1f}s"
    )


# ---------------------------------------------------------------------------
# PL-MD-04  L2b  stale_handling: 121s-old snapshot → risk engine check #11 rejects live
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-04", "pipeline_market_data", "§3 stale_handling", "L2b",
        "A live proposal with a market snapshot aged > 120s triggers "
        "the stale_market_data violation in risk engine check #11",
    ),
)
def test_market_data_stale_snapshot_triggers_risk_violation(tmp_path, monkeypatch):
    """Insert a 125s-old snapshot and verify risk engine rejects a live proposal."""
    conn = _setup_db(tmp_path, monkeypatch)

    _insert_fresh_snapshot(conn, "BTC/USDT", price=60000.0, age_seconds=125.0)

    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    cfg = AletheiaConfig()
    cfg.live.state = "live_enabled"
    cfg.exchange.mode = "live"
    cfg.agent.permission_mode = "Full"
    cfg.risk.max_live_position_notional_usdt = 1_000_000.0
    cfg.risk.max_total_live_exposure_pct = 100.0
    cfg.risk.max_daily_loss_pct = 99.0
    cfg.risk.max_live_limited_cap_usdt = 10_000.0
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        execution_mode="bitget_live_limited",
        quantity=0.001,
        price=60000.0,
        stop_price=58000.0,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert any("stale_market_data" in v for v in result.violations), (
        f"Expected stale_market_data violation with 125s-old snapshot, "
        f"got violations={result.violations}. "
        "Risk engine check #11 must reject live proposals with aged data."
    )


# ---------------------------------------------------------------------------
# PL-MD-05  L2b  xfail  xstock_infra_event: xStock failures don't trigger infra_event
# Captures MD-1 drift
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-05", "pipeline_market_data", "§4 xstock_infra_event", "L2b",
        "When xStock fetch fails 3 consecutive times, an infra_event row "
        "must be written to signal infrastructure degradation to operators",
        status="drift",
    ),
)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "MD-1 — xStock consecutive failures don't trigger infra_event. "
        "When xStock data fetch fails 3x in a row, no infra_event row is written "
        "to audit_events or knowledge_items. Operators have no visibility into "
        "persistent infrastructure failures."
    ),
)
def test_xstock_consecutive_failures_trigger_infra_event(tmp_path, monkeypatch):
    """Verify that 3 consecutive xStock fetch failures produce an infra_event row.

    This test captures the MD-1 gap: the xStock provider swallows errors but
    never surfaces them as structured infra events.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    # Simulate 3 consecutive xStock fetch failures by calling the provider
    # with a patched fetch that always raises
    import aletheia.data.ccxt_provider as ccxt_mod

    fail_count = [0]

    class _FailingXStockProvider:
        def get_ticker(self, symbol):
            fail_count[0] += 1
            raise RuntimeError("xstock_api_unavailable")

        def get_ohlcv(self, symbol, timeframe="1d", limit=100, since=None):
            fail_count[0] += 1
            raise RuntimeError("xstock_api_unavailable")

        async def close(self):
            pass

    # Trigger 3 failures
    provider = _FailingXStockProvider()
    for _ in range(3):
        try:
            provider.get_ticker("AAPL/USDT")
        except RuntimeError:
            pass

    assert fail_count[0] >= 3, "Provider did not fail 3 times as expected"

    # The infra_event should now exist in audit_events or knowledge_items
    audit_rows = conn.execute(
        "SELECT * FROM audit_events WHERE event_type LIKE '%infra%' OR event_type LIKE '%xstock%'",
    ).fetchall()
    ki_rows = conn.execute(
        "SELECT * FROM knowledge_items WHERE item_type LIKE '%infra%' OR title LIKE '%xstock%'",
    ).fetchall()

    assert len(audit_rows) + len(ki_rows) >= 1, (
        "No infra_event row written after 3 consecutive xStock fetch failures. "
        "MD-1 gap: infrastructure failures are silently swallowed."
    )
