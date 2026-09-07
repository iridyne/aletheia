"""Tests for exchange account sync — closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap.

Governance under test:
- READ-ONLY: never writes external positions into the local positions table.
- HONEST DEGRADATION: missing creds → unconfigured; API error → error + last cache
  remains; cache too old → stale. No fake positive states.
- CACHED: HTTP path reads cache only; never blocks on Bitget.
"""

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

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


def _patch_client(monkeypatch, *, balances=None, positions=None, orders=None,
                  raises_on_balances=False, mix_equity=None):
    """Provide a UTA read result without network or credentials."""
    from aletheia.portfolio import exchange_sync as es
    from aletheia.uta import manager

    def query(**kwargs):
        if raises_on_balances:
            raise RuntimeError("account read unavailable")
        equity = sum(b.available + b.frozen for b in balances) if balances else 10000.0
        rows = [{"broker_id": "bitget_demo", "market_type": "usdt_futures", **vars(p)} for p in positions or []]
        return manager.UnifiedState(
            total_equity_usdt=equity, total_available_usdt=equity,
            total_unrealized_pnl=sum(p.unrealized_pnl for p in positions or []),
            accounts=[{"broker_id": "bitget_demo", "equity_usdt": equity, "available_usdt": equity}],
            positions=rows, connected_brokers=["bitget_demo"],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            execution_modes=manager.DEFAULT_UNIFIED_EXECUTION_MODES,
        )

    monkeypatch.setattr(manager, "get_unified_state_sync", query)
    monkeypatch.setattr(es, "_unified_identity_is_current", lambda state: (True, "current"))
    return es


# --- happy path: real values flow through ---

def test_sync_writes_cache_and_status_ok(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch,
                       balances=[_FakeBalance("USDT", 9500.0, 500.0)],
                       positions=[_FakePosition(unrealized_pnl=42.0)],
                       orders=[{"id": "o1"}])
    state = es.sync_all_exchanges()
    assert state is not None
    assert state["total_equity_usdt"] == 10000.0
    assert state["total_unrealized_pnl"] == 42.0
    cached = es.get_unified_cached_state()
    assert cached["total_equity_usdt"] == 10000.0
    assert es.status_for(cached) == "ok"


# --- degradation: no credentials ---


# --- degradation: API failure preserves last cache ---

def test_sync_failure_preserves_previous_cache(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch)
    first = es.sync_all_exchanges()
    assert first is not None

    # Now make the next call fail; the previous cache must remain.
    _patch_client(monkeypatch, raises_on_balances=True)
    second = es.sync_all_exchanges()
    assert second is None  # this call failed
    cached = es.get_unified_cached_state()
    assert cached is not None
    assert cached["total_equity_usdt"] == first["total_equity_usdt"]


# --- staleness ---

def test_status_stale_when_cache_too_old(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch)
    es.sync_all_exchanges()
    cached = es.get_unified_cached_state()
    # Manually backdate the entry to be older than the freshness window.
    cached["age_seconds"] = 99999
    assert es.status_for(cached) == "stale"


def test_future_account_timestamp_is_stale(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import exchange_sync as es

    es._store_unified({"fetched_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()})
    cached = es.get_unified_cached_state()
    assert cached is not None
    assert cached["age_seconds"] < 0
    assert es.status_for(cached) == "stale"
    assert es.status_for({"age_seconds": float("nan")}) == "stale"
    assert es.status_for({"age_seconds": float("inf")}) == "stale"


@pytest.mark.asyncio
async def test_uta_state_uses_canonical_future_timestamp_status(monkeypatch):
    from aletheia.portfolio import exchange_sync as es
    from aletheia.server.routes import bitget

    monkeypatch.setattr(es, "get_unified_cached_state", lambda: {
        "total_equity_usdt": 100.0,
        "total_available_usdt": 100.0,
        "total_unrealized_pnl": 0.0,
        "accounts": [],
        "positions": [],
        "errors": [],
        "fetched_at": "future",
        "age_seconds": -5.0,
    })

    payload = await bitget.uta_state()

    assert payload["sync_status"] == "stale"



@pytest.mark.asyncio
async def test_uta_state_unconfigured_is_null_not_zero(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.server.routes import bitget

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "okx_demo"
    save_config(cfg)

    payload = await bitget.uta_state()

    assert payload["sync_status"] == "unconfigured"
    assert payload["total_equity_usdt"] is None
    assert payload["total_available_usdt"] is None


# --- portfolio integration: new fields exposed ---

def test_compute_portfolio_carries_exchange_fields(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # Schema defaults changed to paper_internal; this test exercises the bitget-demo
    # path where an external exchange cache drives compute_portfolio.
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.mode = "demo"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    es = _patch_client(monkeypatch,
                       balances=[_FakeBalance("USDT", 10000.0, 0.0)])
    es.sync_all_exchanges()

    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["exchange_equity_usdt"] == 10000.0
    assert out["exchange_sync_status"] == "ok"
    assert out["exchange_state_age_sec"] is not None
    # total_value_usdt is the authoritative account equity when the exchange
    # view is live — not the locally-managed notional. With no local positions
    # the notional is 0, so this proves the override (not just a fallthrough).
    assert out["total_value_usdt"] == 10000.0


def test_account_equity_projection_preserves_zero():
    from aletheia.portfolio import exchange_sync as es

    assert es.account_equity_value(
        {"equity_usdt": 0, "total_equity_usdt": 99.0}
    ) == 0.0
    assert es.account_equity_value({"total_equity_usdt": 0}) == 0.0
    assert es.account_available_value(
        {"available_usdt": 0, "total_available_usdt": 88.0}
    ) == 0.0
    assert es.account_equity_value({}) is None
    assert es.account_equity_value({"equity_usdt": float("nan")}) is None


def test_fresh_zero_exchange_equity_reaches_portfolio_and_context(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio import exchange_sync as es

    cfg = load_config()
    cfg.exchange.mode = "demo"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    es = _patch_client(monkeypatch, balances=[_FakeBalance("USDT", 0.0, 0.0)])
    es.sync_all_exchanges()

    from aletheia.portfolio.pnl import compute_portfolio

    portfolio = compute_portfolio()
    assert portfolio["exchange_equity_usdt"] == 0.0
    assert portfolio["account_equity_usdt"] == 0.0
    assert portfolio["total_value_usdt"] == 0.0



def test_inspect_status_preserves_zero_exchange_equity(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from typer.testing import CliRunner

    from aletheia.cli.inspect_cmd import inspect_app
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio import exchange_sync as es

    cfg = load_config()
    cfg.exchange.mode = "demo"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    es = _patch_client(monkeypatch, balances=[_FakeBalance("USDT", 0.0, 0.0)])
    es.sync_all_exchanges()

    monkeypatch.setattr("aletheia.portfolio.pnl.mark_positions_to_market", lambda persist=False: [])
    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.build_outcome_cohort_summary",
        lambda **_kwargs: {
            "eligible_realized_pnl": 0.0,
            "raw_event_count": 0,
            "eligible_outcome_count": 0,
            "excluded_outcome_count": 0,
        },
    )
    monkeypatch.setattr(
        "aletheia.analytics.trade_stats.compute_rolling_trade_stats",
        lambda **_kwargs: {
            "days": 7,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "total_pnl": None,
            "avg_pnl": None,
            "best_win": None,
            "worst_loss": None,
        },
    )
    monkeypatch.setattr(
        "aletheia.analytics.trade_stats.compute_order_stats",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "aletheia.maintenance.historical_accounting.historical_accounting_summary",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr("aletheia.data.price_feed.cache_stats", lambda: {})
    monkeypatch.setattr(repo, "get_active_universe_snapshot", lambda: None)
    monkeypatch.setattr(repo, "get_active_universe_members", lambda limit=5: [])

    result = CliRunner().invoke(inspect_app, ["status", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["portfolio"]["equity_usdt"] == 0.0


def test_unified_cache_carries_sync_metadata(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio import exchange_sync as es
    import aletheia.uta.manager as manager

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "okx_demo"
    save_config(cfg)
    monkeypatch.setattr(
        es, "_unified_identity_is_current", lambda _state: (True, "current")
    )
    monkeypatch.setattr(
        manager,
        "get_unified_state_sync",
        lambda **kwargs: SimpleNamespace(
            total_equity_usdt=123.45,
            total_available_usdt=120.0,
            total_unrealized_pnl=3.45,
            accounts=[{"broker_id": "okx_demo", "equity_usdt": 123.45, "available_usdt": 120.0}],
            positions=[],
            errors=[],
            connected_brokers=["okx_demo"],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            execution_modes=manager.DEFAULT_UNIFIED_EXECUTION_MODES,
        ),
    )

    payload = es.sync_all_exchanges()
    cached = es.get_unified_cached_state()

    assert payload is not None
    assert cached is not None
    assert cached["connected_brokers"] == ["okx_demo"]
    assert cached["execution_modes"] == [
        "binance_testnet", "okx_demo", "bitget_demo",
    ]
    assert cached["sync_status"] == "ok"
    assert cached["sync_complete"] is True

    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["uta_state"]["connected_brokers"] == ["okx_demo"]
    assert out["uta_state"]["sync_status"] == "ok"
    assert out["total_value_usdt"] == 123.45


def test_unified_cache_generation_mismatch_revokes_authority(monkeypatch):
    from aletheia.portfolio import exchange_sync as es

    identity = {
        "account_id": "okx_demo",
        "execution_mode": "okx_demo",
        "venue": "okx",
        "environment": "demo",
        "profile": "okx_demo",
        "credential_generation": 1,
        "sub_accounts": [],
        "adapter_implementation": "CCXTUTAAdapter",
        "adapter_version": "1",
        "account_fingerprint": None,
        "ccxt_version": "4.5.56",
    }

    class Registry:
        def configured_identities(self):
            from aletheia.uta.models import UTAAccountIdentity

            return (UTAAccountIdentity.model_validate(identity),)

    monkeypatch.setattr("aletheia.uta.manager.build_uta_registry", lambda _cfg: Registry())
    state = {
        "execution_modes": ["okx_demo"],
        "accounts": [
            {"broker_id": "okx_demo", "identity": {**identity, "credential_generation": 0}, "boundary": "legacy"}
        ]
    }

    current, reason = es._unified_identity_is_current(state)

    assert current is False
    assert reason == "uta_account_generation_mismatch"


def test_unified_cache_rejects_legacy_adapter_identity(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync as es
    from aletheia.uta.models import UTAAccountIdentity

    uta_identity = UTAAccountIdentity(
        account_id="okx_demo",
        execution_mode="okx_demo",
        venue="okx",
        environment="demo",
        profile="okx_demo",
        credential_generation=1,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version="4.5.56",
    )

    class Registry:
        def configured_identities(self):
            return (uta_identity,)

    cfg = AletheiaConfig()
    monkeypatch.setattr(es, "load_config", lambda: cfg)
    monkeypatch.setattr("aletheia.uta.manager.build_uta_registry", lambda _cfg: Registry())
    state = {
        "execution_modes": ["okx_demo"],
        "accounts": [
            {
                "broker_id": "okx_demo",
                "boundary": "legacy",
                "identity": uta_identity.model_copy(
                    update={
                        "adapter_implementation": "OkxDemoAdapter",
                        "adapter_version": "legacy",
                        "ccxt_version": None,
                    }
                ).model_dump(mode="json"),
            }
        ]
    }

    assert es._unified_identity_is_current(state) == (False, "uta_account_generation_mismatch")


def test_unified_cache_validates_only_persisted_account_scope(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync as es
    from aletheia.uta.models import UTAAccountIdentity

    demo = UTAAccountIdentity(
        account_id="okx_demo",
        execution_mode="okx_demo",
        venue="okx",
        environment="demo",
        profile="okx_demo",
        credential_generation=1,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version="4.5.56",
    )
    live = demo.model_copy(
        update={
            "account_id": "okx_live_limited",
            "execution_mode": "okx_live_limited",
            "environment": "live",
            "profile": "okx_live",
            "credential_generation": 9,
        }
    )

    class Registry:
        def configured_identities(self):
            return (demo, live)

    cfg = AletheiaConfig()
    monkeypatch.setattr(es, "load_config", lambda: cfg)
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry", lambda _cfg: Registry()
    )

    paper_state = {
        "execution_modes": ["okx_demo"],
        "accounts": [{
            "broker_id": "okx_demo",
            "boundary": "uta_v1",
            "identity": demo.model_dump(mode="json"),
        }],
    }
    live_state = {
        "execution_modes": ["okx_live_limited"],
        "accounts": [{
            "broker_id": "okx_live_limited",
            "boundary": "uta_v1",
            "identity": live.model_dump(mode="json"),
        }],
    }

    assert es._unified_identity_is_current(paper_state) == (True, "current")
    assert es._unified_identity_is_current(live_state) == (True, "current")
    assert es._unified_identity_is_current({"accounts": []}) == (
        False, "uta_scope_missing"
    )


def test_unified_cache_preserves_explicit_empty_connected_set(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import exchange_sync as es

    monkeypatch.setattr(
        es, "_unified_identity_is_current", lambda _state: (True, "current")
    )
    es._store_unified(
        {
            "accounts": [{"broker_id": "okx_demo"}],
            "positions": [],
            "errors": [],
            "connected_brokers": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    cached = es.get_unified_cached_state()

    assert cached["connected_brokers"] == []
    assert cached["sync_status"] == "error"


@pytest.mark.asyncio
async def test_exchange_cache_rejects_non_finite_json(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import exchange_sync as es
    from aletheia.server.routes import bitget
    from aletheia.storage.sqlite import execute_write

    with pytest.raises(ValueError):
        es._store_unified({"total_equity_usdt": float("inf")})

    execute_write(
        "INSERT INTO settings(key, value) VALUES(?, ?)",
        ("exchange_account_state.unified", '{"equity_usdt": Infinity}'),
    )
    cached = es.get_unified_cached_state()
    assert es.status_for(cached) == "error"
    assert cached["cache_error"] == "invalid_cache"
    assert cached["equity_usdt"] is None

    execute_write(
        "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
        ("exchange_account_state.unified", "{"),
    )
    payload = await bitget.uta_state()
    assert payload["sync_status"] == "error"
    assert payload["cache_error"] == "invalid_cache"
    assert payload["total_equity_usdt"] is None


def test_uta_v1_default_cache_never_falls_back_to_legacy_bitget(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio import exchange_sync as es

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    from aletheia.storage.sqlite import execute_write
    execute_write("INSERT INTO settings(key, value) VALUES(?, ?)",
                  ("exchange_account_state.demo", '{"equity_usdt": 999}'))

    assert es.get_unified_cached_state() is None


def test_active_broker_rejects_generation_mismatch(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync as es

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "okx_demo"
    monkeypatch.setattr(
        es,
        "get_unified_cached_state",
        lambda: {
            "age_seconds": 0,
            "sync_status": "generation_mismatch",
            "accounts": [{"broker_id": "okx_demo"}],
        },
    )

    complete, reason = es.active_broker_view_complete(cfg)

    assert complete is False
    assert reason == "unified view generation_mismatch"


def test_compute_portfolio_unconfigured_keeps_local_view(tmp_path, monkeypatch):
    """No exchange creds shouldn't break the locally-managed portfolio view."""
    setup_env(tmp_path, monkeypatch)
    # Schema defaults changed to paper_internal; this test exercises the path where
    # an external exchange is expected but creds are absent (→ "unconfigured").
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["exchange_sync_status"] == "unconfigured"
    assert out["exchange_equity_usdt"] is None
    # A demo account without a fetched authoritative equity is unavailable, not $0.
    assert out["total_value_usdt"] is None
    assert out["positions_count"] == 0


def test_compute_portfolio_ignores_exchange_cache_for_local_paper(tmp_path, monkeypatch):
    """A stale external cache must not become local-paper account value."""
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch, balances=[_FakeBalance("USDT", 10000.0, 0.0)])
    es.sync_all_exchanges()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["exchange_sync_status"] == "not_applicable"
    assert out["exchange_equity_usdt"] is None
    # The external cache cannot supply local-paper equity; its own cash ledger can.
    assert out["total_value_usdt"] == out["account_equity_usdt"] == 200.0


# --- drift detection ---

def test_external_position_count_surfaces_drift(tmp_path, monkeypatch):
    """If the exchange shows positions we didn't open via aletheia, expose the delta."""
    setup_env(tmp_path, monkeypatch)
    # Schema defaults changed to paper_internal; this test exercises the bitget-demo
    # path so classify_positions can match positions against the exchange cache.
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.mode = "demo"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    es = _patch_client(monkeypatch,
                       balances=[_FakeBalance("USDT", 10000.0, 0.0)],
                       positions=[_FakePosition(), _FakePosition(symbol="SOLUSDT")])
    es.sync_all_exchanges()

    from aletheia.portfolio.pnl import compute_portfolio
    out = compute_portfolio()
    assert out["exchange_external_positions"] == 2  # 0 local, 2 on exchange


# --- API endpoint ---

@pytest.mark.anyio
async def test_exchange_account_endpoint(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    es = _patch_client(monkeypatch)
    es.sync_all_exchanges()

    from httpx import AsyncClient, ASGITransport
    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/exchange/account")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["state"]["total_equity_usdt"] == 10000.0


@pytest.mark.anyio
async def test_positions_reconciliation_endpoint(tmp_path, monkeypatch):
    """GET /api/positions/reconciliation classifies external positions the system
    did not open."""
    setup_env(tmp_path, monkeypatch)
    # Schema defaults changed to paper_internal; this test exercises the bitget-demo
    # path so classify_positions can read the exchange cache and surface externals.
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.mode = "demo"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    es = _patch_client(monkeypatch,
                       positions=[_FakePosition(symbol="BTCUSDT", side="short")])
    es.sync_all_exchanges()

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
    es.sync_all_exchanges()

    # Local positions table must remain untouched by the sync.
    assert repo.list_positions() == []


def test_adopted_positions_carry_synthetic_chain(tmp_path, monkeypatch):
    """trust §1.1 amendment: an external position MAY enter the local table, but only
    via adopt_position, which must build a complete `source="adopted"` causal chain
    (proposal → order → position) with non-null back-links — keeping the chain honest
    instead of forbidding entry. See docs/governance/decisions/DECISIONS.md
    (2026-06-12)."""
    setup_env(tmp_path, monkeypatch)
    # Schema defaults changed to paper_internal; set paper_execution_mode = "bitget_demo"
    # so that adopt_position("demo") maps to bitget_demo execution_mode, matching the
    # assertion below (demo account → bitget_demo so position_manager auto-manages it).
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    from aletheia.portfolio.position_reconciler import adopt_position

    ex = {"symbol": "BTCUSDT", "side": "short", "quantity": 0.0004,
          "entry_price": 61524.3, "unrealized_pnl": -0.87, "mark_price": 63713.9,
          "market_type": "usdt_futures", "leverage": 3,
          "leverage_source": "exchange_reported"}
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
    assert pos["unrealized_pnl_source"] == "exchange_exact"
    # Honest mapping: demo account → bitget_demo (so position_manager auto-manages it).
    assert pos["execution_mode"] == "bitget_demo"
    # Entry/qty come from the exchange truth, not fabricated.
    assert pos["entry_price"] == 61524.3
    assert pos["quantity"] == 0.0004
    assert order["status"] == "filled"


def test_adopt_preserves_exchange_open_time(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.position_reconciler import adopt_position

    opened_at_ms = 1784044800000
    result = adopt_position({
        "symbol": "BTCUSDT", "side": "long", "quantity": 0.1,
        "entry_price": 60000.0, "cTime": opened_at_ms,
        "market_type": "usdt_futures", "leverage": 2,
        "leverage_source": "exchange_reported",
    }, "demo")

    position = repo._select_one("positions", result["position_id"])
    assert position["opened_at"] == datetime.fromtimestamp(
        opened_at_ms / 1000, tz=timezone.utc,
    ).isoformat()


def test_adopt_live_position_is_human_only_by_execution_mode(tmp_path, monkeypatch):
    """An adopted LIVE position must get execution_mode=bitget_live_limited, which
    position_manager skips — so live stays human-only by construction."""
    setup_env(tmp_path, monkeypatch)
    # Schema defaults changed exchange.name to "paper_internal"; _execution_mode_for("live")
    # resolves via _LIVE_EXECUTION_MODES[cfg.exchange.name], which requires "bitget" to
    # return "bitget_live_limited".
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.name = "bitget"
    save_config(cfg)
    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.portfolio import position_manager as pm

    ex = {"symbol": "ETHUSDT", "side": "long", "quantity": 0.5,
          "entry_price": 3000.0, "unrealized_pnl": 5.0, "mark_price": 3010.0,
          "market_type": "usdt_futures", "leverage": 2,
          "leverage_source": "exchange_reported"}
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
def test_exact_live_refresh_does_not_replace_demo_cache(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import exchange_sync as es
    from aletheia.uta import manager
    from aletheia.uta.models import UTAAccountIdentity

    demo = UTAAccountIdentity(account_id="okx_demo", execution_mode="okx_demo",
        venue="okx", environment="demo", profile="okx_demo", credential_generation=1,
        adapter_implementation="CCXTUTAAdapter", adapter_version="1", ccxt_version="4.5.56")
    live = demo.model_copy(update={"account_id": "okx_live_limited",
        "execution_mode": "okx_live_limited", "environment": "live", "profile": "okx_live"})
    monkeypatch.setattr(manager, "build_uta_registry", lambda cfg: SimpleNamespace(
        configured_identities=lambda: (demo, live)))

    def query(*, execution_modes=None):
        identity = live if execution_modes == (live.execution_mode,) else demo
        amount = 25.0 if identity == live else 100.0
        return manager.UnifiedState(amount, amount, 0.0,
            accounts=[{"broker_id": identity.execution_mode, "identity": identity.model_dump(mode="json"),
                       "equity_usdt": amount, "available_usdt": amount}],
            connected_brokers=[identity.execution_mode],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            execution_modes=execution_modes or manager.DEFAULT_UNIFIED_EXECUTION_MODES)

    monkeypatch.setattr(manager, "get_unified_state_sync", query)
    es.sync_all_exchanges()
    live_state = es.sync_all_exchanges(execution_modes=(live.execution_mode,))
    assert es.status_for(live_state) == "ok"
    assert es.get_unified_cached_state()["total_equity_usdt"] == 100.0
    assert es.get_unified_cached_state(execution_modes=(live.execution_mode,))["total_equity_usdt"] == 25.0
    from aletheia.risk.engine import account_available_for_execution
    assert account_available_for_execution(live.execution_mode) == 25.0
