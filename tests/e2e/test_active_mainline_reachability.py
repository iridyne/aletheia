"""Production-entry reachability proofs for outcome and Learning side effects."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_scheduler_registration_reaches_net_outcome_side_effect(
    e2e_db, monkeypatch
):
    """The registered outcome job reaches real settlement and a durable net result."""
    from aletheia.agent_runtime import scheduler
    from aletheia.config.loader import load_config, save_config
    from aletheia.data.providers import Candle
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome

    cfg = load_config()
    cfg.runtime.scheduler_enabled = True
    cfg.runtime.paused = False
    save_config(cfg)

    # Register all production jobs without starting worker threads. APScheduler's
    # clock/threads are the external runtime boundary; add_job remains real.
    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler.start",
        lambda self, *args, **kwargs: None,
    )
    scheduler._scheduler = None
    registered = scheduler.init_scheduler()
    assert registered is not None
    job = registered.get_job("outcome_settler")
    assert job is not None
    assert job.trigger.interval == timedelta(minutes=10)

    now = datetime.now(timezone.utc)
    entry = now - timedelta(minutes=20)
    due = entry + timedelta(minutes=15)
    outcome = UniverseForwardOutcome(
        id="reachability-outcome",
        outcome_candidate_key="reachability-candidate:15m",
        member_id="reachability-member",
        snapshot_id="reachability-snapshot",
        symbol="BTC/USDT",
        venue="fixture",
        market_type="spot",
        scanner_family="momentum",
        direction="long",
        horizon="15m",
        entry_price_usd=100.0,
        entry_spread_bps=4.0,
        entry_ts=entry.isoformat(),
        due_at=due.isoformat(),
    )
    repo.insert_forward_outcomes_batch([outcome])

    class FixtureMarketProvider:
        async def get_ohlcv(self, instrument, timeframe="1m", limit=100, since=None):
            return [
                Candle(int(entry.timestamp() * 1000), 100.0, 100.5, 99.5, 100.0, 10.0),
                Candle(
                    int((due - timedelta(minutes=1)).timestamp() * 1000),
                    100.0, 102.0, 99.8, 101.5, 20.0,
                ),
            ]

        async def get_ticker(self, instrument):
            raise AssertionError("fixed-horizon OHLCV should settle this outcome")

        async def close(self):
            return None

    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda *args, **kwargs: FixtureMarketProvider(),
    )
    # Guard-candidate generation is a separate downstream feature. Keep its real
    # entry reachable but prevent this positive fixture from writing Markdown.
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._maybe_generate_guard_candidates",
        lambda rows: None,
    )

    # This is the exact coroutine the registered scheduler adapter invokes.
    await scheduler._async_settle_outcomes()
    settled = repo._select_one("universe_forward_outcomes", outcome.id)
    assert settled["status"] == "settled"
    assert settled["net_return_pct"] is not None
    assert settled["cost_model_version"] is not None
    assert settled["settlement_price_source"] == "horizon_ohlcv"

    scheduler._scheduler = None
