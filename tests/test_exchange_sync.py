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
    def __init__(self, symbol="ETHUSDT", quantity=1.0, unrealized_pnl=12.5):
        self.symbol = symbol; self.side = "long"; self.quantity = quantity
        self.entry_price = 3000.0; self.unrealized_pnl = unrealized_pnl; self.leverage = 2


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
    # Hero number for managed notional is still present (UI shows both).
    assert "total_value_usdt" in out


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


# --- red line: external positions never get written into the local positions table ---

def test_external_positions_not_written_to_local_table(tmp_path, monkeypatch):
    """trust §1.1: external positions have no proposal back-link, so they MUST stay
    out of the local positions table to keep the causal chain honest."""
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch,
                       positions=[_FakePosition(), _FakePosition(symbol="ETHUSDT")])
    es.sync_exchange_account()

    # Local positions table must remain untouched by the sync.
    assert repo.list_positions() == []
