"""End-to-end contracts for the VergeX/Hyperliquid utilization completion."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
                # The trailing exchange row is still forming and must not enter
                # technical facts.  Put the event on the latest *closed* bar.
                if timeframe == "15m" and i == count - 2:
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
        "SELECT rsi_15m, atr_15m_pct, rsi_4h, atr_4h_pct, bb_width_4h, "
        "technical_algorithm_version, technical_snapshot_manifest "
        "FROM universe_feature_snapshots WHERE id=?",
        (feat["feature_snapshot_id"],),
    ).fetchone()
    assert row is not None
    assert all(row[key] is not None for key in row.keys())
    assert row["technical_algorithm_version"] == "technical-analysis-v1"
    manifest = json.loads(row["technical_snapshot_manifest"])
    assert set(manifest) == {"15m", "1h", "4h"}
    assert all(manifest[tf]["input_fingerprint"] for tf in manifest)


@pytest.mark.asyncio
async def test_pending_feature_refresh_bypasses_recent_row_suppression(
    tmp_path, monkeypatch,
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import UniverseFeatureSnapshot
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.features import _fetch_single_feature

    now = datetime.now(timezone.utc)

    class FakeExchange:
        id = "okx"

        async def fetch_ohlcv(self, symbol, timeframe, limit):
            count = {"15m": 100, "1h": 168, "4h": 50}[timeframe]
            step = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}[timeframe]
            end = int(now.timestamp() * 1000)
            rows = []
            for i in range(count):
                close = 100 + i / 100
                rows.append([
                    end - (count - 1 - i) * step,
                    close - 0.1,
                    close + 1,
                    close - 1,
                    close,
                    10,
                ])
            return rows

    db = get_db()
    db.execute(
        "INSERT INTO universe_registry (id, venue, market_type, symbol) "
        "VALUES ('asset-priority', 'okx', 'spot', 'BTC/USDT')"
    )
    db.execute(
        "INSERT INTO universe_feature_snapshots (id, asset_id, ts, rsi_14) "
        "VALUES ('feature-recent', 'asset-priority', ?, 50)",
        ((now - timedelta(minutes=5)).isoformat(),),
    )
    db.commit()
    buffered: list[UniverseFeatureSnapshot] = []

    feature = await _fetch_single_feature(
        FakeExchange(),
        {
            "id": "asset-priority",
            "venue": "okx",
            "market_type": "spot",
            "symbol": "BTC/USDT",
        },
        {"quoteVolume": 1_000_000.0, "bid": 99.9, "ask": 100.1},
        now,
        lambda symbol: symbol,
        snapshot_buffer=buffered,
        force_persist=True,
    )

    assert feature is not None
    assert len(buffered) == 1
    assert buffered[0].id == feature["feature_snapshot_id"]
    assert buffered[0].id != "feature-recent"


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
    assert status["accepted_inputs"] == ["aggregate_rows", "sanitized_holder_batch"]


def test_sanitized_holder_batch_persists_only_aggregate_evidence(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env
    from tests.test_position_structure_features import _position_x_markets

    setup_env(tmp_path, monkeypatch)
    from aletheia.data.collectors.vergex_structure import ingest_owner_aggregate
    from aletheia.storage.sqlite import get_db

    result = ingest_owner_aggregate({
        "source_ref": "owner-sanitized-batch-2026-08-11",
        "markets": _position_x_markets(),
    })

    assert result["input_mode"] == "sanitized_holder_batch"
    assert result["snapshots_inserted"] == 3
    assert result["shadow_outcomes_seeded"] == 9
    assert result["universe_size"] == 3
    assert result["mean_coverage_ratio"] == 0.9
    assert len(result["batch_id"]) == 64
    db = get_db()
    snapshots = db.execute(
        "SELECT raw_json, dimension_scores_json, risk_context_json "
        "FROM perp_structure_snapshots"
    ).fetchall()
    assert len(snapshots) == 3
    persisted = "".join(value for row in snapshots for value in row)
    assert "holders" not in persisted
    assert "entry_price" not in persisted
    assert "liquidation_price" not in persisted
    assert result["batch_id"] in persisted


def test_sanitized_holder_batch_validation_is_atomic(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env
    from tests.test_position_structure_features import _position_x_markets

    setup_env(tmp_path, monkeypatch)
    from aletheia.data.collectors.vergex_structure import ingest_owner_aggregate
    from aletheia.storage.sqlite import get_db

    markets = _position_x_markets()
    markets[1]["holders"][0]["wallet_address"] = "must-not-enter-runtime"
    with pytest.raises(ValueError, match="forbidden sensitive field"):
        ingest_owner_aggregate({"source_ref": "unsafe-holder-batch", "markets": markets})

    assert get_db().execute("SELECT COUNT(*) FROM perp_structure_snapshots").fetchone()[0] == 0


def _insert_position_x_evaluation_fixture(*, retrospective: bool = False):
    from aletheia.storage import repositories as repo

    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    for index, (score, percentile, raw_return) in enumerate([
        (1.0, 100.0, 10.0),
        (0.0, 50.0, 0.0),
        (-1.0, 0.0, -10.0),
    ]):
        as_of = start + timedelta(minutes=index)
        due = as_of + timedelta(hours=1)
        observed = due + timedelta(minutes=1) if retrospective else as_of + timedelta(minutes=1)
        snapshot_id = f"p3-snapshot-{index}"
        source_hash = f"p3-source-{index}"
        dimensions = {
            name: {"raw": score, "percentile": percentile, "direction": "neutral"}
            for name in ("cgo", "vap", "skw", "lfa", "wp")
        }
        repo.insert_perp_structure_snapshot({
            "id": snapshot_id,
            "symbol": f"P3-{index}/USDT",
            "venue": "hyperliquid",
            "source": "owner_aggregate",
            "source_hash": source_hash,
            "as_of_time": as_of.isoformat(),
            "observed_at": observed.isoformat(),
            "reference_price": 100.0,
            "data_quality": "complete",
            "structure_bias": "bullish" if score > 0 else "bearish" if score < 0 else "neutral",
            "composite_z": score,
            "dimension_scores_json": json.dumps(dimensions),
            "risk_context_json": json.dumps({
                "benchmark_version": "position-x-p1-v1",
                "availability_stratum": "cgo+vap+skw+lfa+wp",
                "equal_weight_score": score / 2,
                "batch_id": "p3-batch",
                "parameters": {"price_bin_width_pct": 0.0025},
            }),
        })
        repo.insert_perp_structure_shadow_outcome({
            "id": f"p3-outcome-{index}",
            "snapshot_id": snapshot_id,
            "symbol": f"P3-{index}/USDT",
            "venue": "hyperliquid",
            "market_type": "perp",
            "direction": "long",
            "horizon": "1h",
            "entry_price_usd": 100.0,
            "entry_ts": as_of.isoformat(),
            "due_at": due.isoformat(),
            "settled_at": (due + timedelta(minutes=1)).isoformat(),
            "exit_price_usd": 100.0 * (1 + raw_return / 100.0),
            "directional_return_pct": raw_return,
            "settlement_price_source": "fixture_ohlcv",
            "settlement_lag_sec": 60.0,
            "status": "settled",
            "policy_epoch_id": "p3-epoch",
            "source_hash": source_hash,
        })


def test_position_x_p3_evaluator_compares_gross_baselines_without_authority(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _insert_position_x_evaluation_fixture()
    from aletheia.data.perp_structure_shadow import evaluate_position_x_shadow

    result = evaluate_position_x_shadow(
        policy_epoch_id="p3-epoch",
        horizons=("1h",),
        minimum_samples=3,
        now=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    horizon = result["horizons"]["1h"]
    assert result["status"] == "ready_for_research_review"
    assert result["decision_influence"] == "disabled"
    assert horizon["eligible"] == 3
    assert horizon["feature_contracts_consistent"] is True
    assert horizon["baselines"]["weighted_prior"]["spearman_ic"] == 1.0
    assert horizon["baselines"]["weighted_prior"]["mean_directional_return_pct"] == pytest.approx(6.666667)
    assert horizon["baselines"]["no_structure"]["mean_directional_return_pct"] == 0.0
    assert result["economics"]["cost_aware_status"] == "unavailable"


def test_position_x_p3_evaluator_rejects_post_outcome_imports(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    _insert_position_x_evaluation_fixture(retrospective=True)
    from aletheia.data.perp_structure_shadow import evaluate_position_x_shadow

    result = evaluate_position_x_shadow(
        policy_epoch_id="p3-epoch",
        horizons=("1h",),
        minimum_samples=1,
        minimum_days=0,
        now=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    horizon = result["horizons"]["1h"]
    assert result["status"] == "insufficient_data"
    assert horizon["eligible"] == 0
    assert horizon["exclusions"] == {"ingestion_lag_exceeded": 3}


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
