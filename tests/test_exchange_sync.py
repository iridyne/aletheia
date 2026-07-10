"""Tests for exchange account sync — closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap.

Governance under test:
- READ-ONLY: never writes external positions into the local positions table.
- HONEST DEGRADATION: missing creds → unconfigured; API error → error + last cache
  remains; cache too old → stale. No fake positive states.
- CACHED: HTTP path reads cache only; never blocks on Bitget.
"""

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


class _FakeBalance:
    def __init__(self, currency, available, frozen):
        self.currency = currency; self.available = available; self.frozen = frozen


class _FakePosition:
    def __init__(self, symbol="ETHUSDT", quantity=1.0, unrealized_pnl=12.5, side="long"):
        self.symbol = symbol; self.side = side; self.quantity = quantity
        self.entry_price = 3000.0; self.unrealized_pnl = unrealized_pnl; self.leverage = 2
        self.mark_price = 3010.0


def test_store_retries_transient_sqlite_busy(monkeypatch):
    from aletheia.portfolio import exchange_sync as es

    attempts = []

    class _Conn:
        def __init__(self, attempt_num):
            self.attempt_num = attempt_num
            self.closed = False

        def execute(self, sql, *args, **kwargs):
            attempts.append(self.attempt_num)
            if self.attempt_num == 0:
                raise sqlite3.OperationalError("database is locked")

        def close(self):
            self.closed = True

    call_count = [0]

    def fake_connect(*args, **kwargs):
        conn = _Conn(call_count[0])
        call_count[0] += 1
        return conn

    monkeypatch.setattr(es.sqlite3, "connect", fake_connect)
    monkeypatch.setattr(es.time, "sleep", lambda _sec: None)

    es._store(es.ExchangeAccountState(
        mode="demo",
        equity_usdt=1.0,
        available_usdt=1.0,
        frozen_usdt=0.0,
        futures_unrealized_pnl=0.0,
        open_positions_count=0,
        open_orders_count=0,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        asset_breakdown=[],
        positions=[],
    ))

    assert len(attempts) >= 2
    assert attempts[0] == 0  # first attempt failed
    assert attempts[1] == 1  # retried with new connection


def _patch_client(monkeypatch, *, balances=None, positions=None, orders=None,
                  raises_on_balances=False, mix_equity=None):
    """Mock BitgetClient so tests don't hit the network.

    mix_equity: if given, get_mix_account returns {usdtEquity: mix_equity};
                if None (default), returns {} so spot balances determine equity.
    """
    from aletheia.portfolio import exchange_sync as es

    _mix_equity = mix_equity

    class _FakeClient:
        def __init__(self, mode="demo"):
            self.mode = mode
        def _get_creds(self):
            return ("k", "s", "p")
        async def get_balances(self):
            if raises_on_balances:
                raise RuntimeError("Bitget API error: timeout")
            return balances or [_FakeBalance("USDT", 10000.0, 0.0)]
        async def get_mix_account(self, product_type="USDT-FUTURES"):
            if _mix_equity is not None:
                return {"usdtEquity": str(_mix_equity), "available": str(_mix_equity), "locked": "0", "unrealizedPL": "0"}
            return {}
        async def get_positions(self):
            return positions if positions is not None else []
        async def get_open_orders(self, market_type="spot"):
            return orders or []

    # Patch the import path used inside _client_or_unconfigured.
    import aletheia.exchanges.bitget.client as bg_client
    monkeypatch.setattr(bg_client, "BitgetClient", _FakeClient)
    return es


# --- happy path: real values flow through ---

def test_sync_writes_cache_and_status_ok(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch,
                       balances=[_FakeBalance("USDT", 9500.0, 500.0)],
                       positions=[_FakePosition(unrealized_pnl=42.0)],
                       orders=[{"id": "o1"}])
    state = es.sync_exchange_account()
    assert state is not None
    # equity = spot holdings (9500 available + 500 frozen); futures unrealized PnL is
    # a separate field. When mix/account returns {} (no demo futures data) the spot
    # sum is the authoritative total.
    assert state.equity_usdt == 9500.0 + 500.0
    assert state.futures_unrealized_pnl == 42.0
    cached = es.get_cached_state()
    assert cached["equity_usdt"] == state.equity_usdt
    assert es.status_for(cached) == "ok"


# --- degradation: no credentials ---

def test_sync_unconfigured_returns_none_no_raise(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import exchange_sync as es
    # Force _client_or_unconfigured to return (None, mode) by patching BitgetClient.
    import aletheia.exchanges.bitget.client as bg_client

    class _NoCreds:
        def __init__(self, mode="demo"): self.mode = mode
        def _get_creds(self): raise RuntimeError("not configured")
    monkeypatch.setattr(bg_client, "BitgetClient", _NoCreds)

    assert es.sync_exchange_account() is None
    assert es.status_for(es.get_cached_state()) == "unconfigured"


# --- degradation: API failure preserves last cache ---

def test_sync_failure_preserves_previous_cache(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch)
    first = es.sync_exchange_account()
    assert first is not None

    # Now make the next call fail; the previous cache must remain.
    _patch_client(monkeypatch, raises_on_balances=True)
    second = es.sync_exchange_account()
    assert second is None  # this call failed
    cached = es.get_cached_state()
    assert cached is not None
    assert cached["equity_usdt"] == first.equity_usdt  # still the old good value


# --- staleness ---

def test_status_stale_when_cache_too_old(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch)
    es.sync_exchange_account()
    cached = es.get_cached_state()
    # Manually backdate the entry to be older than the freshness window.
    cached["age_seconds"] = 99999
    assert es.status_for(cached) == "stale"


# --- portfolio integration: new fields exposed ---

def test_compute_portfolio_carries_exchange_fields(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch,
                       balances=[_FakeBalance("USDT", 10000.0, 0.0)])
    es.sync_exchange_account()

    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["exchange_equity_usdt"] == 10000.0
    assert out["exchange_sync_status"] == "ok"
    assert out["exchange_state_age_sec"] is not None
    # total_value_usdt is the authoritative account equity when the exchange
    # view is live — not the locally-managed notional. With no local positions
    # the notional is 0, so this proves the override (not just a fallthrough).
    assert out["total_value_usdt"] == 10000.0


def test_compute_portfolio_unconfigured_keeps_local_view(tmp_path, monkeypatch):
    """No exchange creds shouldn't break the locally-managed portfolio view."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["exchange_sync_status"] == "unconfigured"
    assert out["exchange_equity_usdt"] is None
    # Local fields still present and honest.
    assert out["total_value_usdt"] == 0
    assert out["positions_count"] == 0


def test_compute_portfolio_ignores_exchange_cache_for_local_paper(tmp_path, monkeypatch):
    """A stale external cache must not become local-paper account value."""
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch, balances=[_FakeBalance("USDT", 10000.0, 0.0)])
    es.sync_exchange_account()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["exchange_sync_status"] == "not_applicable"
    assert out["exchange_equity_usdt"] is None
    assert out["total_value_usdt"] == 0.0


# --- drift detection ---

def test_external_position_count_surfaces_drift(tmp_path, monkeypatch):
    """If the exchange shows positions we didn't open via aletheia, expose the delta."""
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch,
                       balances=[_FakeBalance("USDT", 10000.0, 0.0)],
                       positions=[_FakePosition(), _FakePosition(symbol="SOLUSDT")])
    es.sync_exchange_account()

    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["exchange_external_positions"] == 2  # 0 local, 2 on exchange


# --- API endpoint ---

@pytest.mark.anyio
async def test_exchange_account_endpoint(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch)
    es.sync_exchange_account()

    from httpx import AsyncClient, ASGITransport
    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/exchange/account")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["state"]["equity_usdt"] == 10000.0


@pytest.mark.anyio
async def test_positions_reconciliation_endpoint(tmp_path, monkeypatch):
    """GET /api/positions/reconciliation classifies external positions the system
    did not open."""
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch,
                       positions=[_FakePosition(symbol="BTCUSDT", side="short")])
    es.sync_exchange_account()

    from httpx import AsyncClient, ASGITransport
    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/positions/reconciliation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_status"] == "ok"
    assert body["counts"]["external"] == 1
    assert body["external"][0]["symbol"] == "BTC/USDT"
    assert body["external"][0]["direction"] == "short"


# --- trust §1.1 (AS AMENDED 2026-06-12): sync stays read-only; adoption carries an
# honest synthetic chain. ---

def test_sync_alone_never_writes_local_positions(tmp_path, monkeypatch):
    """exchange_sync itself is purely read-only — pulling the exchange view must NOT
    write anything into the local positions table. Only an explicit adopt_position
    call does that (see the adoption test below)."""
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch,
                       positions=[_FakePosition(), _FakePosition(symbol="ETHUSDT")])
    es.sync_exchange_account()

    # Local positions table must remain untouched by the sync.
    assert repo.list_positions() == []


def test_adopted_positions_carry_synthetic_chain(tmp_path, monkeypatch):
    """trust §1.1 amendment: an external position MAY enter the local table, but only
    via adopt_position, which must build a complete `source="adopted"` causal chain
    (proposal → order → position) with non-null back-links — keeping the chain honest
    instead of forbidding entry. See docs/DECISIONS.md (2026-06-12)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.position_reconciler import adopt_position

    ex = {"symbol": "BTCUSDT", "side": "short", "quantity": 0.0004,
          "entry_price": 61524.3, "unrealized_pnl": -0.87, "mark_price": 63713.9}
    result = adopt_position(ex, "demo")

    pos = repo._select_one("positions", result["position_id"])
    order = repo._select_one("orders", result["order_id"])
    prop = repo._select_one("trade_proposals", result["proposal_id"])

    # The whole chain is tagged adopted, and the back-links are non-null + consistent.
    assert pos["source"] == "adopted"
    assert order["source"] == "adopted"
    assert prop["source"] == "adopted"
    assert pos["proposal_id"] == prop["id"]
    assert pos["order_id"] == order["id"]
    # Honest mapping: demo account → bitget_demo (so position_manager auto-manages it).
    assert pos["execution_mode"] == "bitget_demo"
    # Entry/qty come from the exchange truth, not fabricated.
    assert pos["entry_price"] == 61524.3
    assert pos["quantity"] == 0.0004
    assert order["status"] == "filled"


def test_adopt_live_position_is_human_only_by_execution_mode(tmp_path, monkeypatch):
    """An adopted LIVE position must get execution_mode=bitget_live_limited, which
    position_manager skips — so live stays human-only by construction."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.portfolio import position_manager as pm

    ex = {"symbol": "ETHUSDT", "side": "long", "quantity": 0.5,
          "entry_price": 3000.0, "unrealized_pnl": 5.0, "mark_price": 3010.0}
    result = adopt_position(ex, "live")
    pos = repo._select_one("positions", result["position_id"])
    assert pos["execution_mode"] == "bitget_live_limited"
    assert pos["execution_mode"] not in pm._PAPER_MODES  # position_manager will skip it


def test_adopt_refuses_zero_entry_or_quantity(tmp_path, monkeypatch):
    """Never synthesize a chain off a missing size/entry — that would fabricate the
    very causal facts the design exists to keep honest."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.position_reconciler import adopt_position
    import pytest as _pytest
    with _pytest.raises(ValueError):
        adopt_position({"symbol": "BTCUSDT", "side": "short", "quantity": 0.001,
                        "entry_price": 0.0}, "demo")
    with _pytest.raises(ValueError):
        adopt_position({"symbol": "BTCUSDT", "side": "short", "quantity": 0.0,
                        "entry_price": 60000.0}, "demo")
