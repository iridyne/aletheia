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
    pending = next(row for row in repo.list_universe_discovery_outcomes(window["id"]) if row["status"] == "pending")
    assert repo.settle_filter_candidate_outcome(
        pending["id"],
        exit_price=101.0,
        settled_at="2026-01-16T06:00:01+00:00",
        economics={},
        settlement_price_source="horizon_ohlcv",
        settlement_lag_sec=0.0,
    ) is False


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


def test_directional_annotations_and_absolute_return_ranks(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    benchmark.capture_completed_run(
        scanner_run_id="directional-annotations",
        population=population,
        allocated=population[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    facts = decode_filter_decision_batch(
        repo.list_universe_discovery_batches(window["id"])[0]["candidate_facts"]
    )
    assert all(isinstance(fact["direction_annotations"], dict) for fact in facts)
    for row in repo.list_universe_discovery_outcomes(window["id"]):
        path = json.loads(row["benchmark_path_facts"])
        if path.get("matched_pair_id"):
            assert path["matched_pair_role"] in {"selected", "control"}

    ranks = benchmark._rank_values(
        [
            {"venue": "okx", "market_type": "perp", "symbol": "A/USDT", "asset_id": "a", "return_4h": -9.0},
            {"venue": "okx", "market_type": "perp", "symbol": "B/USDT", "asset_id": "b", "return_4h": 2.0},
        ],
        "return_4h",
        absolute=True,
    )
    assert ranks[("okx", "perp", "A/USDT", "a")] > ranks[("okx", "perp", "B/USDT", "b")]


def test_holm_uses_frozen_twenty_one_hypothesis_family():
    results = {
        f"comparison-{index}": (
            {"available": True, "p_value": 0.01} if index == 0
            else {"available": False, "reason": "insufficient_complete_batches"}
        )
        for index in range(21)
    }
    benchmark._holm(results)
    assert results["comparison-0"]["holm_adjusted_p"] == pytest.approx(0.21)
    assert {result["holm_family_size"] for result in results.values()} == {21}
    assert results["comparison-1"]["holm_adjusted_p"] is None


def test_price_quality_failure_is_terminal_but_not_settled(benchmark_db, monkeypatch):
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    benchmark.capture_completed_run(
        scanner_run_id="bad-price-quality",
        population=population,
        allocated=population[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )

    async def observations(rows, *, now):
        return {
            row["id"]: {
                "exit_price": 101.0,
                "range_high": 102.0,
                "range_low": 99.0,
                "price_source": "horizon_ohlcv",
                "price_quality_ok": False,
                "settlement_lag_sec": 0.0,
            }
            for row in rows
        }

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )
    asyncio.run(settle_due_filter_outcomes(max_rows=1))
    rows = repo.list_universe_discovery_outcomes(window["id"])
    assert sum(row["status"] == "failed" for row in rows) == 1
    assert sum(row["status"] == "settled" for row in rows) == 0


def test_analysis_reports_gaps_strata_costs_and_settlement(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population()
    benchmark.capture_completed_run(
        scanner_run_id="report-contract",
        population=population,
        allocated=population[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    analysis = benchmark.analyze_benchmark(window["id"])
    assert analysis["capture_continuity"]["material_gap_detected"] is True
    assert "material_capture_continuity_gap" in analysis["integrity"]["reasons"]
    assert set(analysis["stratified_4h_summary"]) == {"direction", "market_type", "regime_tag"}
    assert set(analysis["costs"]) == {"assumptions", "selected_4h_mean_components"}
    assert set(analysis["settlement"]) == {"status_counts", "price_source_counts", "lag_sec"}
    assert "insufficient_matched_outcome_coverage" in analysis["integrity"]["reasons"]
def test_cluster_bootstrap_preserves_asset_level_estimand(monkeypatch):
    monkeypatch.setattr(benchmark, "MIN_INFERENCE_BATCHES", 2)
    monkeypatch.setattr(benchmark, "BOOTSTRAP_DRAWS", 100)
    result = benchmark._paired_inference(
        {"batch-a": [10.0], "batch-b": [0.0, 0.0, 0.0]},
        contract_hash="contract",
        comparison="asset-weighted",
    )
    assert result["complete_batches"] == 2
    assert result["complete_pairs"] == 4
    assert result["mean_difference_pct"] == 2.5


def test_capture_rejects_t0_before_preregistered_window(benchmark_db):
    window = benchmark.start_benchmark(now="2026-01-02T00:00:00+00:00", code_commit="commit-a")
    result = benchmark.capture_completed_run(
        scanner_run_id="predates-window",
        population=_population(),
        allocated=_population()[:5],
        entry_ts="2026-01-01T23:59:59+00:00",
        regime_tag="sideways",
    )
    assert result == {
        "accepted": False,
        "reason": "capture_before_window",
        "batch_id": result["batch_id"],
    }
    assert repo.list_universe_discovery_batches(window["id"]) == []


def test_analysis_uses_frozen_costs_and_gates_incomplete_family_winner(benchmark_db, monkeypatch):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="frozen-costs",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    batches = repo.list_universe_discovery_batches(window["id"])
    outcomes = repo.list_universe_discovery_outcomes(window["id"])
    for row in outcomes:
        row.update({
            "status": "settled",
            "exit_price_usd": float(row["entry_price_usd"]) * 1.01,
            "settlement_price_source": "horizon_ohlcv",
            "estimated_fee_pct": 99.0,
            "estimated_spread_slippage_pct": 99.0,
            "estimated_funding_pct": 99.0,
        })

    def favorable(_differences, *, contract_hash, comparison):
        return {
            "available": True,
            "complete_batches": 40,
            "mean_difference_pct": 1.0,
            "ci95": [0.5, 1.5],
            "p_value": 0.001,
        }

    def significant(results):
        for result in results.values():
            result["holm_adjusted_p"] = 0.001

    monkeypatch.setattr(benchmark, "_paired_inference", favorable)
    monkeypatch.setattr(benchmark, "_holm", significant)
    analysis = benchmark.analyze_benchmark(window["id"], snapshot=(window, batches, outcomes))
    components = analysis["costs"]["selected_4h_mean_components"]
    assert all(value != 99.0 for value in components.values())
    assert analysis["integrity"]["complete"] is False
    assert analysis["family_winner"] == {"status": "no_supported_winner"}


def test_finalize_cleans_artifact_when_transaction_snapshot_fails(benchmark_db, monkeypatch):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="finalize-race",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    original = repo.list_universe_discovery_outcomes
    calls = 0

    def changing(window_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("snapshot read failed")
        return original(window_id)

    monkeypatch.setattr(repo, "list_universe_discovery_outcomes", changing)
    output = benchmark_db / "raced.json.gz"
    with pytest.raises(RuntimeError, match="snapshot read failed"):
        benchmark.finalize_benchmark(window["id"], output, now="2026-01-16T06:00:00+00:00")
    assert output.exists() is False
    assert repo.get_universe_discovery_benchmark(window["id"])["status"] == "active"


def test_finalize_requires_durable_runtime_commit(benchmark_db, monkeypatch):
    window = benchmark.start_benchmark(now="2026-01-01T00:00:00+00:00", code_commit="commit-a")
    output = benchmark_db / "missing-commit.json.gz"
    monkeypatch.setattr(benchmark, "_current_commit", lambda: None)
    with pytest.raises(ValueError, match="benchmark_runtime_commit_unavailable"):
        benchmark.finalize_benchmark(window["id"], output, now="2026-01-16T06:00:00+00:00")
    assert output.exists() is False


def test_inactive_benchmark_rows_leave_general_settlement_queue(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="inactive-queue",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    benchmark.abort_benchmark(window["id"], reason="test", now="2026-01-02T00:00:00+00:00")
    pending = repo.list_pending_filter_candidate_outcomes("2026-02-01T00:00:00+00:00", limit=1000)
    assert not {row["benchmark_window_id"] for row in pending} - {None}


def test_capture_preserves_source_specific_regime_and_durable_session_only(benchmark_db):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    population = _population(2)
    population[0]["analysis_context"] = {"kind": "crypto"}
    population[1]["analysis_context"] = {"kind": "equity"}
    benchmark.capture_completed_run(
        scanner_run_id="source-semantics",
        population=population,
        allocated=population,
        entry_ts=started_at,
        regime_tag="sideways",
    )
    rows = repo.list_universe_discovery_outcomes(window["id"])
    by_asset = {}
    for row in rows:
        by_asset.setdefault(json.loads(row["benchmark_path_facts"])["asset_id"], row)
    assert by_asset["asset-0"]["regime_tag"] == "sideways"
    assert by_asset["asset-1"]["regime_tag"] is None
    assert all(row["producer_runtime_session_id"] is None for row in rows)


def test_matching_rejects_invalid_control_price():
    population = _population(2)
    selected_identity = benchmark._identity(population[0])
    population[1]["last_price"] = 0.0
    memberships, annotations = benchmark._cohort_memberships(
        population,
        allocated=[population[0]],
        audits={
            benchmark._identity(item): {
                "family_evidence": {family: {"applicability": "not_applicable"} for family in benchmark.FAMILIES}
            }
            for item in population
        },
        max_candidates=2,
    )
    assert "matched_non_selected" not in memberships.get(
        (benchmark._identity(population[1]), population[0]["scanner_direction"]), set()
    )
    assert "matched_pair_id" not in annotations.get((selected_identity, population[0]["scanner_direction"]), {})


def test_analysis_excludes_partial_family_batch_and_keeps_selected_asset_weights(benchmark_db, monkeypatch):
    started_at = "2026-01-01T00:00:00+00:00"
    window = benchmark.start_benchmark(now=started_at, code_commit="commit-a")
    benchmark.capture_completed_run(
        scanner_run_id="inference-inputs",
        population=_population(),
        allocated=_population()[:5],
        entry_ts=started_at,
        regime_tag="sideways",
    )
    batches = repo.list_universe_discovery_batches(window["id"])
    outcomes = repo.list_universe_discovery_outcomes(window["id"])
    family_labels = [f"family:{family}" for family in benchmark.FAMILIES] + ["simple_momentum"]
    target = next(
        row
        for row in outcomes
        if row["horizon"] == "4h"
        and "production_selected" not in json.loads(row["benchmark_cohorts"])
        and any(label in family_labels for label in json.loads(row["benchmark_cohorts"]))
    )
    target_label = next(label for label in json.loads(target["benchmark_cohorts"]) if label in family_labels)
    other_label = next(label for label in family_labels if label != target_label)
    ordered = sorted((target_label, other_label), key=family_labels.index)
    comparison = f"{ordered[0]}_vs_{ordered[1]}"
    for row in outcomes:
        if row is target or row["horizon"] != "4h":
            continue
        row.update(
            status="settled",
            exit_price_usd=float(row["entry_price_usd"]) * 1.01,
            settlement_price_source="horizon_ohlcv",
        )

    calls = {}

    def capture_inputs(differences, *, contract_hash, comparison):
        calls[comparison] = differences
        return {"available": False, "reason": "test"}

    monkeypatch.setattr(benchmark, "MIN_INFERENCE_BATCHES", 1)
    monkeypatch.setattr(benchmark, "_paired_inference", capture_inputs)
    benchmark.analyze_benchmark(window["id"], snapshot=(window, batches, outcomes))
    assert calls[comparison] == {}
    selected_values = next(iter(calls["selected_vs_zero"].values()))
    assert len(selected_values) == sum(
        row["horizon"] == "4h" and "production_selected" in json.loads(row["benchmark_cohorts"]) for row in outcomes
    )
