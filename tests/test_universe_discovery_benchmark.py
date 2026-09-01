from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from aletheia.analytics.filter_evidence import decode_filter_decision_batch
from aletheia.config import loader
from aletheia.storage import repositories as repo
from aletheia.storage import sqlite as sqlite_store
from aletheia.storage.migrations import apply_migrations
from aletheia.storage.models import FilterCandidateOutcome
from aletheia.universe import discovery_benchmark as benchmark


@pytest.fixture
def benchmark_db(tmp_path, monkeypatch):
    sqlite_store.reset_db()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    apply_migrations()
    monkeypatch.setattr(benchmark, "_current_commit", lambda: "commit-a")
    yield tmp_path
    sqlite_store.reset_db()
    loader._invalidate_config_cache()


def _population(count: int = 18) -> list[dict]:
    rows = []
    for index in range(count):
        ret = (index - count / 2) * 0.6
        rows.append({
            "asset_id": f"asset-{index}",
            "feature_snapshot_id": f"feature-{index}",
            "venue": "okx",
            "symbol": f"ASSET{index}/USDT",
            "market_type": "perp",
            "last_price": 100.0 + index,
            "quote_volume_24h": 10_000_000.0 + index * 100_000,
            "spread_bps": 5.0 + index % 3,
            "depth_10bps_usd": 50_000.0,
            "atr_pct": 2.0 + index % 4,
            "return_4h": ret,
            "return_24h": ret * 2,
            "relative_strength_24h": ret * 2,
            "relative_strength_4h": ret,
            "vol_ratio": 2.0,
            "rsi_14": 55.0,
            "rsi_15m": 52.0,
            "rsi_4h": 55.0,
            "funding_rate": 0.0001,
            "open_interest": 1_000_000.0,
            "oi_delta_1h": 0.1,
            "long_short_ratio": 1.0,
            "data_freshness_sec": 5,
            "tradability_score": 80.0,
            "scanner_family": "momentum",
            "scanner_direction": "long" if ret > 0 else "short",
            "total_score": 70.0 - index,
            "execution_eligible": True,
        })
    return rows


def test_capture_is_frozen_lossless_and_idempotent(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    allocated = population[:5]

    first = benchmark.capture_completed_run(
        scanner_run_id="scanner-run-1",
        population=population,
        allocated=allocated,
        entry_ts=started_at,
        regime_tag="sideways",
    )
    second = benchmark.capture_completed_run(
        scanner_run_id="scanner-run-1",
        population=population,
        allocated=allocated,
        entry_ts=started_at,
        regime_tag="sideways",
    )

    assert first["accepted"] is True
    assert first["outcomes_inserted"] > 0
    assert second == {
        "accepted": True,
        "idempotent": True,
        "reason": "already_captured",
        "batch_id": first["batch_id"],
    }
    batches = repo.list_universe_discovery_batches(window["id"])
    assert len(batches) == 1
    facts = decode_filter_decision_batch(batches[0]["candidate_facts"])
    assert len(facts) == len(population)
    assert facts[0]["production_scanner_family"] == "momentum"
    assert set(facts[0]["family_evidence"]) == set(benchmark.FAMILIES)
    status = benchmark.status_benchmark(window["id"], now=started_at)
    assert status["phase"] == "collecting"
    assert status["promotion_eligible"] is False


def test_identity_drift_invalidates_without_rewriting_evidence(benchmark_db, monkeypatch):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    monkeypatch.setattr(benchmark, "_current_commit", lambda: "commit-b")

    result = benchmark.capture_completed_run(
        scanner_run_id="scanner-run-2",
        population=_population(),
        allocated=[],
        entry_ts=started_at,
        regime_tag=None,
    )

    assert result == {"accepted": False, "reason": "frozen_contract_changed", "invalidated": True}
    status = benchmark.status_benchmark(window["id"], now=started_at)
    assert status["status"] == "invalidated"
    assert status["batch_count"] == 0


def test_finalize_reports_unavailable_and_writes_hashed_package(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="scanner-run-3",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    output = benchmark_db / "universe-discovery.json.gz"

    with pytest.raises(ValueError, match="benchmark_finalize_not_due"):
        benchmark.finalize_benchmark(window["id"], output, now="2026-01-15T00:00:00+00:00")
    result = benchmark.finalize_benchmark(window["id"], output, now="2026-01-16T06:00:00+00:00")

    package = json.loads(gzip.decompress(output.read_bytes()))
    assert result["status"] == "finalized"
    assert result["analysis"]["conclusion"] == "unavailable"
    assert result["analysis"]["promotion_eligible"] is False
    assert package["manifest"]["contract_hash"] == window["contract_hash"]
    assert package["manifest"]["promotion_eligible"] is False
    assert len(package["population_batches"][0]["candidate_facts"]) == len(_population())
    assert result["artifact_sha256"]



def test_active_benchmark_outcomes_preempt_older_backlog(benchmark_db, monkeypatch):
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="scanner-run-priority",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    ordinary = FilterCandidateOutcome(
        outcome_candidate_key="ordinary-older-backlog",
        venue="okx",
        symbol="OLD/USDT",
        market_type="spot",
        direction="long",
        horizon="24h",
        entry_price_usd=100.0,
        entry_ts="2025-01-01T00:00:00+00:00",
        due_at="2025-01-02T00:00:00+00:00",
    )
    repo.insert_filter_candidate_outcomes_batch([ordinary])

    async def observations(rows, *, now):
        return {
            row["id"]: {
                "exit_price": 101.0,
                "range_high": 102.0,
                "range_low": 99.0,
                "price_source": "horizon_ohlcv",
                "price_quality_ok": True,
                "settlement_lag_sec": 0.0,
            }
            for row in rows
        }

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )
    assert asyncio.run(settle_due_filter_outcomes(max_rows=1)) == 1
    benchmark_rows = repo.list_universe_discovery_outcomes(window["id"])
    assert sum(row["status"] == "settled" for row in benchmark_rows) == 1
    pending = repo.list_pending_filter_candidate_outcomes(
        datetime.now(timezone.utc).isoformat(), limit=1000
    )
    assert ordinary.id in {row["id"] for row in pending}


def test_cli_start_status_and_abort(benchmark_db):
    from aletheia.cli.evidence_cmd import evidence_app

    runner = CliRunner()
    started = runner.invoke(evidence_app, ["universe-discovery", "start", "--json"])
    assert started.exit_code == 0, started.output
    payload = json.loads(started.output)
    assert payload["phase"] == "collecting"
    assert payload["promotion_eligible"] is False

    status = runner.invoke(evidence_app, ["universe-discovery", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["id"] == payload["id"]

    aborted = runner.invoke(
        evidence_app,
        ["universe-discovery", "abort", "--reason", "operator test", "--json"],
    )
    assert aborted.exit_code == 0, aborted.output
    assert json.loads(aborted.output)["status"] == "aborted"


def test_service_capture_failure_is_nonfatal(monkeypatch, caplog):
    from aletheia.universe import service

    def fail(**_kwargs):
        raise RuntimeError("capture unavailable")

    monkeypatch.setattr(benchmark, "capture_completed_run", fail)
    service._capture_universe_discovery_nonfatal(
        scanner_run_id="run",
        population=[],
        allocated=[],
        entry_ts=datetime.now(timezone.utc).isoformat(),
        regime_tag=None,
        cfg=None,
    )
    assert "capture failed (non-fatal)" in caplog.text