"""Focused coverage for bounded active-Universe OHLCV catch-up."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _member(symbol: str, asset_id: str, rank: int = 1) -> dict:
    return {
        "symbol": symbol,
        "asset_id": asset_id,
        "market_type": "perp",
        "rank": rank,
        "execution_eligible": 1,
    }


def test_plan_uses_exact_identity_prioritizes_1h_and_honors_pair_limit(monkeypatch):
    from aletheia.data import active_ohlcv_backfill as backfill

    members = [_member("NEW/USDT", "new", 1), _member("OLD/USDT", "old", 2)]
    assets = {
        "new": {"venue": "okx", "market_type": "perp"},
        "old": {"venue": "okx", "market_type": "perp"},
    }
    coverage = {
        ("okx", "NEW/USDT", "perp", "1h"): 0,
        ("okx", "NEW/USDT", "perp", "4h"): 0,
        ("okx", "OLD/USDT", "perp", "1h"): 250,
        ("okx", "OLD/USDT", "perp", "4h"): 20,
    }

    monkeypatch.setattr(backfill.repo, "get_active_universe_members", lambda limit=50: members)
    monkeypatch.setattr(backfill.repo, "get_universe_asset", lambda asset_id: assets[asset_id])
    monkeypatch.setattr(
        backfill.repo,
        "get_ohlcv_coverage",
        lambda symbol, timeframe, exchange="", market_type="": {
            "bar_count": coverage[(exchange, symbol, market_type, timeframe)]
        },
    )
    monkeypatch.setattr(backfill.repo, "get_ohlcv_backfill_state", lambda *args: None)
    monkeypatch.setattr(backfill.repo, "upsert_ohlcv_backfill_state", lambda **kwargs: None)

    planned = backfill.plan_active_ohlcv_backfill(min_bars=200, max_pairs=2)

    assert [(item.symbol, item.timeframe) for item in planned] == [
        ("NEW/USDT", "1h"),
        ("NEW/USDT", "4h"),
    ]
    assert all(item.exchange == "okx" and item.market_type == "perp" for item in planned)


def test_plan_skips_target_until_durable_retry_time(monkeypatch):
    from aletheia.data import active_ohlcv_backfill as backfill

    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill.repo, "get_active_universe_members", lambda limit=50: [_member("NEW/USDT", "new")]
    )
    monkeypatch.setattr(
        backfill.repo,
        "get_universe_asset",
        lambda asset_id: {"venue": "okx", "market_type": "perp"},
    )
    monkeypatch.setattr(
        backfill.repo,
        "get_ohlcv_coverage",
        lambda *args, **kwargs: {"bar_count": 0},
    )
    monkeypatch.setattr(
        backfill.repo,
        "get_ohlcv_backfill_state",
        lambda *args: {"next_attempt_at": (now + timedelta(minutes=20)).isoformat()},
    )
    monkeypatch.setattr(backfill.repo, "upsert_ohlcv_backfill_state", lambda **kwargs: None)

    assert backfill.plan_active_ohlcv_backfill(
        min_bars=200, max_pairs=3, now=now
    ) == []


def test_plan_repairs_active_member_even_when_not_yet_execution_eligible(monkeypatch):
    from aletheia.data import active_ohlcv_backfill as backfill

    member = _member("WATCH/USDT", "watch")
    member["execution_eligible"] = 0
    monkeypatch.setattr(
        backfill.repo, "get_active_universe_members", lambda limit=50: [member]
    )
    monkeypatch.setattr(
        backfill.repo,
        "get_universe_asset",
        lambda asset_id: {"venue": "okx", "market_type": "perp"},
    )
    monkeypatch.setattr(
        backfill.repo,
        "get_ohlcv_coverage",
        lambda *args, **kwargs: {"bar_count": 0},
    )
    monkeypatch.setattr(backfill.repo, "get_ohlcv_backfill_state", lambda *args: None)
    monkeypatch.setattr(backfill.repo, "upsert_ohlcv_backfill_state", lambda **kwargs: None)

    planned = backfill.plan_active_ohlcv_backfill(min_bars=200, max_pairs=2)

    assert {(item.symbol, item.timeframe) for item in planned} == {
        ("WATCH/USDT", "1h"),
        ("WATCH/USDT", "4h"),
    }


@pytest.mark.asyncio
async def test_run_records_partial_coverage_and_defers_retry(monkeypatch):
    from aletheia.data import active_ohlcv_backfill as backfill
    from aletheia.data.history_downloader import DownloadResult

    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    target = backfill.CoverageTarget(
        exchange="okx",
        symbol="NEW/USDT",
        market_type="perp",
        timeframe="1h",
        rank=1,
        bar_count=0,
    )
    monkeypatch.setattr(
        backfill, "plan_active_ohlcv_backfill", lambda **kwargs: [target]
    )
    download = AsyncMock(
        return_value=DownloadResult("NEW/USDT", "1h", 75, 1, 2, None)
    )
    monkeypatch.setattr(backfill, "download_symbol_history", download)
    monkeypatch.setattr(
        backfill.repo,
        "get_ohlcv_coverage",
        lambda *args, **kwargs: {"bar_count": 75},
    )
    writes: list[dict] = []
    monkeypatch.setattr(
        backfill.repo, "upsert_ohlcv_backfill_state", lambda **kwargs: writes.append(kwargs)
    )

    result = await backfill.run_active_ohlcv_backfill(
        min_bars=200, max_pairs=3, months=1, now=now
    )

    download.assert_awaited_once_with(
        "NEW/USDT", "1h", months=1, exchange="okx", market_type="perp"
    )
    assert result.attempted == 1
    assert result.partial == 1
    assert result.failed == 0
    assert writes[-1]["status"] == "partial"
    assert writes[-1]["bar_count"] == 75
    assert writes[-1]["next_attempt_at"] > now.isoformat()


@pytest.mark.asyncio
async def test_run_applies_exponential_failure_backoff_without_persisting_raw_error(monkeypatch):
    from aletheia.data import active_ohlcv_backfill as backfill
    from aletheia.data.history_downloader import DownloadResult

    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    target = backfill.CoverageTarget(
        exchange="okx",
        symbol="BAD/USDT",
        market_type="perp",
        timeframe="1h",
        rank=1,
        bar_count=0,
        consecutive_failures=2,
    )
    monkeypatch.setattr(
        backfill, "plan_active_ohlcv_backfill", lambda **kwargs: [target]
    )
    monkeypatch.setattr(
        backfill,
        "download_symbol_history",
        AsyncMock(return_value=DownloadResult(
            "BAD/USDT", "1h", 0, None, None, "sensitive provider detail"
        )),
    )
    monkeypatch.setattr(
        backfill.repo,
        "get_ohlcv_coverage",
        lambda *args, **kwargs: {"bar_count": 0},
    )
    writes: list[dict] = []
    monkeypatch.setattr(
        backfill.repo, "upsert_ohlcv_backfill_state", lambda **kwargs: writes.append(kwargs)
    )

    result = await backfill.run_active_ohlcv_backfill(
        min_bars=200, max_pairs=1, months=1, now=now
    )

    assert result.failed == 1
    assert writes[-1]["consecutive_failures"] == 3
    assert writes[-1]["last_error"] == "provider_fetch_failed"
    assert writes[-1]["next_attempt_at"] == (now + timedelta(minutes=60)).isoformat()


@pytest.mark.asyncio
async def test_one_pair_timeout_does_not_starve_later_target(monkeypatch):
    from aletheia.data import active_ohlcv_backfill as backfill
    from aletheia.data.history_downloader import DownloadResult

    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    targets = [
        backfill.CoverageTarget("okx", "SLOW/USDT", "perp", "1h", 1, 0),
        backfill.CoverageTarget("okx", "FAST/USDT", "perp", "1h", 2, 0),
    ]
    monkeypatch.setattr(
        backfill, "plan_active_ohlcv_backfill", lambda **kwargs: targets
    )

    async def download(symbol, timeframe, **kwargs):
        if symbol == "SLOW/USDT":
            await backfill.asyncio.sleep(0.2)
        return DownloadResult(symbol, timeframe, 250, 1, 2, None)

    monkeypatch.setattr(backfill, "download_symbol_history", download)
    monkeypatch.setattr(
        backfill.repo,
        "get_ohlcv_coverage",
        lambda symbol, *args, **kwargs: {
            "bar_count": 250 if symbol == "FAST/USDT" else 0
        },
    )
    writes: list[dict] = []
    monkeypatch.setattr(
        backfill.repo, "upsert_ohlcv_backfill_state", lambda **kwargs: writes.append(kwargs)
    )

    result = await backfill.run_active_ohlcv_backfill(
        min_bars=200,
        max_pairs=2,
        months=1,
        now=now,
        pair_timeout_sec=0.1,
        run_budget_sec=1,
    )

    assert result.attempted == 2
    assert result.failed == 1
    assert result.ready == 1
    assert any(row["symbol"] == "SLOW/USDT" and row["last_error"] == "download_timeout" for row in writes)
    assert any(row["symbol"] == "FAST/USDT" and row["status"] == "ready" for row in writes)


def test_historical_data_config_exposes_bounded_active_backfill_controls():
    from aletheia.config.schema import HistoricalDataConfig

    cfg = HistoricalDataConfig()
    assert cfg.active_universe_backfill_enabled is True
    assert cfg.active_universe_backfill_interval_minutes >= 5
    assert 1 <= cfg.active_universe_backfill_max_pairs_per_run <= 10


def test_scheduler_registers_active_backfill_as_independent_bounded_job():
    from pathlib import Path

    source = Path("aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
    assert '"ohlcv_active_backfill"' in source
    assert "_run_active_ohlcv_backfill" in source
    assert '_ASYNC_JOB_TIMEOUT_SEC["ohlcv_active_backfill"]' in source
    assert "active_universe_backfill_interval_minutes" in source


def test_backfill_state_is_durable_and_exact_instrument_scoped(tmp_path):
    import sqlite3

    from aletheia.storage import repositories as repo
    from aletheia.storage import sqlite as sqlite_store
    from aletheia.storage.migrations import run_migrations

    sqlite_store.reset_db()
    db = sqlite3.connect(tmp_path / "state.db", check_same_thread=False)
    db.row_factory = sqlite3.Row
    sqlite_store._db_conn = db
    try:
        run_migrations()
        common = {
            "exchange": "okx",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "status": "failed",
            "bar_count": 0,
            "consecutive_failures": 1,
            "last_attempt_at": "2026-07-11T00:00:00+00:00",
            "next_attempt_at": "2026-07-11T00:15:00+00:00",
            "last_error": "no_data",
        }
        repo.upsert_ohlcv_backfill_state(**common, market_type="spot")
        repo.upsert_ohlcv_backfill_state(**common, market_type="perp")

        spot = repo.get_ohlcv_backfill_state("okx", "BTC/USDT", "spot", "1h")
        perp = repo.get_ohlcv_backfill_state("okx", "BTC/USDT", "perp", "1h")
        assert spot is not None and spot["market_type"] == "spot"
        assert perp is not None and perp["market_type"] == "perp"
        assert db.execute("SELECT COUNT(*) FROM ohlcv_backfill_state").fetchone()[0] == 2
        db.execute(
            "UPDATE ohlcv_backfill_state SET updated_at='2020-01-01T00:00:00+00:00'"
        )
        assert repo.prune_ohlcv_backfill_state(90) == 2
    finally:
        sqlite_store.reset_db()


def test_registered_job_honors_runtime_disable(monkeypatch):
    from aletheia.agent_runtime import scheduler

    cfg = SimpleNamespace(
        runtime=SimpleNamespace(paused=False),
        historical_data=SimpleNamespace(
            enabled=True,
            active_universe_backfill_enabled=False,
        ),
    )
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)

    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(scheduler, "_run_sync_with_timeout", run)

    assert scheduler._run_active_ohlcv_backfill() is None
    assert called is False


@pytest.mark.asyncio
async def test_historical_settings_route_persists_active_backfill_controls(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.files import jsonl
    from aletheia.server.routes import settings

    cfg = AletheiaConfig()
    saved: list[AletheiaConfig] = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda value: saved.append(value))
    monkeypatch.setattr(jsonl, "write_audit", lambda event: None)

    response = await settings.update_historical_data_config(
        settings.HistoricalDataConfigUpdate(
            active_universe_backfill_enabled=False,
            active_universe_backfill_interval_minutes=25,
            active_universe_backfill_max_pairs_per_run=5,
        )
    )

    assert saved == [cfg]
    assert response["historical_data"]["active_universe_backfill_enabled"] is False
    assert response["historical_data"]["active_universe_backfill_interval_minutes"] == 25
    assert response["historical_data"]["active_universe_backfill_max_pairs_per_run"] == 5
