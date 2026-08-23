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


def _insert_fresh_snapshot(conn, symbol: str, price: float = 50000.0, age_seconds: float = 0.0,
                           market_type: str = "spot"):
    """Insert a market_snapshots row with a controlled timestamp and market_type."""
    snap_id = str(uuid.uuid4())
    # age_seconds > 0 means the snapshot is that many seconds old
    snap_ts = datetime.fromtimestamp(
        time.time() - age_seconds, tz=timezone.utc
    ).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots (id, symbol, market_type, price, volume_24h, "
        "change_pct_24h, snapshot_at, provider) VALUES (?,?,?,?,?,?,?,?)",
        (snap_id, symbol, market_type, price, 1_000_000.0, 0.5, snap_ts, "ccxt"),
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
    cfg.risk.max_live_position_margin_usdt = 1_000_000.0
    cfg.risk.max_total_live_exposure_pct = 100.0
    cfg.risk.max_daily_loss_pct = 99.0
    cfg.risk.live.capital.base_source = "custom"
    cfg.risk.live.capital.base_usdt = 10_000.0
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
# PL-MD-05  L2b  xstock_infra_event: one event per consecutive-failure window
# MD-1 drift fixed: write_audit called once at the 3rd consecutive failure
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-05", "pipeline_market_data", "§4 xstock_infra_event", "L2b",
        "When xStock fetch reaches 3 consecutive failures, exactly one infra_event "
        "must mark that threshold transition; later failures in the same window do "
        "not amplify durable writes",
        status="implemented",
    ),
)
def test_xstock_consecutive_failures_emit_one_threshold_event(tmp_path, monkeypatch):
    """Five failures emit once at three while indeterminate listing preserves intent.

    Exercises the actual collect_market_snapshots code path with a monkeypatched
    CCXTProvider that always raises, so the failure counter in market_data.py
    keeps accumulating without turning every later failure into another durable write.
    """
    import asyncio
    import aletheia.trading.market_data as md_mod
    import aletheia.config.loader as loader_mod
    import aletheia.data.ccxt_provider as ccxt_mod
    import aletheia.data.venue_listing_cache as venue_cache_mod

    conn = _setup_db(tmp_path, monkeypatch)

    # Reset module-level failure counter so prior test runs don't bleed in
    md_mod._xstock_failure_counts.clear()

    # Build a minimal config with one xstock symbol
    from aletheia.config.schema import AletheiaConfig, MarketDataConfig
    cfg = AletheiaConfig()
    cfg.market_data = MarketDataConfig(
        observed_symbols=[],
        observed_stock_tokens=[],
        observed_xstocks=["AAPLON/USDT"],
        ccxt_exchange="binance",
    )
    monkeypatch.setattr(md_mod, "load_config", lambda: cfg)

    saved_configs: list = []
    monkeypatch.setattr(loader_mod, "save_config", lambda value: saved_configs.append(value))

    # Patch _build_dynamic_instruments to return empty (no regular instruments)
    monkeypatch.setattr(md_mod, "_build_dynamic_instruments", lambda cfg: [])

    # Fake CCXTProvider that always raises on get_ticker
    class _FailingCCXTProvider:
        async def get_ticker(self, instrument):
            raise RuntimeError("xstock_api_unavailable")

        async def close(self):
            pass

    monkeypatch.setattr(ccxt_mod, "CCXTProvider", lambda **kwargs: _FailingCCXTProvider())
    # get_market_data_provider also needs to return something that can be closed
    # The factory receives exact-venue kwargs even though this fake only drives
    # the separately pinned xStock failure path.
    monkeypatch.setattr(
        ccxt_mod,
        "get_market_data_provider",
        lambda **kwargs: _FailingCCXTProvider(),
    )

    async def _indeterminate_listing(exchange_name):
        assert exchange_name == "bitget"
        return None

    monkeypatch.setattr(venue_cache_mod, "get_venue_listing", _indeterminate_listing)
    monkeypatch.setattr(md_mod, "_listed_symbols_on_exchange", _indeterminate_listing)

    # Runs 3-5 remain one continuous degradation window.
    for _ in range(5):
        asyncio.run(md_mod.collect_market_snapshots())

    audit_rows = conn.execute(
        "SELECT * FROM audit_events "
        "WHERE source='market_data' AND event_type='infra_event' "
        "AND entity_id='AAPLON/USDT'",
    ).fetchall()

    assert len(audit_rows) == 1
    assert md_mod._xstock_failure_counts["AAPLON/USDT"] == 5
    assert saved_configs == []
    assert cfg.market_data.observed_stock_tokens == []
    assert cfg.market_data.observed_xstocks == ["AAPLON/USDT"]


# ---------------------------------------------------------------------------
# PL-MD-08  L2b  xstock_auto_remove: delist error removes symbol from config
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-08", "pipeline_market_data", "§4 xstock_auto_remove", "L2b",
        "When xStock fetch fails and live market-list check confirms symbol absent "
        "on the responsible exchange, remove it from current and legacy config fields, "
        "audit the mutation, and reuse one venue listing per collection cycle",
        status="implemented",
    ),
)
def test_xstock_delist_confirmed_by_exchange_removes_immediately(tmp_path, monkeypatch):
    """Confirmed absence clears both config fields and records the system mutation.

    The check uses the exchange responsible for this specific token (instrument.exchange),
    not a substitute venue. Two failures on that venue still load its listing only once.
    """
    import asyncio
    import aletheia.trading.market_data as md_mod
    import aletheia.config.loader as loader_mod
    import aletheia.data.ccxt_provider as ccxt_mod

    _setup_db(tmp_path, monkeypatch)

    md_mod._xstock_failure_counts.clear()

    from aletheia.config.schema import AletheiaConfig, MarketDataConfig
    cfg = AletheiaConfig()
    cfg.market_data = MarketDataConfig(
        observed_symbols=[],
        observed_stock_tokens=["AAPLON/USDT", "TSLAON/USDT"],
        observed_xstocks=["AAPLON/USDT", "TSLAON/USDT"],
        ccxt_exchange="binance",
    )

    monkeypatch.setattr(md_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(loader_mod, "load_config", lambda: cfg)

    saved_configs: list = []
    monkeypatch.setattr(loader_mod, "save_config", lambda c: saved_configs.append(c))

    import aletheia.files.jsonl as jsonl_mod
    audit_events: list[dict] = []
    monkeypatch.setattr(jsonl_mod, "write_audit", lambda event: audit_events.append(event))

    try:
        import aletheia.infra.events as events_mod
        monkeypatch.setattr(events_mod, "emit_infra_event", lambda **kwargs: None)
    except (ImportError, AttributeError):
        pass

    monkeypatch.setattr(md_mod, "_build_dynamic_instruments", lambda cfg: [])

    listing_calls: list[str] = []

    async def _no_listed_symbols(exchange_name):
        listing_calls.append(exchange_name)
        assert exchange_name == "bitget", (
            f"Must query the responsible exchange (bitget), got '{exchange_name}'"
        )
        return frozenset()

    monkeypatch.setattr(md_mod, "_listed_symbols_on_exchange", _no_listed_symbols)

    class _FailingProvider:
        async def get_ticker(self, instrument):
            raise RuntimeError("fetch failed")

        async def close(self):
            pass

    monkeypatch.setattr(ccxt_mod, "CCXTProvider", lambda **kwargs: _FailingProvider())
    monkeypatch.setattr(
        ccxt_mod,
        "get_market_data_provider",
        lambda **kwargs: _FailingProvider(),
    )

    asyncio.run(md_mod.collect_market_snapshots())

    assert saved_configs, (
        "save_config was not called after confirmed-absent listing verification"
    )
    from aletheia.data.symbols import to_canonical
    removed = {"AAPLON/USDT", "TSLAON/USDT"}
    for field in ("observed_stock_tokens", "observed_xstocks"):
        tokens = list(getattr(saved_configs[-1].market_data, field) or [])
        assert not removed.intersection({to_canonical(t) for t in tokens}), (
            f"confirmed-absent tokens remain in {field}: {tokens}"
        )
    assert listing_calls == ["bitget"]
    assert {event["entity_id"] for event in audit_events} == removed
    assert all(event["actor"] == "system" for event in audit_events)
    assert all(event["source"] == "market_data" for event in audit_events)
    assert all(event["before"] != event["after"] for event in audit_events)


@spec_claim(
    SpecClaim(
        "PL-MD-08b", "pipeline_market_data", "§4 xstock_auto_remove_safe_default", "L2b",
        "When the exchange listing check is indeterminate, the symbol must NOT be removed",
        status="implemented",
    ),
)
def test_xstock_listing_check_failure_does_not_remove(tmp_path, monkeypatch):
    """An indeterminate listing check preserves configured intent.

    A transient network error must never trigger auto-removal.
    """
    import asyncio
    import aletheia.trading.market_data as md_mod
    import aletheia.config.loader as loader_mod
    import aletheia.data.ccxt_provider as ccxt_mod

    _setup_db(tmp_path, monkeypatch)

    md_mod._xstock_failure_counts.clear()

    from aletheia.config.schema import AletheiaConfig, MarketDataConfig
    cfg = AletheiaConfig()
    cfg.market_data = MarketDataConfig(
        observed_symbols=[],
        observed_stock_tokens=["AAPLON/USDT"],
        ccxt_exchange="binance",
    )

    monkeypatch.setattr(md_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(loader_mod, "load_config", lambda: cfg)

    saved_configs: list = []
    monkeypatch.setattr(loader_mod, "save_config", lambda c: saved_configs.append(c))

    monkeypatch.setattr(md_mod, "_build_dynamic_instruments", lambda cfg: [])

    async def _indeterminate(exchange_name):
        return None

    monkeypatch.setattr(md_mod, "_listed_symbols_on_exchange", _indeterminate)

    class _FailingProvider:
        async def get_ticker(self, instrument):
            raise RuntimeError("connection timeout")

        async def close(self):
            pass

    monkeypatch.setattr(ccxt_mod, "CCXTProvider", lambda **kwargs: _FailingProvider())
    monkeypatch.setattr(
        ccxt_mod,
        "get_market_data_provider",
        lambda **kwargs: _FailingProvider(),
    )

    asyncio.run(md_mod.collect_market_snapshots())

    assert not saved_configs, (
        "save_config was called despite an indeterminate listing check. "
        "A transient outage must never trigger config auto-removal."
    )


@spec_claim(
    SpecClaim(
        "PL-MD-08c", "pipeline_market_data", "§4 xstock_listing_deadline", "L2b",
        "A cancellation-resistant exchange listing call returns indeterminate at the "
        "hard deadline and still closes its provider eventually",
        status="implemented",
    ),
)
async def test_xstock_listing_check_has_hard_deadline_and_eventual_cleanup(monkeypatch):
    import asyncio
    import time

    import aletheia.data.ccxt_provider as ccxt_mod
    import aletheia.trading.market_data as md_mod

    cancellation_seen = asyncio.Event()
    cleanup_seen = asyncio.Event()
    factory_calls: list[dict] = []

    class _CancellationResistantExchange:
        def __init__(self):
            self.options = {}

        async def load_markets(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_seen.set()
                await asyncio.sleep(0.15)
                return {"AAPLON/USDT": {"spot": True, "active": True}}

    class _Provider:
        def __init__(self):
            self.exchange = _CancellationResistantExchange()

        def _ensure_primary(self):
            return self.exchange

        async def close(self):
            cleanup_seen.set()

    def _provider_factory(**kwargs):
        factory_calls.append(kwargs)
        return _Provider()

    monkeypatch.setattr(ccxt_mod, "get_market_data_provider", _provider_factory)
    monkeypatch.setattr(md_mod, "_XSTOCK_LISTING_TIMEOUT_SEC", 0.01)

    started = time.monotonic()
    listed = await md_mod._listed_symbols_on_exchange("bitget")
    elapsed = time.monotonic() - started

    assert listed is None
    assert elapsed < 0.1, f"listing deadline drained cancellation for {elapsed:.3f}s"
    assert factory_calls == [{
        "exchange": "bitget",
        "native_ccxt": True,
        "fallback_exchange": "bitget",
    }]
    await asyncio.wait_for(cancellation_seen.wait(), timeout=0.1)
    await asyncio.wait_for(cleanup_seen.wait(), timeout=1.0)


# ---------------------------------------------------------------------------
# PL-MD-06  L2b  typed_freshness: futures freshness prefers the perp snapshot
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-06", "pipeline_market_data", "§3 typed_freshness", "L2b",
        "_market_data_age_seconds(symbol, market_type='usdt_futures') reads the "
        "perp snapshot, not a same-symbol spot snapshot — so a futures decision's "
        "freshness is not computed off a spot row (#13)",
    ),
)
def test_market_data_freshness_prefers_typed_snapshot(tmp_path, monkeypatch):
    """A stale spot snapshot must NOT satisfy a fresh futures freshness check."""
    conn = _setup_db(tmp_path, monkeypatch)

    # Old spot row + fresh perp row for the SAME symbol.
    _insert_fresh_snapshot(conn, "BTC/USDT", price=60000.0, age_seconds=300.0, market_type="spot")
    _insert_fresh_snapshot(conn, "BTC/USDT", price=60050.0, age_seconds=3.0, market_type="perp")

    from aletheia.risk.engine import _market_data_age_seconds

    # usdt_futures proposal → should resolve the ~3s perp row, not the 300s spot row.
    age = _market_data_age_seconds("BTC/USDT", market_type="usdt_futures")
    assert age is not None, "typed freshness returned None despite a fresh perp snapshot"
    assert age < 120.0, (
        f"Expected the ~3s perp snapshot, got age={age:.1f}s — futures freshness "
        "is reading the stale spot row (#13 regression)"
    )

    # Symbol-only (legacy) call still returns the most recent row regardless of type.
    legacy = _market_data_age_seconds("BTC/USDT")
    assert legacy is not None and legacy < 120.0


# ---------------------------------------------------------------------------
# PL-MD-07  L2b  typed_fallback: typed query with no typed row falls back
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-MD-07", "pipeline_market_data", "§3 typed_fallback", "L2b",
        "_market_data_age_seconds(symbol, market_type='usdt_futures') falls back "
        "to the symbol-only snapshot when no typed row exists, so old spot-only "
        "DBs do not fail freshness closed (age=None) for futures proposals (#13)",
    ),
)
def test_market_data_freshness_falls_back_to_symbol_only(tmp_path, monkeypatch):
    """Only a spot snapshot exists → a futures freshness query still finds it."""
    conn = _setup_db(tmp_path, monkeypatch)

    _insert_fresh_snapshot(conn, "BTC/USDT", price=60000.0, age_seconds=5.0, market_type="spot")

    from aletheia.risk.engine import _market_data_age_seconds

    age = _market_data_age_seconds("BTC/USDT", market_type="usdt_futures")
    assert age is not None, (
        "typed futures query returned None despite a fresh spot snapshot — the "
        "symbol-only fallback is missing, which would fail freshness closed (#13)"
    )
    assert age < 120.0
