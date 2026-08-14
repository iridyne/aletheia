from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from aletheia.data.providers import Candle, OHLCVResult
from aletheia.storage.models import Opportunity, Position


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from aletheia.config import loader
    from aletheia.storage import sqlite as sqlite_store
    from aletheia.storage.migrations import apply_migrations

    sqlite_store.reset_db()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    apply_migrations()
    yield
    sqlite_store.reset_db()
    loader._invalidate_config_cache()


def _seed_position(
    *,
    position_id: str = "position-path-1",
    opened_at: str = "2026-08-13T00:00:00+00:00",
    symbol: str = "BTC/USDT",
    venue: str = "bitget",
    identity_suffix: str = "1",
) -> Position:
    from aletheia.storage import repositories as repo

    asset_id = f"asset-{identity_suffix}"
    snapshot_id = f"snapshot-{identity_suffix}"
    member_id = f"member-{identity_suffix}"
    opportunity_id = f"opportunity-path-{identity_suffix}"
    db = repo.get_db()
    db.execute(
        "INSERT INTO universe_registry "
        "(id,venue,market_type,symbol,status,first_seen_at,last_seen_at) "
        "VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
        (asset_id, venue, "perp", symbol, "active"),
    )
    db.execute(
        "INSERT INTO universe_snapshots (id,status,created_at) "
        "VALUES (?,'active',datetime('now'))",
        (snapshot_id,),
    )
    db.execute(
        "INSERT INTO universe_members "
        "(id,snapshot_id,asset_id,symbol,market_type,rank,total_score,"
        "opportunity_score,tradability_score,execution_eligible,expires_at) "
        "VALUES (?,?,?,?,?,1,90,90,90,1,datetime('now','+1 day'))",
        (member_id, snapshot_id, asset_id, symbol, "perp"),
    )
    repo.insert_opportunity(
        Opportunity(
            id=opportunity_id,
            symbol=symbol,
            market="usdt_futures",
            direction_bias="long",
            source_agent="test",
            universe_snapshot_id=snapshot_id,
            universe_member_id=member_id,
        )
    )
    position = Position(
        id=position_id,
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opportunity_id=opportunity_id,
        entry_fee=0.01,
        funding_cost=0.0,
        funding_source="local_paper_funding_v2",
        stop_price=95.0,
        take_profit_price=110.0,
        opened_at=opened_at,
    )
    repo.insert_position(position)
    return position


@pytest.mark.asyncio
async def test_observer_defers_when_provider_cannot_prove_closed_bar(
    monkeypatch,
):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio.position_path_observer import observe_open_position_paths
    from aletheia.storage import repositories as repo

    position = _seed_position()
    bucket = 1_786_579_200_000
    repo.upsert_ohlcv_candles(
        position.symbol,
        "5m",
        [
            Candle(
                bucket,
                100.0,
                104.0,
                99.0,
                103.0,
                12.0,
                True,
                "stored",
                "bitget",
            )
        ],
        provider="stored",
        exchange="bitget",
        market_type="perp",
    )
    calls = {"created": 0, "requested": 0, "closed": 0}

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            calls["requested"] += 1
            assert str(instrument) == "bitget:BTC/USDT:perp"
            assert timeframe == "5m"
            assert limit == 2
            assert since in {bucket, bucket + 300_000}
            return OHLCVResult(
                [
                    Candle(
                        since,
                        100.0,
                        105.0,
                        98.0,
                        104.0,
                        13.0,
                        calls["requested"] > 1,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            calls["closed"] += 1

    def create_provider(**_):
        calls["created"] += 1
        return Provider()

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", create_provider
    )
    now = datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)

    result = await observe_open_position_paths(now=now)

    assert result == {
        "positions": 1,
        "enqueued_buckets": 1,
        "enqueued_position_buckets": 1,
        "catchup_bucket_count": 0,
        "inserted": 0,
        "available": 0,
        "unavailable": 0,
        "deferred": 1,
        "errors": [],
    }
    assert calls == {"created": 1, "requested": 1, "closed": 1}
    assert repo.list_position_path_observations(position.id) == []

    retried = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 900_000) / 1000, tz=timezone.utc)
    )
    assert retried["inserted"] == 2
    assert retried["available"] == 2
    assert retried["deferred"] == 0
    assert calls == {"created": 3, "requested": 3, "closed": 3}
    rows = repo.list_position_path_observations(position.id)
    assert len(rows) == 2
    assert rows[0]["evidence_status"] == "available"
    assert (
        repo.get_db()
        .execute(
            "SELECT COUNT(*) FROM position_path_observation_deferred_buckets "
            "WHERE position_id=? AND bucket_open_ms=?",
            (position.id, bucket),
        )
        .fetchone()[0]
        == 0
    )


@pytest.mark.asyncio
async def test_observer_requests_once_per_unique_instrument(
    monkeypatch,
):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio.position_path_observer import observe_open_position_paths
    from aletheia.storage import repositories as repo

    first_position = _seed_position()
    second_position = first_position.model_copy(update={"id": "position-path-2"})
    repo.insert_position(second_position)
    bucket = 1_786_579_200_000
    calls = {"created": 0, "requested": 0, "closed": 0}

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            calls["requested"] += 1
            assert str(instrument) == "bitget:BTC/USDT:perp"
            assert timeframe == "5m"
            assert limit == 2
            assert since == bucket
            return OHLCVResult(
                [
                    Candle(
                        bucket,
                        100.0,
                        104.0,
                        99.0,
                        103.0,
                        12.0,
                        True,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            calls["closed"] += 1

    def create_provider(**_):
        calls["created"] += 1
        return Provider()

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", create_provider
    )
    now = datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)

    result = await observe_open_position_paths(now=now)

    assert result == {
        "positions": 2,
        "enqueued_buckets": 1,
        "enqueued_position_buckets": 2,
        "catchup_bucket_count": 0,
        "inserted": 2,
        "available": 2,
        "unavailable": 0,
        "deferred": 0,
        "errors": [],
    }
    assert calls == {"created": 1, "requested": 1, "closed": 1}
    assert len(repo.list_position_path_observations(first_position.id)) == 1
    assert len(repo.list_position_path_observations(second_position.id)) == 1
    assert repo.list_position_path_observations(first_position.id)[0][
        "capture_mode"
    ] == "open_snapshot"


@pytest.mark.asyncio
async def test_observer_samples_position_closed_after_target_bucket(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio.position_path_observer import observe_open_position_paths
    from aletheia.storage import repositories as repo

    bucket = 1_786_579_200_000
    position = _seed_position(position_id="position-path-just-closed")
    closed_at = datetime.fromtimestamp(
        (bucket + 300_000) / 1000, tz=timezone.utc
    ).isoformat()
    repo.update_position(position.id, status="closed", closed_at=closed_at)
    calls = {"requested": 0}

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            calls["requested"] += 1
            assert since == bucket
            return OHLCVResult(
                [
                    Candle(
                        bucket,
                        100.0,
                        104.0,
                        99.0,
                        103.0,
                        12.0,
                        True,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            return None

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: Provider()
    )

    result = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )

    assert result == {
        "positions": 1,
        "enqueued_buckets": 1,
        "enqueued_position_buckets": 1,
        "catchup_bucket_count": 0,
        "inserted": 1,
        "available": 1,
        "unavailable": 0,
        "deferred": 0,
        "errors": [],
    }
    assert calls["requested"] == 1
    rows = repo.list_position_path_observations(position.id)
    assert len(rows) == 1
    assert rows[0]["bucket_open_ms"] == bucket
    assert rows[0]["capture_mode"] == "post_close_final_snapshot"


@pytest.mark.asyncio
async def test_observer_skips_existing_bucket_before_provider_lookup(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio.position_path_observer import (
        _store_unavailable,
        observe_open_position_paths,
    )
    from aletheia.storage import repositories as repo

    bucket = 1_786_579_200_000
    position = _seed_position(position_id="position-path-observed")
    assert _store_unavailable(
        position.model_dump(),
        bucket_open_ms=bucket,
        observed_at="2026-08-13T00:05:00+00:00",
        reason="exact_5m_bar_unavailable",
    )

    def unexpected_provider(**_):
        raise AssertionError("an existing observation must skip provider lookup")

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", unexpected_provider
    )

    result = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )

    assert result == {
        "positions": 1,
        "enqueued_buckets": 1,
        "enqueued_position_buckets": 0,
        "catchup_bucket_count": 0,
        "inserted": 0,
        "available": 0,
        "unavailable": 0,
        "deferred": 0,
        "errors": [],
    }
    assert len(repo.list_position_path_observations(position.id)) == 1


@pytest.mark.asyncio
async def test_observer_recovers_oldest_missing_buckets_with_bounded_catchup(
    monkeypatch,
):
    from aletheia.data import ccxt_provider
    from aletheia.data.providers import Candle, OHLCVResult
    from aletheia.portfolio import position_path_observer as observer
    from aletheia.storage import repositories as repo

    bucket = 1_786_579_200_000
    position = _seed_position(position_id="position-path-gap")
    observer._store_unavailable(
        position.model_dump(),
        bucket_open_ms=bucket + 1_500_000,
        observed_at="2026-08-13T00:25:00+00:00",
        reason="seeded_latest_bucket",
    )
    requested: list[int] = []

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            requested.append(since)
            return OHLCVResult(
                [
                    Candle(
                        since,
                        100.0,
                        104.0,
                        99.0,
                        103.0,
                        12.0,
                        True,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            return None

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: Provider()
    )
    now = datetime.fromtimestamp((bucket + 1_800_000) / 1000, tz=timezone.utc)

    first = await observer.observe_open_position_paths(now=now)
    second = await observer.observe_open_position_paths(now=now)

    assert first["inserted"] == 2
    assert second["inserted"] == 2
    assert requested == [
        bucket,
        bucket + 300_000,
        bucket + 600_000,
        bucket + 900_000,
    ]
    rows = repo.list_position_path_observations(position.id)
    assert [row["bucket_open_ms"] for row in rows] == [
        bucket,
        bucket + 300_000,
        bucket + 600_000,
        bucket + 900_000,
        bucket + 1_500_000,
    ]
    recovered = rows[:-1]
    assert all(row["funding_cost"] is None for row in recovered)
    assert all(row["funding_source"] == "unavailable" for row in recovered)
    assert all(row["stop_price"] is None for row in recovered)
    assert all(row["take_profit_price"] is None for row in recovered)
    assert all(row["exit_plan_version"] == 0 for row in recovered)


@pytest.mark.asyncio
async def test_observer_recovers_closed_position_path_after_missed_runs(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.data.providers import Candle, OHLCVResult
    from aletheia.portfolio import position_path_observer as observer
    from aletheia.storage import repositories as repo

    bucket = 1_786_579_200_000
    position = _seed_position(position_id="position-path-closed-gap")
    repo.update_position(
        position.id,
        status="closed",
        closed_at=datetime.fromtimestamp(
            (bucket + 900_000) / 1000, tz=timezone.utc
        ).isoformat(),
    )
    requested: list[int] = []

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            requested.append(since)
            return OHLCVResult(
                [
                    Candle(
                        since,
                        100.0,
                        104.0,
                        99.0,
                        103.0,
                        12.0,
                        True,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            return None

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: Provider()
    )
    now = datetime.fromtimestamp((bucket + 1_800_000) / 1000, tz=timezone.utc)

    results = [
        await observer.observe_open_position_paths(now=now)
        for _ in range(2)
    ]
    assert [result["inserted"] for result in results] == [2, 1]

    assert requested == [bucket, bucket + 300_000, bucket + 600_000]
    rows = repo.list_position_path_observations(position.id)
    assert [row["bucket_open_ms"] for row in rows] == [
        bucket,
        bucket + 300_000,
        bucket + 600_000,
    ]
    assert all(row["capture_mode"] == "post_close_final_snapshot" for row in rows)


@pytest.mark.asyncio
async def test_observer_terminal_recovery_uses_position_budget_for_the_round(
    monkeypatch,
):
    from aletheia.data import ccxt_provider
    from aletheia.data.providers import Candle, OHLCVResult
    from aletheia.portfolio import position_path_observer as observer
    from aletheia.storage import repositories as repo

    bucket = 1_786_579_200_000
    position = _seed_position(position_id="position-path-terminal-budget")
    repo.defer_position_path_observation_bucket(
        position.id,
        bucket_open_ms=bucket,
        attempted_at="2026-08-13T00:05:00+00:00",
        reason="scheduled",
    )
    for _ in range(3):
        repo.defer_position_path_observation_bucket(
            position.id,
            bucket_open_ms=bucket,
            attempted_at="2026-08-13T00:10:00+00:00",
            reason="market_data_fetch_failed",
        )
    requested: list[int] = []

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            requested.append(since)
            return OHLCVResult(
                [
                    Candle(
                        since,
                        100.0,
                        104.0,
                        99.0,
                        103.0,
                        12.0,
                        True,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            return None

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: Provider()
    )
    now = datetime.fromtimestamp((bucket + 600_000) / 1000, tz=timezone.utc)

    first = await observer.observe_open_position_paths(now=now)
    assert first["unavailable"] == 1
    assert requested == []
    assert repo.list_oldest_deferred_position_path_bucket() == []

    second = await observer.observe_open_position_paths(now=now)
    assert second["available"] == 1
    assert requested == [bucket + 300_000]


@pytest.mark.asyncio
async def test_observer_rejects_closed_bar_with_wrong_provider_identity(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.data.providers import Candle, OHLCVResult
    from aletheia.portfolio import position_path_observer as observer
    from aletheia.storage import repositories as repo

    bucket = 1_786_579_200_000
    position = _seed_position(position_id="position-path-bar-identity")

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            return OHLCVResult(
                [
                    Candle(
                        bucket,
                        100.0,
                        104.0,
                        99.0,
                        103.0,
                        12.0,
                        True,
                        "ccxt",
                        "okx",
                    )
                ],
                requested=2,
            )

        async def close(self):
            return None

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: Provider()
    )

    result = await observer.observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )

    assert result["deferred"] == 1
    assert repo.list_position_path_observations(position.id) == []


def test_position_path_observer_status_exposes_recovery_and_24h_counts():
    from aletheia.portfolio.position_path_observer import (
        _store_unavailable,
        build_position_path_observer_status,
    )
    from aletheia.storage import repositories as repo

    bucket = 1_786_579_200_000
    now = datetime.fromtimestamp((bucket + 1_800_000) / 1000, tz=timezone.utc)
    position = _seed_position(position_id="position-path-status")
    assert repo.insert_position_path_observation(
        {
            "position_id": position.id,
            "bucket_open_ms": bucket + 300_000,
            "observed_at": "2026-08-13T00:06:00+00:00",
            "venue": "bitget",
            "symbol": "BTC/USDT",
            "market_type": "perp",
            "direction": "long",
            "entry_price": 100.0,
            "quantity": 0.01,
            "bar_open": 100.0,
            "bar_high": 104.0,
            "bar_low": 99.0,
            "bar_close": 103.0,
            "bar_volume": 12.0,
            "bar_provider": "ccxt",
            "mark_price": 103.0,
            "unrealized_pnl": 0.03,
            "entry_fee": 0.01,
            "funding_cost": 0.0,
            "funding_source": "local_paper_funding_v2",
            "stop_price": 95.0,
            "take_profit_price": 110.0,
            "exit_plan_version": 0,
            "evidence_status": "available",
            "evidence_reason": "complete_exact_5m_bar",
            "producer_commit": None,
            "runtime_session_id": None,
            "capture_mode": "open_snapshot",
        }
    )
    assert _store_unavailable(
        position.model_dump(),
        bucket_open_ms=bucket + 600_000,
        observed_at="2026-08-13T00:11:00+00:00",
        reason="exact_5m_bar_unavailable",
    )
    repo.defer_position_path_observation_bucket(
        position.id,
        bucket_open_ms=bucket,
        attempted_at="2026-08-13T00:05:00+00:00",
        reason="scheduled",
    )

    status = build_position_path_observer_status(now=now)

    assert status["oldest_missing_bucket"] == {
        "position_id": position.id,
        "bucket_open_ms": bucket,
        "bucket_open_at": "2026-08-13T00:00:00+00:00",
    }
    assert status["deferred_bucket_count"] == 1
    assert status["observer_lag"] == {"seconds": 1500.0, "buckets": 5}
    assert status["last_24h"] == {
        "available": 1,
        "unavailable": 1,
        "skipped": 3,
    }


@pytest.mark.asyncio
async def test_observer_skips_bar_that_crosses_position_entry(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio.position_path_observer import observe_open_position_paths
    from aletheia.storage import repositories as repo

    bucket = 1_786_579_200_000
    position = _seed_position(
        opened_at=datetime.fromtimestamp(
            (bucket + 1) / 1000, tz=timezone.utc
        ).isoformat()
    )
    calls = {"created": 0, "requested": 0, "closed": 0}

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            calls["requested"] += 1
            assert since == bucket + 300_000
            return OHLCVResult(
                [
                    Candle(
                        bucket + 300_000,
                        101.0,
                        104.0,
                        100.0,
                        103.0,
                        11.0,
                        True,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            calls["closed"] += 1

    def create_provider(**_):
        calls["created"] += 1
        return Provider()

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", create_provider
    )

    skipped = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )
    assert skipped == {
        "positions": 1,
        "enqueued_buckets": 1,
        "enqueued_position_buckets": 0,
        "catchup_bucket_count": 0,
        "inserted": 0,
        "available": 0,
        "unavailable": 0,
        "deferred": 0,
        "errors": [],
    }
    assert calls == {"created": 0, "requested": 0, "closed": 0}
    assert repo.list_position_path_observations(position.id) == []

    recorded = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 600_000) / 1000, tz=timezone.utc)
    )
    assert recorded["inserted"] == 1
    assert calls == {"created": 1, "requested": 1, "closed": 1}
    rows = repo.list_position_path_observations(position.id)
    assert len(rows) == 1
    assert rows[0]["bucket_open_ms"] == bucket + 300_000



@pytest.mark.asyncio
async def test_observer_persists_completed_instrument_before_other_timeout(
    monkeypatch,
):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio import position_path_observer as observer
    from aletheia.storage import repositories as repo

    fast = _seed_position(position_id="position-fast", identity_suffix="fast")
    slow = _seed_position(
        position_id="position-slow",
        symbol="ETH/USDT",
        venue="okx",
        identity_suffix="slow",
    )
    bucket = 1_786_579_200_000
    closed_exchanges: list[str] = []
    slow_cancelled_after_fast_persisted = False

    class Provider:
        def __init__(self, exchange: str):
            self.exchange = exchange

        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            nonlocal slow_cancelled_after_fast_persisted
            if self.exchange == "okx":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    slow_cancelled_after_fast_persisted = bool(
                        repo.list_position_path_observations(fast.id)
                    )
                    raise
            return OHLCVResult(
                [
                    Candle(
                        bucket,
                        100.0,
                        104.0,
                        99.0,
                        103.0,
                        12.0,
                        True,
                        "ccxt",
                        self.exchange,
                    )
                ],
                requested=2,
            )

        async def close(self):
            closed_exchanges.append(self.exchange)

    monkeypatch.setattr(
        ccxt_provider,
        "get_market_data_provider",
        lambda **kwargs: Provider(kwargs["exchange"]),
    )
    monkeypatch.setattr(observer, "_OHLCV_REQUEST_TIMEOUT_SECONDS", 10.0)
    monkeypatch.setattr(observer, "_OBSERVATION_CYCLE_TIMEOUT_SECONDS", 0.02)

    result = await observer.observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )

    assert result["inserted"] == 1
    assert result["available"] == 1
    assert result["unavailable"] == 0
    assert result["deferred"] == 1
    assert result["errors"] == [
        {
            "instrument": "okx:ETH/USDT:perp",
            "error_type": "ObservationCycleTimeout",
        }
    ]
    assert slow_cancelled_after_fast_persisted is True
    assert repo.list_position_path_observations(fast.id)[0]["evidence_status"] == "available"
    assert repo.list_position_path_observations(slow.id) == []
    assert sorted(closed_exchanges) == ["bitget", "okx"]


@pytest.mark.asyncio
async def test_observer_bounds_unique_instrument_concurrency(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio import position_path_observer as observer
    from aletheia.storage import repositories as repo

    count = observer._MAX_CONCURRENT_OHLCV_REQUESTS + 2
    positions = [
        _seed_position(
            position_id=f"position-concurrent-{index}",
            symbol=f"COIN{index}/USDT",
            identity_suffix=f"concurrent-{index}",
        )
        for index in range(count)
    ]
    bucket = 1_786_579_200_000
    active = 0
    maximum_active = 0
    requested = 0
    closed = 0

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            nonlocal active, maximum_active, requested
            active += 1
            requested += 1
            maximum_active = max(maximum_active, active)
            try:
                await asyncio.sleep(0)
                return OHLCVResult(
                    [
                        Candle(
                            bucket,
                            100.0,
                            104.0,
                            99.0,
                            103.0,
                            12.0,
                            True,
                            "ccxt",
                            "bitget",
                        )
                    ],
                    requested=2,
                )
            finally:
                active -= 1

        async def close(self):
            nonlocal closed
            closed += 1

    provider = Provider()
    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: provider
    )

    result = await observer.observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )

    assert result["inserted"] == count
    assert requested == count
    assert maximum_active == observer._MAX_CONCURRENT_OHLCV_REQUESTS
    assert closed == 1
    assert all(repo.list_position_path_observations(position.id) for position in positions)


@pytest.mark.asyncio
async def test_observer_rereads_position_closed_during_request(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio.position_path_observer import observe_open_position_paths
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-closed-during-request")
    bucket = 1_786_579_200_000
    closed = 0

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            repo.update_position(
                position.id,
                status="closed",
                closed_at=datetime.fromtimestamp(
                    (bucket + 300_000) / 1000, tz=timezone.utc
                ).isoformat(),
                entry_price=120.0,
                quantity=0.02,
                current_price=107.0,
                unrealized_pnl=0.0,
                entry_fee=0.25,
                funding_cost=0.5,
                stop_price=92.0,
                take_profit_price=118.0,
                exit_plan_version=3,
            )
            return OHLCVResult(
                [
                    Candle(
                        bucket,
                        100.0,
                        131.0,
                        99.0,
                        130.0,
                        12.0,
                        True,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            nonlocal closed
            closed += 1

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: Provider()
    )

    result = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )

    assert result["inserted"] == 1
    row = repo.list_position_path_observations(position.id)[0]
    assert row["capture_mode"] == "post_close_final_snapshot"
    assert row["entry_price"] == 120.0
    assert row["quantity"] == 0.02
    assert row["mark_price"] == 107.0
    assert row["unrealized_pnl"] == 0.0
    assert row["entry_fee"] == 0.25
    assert row["funding_cost"] == 0.5
    assert row["stop_price"] == 92.0
    assert row["take_profit_price"] == 118.0
    assert row["exit_plan_version"] == 3
    assert row["bar_close"] == 130.0
    assert closed == 1

@pytest.mark.asyncio
async def test_observer_persists_invalid_opened_at_as_unavailable(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio.position_path_observer import observe_open_position_paths
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-invalid-opened-at")
    repo.update_position(position.id, opened_at="not-a-timestamp")
    monkeypatch.setattr(
        ccxt_provider,
        "get_market_data_provider",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("invalid opened_at must not request market data")
        ),
    )
    bucket = 1_786_579_200_000

    result = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )

    assert result["inserted"] == 1
    assert result["unavailable"] == 1
    row = repo.list_position_path_observations(position.id)[0]
    assert row["bucket_open_ms"] == bucket
    assert row["evidence_status"] == "unavailable"
    assert row["evidence_reason"] == "position_opened_at_invalid"
    assert repo.list_oldest_deferred_position_path_bucket() == []


@pytest.mark.asyncio
async def test_observer_persists_unavailable_after_bounded_market_data_retries(
    monkeypatch,
):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio import position_path_observer as observer
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    position = _seed_position(position_id="position-retry-backoff")
    bucket = 1_786_579_200_000

    class Provider:
        async def get_ohlcv(self, *args, **kwargs):
            raise TimeoutError("transient provider failure")

        async def close(self):
            return None

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: Provider()
    )
    results = []
    for offset in (300_000, 600_000, 900_000):
        results.append(
            await observer.observe_open_position_paths(
                now=datetime.fromtimestamp((bucket + offset) / 1000, tz=timezone.utc)
            )
        )

    assert [result["deferred"] for result in results] == [1, 1, 0]
    assert [result["inserted"] for result in results] == [0, 0, 1]
    assert results[-1]["unavailable"] == 1
    rows = repo.list_position_path_observations(position.id)
    assert len(rows) == 1
    assert rows[0]["evidence_status"] == "unavailable"
    assert rows[0]["evidence_reason"] == "max_retries_exceeded:market_data_fetch_failed"
    assert repo.list_oldest_deferred_position_path_bucket() == []
    assert (
        get_db()
        .execute(
            "SELECT COUNT(*) FROM position_path_observation_deferred_buckets "
            "WHERE position_id=? AND bucket_open_ms=?",
            (position.id, bucket),
        )
        .fetchone()[0]
        == 0
    )


def test_unavailable_identity_is_preserved_and_reported():
    from aletheia.analytics.position_path_observations import build_position_path_report
    from aletheia.portfolio.position_path_observer import _store_unavailable
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-unavailable")
    repo.update_position(
        position.id,
        status="closed",
        closed_at="2026-08-13T00:05:00+00:00",
    )
    raw = repo.get_position(position.id)
    assert raw is not None
    inserted = _store_unavailable(
        raw,
        bucket_open_ms=1_786_579_200_000,
        observed_at="2026-08-13T00:05:00+00:00",
        reason="instrument_identity_unavailable",
    )

    assert inserted is True
    report = build_position_path_report(position.id)
    assert report["path_evidence_status"] == "partial_data"
    assert report["unavailable_reasons"] == {"instrument_identity_unavailable": 1}
    assert report["mfe_observed_pct"] is None
    rows = repo.list_position_path_observations(position.id)
    assert rows[0]["capture_mode"] == "post_close_final_snapshot"

    db = repo.get_db()
    with pytest.raises(Exception, match="append-only"):
        db.execute(
            "UPDATE position_path_observations SET evidence_reason='changed' "
            "WHERE position_id=?",
            (position.id,),
        )


@pytest.mark.asyncio
async def test_observer_recovers_skipped_buckets_for_position_closed_during_outage(
    monkeypatch,
):
    from aletheia.data import ccxt_provider
    from aletheia.portfolio.position_path_observer import observe_open_position_paths
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-catchup")
    bucket = 1_786_579_200_000

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            assert timeframe == "5m"
            return OHLCVResult(
                [
                    Candle(
                        int(since),
                        100.0,
                        104.0,
                        99.0,
                        103.0,
                        12.0,
                        True,
                        "ccxt",
                        "bitget",
                    )
                ],
                requested=2,
            )

        async def close(self):
            return None

    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider", lambda **_: Provider()
    )
    first = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 300_000) / 1000, tz=timezone.utc)
    )
    assert first["enqueued_buckets"] == 1
    assert first["enqueued_position_buckets"] == 1

    closed_at = datetime.fromtimestamp(
        (bucket + 900_000) / 1000, tz=timezone.utc
    ).isoformat()
    repo.update_position(position.id, status="closed", closed_at=closed_at)

    resumed = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 900_000) / 1000, tz=timezone.utc)
    )
    assert resumed["catchup_bucket_count"] == 1
    assert resumed["enqueued_buckets"] == 2
    assert resumed["enqueued_position_buckets"] == 2
    assert resumed["inserted"] == 1

    finished = await observe_open_position_paths(
        now=datetime.fromtimestamp((bucket + 900_000) / 1000, tz=timezone.utc)
    )
    assert finished["enqueued_buckets"] == 0
    assert finished["inserted"] == 1
    assert [
        row["bucket_open_ms"]
        for row in repo.list_position_path_observations(position.id)
    ] == [bucket, bucket + 300_000, bucket + 600_000]
    cursor = repo.get_position_path_observer_cursor()
    assert cursor is not None
    assert cursor["last_enqueued_bucket_open_ms"] == bucket + 600_000


def test_observer_first_cursor_initialization_does_not_backfill_history():
    from aletheia.storage import repositories as repo

    position = _seed_position(
        position_id="position-path-first-cursor",
        opened_at="2026-08-12T00:00:00+00:00",
    )
    bucket = 1_786_579_200_000

    result = repo.enqueue_position_path_observation_buckets_through(
        bucket,
        observed_at="2026-08-13T00:05:00+00:00",
    )

    assert result == {
        "enqueued_buckets": 1,
        "enqueued_position_buckets": 1,
        "catchup_bucket_count": 0,
    }
    deferred = repo.list_oldest_deferred_position_path_bucket()
    assert [(row["position_id"], row["bucket_open_ms"]) for row in deferred] == [
        (position.id, bucket)
    ]
    assert repo.get_position_path_observer_cursor()["last_enqueued_bucket_open_ms"] == bucket


def test_observer_cursor_and_deferred_rows_roll_back_together():
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-cursor-rollback")
    bucket = 1_786_579_200_000

    with pytest.raises(Exception):
        repo.enqueue_position_path_observation_buckets_through(
            bucket,
            observed_at="not-a-timestamp",
        )

    assert repo.get_position_path_observer_cursor() is None
    assert repo.list_oldest_deferred_position_path_bucket() == []

    result = repo.enqueue_position_path_observation_buckets_through(
        bucket,
        observed_at="2026-08-13T00:05:00+00:00",
    )
    assert result["enqueued_position_buckets"] == 1
    assert repo.get_position_path_observer_cursor()["last_enqueued_bucket_open_ms"] == bucket
    assert repo.list_oldest_deferred_position_path_bucket()[0]["position_id"] == position.id
