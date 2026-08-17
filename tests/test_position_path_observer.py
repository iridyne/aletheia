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
    entry_order_id = f"test-entry-order-{position_id}"
    db.execute(
        "INSERT INTO orders "
        "(id,symbol,market_type,direction,order_type,quantity,price,"
        "execution_mode,position_id,status,source,created_at,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            entry_order_id,
            symbol,
            "usdt_futures",
            "long",
            "market",
            0.01,
            100.0,
            "paper_internal",
            position_id,
            "filled",
            "native",
            opened_at,
            opened_at,
        ),
    )
    db.execute(
        "INSERT INTO fills "
        "(id,order_id,fill_price,fill_quantity,fee,fee_currency,fee_source,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            f"test-entry-fill-{position_id}",
            entry_order_id,
            100.0,
            0.01,
            0.01,
            "USDT",
            "estimated",
            opened_at,
        ),
    )
    db.commit()
    repo.update_position(position_id, order_id=entry_order_id)
    return position


def _insert_entry_leg(
    position: Position,
    *,
    leg_id: str,
    quantity: float,
    price: float,
    filled_at: str,
    fee: float,
) -> None:
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    order_id = f"{leg_id}-order"
    db.execute(
        "INSERT INTO orders "
        "(id,symbol,market_type,direction,order_type,quantity,price,"
        "execution_mode,position_id,status,source,created_at,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            order_id,
            position.symbol,
            position.market_type,
            position.direction,
            "market",
            quantity,
            price,
            position.execution_mode,
            position.id,
            "filled",
            "native",
            filled_at,
            filled_at,
        ),
    )
    db.execute(
        "INSERT INTO fills "
        "(id,order_id,fill_price,fill_quantity,fee,fee_currency,fee_source,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            f"{leg_id}-fill",
            order_id,
            price,
            quantity,
            fee,
            "USDT",
            "estimated",
            filled_at,
        ),
    )
    db.commit()


def _insert_close_event(
    position: Position,
    *,
    event_id: str,
    quantity: float,
    price: float | None,
    closed_at: str,
    entry_fee: float | None,
    realized_pnl: float | None,
    is_partial: int,
) -> None:
    from aletheia.storage import repositories as repo

    repo.get_db().execute(
        "INSERT INTO position_close_events "
        "(id,position_id,symbol,direction,execution_mode,close_reason,"
        "exit_price,closed_qty,realized_pnl,entry_fee,exit_fee,is_partial,closed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            position.id,
            position.symbol,
            position.direction,
            position.execution_mode,
            "manual",
            price,
            quantity,
            realized_pnl,
            entry_fee,
            0.01,
            is_partial,
            closed_at,
        ),
    )
    repo.get_db().commit()


def test_projector_projects_single_entry_as_of_bucket():
    from aletheia.portfolio.position_path_observer import _project_position_state_as_of
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-single-entry")
    state = _project_position_state_as_of(
        repo.get_position(position.id),
        as_of_ms=1_786_579_200_000,
    )

    assert state["quantity"] == 0.01
    assert state["entry_price"] == 100.0
    assert state["remaining_notional"] == 1.0
    assert state["entry_fee"] == 0.01


def test_projector_weights_multiple_entry_fills_only_when_visible():
    from aletheia.portfolio.position_path_observer import _project_position_state_as_of
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-multiple-entry")
    _insert_entry_leg(
        position,
        leg_id="multiple-entry-leg",
        quantity=0.02,
        price=120.0,
        filled_at="2026-08-13T00:04:00+00:00",
        fee=0.02,
    )
    state = _project_position_state_as_of(
        repo.get_position(position.id),
        as_of_ms=1_786_579_500_000,
    )

    assert state["quantity"] == 0.03
    assert state["entry_price"] == pytest.approx(113.3333333333)
    assert state["entry_fee"] == pytest.approx(0.03)


def test_projector_applies_scale_in_and_partial_reduction_as_of_time():
    from aletheia.portfolio.position_path_observer import _project_position_state_as_of
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-partial-projector")
    _insert_entry_leg(
        position,
        leg_id="partial-scale-in-leg",
        quantity=0.02,
        price=120.0,
        filled_at="2026-08-13T00:04:00+00:00",
        fee=0.02,
    )
    _insert_close_event(
        position,
        event_id="partial-reduction-event",
        quantity=0.01,
        price=130.0,
        closed_at="2026-08-13T00:06:00+00:00",
        entry_fee=0.01,
        realized_pnl=0.19,
        is_partial=1,
    )
    state = _project_position_state_as_of(
        repo.get_position(position.id),
        as_of_ms=1_786_579_800_000,
    )

    assert state["quantity"] == pytest.approx(0.02)
    assert state["entry_price"] == pytest.approx(113.3333333333)
    assert state["remaining_notional"] == pytest.approx(2.2666666667)
    assert state["entry_fee"] == pytest.approx(0.02)
    assert state["realized_pnl"] == pytest.approx(0.19)


def test_historical_snapshot_uses_exit_plan_effective_at_bucket():
    import hashlib
    import json

    from aletheia.portfolio.position_path_observer import (
        _historical_position_snapshot,
    )
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-plan-projector")
    db = repo.get_db()
    for version, effective_at, stop_price in (
        (1, "2026-08-13T00:00:00+00:00", 95.0),
        (2, "2026-08-13T00:07:00+00:00", 97.0),
    ):
        plan_json = json.dumps(
            {
                "stop_price": stop_price,
                "take_profit_price": 110.0,
                "exit_limit_price": None,
                "exit_limit_source": None,
            },
            sort_keys=True,
        )
        db.execute(
            "INSERT INTO position_exit_plan_versions "
            "(position_id,version,effective_at,stop_price,take_profit_price,"
            "exit_limit_price,exit_limit_source,plan_json,plan_hash,provenance,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                position.id,
                version,
                effective_at,
                stop_price,
                110.0,
                None,
                None,
                plan_json,
                hashlib.sha256(plan_json.encode()).hexdigest(),
                "test",
                effective_at,
            ),
        )
    db.commit()

    first = _historical_position_snapshot(
        repo.get_position(position.id),
        bucket_open_ms=1_786_579_200_000,
    )
    second = _historical_position_snapshot(
        repo.get_position(position.id),
        bucket_open_ms=1_786_579_800_000,
    )

    assert (first["exit_plan_version"], first["stop_price"]) == (1, 95.0)
    assert (second["exit_plan_version"], second["stop_price"]) == (2, 97.0)


def test_projector_terminalizes_final_close_without_reusing_current_state():
    from aletheia.portfolio.position_path_observer import _project_position_state_as_of
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-final-close-projector")
    db = repo.get_db()
    close_order_id = "final-close-projector-order"
    close_at = "2026-08-13T00:12:00+00:00"
    db.execute(
        "INSERT INTO orders "
        "(id,symbol,market_type,direction,order_type,quantity,price,"
        "execution_mode,position_id,close_reason,status,source,created_at,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            close_order_id,
            position.symbol,
            position.market_type,
            position.direction,
            "market",
            0.01,
            130.0,
            position.execution_mode,
            position.id,
            "stop_loss",
            "filled",
            "native",
            close_at,
            close_at,
        ),
    )
    db.execute(
        "INSERT INTO fills "
        "(id,order_id,fill_price,fill_quantity,fee,fee_currency,fee_source,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "final-close-projector-fill",
            close_order_id,
            130.0,
            0.01,
            0.01,
            "USDT",
            "estimated",
            close_at,
        ),
    )
    db.commit()
    _insert_close_event(
        position,
        event_id="final-close-projector-event",
        quantity=0.01,
        price=None,
        closed_at=close_at,
        entry_fee=0.01,
        realized_pnl=0.29,
        is_partial=0,
    )

    before_close = _project_position_state_as_of(
        repo.get_position(position.id),
        as_of_ms=1_786_579_800_000,
    )
    after_close = _project_position_state_as_of(
        repo.get_position(position.id),
        as_of_ms=1_786_580_100_000,
    )

    assert before_close["quantity"] == 0.01
    assert after_close["quantity"] is None
    assert after_close["_historical_state_reason"] == "historical_position_fully_closed"

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
        quantity=0.005,
        entry_price=120.0,
        current_price=130.0,
        unrealized_pnl=0.0,
        entry_fee=0.005,
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
    assert all(row["quantity"] == 0.01 for row in rows)
    assert all(row["entry_price"] == 100.0 for row in rows)
    assert all(row["mark_price"] == 103.0 for row in rows)
    assert all(row["unrealized_pnl"] == pytest.approx(0.03) for row in rows)
    assert len({(row["position_id"], row["bucket_open_ms"]) for row in rows}) == 3


@pytest.mark.asyncio
async def test_observer_recovers_oldest_bucket_for_each_position(monkeypatch):
    from aletheia.data import ccxt_provider
    from aletheia.data.providers import Candle, OHLCVResult
    from aletheia.portfolio import position_path_observer as observer
    from aletheia.storage import repositories as repo

    first_position = _seed_position(
        position_id="position-path-batch-1",
        opened_at="2026-08-13T00:00:01+00:00",
        identity_suffix="batch-1",
    )
    second_position = _seed_position(
        position_id="position-path-batch-2",
        opened_at="2026-08-13T01:00:01+00:00",
        symbol="ETH/USDT",
        identity_suffix="batch-2",
    )
    repo.update_position(
        first_position.id,
        status="closed",
        closed_at="2026-08-13T00:16:00+00:00",
    )
    repo.update_position(
        second_position.id,
        status="closed",
        closed_at="2026-08-13T01:16:00+00:00",
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
    now = datetime.fromisoformat("2026-08-13T02:00:00+00:00")

    result = await observer.observe_open_position_paths(now=now)

    assert result["inserted"] == 4
    assert result["available"] == 4
    assert sorted(requested) == [
        int(datetime.fromisoformat(value).timestamp() * 1000)
        for value in (
            "2026-08-13T00:05:00+00:00",
            "2026-08-13T00:10:00+00:00",
            "2026-08-13T01:05:00+00:00",
            "2026-08-13T01:10:00+00:00",
        )
    ]
    assert {
        (row["position_id"], row["bucket_open_ms"])
        for row in repo.list_position_path_observations(first_position.id)
    } == {
        (
            first_position.id,
            int(datetime.fromisoformat(value).timestamp() * 1000),
        )
        for value in (
            "2026-08-13T00:05:00+00:00",
            "2026-08-13T00:10:00+00:00",
        )
    }
    assert {
        (row["position_id"], row["bucket_open_ms"])
        for row in repo.list_position_path_observations(second_position.id)
    } == {
        (
            second_position.id,
            int(datetime.fromisoformat(value).timestamp() * 1000),
        )
        for value in (
            "2026-08-13T01:05:00+00:00",
            "2026-08-13T01:10:00+00:00",
        )
    }


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
    assert status["latest_expected_bucket"]["bucket_open_ms"] == bucket + 1_500_000
    assert status["latest_materialized_bucket"] == {
        "position_id": position.id,
        "bucket_open_ms": bucket + 600_000,
        "bucket_open_at": "2026-08-13T00:10:00+00:00",
    }
    assert status["oldest_deferred_bucket"]["bucket_open_ms"] == bucket
    assert status["ordered_path_coverage"] == {
        "status": "incomplete",
        "expected_bucket_count": 6,
        "materialized_bucket_count": 2,
        "missing_bucket_count": 4,
        "deferred_bucket_count": 1,
        "invalid_position_count": 0,
        "available_observation_count": 1,
        "unavailable_observation_count": 1,
        "coverage_ratio": 1 / 3,
    }
    assert status["unavailable_reason"] == "deferred_bucket_pending"


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


def test_historical_snapshot_projects_state_from_lifecycle_facts():
    from aletheia.portfolio.position_path_observer import (
        _historical_position_snapshot,
        _project_position_state_as_of,
    )
    from aletheia.storage import repositories as repo

    position = _seed_position(
        position_id="position-path-historical-projector",
        opened_at="2026-08-13T00:00:00+00:00",
    )
    db = repo.get_db()
    db.execute(
        "INSERT INTO orders "
        "(id,symbol,market_type,direction,order_type,quantity,price,"
        "execution_mode,position_id,status,source,created_at,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "historical-entry-order",
            position.symbol,
            position.market_type,
            "long",
            "market",
            1.0,
            100.0,
            "paper_internal",
            position.id,
            "filled",
            "native",
            position.opened_at,
            position.opened_at,
        ),
    )
    db.execute(
        "INSERT INTO fills "
        "(id,order_id,fill_price,fill_quantity,fee,fee_currency,fee_source,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "historical-entry-fill",
            "historical-entry-order",
            100.0,
            1.0,
            0.1,
            "USDT",
            "estimated",
            position.opened_at,
        ),
    )
    db.execute(
        "INSERT INTO orders "
        "(id,symbol,market_type,direction,order_type,quantity,price,"
        "execution_mode,position_id,status,source,created_at,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "historical-scale-in-order",
            position.symbol,
            position.market_type,
            "long",
            "market",
            0.5,
            120.0,
            "paper_internal",
            position.id,
            "filled",
            "native",
            "2026-08-13T00:07:00+00:00",
            "2026-08-13T00:07:00+00:00",
        ),
    )
    db.execute(
        "INSERT INTO fills "
        "(id,order_id,fill_price,fill_quantity,fee,fee_currency,fee_source,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "historical-scale-in-fill",
            "historical-scale-in-order",
            120.0,
            0.5,
            0.06,
            "USDT",
            "estimated",
            "2026-08-13T00:07:00+00:00",
        ),
    )
    close_order_id = "historical-partial-close-order"
    close_at = "2026-08-13T00:12:00+00:00"
    db.execute(
        "INSERT INTO orders "
        "(id,symbol,market_type,direction,order_type,quantity,price,"
        "execution_mode,position_id,close_reason,status,source,created_at,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            close_order_id,
            position.symbol,
            position.market_type,
            "long",
            "market",
            0.25,
            130.0,
            "paper_internal",
            position.id,
            "manual",
            "filled",
            "native",
            close_at,
            close_at,
        ),
    )
    db.execute(
        "INSERT INTO fills "
        "(id,order_id,fill_price,fill_quantity,fee,fee_currency,fee_source,filled_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "historical-partial-close-fill",
            close_order_id,
            130.0,
            0.25,
            0.0325,
            "USDT",
            "estimated",
            close_at,
        ),
    )
    db.execute(
        "INSERT INTO position_close_events "
        "(id,position_id,symbol,direction,execution_mode,close_reason,"
        "exit_price,closed_qty,realized_pnl,entry_fee,exit_fee,is_partial,closed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "historical-partial-close",
            position.id,
            position.symbol,
            "long",
            "paper_internal",
            "manual",
            130.0,
            0.25,
            7.475,
            0.025,
            0.0325,
            1,
            "2026-08-13T00:12:00+00:00",
        ),
    )
    db.commit()
    repo.update_position(
        position.id,
        status="closed",
        quantity=1.26,
        entry_price=106.6225165563,
        current_price=130.0,
        unrealized_pnl=0.0,
        entry_fee=0.145,
        funding_cost=9.0,
        closed_at="2026-08-13T00:17:00+00:00",
    )

    snapshot = _historical_position_snapshot(
        repo.get_position(position.id),
        bucket_open_ms=1_786_579_200_000,
    )

    assert snapshot["quantity"] == 1.01
    assert snapshot["entry_price"] == 100.0
    assert snapshot["current_price"] is None
    assert snapshot["unrealized_pnl"] is None
    assert snapshot["entry_fee"] == 0.11
    assert snapshot["funding_cost"] is None

    after_partial = _project_position_state_as_of(
        repo.get_position(position.id),
        as_of_ms=1_786_580_100_000,
    )
    assert after_partial["quantity"] == 1.26
    assert after_partial["entry_price"] == pytest.approx(106.6225165563)
    assert after_partial["remaining_notional"] == pytest.approx(134.3443708609)
    assert after_partial["entry_fee"] == pytest.approx(0.145)
    assert after_partial["realized_pnl"] == pytest.approx(7.475)


def test_historical_snapshot_projects_scale_in_through_bucket_close():
    from aletheia.portfolio.position_path_observer import _historical_position_snapshot
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-bucket-close")
    _insert_entry_leg(
        position,
        leg_id="bucket-close-scale-in",
        quantity=0.5,
        price=120.0,
        filled_at="2026-08-13T00:04:00+00:00",
        fee=0.06,
    )

    snapshot = _historical_position_snapshot(
        repo.get_position(position.id),
        bucket_open_ms=1_786_579_200_000,
    )

    assert snapshot["quantity"] == pytest.approx(0.51)
    assert snapshot["entry_price"] == pytest.approx(119.6078431373)
    assert snapshot["entry_fee"] == pytest.approx(0.07)


def test_projector_converts_base_asset_fill_fee_to_quote():
    from aletheia.portfolio.position_path_observer import _project_position_state_as_of
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-base-fee")
    repo.get_db().execute(
        "UPDATE fills SET fee=?, fee_currency=? WHERE order_id=?",
        (0.001, "BTC", f"test-entry-order-{position.id}"),
    )
    repo.get_db().commit()

    state = _project_position_state_as_of(
        repo.get_position(position.id),
        as_of_ms=1_786_579_200_000,
    )

    assert state["entry_fee"] == pytest.approx(0.1)


def test_projector_rejects_fill_with_mismatched_execution_mode():
    from aletheia.portfolio.position_path_observer import _project_position_state_as_of
    from aletheia.storage import repositories as repo

    position = _seed_position(position_id="position-path-mode-mismatch")
    repo.get_db().execute(
        "UPDATE orders SET execution_mode=? WHERE position_id=?",
        ("okx_demo", position.id),
    )
    repo.get_db().commit()

    state = _project_position_state_as_of(
        repo.get_position(position.id),
        as_of_ms=1_786_579_200_000,
    )

    assert state["quantity"] is None
    assert state["_historical_state_reason"] == "historical_lifecycle_identity_mismatch"
