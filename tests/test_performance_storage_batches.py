"""Focused proof that hot local telemetry paths share short SQLite transactions."""

from __future__ import annotations

from datetime import datetime, timezone
import asyncio
from types import SimpleNamespace

from aletheia.agents import helpers
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import AgentRun, UniverseAsset, UniverseFeatureSnapshot
from aletheia.storage.sqlite import get_db, reset_db


def _setup_db(tmp_path, monkeypatch) -> None:
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    run_migrations()


def _transaction_trace(db):
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    return statements


def test_registry_bulk_upsert_uses_one_transaction(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    assets = [
        UniverseAsset(venue="okx", market_type="spot", symbol="BTC/USDT", last_seen_at=now),
        UniverseAsset(venue="okx", market_type="perp", symbol="ETH/USDT", last_seen_at=now),
    ]
    trace = _transaction_trace(db)

    inserted, other, stale = repo.sync_universe_assets(
        assets,
        active_venue="okx",
        stale_cutoff_iso=now,
    )

    assert (inserted, other, stale) == (2, 0, 0)
    assert sum(statement == "BEGIN" for statement in trace) == 1
    assert sum(statement == "COMMIT" for statement in trace) == 1
    assert db.execute("SELECT COUNT(*) FROM universe_registry").fetchone()[0] == 2
    reset_db()


def test_collector_tick_uses_one_transaction(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    trace = _transaction_trace(db)

    counts = repo.insert_collector_snapshot_batch(
        orderbooks=[{
            "symbol": "BTC/USDT",
            "exchange": "okx",
            "market_type": "spot",
            "bid_price": 100.0,
            "ask_price": 101.0,
            "spread_bps": 100.0,
            "bid_depth_10bps_usd": 1_000.0,
            "ask_depth_10bps_usd": 900.0,
            "total_depth_10bps_usd": 1_900.0,
            "imbalance_ratio": 0.1,
            "fetch_latency_ms": 10,
            "observed_at": now,
        }],
        funding=[{
            "symbol": "BTC/USDT",
            "exchange": "okx",
            "funding_rate": 0.0001,
            "funding_rate_annualized": 0.1095,
            "next_funding_time": None,
            "mark_price": 100.0,
            "observed_at": now,
        }],
        open_interest=[{
            "symbol": "BTC/USDT",
            "exchange": "okx",
            "open_interest_usd": 1_000_000.0,
            "open_interest_coin": 10_000.0,
            "oi_change_1h_pct": 1.0,
            "long_short_ratio": 1.1,
            "observed_at": now,
        }],
    )

    assert counts == {"orderbooks": 1, "funding": 1, "open_interest": 1}
    assert sum(statement == "BEGIN" for statement in trace) == 1
    assert sum(statement == "COMMIT" for statement in trace) == 1
    reset_db()


def test_agent_trace_flushes_as_one_batch_transaction(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    run = AgentRun(agent_name="research_agent", trigger_type="manual")
    repo.insert_agent_run(run)
    db = get_db()
    trace = _transaction_trace(db)

    helpers.log_tool(run.id, "get_market_snapshot", "read_tool", True)
    helpers.record_step(run.id, "considered evidence")
    helpers.log_tool(run.id, "create_research_note", "write_tool", True)
    assert helpers.flush_run_telemetry(run.id) == 3

    assert sum(statement == "BEGIN" for statement in trace) == 1
    assert sum(statement == "COMMIT" for statement in trace) == 1
    assert len(repo.list_run_steps(run.id)) == 3
    reset_db()


def test_context_collector_defers_network_results_into_one_transaction(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.data.collectors.defillama import DefiLlamaCollector
    from aletheia.data.collectors.macro import MacroCollector
    from aletheia.data.models import MacroSnapshot
    from aletheia.storage.models import OnchainSnapshot

    now = datetime.now(timezone.utc).isoformat()

    async def collect_defillama(self):
        assert self.persist is False
        return {
            "snapshots": [
                OnchainSnapshot(
                    source="defillama",
                    metric_type="chain_tvl",
                    total_usd=1_000_000,
                    observed_at=now,
                )
            ],
            "errors": [],
        }

    async def collect_macro(_self, _symbols):
        return {"snapshot": MacroSnapshot(observed_at=now), "errors": []}

    monkeypatch.setattr(scheduler, "_get_observed_instruments", lambda _cfg: [])
    monkeypatch.setattr(DefiLlamaCollector, "collect", collect_defillama)
    monkeypatch.setattr(MacroCollector, "collect", collect_macro)
    cfg = SimpleNamespace(
        market_data=SimpleNamespace(
            enable_hyperliquid=False,
            enable_defillama=True,
            enable_coingecko_top=False,
            enable_coinalyze=False,
            derivatives_history_hours=0,
        )
    )
    db = get_db()
    trace = _transaction_trace(db)

    result = asyncio.run(scheduler._async_data_collector_context(cfg, tick=1))

    assert result == {"status": "success", "error_codes": []}
    assert sum(statement == "BEGIN" for statement in trace) == 1
    assert sum(statement == "COMMIT" for statement in trace) == 1
    assert db.execute("SELECT COUNT(*) FROM onchain_snapshots").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM macro_snapshots").fetchone()[0] == 1
    reset_db()


def test_feature_snapshots_use_one_executemany_transaction(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    repo.sync_universe_assets(
        [
            UniverseAsset(id="asset-1", venue="okx", market_type="spot", symbol="BTC/USDT", last_seen_at=now),
            UniverseAsset(id="asset-2", venue="okx", market_type="spot", symbol="ETH/USDT", last_seen_at=now),
        ],
        active_venue="okx",
        stale_cutoff_iso=now,
    )
    trace = _transaction_trace(db)

    inserted = repo.insert_universe_feature_snapshots_batch(
        [
            UniverseFeatureSnapshot(asset_id="asset-1"),
            UniverseFeatureSnapshot(asset_id="asset-2"),
        ]
    )

    assert inserted == 2
    assert sum(statement == "BEGIN" for statement in trace) == 1
    assert sum(statement == "COMMIT" for statement in trace) == 1
    assert db.execute("SELECT COUNT(*) FROM universe_feature_snapshots").fetchone()[0] == 2
    reset_db()
