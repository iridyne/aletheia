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

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position, PortfolioSnapshot
from aletheia.storage import repositories as repo
from aletheia.portfolio.pnl import compute_portfolio, compute_and_store_snapshot

VERIFIED_OUTCOME = {
    "close_category": "strategy",
    "exit_price_source": "local_fill",
    "exit_fill_verified": True,
    "outcome_eligible": True,
    "outcome_exclusion_reason": "eligible",
    "outcome_semantics_version": "oi-v1",
}


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


# ---------- P1-2: compute_portfolio realized_pnl respects execution_mode ----------

class TestRealizedPnlIsolation:
    """realized_pnl in compute_portfolio must be filtered by execution_mode."""

    def test_realized_pnl_filters_by_mode(self, db):
        """When execution_mode is specified, only that mode's closed positions count."""
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=100.0, realized_pnl=10.0,
            status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=50.0, realized_pnl=25.0,
            status="closed", execution_mode="okx_demo", **VERIFIED_OUTCOME,
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
        summary = compute_portfolio(execution_mode="paper_internal")
        assert summary["realized_pnl"] == 10.0
        assert summary["all_realized_pnl"] == 35.0

    def test_realized_pnl_no_mode_returns_all(self, db):
        """When no execution_mode is specified, all closed positions count."""
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=100.0, realized_pnl=10.0,
            status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=50.0, realized_pnl=25.0,
            status="closed", execution_mode="okx_demo", **VERIFIED_OUTCOME,
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
        summary = compute_portfolio(execution_mode=None)
        assert summary["realized_pnl"] == 35.0
        assert summary["all_realized_pnl"] == 35.0


# ---------- P1-3: compute_and_store_snapshot consistency ----------

class TestSnapshotConsistency:
    """Snapshot rows must use pre-computed values, not re-fetch."""

    def test_paper_row_uses_mode_realized(self, db):
        """Row 1 (paper) uses mode-filtered realized_pnl, not all-mode."""
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=100.0, realized_pnl=10.0,
            status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=50.0, realized_pnl=25.0,
            status="closed", execution_mode="okx_demo", **VERIFIED_OUTCOME,
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
        snap = compute_and_store_snapshot(execution_mode="paper_internal")
        assert snap.realized_pnl == 10.0

    def test_exchange_row_uses_all_realized(self, tmp_path, monkeypatch, db):
        """Row 2 (exchange_account) uses all_realized_pnl from summary."""
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=100.0, realized_pnl=10.0,
            status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=50.0, realized_pnl=25.0,
            status="closed", execution_mode="okx_demo", **VERIFIED_OUTCOME,
            closed_at=datetime.now(timezone.utc).isoformat(),
        ))
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


# ---------- P1-1 + P1-4: /equity-curve endpoint filters ----------

@pytest.mark.anyio
class TestEquityCurveEndpoint:
    """equity-curve endpoint must support execution_mode, days, limit."""

    async def test_returns_all_closed_by_default(self, client):
        c, _ = client
        now = datetime.now(timezone.utc)
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=100.0, realized_pnl=5.0,
            status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
            closed_at=now.isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=50.0, realized_pnl=3.0,
            status="closed", execution_mode="okx_demo", **VERIFIED_OUTCOME,
            closed_at=(now + timedelta(minutes=1)).isoformat(),
        ))
        resp = await c.get("/api/analytics/equity-curve")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["points"]) == 2
        assert body["total_realized"] == 8.0

    async def test_filters_by_execution_mode(self, client):
        c, _ = client
        now = datetime.now(timezone.utc)
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=100.0, realized_pnl=5.0,
            status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
            closed_at=now.isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=50.0, realized_pnl=3.0,
            status="closed", execution_mode="okx_demo", **VERIFIED_OUTCOME,
            closed_at=(now + timedelta(minutes=1)).isoformat(),
        ))
        resp = await c.get("/api/analytics/equity-curve?execution_mode=paper_internal")
        body = resp.json()
        assert len(body["points"]) == 1
        assert body["total_realized"] == 5.0

    async def test_filters_by_days(self, client):
        c, _ = client
        now = datetime.now(timezone.utc)
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=100.0, realized_pnl=5.0,
            status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
            closed_at=(now - timedelta(days=10)).isoformat(),
        ))
        repo.insert_position(Position(
            symbol="ETH/USDT", market_type="spot", direction="long",
            quantity=1.0, entry_price=50.0, realized_pnl=3.0,
            status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
            closed_at=now.isoformat(),
        ))
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
            repo.insert_position(Position(
                symbol="BTC/USDT", market_type="spot", direction="long",
                quantity=1.0, entry_price=100.0, realized_pnl=1.0,
                status="closed", execution_mode="paper_internal", **VERIFIED_OUTCOME,
                closed_at=(now + timedelta(minutes=i)).isoformat(),
            ))
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
