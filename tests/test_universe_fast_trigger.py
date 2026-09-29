"""Focused contract tests for Universe Momentum Fast Trigger Watch."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

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
        rows.append(
            {
                "ts": index * 900_000,
                "open": 100.0,
                "high": 110.0 if index == 19 else 109.0,
                "low": 90.0 if index == 19 else 91.0,
                "close": 100.0,
                "volume": 10.0,
            }
        )
    rows.append(
        {
            "ts": 20 * 900_000,
            "open": 100.0,
            "high": 112.0,
            "low": 88.0,
            "close": 111.0 if direction == "long" else 89.0,
            "volume": 20.0,
        }
    )
    return rows


def test_trigger_requires_pullback_two_quotes_flow_spread_depth_and_freshness():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerPolicy
    from aletheia.trading.momentum_trigger import MomentumTriggerEngine

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = MomentumTriggerEngine(
        instrument=instrument,
        direction="long",
        breakout_level=100.0,
        sweep_band=(99.75, 100.25), invalidation_price=98, atr_abs=2,
        geometry_version="anchor-retrace-reclaim-geometry/v1",
        geometry_fingerprint="a" * 64, trigger_spec_id="anchor-retrace-reclaim",
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
    assert engine.observe(event("quote", 1_175, 2, bid=99.89, ask=99.91)) is None
    assert engine.observe(event("quote", 1_200, 3, bid=100.04, ask=100.06)) is None
    ready = engine.observe(event("quote", 1_550, 4, bid=100.05, ask=100.07))

    assert ready is not None
    assert ready.entry_price == pytest.approx(100.07)
    assert ready.taker_buy_ratio == pytest.approx(10 / 14)
    assert ready.depth_10bps_usd >= 1_000
    assert engine.evaluate(3_001) is None  # quote/depth are stale, never REST-filled


def test_short_trigger_requires_pullback_then_two_reclaim_quotes():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerPolicy
    from aletheia.trading.momentum_trigger import MomentumTriggerEngine

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = MomentumTriggerEngine(
        instrument=instrument,
        direction="short",
        breakout_level=100.0,
        sweep_band=(99.75, 100.25), invalidation_price=102, atr_abs=2,
        geometry_version="anchor-retrace-reclaim-geometry/v1",
        geometry_fingerprint="a" * 64, trigger_spec_id="anchor-retrace-reclaim",
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
    assert engine.observe(event("quote", 1_175, 2, bid=100.09, ask=100.11)) is None
    assert engine.observe(event("quote", 1_200, 3, bid=99.96, ask=99.98)) is None
    ready = engine.observe(event("quote", 1_550, 4, bid=99.95, ask=99.97))

    assert ready is not None
    assert ready.entry_price == pytest.approx(99.95)
    assert ready.taker_buy_ratio == pytest.approx(4 / 14)


def test_trigger_rejects_market_evidence_from_mixed_stream_generations():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerPolicy
    from aletheia.trading.momentum_trigger import MomentumTriggerEngine

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = MomentumTriggerEngine(
        instrument=instrument,
        direction="long",
        breakout_level=100.0,
        sweep_band=(99.75, 100.25), invalidation_price=98, atr_abs=2,
        geometry_version="anchor-retrace-reclaim-geometry/v1",
        geometry_fingerprint="a" * 64, trigger_spec_id="anchor-retrace-reclaim",
        pullback_band=(99.75, 100.25),
        entry_zone=(100.0, 100.25),
        policy=TriggerPolicy(max_spread_bps=10, min_depth_10bps_usd=100),
    )
    engine.confirm_subscription()

    def event(kind, at, sequence, generation, **values):
        return FastMarketEvent(
            event_type=kind,
            instrument=instrument,
            exchange_timestamp_ms=at,
            received_timestamp_ms=at,
            sequence=sequence,
            stream_generation_id=generation,
            subscription_generation_id="subscription-a",
            **values,
        )

    engine.observe(event("quote", 1_000, 1, "stream-a", bid=99.98, ask=100.00))
    engine.observe(event("depth", 1_050, 1, "stream-b", bids=((99.99, 2.0),), asks=((100.01, 2.0),)))
    engine.observe(event("trade", 1_100, 1, "stream-a", price=100.0, size=10.0, side="buy"))
    engine.observe(event("quote", 1_175, 2, "stream-a", bid=99.89, ask=99.91))
    engine.observe(event("quote", 1_200, 3, "stream-a", bid=100.04, ask=100.06))

    assert engine.observe(event("quote", 1_550, 4, "stream-a", bid=100.05, ask=100.07)) is None
    assert engine.health_reason == "market_evidence_generation_mismatch"


def test_reclaim_side_alone_does_not_count_as_pullback():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerPolicy
    from aletheia.trading.momentum_trigger import MomentumTriggerEngine

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = MomentumTriggerEngine(
        instrument=instrument,
        direction="long",
        breakout_level=100.0,
        sweep_band=(99.75, 100.25), invalidation_price=98, atr_abs=2,
        geometry_version="anchor-retrace-reclaim-geometry/v1",
        geometry_fingerprint="a" * 64, trigger_spec_id="anchor-retrace-reclaim",
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


def test_entry_plan_expiry_reason_preserves_monitoring_and_trigger_cause():
    from types import SimpleNamespace

    from aletheia.agent_runtime.fast_trigger_watch import _entry_plan_expiry_reason

    assert _entry_plan_expiry_reason({"id": "plan", "status": "waiting", "monitoring_state": None}) == (
        "monitoring_never_ready"
    )
    assert _entry_plan_expiry_reason(
        {
            "id": "plan", "status": "waiting",
            "monitoring_state": "unavailable",
            "monitoring_evidence_json": json.dumps(
                {"channels": {"depth": {"state": "unavailable"}}}
            ),
        }
    ) == "monitoring_channel_unavailable:depth"
    assert _entry_plan_expiry_reason(
        {"id": "plan", "status": "waiting", "monitoring_state": "ready"},
        SimpleNamespace(state="reclaim", final_requirement_reason="order_flow"),
    ) == "trigger_final_requirements_unsatisfied:order_flow"


def test_entry_plan_expiry_identifies_unrecovered_order_generation(monkeypatch):
    from aletheia.agent_runtime import fast_trigger_watch as watch

    monkeypatch.setattr(
        watch.repo,
        "get_order_for_entry_plan",
        lambda _order_id: {
            "matching_stream_generation_id": "stream-a",
            "matching_subscription_generation_id": "subscription-a",
        },
    )
    row = {
        "id": "plan", "status": "active",
        "order_id": "order-1",
        "monitoring_evidence_json": json.dumps(
            {
                "state": "warming",
                "stream_generation_id": "stream-b",
                "subscription_generation_id": "subscription-b",
            }
        ),
    }

    assert watch._entry_plan_expiry_reason(row) == "expired_during_monitoring_gap"


def test_sequence_regression_disarms_trigger():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerPolicy
    from aletheia.trading.momentum_trigger import MomentumTriggerEngine

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine = MomentumTriggerEngine(
        instrument=instrument,
        direction="long",
        breakout_level=100.0,
        sweep_band=(99.75, 100.25), invalidation_price=98, atr_abs=2,
        geometry_version="anchor-retrace-reclaim-geometry/v1",
        geometry_fingerprint="a" * 64, trigger_spec_id="anchor-retrace-reclaim",
        pullback_band=(99.75, 100.25),
        entry_zone=(100.0, 100.25),
        policy=TriggerPolicy(max_spread_bps=10, min_depth_10bps_usd=100),
    )
    engine.confirm_subscription()
    first = FastMarketEvent(
        event_type="quote",
        instrument=instrument,
        exchange_timestamp_ms=1_000,
        received_timestamp_ms=1_000,
        sequence=2,
        bid=100.0,
        ask=100.01,
    )
    second = first.model_copy(update={"received_timestamp_ms": 1_100, "sequence": 1})
    engine.observe(first)
    engine.observe(second)
    assert engine.health_reason == "sequence_anomaly:quote"
    assert engine.evaluate(1_100) is None


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
        candles.append(
            {
                "ts": index * 60_000,
                "open": center,
                "high": center + 2.0 + offset,
                "low": center - 2.0 - offset,
                "close": center + 0.1,
                "volume": 10.0,
            }
        )
    result = evaluate_market_structure(candles, direction="long")

    assert result["algorithm_version"] == "market-structure/v2"
    assert result["core_algorithm_version"] == "causal-market-structure/v1"
    # A K=2 pivot is knowable only when the second right-side bar closes:
    # pivot open + two later bar opens + the confirming bar duration.
    assert result["last_confirmed_swing_high"]["confirmed_at"] == (
        result["last_confirmed_swing_high"]["pivot_at"] + 3 * 60_000
    )
    assert result["last_confirmed_swing_low"]["confirmed_at"] == (
        result["last_confirmed_swing_low"]["pivot_at"] + 3 * 60_000
    )
    assert hard_gate_decision(result)[0] is False
    assert hard_gate_decision(result)[1].startswith("price_action_gate_unqualified:")

    gapped = candles.copy()
    gapped[20] = {**gapped[20], "ts": gapped[20]["ts"] + 1}
    assert evaluate_market_structure(gapped, direction="long")["reason"] == "candle_gap"


def test_market_structure_v2_projects_only_final_bar_canonical_events():
    from aletheia.data.market_structure import derive_market_structure
    from aletheia.data.providers import Candle
    from aletheia.universe.market_structure import evaluate_market_structure

    candles = []
    for index in range(30):
        high = {3: 110.0, 8: 112.0, 13: 114.0, 23: 118.0}.get(index, 105.0)
        low = {5: 90.0, 10: 92.0, 15: 94.0}.get(index, 95.0)
        close = 120.0 if index == 29 else 100.0
        candles.append(
            {
                "ts": index * 60_000,
                "open": 100.0,
                "high": max(high, close),
                "low": min(low, close, 100.0),
                "close": close,
                "volume": 10.0,
            }
        )
    canonical = derive_market_structure(
        [
            Candle(
                timestamp=row["ts"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in candles
        ],
        tf_ms=60_000,
    )
    result = evaluate_market_structure(candles, direction="long")

    assert result["event"] == "bullish_bos"
    assert result["reason"] == "bullish_bos"
    assert result["verdict"] == "allow"
    assert result["input_fingerprint"] == canonical.geometry_fingerprint
    assert result["last_confirmed_swing_high"]["confirmed_at"] == 26 * 60_000

    # The consumed level cannot repeat on a later final bar.
    candles.append({**candles[-1], "ts": 30 * 60_000, "close": 121.0, "high": 122.0})
    repeated = evaluate_market_structure(candles, direction="long")
    assert repeated["event"] is None


def test_market_structure_v2_state_projection_choch_and_exact_staleness():
    from aletheia.universe.market_structure import (
        HARD_GATE_ALLOWLIST,
        evaluate_market_structure,
    )

    def fixture(*, count=30, highs=(110.0, 112.0), lows=(90.0, 92.0), latest_high_index=15, final_close=100.0):
        rows = [
            {
                "ts": index * 60_000,
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 100.0,
                "volume": 10.0,
            }
            for index in range(count)
        ]
        rows[5]["high"] = highs[0]
        rows[latest_high_index]["high"] = highs[1]
        rows[10]["low"] = lows[0]
        rows[20]["low"] = lows[1]
        rows[-1].update(
            {
                "close": final_close,
                "high": max(rows[-1]["high"], final_close),
                "low": min(rows[-1]["low"], final_close),
            }
        )
        return rows

    bullish = evaluate_market_structure(fixture(), direction="long")
    bearish = evaluate_market_structure(
        fixture(highs=(112.0, 110.0), lows=(92.0, 90.0)),
        direction="short",
    )
    ranged = evaluate_market_structure(
        fixture(highs=(110.0, 112.0), lows=(92.0, 90.0)),
        direction="long",
    )
    choch = evaluate_market_structure(fixture(final_close=90.0), direction="long")
    threshold = evaluate_market_structure(
        fixture(count=120, highs=(110.0, 114.0), latest_high_index=59),
        direction="long",
    )
    stale = evaluate_market_structure(
        fixture(count=120, highs=(110.0, 114.0), latest_high_index=58),
        direction="long",
    )

    assert HARD_GATE_ALLOWLIST == frozenset()
    assert (bullish["structure_state"], bullish["verdict"]) == ("higher_high_higher_low", "allow")
    assert (bearish["structure_state"], bearish["verdict"]) == ("lower_high_lower_low", "allow")
    assert (ranged["structure_state"], ranged["verdict"]) == ("range", "unavailable")
    assert (choch["event"], choch["verdict"]) == ("bearish_choch", "block")
    assert threshold["reason"] != "swing_stale"
    assert stale["reason"] == "swing_stale"


def test_selected_external_account_uses_public_fast_stream():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import validate_fast_trigger_stream
    from aletheia.universe.fast_stream import (
        fast_market_data_unavailability_reason,
        fast_market_data_venue_for_config,
    )

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "binance_testnet"
    cfg.market_data.ccxt_exchange = "binance"

    assert fast_market_data_venue_for_config(cfg) == "binance"
    assert validate_fast_trigger_stream(cfg) is None
    cfg.fast_trigger.enabled = False
    assert fast_market_data_unavailability_reason(cfg, "binance") == "FAST_TRIGGER_DISABLED"


def test_legacy_mode_off_does_not_disable_current_entry_plan_monitoring():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import (
        validate_fast_trigger_stream,
    )
    from aletheia.universe.fast_stream import fast_market_data_unavailability_reason

    cfg = AletheiaConfig()
    cfg.fast_trigger.enabled = True
    cfg.fast_trigger.mode = "off"
    cfg.exchange.paper_execution_mode = "binance_testnet"
    cfg.market_data.ccxt_exchange = "binance"

    assert validate_fast_trigger_stream(cfg) is None
    assert fast_market_data_unavailability_reason(cfg, "binance") is None


def test_fast_trigger_refuses_observe_permission():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.agent_runtime.fast_trigger_watch import _watch_config_identity
    from aletheia.universe.fast_trigger import validate_fast_trigger_stream

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "binance_testnet"
    cfg.agent.permission_mode = "Observe"

    assert validate_fast_trigger_stream(cfg) == "entry_plan_permission_revoked"
    observe_identity = _watch_config_identity(cfg)
    cfg.agent.permission_mode = "Paper"
    assert _watch_config_identity(cfg) != observe_identity
    paper_identity = _watch_config_identity(cfg)
    cfg.market_data.ccxt_exchange = "bitget"
    assert _watch_config_identity(cfg) != paper_identity


@pytest.mark.asyncio
async def test_blocked_start_cancels_open_entry_plans(monkeypatch):
    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.agent.permission_mode = "Observe"
    cancellations = []

    async def cancel(reason):
        cancellations.append(reason)

    monkeypatch.setattr(watch, "_lifecycle_ready", True)
    monkeypatch.setattr(watch, "_watch_task", None)
    monkeypatch.setattr(watch, "_stop_event", None)
    monkeypatch.setattr(watch, "_cancel_open_entry_plans", cancel)

    status = await watch.reconfigure_fast_trigger_watch(cfg, asyncio.get_running_loop())

    assert status["task_started"] is False
    assert status["reason"] == "entry_plan_permission_revoked"
    assert cancellations == ["entry_plan_permission_revoked"]


def test_external_execution_mode_keeps_execution_venue_authority():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_stream import fast_market_data_venue_for_config

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.market_data.ccxt_exchange = "binance"

    assert fast_market_data_venue_for_config(cfg) == "bitget"
    cfg.exchange.paper_execution_mode = "paper_internal"
    assert fast_market_data_venue_for_config(cfg) == "unknown"


def test_bitget_candidate_enters_planning_while_unsupported_candidate_stays_visible(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember, UniverseSnapshot
    from aletheia.universe.fast_stream import fast_market_data_unavailability_reason
    from aletheia.universe.service import _ensure_planning_generations
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1

    now = datetime.now(timezone.utc)
    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.market_data.ccxt_exchange = "bitget"
    snapshot = UniverseSnapshot(
        id="bitget-planning-snapshot",
        exchange="bitget",
        market_type="spot",
        status="active",
        expires_at=(now + timedelta(hours=1)).isoformat(),
        universe_size=2,
    )
    repo._insert("universe_snapshots", snapshot.model_dump())
    opportunity = CandidateOpportunityContextV1(
        candidate_id="bitget-planning-candidate",
        candidate_selection={
            "policy": "candidate_selection_utility_v1",
            "policy_epoch": "epoch-1",
            "source_record_hash": "a" * 64,
        },
        source={
            "detected_at": now.isoformat(),
            "source_cutoff_at": now.isoformat(),
            "feature_snapshot_id": "feature-1",
        },
        opportunity={
            "primary_mechanism": "continuation",
            "mechanism_version": "continuation/v1",
            "mechanism_source": "target_authority",
            "source_component_id": "momentum",
            "direction": "long",
            "horizon": "4h",
            "economic_utility_pct": 1.0,
            "capability_status": "supported",
            "capability_gaps": (),
            "primary_evidence_refs": (),
            "secondary_mechanisms": (),
        },
        context={"regime": "trending"},
    )
    bitget = UniverseMember(
        id="bitget-planning-candidate",
        snapshot_id=snapshot.id,
        asset_id="bitget-asset",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="spot",
        rank=1,
        total_score=90,
        scanner_family="momentum",
        scanner_direction="long",
        execution_eligible=fast_market_data_unavailability_reason(cfg, "bitget") is None,
        candidate_selection_policy="candidate_selection_utility_v1",
        candidate_selection_policy_epoch="epoch-1",
        candidate_selection_utility_pct=1.0,
        opportunity_mechanism="continuation",
        opportunity_mechanism_version="continuation/v1",
        opportunity_source_component="momentum",
        opportunity_source_cutoff_at=now.isoformat(),
        opportunity_evidence_hash="b" * 64,
        candidate_opportunity_context_version=opportunity.version,
        candidate_opportunity_context_json=opportunity.model_dump_json(),
        candidate_opportunity_context_hash=opportunity.context_hash,
        expires_at=snapshot.expires_at,
    )
    unsupported = UniverseMember(
        id="unsupported-visible-candidate",
        snapshot_id=snapshot.id,
        asset_id="unsupported-asset",
        venue="okx",
        symbol="ETH/USDT",
        market_type="spot",
        rank=2,
        total_score=80,
        scanner_family="momentum",
        scanner_direction="long",
        execution_eligible=fast_market_data_unavailability_reason(cfg, "okx") is None,
        risk_flags='["fast_market_data_venue_unavailable"]',
        expires_at=snapshot.expires_at,
    )
    repo._insert("universe_members", bitget.model_dump())
    repo._insert("universe_members", unsupported.model_dump())

    selected, _created, count = _ensure_planning_generations(
        [bitget, unsupported],
        soft_lease_sec=300,
        max_attention_age_sec=300,
        now=now,
    )

    assert count == 1 and len(selected) == 1
    assert repo.get_candidate_planning_generation(next(iter(selected)))["last_candidate_id"] == bitget.id
    assert (
        repo.get_db()
        .execute(
            "SELECT COUNT(*) FROM candidate_planning_generations WHERE last_candidate_id=?",
            (unsupported.id,),
        )
        .fetchone()[0]
        == 0
    )
    assert repo.get_universe_member(unsupported.id)["risk_flags"] == '["fast_market_data_venue_unavailable"]'


@pytest.mark.asyncio
async def test_superseded_resting_plan_cancels_only_through_gateway(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.execution import gateway

    payload = "{}"
    row = {
        "id": "plan-1",
        "status": "ordered",
        "plan_json": payload,
        "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
    }
    calls = []
    monkeypatch.setattr(watch.repo, "get_entry_plan", lambda _: row)
    monkeypatch.setattr(watch.repo, "get_order_for_entry_plan", lambda _: {"id": "owned-order"})
    monkeypatch.setattr(watch.repo, "transition_entry_plan", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(
        watch.repo,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 0},
    )
    monkeypatch.setattr(
        watch.EntryPlan,
        "model_validate_json",
        lambda _payload: SimpleNamespace(plan_id="plan-1", planning_generation_id="generation-1"),
    )
    monkeypatch.setattr(
        gateway, "cancel_entry_plan_order",
        lambda plan_id, *, reason: calls.append((plan_id, reason)) or {"status": "cancelled"},
    )

    plans, engines = await watch._load_entry_plan_watchers(SimpleNamespace(fast_trigger=SimpleNamespace(max_setups=10)))

    assert plans == {}
    assert engines == {}
    assert calls == [("plan-1", "incompatible_planning_generation")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "integrity", "expected_action"),
    [
        ("ordered", "hash", "cancel"),
        ("partially_filled", "hash", "cancel"),
        ("ordered", "contract", "cancel"),
        ("partially_filled", "contract", "cancel"),
        ("waiting", "hash", "fail"),
    ],
)
async def test_integrity_failure_preserves_order_lifecycle(monkeypatch, status, integrity, expected_action):
    from types import SimpleNamespace

    from aletheia.agent_runtime import fast_trigger_watch as watch

    payload = "{}"
    row = {
        "id": f"{status}-{integrity}-plan",
        "status": status,
        "plan_json": payload,
        "plan_hash": (hashlib.sha256(payload.encode()).hexdigest() if integrity == "contract" else "0" * 64),
        "order_id": "order-1" if status in {"ordered", "partially_filled"} else None,
    }
    transitions = []
    cancellations = []
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(watch.repo, "get_entry_plan", lambda _plan_id: row)
    monkeypatch.setattr(watch.repo, "get_trade_proposal_by_entry_plan", lambda _plan_id: None)
    monkeypatch.setattr(watch.repo, "get_order_for_entry_plan",
        lambda _: {"id": "order-1"} if expected_action == "cancel" else None)
    if integrity == "contract":
        monkeypatch.setattr(
            watch.EntryPlan,
            "model_validate_json",
            lambda _payload: (_ for _ in ()).throw(ValueError("invalid")),
        )
    monkeypatch.setattr(
        watch.repo,
        "transition_entry_plan",
        lambda plan_id, **kwargs: transitions.append((plan_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway.cancel_entry_plan_order",
        lambda plan_id, *, reason: cancellations.append((plan_id, reason)) or {"status": "cancelled"},
    )

    await watch._load_entry_plan_watchers(SimpleNamespace(fast_trigger=SimpleNamespace(max_setups=10)))

    if expected_action == "cancel":
        assert len(transitions) == 1 and transitions[0][1]["status"] == "failed"
        assert cancellations == [
            (row["id"], f"entry_plan_{integrity}_mismatch" if integrity == "hash" else "entry_plan_contract_invalid")
        ]
    else:
        assert cancellations == []
        assert len(transitions) == 1
        plan_id, kwargs = transitions[0]
        assert plan_id == row["id"]
        assert kwargs["expected_status"] == status
        assert kwargs["status"] == "failed"
        assert kwargs["reason"] == "entry_plan_hash_mismatch"


@pytest.mark.asyncio
async def test_triggered_post_only_plan_enforces_maker_timeout(monkeypatch):
    from types import SimpleNamespace
    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.trading.entry_plan import entry_plan_payload
    from tests.test_entry_execution_projection import plan_for, facts

    plan = plan_for(monkeypatch, conditional=True).model_copy(update={
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    payload, plan_hash = entry_plan_payload(plan)
    row = {"id": plan.plan_id, "status": "active", "plan_json": payload, "plan_hash": plan_hash}
    cancellations = []
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(watch.repo, "get_candidate_planning_generation", lambda _id: {"is_current": 1})
    monkeypatch.setattr("aletheia.execution.entry_plan_execution.require_entry_plan_runtime_authority", lambda _plan: None)
    def no_market_facts(_plan, *, bid, ask, now):
        assert bid is None and ask is None
        return facts(order_status="submitted", order_tactic="POST_ONLY_LIMIT", order_age_sec=61,
                     passive_eligible=False, ioc_eligible=False)
    monkeypatch.setattr("aletheia.execution.entry_plan_execution._capture_facts", no_market_facts)
    monkeypatch.setattr("aletheia.risk.policy.resolve_risk_policy", lambda _cfg, _mode: SimpleNamespace(
        policy=SimpleNamespace(policy_id=plan.risk_policy_id, revision=plan.risk_policy_revision),
        policy_hash=plan.risk_policy_hash))
    monkeypatch.setattr("aletheia.execution.gateway.cancel_entry_plan_order",
        lambda plan_id, *, reason: cancellations.append((plan_id, reason)) or {"status": "cancelled"})
    cfg = SimpleNamespace(fast_trigger=SimpleNamespace(max_setups=10, mode="shadow"),
                          exchange=SimpleNamespace(paper_execution_mode="bitget_demo"))
    plans, engines = await watch._load_entry_plan_watchers(cfg)
    assert plans == {plan.plan_id: plan}
    assert engines == {}
    assert cancellations == [(plan.plan_id, "passive_window_elapsed")]


@pytest.mark.asyncio
async def test_pause_invalidates_waiting_plan_and_gateway_cancels_order(monkeypatch):
    from aletheia.agent_runtime import fast_trigger_watch as watch

    transitions = []
    cancellations = []
    rows = [
        {"id": "waiting-plan", "status": "waiting", "created_at": "2026-08-31T00:00:00+00:00"},
        {"id": "ordered-plan", "status": "ordered", "created_at": "2026-08-31T00:00:01+00:00"},
    ]
    monkeypatch.setattr(
        watch.repo,
        "watchable_entry_plan_high_watermark",
        lambda: (rows[-1]["created_at"], rows[-1]["id"]),
    )
    monkeypatch.setattr(
        watch.repo,
        "list_watchable_entry_plans_page",
        lambda *, after, through, limit: rows if after is None else [],
    )
    monkeypatch.setattr(
        watch.repo,
        "get_entry_plan",
        lambda plan_id: next(row for row in rows if row["id"] == plan_id),
    )
    monkeypatch.setattr(watch.repo, "get_order_for_entry_plan",
        lambda plan_id: {"id": "order-1"} if plan_id == "ordered-plan" else None)
    monkeypatch.setattr(watch.repo, "get_trade_proposal_by_entry_plan", lambda _plan_id: None)
    monkeypatch.setattr(
        watch.repo,
        "transition_entry_plan",
        lambda plan_id, **kwargs: transitions.append((plan_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway.cancel_entry_plan_order",
        lambda plan_id, *, reason: cancellations.append((plan_id, reason)) or {"status": "cancelled"},
    )

    await watch._cancel_open_entry_plans("fast_trigger_paused")

    assert len(transitions) == 2
    assert transitions[1][0] == "ordered-plan" and transitions[1][1]["status"] == "invalidated"
    plan_id, kwargs = transitions[0]
    assert plan_id == "waiting-plan"
    assert kwargs["expected_status"] == "waiting"
    assert kwargs["status"] == "invalidated"
    assert kwargs["reason"] == "fast_trigger_paused"
    assert cancellations == [("ordered-plan", "fast_trigger_paused")]


@pytest.mark.asyncio
async def test_blocked_cleanup_pages_past_unresolved_prefix(monkeypatch):
    from aletheia.agent_runtime import fast_trigger_watch as watch

    rows = [
        {
            "id": f"plan-{index:03d}",
            "status": "ordered" if index < 100 else "waiting",
            "created_at": "2026-08-31T00:00:00+00:00",
        }
        for index in range(101)
    ]
    attempts = []

    def page(*, after, through, limit):
        remaining = [row for row in rows if after is None or (row["created_at"], row["id"]) > after]
        return remaining[:limit]

    async def terminalize(row, **_kwargs):
        attempts.append(row["id"])
        return "cancel_pending" if row["status"] == "ordered" else "invalidated"

    monkeypatch.setattr(
        watch.repo,
        "watchable_entry_plan_high_watermark",
        lambda: (rows[-1]["created_at"], rows[-1]["id"]),
    )
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans_page", page)
    monkeypatch.setattr(watch, "_terminalize_entry_plan", terminalize)

    cleanup = await watch._cancel_open_entry_plans("fast_trigger_kill_switch_active")

    assert attempts == [row["id"] for row in rows]
    assert cleanup == {
        "checked": 101,
        "terminalized": 1,
        "unresolved": 100,
        "remaining_unattempted": 0,
    }


@pytest.mark.asyncio
async def test_blocked_cleanup_keeps_stale_cas_unresolved(monkeypatch):
    from aletheia.agent_runtime import fast_trigger_watch as watch

    row = {
        "id": "concurrently-advanced-plan",
        "status": "waiting",
        "created_at": "2026-09-01T00:00:00+00:00",
    }
    monkeypatch.setattr(
        watch.repo,
        "watchable_entry_plan_high_watermark",
        lambda: (row["created_at"], row["id"]),
    )
    monkeypatch.setattr(
        watch.repo,
        "list_watchable_entry_plans_page",
        lambda *, after, through, limit: [row] if after is None else [],
    )

    async def terminalize(*_args, **_kwargs):
        return "stale"

    monkeypatch.setattr(watch, "_terminalize_entry_plan", terminalize)

    cleanup = await watch._cancel_open_entry_plans("fast_trigger_kill_switch_active")

    assert cleanup["terminalized"] == 0
    assert cleanup["unresolved"] == 1


@pytest.mark.asyncio
async def test_watch_loop_reloads_entry_plans_while_stream_is_busy(monkeypatch):
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
    observed = []

    class Engine:
        def __init__(self, instrument, generation):
            self.instrument = instrument
            self.generation = generation
            self.state = "armed"

        def confirm_subscription(self):
            pass

        def observe(self, event):
            observed.append((self.generation, event.sequence))
            return None

    class BusyStream:
        def __init__(self):
            self.subscriptions = []

        async def connect(self, _instruments=None):
            pass

        async def set_instruments(self, instruments):
            self.subscriptions.append(frozenset(instruments))
            if second in instruments:
                watch._stop_event.set()

        def health(self):
            return FastStreamHealth(connected=True, subscriptions_confirmed=True)

        def monitoring_state(self, _instrument, _channels):
            return {"state": "ready"}

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
        engines = {"first": Engine(first, load_count)}
        if load_count > 1:
            engines["second"] = Engine(second, load_count)
        from types import SimpleNamespace
        return {
            key: SimpleNamespace(
                venue=engine.instrument.exchange, symbol=engine.instrument.symbol,
                market_type=engine.instrument.market_type, order_tactic="MARKET",
                trigger_data_requirements=("quote",), execution_mode="TRIGGERED",
            ) for key, engine in engines.items()
        }, engines

    monkeypatch.setattr(watch, "_WATCHER_RELOAD_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(watch, "_stop_event", asyncio.Event())
    monkeypatch.setattr(watch, "_configured_block_reason", lambda _cfg: None)
    monkeypatch.setattr(watch, "_load_entry_plan_watchers", load_watchers)
    monkeypatch.setattr(watch.repo, "record_entry_plan_monitoring", lambda *args: None)
    monkeypatch.setattr(watch.repo, "get_entry_plan", lambda *args: None)
    monkeypatch.setattr(watch, "fast_market_stream_for_config", lambda _cfg: stream)
    monkeypatch.setattr(loader, "load_config", lambda persist_migrations=False: cfg)

    await asyncio.wait_for(watch._watch_loop(cfg), timeout=1.0)

    assert frozenset({first, second}) in stream.subscriptions
    assert load_count >= 2
    assert all(generation == 1 for generation, _sequence in observed)


@pytest.mark.asyncio
async def test_resting_maker_cross_rejection_does_not_restart_stream(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent, FastStreamHealth

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "shadow"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    plan = SimpleNamespace(
        plan_id="resting-maker",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        invalidation_price=90.0,
        execution_mode="RESTING_MAKER",
        activation_policy="IMMEDIATE",
        order_tactic="POST_ONLY_LIMIT",
        limit_price=100.2,
    )
    execute_calls = 0

    class Stream:
        connect_calls = 0

        async def connect(self, _instruments=None):
            self.connect_calls += 1

        async def set_instruments(self, _instruments):
            pass

        def health(self):
            return FastStreamHealth(connected=True, subscriptions_confirmed=True)

        def monitoring_state(self, _instrument, _channels):
            return {"state": "ready"}

        async def close(self):
            pass

        async def _events(self):
            for sequence in (1, 2):
                yield FastMarketEvent(
                    event_type="quote",
                    instrument=instrument,
                    exchange_timestamp_ms=sequence,
                    received_timestamp_ms=sequence,
                    sequence=sequence,
                    bid=100.0,
                    ask=100.1,
                )
            watch._stop_event.set()

        def events(self):
            return self._events()

    stream = Stream()

    def reject_cross(*_args, **_kwargs):
        nonlocal execute_calls
        execute_calls += 1
        raise ValueError("post_only_would_cross_bbo")

    monkeypatch.setattr(watch, "_stop_event", asyncio.Event())
    monkeypatch.setattr(watch, "_configured_block_reason", lambda _cfg: None)
    monkeypatch.setattr(
        watch,
        "_load_entry_plan_watchers",
        lambda _cfg: asyncio.sleep(0, result=({plan.plan_id: plan}, {})),
    )
    monkeypatch.setattr(watch.repo, "get_entry_plan", lambda _id: {"status": "active"})
    monkeypatch.setattr(watch, "fast_market_stream_for_config", lambda _cfg: stream)
    monkeypatch.setattr(loader, "load_config", lambda persist_migrations=False: cfg)
    monkeypatch.setattr("aletheia.execution.entry_plan_execution.execute_entry_plan", reject_cross)

    await asyncio.wait_for(watch._watch_loop(cfg), timeout=1.0)

    assert execute_calls == 2
    assert stream.connect_calls == 1


def test_binance_kline_updates_keep_one_open_candle_until_next_bar():
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent
    from aletheia.universe.fast_trigger import TriggerPolicy
    from aletheia.trading.momentum_trigger import MomentumTriggerEngine

    instrument = InstrumentId.from_parts("binance", "BTC/USDT", "perp")
    engine = MomentumTriggerEngine(
        instrument=instrument,
        direction="long",
        breakout_level=100.0,
        sweep_band=(99.75, 100.25), invalidation_price=98, atr_abs=2,
        geometry_version="anchor-retrace-reclaim-geometry/v1",
        geometry_fingerprint="a" * 64, trigger_spec_id="anchor-retrace-reclaim",
        pullback_band=(99.5, 100.5),
        entry_zone=(100.0, 100.5),
        policy=TriggerPolicy(max_spread_bps=10, min_depth_10bps_usd=100),
    )

    def candle(event_time, bar_open, close, sequence):
        if not bar_open:
            return None
        return FastMarketEvent(
            event_type="candle",
            instrument=instrument,
            exchange_timestamp_ms=bar_open,  # bar open time is the canonical candle timestamp
            received_timestamp_ms=event_time,
            sequence=sequence,
            candle=(1.0, 3.0, 1.0, float(close), 4.0),
        )

    first = candle(61_000, 60_000, 2, 1)
    second = candle(61_500, 60_000, 2.5, 2)
    assert first is not None and second is not None
    engine.observe(first)
    engine.observe(second)
    assert list(engine._closed_candles_1m) == []
    assert engine._current_candle_1m["close"] == 2.5

    third = candle(120_500, 120_000, 2.75, 3)
    assert third is not None
    engine.observe(third)
    assert [(row["ts"], row["close"]) for row in engine._closed_candles_1m] == [
        (60_000, 2.5)
    ]
    assert candle(121_000, 0, 2.75, 4) is None


@pytest.mark.parametrize("mode", ["okx_demo", "bitget_live_limited"])
def test_execute_mode_refuses_unavailable_or_live_stream(mode):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import validate_fast_trigger_stream

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "execute"
    cfg.exchange.paper_execution_mode = mode
    reason = validate_fast_trigger_stream(cfg)
    expected = (
        "fast_trigger_live_forbidden" if "live" in mode else f"fast_trigger_stream_unavailable:{mode.split('_')[0]}"
    )
    assert reason == expected


def test_binance_testnet_execution_uses_public_binance_stream():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.fast_trigger import validate_fast_trigger_stream

    cfg = AletheiaConfig()
    cfg.fast_trigger.mode = "execute"
    cfg.exchange.paper_execution_mode = "binance_testnet"

    assert validate_fast_trigger_stream(cfg) is None


@pytest.mark.asyncio
async def test_venue_mismatch_waiting_plan_invalidates_explicitly(monkeypatch):
    """A waiting plan whose venue differs from the configured monitoring venue must be
    transitioned to 'invalidated', not silently skipped."""
    from types import SimpleNamespace
    from aletheia.agent_runtime import fast_trigger_watch as watch

    payload = "{}"
    row = {
        "id": "mismatch-waiting",
        "status": "waiting",
        "plan_json": payload,
        "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
        "order_id": None,
    }
    plan = SimpleNamespace(
        plan_id="mismatch-waiting",
        planning_generation_id="gen-1",
        risk_policy_id="p1",
        risk_policy_revision=1,
        risk_policy_hash="ph",
        execution_account_mode="bitget_demo",
        execution_mode="TRIGGERED",
        order_tactic="POST_ONLY_LIMIT",
        max_wait_sec=60,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        venue="okx",
        symbol="ETH/USDT",
        market_type="perp",
        direction="long",
        trigger_params={},
    )
    transitions = []
    cancellations = []
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(watch.repo, "get_entry_plan", lambda _plan_id: row)
    monkeypatch.setattr(watch.repo, "get_trade_proposal_by_entry_plan", lambda _plan_id: None)
    monkeypatch.setattr(watch.repo, "get_candidate_planning_generation", lambda _id: {"is_current": 1})
    monkeypatch.setattr(watch.EntryPlan, "model_validate_json", lambda _payload: plan)
    monkeypatch.setattr(
        "aletheia.risk.policy.resolve_risk_policy",
        lambda _cfg, _mode: SimpleNamespace(
            policy=SimpleNamespace(policy_id="p1", revision=1),
            policy_hash="ph",
        ),
    )
    monkeypatch.setattr(
        watch.repo,
        "transition_entry_plan",
        lambda plan_id, **kwargs: transitions.append((plan_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway.cancel_entry_plan_order",
        lambda plan_id, *, reason: cancellations.append((plan_id, reason)) or {"status": "cancelled"},
    )
    cfg = SimpleNamespace(
        fast_trigger=SimpleNamespace(max_setups=10, mode="shadow"),
        exchange=SimpleNamespace(paper_execution_mode="bitget_demo"),
    )
    plans, engines = await watch._load_entry_plan_watchers(cfg)

    assert plans == {}
    assert cancellations == []
    assert any(
        plan_id == "mismatch-waiting" and kwargs.get("status") == "invalidated" for plan_id, kwargs in transitions
    ), f"Expected invalidated transition; got {transitions}"


@pytest.mark.asyncio
async def test_venue_mismatch_ordered_plan_routes_through_gateway(monkeypatch):
    """An ordered plan whose venue differs from the configured monitoring venue must
    go through Gateway cancel, never be silently dropped."""
    from types import SimpleNamespace
    from aletheia.agent_runtime import fast_trigger_watch as watch

    payload = "{}"
    row = {
        "id": "mismatch-ordered",
        "status": "ordered",
        "plan_json": payload,
        "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
        "order_id": "ord-1",
    }
    plan = SimpleNamespace(
        plan_id="mismatch-ordered",
        planning_generation_id="gen-2",
        risk_policy_id="p1",
        risk_policy_revision=1,
        risk_policy_hash="ph",
        execution_account_mode="bitget_demo",
        execution_mode="RESTING_MAKER",
        order_tactic="POST_ONLY_LIMIT",
        max_wait_sec=0,  # no timeout so maker branch doesn't fire
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        venue="okx",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        trigger_params={},
    )
    cancellations = []
    monkeypatch.setattr(watch.repo, "get_entry_plan", lambda _: row)
    monkeypatch.setattr(watch.repo, "get_order_for_entry_plan", lambda _: {"id": "owned-order"})
    monkeypatch.setattr(watch.repo, "transition_entry_plan", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(watch.repo, "get_candidate_planning_generation", lambda _id: {"is_current": 1})
    monkeypatch.setattr(watch.EntryPlan, "model_validate_json", lambda _payload: plan)
    monkeypatch.setattr(
        "aletheia.risk.policy.resolve_risk_policy",
        lambda _cfg, _mode: SimpleNamespace(
            policy=SimpleNamespace(policy_id="p1", revision=1),
            policy_hash="ph",
        ),
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway.cancel_entry_plan_order",
        lambda plan_id, *, reason: cancellations.append((plan_id, reason)) or {"status": "cancelled"},
    )
    cfg = SimpleNamespace(
        fast_trigger=SimpleNamespace(max_setups=10, mode="shadow"),
        exchange=SimpleNamespace(paper_execution_mode="bitget_demo"),
    )
    plans, engines = await watch._load_entry_plan_watchers(cfg)

    assert plans == {}
    assert any(
        plan_id == "mismatch-ordered" and reason == "entry_plan_execution_venue_unavailable"
        for plan_id, reason in cancellations
    ), f"Expected Gateway cancellation; got {cancellations}"


def test_fast_trigger_status_exposes_watched_plan_ids():
    """fast_trigger_watch_status() must include watched_entry_plan_ids."""
    from aletheia.agent_runtime.fast_trigger_watch import fast_trigger_watch_status

    status = fast_trigger_watch_status()
    assert "watched_entry_plan_ids" in status
    assert isinstance(status["watched_entry_plan_ids"], list)


@pytest.mark.asyncio
async def test_resting_maker_timeout_uses_orders_created_at_not_plan_age(monkeypatch):
    """Maker timeout fires from orders.created_at, not plan creation time.
    A newly submitted order (created just now) must NOT be cancelled even if the plan
    was created long ago."""
    from types import SimpleNamespace
    from aletheia.agent_runtime import fast_trigger_watch as watch

    payload = "{}"
    row = {
        "id": "fresh-order-plan",
        "status": "ordered",
        "plan_json": payload,
        "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
        "order_id": "order-fresh",
    }
    plan = SimpleNamespace(
        plan_id="fresh-order-plan",
        planning_generation_id="gen-3",
        risk_policy_id="p1",
        risk_policy_revision=1,
        risk_policy_hash="ph",
        execution_account_mode="bitget_demo",
        execution_mode="RESTING_MAKER",
        order_tactic="POST_ONLY_LIMIT",
        max_wait_sec=60,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        trigger_params={},
    )
    cancellations = []
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(watch.repo, "get_candidate_planning_generation", lambda _id: {"is_current": 1})
    monkeypatch.setattr(watch.EntryPlan, "model_validate_json", lambda _payload: plan)
    # Order created 5 seconds ago — well within 60-second window
    monkeypatch.setattr(
        watch.repo,
        "get_order",
        lambda _id: {"created_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()},
    )
    monkeypatch.setattr(
        "aletheia.risk.policy.resolve_risk_policy",
        lambda _cfg, _mode: SimpleNamespace(
            policy=SimpleNamespace(policy_id="p1", revision=1),
            policy_hash="ph",
        ),
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway.cancel_entry_plan_order",
        lambda plan_id, *, reason: cancellations.append((plan_id, reason)) or {"status": "cancelled"},
    )
    cfg = SimpleNamespace(
        fast_trigger=SimpleNamespace(max_setups=10, mode="shadow"),
        exchange=SimpleNamespace(paper_execution_mode="bitget_demo"),
    )
    plans, engines = await watch._load_entry_plan_watchers(cfg)

    # Fresh order: no timeout cancellation; plan is loaded
    assert cancellations == []
    assert "fresh-order-plan" in plans


@pytest.mark.asyncio
async def test_maker_timeout_does_not_drop_later_plans_at_legacy_cap(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agent_runtime import fast_trigger_watch as watch

    rows = []
    plans = {}
    for index in range(12):
        payload = f"plan-{index}"
        plan_id = payload
        ordered = index >= 10
        rows.append(
            {
                "id": plan_id,
                "status": "active",
                "plan_json": payload,
                "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
                "order_id": f"order-{index}" if ordered else None,
            }
        )
        plans[payload] = SimpleNamespace(
            plan_id=plan_id,
            planning_generation_id=f"generation-{index}",
            risk_policy_id="p1",
            risk_policy_revision=1,
            risk_policy_hash="ph",
            execution_account_mode="bitget_demo",
            execution_mode="RESTING_MAKER",
            order_tactic="POST_ONLY_LIMIT",
            max_wait_sec=60,
            capture_policy_id="maker-preferred/v1", capture_priority="MAKER_PREFERRED",
            activation_policy="IMMEDIATE", target_quantity=1.,
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            venue="bitget",
            symbol=f"ASSET{index}/USDT",
            market_type="perp",
            direction="long",
        )

    requested_limits = []

    def list_plans(limit=100):
        requested_limits.append(limit)
        return rows if limit is None else rows[:limit]

    cancellations = []
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", list_plans)
    monkeypatch.setattr(watch.repo, "get_candidate_planning_generation", lambda _id: {"is_current": 1})
    monkeypatch.setattr(watch.EntryPlan, "model_validate_json", lambda payload: plans[payload])
    from tests.test_entry_execution_projection import facts
    monkeypatch.setattr("aletheia.execution.entry_plan_execution._capture_facts",
        lambda plan, **kwargs: facts(order_status="submitted" if plan.plan_id in {"plan-10", "plan-11"} else None,
            order_tactic="POST_ONLY_LIMIT", order_age_sec=120 if plan.plan_id == "plan-10" else 5,
            passive_eligible=False, ioc_eligible=False))
    monkeypatch.setattr(
        "aletheia.risk.policy.resolve_risk_policy",
        lambda _cfg, _mode: SimpleNamespace(
            policy=SimpleNamespace(policy_id="p1", revision=1),
            policy_hash="ph",
        ),
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway.cancel_entry_plan_order",
        lambda plan_id, *, reason: cancellations.append((plan_id, reason)) or {"status": "cancelled"},
    )
    cfg = SimpleNamespace(
        fast_trigger=SimpleNamespace(max_setups=10, mode="shadow"),
        exchange=SimpleNamespace(paper_execution_mode="bitget_demo"),
    )

    watched, _ = await watch._load_entry_plan_watchers(cfg)

    assert requested_limits == [None]
    assert len(watched) == 12
    assert cancellations == [
        ("plan-10", "passive_window_elapsed"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_id", ["anchor-retrace-reclaim", "momentum-retrace-reclaim", "momentum-retrace-reclaim-geometry", None])
async def test_triggered_maker_waiting_does_not_start_maker_timeout(monkeypatch, spec_id):
    """A TRIGGERED+POST_ONLY_LIMIT plan still in 'waiting' state must NOT be
    subject to maker timeout — the order hasn't been placed yet."""
    from types import SimpleNamespace
    from aletheia.agent_runtime import fast_trigger_watch as watch

    payload = "{}"
    row = {
        "id": "triggered-waiting",
        "status": "waiting",
        "plan_json": payload,
        "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
        "order_id": None,
    }
    plan = SimpleNamespace(
        plan_id="triggered-waiting",
        planning_generation_id="gen-4",
        risk_policy_id="p1",
        risk_policy_revision=1,
        risk_policy_hash="ph",
        execution_account_mode="bitget_demo",
        execution_mode="TRIGGERED",
        order_tactic="POST_ONLY_LIMIT",
        max_wait_sec=60,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        venue="bitget",
        symbol="ETH/USDT",
        market_type="perp",
        direction="long",
        trigger_spec_id=spec_id,
        trigger_params={
            "sweep_low": 2900, "sweep_high": 2950, "invalidation_price": 2800,
            "atr_abs": 50, "geometry_version": "anchor-retrace-reclaim-geometry/v1",
            "geometry_fingerprint": "a" * 64,
            "breakout_level": "3000.0",
            "pullback_low": "2900.0",
            "pullback_high": "2950.0",
            "entry_low": "2920.0",
            "entry_high": "2940.0",
        },
    )
    cancellations = []
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(watch.repo, "get_candidate_planning_generation", lambda _id: {"is_current": 1})
    monkeypatch.setattr(watch.EntryPlan, "model_validate_json", lambda _payload: plan)
    monkeypatch.setattr(
        "aletheia.risk.policy.resolve_risk_policy",
        lambda _cfg, _mode: SimpleNamespace(
            policy=SimpleNamespace(policy_id="p1", revision=1),
            policy_hash="ph",
        ),
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway.cancel_entry_plan_order",
        lambda plan_id, *, reason: cancellations.append((plan_id, reason)) or {"status": "cancelled"},
    )

    async def no_prewarm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(watch, "_prewarm_market_structure", no_prewarm)
    cfg = SimpleNamespace(
        fast_trigger=SimpleNamespace(max_setups=10, mode="shadow"),
        exchange=SimpleNamespace(paper_execution_mode="bitget_demo"),
        universe=SimpleNamespace(max_spread_bps=50, min_depth_10bps_usd=10000),
    )
    plans, engines = await watch._load_entry_plan_watchers(cfg)

    assert cancellations == []
    assert "triggered-waiting" in plans
    assert ("triggered-waiting" in engines) is (spec_id == "anchor-retrace-reclaim")


def test_triggered_maker_stays_subscribed_after_trigger():
    from types import SimpleNamespace

    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.data.symbols import InstrumentId

    plan = SimpleNamespace(
        order_tactic="POST_ONLY_LIMIT",
        venue="bitget",
        symbol="ETH/USDT",
        market_type="perp",
    )
    instruments = watch._watched_instruments({}, {"triggered-maker": plan})
    assert instruments == {InstrumentId.from_parts("bitget", "ETH/USDT", "perp")}


@pytest.mark.asyncio
async def test_reconfigure_unavailable_status_has_watched_plan_ids(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agent_runtime import fast_trigger_watch as watch

    monkeypatch.setattr(watch, "_lifecycle_ready", False)
    cfg = SimpleNamespace(fast_trigger=SimpleNamespace(mode="shadow"))
    status = await watch.reconfigure_fast_trigger_watch(
        cfg,
        asyncio.get_running_loop(),
    )
    assert status["watched_entry_plan_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["invalidated", "active_claim"])
async def test_entry_plan_watcher_retires_historical_plan_without_starting_engine(monkeypatch, outcome):
    from types import SimpleNamespace
    from aletheia.agent_runtime import fast_trigger_watch as watch

    payload = "{}"
    row = {"id": "historical-plan", "status": "waiting", "plan_json": payload,
           "plan_hash": hashlib.sha256(payload.encode()).hexdigest(), "order_id": None}
    plan = SimpleNamespace(plan_id=row["id"], planning_generation_id="generation",
                           _historical_entry_metadata={"version": "entry-plan/v1"})
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(watch.repo, "get_candidate_planning_generation", lambda _id: {"is_current": 1})
    monkeypatch.setattr(watch, "decode_entry_plan", lambda _payload: plan)
    retired = []

    async def retire(row, **kwargs):
        retired.append((row["id"], kwargs["reason"]))
        return outcome

    monkeypatch.setattr(watch, "_terminalize_entry_plan", retire)
    plans, engines = await watch._load_entry_plan_watchers(SimpleNamespace())
    assert engines == {}
    assert plans == ({plan.plan_id: plan} if outcome == "active_claim" else {})
    assert retired == [(plan.plan_id, "entry_plan_runtime_authority_revoked")]


@pytest.mark.asyncio
async def test_sequence_anomaly_engine_is_resynchronized_not_reused(monkeypatch):
    """A sequence anomaly must not permanently silence a Triggered Plan.

    ``MomentumTriggerEngine`` fails closed on a duplicate/out-of-order frame and
    nothing inside it clears ``health_reason``, so a reused instance rejects every
    later event forever while the stream keeps reporting ready.  The watcher owns
    the engine lifecycle, so a reload must discard that instance and re-arm.
    """

    from types import SimpleNamespace

    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.data.symbols import InstrumentId
    from aletheia.universe.fast_stream import FastMarketEvent

    payload = "{}"
    row = {
        "id": "resync-plan",
        "status": "waiting",
        "plan_json": payload,
        "plan_hash": hashlib.sha256(payload.encode()).hexdigest(),
        "order_id": None,
    }
    plan = SimpleNamespace(
        plan_id="resync-plan",
        planning_generation_id="generation-resync",
        risk_policy_id="policy-resync",
        risk_policy_revision=1,
        risk_policy_hash="policy-hash-resync",
        execution_account_mode="bitget_demo",
        execution_mode="TRIGGERED",
        order_tactic="OPPOSING_BBO_IOC",
        max_wait_sec=0,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        trigger_spec_id="anchor-retrace-reclaim",
        trigger_params={
            "breakout_level": 100.0,
            "pullback_low": 99.5,
            "pullback_high": 100.5,
            "entry_low": 100.0,
            "entry_high": 100.5,
            "invalidation_price": 98.0,
            "sweep_low": 99.5,
            "sweep_high": 100.0,
            "atr_abs": 2.0,
            "geometry_version": "anchor-retrace-reclaim-geometry/v1",
            "geometry_fingerprint": "d" * 64,
        },
    )
    monkeypatch.setattr(watch.repo, "list_watchable_entry_plans", lambda _limit: [row])
    monkeypatch.setattr(watch.repo, "get_candidate_planning_generation", lambda _id: {"is_current": 1})
    monkeypatch.setattr(watch.EntryPlan, "model_validate_json", lambda _payload: plan)
    monkeypatch.setattr(
        "aletheia.risk.policy.resolve_risk_policy",
        lambda _cfg, _mode: SimpleNamespace(
            policy=SimpleNamespace(policy_id="policy-resync", revision=1),
            policy_hash="policy-hash-resync",
        ),
    )

    async def no_prewarm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(watch, "_prewarm_market_structure", no_prewarm)
    cfg = SimpleNamespace(
        fast_trigger=SimpleNamespace(max_setups=10, mode="shadow"),
        exchange=SimpleNamespace(paper_execution_mode="bitget_demo"),
        universe=SimpleNamespace(max_spread_bps=50, min_depth_10bps_usd=100),
    )

    _, engines = await watch._load_entry_plan_watchers(cfg)
    engine = engines[plan.plan_id]

    # A healthy engine keeps its rolling windows across an ordinary reload.
    _, reused = await watch._load_entry_plan_watchers(cfg, engines)
    assert reused[plan.plan_id] is engine

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "perp")
    engine.confirm_subscription()
    first = FastMarketEvent(
        event_type="quote",
        instrument=instrument,
        exchange_timestamp_ms=1_000,
        received_timestamp_ms=1_000,
        sequence=2,
        bid=100.0,
        ask=100.01,
    )
    engine.observe(first)
    engine.observe(first.model_copy(update={"received_timestamp_ms": 1_100, "sequence": 1}))
    assert engine.health_reason == "sequence_anomaly:quote"
    assert engine.observe(
        first.model_copy(update={"received_timestamp_ms": 1_200, "sequence": 3})
    ) is None, "poisoned engine must stay closed until it is replaced"

    _, rebuilt = await watch._load_entry_plan_watchers(cfg, reused)

    fresh = rebuilt[plan.plan_id]
    assert fresh is not engine
    assert fresh.health_reason is None
    assert fresh.state == "armed"
    assert fresh.subscription_confirmed is False
    # If proof persistence expired/failed after observe(), the durable Plan is
    # still waiting. A terminal in-memory engine cannot strand that Plan.
    fresh.state = "triggered"
    _, rearmed = await watch._load_entry_plan_watchers(cfg, rebuilt)
    assert rearmed[plan.plan_id] is not fresh
    assert rearmed[plan.plan_id].state == "armed"


@pytest.fixture(autouse=True)
def _watcher_owner_fixture(monkeypatch):
    # These watcher tests supply decoded owner snapshots. Runtime admission is
    # checked by the Entry authority suite, independently of watcher scheduling.
    monkeypatch.setattr("aletheia.execution.entry_plan_execution.require_entry_plan_runtime_authority", lambda _: None)
