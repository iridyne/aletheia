"""End-to-end tests for the PnL pipeline audit fixes.

Covers:
- P0-1: portfolio-history data source continuity (snapshots provide time coverage)
- P1-1: /equity-curve execution_mode filtering
- P1-2: compute_portfolio realized_pnl respects execution_mode
- P1-3: compute_and_store_snapshot no double-fetch / consistent snapshots
- P1-4: /equity-curve days + limit parameters
- P2-2: auto-deposit uses available_usdt not equity_usdt
"""

import json
from datetime import datetime, timezone, timedelta

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ISO
from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position, PortfolioSnapshot
from aletheia.storage import repositories as repo
from aletheia.portfolio.pnl import compute_portfolio, compute_and_store_snapshot
from tests.specs.spec_meta import SpecClaim, spec_claim


def _insert_verified_closed_position(
    *,
    position_id: str,
    symbol: str,
    entry_price: float,
    realized_pnl: float,
    execution_mode: str,
    closed_at: str,
    expected_outcome_eligible: bool = True,
) -> None:
    """Close a complete fixture through the same producer as runtime trades."""
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    _insert_closed_position_via_producer(
        position_id=position_id,
        symbol=symbol,
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=entry_price,
        realized_pnl=realized_pnl,
        execution_mode=execution_mode,
        closed_at=closed_at,
        expected_outcome_eligible=expected_outcome_eligible,
    )


def _setup(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    test_db = tmp_path / "test.db"
    conn = sqlite3.connect(str(test_db), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    # Patch db_path so worker threads (sync endpoints via run_in_threadpool)
    # create their own WAL-compatible connection to the same test DB file.
    monkeypatch.setattr(sq, "db_path", lambda: test_db)
    run_migrations()


@pytest.fixture
async def client(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, tmp_path
    reset_db()


@pytest.fixture
def db(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    yield tmp_path
    reset_db()


def test_exchange_upnl_is_scoped_to_its_broker_and_duplicate_is_rejected(db, monkeypatch):
    """Same-symbol exchange uPnL must never cross broker/account projections."""
    from aletheia.portfolio import pnl, exchange_sync

    for mode in ("bitget_demo", "okx_demo", "binance_testnet"):
        repo.insert_position(Position(
            id=f"upnl-{mode}", symbol="BTC/USDT", market_type="usdt_futures",
            direction="long", quantity=1.0, entry_price=100.0,
            execution_mode=mode,
        ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 0.0,
        "positions": [
            {"broker_id": "bitget_demo", "symbol": "BTC/USDT", "market_type": "perp", "side": "long", "unrealized_pnl": 7.0},
            {"broker_id": "okx_demo", "symbol": "BTC/USDT", "market_type": "usdt_futures", "side": "long", "unrealized_pnl": -3.0},
            # Ambiguous candidates must fail closed, not last-write-wins.
            {"broker_id": "binance_testnet", "symbol": "BTC/USDT", "market_type": "usdt_futures", "side": "long", "unrealized_pnl": 1.0},
            {"broker_id": "binance_testnet", "symbol": "BTC/USDT", "market_type": "usdt_futures", "side": "long", "unrealized_pnl": 99.0},
        ],
    })

    marked = {row["execution_mode"]: row for row in pnl.mark_positions_to_market()}

    assert marked["bitget_demo"]["unrealized_pnl"] == 7.0
    assert marked["okx_demo"]["unrealized_pnl"] == -3.0
    assert marked["binance_testnet"]["unrealized_pnl"] == 10.0


@pytest.mark.parametrize("prebuilt_index", [False, True])
def test_paper_internal_never_consumes_exchange_upnl(
    db, monkeypatch, prebuilt_index,
):
    """Local-paper PnL stays local even if a cache row claims its mode."""
    from aletheia.portfolio import exchange_sync, pnl

    repo.insert_position(Position(
        id="upnl-local-paper",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
    ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 0.0,
        "positions": [{
            "broker_id": "paper_internal",
            "symbol": "BTC/USDT",
            "market_type": "usdt_futures",
            "side": "long",
            "unrealized_pnl": 777.0,
        }],
    })
    if prebuilt_index:
        monkeypatch.setattr(pnl, "_build_exchange_upnl_index", lambda: {
            ("BTC/USDT", "paper_internal", "perp", "long"): 777.0,
        })

    [marked] = pnl.mark_positions_to_market(persist=True)

    assert marked["unrealized_pnl"] == 10.0
    assert marked["unrealized_pnl_source"] == "local_estimate"
    stored = repo._select_one("positions", "upnl-local-paper")
    assert stored is not None
    assert stored["unrealized_pnl"] == 10.0
    assert stored["unrealized_pnl_source"] == "local_estimate"


@spec_claim(
    SpecClaim(
        "PL-PM-17",
        "pipeline_position_manager",
        "§1 exchange uPnL market identity",
        "L2c",
        "Exchange uPnL may be assigned only when the remote and local Position "
        "share a known normalized market type; missing, unknown, or mismatched "
        "market types must fall back to the local mark estimate.",
        status="implemented",
    )
)
@pytest.mark.parametrize("remote_market_type", [None, "unknown", "usdt_futures"])
def test_exchange_upnl_market_type_mismatch_falls_back_to_local_estimate(
    db, monkeypatch, remote_market_type,
):
    """Unproven market identity must not overwrite a local spot Position."""
    from aletheia.portfolio import exchange_sync, pnl

    repo.insert_position(Position(
        id="upnl-okx-spot",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="okx_demo",
    ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 0.0,
        "positions": [{
            "broker_id": "okx_demo",
            "symbol": "BTC/USDT",
            "market_type": remote_market_type,
            "side": "long",
            "unrealized_pnl": 777.0,
        }],
    })

    [marked] = pnl.mark_positions_to_market(persist=True)

    assert marked["unrealized_pnl"] == 10.0
    stored = repo._select_one("positions", "upnl-okx-spot")
    assert stored is not None
    assert stored["unrealized_pnl"] == 10.0


@spec_claim(
    SpecClaim(
        "PL-PM-16",
        "pipeline_position_manager",
        "§1 exchange uPnL cardinality",
        "L2c",
        "One aggregate venue uPnL may be assigned only when exactly one local "
        "open Position shares its current broker/symbol/direction match key; "
        "local multiplicity must not duplicate or persist the aggregate value.",
        status="implemented",
    )
)
def test_exchange_aggregate_upnl_is_not_reused_across_local_positions(
    db, monkeypatch,
):
    """One venue aggregate must not become each local Position's full uPnL."""
    from aletheia.portfolio import exchange_sync, pnl

    repo.insert_position(Position(
        id="upnl-bitget-leg-a",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="bitget_demo",
    ))
    repo.insert_position(Position(
        id="upnl-bitget-leg-b",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        entry_price=120.0,
        execution_mode="bitget_demo",
    ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 0.0,
        "positions": [{
            "broker_id": "bitget_demo",
            "symbol": "BTC/USDT",
            "market_type": "usdt_futures",
            "side": "long",
            "unrealized_pnl": 777.0,
        }],
    })

    marked = {
        row["id"]: row for row in pnl.mark_positions_to_market(persist=True)
    }

    assert marked["upnl-bitget-leg-a"]["unrealized_pnl"] == 10.0
    assert marked["upnl-bitget-leg-b"]["unrealized_pnl"] == -20.0
    assert marked["upnl-bitget-leg-a"]["unrealized_pnl_source"] == "ambiguous"
    assert marked["upnl-bitget-leg-b"]["unrealized_pnl_source"] == "ambiguous"
    stored_a = repo._select_one("positions", "upnl-bitget-leg-a")
    stored_b = repo._select_one("positions", "upnl-bitget-leg-b")
    assert stored_a is not None
    assert stored_b is not None
    assert stored_a["unrealized_pnl"] == 10.0
    assert stored_b["unrealized_pnl"] == -20.0


@spec_claim(
    SpecClaim(
        "PL-PM-19",
        "pipeline_position_manager",
        "§1 exchange uPnL account and Position identity",
        "L2c",
        "Exchange uPnL must match the local broker/account fingerprint and, when "
        "available, the broker Position identity; mismatches or ambiguous rows "
        "fall back without persisting the remote value and expose a source status.",
        status="implemented",
    )
)
def test_exchange_upnl_account_fingerprint_mismatch_falls_back_and_is_labeled(
    db, monkeypatch,
):
    """A cached value from another account cannot become authoritative PnL."""
    from aletheia.portfolio import exchange_sync, pnl

    repo.insert_position(Position(
        id="upnl-account-mismatch",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="okx_demo",
    ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 0.0,
        "positions": [{
            "broker_id": "okx_demo",
            "account_fingerprint": "sha256:other-account",
            "symbol": "BTC/USDT",
            "market_type": "usdt_futures",
            "side": "long",
            "unrealized_pnl": 777.0,
        }],
    })

    [marked] = pnl.mark_positions_to_market(persist=True)

    assert marked["unrealized_pnl"] == 10.0
    assert marked["unrealized_pnl_source"] == "unavailable"
    stored = repo._select_one("positions", "upnl-account-mismatch")
    assert stored is not None
    assert stored["unrealized_pnl"] == 10.0
    assert stored["unrealized_pnl_source"] == "unavailable"


def test_exchange_position_identity_disambiguates_same_instrument_rows(
    db, monkeypatch,
):
    """A broker Position ID permits independent attribution of same-key rows."""
    from aletheia.portfolio import exchange_sync, pnl
    from aletheia.portfolio.disappearance_confirmation import logical_account_fingerprint

    account_fingerprint = logical_account_fingerprint(
        broker_id="okx_demo",
        account_environment="demo",
        execution_mode="okx_demo",
    )
    for position_id, broker_position_id, entry_price in (
        ("upnl-position-a", "venue-a", 100.0),
        ("upnl-position-b", "venue-b", 120.0),
    ):
        repo.insert_position(Position(
            id=position_id,
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            entry_price=entry_price,
            execution_mode="okx_demo",
            reconciliation_identity=json.dumps({
                "account_fingerprint": account_fingerprint,
                "broker_position_id": broker_position_id,
            }),
        ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 0.0,
        "positions": [
            {
                "broker_id": "okx_demo",
                "account_fingerprint": account_fingerprint,
                "position_id": "venue-a",
                "symbol": "BTC/USDT",
                "market_type": "usdt_futures",
                "side": "long",
                "unrealized_pnl": 11.0,
            },
            {
                "broker_id": "okx_demo",
                "account_fingerprint": account_fingerprint,
                "position_id": "venue-b",
                "symbol": "BTC/USDT",
                "market_type": "usdt_futures",
                "side": "long",
                "unrealized_pnl": 22.0,
            },
        ],
    })

    marked = {
        row["id"]: row for row in pnl.mark_positions_to_market(persist=True)
    }

    assert marked["upnl-position-a"]["unrealized_pnl"] == 11.0
    assert marked["upnl-position-a"]["unrealized_pnl_source"] == "exchange_exact"
    assert marked["upnl-position-b"]["unrealized_pnl"] == 22.0
    assert marked["upnl-position-b"]["unrealized_pnl_source"] == "exchange_exact"


def test_duplicate_local_broker_position_identity_falls_back(
    db, monkeypatch,
):
    """Duplicate local rows cannot both adopt one exact venue uPnL value."""
    from aletheia.portfolio import exchange_sync, pnl
    from aletheia.portfolio.disappearance_confirmation import logical_account_fingerprint

    account_fingerprint = logical_account_fingerprint(
        broker_id="okx_demo",
        account_environment="demo",
        execution_mode="okx_demo",
    )
    for position_id, entry_price in (("upnl-duplicate-a", 100.0), ("upnl-duplicate-b", 120.0)):
        repo.insert_position(Position(
            id=position_id,
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            entry_price=entry_price,
            execution_mode="okx_demo",
            reconciliation_identity=json.dumps({
                "account_fingerprint": account_fingerprint,
                "broker_position_id": "venue-duplicate",
            }),
        ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 0.0,
        "positions": [{
            "broker_id": "okx_demo",
            "account_fingerprint": account_fingerprint,
            "position_id": "venue-duplicate",
            "symbol": "BTC/USDT",
            "market_type": "usdt_futures",
            "side": "long",
            "unrealized_pnl": 777.0,
        }],
    })

    marked = {
        row["id"]: row for row in pnl.mark_positions_to_market(persist=True)
    }

    assert marked["upnl-duplicate-a"]["unrealized_pnl"] == 10.0
    assert marked["upnl-duplicate-a"]["unrealized_pnl_source"] == "ambiguous"
    assert marked["upnl-duplicate-b"]["unrealized_pnl"] == -10.0
    assert marked["upnl-duplicate-b"]["unrealized_pnl_source"] == "ambiguous"


def test_exchange_exact_short_does_not_inherit_long_upnl(
    db, monkeypatch,
):
    """Direction remains part of the exchange uPnL identity."""
    from aletheia.portfolio import exchange_sync, pnl

    repo.insert_position(Position(
        id="upnl-short-exact",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="okx_demo",
    ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 90.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 0.0,
        "positions": [
            {
                "broker_id": "okx_demo",
                "symbol": "BTC/USDT",
                "market_type": "usdt_futures",
                "side": "long",
                "unrealized_pnl": -777.0,
            },
            {
                "broker_id": "okx_demo",
                "symbol": "BTC/USDT",
                "market_type": "usdt_futures",
                "side": "short",
                "unrealized_pnl": 7.0,
            },
        ],
    })

    [marked] = pnl.mark_positions_to_market(persist=True)

    assert marked["unrealized_pnl"] == 7.0
    assert marked["unrealized_pnl_source"] == "exchange_exact"


@pytest.mark.asyncio
async def test_positions_api_exposes_unrealized_pnl_source(client):
    """The owner-facing Position projection preserves mark provenance."""
    http_client, _tmp_path = client
    repo.insert_position(Position(
        id="upnl-api-source",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="okx_demo",
        unrealized_pnl_source="ambiguous",
    ))

    response = await http_client.get("/api/positions")

    assert response.status_code == 200
    [row] = [item for item in response.json() if item["id"] == "upnl-api-source"]
    assert row["unrealized_pnl_source"] == "ambiguous"


@spec_claim(
    SpecClaim(
        "PL-PM-18",
        "pipeline_position_manager",
        "§1 exchange uPnL cache health",
        "L2c",
        "Exchange uPnL may be assigned and persisted only from a fresh unified "
        "cache; stale or unclassified cache data must fall back to the local "
        "mark estimate.",
        status="implemented",
    )
)
@pytest.mark.parametrize("age_seconds", [None, 601.0])
def test_stale_exchange_upnl_is_not_applied_or_persisted(
    db, monkeypatch, age_seconds,
):
    """A preserved old venue value must not masquerade as the current mark."""
    from aletheia.portfolio import exchange_sync, pnl

    repo.insert_position(Position(
        id="upnl-stale-okx",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="okx_demo",
    ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": age_seconds,
        "positions": [{
            "broker_id": "okx_demo",
            "symbol": "BTC/USDT",
            "market_type": "usdt_futures",
            "side": "long",
            "unrealized_pnl": 777.0,
        }],
    })

    [marked] = pnl.mark_positions_to_market(persist=True)

    assert marked["unrealized_pnl"] == 10.0
    stored = repo._select_one("positions", "upnl-stale-okx")
    assert stored is not None
    assert stored["unrealized_pnl"] == 10.0


# ---------- P1-2: compute_portfolio realized_pnl respects execution_mode ----------

class TestRealizedPnlIsolation:
    """realized_pnl in compute_portfolio must be filtered by execution_mode."""

    def test_realized_pnl_filters_by_mode(self, db):
        """When execution_mode is specified, only that mode's closed positions count."""
        now = datetime.now(timezone.utc)
        _insert_verified_closed_position(
            position_id="mode-filter-paper", symbol="BTC/USDT",
            entry_price=100.0, realized_pnl=10.0,
            execution_mode="paper_internal",
            closed_at=(now - timedelta(seconds=2)).isoformat(),
        )
        _insert_verified_closed_position(
            position_id="mode-filter-demo", symbol="ETH/USDT",
            entry_price=50.0, realized_pnl=25.0,
            execution_mode="okx_demo",
            closed_at=(now - timedelta(seconds=1)).isoformat(),
        )
        summary = compute_portfolio(execution_mode="paper_internal")
        assert summary["realized_pnl"] == 10.0
        assert summary["all_realized_pnl"] == 35.0

    def test_realized_pnl_no_mode_returns_all(self, db):
        """When no execution_mode is specified, all closed positions count."""
        now = datetime.now(timezone.utc)
        _insert_verified_closed_position(
            position_id="all-mode-paper", symbol="BTC/USDT",
            entry_price=100.0, realized_pnl=10.0,
            execution_mode="paper_internal",
            closed_at=(now - timedelta(seconds=2)).isoformat(),
        )
        _insert_verified_closed_position(
            position_id="all-mode-demo", symbol="ETH/USDT",
            entry_price=50.0, realized_pnl=25.0,
            execution_mode="okx_demo",
            closed_at=(now - timedelta(seconds=1)).isoformat(),
        )
        summary = compute_portfolio(execution_mode=None)
        assert summary["realized_pnl"] == 35.0
        assert summary["all_realized_pnl"] == 35.0


@pytest.mark.anyio
async def test_equity_curve_exposes_live_mark_separately_from_closed_curves(client, monkeypatch):
    """Open-position movement must be timestamped but never called realized PnL."""
    c, _ = client
    now = datetime.now(timezone.utc)
    _insert_verified_closed_position(
        position_id="live-mark-closed", symbol="BTC/USDT",
        entry_price=100.0, realized_pnl=5.0,
        execution_mode="paper_internal", closed_at=(now - timedelta(minutes=2)).isoformat(),
    )
    repo.insert_position(Position(
        id="live-mark-open", symbol="ETH/USDT", market_type="spot",
        direction="long", quantity=1.0, entry_price=10.0, current_price=13.0,
        unrealized_pnl=3.0, execution_mode="paper_internal", status="open",
    ))
    # A stale/unrelated unified-exchange cache must never overwrite the local
    # paper mark. That was the live-curve freeze observed in the running UI.
    import aletheia.config.loader as loader
    from aletheia.portfolio import exchange_sync
    config = loader.load_config()
    config.exchange.paper_execution_mode = "paper_internal"
    monkeypatch.setattr(loader, "load_config", lambda: config)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: {
        "age_seconds": 1, "total_unrealized_pnl": 999.0,
    })

    body = (await c.get("/api/analytics/equity-curve")).json()
    mark = body["current_mark_point"]
    assert mark["time"]
    assert mark["closed_raw_pnl"] == 5.0
    assert mark["current_unrealized"] == 3.0
    assert mark["cumulative_pnl"] == 8.0
    assert body["total_realized"] == 5.0


# ---------- P1-3: compute_and_store_snapshot consistency ----------

class TestSnapshotConsistency:
    """Snapshot rows must use pre-computed values, not re-fetch."""

    def test_paper_row_uses_mode_realized(self, db):
        """Row 1 (paper) uses mode-filtered realized_pnl, not all-mode."""
        now = datetime.now(timezone.utc)
        _insert_verified_closed_position(
            position_id="snapshot-paper", symbol="BTC/USDT",
            entry_price=100.0, realized_pnl=10.0,
            execution_mode="paper_internal",
            closed_at=(now - timedelta(seconds=2)).isoformat(),
        )
        _insert_verified_closed_position(
            position_id="snapshot-demo", symbol="ETH/USDT",
            entry_price=50.0, realized_pnl=25.0,
            execution_mode="okx_demo",
            closed_at=(now - timedelta(seconds=1)).isoformat(),
        )
        snap = compute_and_store_snapshot(execution_mode="paper_internal")
        assert snap.realized_pnl == 10.0

    def test_exchange_row_uses_all_realized(self, tmp_path, monkeypatch, db):
        """Row 2 (exchange_account) uses all_realized_pnl from summary."""
        now = datetime.now(timezone.utc)
        _insert_verified_closed_position(
            position_id="exchange-row-paper", symbol="BTC/USDT",
            entry_price=100.0, realized_pnl=10.0,
            execution_mode="paper_internal",
            closed_at=(now - timedelta(seconds=2)).isoformat(),
        )
        _insert_verified_closed_position(
            position_id="exchange-row-demo", symbol="ETH/USDT",
            entry_price=50.0, realized_pnl=25.0,
            execution_mode="okx_demo",
            closed_at=(now - timedelta(seconds=1)).isoformat(),
        )
        # compute_portfolio() only enters the exchange-account path when
        # paper_execution_mode != "paper_internal" (uses_external_exchange=True).
        # The new config default is "paper_internal", so we must override it
        # to "okx_demo" to make get_cached_state() be called and Row 2 written.
        import aletheia.config.loader as loader
        config = loader.load_config()
        config.exchange.paper_execution_mode = "okx_demo"
        monkeypatch.setattr(loader, "load_config", lambda: config)
        # Inject fake exchange cache so Row 2 is written
        import aletheia.portfolio.exchange_sync as es
        fake_state = {
            "equity_usdt": 5000.0,
            "available_usdt": 4000.0,
            "frozen_usdt": 1000.0,
            "futures_unrealized_pnl": 0.0,
            "open_positions_count": 0,
            "open_orders_count": 0,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "asset_breakdown": [],
            "positions": [],
            "age_seconds": 10,
        }
        monkeypatch.setattr(es, "get_cached_state", lambda mode=None: fake_state)
        compute_and_store_snapshot(execution_mode="paper_internal")
        snapshots = repo.list_portfolio_snapshots(limit=10)
        exc_rows = [s for s in snapshots if s["execution_mode"] == "exchange_account"]
        assert len(exc_rows) == 1
        assert exc_rows[0]["realized_pnl"] == 35.0

    def test_local_paper_snapshot_uses_ledger_equity_not_position_notional(self, db, monkeypatch):
        """A 400-USDT paper account with a 10-USDT mark has 410 equity, not 110 notional."""
        import aletheia.config.loader as loader
        from aletheia.portfolio.paper_account import set_cash_balance

        config = loader.load_config()
        config.exchange.paper_execution_mode = "paper_internal"
        monkeypatch.setattr(loader, "load_config", lambda: config)
        set_cash_balance(400.0, note="test account baseline")
        repo.insert_position(Position(
            id="account-equity-open", symbol="BTC/USDT", market_type="spot",
            direction="long", quantity=1.0, entry_price=100.0, current_price=110.0,
            unrealized_pnl=10.0, execution_mode="paper_internal", status="open",
        ))
        snap = compute_and_store_snapshot(execution_mode="paper_internal")

        assert snap.total_value_usdt == 410.0
        assert snap.account_equity_usdt == 410.0
        row = repo.list_account_equity_snapshots("paper_internal")[0]
        assert row["account_equity_usdt"] == 410.0


# ---------- P1-1 + P1-4: /equity-curve endpoint filters ----------

@pytest.mark.anyio
class TestEquityCurveEndpoint:
    """equity-curve endpoint must support execution_mode, days, limit."""

    async def test_returns_all_closed_by_default(self, client):
        c, _ = client
        now = datetime.now(timezone.utc)
        _insert_verified_closed_position(
            position_id="curve-default-paper", symbol="BTC/USDT",
            entry_price=100.0, realized_pnl=5.0,
            execution_mode="paper_internal",
            closed_at=(now - timedelta(minutes=2)).isoformat(),
        )
        _insert_verified_closed_position(
            position_id="curve-default-demo", symbol="ETH/USDT",
            entry_price=50.0, realized_pnl=3.0,
            execution_mode="okx_demo",
            closed_at=(now - timedelta(minutes=1)).isoformat(),
        )
        resp = await c.get("/api/analytics/equity-curve")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["points"]) == 2
        assert body["total_realized"] == 8.0

    async def test_filters_by_execution_mode(self, client):
        c, _ = client
        now = datetime.now(timezone.utc)
        _insert_verified_closed_position(
            position_id="curve-filter-paper", symbol="BTC/USDT",
            entry_price=100.0, realized_pnl=5.0,
            execution_mode="paper_internal",
            closed_at=(now - timedelta(minutes=2)).isoformat(),
        )
        _insert_verified_closed_position(
            position_id="curve-filter-demo", symbol="ETH/USDT",
            entry_price=50.0, realized_pnl=3.0,
            execution_mode="okx_demo",
            closed_at=(now - timedelta(minutes=1)).isoformat(),
        )
        resp = await c.get("/api/analytics/equity-curve?execution_mode=paper_internal")
        body = resp.json()
        assert len(body["points"]) == 1
        assert body["total_realized"] == 5.0

    async def test_filters_by_days(self, client):
        c, _ = client
        now = datetime.now(timezone.utc)
        pre_clean_window = (
            datetime.fromisoformat(CLEAN_WINDOW_ANCHOR_ISO) - timedelta(minutes=1)
        )
        _insert_verified_closed_position(
            position_id="curve-days-old", symbol="BTC/USDT",
            entry_price=100.0, realized_pnl=5.0,
            execution_mode="paper_internal",
            closed_at=pre_clean_window.isoformat(),
            expected_outcome_eligible=False,
        )
        _insert_verified_closed_position(
            position_id="curve-days-current", symbol="ETH/USDT",
            entry_price=50.0, realized_pnl=3.0,
            execution_mode="paper_internal",
            closed_at=(now - timedelta(minutes=1)).isoformat(),
        )
        resp = await c.get("/api/analytics/equity-curve?days=7")
        body = resp.json()
        # `days` windows the DISPLAYED points (only the in-window trade is emitted)…
        assert len(body["points"]) == 1
        assert body["points"][0]["pnl"] == 3.0
        # The verified strategy curve never carries pre-clean-window history forward.
        # Raw recorded account history remains available on its separately labelled curve.
        assert body["points"][0]["cumulative_pnl"] == 3.0
        assert body["total_realized"] == 3.0
        assert body["raw_recorded_points"][0]["cumulative_pnl"] == 8.0

    async def test_respects_limit(self, client):
        c, _ = client
        now = datetime.now(timezone.utc)
        for i in range(10):
            _insert_verified_closed_position(
                position_id=f"curve-limit-{i}", symbol="BTC/USDT",
                entry_price=100.0, realized_pnl=1.0,
                execution_mode="paper_internal",
                closed_at=(now - timedelta(minutes=10 - i)).isoformat(),
            )
        resp = await c.get("/api/analytics/equity-curve?limit=5")
        body = resp.json()
        assert len(body["points"]) == 5

    async def test_unrealized_respects_mode_filter(self, client):
        c, _ = client
        now = datetime.now(timezone.utc)
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=100.0, unrealized_pnl=7.0,
            status="open", execution_mode="paper_internal",
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=50.0, unrealized_pnl=2.0,
            status="open", execution_mode="okx_demo",
        ))
        resp = await c.get("/api/analytics/equity-curve?execution_mode=paper_internal")
        body = resp.json()
        assert body["current_unrealized"] == 7.0


# ---------- P0-1: portfolio-history provides continuous time coverage ----------

@pytest.mark.anyio
class TestPortfolioHistoryCoverage:
    """portfolio-history endpoint returns snapshots with continuous time coverage."""

    async def test_snapshots_cover_full_timespan(self, client):
        """Snapshot data covers system runtime, not just trade close timestamps."""
        c, _ = client
        now = datetime.now(timezone.utc)
        base = now - timedelta(hours=23)
        for i in range(24):
            repo.insert_portfolio_snapshot(PortfolioSnapshot(
                total_value_usdt=10000 + i * 10,
                unrealized_pnl=float(i),
                realized_pnl=0.0,
                execution_mode="exchange_account",
                exchange_equity_usdt=10000 + i * 10,
                snapshot_at=(base + timedelta(hours=i)).isoformat(),
            ))
        resp = await c.get("/api/market/portfolio-history?days=7")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 24
        assert rows[0]["snapshot_at"] < rows[-1]["snapshot_at"]

    async def test_snapshots_days_filter(self, client):
        """days param correctly filters at backend level."""
        c, _ = client
        now = datetime.now(timezone.utc)
        repo.insert_portfolio_snapshot(PortfolioSnapshot(
            total_value_usdt=9000, snapshot_at=(now - timedelta(days=15)).isoformat(),
        ))
        repo.insert_portfolio_snapshot(PortfolioSnapshot(
            total_value_usdt=10000, snapshot_at=now.isoformat(),
        ))
        resp = await c.get("/api/market/portfolio-history?days=7")
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["total_value_usdt"] == 10000

    async def test_account_equity_history_excludes_legacy_position_notional_rows(self, client, monkeypatch):
        """Only explicit account-equity snapshots can enter the Overview curve."""
        import aletheia.config.loader as loader
        c, _ = client
        config = loader.load_config()
        config.exchange.paper_execution_mode = "paper_internal"
        monkeypatch.setattr(loader, "load_config", lambda: config)
        now = datetime.now(timezone.utc)
        repo.insert_portfolio_snapshot(PortfolioSnapshot(
            total_value_usdt=9999.0, execution_mode="paper_internal",
            snapshot_at=(now - timedelta(minutes=2)).isoformat(),
        ))
        repo.insert_portfolio_snapshot(PortfolioSnapshot(
            total_value_usdt=400.0, account_equity_usdt=400.0,
            execution_mode="paper_internal", snapshot_at=now.isoformat(),
        ))

        body = (await c.get("/api/market/account-equity-history?days=7")).json()

        assert body["source"] == "local_paper_ledger"
        assert body["state"] == "available"
        assert body["points"] == [{"time": now.isoformat(), "equity_usdt": 400.0}]


# ---------- P2-2: auto-deposit uses available_usdt ----------

class TestAutoDeposit:
    """auto-record deposit should use available_usdt, not equity_usdt."""

    def test_uses_available_not_equity(self, db, monkeypatch):
        from aletheia.portfolio.exchange_sync import sync_all_exchanges
        from aletheia.portfolio.uta import manager as uta_mgr
        from dataclasses import dataclass

        @dataclass
        class FakeUnified:
            total_equity_usdt: float = 10500.0
            total_available_usdt: float = 10000.0
            total_unrealized_pnl: float = 500.0
            accounts: list = None
            positions: list = None
            errors: list = None
            fetched_at: str = ""

            def __post_init__(self):
                if self.accounts is None:
                    self.accounts = [{
                        "broker_id": "test_broker",
                        "display_name": "Test",
                        "equity_usdt": 10500.0,
                        "available_usdt": 10000.0,
                        "unrealized_pnl": 500.0,
                    }]
                if self.positions is None:
                    self.positions = []
                if self.errors is None:
                    self.errors = []
                if not self.fetched_at:
                    self.fetched_at = datetime.now(timezone.utc).isoformat()

        monkeypatch.setattr(uta_mgr, "get_unified_state_sync", lambda: FakeUnified())
        # Skip the legacy bitget sync
        import aletheia.portfolio.exchange_sync as es
        monkeypatch.setattr(es, "sync_exchange_account", lambda: None)

        sync_all_exchanges()

        events = repo.list_capital_events()
        assert len(events) == 1
        assert events[0]["amount_usdt"] == 10000.0
        assert events[0]["equity_at_event"] == 10500.0
        assert events[0]["broker_id"] == "test_broker"

    def test_falls_back_to_equity_when_available_zero(self, db, monkeypatch):
        from aletheia.portfolio.exchange_sync import sync_all_exchanges
        from aletheia.portfolio.uta import manager as uta_mgr
        from dataclasses import dataclass

        @dataclass
        class FakeUnified:
            total_equity_usdt: float = 5000.0
            total_available_usdt: float = 0.0
            total_unrealized_pnl: float = 0.0
            accounts: list = None
            positions: list = None
            errors: list = None
            fetched_at: str = ""

            def __post_init__(self):
                if self.accounts is None:
                    self.accounts = [{
                        "broker_id": "fallback_broker",
                        "display_name": "Test",
                        "equity_usdt": 5000.0,
                        "available_usdt": 0.0,
                        "unrealized_pnl": 0.0,
                    }]
                if self.positions is None:
                    self.positions = []
                if self.errors is None:
                    self.errors = []
                if not self.fetched_at:
                    self.fetched_at = datetime.now(timezone.utc).isoformat()

        monkeypatch.setattr(uta_mgr, "get_unified_state_sync", lambda: FakeUnified())
        import aletheia.portfolio.exchange_sync as es
        monkeypatch.setattr(es, "sync_exchange_account", lambda: None)

        sync_all_exchanges()

        events = repo.list_capital_events()
        assert len(events) == 1
        assert events[0]["amount_usdt"] == 5000.0

    def test_no_duplicate_deposit_on_repeat_sync(self, db, monkeypatch):
        from aletheia.portfolio.exchange_sync import sync_all_exchanges
        from aletheia.portfolio.uta import manager as uta_mgr
        from dataclasses import dataclass

        @dataclass
        class FakeUnified:
            total_equity_usdt: float = 10000.0
            total_available_usdt: float = 10000.0
            total_unrealized_pnl: float = 0.0
            accounts: list = None
            positions: list = None
            errors: list = None
            fetched_at: str = ""

            def __post_init__(self):
                if self.accounts is None:
                    self.accounts = [{
                        "broker_id": "repeat_broker",
                        "display_name": "Test",
                        "equity_usdt": 10000.0,
                        "available_usdt": 10000.0,
                        "unrealized_pnl": 0.0,
                    }]
                if self.positions is None:
                    self.positions = []
                if self.errors is None:
                    self.errors = []
                if not self.fetched_at:
                    self.fetched_at = datetime.now(timezone.utc).isoformat()

        monkeypatch.setattr(uta_mgr, "get_unified_state_sync", lambda: FakeUnified())
        import aletheia.portfolio.exchange_sync as es
        monkeypatch.setattr(es, "sync_exchange_account", lambda: None)

        sync_all_exchanges()
        sync_all_exchanges()

        events = repo.list_capital_events()
        assert len(events) == 1
