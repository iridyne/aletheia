from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


_COSTS = {
    "execution_fee_pct": 0.06,
    "slippage_bps": 5.0,
    "fallback_spread_bps": 25.0,
}


def _outcome(index: int, *, entry_at: datetime) -> dict[str, object]:
    return {
        "outcome_candidate_key": f"candidate-selection-convergence:{index}:long",
        "scanner_run_id": f"run-{index}",
        "mechanism_id": "continuation",
        "direction": "long",
        "venue": "okx",
        "market_type": "spot",
        "strength_quintile": 5,
        "horizon": "4h",
        "status": "settled",
        "entry_ts": entry_at.isoformat(),
        "settled_at": (entry_at + timedelta(hours=4)).isoformat(),
        "symbol": "SOL/USDT",
        "net_return_pct": 0.5,
        "mae_pct": -0.25,
        "cost_model_version": "v1",
        "min_tradable_edge_pct": 0.05,
        "estimated_fee_pct": 0.1,
        "estimated_spread_slippage_pct": 0.1,
        "estimated_funding_pct": 0.0,
        "mechanism_behavior_version": "universe-opportunity-evidence/v2",
        "outcome_contract_version": "candidate-selection-utility-outcome/v1",
        "cost_assumptions": _COSTS,
    }


def _estimate(outcomes):
    from aletheia.universe.economic_utility import estimate_economic_utility

    return estimate_economic_utility(
        candidate_key="candidate-selection-convergence",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=outcomes,
        source_cutoff_at="2026-09-03T00:00:00+00:00",
        cost_assumptions=_COSTS,
    )


def _authority_bundle():
    from aletheia.config.schema import UniverseConfig
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
    )
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
    )

    population = []
    for index in range(20):
        population.append(
            {
                "asset_id": f"asset-{index}",
                "feature_snapshot_id": f"feature-{index}",
                "feature_snapshot_ts": "2026-09-02T00:00:00+00:00",
                "feature_source": "universe_feature_snapshot/v1",
                "registry_status": "active",
                "symbol": f"COIN{index}/USDT",
                "venue": "okx",
                "market_type": "spot",
                "last_price": 100.0,
                "quote_volume_24h": 10_000_000.0,
                "spread_bps": 5.0,
                "depth_10bps_usd": 30_000.0,
                "data_freshness_sec": 60.0,
                "return_1h": 1.0,
                "return_4h": 3.0,
                "return_24h": 8.0 + index,
                "relative_strength_24h": 5.0,
                "rsi_14": 60.0,
                "atr_pct": 2.0,
            }
        )
    legacy = [
        {**item, "scanner_family": "momentum", "scanner_direction": "long", "total_score": 60.0} for item in population
    ]
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    outcomes = [
        {
            **_outcome(index, entry_at=start + timedelta(hours=4 * index)),
            "symbol": f"COIN{index % 6}/USDT",
        }
        for index in range(60)
    ]
    cfg = UniverseConfig(max_candidates=3)
    selection_policy = {
        **candidate_selection_policy_identity(cfg),
        "cost_assumptions": {
            "execution_fee_pct": _COSTS["execution_fee_pct"],
            "slippage_bps": _COSTS["slippage_bps"],
        },
    }
    shadow = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-convergence-bundle",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=cfg,
        legacy_scored=legacy,
        legacy_allocated=legacy[:3],
        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=outcomes,
        selection_policy=selection_policy,
    )
    return build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-convergence-authority",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=cfg,
        legacy_scored=legacy,
        legacy_allocated=legacy[:3],
        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=outcomes,
        estimator_training_cutoff="2026-09-02T00:00:00+00:00",
        selection_policy=selection_policy,
        authority_applied=True,
        qualification_evidence={
            "verdict": "positive",
            "promotion_eligible": True,
            "policy_epoch_id": shadow.batch.policy_epoch_id,
            "selection_policy": selection_policy,
            "blocking_reasons": [],
            "invalid_reasons": [],
        },
    ), legacy


def test_runtime_commit_is_attested_provenance_not_causal_identity():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RuntimeSession
    from aletheia.universe.candidate_selection_evaluation import (
        _runtime_provenance_reason,
    )

    for session_id, commit in (("capture-session", "commit-a"), ("settle-session", "commit-b")):
        repo.insert_runtime_session(
            RuntimeSession(
                session_id=session_id,
                boot_commit=commit,
                behavior_fingerprint="same-semantic-manifest",
            )
        )
    row = {
        "status": "settled",
        "producer_runtime_session_id": "capture-session",
        "producer_commit": "commit-a",
        "producer_behavior_fingerprint": "same-semantic-manifest",
        "settlement_runtime_session_id": "settle-session",
        "settlement_producer_commit": "commit-b",
        "settlement_behavior_fingerprint": "same-semantic-manifest",
    }

    assert _runtime_provenance_reason(row) is None


def test_policy_identity_ignores_commit_but_rotates_semantic_contract(monkeypatch):
    from aletheia.agent_runtime import runtime_session
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
    )

    cfg = AletheiaConfig()
    monkeypatch.setattr(
        runtime_session,
        "current_provenance",
        lambda: {"producer_commit": "commit-a", "behavior_fingerprint": "same-runtime"},
    )
    commit_a = candidate_selection_policy_identity(cfg)
    monkeypatch.setattr(
        runtime_session,
        "current_provenance",
        lambda: {"producer_commit": "commit-b", "behavior_fingerprint": "same-runtime"},
    )
    commit_b = candidate_selection_policy_identity(cfg)
    changed = cfg.model_copy(deep=True)
    changed.universe.max_spread_bps += 1.0

    assert commit_a == commit_b
    assert "runtime_identity" not in commit_a
    assert candidate_selection_policy_identity(changed) != commit_a

    from aletheia.universe import candidate_selection_authority as authority

    original_manifest = authority.candidate_selection_semantic_manifest()
    monkeypatch.setattr(
        authority,
        "candidate_selection_semantic_manifest",
        lambda: {**original_manifest, "economic_estimator": "future-estimator-contract"},
    )
    assert candidate_selection_policy_identity(cfg) != commit_a


def test_overlapping_outcomes_do_not_inflate_independent_n():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    overlapping = [_outcome(index, entry_at=start + timedelta(minutes=10 * index)) for index in range(24)]
    non_overlapping = [_outcome(index, entry_at=start + timedelta(hours=4 * index)) for index in range(3)]

    assert _estimate(overlapping).parent_independent_n == 1
    assert _estimate(non_overlapping).parent_independent_n == 3


def test_independent_economic_units_cross_boundaries_symbols_and_order():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    boundary = [
        _outcome(0, entry_at=start + timedelta(hours=3, minutes=50)),
        _outcome(1, entry_at=start + timedelta(hours=4, minutes=10)),
    ]
    same_t0 = [
        {**_outcome(index + 10, entry_at=start), "symbol": symbol, "net_return_pct": value}
        for index, (symbol, value) in enumerate(
            (("BTC/USDT", 1.0), ("ETH/USDT", 2.0), ("SOL/USDT", 3.0), ("DOGE/USDT", 4.0))
        )
    ]
    exact = [_outcome(index, entry_at=start + timedelta(hours=4 * index)) for index in range(3)]

    assert _estimate(boundary).parent_independent_n == 1
    assert _estimate(same_t0).parent_independent_n == 1
    assert _estimate(exact).parent_independent_n == 3
    forward = _estimate(exact + same_t0[1:])
    reverse = _estimate(list(reversed(exact + same_t0[1:])))
    assert forward == reverse


def test_qualification_uses_same_non_overlapping_t0_rule():
    from aletheia.universe.candidate_selection_evaluation import (
        _independent_policy_batches,
    )

    def selected(*offsets: timedelta):
        return _independent_policy_batches([
            {"scanner_run_id": f"run-{index}", "captured_at": (datetime(2026, 8, 1, tzinfo=timezone.utc) + offset).isoformat()}
            for index, offset in enumerate(offsets)
        ])

    assert len(selected(*(timedelta(hours=index) for index in range(4)))) == 1
    assert len(selected(timedelta(hours=3, minutes=50), timedelta(hours=4, minutes=10))) == 1
    assert len(selected(timedelta(), timedelta(hours=4), timedelta(hours=8))) == 3


def test_frozen_cohort_index_matches_reference_estimator():
    from aletheia.universe.economic_utility import (
        build_economic_utility_cohort_index,
        estimate_economic_utility,
    )

    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    outcomes = [
        {
            **_outcome(index, entry_at=start + timedelta(hours=4 * index)),
            "symbol": f"COIN{index % 6}/USDT",
        }
        for index in range(60)
    ]
    reference = _estimate(outcomes)
    index = build_economic_utility_cohort_index(
        outcomes,
        source_cutoff_at="2026-09-03T00:00:00+00:00",
        cost_assumptions=_COSTS,
    )
    indexed = estimate_economic_utility(
        candidate_key="candidate-selection-convergence",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        cohort_index=index,
    )

    assert indexed == reference


def test_candidate_bundle_builds_one_frozen_cohort_index_per_refresh(monkeypatch):
    from aletheia.universe import candidate_selection_shadow as shadow

    calls = 0
    original = shadow.build_economic_utility_cohort_index

    def track(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(shadow, "build_economic_utility_cohort_index", track)
    _authority_bundle()

    assert calls == 2  # one Shadow qualification bundle plus one authority bundle


def test_continuation_uses_momentum_as_typed_primary_source():
    from aletheia.universe.continuous_scoring import ComponentScore
    from aletheia.universe.opportunity_evidence import _project

    projected = _project(
        "continuation",
        (
            ComponentScore("momentum", "short", 40.0, 35.0, True, None),
            ComponentScore("short_bias", "short", 90.0, 85.0, True, None),
        ),
    )

    assert projected.source_component_id == "momentum"
    assert projected.point_strength == 40.0


@pytest.mark.anyio
async def test_shadow_not_due_skips_expensive_candidate_build(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe import service

    monkeypatch.setattr(
        service.repo,
        "universe_twin_shadow_capture_due",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        service,
        "_build_candidate_selection_bundle",
        lambda **_kwargs: pytest.fail("not-due shadow must not load or aggregate history"),
    )

    assert (
        await service._persist_candidate_selection_shadow_nonfatal(
            population=[{}],
            legacy_scored=[],
            legacy_allocated=[],
            scanner_run_id="not-due",
            captured_at="2026-09-03T00:00:00+00:00",
            cfg=AletheiaConfig(),
        )
        == {"status": "not_due", "record_count": 0, "reason": None}
    )


def test_typed_mechanism_survives_target_projection_and_member_model():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember, UniverseSnapshot
    from aletheia.universe.candidate_opportunity import target_candidate_opportunity_context
    from aletheia.universe.candidate_selection_shadow import target_candidates_from_bundle

    bundle, legacy = _authority_bundle()
    assert repo.get_universe_twin_shadow_batch(bundle.batch.scanner_run_id) is None
    projected = target_candidates_from_bundle(bundle, legacy)
    assert projected
    candidate = projected[0]
    assert candidate["opportunity_mechanism"] == "continuation"
    assert candidate["opportunity_mechanism_version"] == "continuation/v1"
    assert candidate["opportunity_source_component"] == "momentum"
    assert candidate["opportunity_source_cutoff_at"] == "2026-09-02T00:00:00+00:00"
    assert len(candidate["opportunity_evidence_hash"]) == 64
    member_id = "target-member"
    target_record = candidate["_candidate_opportunity_record"]
    context = target_candidate_opportunity_context(
        candidate_id=member_id,
        decision=target_record["decision"],
        policy_epoch=candidate["candidate_selection_policy_epoch"],
        source_record_hash=target_record["source_record_hash"],
        detected_at="2026-09-02T00:00:00+00:00",
    )

    member = UniverseMember(
        id=member_id,
        snapshot_id="snapshot",
        asset_id=candidate["asset_id"],
        venue=candidate["venue"],
        symbol=candidate["symbol"],
        market_type=candidate["market_type"],
        rank=1,
        total_score=candidate["total_score"],
        scanner_family=candidate["scanner_family"],
        scanner_direction=candidate["scanner_direction"],
        candidate_selection_policy=candidate["candidate_selection_policy"],
        candidate_selection_policy_epoch=candidate["candidate_selection_policy_epoch"],
        candidate_selection_utility_pct=candidate["candidate_selection_utility_pct"],
        opportunity_mechanism=candidate["opportunity_mechanism"],
        opportunity_mechanism_version=candidate["opportunity_mechanism_version"],
        opportunity_source_component=candidate["opportunity_source_component"],
        opportunity_source_cutoff_at=candidate["opportunity_source_cutoff_at"],
        opportunity_evidence_hash=candidate["opportunity_evidence_hash"],
        candidate_opportunity_context_version=context.version,
        candidate_opportunity_context_json=context.model_dump_json(),
        candidate_opportunity_context_hash=context.context_hash,
    )
    assert member.model_dump()["opportunity_source_component"] == "momentum"
    repo.insert_universe_snapshot(UniverseSnapshot(id="snapshot"))
    repo.insert_universe_member(member)
    persisted = repo.get_universe_member(member.id)
    assert persisted["opportunity_mechanism"] == "continuation"
    assert persisted["opportunity_source_component"] == "momentum"
    assert persisted["opportunity_evidence_hash"] == candidate["opportunity_evidence_hash"]


@pytest.mark.parametrize("denominator_rows", [20, 100, 250, 500])
def test_candidate_storage_projection_is_cadence_bounded(denominator_rows):
    from aletheia.universe.candidate_selection_shadow import (
        candidate_selection_storage_projection,
    )

    bundle, _legacy = _authority_bundle()
    projection = candidate_selection_storage_projection(
        bundle.evidence[:1] * denominator_rows,
    )

    assert projection["rows_per_run"] == denominator_rows
    assert projection["runs_per_day"] == 6
    assert projection["mib_per_30d"] == pytest.approx(
        projection["mib_per_day"] * 30,
    )
    assert projection["mib_per_day"] < 12.0


@pytest.mark.anyio
@pytest.mark.parametrize("verdict", ["positive", "negative", "neutral", "invalid"])
async def test_terminal_candidate_qualification_stops_confirmatory_capture(monkeypatch, verdict):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe import (
        candidate_selection_authority,
        candidate_selection_evaluation,
        service,
    )

    evaluations = 0
    policy = {}

    def evaluate(**_kwargs):
        nonlocal evaluations
        evaluations += 1
        return {"verdict": verdict, "selection_policy": dict(policy)}

    monkeypatch.setattr(
        service.repo,
        "universe_twin_shadow_capture_due",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        candidate_selection_evaluation,
        "evaluate_candidate_selection_policy",
        evaluate,
    )
    monkeypatch.setattr(
        candidate_selection_authority,
        "candidate_selection_policy_identity",
        lambda *_args, **_kwargs: dict(policy),
    )
    monkeypatch.setattr(
        service,
        "_build_candidate_selection_bundle",
        lambda **_kwargs: pytest.fail("terminal epoch must not append confirmatory evidence"),
    )
    monkeypatch.setattr(service, "_terminal_candidate_selection_policy_fingerprint", None)

    for captured_at in ("2026-09-03T00:00:00+00:00", "2026-09-03T04:00:00+00:00"):
        assert await service._persist_candidate_selection_shadow_nonfatal(
            population=[{}],
            legacy_scored=[],
            legacy_allocated=[],
            scanner_run_id="terminal",
            captured_at=captured_at,
            cfg=AletheiaConfig(),
        ) == {"status": "terminal", "record_count": 0, "reason": None}
    assert evaluations == 1

    policy["epoch"] = 2
    assert await service._persist_candidate_selection_shadow_nonfatal(
        population=[{}],
        legacy_scored=[],
        legacy_allocated=[],
        scanner_run_id="new-policy",
        captured_at="2026-09-03T08:00:00+00:00",
        cfg=AletheiaConfig(),
    ) == {"status": "terminal", "record_count": 0, "reason": None}
    assert evaluations == 2


@pytest.mark.anyio
async def test_shadow_timeout_late_atomic_commit_updates_same_scanner(tmp_path, monkeypatch):
    import asyncio
    import json
    import threading
    from tests.test_entry_logic import setup_env
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseScannerRun
    from aletheia.universe import service
    from aletheia.universe import candidate_selection_evaluation
    setup_env(tmp_path, monkeypatch)
    bundle, _ = _authority_bundle()
    scanner_id = bundle.batch.scanner_run_id
    repo.insert_universe_scanner_run(UniverseScannerRun(
        id=scanner_id, status="completed", metrics=json.dumps({"candidate_path": {"shadow_capture_status": "pending"}}),
    ))
    monkeypatch.setattr(service, "_terminal_candidate_selection_policy_fingerprint", None)
    monkeypatch.setattr(candidate_selection_evaluation, "evaluate_candidate_selection_policy", lambda **kwargs: {"verdict": "insufficient"})
    monkeypatch.setattr(service, "_build_candidate_selection_bundle", lambda **kwargs: bundle)
    monkeypatch.setattr(repo, "universe_twin_shadow_capture_due", lambda *args, **kwargs: True)
    monkeypatch.setattr(service, "_TWIN_SHADOW_TIMEOUT_SEC", 0.1)
    entered, release = threading.Event(), threading.Event()
    original_insert = repo.insert_universe_twin_shadow_bundle
    def delayed_insert(*args, **kwargs):
        count = original_insert(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return count
    monkeypatch.setattr(repo, "insert_universe_twin_shadow_bundle", delayed_insert)
    try:
        result = await service._persist_candidate_selection_shadow_nonfatal(
            population=[], legacy_scored=[], legacy_allocated=[], scanner_run_id=scanner_id,
            captured_at=bundle.batch.captured_at, cfg=AletheiaConfig(),
        )
        assert entered.is_set()
        assert result == {"status": "pending", "record_count": 0, "reason": "capture_completion_unknown"}
    finally:
        release.set()
        await asyncio.gather(*tuple(service._candidate_shadow_tasks))
    metrics = json.loads(repo.get_db().execute("SELECT metrics FROM universe_scanner_runs WHERE id=?", (scanner_id,)).fetchone()[0])
    assert metrics["candidate_path"]["shadow_capture_status"] == "captured"
    assert repo.get_db().execute("SELECT COUNT(*) FROM universe_twin_shadow_batches WHERE scanner_run_id=?", (scanner_id,)).fetchone()[0] == 1
