"""Focused contract tests for Universe Momentum Fast Trigger Watch."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import sqlite3
import statistics
import threading

import pytest


def _prepare(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()


def _bars(direction: str) -> list[dict]:
    rows = []
    for index in range(20):
        rows.append({
            "ts": index * 900_000,
            "open": 100.0,
            "high": 110.0 if index == 19 else 109.0,
            "low": 90.0 if index == 19 else 91.0,
            "close": 100.0,
            "volume": 10.0,
        })
    rows.append({
        "ts": 20 * 900_000,
        "open": 100.0,
        "high": 112.0,
        "low": 88.0,
        "close": 111.0 if direction == "long" else 89.0,
        "volume": 20.0,
    })
    return rows


def _authorized_setup(
    cfg, now, *, market_type="perp", venue="bitget", fingerprint: str | None = None,
    level: float = 100.0,
):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseSetup
    from aletheia.universe.fast_trigger import authorize_setup

    leverage = 1 if market_type == "spot" else 3
    setup = UniverseSetup(
        snapshot_id="snapshot-1", member_id="member-1", asset_id="asset-1",
        venue=venue, symbol="BTC/USDT", market_type=market_type, direction="long",
        rank=1, material_fingerprint=fingerprint or "f" * 64, trigger_spec_hash="spec",
        motive_evidence='{"reasons":["test momentum setup"],"total_score":75}',
        breakout_level=level, atr=2.0,
        pullback_band=json.dumps([level - 0.5, level + 0.5]),
        entry_zone=json.dumps([level, level + 0.5]), invalidation_price=level - 2.0,
        funding_rate=0.0 if market_type == "perp" else None,
        funding_observed_at=now.isoformat() if market_type == "perp" else None,
        regime_tag="bull", valid_until=(now + timedelta(minutes=45)).isoformat(),
    )
    repo.insert_universe_setup(setup)
    authorization = authorize_setup(
        setup.id,
        reasoning="bounded momentum reclaim authorization",
        max_spread_bps=10,
        min_depth_10bps_usd=25_000,
        max_risk_pct=1.0,
        max_leverage=leverage,
        target_risk_reward=2.0,
        expected_horizon_hours=4,
        now=now,
        cfg=cfg,
    )
    return setup, authorization


def _trigger_snapshot(now):
    from aletheia.universe.fast_trigger import TriggerSnapshot

    timestamp_ms = int(now.timestamp() * 1_000)
    return TriggerSnapshot(
        observed_at=now.isoformat(), bid=100.1, ask=100.2,
        spread_bps=9.99, depth_10bps_usd=30_000, taker_buy_ratio=0.7,
        quote_exchange_timestamp_ms=timestamp_ms,
        quote_received_timestamp_ms=timestamp_ms,
        trade_exchange_timestamp_ms=timestamp_ms,
        trade_received_timestamp_ms=timestamp_ms,
        depth_exchange_timestamp_ms=timestamp_ms,
        depth_received_timestamp_ms=timestamp_ms,
        quote_sequence=3, trade_sequence=2, depth_sequence=1,
    )


@pytest.mark.parametrize(
    ("direction", "level", "zone", "invalidation"),
    [
        ("long", 110.0, (110.0, 110.2775), 108.89),
        ("short", 90.0, (89.7775, 90.0), 90.89),
    ],
)
def test_build_momentum_setup_uses_21_closed_bars(
    direction, level, zone, invalidation
):
    from aletheia.universe.fast_trigger import build_momentum_setup

    setup = build_momentum_setup(
        snapshot_id="snapshot-1",
        member_id="member-1",
        asset_id="asset-1",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction=direction,
        regime_tag="bull" if direction == "long" else "bear",
        rank=1,
        material_fingerprint="fingerprint-1",
        atr_15m_pct=1.0,
        candles=_bars(direction),
        now=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )

    assert setup.trigger_spec_hash
    assert setup.breakout_level == pytest.approx(level)
    assert json.loads(setup.entry_zone) == pytest.approx(list(zone))
    assert setup.invalidation_price == pytest.approx(invalidation)
    assert setup.state == "pending_authorization"


def test_setup_fingerprint_changes_with_breakout_structure():
    from aletheia.universe.fast_trigger import build_momentum_setup

    first = _bars("long")
    second = _bars("long")
    second[19]["high"] = 110.5
    second[-1]["close"] = 111.5
    common = {
        "snapshot_id": "snapshot-1", "member_id": "member-1",
        "asset_id": "asset-1", "venue": "bitget", "symbol": "BTC/USDT",
        "market_type": "perp", "direction": "long", "regime_tag": "bull",
        "rank": 1, "material_fingerprint": "candidate-fingerprint",
        "atr_15m_pct": 1.0, "now": datetime(2026, 8, 26, tzinfo=timezone.utc),
    }
    one = build_momentum_setup(candles=first, **common)
    two = build_momentum_setup(candles=second, **common)
    assert one.material_fingerprint != two.material_fingerprint


def test_build_momentum_setup_preserves_funding_observed_at():
    from aletheia.universe.fast_trigger import build_momentum_setup

    observed_at = "2026-08-26T11:45:00+00:00"
    setup = build_momentum_setup(
        snapshot_id="snapshot-1",
        member_id="member-1",
        asset_id="asset-1",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        regime_tag="bull",
        rank=1,
        material_fingerprint="fingerprint-1",
        atr_15m_pct=1.0,
        candles=_bars("long"),
        funding_rate=0.0004,
        funding_observed_at=observed_at,
        now=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert setup.funding_observed_at == observed_at


def test_trigger_requires_pullback_two_quotes_flow_spread_depth_and_freshness():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerEngine, TriggerPolicy

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = TriggerEngine(
        instrument=instrument,
        direction="long",
        breakout_level=100.0,
        pullback_band=(99.75, 100.25),
        entry_zone=(100.0, 100.25),
        policy=TriggerPolicy(max_spread_bps=10, min_depth_10bps_usd=1_000),
    )
    engine.confirm_subscription()

    def event(kind, at, sequence, **values):
        return FastMarketEvent(
            event_type=kind,
            instrument=instrument,
            exchange_timestamp_ms=at,
            received_timestamp_ms=at,
            sequence=sequence,
            **values,
        )

    assert engine.observe(event("quote", 1_000, 1, bid=99.98, ask=100.00)) is None
    engine.observe(event("depth", 1_050, 1, bids=((99.99, 20.0),), asks=((100.01, 20.0),)))
    engine.observe(event("trade", 1_100, 1, price=100.0, size=10.0, side="buy"))
    engine.observe(event("trade", 1_150, 2, price=100.0, size=4.0, side="sell"))
    assert engine.observe(event("quote", 1_200, 2, bid=100.04, ask=100.06)) is None
    ready = engine.observe(event("quote", 1_550, 3, bid=100.05, ask=100.07))

    assert ready is not None
    assert ready.entry_price == pytest.approx(100.07)
    assert ready.taker_buy_ratio == pytest.approx(10 / 14)
    assert ready.depth_10bps_usd >= 1_000
    assert engine.evaluate(3_001) is None  # quote/depth are stale, never REST-filled


def test_short_trigger_requires_pullback_then_two_reclaim_quotes():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerEngine, TriggerPolicy

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = TriggerEngine(
        instrument=instrument,
        direction="short",
        breakout_level=100.0,
        pullback_band=(99.75, 100.25),
        entry_zone=(99.75, 100.0),
        policy=TriggerPolicy(max_spread_bps=10, min_depth_10bps_usd=1_000),
    )
    engine.confirm_subscription()

    def event(kind, at, sequence, **values):
        return FastMarketEvent(
            event_type=kind,
            instrument=instrument,
            exchange_timestamp_ms=at,
            received_timestamp_ms=at,
            sequence=sequence,
            **values,
        )

    engine.observe(event("quote", 1_000, 1, bid=99.99, ask=100.01))
    engine.observe(
        event(
            "depth",
            1_050,
            1,
            bids=((99.99, 20.0),),
            asks=((100.01, 20.0),),
        )
    )
    engine.observe(event("trade", 1_100, 1, price=100.0, size=4.0, side="buy"))
    engine.observe(event("trade", 1_150, 2, price=100.0, size=10.0, side="sell"))
    assert engine.observe(event("quote", 1_200, 2, bid=99.96, ask=99.98)) is None
    ready = engine.observe(event("quote", 1_550, 3, bid=99.95, ask=99.97))

    assert ready is not None
    assert ready.entry_price == pytest.approx(99.95)
    assert ready.taker_buy_ratio == pytest.approx(4 / 14)


def test_reclaim_side_alone_does_not_count_as_pullback():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerEngine, TriggerPolicy

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = TriggerEngine(
        instrument=instrument,
        direction="long",
        breakout_level=100.0,
        pullback_band=(99.75, 100.25),
        entry_zone=(100.0, 100.25),
        policy=TriggerPolicy(max_spread_bps=10, min_depth_10bps_usd=100),
    )
    engine.confirm_subscription()
    for sequence, at in enumerate((1_000, 1_400), start=1):
        engine.observe(
            FastMarketEvent(
                event_type="quote",
                instrument=instrument,
                exchange_timestamp_ms=at,
                received_timestamp_ms=at,
                sequence=sequence,
                bid=100.09,
                ask=100.11,
            )
        )
    assert engine.state == "armed"


def test_sequence_regression_disarms_trigger():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerEngine, TriggerPolicy

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = TriggerEngine(
        instrument=instrument,
        direction="long",
        breakout_level=100.0,
        pullback_band=(99.75, 100.25),
        entry_zone=(100.0, 100.25),
        policy=TriggerPolicy(max_spread_bps=10, min_depth_10bps_usd=100),
    )
    engine.confirm_subscription()
    first = FastMarketEvent(
        event_type="quote", instrument=instrument, exchange_timestamp_ms=1_000,
        received_timestamp_ms=1_000, sequence=2, bid=100.0, ask=100.01,
    )
    second = first.model_copy(update={"received_timestamp_ms": 1_100, "sequence": 1})
    engine.observe(first)
    engine.observe(second)
    assert engine.health_reason == "sequence_anomaly:quote"
    assert engine.evaluate(1_100) is None


def test_authorization_and_materialization_are_single_use(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.universe.fast_trigger import (
        materialize_trigger,
    )

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, auth = _authorized_setup(cfg, now)
    monkeypatch.setattr(
        "aletheia.risk.engine.account_available_for_execution",
        lambda execution_mode, cfg: 1_000.0,
    )
    snapshot = _trigger_snapshot(now)

    first = materialize_trigger(setup.id, snapshot, cfg=cfg, now=now)
    second = materialize_trigger(setup.id, snapshot, cfg=cfg, now=now)

    assert first["status"] == "shadow_materialized"
    assert second["status"] == "already_materialized"
    assert first["authorization_id"] == auth.id
    assert len(repo.list_opportunities()) == 1
    assert len(repo.list_trade_proposals()) == 1
    proposal = repo.get_trade_proposal(first["proposal_id"])
    assert proposal["setup_id"] == setup.id
    assert proposal["price"] == pytest.approx(100.2)
    assert proposal["take_profit"] == pytest.approx(104.6)
    assert proposal["risk_reward"] == pytest.approx(2.0)
    assert repo.get_universe_setup(setup.id)["state"] == "refused"


def test_generation_is_complete_hash_locked_and_retained(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.universe.fast_trigger import materialize_trigger

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, _ = _authorized_setup(cfg, now)
    monkeypatch.setattr(
        "aletheia.risk.engine.account_available_for_execution",
        lambda execution_mode, cfg: 1_000.0,
    )

    result = materialize_trigger(setup.id, _trigger_snapshot(now), cfg=cfg, now=now)
    opportunity = repo.get_opportunity(result["opportunity_id"])
    generation = json.loads(opportunity["generation_json"])

    assert opportunity["generation_version"] == "opportunity-generation/v2"
    assert len(opportunity["generation_hash"]) == 64
    assert generation["instrument_identity"] == {
        "venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp"
    }
    assert generation["signal_mark"] == pytest.approx(100.2)
    assert set(generation) == {
        "instrument_identity", "family", "archetype", "direction",
        "detected_at", "signal_mark", "motive_evidence", "entry_trigger",
        "entry_zone", "invalidation", "horizon_hours", "cost_snapshot",
        "material_fingerprint", "valid_until",
    }
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        repo.get_db().execute(
            "UPDATE opportunities SET generation_json='{}' WHERE id=?",
            (opportunity["id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="retained"):
        repo.get_db().execute("DELETE FROM opportunities WHERE id=?", (opportunity["id"],))


def test_entry_zone_escape_invalidates_without_repricing_generation(
    tmp_path, monkeypatch
):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.universe.fast_trigger import (
        materialize_trigger,
        validate_materialized_opportunity,
    )

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, _ = _authorized_setup(cfg, now)
    monkeypatch.setattr(
        "aletheia.risk.engine.account_available_for_execution",
        lambda execution_mode, cfg: 1_000.0,
    )
    materialized = materialize_trigger(
        setup.id, _trigger_snapshot(now), cfg=cfg, now=now, execute_gateway=False
    )
    before = repo.get_opportunity(materialized["opportunity_id"])

    at_eleven = _trigger_snapshot(now).model_copy(update={"bid": 100.3, "ask": 100.4})
    assert validate_materialized_opportunity(setup.id, at_eleven, now=now)["status"] == "valid"
    at_twenty_five = _trigger_snapshot(now).model_copy(update={"bid": 125.0, "ask": 125.1})
    invalidated = validate_materialized_opportunity(setup.id, at_twenty_five, now=now)
    after = repo.get_opportunity(materialized["opportunity_id"])

    assert invalidated["reason_code"] == "fast_trigger_entry_zone_escaped"
    assert after["status"] == "invalidated"
    assert after["generation_hash"] == before["generation_hash"]
    assert json.loads(after["generation_json"])["signal_mark"] == pytest.approx(100.2)
    assert repo.get_universe_setup(setup.id)["state"] == "invalidated"
    assessments = repo.list_opportunity_validity_assessments(after["id"])
    assert any(
        row["authority"] == "hard" and row["verdict"] == "block"
        for row in assessments
    )

    setup_2, _ = _authorized_setup(
        cfg, now, fingerprint="2" * 64, level=125.0
    )
    trigger_2 = _trigger_snapshot(now).model_copy(
        update={"bid": 125.1, "ask": 125.2}
    )
    materialized_2 = materialize_trigger(
        setup_2.id, trigger_2, cfg=cfg, now=now, execute_gateway=False
    )
    opportunity_2 = repo.get_opportunity(materialized_2["opportunity_id"])
    assert opportunity_2["id"] != after["id"]
    assert json.loads(opportunity_2["generation_json"])["signal_mark"] == pytest.approx(125.2)
    assert json.loads(after["generation_json"])["signal_mark"] == pytest.approx(100.2)


def test_market_structure_is_closed_bar_causal_and_shadow_unqualified():
    from aletheia.universe.market_structure import (
        evaluate_market_structure,
        hard_gate_decision,
    )

    candles = []
    offsets = (0.0, 0.8, 1.6, 0.8, 0.0)
    for index in range(40):
        center = 100.0 + index * 0.2
        offset = offsets[index % len(offsets)]
        candles.append({
            "ts": index * 60_000,
            "open": center,
            "high": center + 2.0 + offset,
            "low": center - 2.0 - offset,
            "close": center + 0.1,
            "volume": 10.0,
        })
    result = evaluate_market_structure(candles, direction="long")

    assert result["algorithm_version"] == "market-structure/v1"
    assert result["last_confirmed_swing_high"]["confirmed_at"] <= result["as_of"]
    assert result["last_confirmed_swing_low"]["confirmed_at"] <= result["as_of"]
    assert hard_gate_decision(result)[0] is False
    assert hard_gate_decision(result)[1].startswith("price_action_gate_unqualified:")

    gapped = candles.copy()
    gapped[20] = {**gapped[20], "ts": gapped[20]["ts"] + 1}
    assert evaluate_market_structure(gapped, direction="long")["reason"] == "candle_gap"


def test_local_paper_fast_trigger_executes_without_exchange_order(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.loader import save_config
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot
    from aletheia.universe.fast_trigger import materialize_trigger

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "execute"
    cfg.exchange.paper_execution_mode = "paper_internal"
    # Local paper owns execution/account state; Bitget is only the exact public
    # market-data venue for this first Fast Stream adapter.
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.market_data.ccxt_fallback = "bitget"
    save_config(cfg)
    setup, _ = _authorized_setup(cfg, now, market_type="spot")
    repo.insert_market_snapshot(
        MarketSnapshot(
            symbol="BTC/USDT", market_type="spot", exchange="bitget", price=100.2,
            snapshot_at=now.isoformat(),
        )
    )
    monkeypatch.setattr(
        "aletheia.risk.engine.account_available_for_execution",
        lambda execution_mode, cfg: 1_000.0,
    )
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda *args, **kwargs: 100.2,
    )
    monkeypatch.setattr(
        "aletheia.net.httpx_client",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local paper fast trigger attempted network I/O")
        ),
    )

    result = materialize_trigger(setup.id, _trigger_snapshot(now), cfg=cfg, now=now)

    assert result["status"] == "materialized"
    assert result["execution"]["status"] == "executed"
    proposal = repo.get_trade_proposal(result["proposal_id"])
    assert proposal["execution_mode"] == "paper_internal"
    assert proposal["order_type"] == "market"
    order = repo.get_order(result["execution"]["order_id"])
    assert order["execution_mode"] == "paper_internal"
    assert order["status"] == "filled"
    assert result["frame_to_gateway_ms"] <= 250.0
    order_latency_ms = (
        datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
        - now
    ).total_seconds() * 1_000
    assert order_latency_ms <= 300.0
    assert repo.get_universe_setup(setup.id)["state"] == "executed"


def test_frame_to_gateway_p95_is_within_local_budget(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import materialize_trigger

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "execute"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    monkeypatch.setattr(
        "aletheia.risk.engine.account_available_for_execution",
        lambda execution_mode, cfg: 1_000.0,
    )
    calls = []

    def _gateway(proposal_id, execution_mode):
        calls.append((proposal_id, execution_mode))
        return {"status": "executed", "proposal_id": proposal_id}

    monkeypatch.setattr(
        "aletheia.execution.gateway.execute_existing_proposal", _gateway
    )
    latencies = []
    for index in range(30):
        now = datetime.now(timezone.utc)
        setup, _ = _authorized_setup(
            cfg, now, fingerprint=hashlib.sha256(str(index).encode()).hexdigest()
        )
        result = materialize_trigger(
            setup.id, _trigger_snapshot(now), cfg=cfg, now=now
        )
        latencies.append(result["frame_to_gateway_ms"])

    p95 = statistics.quantiles(latencies, n=20)[18]
    print(f"fast_trigger_frame_to_gateway_p95_ms={p95:.3f}")
    assert len(calls) == 30
    assert p95 <= 250.0


def test_default_local_paper_uses_bitget_public_fast_stream():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import validate_fast_trigger_execution
    from aletheia.universe.fast_stream import fast_market_data_venue_for_config

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.market_data.ccxt_exchange = "binance"

    assert fast_market_data_venue_for_config(cfg) == "bitget"
    assert validate_fast_trigger_execution(cfg) is None


def test_local_paper_selects_bitget_public_data_adapter():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_stream import (
        BitgetFastMarketStream,
        fast_market_data_venue_for_config,
        fast_market_stream_for_config,
    )

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.market_data.ccxt_exchange = "binance"

    stream = fast_market_stream_for_config(cfg)

    assert fast_market_data_venue_for_config(cfg) == "bitget"
    assert isinstance(stream, BitgetFastMarketStream)


def test_concurrent_trigger_frames_materialize_once(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.universe.fast_trigger import materialize_trigger

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, _ = _authorized_setup(cfg, now)
    barrier = threading.Barrier(2)

    def available(_execution_mode, _cfg):
        barrier.wait(timeout=5)
        return 1_000.0

    monkeypatch.setattr(
        "aletheia.risk.engine.account_available_for_execution", available
    )
    snapshot = _trigger_snapshot(now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: materialize_trigger(setup.id, snapshot, cfg=cfg, now=now),
                range(2),
            )
        )

    assert {result["status"] for result in results} == {
        "shadow_materialized",
        "already_materialized",
    }
    assert len(repo.list_opportunities()) == 1
    assert len(repo.list_trade_proposals()) == 1


def test_fast_trigger_hot_path_has_no_llm_or_provider_calls():
    from aletheia.agent_runtime.fast_trigger_watch import _watch_loop
    from aletheia.universe.fast_trigger import TriggerEngine, materialize_trigger

    source = "\n".join(
        inspect.getsource(value) for value in (TriggerEngine, materialize_trigger, _watch_loop)
    ).lower()
    assert "llm_" not in source
    assert "provider" not in source


@pytest.mark.asyncio
async def test_watch_loop_reloads_setups_while_stream_is_busy(monkeypatch):
    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent, FastStreamHealth

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    first = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    second = InstrumentId.from_parts("bitget", "ETH/USDT", "perp")

    class Engine:
        def __init__(self, instrument):
            self.instrument = instrument
            self.state = "armed"

        def confirm_subscription(self):
            pass

        def observe(self, _event):
            return None

    class BusyStream:
        def __init__(self):
            self.subscriptions = []

        async def connect(self):
            pass

        async def set_instruments(self, instruments):
            self.subscriptions.append(frozenset(instruments))
            if second in instruments:
                watch._stop_event.set()

        def health(self):
            return FastStreamHealth(connected=True, subscriptions_confirmed=True)

        async def close(self):
            pass

        async def _events(self):
            sequence = 0
            while True:
                await asyncio.sleep(0.001)
                sequence += 1
                yield FastMarketEvent(
                    event_type="quote",
                    instrument=first,
                    exchange_timestamp_ms=sequence,
                    received_timestamp_ms=sequence,
                    sequence=sequence,
                    bid=100.0,
                    ask=100.1,
                )

        def events(self):
            return self._events()

    stream = BusyStream()
    load_count = 0

    async def load_watchers(_cfg, existing=None):
        nonlocal load_count
        load_count += 1
        engines = {"first": existing.get("first", Engine(first)) if existing else Engine(first)}
        if load_count > 1:
            engines["second"] = existing.get("second", Engine(second)) if existing else Engine(second)
        return {key: object() for key in engines}, engines

    monkeypatch.setattr(watch, "_WATCHER_RELOAD_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(watch, "_stop_event", asyncio.Event())
    monkeypatch.setattr(watch, "_configured_block_reason", lambda _cfg: None)
    monkeypatch.setattr(watch, "_load_watchers", load_watchers)
    monkeypatch.setattr(watch, "fast_market_stream_for_config", lambda _cfg: stream)
    monkeypatch.setattr(loader, "load_config", lambda persist_migrations=False: cfg)

    await asyncio.wait_for(watch._watch_loop(cfg), timeout=1.0)

    assert frozenset({first, second}) in stream.subscriptions
    assert load_count >= 2


def test_authorization_refuses_non_2r_target_and_cross_venue(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseSetup
    from aletheia.universe.fast_trigger import authorize_setup

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup = UniverseSetup(
        snapshot_id="s", member_id="m", asset_id="a", venue="bitget",
        symbol="BTC/USDT", market_type="perp", direction="long", rank=1,
        material_fingerprint="fp", trigger_spec_hash="spec", breakout_level=100,
        atr=2, pullback_band="[99.5,100.5]", entry_zone="[100,100.5]",
        invalidation_price=98, valid_until=(now + timedelta(minutes=30)).isoformat(),
    )
    repo.insert_universe_setup(setup)
    kwargs = {
        "reasoning": "bounded", "max_spread_bps": 10,
        "min_depth_10bps_usd": 25_000, "max_risk_pct": 1,
        "max_leverage": 3, "expected_horizon_hours": 4, "now": now, "cfg": cfg,
    }
    with pytest.raises(ValueError, match="target_must_be_2r"):
        authorize_setup(setup.id, target_risk_reward=3, **kwargs)
    repo.get_db().execute("UPDATE universe_setups SET venue='okx' WHERE id=?", (setup.id,))
    repo.get_db().commit()
    with pytest.raises(ValueError, match="execution_venue_mismatch"):
        authorize_setup(setup.id, target_risk_reward=2, **kwargs)


def test_execute_materialization_never_retries_submission_unknown(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.universe.fast_trigger import materialize_trigger

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "execute"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, _ = _authorized_setup(cfg, now)
    monkeypatch.setattr(
        "aletheia.risk.engine.account_available_for_execution",
        lambda execution_mode, cfg: 1_000.0,
    )
    calls = []

    def _execute(proposal_id, execution_mode):
        calls.append((proposal_id, execution_mode))
        return {"status": "submission_unknown", "proposal_id": proposal_id}

    monkeypatch.setattr(
        "aletheia.execution.gateway.execute_existing_proposal", _execute
    )

    first = materialize_trigger(setup.id, _trigger_snapshot(now), cfg=cfg, now=now)
    second = materialize_trigger(setup.id, _trigger_snapshot(now), cfg=cfg, now=now)

    assert first["execution"]["status"] == "submission_unknown"
    assert second["status"] == "already_materialized"
    assert calls == [(first["proposal_id"], "bitget_demo")]
    assert repo.get_universe_setup(setup.id)["state"] == "refused"


@pytest.mark.parametrize(
    ("runtime_field", "reason"),
    [
        ("paused", "fast_trigger_runtime_paused"),
        ("kill_switch_active", "fast_trigger_runtime_paused"),
    ],
)
def test_materialization_fails_closed_for_runtime_blocks(
    tmp_path, monkeypatch, runtime_field, reason
):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import materialize_trigger

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, _ = _authorized_setup(cfg, now)
    setattr(cfg.runtime, runtime_field, True)

    result = materialize_trigger(setup.id, _trigger_snapshot(now), cfg=cfg, now=now)

    assert result["reason_code"] == reason


def test_materialization_rejects_stale_t0_snapshot(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import materialize_trigger

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, _ = _authorized_setup(cfg, now)
    stale = _trigger_snapshot(now - timedelta(seconds=2))

    assert materialize_trigger(setup.id, stale, cfg=cfg, now=now)["reason_code"] == (
        "fast_trigger_quote_stale"
    )


def test_shadow_authorization_cannot_become_execute_authority(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.universe.fast_trigger import materialize_trigger

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, _ = _authorized_setup(cfg, now)
    cfg.fast_trigger.mode = "execute"

    result = materialize_trigger(setup.id, _trigger_snapshot(now), cfg=cfg, now=now)

    assert result["reason_code"] == "fast_trigger_authorization_mismatch"
    assert repo.get_universe_setup(setup.id)["state"] == "invalidated"


@pytest.mark.parametrize(
    ("mutation", "reason", "terminal"),
    [
        ("expired", "fast_trigger_authorization_expired", "expired"),
        ("mismatch", "fast_trigger_authorization_mismatch", "invalidated"),
        ("zone", "fast_trigger_entry_zone_escaped", "invalidated"),
    ],
)
def test_materialization_revalidates_authorization_and_entry_zone(
    tmp_path, monkeypatch, mutation, reason, terminal
):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.universe.fast_trigger import materialize_trigger

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    setup, authorization = _authorized_setup(cfg, now)
    snapshot = _trigger_snapshot(now)
    if mutation == "expired":
        repo.get_db().execute(
            "UPDATE conditional_entry_authorizations SET valid_until=? WHERE id=?",
            ((now - timedelta(seconds=1)).isoformat(), authorization.id),
        )
    elif mutation == "mismatch":
        repo.get_db().execute(
            "UPDATE conditional_entry_authorizations SET trigger_spec_hash='changed' WHERE id=?",
            (authorization.id,),
        )
    else:
        snapshot = snapshot.model_copy(update={"bid": 100.55, "ask": 100.6})
    repo.get_db().commit()

    result = materialize_trigger(setup.id, snapshot, cfg=cfg, now=now)

    assert result["reason_code"] == reason
    assert repo.get_universe_setup(setup.id)["state"] == terminal


def test_bitget_parser_and_subscription_ack_are_exact_venue():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import (
        BitgetFastMarketStream,
        parse_bitget_message,
    )

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    args = {"instType": "USDT-FUTURES", "instId": "BTCUSDT"}
    quote = parse_bitget_message(
        {"arg": {**args, "channel": "ticker"}, "data": [{"ts": "1", "bidPr": "100", "askPr": "101"}]},
        {instrument},
        received_timestamp_ms=2,
    )
    trade = parse_bitget_message(
        {"arg": {**args, "channel": "trade"}, "data": [{"ts": "2", "price": "100", "size": "3", "side": "buy"}]},
        {instrument},
        received_timestamp_ms=3,
    )
    depth = parse_bitget_message(
        {"arg": {**args, "channel": "books5"}, "data": [{"ts": "3", "bids": [["99", "2"]], "asks": [["101", "2"]]}]},
        {instrument},
        received_timestamp_ms=4,
    )
    assert [quote[0].event_type, trade[0].event_type, depth[0].event_type] == [
        "quote", "trade", "depth"
    ]
    stream = BitgetFastMarketStream()
    stream._instruments = {instrument}
    for channel in ("ticker", "trade", "books5", "candle1m"):
        stream._record_ack({"event": "subscribe", "arg": {**args, "channel": channel}})
    assert stream.health().subscriptions_confirmed is True


def test_bitget_trade_batch_is_normalized_to_chronological_sequence():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import parse_bitget_message

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    events = parse_bitget_message(
        {
            "arg": {"instType": "USDT-FUTURES", "channel": "trade", "instId": "BTCUSDT"},
            "data": [
                {"ts": "200", "price": "101", "size": "1", "side": "buy"},
                {"ts": "100", "price": "100", "size": "1", "side": "sell"},
            ],
        },
        {instrument},
        received_timestamp_ms=300,
    )
    assert [event.exchange_timestamp_ms for event in events] == [100, 200]
    assert events[0].sequence < events[1].sequence


def test_bitget_candle_updates_use_envelope_sequence_not_bar_open_time():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import parse_bitget_message

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    base = {
        "arg": {
            "instType": "USDT-FUTURES",
            "channel": "candle1m",
            "instId": "BTCUSDT",
        },
        "data": [["100", "99", "101", "98", "100", "5"]],
    }
    first = parse_bitget_message(
        {**base, "ts": "200"}, {instrument}, received_timestamp_ms=201
    )[0]
    second = parse_bitget_message(
        {**base, "ts": "300"}, {instrument}, received_timestamp_ms=301
    )[0]
    assert first.exchange_timestamp_ms == second.exchange_timestamp_ms == 100
    assert first.sequence < second.sequence


@pytest.mark.asyncio
async def test_bitget_ping_loop_uses_public_text_ping(monkeypatch):
    from aletheia.universe.fast_stream import BitgetFastMarketStream

    class _Ws:
        closed = False
        sent = []

        async def send_str(self, value):
            self.sent.append(value)
            stream._pong_event.set()
            self.closed = True

    async def _no_wait(_seconds):
        return None

    stream = BitgetFastMarketStream()
    stream._ws = _Ws()
    stream._pong_event = asyncio.Event()
    monkeypatch.setattr("aletheia.universe.fast_stream.asyncio.sleep", _no_wait)
    await stream._ping_loop()
    assert stream._ws.sent == ["ping"]


@pytest.mark.asyncio
async def test_bitget_missing_pong_closes_stream_for_reconnect(monkeypatch):
    from aletheia.universe.fast_stream import BitgetFastMarketStream

    class _Ws:
        closed = False

        async def send_str(self, _value):
            return None

        async def close(self):
            self.closed = True

    async def _no_wait(_seconds):
        return None

    async def _timeout(_awaitable, *, timeout):
        _awaitable.close()
        raise TimeoutError

    stream = BitgetFastMarketStream()
    stream._ws = _Ws()
    stream._pong_event = asyncio.Event()
    monkeypatch.setattr("aletheia.universe.fast_stream.asyncio.sleep", _no_wait)
    monkeypatch.setattr("aletheia.universe.fast_stream.asyncio.wait_for", _timeout)
    await stream._ping_loop()
    assert stream._ws.closed is True
    assert stream.health().reason == "fast_trigger_stream_pong_timeout:bitget"


def test_momentum_emitter_persists_setup_not_opportunity(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember, UniverseSnapshot
    from aletheia.storage.sqlite import get_db
    from aletheia.data.symbols import build_instrument_background
    from aletheia.universe.signal_emitter import emit_deterministic_opportunities

    repo.insert_universe_snapshot(
        UniverseSnapshot(id="snapshot-momentum", market_type="perp", universe_size=1)
    )
    member = UniverseMember(
        id="member-momentum", snapshot_id="snapshot-momentum", asset_id="asset-1",
        symbol="BTC/USDT", market_type="perp", rank=1, total_score=75,
        scanner_family="momentum", scanner_direction="long", venue="bitget",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_universe_member(member)
    feature = {
        "asset_id": "asset-1", "venue": "bitget", "symbol": "BTC/USDT",
        "market_type": "perp", "return_1h": 2.0, "return_4h": 3.0,
        "return_24h": 4.0, "rsi_14": 60.0, "funding_rate": 0.0,
        "atr_15m_pct": 1.0, "_closed_ohlcv_15m": _bars("long"), "reasons": [],
        "instrument_background": build_instrument_background(
            symbol="BTC/USDT",
            venue="bitget",
            market_type="perp",
            market_info={
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
            },
        ),
    }
    monkeypatch.setattr(
        "aletheia.universe.signal_emitter.lookup_factor_signal",
        lambda *args, **kwargs: {"status": "watch"},
    )

    created = emit_deterministic_opportunities(
        [member.model_dump()], "snapshot-momentum", [feature]
    )

    assert created == 0
    assert get_db().execute("SELECT COUNT(*) FROM universe_setups").fetchone()[0] == 1
    assert get_db().execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["okx_demo", "binance_testnet", "bitget_live_limited"])
def test_execute_mode_refuses_unavailable_or_live_stream(mode):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import validate_fast_trigger_execution

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "execute"
    cfg.exchange.paper_execution_mode = mode
    reason = validate_fast_trigger_execution(cfg)
    expected = (
        "fast_trigger_live_forbidden"
        if "live" in mode else f"fast_trigger_stream_unavailable:{mode.split('_')[0]}"
    )
    assert reason == expected
