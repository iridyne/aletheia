"""End-to-end contracts for the VergeX/Hyperliquid utilization completion."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


def test_funding_crowding_threshold_uses_raw_fraction_units():
    from aletheia.universe.funding_units import CROWDED_FUNDING_RATE
    from aletheia.universe.scanners import score_momentum

    assert CROWDED_FUNDING_RATE == 0.0001  # 0.01%, expressed as a raw fraction.
    base = {
        "relative_strength_24h": 6.0,
        "relative_strength_4h": 2.0,
        "return_24h": 6.0,
        "rsi_14": 62.0,
        "rsi_4h": 60.0,
    }
    neutral_score, neutral_reasons, _ = score_momentum(
        {**base, "funding_rate": CROWDED_FUNDING_RATE}
    )
    crowded_score, crowded_reasons, _ = score_momentum(
        {**base, "funding_rate": CROWDED_FUNDING_RATE + 0.000001}
    )

    assert crowded_score == neutral_score - 15.0
    assert "funding crowded long" not in neutral_reasons
    assert "funding crowded long" in crowded_reasons


def test_atr_chase_guard_removes_direction_but_keeps_shadow_scores():
    from aletheia.universe.scanners import run_all_scanners

    result = run_all_scanners({
        "relative_strength_24h": 7.0,
        "relative_strength_4h": 2.0,
        "return_4h": 4.0,
        "return_24h": 7.0,
        "return_15m": 2.0,
        "last_candle_range_atr_15m": 1.51,
        "rsi_14": 62.0,
        "rsi_4h": 61.0,
    })

    assert result["scanner_direction"] is None
    assert result["proposed_direction"] == "long"
    assert result["opportunity_score"] == 0.0
    assert result["all_scores"]["momentum"] > 0
    assert "15m candle >1.5 ATR; no chase" in result["reasons"]


def test_hyperliquid_open_interest_is_rendered_with_source_freshness():
    from aletheia.data.context_builder import render_enriched_context_md

    rendered = render_enriched_context_md(
        ["BTC/USDT"],
        {},
        {},
        None,
        xvenue_funding={
            "BTC/USDT": {
                "hl_ann": 0.12,
                "hl_open_interest_usd": 1_250_000_000.0,
                "hl_oi_change_1h_pct": 3.2,
                "hl_observed_at": "2026-07-17T08:00:00+00:00",
            }
        },
    )

    assert "Hyperliquid" in rendered
    assert "OI $1.25B" in rendered
    assert "OI Delta1h +3.2%" in rendered
    assert "2026-07-17T08:00:00+00:00" in rendered


def test_multitimeframe_features_are_durable_and_instrument_scoped(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import list_universe_feature_snapshots

    db = get_db()
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(universe_feature_snapshots)")
    }
    assert {
        "rsi_15m",
        "atr_15m_pct",
        "return_15m",
        "last_candle_range_atr_15m",
        "rsi_4h",
        "atr_4h_pct",
        "bb_width_4h",
        "vol_ratio_4h",
        "bb_squeeze",
    } <= columns

    now = datetime.now(timezone.utc).isoformat()
    db.executemany(
        "INSERT INTO universe_registry (id, venue, market_type, symbol) VALUES (?, ?, ?, ?)",
        [
            ("btc-spot", "okx", "spot", "BTC/USDT"),
            ("btc-perp", "okx", "perp", "BTC/USDT"),
        ],
    )
    db.executemany(
        "INSERT INTO universe_feature_snapshots "
        "(id, asset_id, ts, rsi_15m, rsi_4h) VALUES (?, ?, ?, ?, ?)",
        [
            ("feat-spot", "btc-spot", now, 41.0, 51.0),
            ("feat-perp", "btc-perp", now, 61.0, 71.0),
        ],
    )
    db.commit()

    rows = list_universe_feature_snapshots(limit=10)
    identity = {(row["venue"], row["market_type"], row["symbol"]): row for row in rows}
    assert identity[("okx", "spot", "BTC/USDT")]["rsi_15m"] == 41.0
    assert identity[("okx", "perp", "BTC/USDT")]["rsi_4h"] == 71.0


@pytest.mark.asyncio
async def test_feature_collector_fetches_and_persists_15m_1h_4h(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.universe.features import _fetch_single_feature
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc)

    class FakeExchange:
        id = "okx"

        async def fetch_ohlcv(self, symbol, timeframe, limit):
            count = 100 if timeframe == "15m" else 168 if timeframe == "1h" else 50
            step = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}[timeframe]
            end = int(now.timestamp() * 1000)
            rows = []
            for i in range(count):
                close = 100.0 + i * 0.1
                high = close + 0.4
                low = close - 0.4
                if timeframe == "15m" and i == count - 1:
                    high = close + 2.0
                    low = close - 2.0
                rows.append([end - (count - 1 - i) * step, close - 0.1, high, low, close, 10.0 + i])
            return rows

    db = get_db()
    db.execute(
        "INSERT INTO universe_registry (id, venue, market_type, symbol) "
        "VALUES ('asset-btc', 'okx', 'spot', 'BTC/USDT')"
    )
    db.commit()
    feat = await _fetch_single_feature(
        FakeExchange(),
        {"id": "asset-btc", "venue": "okx", "market_type": "spot", "symbol": "BTC/USDT"},
        {"quoteVolume": 1_000_000.0, "bid": 109.8, "ask": 110.0},
        now,
        lambda symbol: symbol,
    )

    assert feat is not None
    assert feat["rsi_15m"] is not None and feat["rsi_4h"] is not None
    assert feat["last_candle_range_atr_15m"] > 1.5
    row = db.execute(
        "SELECT rsi_15m, atr_15m_pct, rsi_4h, atr_4h_pct, bb_width_4h "
        "FROM universe_feature_snapshots WHERE id=?",
        (feat["feature_snapshot_id"],),
    ).fetchone()
    assert row is not None
    assert all(row[key] is not None for key in row.keys())


def test_pi_decision_persists_exact_evidence_manifest():
    from aletheia.agent_shell.reflection import _build_pi_decision

    manifest = {
        "version": "vergex-utilization-v1",
        "policy_epoch_id": "epoch-test",
        "instrument": {"venue": "okx", "symbol": "BTC/USDT", "market_type": "perp"},
        "technical_snapshot": {"id": "feat-1", "timeframes": ["15m", "1h", "4h"]},
        "hyperliquid": {"funding_id": "fr-1", "open_interest_id": "oi-1"},
        "position_x": {"status": "not_configured"},
    }
    row = _build_pi_decision({
        "action": "skip",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "perp",
        "evidence_manifest": manifest,
    })

    assert json.loads(row.evidence_manifest_json) == manifest


def test_debate_uses_candidate_market_type_not_same_symbol_other_instrument():
    from aletheia.agent_shell.trading_decision import _build_bull_prompt

    ctx = {
        "layer1": {
            "feature_index": {
                "okx|spot|BTC/USDT": {
                    "symbol": "BTC/USDT", "market_type": "spot", "rsi_14": 30.0,
                },
                "okx|perp|BTC/USDT": {
                    "symbol": "BTC/USDT", "market_type": "perp", "rsi_14": 70.0,
                },
            },
            "regime": None,
        },
        "layer2": {
            "pending_signals": [{
                "symbol": "BTC/USDT", "market_type": "perp", "direction": "long",
            }],
        },
    }

    prompt = _build_bull_prompt("BTC/USDT", "long", ctx)
    assert "RSI=70.0" in prompt
    assert "RSI=30.0" not in prompt


def test_runtime_manifest_does_not_reconstruct_missing_history_from_latest_rows(
    tmp_path, monkeypatch,
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import build_decision_evidence_manifest
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO funding_rate_snapshots "
        "(id, symbol, exchange, funding_rate, funding_rate_annualized, observed_at) "
        "VALUES ('hl-fr', 'BTC/USDT', 'hyperliquid', 0.0001, 0.876, ?)",
        (now,),
    )
    db.execute(
        "INSERT INTO open_interest_snapshots "
        "(id, symbol, exchange, open_interest_usd, observed_at) "
        "VALUES ('hl-oi', 'BTC/USDT', 'hyperliquid', 1000000, ?)",
        (now,),
    )
    db.commit()
    feature = {
        "feature_snapshot_id": "feat-1",
        "feature_ts": now,
        "venue": "okx",
        "symbol": "BTC/USDT",
        "market_type": "perp",
    }
    manifest = build_decision_evidence_manifest(
        {
            "vergex_policy_epoch_id": "epoch-1",
            "layer1": {"feature_index": {"okx|perp|BTC/USDT": feature}},
        },
        SimpleNamespace(symbol="BTC/USDT", market_type="perp"),
    )

    assert manifest["technical_snapshot"]["id"] == "feat-1"
    assert manifest["hyperliquid"]["funding_id"] is None
    assert manifest["hyperliquid"]["open_interest_id"] is None
    assert manifest["opportunity_t0"] == {"status": "unavailable"}
    assert manifest["opportunity_t1"] == {"status": "unavailable"}
    assert manifest["position_x"]["status"] == "unavailable"


def test_owner_aggregate_import_seeds_observation_only_shadow_outcomes(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.data.collectors.vergex_structure import ingest_owner_aggregate
    from aletheia.storage.sqlite import get_db

    payload = {
        "source_ref": "owner-export-2026-07-17",
        "rows": [
            {
                "symbol": "BTC/USDT",
                "venue": "hyperliquid",
                "as_of_time": "2026-07-17T08:00:00+00:00",
                "reference_price": 65000.0,
                "structure_bias": "bullish",
                "composite_z": 1.4,
                "data_quality": "complete",
            }
        ],
    }
    result = ingest_owner_aggregate(payload)

    assert result["status"] == "ingested"
    assert result["snapshots_inserted"] == 1
    assert result["shadow_outcomes_seeded"] == 3
    db = get_db()
    snap = db.execute("SELECT * FROM perp_structure_snapshots").fetchone()
    assert snap["source"] == "owner_aggregate"
    assert snap["source_ref"] == payload["source_ref"]
    assert snap["source_hash"] == result["source_hash"]
    outcomes = db.execute(
        "SELECT horizon, status, market_type, direction, policy_epoch_id "
        "FROM perp_structure_shadow_outcomes ORDER BY horizon"
    ).fetchall()
    assert {(row["horizon"], row["status"]) for row in outcomes} == {
        ("1h", "pending"),
        ("4h", "pending"),
        ("24h", "pending"),
    }
    assert {row["direction"] for row in outcomes} == {"long"}
    assert {row["market_type"] for row in outcomes} == {"perp"}
    assert all(row["policy_epoch_id"] for row in outcomes)


def test_structure_source_status_is_honest_without_owner_import():
    from aletheia.data.collectors.vergex_structure import structure_source_status

    status = structure_source_status()
    assert status["mode"] == "owner_aggregate_import"
    assert status["automatic_source_configured"] is False
    assert status["decision_authority"] == "observation_only"


def test_owner_aggregate_rejects_sensitive_account_fields(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.data.collectors.vergex_structure import ingest_owner_aggregate

    with pytest.raises(ValueError, match="forbidden sensitive field"):
        ingest_owner_aggregate({
            "source_ref": "unsafe-export",
            "rows": [{
                "symbol": "BTC/USDT",
                "as_of_time": "2026-07-17T08:00:00Z",
                "wallet_address": "must-not-enter-runtime",
            }],
        })


@pytest.mark.asyncio
async def test_structure_shadow_outcome_settles_without_decision_influence(
    tmp_path, monkeypatch,
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.data.collectors.vergex_structure import ingest_owner_aggregate
    from aletheia.data.perp_structure_shadow import (
        settle_due_perp_structure_outcomes,
        shadow_window_status,
    )
    from aletheia.storage.sqlite import get_db
    import aletheia.universe.outcome_recorder as outcome_recorder

    ingest_owner_aggregate({
        "source_ref": "historical-owner-export",
        "rows": [{
            "symbol": "BTC/USDT",
            "venue": "hyperliquid",
            "as_of_time": "2026-07-01T00:00:00+00:00",
            "reference_price": 100.0,
            "structure_bias": "bullish",
            "data_quality": "complete",
        }],
    })

    async def fake_observations(rows, *, now):
        return {
            row["id"]: {
                "exit_price": 110.0,
                "price_source": "test_ohlcv",
                "settlement_lag_sec": 0.0,
            }
            for row in rows
        }

    monkeypatch.setattr(outcome_recorder, "_batch_fetch_market_observations", fake_observations)
    assert await settle_due_perp_structure_outcomes() == 3
    rows = get_db().execute(
        "SELECT status, directional_return_pct FROM perp_structure_shadow_outcomes"
    ).fetchall()
    assert {row["status"] for row in rows} == {"settled"}
    assert {round(row["directional_return_pct"], 6) for row in rows} == {10.0}
    window = shadow_window_status(now=datetime(2026, 7, 17, tzinfo=timezone.utc))
    assert window["mature_for_review"] is True
    assert window["decision_influence"] == "disabled"
