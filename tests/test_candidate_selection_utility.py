from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.config.schema import UniverseConfig


def test_fast_route_actionability_does_not_require_slow_snapshots(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe import service, candidate_selection_shadow, snapshot_freshness
    from aletheia.universe.allocator import decorate_universe_candidates
    from aletheia.storage import repositories as repo
    from tests.test_candidate_selection_convergence import _authority_bundle

    _, population = _authority_bundle(with_history=False)
    cfg = AletheiaConfig()
    cfg.universe.max_candidates = 3
    route_reason = None
    monkeypatch.setattr(service, "fast_market_data_unavailability_reason", lambda *args: route_reason)
    monkeypatch.setattr(candidate_selection_shadow, "load_candidate_selection_outcomes", lambda **kwargs: [])
    monkeypatch.setattr(snapshot_freshness, "has_fresh_market_snapshot",
                        lambda *args, **kwargs: pytest.fail("slow snapshot must not own current admission"))
    assert repo.get_db().execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0

    def select():
        bundle = service._build_candidate_selection_bundle(
            population=population, scanner_run_id="actionability", captured_at="2026-09-02T00:00:00+00:00",
            cfg=cfg, authority_applied=True,
        )
        return candidate_selection_shadow.target_candidates_from_bundle(bundle, population)

    selected = select()
    assert len(selected) == 3 and all(row["execution_eligible"] for row in selected)
    decorate_universe_candidates(selected, cfg.universe)
    assert all(row["execution_eligible"] for row in selected)
    route_reason = "fast_market_route_unsupported"
    assert select() == []
def test_spot_short_is_not_admitted_for_planning():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        target_candidates_from_bundle,
    )

    population = [_feature(
        return_1h=-1.0,
        return_4h=-3.0,
        return_24h=-8.0,
        relative_strength_24h=-5.0,
        rsi_14=40.0,
    )]
    bundle = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="spot-short",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=1),
        action_route_reasons={("feature-0", "okx", "spot", "COIN0/USDT"): None},
        historical_outcomes=(),
        selection_policy=candidate_selection_policy_identity(AletheiaConfig()),
    )

    assert target_candidates_from_bundle(bundle, population) == []


def test_empty_universe_bootstraps_candidate_and_planning_without_slow_cache(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env
    from aletheia.config.loader import load_config
    from aletheia.storage import repositories as repo

    setup_env(tmp_path, monkeypatch)
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
    db = repo.get_db()
    for table in ("universe_members", "candidate_planning_generations", "market_snapshots"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    candidate = _controlled_admitted_target_candidate(
        cfg, monkeypatch, mechanism="continuation", direction="long", market_type="spot",
    )
    assert candidate.symbol.startswith("COIN") and candidate.execution_eligible
    generation = repo.get_candidate_planning_for_candidate(candidate.id)
    assert generation and generation["status"] == "pending"
    assert generation["candidate_id"] == candidate.id
    assert db.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("legacy_unavailable", [False, True])
def test_legacy_learning_cannot_veto_current_candidate_publication(tmp_path, monkeypatch, legacy_unavailable):
    def legacy_rows(**kwargs):
        if legacy_unavailable:
            raise RuntimeError("legacy ledger unavailable")
        return [{"stage": "paper_active", "experiment_id": "historical-experiment"}]

    monkeypatch.setattr("extensions.learning.ledger.list_open_experiments", legacy_rows)
    test_empty_universe_bootstraps_candidate_and_planning_without_slow_cache(tmp_path, monkeypatch)


def test_actionability_manifest_rotates_existing_policy_epoch(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env
    from aletheia.config.loader import load_config, save_config
    from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
    from aletheia.universe.candidate_eligibility import canonical_fingerprint

    setup_env(tmp_path, monkeypatch)
    cfg = load_config()
    current = candidate_selection_policy_identity(cfg)
    old = {**current, "semantic_manifest": {**current["semantic_manifest"],
        "actionability": "supported-mechanism-and-fast-route-before-capacity/v2"}}
    old_epoch = canonical_fingerprint(old)
    cfg.universe.candidate_selection_policy_epoch = old_epoch
    save_config(cfg)
    restored = load_config()
    assert restored.universe.candidate_selection_policy_epoch == canonical_fingerprint(current)
    assert restored.universe.candidate_selection_policy_epoch != old_epoch
    assert {k:v for k,v in old.items() if k != "semantic_manifest"} == {
        k:v for k,v in current.items() if k != "semantic_manifest"}


def test_refresh_freezes_detected_regime_and_short_control_identity(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from tests.test_entry_logic import setup_env
    from aletheia.config.loader import load_config
    from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity

    setup_env(tmp_path, monkeypatch)
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.regime.enabled = True
    before = candidate_selection_policy_identity(cfg)
    cfg.regime.short_enabled = not cfg.regime.short_enabled
    assert candidate_selection_policy_identity(cfg) != before
    monkeypatch.setattr("extensions.regime.collector.collect_regime_inputs", AsyncMock(return_value={"fixture": True}))
    monkeypatch.setattr("extensions.regime.detector.debounced_detect", lambda **kwargs: SimpleNamespace(
        state="bear", confidence=0.8, long_bias=0.2, short_bias=0.8, signals={},
        detected_at=datetime.now(timezone.utc).isoformat(),
    ))
    candidate = _controlled_admitted_target_candidate(
        cfg, monkeypatch, mechanism="continuation", direction="long", market_type="spot",
    )
    context = json.loads(candidate.candidate_opportunity_context_json)
    assert candidate.regime_tag == context["context"]["regime"] == "bear"
    from aletheia.trading.entry_plan import candidate_planning_identity
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1
    before_identity = candidate_planning_identity(candidate.model_dump())
    context["context"]["regime"] = "bull"
    context.pop("context_hash")
    changed = CandidateOpportunityContextV1.model_validate(context)
    after_identity = candidate_planning_identity({
        **candidate.model_dump(), "candidate_opportunity_context_json": changed.model_dump_json(),
    })
    assert before_identity[0] == after_identity[0]
    assert before_identity[1] != after_identity[1]


def _controlled_admitted_target_candidate(cfg, monkeypatch, *, mechanism, direction, market_type):
    import asyncio
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember
    from aletheia.config.loader import save_config, load_config
    from extensions.data.ccxt_provider import CCXTProvider
    from aletheia.data.providers import timeframe_ms
    from aletheia.universe.service import UniverseService

    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    cfg = load_config()
    class Exchange:
        id = "bitget"
        def __init__(self):
            self.options = {}
            self.markets = {}
            for i in range(20):
                symbol = f"COIN{i}/USDT" + (":USDT" if market_type == "perp" else "")
                self.markets[symbol] = {"id": f"COIN{i}USDT", "symbol": symbol, "base": f"COIN{i}", "quote": "USDT",
                    "active": True, "spot": market_type == "spot", "swap": market_type == "perp", "contract": market_type == "perp",
                    "linear": market_type == "perp", "settle": "USDT" if market_type == "perp" else None, "contractSize": 1,
                    "precision": {"price": 4, "amount": 6}, "limits": {"cost": {"min": 1}}}
        async def load_markets(self):
            return self.markets
        def market(self, symbol):
            return self.markets[symbol]
        async def fetch_tickers(self, symbols):
            return {s: {"last": 100, "quoteVolume": 10_000_000, "percentage": 5 if mechanism == "continuation" else 0, "bid": 99.99, "ask": 100.01} for s in symbols}
        async def fetch_order_book(self, symbol, limit=20):
            return {"symbol": symbol, "bids": [[99.99, 10000]], "asks": [[100.01, 10000]]}
        async def fetch_ohlcv(self, symbol, timeframe="1h", limit=100, **kwargs):
            step = timeframe_ms(timeframe)
            end = int(datetime.now(timezone.utc).timestamp()*1000) // step * step
            index = int(symbol.split("/")[0].removeprefix("COIN")) if symbol.startswith("COIN") else 0
            slope = (.1 + index*.002 if symbol.startswith("COIN") else 0) if mechanism == "continuation" else .1
            closes = [100+slope*(i-limit+1) + (.3*(-1)**i if mechanism == "continuation" else 0) for i in range(limit)]
            return [[end-(limit-i)*step, close, close+.1, close-.1, close, 1000] for i, close in enumerate(closes)]
    monkeypatch.setattr(CCXTProvider, "_ensure_primary", lambda self, *args, **kwargs: Exchange())
    service = UniverseService()
    snapshot_id = asyncio.run(service.refresh())
    assert snapshot_id is not None, [dict(r) for r in repo.get_db().execute("SELECT * FROM universe_scanner_runs ORDER BY rowid DESC LIMIT 1")]
    members = service.get_current_universe()
    assert members, repo.get_db().execute("SELECT metrics FROM universe_scanner_runs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    candidate = UniverseMember.model_validate(members[0])
    assert candidate.opportunity_mechanism == mechanism and candidate.scanner_direction == direction
    assert candidate.candidate_selection_policy_epoch == cfg.universe.candidate_selection_policy_epoch
    return candidate

def test_refresh_publishes_loadable_candidate_economic_evidence(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env
    from aletheia.config.loader import load_config
    from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
    from aletheia.universe.candidate_selection_shadow import load_candidate_selection_outcomes

    setup_env(tmp_path, monkeypatch)
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
    candidate = _controlled_admitted_target_candidate(
        cfg, monkeypatch, mechanism="continuation", direction="long", market_type="spot",
    )
    outcomes = load_candidate_selection_outcomes(selection_policy=candidate_selection_policy_identity(load_config()))
    assert any(row["symbol"] == candidate.symbol and row["direction"] == "long"
               and row["mechanism_id"] == "continuation" for row in outcomes)


def test_current_publication_survives_retired_scanner_failure(tmp_path, monkeypatch):
    import json
    from tests.test_entry_logic import setup_env
    from aletheia.config.loader import load_config
    from aletheia.storage import repositories as repo
    from aletheia.trading.entry_plan import candidate_planning_identity
    from aletheia.universe import scanners

    setup_env(tmp_path, monkeypatch)
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "bitget"
    calls = []

    def unavailable_research_scanner(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("research_scanner_unavailable")

    monkeypatch.setattr(scanners, "run_all_scanners", unavailable_research_scanner)
    candidate = _controlled_admitted_target_candidate(
        cfg, monkeypatch, mechanism="continuation", direction="long", market_type="spot",
    )
    assert calls  # The failing research seam was actually exercised.
    db = repo.get_db()
    run = db.execute("SELECT * FROM universe_scanner_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert run["status"] == "completed" and json.loads(run["errors"]) == []
    metrics = json.loads(run["metrics"])["candidate_path"]
    assert metrics["population_count"] == 20
    assert metrics["selected_count"] == len(repo.list_universe_members(run["snapshot_id"]))
    generation = repo.get_candidate_planning_for_candidate(candidate.id)
    assert generation is not None
    assert (generation["planning_key"], generation["planning_identity"]) == candidate_planning_identity(candidate.model_dump())
    assert db.execute("SELECT COUNT(*) FROM alpha_control_observations").fetchone()[0] == 0



def test_runtime_epoch_does_not_require_economic_admission():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.candidate_eligibility import canonical_fingerprint
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity, candidate_selection_authority_block_reason,
    )
    cfg = AletheiaConfig()
    cfg.universe = UniverseConfig.model_validate({**cfg.universe.model_dump(),
        "candidate_selection_policy": "candidate_selection_utility_v1",
        "candidate_selection_policy_epoch": canonical_fingerprint(candidate_selection_policy_identity(cfg))})
    assert candidate_selection_authority_block_reason(cfg) is None
    cfg.universe.max_candidates += 1
    assert candidate_selection_authority_block_reason(cfg) is not None




def _feature(index: int = 0, **overrides):
    value = {
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
        "return_24h": 8.0,
        "relative_strength_24h": 5.0,
        "rsi_14": 60.0,
        "atr_pct": 2.0,
    }
    value.update(overrides)
    return value


def test_hard_gate_is_fail_closed_and_action_route_is_observe_only():
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate

    cfg = UniverseConfig()
    available = evaluate_hard_gate(
        _feature(), cfg, candidate_key="candidate-1", evaluated_at="2026-09-02T00:00:00+00:00"
    )
    assert (available.status, available.actionability) == ("eligible", "actionable")
    replay = evaluate_hard_gate(
        dict(reversed(list(_feature().items()))),
        cfg,
        candidate_key="candidate-1",
        evaluated_at="2026-09-02T00:00:00+00:00",
    )
    assert replay.input_fingerprint == available.input_fingerprint
    assert replay.policy_fingerprint == available.policy_fingerprint

    missing = evaluate_hard_gate(
        _feature(spread_bps=None),
        cfg,
        candidate_key="candidate-2",
        evaluated_at="2026-09-02T00:00:00+00:00",
    )
    assert missing.status == "unavailable"
    assert "spread_unavailable" in missing.reason_codes

    observe_only = evaluate_hard_gate(
        _feature(),
        cfg,
        candidate_key="candidate-3",
        evaluated_at="2026-09-02T00:00:00+00:00",
        action_route_reason="FAST_MARKET_DATA_VENUE_MISMATCH",
    )
    assert (observe_only.status, observe_only.actionability) == ("eligible", "observe_only")


def test_hard_gate_persists_depth_threshold_and_ignores_volatility():
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate

    cfg = UniverseConfig(min_depth_10bps_usd=25_000)
    rejected = evaluate_hard_gate(
        _feature(depth_10bps_usd=2_499, atr_pct=999.0),
        cfg,
        candidate_key="candidate-depth",
        evaluated_at="2026-09-02T00:00:00+00:00",
    )
    check = next(item for item in rejected.checks if item.check_id == "depth")
    assert rejected.status == "ineligible"
    assert check.observed_value == 2_499
    assert check.threshold == 2_500
    assert all(item.check_id != "volatility" for item in rejected.checks)


@pytest.mark.parametrize(
    ("feature", "config", "expected_status"),
    [
        (_feature(symbol="USDC/USDT"), UniverseConfig(), "ineligible"),
        (_feature(), UniverseConfig(blacklist=["COIN0/USDT"]), "ineligible"),
        (_feature(registry_status="inactive"), UniverseConfig(), "ineligible"),
        (_feature(last_price=float("nan")), UniverseConfig(), "ineligible"),
        (_feature(quote_volume_24h=1.0), UniverseConfig(), "ineligible"),
        (_feature(feature_snapshot_ts="not-a-time"), UniverseConfig(), "unavailable"),
        (
            _feature(feature_snapshot_ts="2026-09-03T00:00:00+00:00"),
            UniverseConfig(),
            "unavailable",
        ),
        (_feature(feature_source="unknown/v1"), UniverseConfig(), "unavailable"),
    ],
)
def test_hard_gate_rejects_invalid_physical_or_provenance_facts(
    feature, config, expected_status,
):
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate

    decision = evaluate_hard_gate(
        feature,
        config,
        candidate_key="candidate-physical-gate",
        evaluated_at="2026-09-02T00:00:00+00:00",
    )
    assert decision.status == expected_status


def test_opportunity_evidence_maps_mechanisms_without_summing_short_bias():
    from aletheia.universe.opportunity_evidence import build_opportunity_evidence

    bundle = build_opportunity_evidence(
        _feature(return_24h=-10.0, relative_strength_24h=-8.0, return_4h=-4.0, rsi_14=35.0),
        candidate_key="candidate-evidence",
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    mechanisms = {item.mechanism_id: item for item in bundle.mechanisms}
    assert set(mechanisms) == {
        "continuation", "reversal", "positioning_forced_flow", "information_catalyst"
    }
    continuation = mechanisms["continuation"]
    assert continuation.point_strength is None or 0 <= continuation.point_strength <= 100
    assert len(continuation.evidence_refs) == len(set(continuation.evidence_refs))
    assert mechanisms["information_catalyst"].applicability == "unavailable"
    assert bundle.context["volatility_state"]["role"] == "context_only"

    invalid = build_opportunity_evidence(
        _feature(return_24h=float("nan")),
        candidate_key="candidate-evidence-invalid",
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    assert all(item.applicability == "unavailable" for item in invalid.mechanisms)

    conflict = build_opportunity_evidence(
        _feature(relative_strength_24h=8.0, return_4h=-5.0, return_24h=-4.0, rsi_14=50.0),
        candidate_key="candidate-evidence-conflict",
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    )
    continuation = next(item for item in conflict.mechanisms if item.mechanism_id == "continuation")
    assert continuation.applicability == "applicable"
    assert continuation.direction == "long"
    assert continuation.source_component_id == "momentum"
    assert "diagnostic_component:short_bias:short" in continuation.reason_codes


def test_within_run_percentile_requires_twenty_and_average_ranks_ties():
    from aletheia.universe.opportunity_evidence import (
        annotate_within_run_percentiles,
        build_opportunity_evidence,
    )

    nineteen = tuple(
        build_opportunity_evidence(
            _feature(index, return_24h=5.0 + index),
            candidate_key=f"candidate-{index}",
            source_cutoff_at="2026-09-02T00:00:00+00:00",
        )
        for index in range(19)
    )
    assert all(
        mechanism.within_run_percentile is None
        for bundle in annotate_within_run_percentiles(nineteen)
        for mechanism in bundle.mechanisms
    )

    twenty = (*nineteen, build_opportunity_evidence(
        _feature(19, return_24h=24.0), candidate_key="candidate-19",
        source_cutoff_at="2026-09-02T00:00:00+00:00",
    ))
    ranked = annotate_within_run_percentiles(twenty)
    continuation = [
        next(item for item in bundle.mechanisms if item.mechanism_id == "continuation")
        for bundle in ranked
    ]
    assert any(item.within_run_percentile is not None for item in continuation)


def _outcomes(count: int = 60):
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        rows.append({
            "outcome_candidate_key": f"candidate-selection-utility:v1:run-{index}:long",
            "scanner_run_id": f"run-{index}",
            "mechanism_id": "continuation",
            "direction": "long",
            "venue": "okx",
            "market_type": "spot",
            "strength_quintile": 5 if index < 25 else 4,
            "horizon": "4h",
            "status": "settled",
            "entry_ts": (start + timedelta(hours=4 * index)).isoformat(),
            "settled_at": (start + timedelta(hours=4 * (index + 1))).isoformat(),
            "symbol": f"COIN{index % 6}/USDT",
            "net_return_pct": 1.0 if index < 25 else 0.2,
            "mae_pct": -0.5,
            "cost_model_version": "v1",
            "min_tradable_edge_pct": 0.05,
            "estimated_fee_pct": 0.1,
            "estimated_spread_slippage_pct": 0.1,
            "estimated_funding_pct": 0.0,
            "mechanism_behavior_version": "universe-opportunity-evidence/v2",
            "outcome_contract_version": "candidate-selection-utility-outcome/v1",
            "cost_assumptions": {
                "execution_fee_pct": 0.06,
                "slippage_bps": 5.0,
                "fallback_spread_bps": 25.0,
            },
        })
    return rows


def test_economic_utility_uses_exact_4h_clustered_shrunk_economics():
    from aletheia.universe.economic_utility import estimate_economic_utility

    cost_assumptions = _outcomes()[0]["cost_assumptions"]
    estimate = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=_outcomes(),
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert estimate.estimate_status == "available"
    assert estimate.strength_quintile == 5
    assert estimate.expected_net_return_pct == pytest.approx((25 * 1.0 + 50 * (25 + 7) / 60) / 75)
    assert estimate.cluster_standard_error_pct == pytest.approx(0.0)
    assert estimate.tail_evidence.mae_p95_pct == pytest.approx(0.5)
    assert estimate.tail_evidence.status == "clear"

    reordered = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=list(reversed(_outcomes())),
        source_cutoff_at="2026-09-02T00:00:00Z",
        cost_assumptions=cost_assumptions,
    )
    assert reordered.source_outcome_fingerprint == estimate.source_outcome_fingerprint

    future = {
        **_outcomes()[0],
        "outcome_candidate_key": "candidate-selection-utility:v1:future:long",
        "scanner_run_id": "future-run",
        "entry_ts": "2026-09-03T00:00:00+00:00",
        "settled_at": "2026-09-03T04:00:00+00:00",
        "net_return_pct": 99.0,
    }
    wrong_cost_version = {
        **_outcomes()[0],
        "outcome_candidate_key": "candidate-selection-utility:v1:wrong-cost:long",
        "scanner_run_id": "wrong-cost-run",
        "cost_model_version": "legacy-cost/v0",
        "net_return_pct": 99.0,
    }
    frozen = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), future, wrong_cost_version],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert frozen.economic_utility_pct is None
    assert frozen.unavailable_reason == "history_economics_incomplete"

    other_venue = [
        {**row, "venue": "bitget", "net_return_pct": 99.0}
        for row in _outcomes()
    ]
    exact_venue = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), *other_venue],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert exact_venue.expected_net_return_pct == estimate.expected_net_return_pct
    assert exact_venue.source_outcome_fingerprint == estimate.source_outcome_fingerprint

    exact_duplicates = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), *_outcomes()],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert exact_duplicates.expected_net_return_pct == estimate.expected_net_return_pct
    wrong_behavior = {
        **_outcomes()[0],
        "outcome_candidate_key": "candidate-selection-utility:v1:wrong-behavior:long",
        "scanner_run_id": "wrong-behavior-run",
        "mechanism_behavior_version": "universe-opportunity-evidence/v0",
        "net_return_pct": 99.0,
    }
    exact_behavior = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), wrong_behavior],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert exact_behavior.source_outcome_fingerprint == estimate.source_outcome_fingerprint

    changed_cost_evidence = [dict(row) for row in _outcomes()]
    changed_cost_evidence[0]["estimated_fee_pct"] = 0.2
    cost_restatement = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=changed_cost_evidence,
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert cost_restatement.source_outcome_fingerprint != estimate.source_outcome_fingerprint

    conflicting = [dict(row) for row in _outcomes()]
    conflicting[0]["net_return_pct"] = 99.0
    conflict = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), conflicting[0]],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert conflict.estimate_status == "unavailable"
    assert conflict.economic_utility_pct is None

    missing_tail = [dict(row) for row in _outcomes()]
    missing_tail[0]["mae_pct"] = None
    unavailable_tail = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=missing_tail,
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert unavailable_tail.estimate_status == "unavailable"
    assert unavailable_tail.tail_evidence.status == "unavailable"

    skipped_missing_tail = {
        **_outcomes()[0],
        "outcome_candidate_key": "candidate-selection-utility:v1:overlap-missing-mae:long",
        "scanner_run_id": "overlap-missing-mae",
        "entry_ts": "2026-07-01T00:10:00+00:00",
        "settled_at": "2026-07-01T04:10:00+00:00",
        "mae_pct": None,
    }
    overlap_unavailable = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=[*_outcomes(), skipped_missing_tail],
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert overlap_unavailable.estimate_status == "unavailable"
    assert overlap_unavailable.tail_evidence.status == "unavailable"

    catastrophic = [dict(row) for row in _outcomes()]
    catastrophic[0]["net_return_pct"] = -20.0
    harmful_tail = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=catastrophic,
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=cost_assumptions,
    )
    assert harmful_tail.tail_evidence.status == "harm"
    assert harmful_tail.economic_utility_pct is None


def test_economic_utility_support_uses_only_retained_clusters():
    from aletheia.universe.economic_utility import estimate_economic_utility

    rows = [{**row, "symbol": "BTC/USDT"} for row in _outcomes()]
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for index, symbol in enumerate(("ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT")):
        rows.append({
            **rows[index],
            "outcome_candidate_key": f"candidate-selection-utility:v1:overlap-{index}:long",
            "scanner_run_id": f"overlap-{index}",
            "entry_ts": (start + timedelta(hours=4 * index, minutes=10)).isoformat(),
            "settled_at": (start + timedelta(hours=4 * (index + 1), minutes=10)).isoformat(),
            "symbol": symbol,
        })

    estimate = estimate_economic_utility(
        candidate_key="candidate-economic",
        mechanism_id="continuation",
        direction="long",
        venue="okx",
        market_type="spot",
        strength_percentile=0.9,
        outcomes=rows,
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=rows[0]["cost_assumptions"],
    )

    assert estimate.parent_independent_n == 60
    assert estimate.child_independent_n == 25
    assert estimate.estimate_status == "unavailable"


def test_candidate_selection_does_not_fill_and_direction_conflict_fails_closed():
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate
    from aletheia.universe.candidate_selection_utility import (
        build_candidate_selection_utility,
        select_admitted_candidates,
    )
    from aletheia.universe.economic_utility import estimate_economic_utility
    from aletheia.universe.opportunity_evidence import build_opportunity_evidence

    cfg = UniverseConfig(max_candidates=30)
    feature = _feature(market_type="perp")
    gate = evaluate_hard_gate(
        feature, cfg, candidate_key="candidate-select", evaluated_at="2026-09-02T00:00:00+00:00"
    )
    evidence = build_opportunity_evidence(
        feature, candidate_key="candidate-select", source_cutoff_at="2026-09-02T00:00:00+00:00"
    )
    outcomes = [{**item, "market_type": "perp"} for item in _outcomes()]
    long = estimate_economic_utility(
        candidate_key="candidate-select", mechanism_id="continuation", direction="long",
        venue="okx",
        market_type="perp", strength_percentile=0.9, outcomes=outcomes,
        source_cutoff_at="2026-09-02T00:00:00+00:00",
        cost_assumptions=outcomes[0]["cost_assumptions"],
    )
    decision = build_candidate_selection_utility(
        hard_gate=gate, opportunity_evidence=evidence, economic_estimates=(long,),

    )
    assert decision.admission_status == "admitted"
    assert select_admitted_candidates((decision,), max_candidates=30) == (decision,)

    primary = next(item for item in evidence.mechanisms if item.mechanism_id == "continuation")
    gap = replace(evidence, mechanisms=(replace(primary, capability_gaps=("missing_fact",)),))
    assert build_candidate_selection_utility(
        hard_gate=gate, opportunity_evidence=gap, economic_estimates=(long,),
    ).admission_status == "observe_only"

    short = replace(long, mechanism_id="reversal", direction="short", economic_utility_pct=long.economic_utility_pct - 0.01)
    both = replace(evidence, mechanisms=(primary, replace(primary, mechanism_id="reversal", direction="short")))
    conflict = build_candidate_selection_utility(
        hard_gate=gate, opportunity_evidence=both, economic_estimates=(long, short),

    )
    assert conflict.admission_status == "direction_conflict"
    long_only = build_candidate_selection_utility(
        hard_gate=gate, opportunity_evidence=both, economic_estimates=(long, short), short_enabled=False,
    )
    assert long_only.admission_status == "admitted"
    assert long_only.primary_direction == "long"


@pytest.mark.parametrize("historical", [False, True])
def test_candidate_selection_shadow_persists_same_t0_full_denominator(historical):
    import json

    from aletheia.storage import repositories as repo
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        load_candidate_selection_outcomes,
    )

    population = [_feature(index, return_24h=5.0 + index) for index in range(20)]
    bundle = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-selection-run",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=3),


        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=(),
    )
    assert bundle.batch.candidate_count == 20
    assert len(bundle.evidence) == 20
    assert len(bundle.outcomes) >= 20
    policy = json.loads(bundle.batch.policy_json)["selection_policy"]
    if historical:
        policy["semantic_manifest"].pop("feature_acquisition")
        for evidence in bundle.evidence:
            payload = json.loads(evidence.compact_output_json)
            payload["evidence_contract"].pop("feature_acquisition_version")
            evidence.compact_output_json = json.dumps(payload)
    assert repo.insert_universe_twin_shadow_bundle(
        bundle.batch, bundle.evidence, outcomes=bundle.outcomes, min_interval_sec=0,
    ) == 20
    priority_keys = repo.list_open_universe_twin_outcome_candidate_keys()
    assert {
        f"{outcome.outcome_candidate_key}:{outcome.horizon}"
        for outcome in bundle.outcomes
    } <= priority_keys
    stored = repo.list_universe_twin_shadow_records("candidate-selection-run")
    payload = json.loads(stored[0]["compact_output_json"])
    assert payload["candidate_selection"]["version"] == "candidate-selection-utility/v2"
    assert payload["evidence_contract"]["shadow_authority_applied"] is False
    diagnostics = {}
    wrong_policy = {"semantic_manifest": {"feature_acquisition": "candidate-feature-acquisition/v2"} if historical else {}}
    assert load_candidate_selection_outcomes(selection_policy=wrong_policy, diagnostics_out=diagnostics) == []
    assert diagnostics["feature_acquisition_excluded"] == 20
    loaded = load_candidate_selection_outcomes(selection_policy=policy)
    assert loaded
    assert {
        row["mechanism_behavior_version"] for row in loaded
    } == {"universe-opportunity-evidence/v2"}
    assert {
        row["outcome_contract_version"] for row in loaded
    } == {"candidate-selection-utility-outcome/v1"}
    assert all(row["cost_assumptions"] for row in loaded)


def test_frozen_training_view_keeps_late_settlement_as_pending_denominator():
    from aletheia.storage import repositories as repo
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        load_candidate_selection_outcomes,
    )

    population = [_feature(index, return_24h=5.0 + index) for index in range(20)]
    bundle = build_candidate_selection_shadow_bundle(
        population, scanner_run_id="candidate-selection-late-settlement",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=3),

        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=(),
    )
    repo.insert_universe_twin_shadow_bundle(
        bundle.batch, bundle.evidence, outcomes=bundle.outcomes, min_interval_sec=0,
    )
    after_cutoff = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-selection-after-cutoff",
        captured_at="2026-09-02T13:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=3),


        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=(),
    )
    repo.insert_universe_twin_shadow_bundle(
        after_cutoff.batch,
        after_cutoff.evidence,
        outcomes=after_cutoff.outcomes,
        min_interval_sec=0,
    )
    after_cutoff_keys = {
        outcome.outcome_candidate_key for outcome in after_cutoff.outcomes
    }
    key = load_candidate_selection_outcomes(selection_policy={"semantic_manifest": {"feature_acquisition": "candidate-feature-acquisition/v2"}})[0]["outcome_candidate_key"]
    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=?, "
        "net_return_pct=4.0, mae_pct=-2.0, cost_model_version='v1' "
        "WHERE outcome_candidate_key=?",
        ("2026-09-03T00:00:00+00:00", key),
    )
    repo.get_db().commit()

    frozen = load_candidate_selection_outcomes(
        selection_policy={"semantic_manifest": {"feature_acquisition": "candidate-feature-acquisition/v2"}},
        limit_batches=0,
        settled_through="2026-09-02T12:00:00+00:00",
    )
    assert frozen[0]["status"] == "pending"
    assert frozen[0]["settled_at"] is None
    assert frozen[0]["net_return_pct"] is None
    assert frozen[0]["mae_pct"] is None
    assert all(row["entry_ts"] < "2026-09-02T12:00:00+00:00" for row in frozen)
    assert not after_cutoff_keys.intersection(
        row["outcome_candidate_key"] for row in frozen
    )


def test_unavailable_target_has_no_outcome_from_legacy_selection():
    import json

    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
    )

    feature = _feature(depth_10bps_usd=None)
    bundle = build_candidate_selection_shadow_bundle(
        [feature],
        scanner_run_id="candidate-selection-legacy-unavailable",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=1),


        action_route_reasons={("feature-0", "okx", "spot", "COIN0/USDT"): None},
        historical_outcomes=(),
    )
    payload = json.loads(bundle.evidence[0].compact_output_json)
    assert payload["candidate_selection"]["hard_gate"]["status"] == "unavailable"
    assert payload["outcome_candidate_keys"] == {}
    assert bundle.outcomes == ()


def test_target_selection_deduplicates_symbol_by_economic_rank(monkeypatch):
    import json
    from dataclasses import replace

    import aletheia.universe.candidate_selection_shadow as shadow_module

    spot = _feature(symbol="SAME/USDT", market_type="spot", feature_snapshot_id="spot")
    perp = _feature(
        index=1, symbol="SAME/USDT", market_type="perp",
        feature_snapshot_id="perp", funding_rate=0.0,
    )
    other = _feature(index=2, symbol="OTHER/USDT", feature_snapshot_id="other")
    population = [spot, perp, other]
    utilities = {"spot": 1.0, "perp": 3.0, "other": 2.0}

    def admitted(decisions, **_kwargs):
        ranked = [
            replace(
                decision,
                economic_utility_pct=utilities[feature["feature_snapshot_id"]],
                admission_status="admitted", admission_basis="measured", startup_reason=None,
            )
            for decision, feature in zip(decisions, population, strict=True)
        ]
        return tuple(sorted(ranked, key=lambda item: -float(item.economic_utility_pct)))

    monkeypatch.setattr(shadow_module, "select_admitted_candidates", admitted)
    bundle = shadow_module.build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-selection-market-dedup",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=1),


        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=(),
        selection_policy={"preferred_market_type": "spot"},
    )
    selected = [
        row.symbol
        for row in bundle.evidence
        if json.loads(row.compact_output_json)["target_selected"]
    ]
    assert selected == ["SAME/USDT"]


def test_shadow_compacts_supported_multi_mechanism_record():
    import json

    historical = [
        {
            **row,
            "outcome_candidate_key": f"{row['outcome_candidate_key']}:{mechanism}:{direction}",
            "mechanism_id": mechanism,
            "direction": direction,
            "market_type": "perp",
        }
        for mechanism in ("continuation", "reversal", "positioning_forced_flow")
        for direction in ("long", "short")
        for row in _outcomes()
    ]
    population = [
        _feature(
            index,
            market_type="perp",
            return_24h=5.0 + index,
            rsi_14=75.0,
            funding_rate=0.001,
            oi_delta_1h=10.0,
        )
        for index in range(20)
    ]
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
    )

    bundle = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="candidate-selection-multi-mechanism",
        captured_at="2026-09-02T00:00:00+00:00",
        universe_config=UniverseConfig(max_candidates=3),


        action_route_reasons={
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        historical_outcomes=historical,
    )
    assert len(bundle.evidence) == 20
    payload = json.loads(bundle.evidence[0].compact_output_json)
    assert {
        item["mechanism_id"]
        for item in payload["candidate_selection"]["opportunity_evidence"]["mechanisms"]
    } == {"continuation", "reversal", "positioning_forced_flow", "information_catalyst"}
    assert max(len(row.compact_output_json.encode()) for row in bundle.evidence) < 6 * 1024


def test_candidate_selection_authority_projects_without_qualification():
    import json

    from aletheia.storage import repositories as repo
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
    )
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        candidate_selection_training_cutoff,
        target_candidates_from_bundle,
    )

    population = [_feature(index, return_24h=5.0 + index) for index in range(20)]
    legacy_scored = [
        {**item, "scanner_family": "momentum", "scanner_direction": "long", "total_score": 50.0 + index}
        for index, item in enumerate(population)
    ]
    universe_config = UniverseConfig(max_candidates=3)
    selection_policy = {
        **candidate_selection_policy_identity(
            universe_config,
            family_spread_tolerances={"momentum": 1.15},
            family_spread_evidence={
                "source_cutoff_at": "2026-09-01T00:00:00+00:00",
                "source_fingerprint": "spread-source",
            },
        ),
        "cost_assumptions": {
            "execution_fee_pct": 0.06,
            "slippage_bps": 5.0,
        },
    }
    kwargs = {
        "population": population,
        "scanner_run_id": "candidate-selection-authority-run",
        "captured_at": "2026-09-02T00:00:00+00:00",
        "universe_config": universe_config,


        "action_route_reasons": {
            (item["feature_snapshot_id"], item["venue"], item["market_type"], item["symbol"]): None
            for item in population
        },
        "historical_outcomes": _outcomes(),
        "selection_policy": selection_policy,
    }
    shadow = build_candidate_selection_shadow_bundle(**kwargs)
    assert shadow.batch.policy_epoch_id
    cost_snapshot = json.loads(shadow.evidence[0].compact_output_json)[
        "evidence_contract"
    ]["cost_assumptions"]
    assert cost_snapshot == {
        "execution_fee_pct": 0.06,
        "fallback_spread_bps": kwargs["universe_config"].max_spread_bps,
        "slippage_bps": 5.0,
    }
    changed_config = UniverseConfig(max_candidates=4)
    changed_policy = build_candidate_selection_shadow_bundle(**{
        **kwargs,
        "universe_config": changed_config,
        "selection_policy": {
            **candidate_selection_policy_identity(changed_config),
            "cost_assumptions": selection_policy["cost_assumptions"],
        },
    })
    assert changed_policy.batch.policy_epoch_id != shadow.batch.policy_epoch_id

    authority = build_candidate_selection_shadow_bundle(
        **kwargs,
        authority_applied=True,
    )
    projected = target_candidates_from_bundle(authority, legacy_scored)
    assert 0 < len(projected) <= 3
    assert all(item["candidate_selection_policy"] == "candidate_selection_utility_v1" for item in projected)
    assert all(item["admission_basis"] in {"measured", "startup"} for item in projected)
    unavailable_population = [
        {**item, "spread_bps": None} for item in population
    ]
    unavailable_legacy = [
        {**item, "spread_bps": None} for item in legacy_scored
    ]
    unavailable_authority = build_candidate_selection_shadow_bundle(
        **{
            **kwargs,
            "population": unavailable_population,


            "estimator_training_cutoff": kwargs["captured_at"],
        },
        authority_applied=True,
        qualification_evidence={
            "verdict": "positive",
            "promotion_eligible": True,
            "policy_epoch_id": shadow.batch.policy_epoch_id,
            "selection_policy": kwargs["selection_policy"],
            "blocking_reasons": [],
            "invalid_reasons": [],
        },
    )
    assert unavailable_authority.batch.policy_epoch_id == shadow.batch.policy_epoch_id
    assert target_candidates_from_bundle(unavailable_authority, unavailable_legacy) == []
    assert repo.insert_universe_twin_shadow_bundle(
        authority.batch,
        authority.evidence,
        outcomes=authority.outcomes,
        min_interval_sec=0,
    ) == 20
    assert candidate_selection_training_cutoff(
        selection_policy=selection_policy,
    ) == kwargs["captured_at"]
    changed_cost_policy = {
        **selection_policy,
        "cost_assumptions": {
            **selection_policy["cost_assumptions"],
            "execution_fee_pct": 0.07,
        },
    }
    assert candidate_selection_training_cutoff(
        selection_policy=changed_cost_policy,
    ) is None
    columns = {
        row[1] for row in repo.get_db().execute("PRAGMA table_info(universe_members)")
    }
    assert {
        "candidate_selection_policy",
        "candidate_selection_policy_epoch",
        "candidate_selection_utility_pct",
    } <= columns


def test_candidate_selection_identity_excludes_legacy_regime_policy():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
    )

    control = AletheiaConfig()
    changed = control.model_copy(deep=True)
    changed.regime.min_hold_periods += 1

    control_identity = candidate_selection_policy_identity(control)
    changed_identity = candidate_selection_policy_identity(changed)
    assert control_identity == changed_identity


def test_candidate_selection_epoch_identity_includes_cost_and_route():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.candidate_selection_authority import (
        candidate_selection_policy_identity,
    )

    cfg = AletheiaConfig()
    before = candidate_selection_policy_identity(cfg)
    cfg.trading.execution_fee_pct += 0.01
    cfg.trading.slippage_bps += 1.0
    after = candidate_selection_policy_identity(cfg)

    assert before["cost_assumptions"] != after["cost_assumptions"]

    before = candidate_selection_policy_identity(cfg)
    cfg.exchange.enabled_markets["usdt_futures"] = False
    after = candidate_selection_policy_identity(cfg)

    assert before["action_route"] != after["action_route"]

    before = candidate_selection_policy_identity(cfg)
    cfg.universe.explore_ratio = 0.25
    after = candidate_selection_policy_identity(cfg)

    assert before == after

    assert candidate_selection_policy_identity(
        cfg,
        family_spread_tolerances={"momentum": 1.0},
    ) == candidate_selection_policy_identity(
        cfg,
        family_spread_tolerances={"momentum": 1.1},
    )






def test_candidate_selection_fixed_look_can_be_positive_for_owner_admission(monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        FilterCandidateOutcome,
        RuntimeSession,
        UniverseTwinShadowBatch,
        UniverseTwinShadowEvidence,
    )
    from aletheia.universe.candidate_selection_evaluation import (
        evaluate_candidate_selection_policy,
    )
    from aletheia.universe.candidate_selection_shadow import (
        CANDIDATE_SELECTION_METRIC_VERSION,
        CANDIDATE_SELECTION_OUTCOME_VERSION,
    )

    start = datetime(2026, 9, 24, tzinfo=timezone.utc)
    cutoff = start.isoformat()
    policy_epoch = "candidate-selection-utility:fixed-policy"
    behavior_hash = "f" * 64
    repo.insert_runtime_session(RuntimeSession(
        session_id="candidate-runtime",
        boot_commit="candidate-deployed-commit",
        behavior_fingerprint="candidate-runtime-behavior",
    ))
    repo.insert_runtime_session(RuntimeSession(
        session_id="candidate-runtime-after-nonsemantic-deploy",
        boot_commit="candidate-unrelated-deploy-commit",
        behavior_fingerprint="candidate-runtime-behavior",
    ))
    for index in range(50):
        captured = start + timedelta(hours=index * 4)
        run_id = f"fixed-look-{index:02d}"
        runtime_session_id = (
            "candidate-runtime-after-nonsemantic-deploy"
            if index >= 25 else "candidate-runtime"
        )
        runtime_commit = (
            "candidate-unrelated-deploy-commit"
            if index >= 25 else "candidate-deployed-commit"
        )
        records = []
        outcomes = []
        for candidate_index, (target_selected, legacy_selected, net) in enumerate(
            ((True, False, 1.0), (False, True, 0.2))
        ):
            symbol = f"COIN{index % 5}{candidate_index}/USDT"
            candidate_key = f"candidate-selection-utility:v1:{run_id}:{candidate_index}"
            outcome_key = f"{candidate_key}:long"
            payload = {
                "candidate_selection": {
                    "primary_direction": "long",
                    "legacy_control": {
                        "primary_direction": "long",
                        "authority_selected": legacy_selected,
                    },
                },
                "target_selected": target_selected,
                "outcome_candidate_keys": {"long": outcome_key},
                "evidence_contract": {
                    "shadow_authority_applied": False,
                    "promotion_eligible": False,
                    "estimator_training_cutoff": cutoff,
                    "behavior_contract_hash": behavior_hash,
                    "policy_epoch_id": policy_epoch,
                    "outcome_contract_version": CANDIDATE_SELECTION_OUTCOME_VERSION,
                    "evidence_sampling_contract": "candidate-selection-evidence-cadence/v2",
                },
            }
            records.append(UniverseTwinShadowEvidence(
                id=f"fixed-{index:02d}-{candidate_index}",
                scanner_run_id=run_id,
                candidate_key=candidate_key,
                candidate_index=candidate_index,
                symbol=symbol,
                market_type="spot",
                experiment_id="candidate-selection-fixed-look",
                policy_epoch_id=policy_epoch,
                behavior_contract_hash=behavior_hash,
                primary_horizon="4h",
                outcome_contract_version=CANDIDATE_SELECTION_OUTCOME_VERSION,
                metric_version=CANDIDATE_SELECTION_METRIC_VERSION,
                outcome_candidate_key=outcome_key if target_selected else None,
                authority_total_score=50.0,
                authority_selected=legacy_selected,
                shadow_pessimistic_selected=target_selected,
                shadow_valid=True,
                compact_output_json=json.dumps(payload, sort_keys=True),
                input_json="{}",
                record_hash=f"{index:02d}{candidate_index}".ljust(64, "0"),
                captured_at=captured.isoformat(),
            ))
            due = captured + timedelta(hours=4)
            gross = net + 0.2
            outcomes.append(FilterCandidateOutcome(
                outcome_candidate_key=outcome_key,
                venue="okx",
                symbol=symbol,
                market_type="spot",
                direction="long",
                scanner_family="candidate_selection_utility",
                horizon="4h",
                allocation_selected=target_selected,
                entry_price_usd=100.0,
                entry_spread_bps=5.0,
                entry_ts=captured.isoformat(),
                due_at=due.isoformat(),
                settled_at=(due + timedelta(minutes=1)).isoformat(),
                    exit_price_usd=100.0 * (1.0 + gross / 100.0),
                    gross_return_pct=gross,
                    directional_return_pct=gross,
                estimated_fee_pct=0.1,
                estimated_spread_slippage_pct=0.1,
                estimated_funding_pct=0.0,
                net_return_pct=net,
                min_tradable_edge_pct=0.05,
                    mfe_pct=gross + 0.2,
                mae_pct=None if index == 0 and target_selected else -0.4,
                settlement_price_source="horizon_ohlcv",
                settlement_lag_sec=60.0,
                cost_model_version="v1",
                producer_runtime_session_id=runtime_session_id,
                producer_commit=runtime_commit,
                producer_behavior_fingerprint="candidate-runtime-behavior",
                settlement_runtime_session_id=runtime_session_id,
                settlement_producer_commit=runtime_commit,
                settlement_behavior_fingerprint="candidate-runtime-behavior",
                status="settled",
            ))
        batch = UniverseTwinShadowBatch(
            scanner_run_id=run_id,
            experiment_id="candidate-selection-fixed-look",
            policy_epoch_id=policy_epoch,
            behavior_contract_hash=behavior_hash,
            record_version="candidate-selection-utility-record/v3",
            authority_behavior_version="universe-step-v3",
            shadow_behavior_version="candidate-selection-utility/v1",
            allocation_replay_contract="candidate-selection-utility-allocation/v1",
            primary_horizon="4h",
            outcome_contract_version=CANDIDATE_SELECTION_OUTCOME_VERSION,
            feature_source_fingerprint="source-v1",
            metric_version=CANDIDATE_SELECTION_METRIC_VERSION,
            allocator_random_state_json="null",
            policy_json=json.dumps({
                "universe": {"max_candidates": 2},
                "selection_policy": {"semantic_manifest": {"estimator": "v2"}},
            }),
            allocation_context_json=json.dumps({
                "evidence_sampling_contract": "candidate-selection-evidence-cadence/v2",
                "minimum_interval_sec": 4 * 60 * 60,
            }),
            candidate_count=2,
            blocking_reasons_json='["owner_admission_required"]',
            batch_hash=f"batch-{index:02d}".ljust(64, "0"),
            captured_at=captured.isoformat(),
        )
        if index == 0:
            # A prior frozen policy remains immutable evidence but cannot
            # contaminate the latest epoch after selection settings rotate.
            old_records = []
            old_outcomes = []
            for candidate_index, record in enumerate(records):
                payload = json.loads(record.compact_output_json)
                old_candidate_key = f"candidate-selection-utility:v1:old-policy:{candidate_index}"
                old_outcome_key = f"{old_candidate_key}:long"
                payload["outcome_candidate_keys"] = {"long": old_outcome_key}
                payload["evidence_contract"]["policy_epoch_id"] = (
                    "candidate-selection-utility:old-policy"
                )
                old_records.append(record.model_copy(update={
                    "id": f"old-policy-{candidate_index}",
                    "scanner_run_id": "fixed-look-old-policy",
                    "candidate_key": old_candidate_key,
                    "outcome_candidate_key": old_outcome_key if candidate_index == 0 else None,
                    "policy_epoch_id": "candidate-selection-utility:old-policy",
                    "compact_output_json": json.dumps(payload, sort_keys=True),
                    "record_hash": f"old-policy-{candidate_index}".ljust(64, "0"),
                    "captured_at": (start - timedelta(days=1)).isoformat(),
                }))
                old_outcomes.append(outcomes[candidate_index].model_copy(update={
                    "id": f"oldoutcome{candidate_index:02d}",
                    "outcome_candidate_key": old_outcome_key,
                    "entry_ts": (start - timedelta(days=1)).isoformat(),
                    "due_at": (start - timedelta(hours=20)).isoformat(),
                    "settled_at": (start - timedelta(hours=19, minutes=59)).isoformat(),
                }))
            old_batch = batch.model_copy(update={
                "scanner_run_id": "fixed-look-old-policy",
                "policy_epoch_id": "candidate-selection-utility:old-policy",
                "batch_hash": "old-policy-batch".ljust(64, "0"),
                "captured_at": (start - timedelta(days=1)).isoformat(),
            })
            assert repo.insert_universe_twin_shadow_bundle(
                old_batch, old_records, outcomes=old_outcomes, min_interval_sec=0,
            ) == 2
        assert repo.insert_universe_twin_shadow_bundle(
            batch, records, outcomes=outcomes, min_interval_sec=0,
        ) == 2

    incomplete_tail = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=10)).isoformat(),
    )
    assert incomplete_tail["verdict"] == "insufficient_data"
    assert incomplete_tail["complete_cluster_n"] == 49

    # A later complete cluster cannot substitute for missing evidence inside
    # the first outcome-blind T0 exposure prefix.
    substitute_captured = start + timedelta(days=11)
    substitute_records = []
    substitute_outcomes = []
    for candidate_index, record in enumerate(records):
        payload = json.loads(record.compact_output_json)
        candidate_key = f"candidate-selection-utility:v1:substitute:{candidate_index}"
        outcome_key = f"{candidate_key}:long"
        payload["outcome_candidate_keys"] = {"long": outcome_key}
        substitute_records.append(record.model_copy(update={
            "id": f"substitute-{candidate_index}",
            "scanner_run_id": "fixed-look-substitute",
            "candidate_key": candidate_key,
            "outcome_candidate_key": outcome_key if candidate_index == 0 else None,
            "compact_output_json": json.dumps(payload, sort_keys=True),
            "record_hash": f"substitute-{candidate_index}".ljust(64, "0"),
            "captured_at": substitute_captured.isoformat(),
        }))
        substitute_outcomes.append(outcomes[candidate_index].model_copy(update={
            "id": f"substitute-outcome-{candidate_index}",
            "outcome_candidate_key": outcome_key,
            "entry_ts": substitute_captured.isoformat(),
            "due_at": (substitute_captured + timedelta(hours=4)).isoformat(),
            "settled_at": (substitute_captured + timedelta(hours=4, minutes=1)).isoformat(),
            "mae_pct": -0.4,
        }))
    substitute_batch = batch.model_copy(update={
        "scanner_run_id": "fixed-look-substitute",
        "batch_hash": "substitute-batch".ljust(64, "0"),
        "captured_at": substitute_captured.isoformat(),
    })
    assert repo.insert_universe_twin_shadow_bundle(
        substitute_batch,
        substitute_records,
        outcomes=substitute_outcomes,
        min_interval_sec=0,
    ) == 2
    still_incomplete = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=12)).isoformat(),
    )
    assert still_incomplete["verdict"] == "insufficient_data"
    assert still_incomplete["fixed_look_cluster_n"] == 49

    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET settled_at=?, "
        "producer_commit='unattested-future-commit' "
        "WHERE outcome_candidate_key=?",
        (
            (start + timedelta(days=20)).isoformat(),
            "candidate-selection-utility:v1:fixed-look-00:0:long",
        ),
    )
    before_settlement = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=10)).isoformat(),
    )
    assert before_settlement["verdict"] == "insufficient_data"
    assert "runtime_provenance_unattested" not in before_settlement["invalid_reasons"]
    assert before_settlement["outcome_exclusion_counts"]["settlement_after_as_of"] == 1

    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET settled_at=?, producer_commit=?, mae_pct=-0.4 "
        "WHERE outcome_candidate_key=?",
        (
            outcomes[0].settled_at,
            "candidate-deployed-commit",
            "candidate-selection-utility:v1:fixed-look-00:0:long",
        ),
    )

    report = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=10)).isoformat(),
    )
    assert report["verdict"] == "positive", report
    assert report["policy_epoch_id"] == policy_epoch
    assert report["raw_scanner_run_n"] == 50
    assert report["independent_policy_unit_n"] == 50
    assert report["expected_cluster_n"] == 50
    assert report["qualification_eligible"] is True
    assert report["promotion_eligible"] is True
    assert report["owner_admission_required"] is True
    assert report["runtime_provenance"]["producer_commits"] == [
        "candidate-deployed-commit",
        "candidate-unrelated-deploy-commit",
    ]

    late_captured = start + timedelta(days=12)
    late_records = []
    late_outcomes = []
    for candidate_index, record in enumerate(records):
        payload = json.loads(record.compact_output_json)
        late_candidate_key = f"candidate-selection-utility:v1:late:{candidate_index}"
        late_outcome_key = f"{late_candidate_key}:long"
        payload["outcome_candidate_keys"] = {"long": late_outcome_key}
        late_records.append(record.model_copy(update={
            "id": f"late-policy-{candidate_index}",
            "scanner_run_id": "fixed-look-late-pending",
            "candidate_key": late_candidate_key,
            "outcome_candidate_key": late_outcome_key if candidate_index == 0 else None,
            "compact_output_json": json.dumps(payload, sort_keys=True),
            "record_hash": f"late-policy-{candidate_index}".ljust(64, "0"),
            "captured_at": late_captured.isoformat(),
        }))
        late_outcomes.append(outcomes[candidate_index].model_copy(update={
            "id": f"lateoutcome{candidate_index:02d}",
            "outcome_candidate_key": late_outcome_key,
            "entry_ts": late_captured.isoformat(),
            "due_at": (late_captured + timedelta(hours=4)).isoformat(),
            "settled_at": None,
            "gross_return_pct": None,
            "directional_return_pct": None,
            "net_return_pct": None,
            "mfe_pct": None,
            "mae_pct": None,
            "status": "pending",
        }))
    late_batch = batch.model_copy(update={
        "scanner_run_id": "fixed-look-late-pending",
        "batch_hash": "late-policy-batch".ljust(64, "0"),
        "captured_at": late_captured.isoformat(),
    })
    assert repo.insert_universe_twin_shadow_bundle(
        late_batch, late_records, outcomes=late_outcomes, min_interval_sec=0,
    ) == 2
    listed_scanner_runs = []
    list_records = repo.list_universe_twin_shadow_records

    def track_record_reads(scanner_run_id):
        listed_scanner_runs.append(scanner_run_id)
        return list_records(scanner_run_id)

    monkeypatch.setattr(repo, "list_universe_twin_shadow_records", track_record_reads)
    frozen = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=13)).isoformat(),
    )
    assert frozen["verdict"] == "positive", frozen
    assert frozen["raw_scanner_run_n"] == 52
    assert frozen["independent_policy_unit_n"] == 52
    assert frozen["fixed_look_cluster_n"] == 50
    assert frozen["full_arm_outcome_coverage"] == 1.0
    assert "fixed-look-late-pending" not in listed_scanner_runs

    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=?, "
        "producer_commit='unattested-late-commit' WHERE outcome_candidate_key=?",
        (
            (late_captured + timedelta(hours=4, minutes=1)).isoformat(),
            "candidate-selection-utility:v1:late:0:long",
        ),
    )
    still_frozen = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=13)).isoformat(),
    )
    assert still_frozen["verdict"] == "positive", still_frozen

    repo.get_db().execute(
        "UPDATE filter_candidate_outcomes SET producer_commit='unattested-commit' "
        "WHERE outcome_candidate_key=?",
        ("candidate-selection-utility:v1:fixed-look-01:0:long",),
    )
    invalid = evaluate_candidate_selection_policy(
        as_of_at=(start + timedelta(days=10)).isoformat(),
    )
    assert invalid["verdict"] == "invalid"
    assert "runtime_provenance_unattested" in invalid["invalid_reasons"]

    historical = evaluate_candidate_selection_policy(
        as_of_at=(start - timedelta(hours=12)).isoformat(),
    )
    assert historical["policy_epoch_id"] == "candidate-selection-utility:old-policy"


def test_startup_requires_genuine_scarcity_and_cutoff_safe_settlement():
    from aletheia.universe.economic_utility import estimate_economic_utility
    row = _outcomes(1)[0]
    def estimate(rows, cutoff="2026-09-02T00:00:00+00:00"):
        return estimate_economic_utility(
            candidate_key="x", mechanism_id="continuation", direction="long", venue="okx",
            market_type="spot", strength_percentile=None, outcomes=rows,
            source_cutoff_at=cutoff, cost_assumptions=row["cost_assumptions"],
        )
    assert estimate([]).unavailable_reason == "no_history"
    assert estimate([row]).unavailable_reason == "parent_sample_insufficient"
    for changes, reason in (
        ({"outcome_candidate_key": None}, "history_identity_invalid"),
        ({"entry_ts": "bad"}, "history_time_invalid"),
        ({"mae_pct": None}, "history_economics_incomplete"),
        ({"estimated_fee_pct": None}, "history_economics_incomplete"),
        ({"net_return_pct": -20}, "tail_harm"),
        ({"status": "pending"}, "settlement_overdue"),
    ):
        result = estimate([{**row, **changes}])
        assert result.unavailable_reason == reason
        assert result.economic_utility_pct is None
    late = {**row, "settled_at": "2026-07-01T05:00:00+00:00"}
    assert estimate([late], "2026-07-01T03:00:00+00:00").unavailable_reason == "parent_sample_insufficient"
    assert estimate([late], "2026-07-01T04:00:00+00:00").unavailable_reason == "settlement_overdue"
    assert estimate([late], "2026-07-01T05:00:00+00:00").unavailable_reason == "parent_sample_insufficient"


def test_startup_projection_measured_first_and_capacity():
    from aletheia.universe.candidate_selection_shadow import build_candidate_selection_shadow_bundle, target_candidates_from_bundle
    population = [_feature(0), _feature(1, venue="bitget")]
    def project(history, capacity):
        bundle = build_candidate_selection_shadow_bundle(
            population, scanner_run_id="startup", captured_at="2026-09-02T00:00:00+00:00",
            universe_config=UniverseConfig(max_candidates=capacity),
            action_route_reasons={}, historical_outcomes=history, authority_applied=True,
            selection_policy={"cost_assumptions": {"execution_fee_pct": .06, "slippage_bps": 5.0}},
        )
        return target_candidates_from_bundle(bundle, population)
    cold = project([], 2)
    assert len(cold) == 2
    assert all(item["admission_basis"] == "startup" and item["candidate_selection_utility_pct"] is None for item in cold)
    # A tiny cross-section has no percentile; mature history must not be called scarce.
    assert [item["venue"] for item in project(_outcomes(), 2)] == ["bitget"]
    assert project([{**_outcomes(1)[0], "net_return_pct": -20}], 2)[0]["venue"] == "bitget"
    population[:] = [_feature(index, return_24h=5.0 + index % 20,
                             venue="okx" if index < 20 else "bitget") for index in range(40)]
    mixed = project(_outcomes(), 40)
    measured = [item for item in mixed if item["admission_basis"] == "measured"]
    startup = [item for item in mixed if item["admission_basis"] == "startup"]
    assert measured and startup
    assert mixed == measured + startup
    assert project(_outcomes(), len(measured)) == measured


def test_startup_direction_conflict_and_zero_signal_do_not_hide_economic_rejection():
    from dataclasses import replace
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate
    from aletheia.universe.opportunity_evidence import build_opportunity_evidence
    from aletheia.universe.economic_utility import estimate_economic_utility
    from aletheia.universe.candidate_selection_utility import build_candidate_selection_utility
    cutoff = "2026-09-02T00:00:00+00:00"
    feature = _feature(market_type="perp")
    gate = evaluate_hard_gate(feature, UniverseConfig(), candidate_key="x", evaluated_at=cutoff)
    evidence = build_opportunity_evidence(feature, candidate_key="x", source_cutoff_at=cutoff)
    long = next(item for item in evidence.mechanisms if item.mechanism_id == "continuation")
    short = replace(long, mechanism_id="reversal", direction="short", pessimistic_strength=0.0)
    long_estimate = estimate_economic_utility(candidate_key="x", mechanism_id="continuation", direction="long",
        venue="okx", market_type="perp", strength_percentile=None, source_cutoff_at=cutoff)
    short_estimate = replace(long_estimate, mechanism_id="reversal", direction="short")
    def decide(short, short_estimate):
        return build_candidate_selection_utility(hard_gate=gate,
            opportunity_evidence=replace(evidence, mechanisms=(long, short)),
            economic_estimates=(long_estimate, short_estimate), )
    assert decide(short, short_estimate).admission_basis == "startup"
    assert decide(replace(short, pessimistic_strength=1.0), short_estimate).admission_status == "direction_conflict"
    assert decide(short, replace(short_estimate, unavailable_reason="tail_harm")).admission_status == "unavailable"


@pytest.mark.parametrize('reason', ['channel_event_not_observed', 'channel_stale'])
def test_missing_required_fast_channel_cannot_authorize_order_intent(monkeypatch, reason):
    from tests.test_entry_activation import _persist_observed_plan
    from aletheia.storage import repositories as repo
    from aletheia.trading.entry_activation import build_entry_trigger_activation
    from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent

    plan, ready, now, _ = _persist_observed_plan(monkeypatch, 'OPPOSING_BBO_IOC')
    activation = build_entry_trigger_activation(plan, ready, now=now)
    repo.record_entry_plan_monitoring(plan.plan_id, {
        'state': 'unavailable', 'stream_generation_id': ready.stream_generation_id,
        'subscription_generation_id': ready.subscription_generation_id,
        'required_channels': ['quote', 'trade', 'depth', 'candle'],
        'channels': {'depth': {'state': 'unavailable', 'reason': reason}},
    })
    with pytest.raises(ValueError, match='monitoring_mismatch'):
        repo.bind_entry_trigger_activation(activation, expected_activation_id=None, now=now)
    with pytest.raises(ValueError, match='activation_required'):
        build_entry_plan_order_intent(plan, bid=ready.bid, ask=ready.ask,
            observed_at=now.isoformat(), source_observed_at=now.isoformat(), now=now)
    assert repo.get_trade_proposal_by_entry_plan(plan.plan_id) is None


def test_economic_utility_reason_classification_is_exhaustive():
    """Every reason the estimator can emit belongs to exactly one class.

    Admission reads these sets to decide whether a startup bypass is lawful, so
    an unclassified reason is not a cosmetic gap: it lands in the strict branch
    and stalls admission silently (that is how `history_economics_incomplete`
    froze Candidate admission at zero). This parses the producing module instead
    of restating the list, so adding a reason there without classifying it fails
    here rather than in production.
    """
    import ast
    from pathlib import Path

    from aletheia.universe import economic_utility as eu

    source = Path(eu.__file__).read_text(encoding="utf-8")
    emitted: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        # `failures.setdefault(...).add("reason")`
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add" and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            emitted.add(node.args[0].value)
        # `reason = "reason"`
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if any(isinstance(t, ast.Name) and t.id == "reason" for t in node.targets):
                emitted.add(node.value.value)

    assert emitted, "reason extraction found nothing — the AST shapes changed"
    classes = {
        "absent": eu.EVIDENCE_ABSENT_REASONS,
        "rejection": eu.MEASURED_REJECTION_REASONS,
        "unmatched": eu.EVIDENCE_UNMATCHED_REASONS,
        "integrity": eu.EVIDENCE_INTEGRITY_FAULT_REASONS,
    }
    unclassified = emitted - eu.ECONOMIC_UNAVAILABLE_REASONS
    assert not unclassified, (
        f"reasons emitted but not classified: {sorted(unclassified)} — add each to exactly one of "
        "EVIDENCE_ABSENT_REASONS (not measured yet), MEASURED_REJECTION_REASONS (measured and refused), "
        "EVIDENCE_UNMATCHED_REASONS (cohort mature but this Candidate cannot be placed onto it), "
        "or EVIDENCE_INTEGRITY_FAULT_REASONS (stored evidence self-contradictory)"
    )
    stale = eu.ECONOMIC_UNAVAILABLE_REASONS - emitted
    assert not stale, f"classified reasons no longer emitted: {sorted(stale)}"
    for name, members in classes.items():
        others = set().union(*(v for k, v in classes.items() if k != name))
        assert not (members & others), f"{name} overlaps another class: {sorted(members & others)}"
    # The distinction the classification exists to enforce.
    assert "tail_harm" in eu.MEASURED_REJECTION_REASONS
    assert not eu.reason_proves_evidence_absent("tail_harm")
    assert eu.reason_proves_evidence_absent("history_economics_incomplete")
    # Mature parent history must never be called scarce: only reachable once the
    # parent cohort is supported, so a startup bypass would evade its economics.
    assert "percentile_unavailable" in eu.EVIDENCE_UNMATCHED_REASONS
    assert not eu.reason_proves_evidence_absent("percentile_unavailable")
    assert not eu.reason_proves_evidence_absent("unknown_future_reason")
    assert not eu.reason_proves_evidence_absent(None)


def test_incomplete_cost_history_admits_via_startup_instead_of_stalling():
    """Missing cost fields are absent evidence, not a measured refusal.

    Regression: a cohort whose outcomes lacked cost fields reported
    `history_economics_incomplete`, which the consumer's literal scarcity set did
    not list, so a hard-gate-eligible actionable Candidate reached
    `unavailable` and admission stayed at zero indefinitely.
    """
    from aletheia.universe.economic_utility import estimate_economic_utility

    row = _outcomes(1)[0]

    def estimate(rows):
        return estimate_economic_utility(
            candidate_key="x", mechanism_id="continuation", direction="long", venue="okx",
            market_type="spot", strength_percentile=None, outcomes=rows,
            source_cutoff_at="2026-09-02T00:00:00+00:00", cost_assumptions=row["cost_assumptions"],
        )

    incomplete = estimate([{**row, "estimated_funding_pct": None}])
    assert incomplete.unavailable_reason == "history_economics_incomplete"
    assert incomplete.economic_utility_pct is None
    # The estimate stays unavailable; what changes is that admission may now treat
    # it as unmeasured and fall back to the startup basis.
    from aletheia.universe.economic_utility import reason_proves_evidence_absent
    assert reason_proves_evidence_absent(incomplete.unavailable_reason)
    assert not reason_proves_evidence_absent(estimate([{**row, "net_return_pct": -20}]).unavailable_reason)


def test_admission_consumes_estimator_classification_not_its_own_literals():
    """Admission must admit on absent evidence and refuse a measured rejection.

    This binds the consumer, not just the data: reverting the startup gate to a
    literal scarcity set makes `history_economics_incomplete` stall admission
    again, and this test fails. `settlement_overdue` covers the second reason that
    froze the live runtime.
    """
    from dataclasses import replace
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate
    from aletheia.universe.opportunity_evidence import build_opportunity_evidence
    from aletheia.universe.economic_utility import (
        EVIDENCE_ABSENT_REASONS,
        MEASURED_REJECTION_REASONS,
        EVIDENCE_UNMATCHED_REASONS,
        EVIDENCE_INTEGRITY_FAULT_REASONS,
        estimate_economic_utility,
    )
    from aletheia.universe.candidate_selection_utility import build_candidate_selection_utility

    cutoff = "2026-09-02T00:00:00+00:00"
    feature = _feature(market_type="perp")
    gate = evaluate_hard_gate(feature, UniverseConfig(), candidate_key="x", evaluated_at=cutoff)
    evidence = build_opportunity_evidence(feature, candidate_key="x", source_cutoff_at=cutoff)
    long = next(item for item in evidence.mechanisms if item.mechanism_id == "continuation")
    base = estimate_economic_utility(candidate_key="x", mechanism_id="continuation", direction="long",
        venue="okx", market_type="perp", strength_percentile=None, source_cutoff_at=cutoff)

    def decide(reason):
        return build_candidate_selection_utility(
            hard_gate=gate,
            opportunity_evidence=replace(evidence, mechanisms=(long,)),
            economic_estimates=(replace(base, unavailable_reason=reason),),
        )

    # Hard-gate-eligible + actionable + unmeasured => startup admission is lawful.
    for reason in sorted(EVIDENCE_ABSENT_REASONS):
        decision = decide(reason)
        assert decision.admission_status == "admitted", f"{reason} must not stall admission"
        assert decision.admission_basis == "startup"
        assert decision.startup_reason == reason
        assert decision.economic_utility_pct is None

    # Measured refusal and self-contradictory evidence must never be bypassed.
    for reason in sorted(MEASURED_REJECTION_REASONS | EVIDENCE_UNMATCHED_REASONS
                         | EVIDENCE_INTEGRITY_FAULT_REASONS):
        decision = decide(reason)
        assert decision.admission_status == "unavailable", f"{reason} must not be bypassed"
        assert decision.admission_basis is None
    # An unclassified reason fails closed.
    assert decide("unknown_future_reason").admission_status == "unavailable"


def test_no_module_reimplements_the_reason_classification_from_literals():
    """The classification has exactly one owner.

    Four modules had each hardcoded the same three-literal scarcity set. Admission
    consulted one, the shadow target projection a second, the Opportunity context
    validator a third, so fixing one site left the others contradicting it: the
    live runtime admitted 21 Candidates and then failed the whole scan with
    `candidate_opportunity_startup_invalid`. Any module deciding startup lawfulness
    must call `reason_proves_evidence_absent` instead of restating the members.
    """
    import ast
    from pathlib import Path

    from aletheia.universe import economic_utility as eu

    owner = Path(eu.__file__).resolve()
    root = owner.parent.parent  # core/aletheia
    scarcity_markers = {"no_history", "parent_sample_insufficient", "child_sample_insufficient"}
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.resolve() == owner:
            continue  # the owner is allowed to name its own members
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Set, ast.FrozenSet) if hasattr(ast, "FrozenSet") else ast.Set):
                continue
            members = {e.value for e in node.elts
                       if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            # A set literal that reproduces two or more scarcity reasons is a
            # second implementation of the classification.
            if len(members & scarcity_markers) >= 2:
                offenders.append(f"{path.relative_to(root)}:{node.lineno} -> {sorted(members & scarcity_markers)}")
    assert not offenders, (
        "these modules restate the economic-reason classification instead of calling "
        "reason_proves_evidence_absent(): " + "; ".join(offenders)
    )


def test_allowlist_bounds_admission_at_the_binding_gate():
    """A configured allowlist must actually block, not merely be reported.

    The tradability funnel in `universe/filters.py` is research capture — its only
    production caller is a non-fatal benchmark — so a rule added only there is
    visible and non-binding. `evaluate_hard_gate` is what decides admission.
    """
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate

    cfg = UniverseConfig()
    at = "2026-09-02T00:00:00+00:00"
    admitted = _feature(0, symbol="BTC/USDT")
    refused = _feature(1, symbol="AAPL/USDT")

    # Empty allowlist keeps the prior unrestricted behavior for both.
    for feature in (admitted, refused):
        decision = evaluate_hard_gate(feature, cfg, candidate_key="k", evaluated_at=at)
        assert decision.status == "eligible"
        check = next(item for item in decision.checks if item.check_id == "allowlist")
        assert check.status == "passed"
        assert check.reason_code == "allowlist_unrestricted"

    cfg.allowlist = ["BTC/USDT"]
    inside = evaluate_hard_gate(admitted, cfg, candidate_key="k", evaluated_at=at)
    assert inside.status == "eligible"
    assert inside.actionability == "actionable"
    assert next(i for i in inside.checks if i.check_id == "allowlist").reason_code == "allowlisted"

    outside = evaluate_hard_gate(refused, cfg, candidate_key="k", evaluated_at=at)
    assert outside.status == "ineligible"
    assert outside.actionability == "none"
    assert "not_allowlisted" in outside.reason_codes

    # Case-insensitive, and the bound is part of the hashed policy so the cohort
    # rotates rather than mixing pre- and post-narrowing evidence.
    cfg.allowlist = ["btc/usdt"]
    assert evaluate_hard_gate(admitted, cfg, candidate_key="k", evaluated_at=at).status == "eligible"
    cfg_wide = UniverseConfig()
    assert inside.policy_fingerprint != evaluate_hard_gate(
        admitted, cfg_wide, candidate_key="k", evaluated_at=at
    ).policy_fingerprint


def test_allowlist_is_visible_in_the_tradability_funnel_with_a_complete_denominator():
    """The funnel must show the allowlist decision for every candidate.

    `filter_evidence_contract` requires each candidate's row up to its first
    rejection; a gate that drops assets without evidence makes the funnel read as
    if they were never considered.
    """
    from aletheia.universe.filters import _TRADABILITY_FILTERS, apply_coarse_tradability_filters

    cfg = UniverseConfig()
    cfg.allowlist = ["COIN0/USDT"]
    population = [_feature(0), _feature(1), _feature(2)]

    assert "tradability_allowlist" in _TRADABILITY_FILTERS
    # The coarse pass runs every gate; the fine pass only re-applies per-family spread.
    kept = apply_coarse_tradability_filters(population, cfg, scanner_run_id="allowlist-run")
    assert [item["symbol"] for item in kept] == ["COIN0/USDT"]

    # Ordering matters: the allowlist is the outer bound, so it answers before the
    # exclusion list rather than after it.
    assert _TRADABILITY_FILTERS.index("tradability_allowlist") < _TRADABILITY_FILTERS.index(
        "tradability_blacklist"
    )
