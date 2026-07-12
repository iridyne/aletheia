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
        "aletheia.data.ccxt_provider.get_market_data_provider",
        lambda: FixtureMarketProvider(),
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


def test_registered_learning_consumer_persists_and_links_exact_exposure(
    e2e_db, monkeypatch
):
    """A real allocator consumer reaches the ledger and its exact outcome reader."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.learning.ledger import linked_settled_exposure_counts
    from aletheia.learning.registry import get_learning_path
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome
    from aletheia.universe.allocator import compute_total_score

    cfg = load_config()
    cfg.agent.autonomous_learning = True
    cfg.runtime.paused = False
    save_config(cfg)

    path = get_learning_path("universe_family_calibration")
    assert path.consumer == "universe allocator"
    assert path.fallback == "neutral multiplier 1.0"

    feature = {
        "feature_snapshot_id": "reachability-feature",
        "market_type": "perp",
        "tradability_score": 80.0,
        "quote_volume_24h": 1_000_000_000.0,
        "spread_bps": 3.0,
        "rsi_14": 60.0,
        "return_4h": 1.0,
        "data_freshness_sec": 30.0,
    }
    scanner = {
        "opportunity_score": 80.0,
        "scanner_family": "momentum",
        "scanner_direction": "long",
    }
    score = compute_total_score(
        feature,
        scanner,
        cfg.universe,
        calibration={"momentum:long:perp": 1.1},
    )
    assert score > 0
    assert feature["learning_candidate_key"] == (
        "universe:reachability-feature:momentum:long:perp"
    )
    assert feature["learning_exposure_id"] is not None

    exposure = e2e_db.execute(
        "SELECT * FROM learning_exposures WHERE exposure_id=?",
        (feature["learning_exposure_id"],),
    ).fetchone()
    assert exposure is not None
    expected_outcome_key = (
        "universe:reachability-feature:momentum:long:perp:4h"
    )
    assert exposure["path_id"] == "universe_family_calibration"
    assert exposure["outcome_candidate_key"] == expected_outcome_key
    assert exposure["consumer"] == "universe_allocator"
    assert exposure["after_json"] == exposure["before_json"]

    now = datetime.now(timezone.utc)
    repo.insert_forward_outcomes_batch([UniverseForwardOutcome(
        id="reachability-linked-outcome",
        outcome_candidate_key=expected_outcome_key,
        member_id="reachability-feature",
        snapshot_id="reachability-snapshot",
        symbol="BTC/USDT",
        venue="fixture",
        market_type="perp",
        scanner_family="momentum",
        direction="long",
        horizon="4h",
        entry_price_usd=100.0,
        entry_ts=(now - timedelta(hours=5)).isoformat(),
        due_at=(now - timedelta(hours=1)).isoformat(),
        settled_at=now.isoformat(),
        exit_price_usd=102.0,
        gross_return_pct=2.0,
        directional_return_pct=2.0,
        estimated_fee_pct=0.12,
        estimated_spread_slippage_pct=0.10,
        estimated_funding_pct=0.01,
        net_return_pct=1.77,
        min_tradable_edge_pct=0.23,
        settlement_price_source="horizon_ohlcv",
        settlement_lag_sec=0.0,
        cost_model_version="outcome_economics.v1",
        status="settled",
    )])

    linked = linked_settled_exposure_counts(
        exposure["experiment_id"], policy_epoch=exposure["concurrent_policy_epoch"]
    )
    assert linked[exposure["assignment"]] == 1
    assert sum(linked.values()) == 1
