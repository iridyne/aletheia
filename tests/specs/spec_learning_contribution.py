"""Behavioral contribution-integrity claims for Learning Phase 0."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "learning_evolution"


@spec_claim(
    SpecClaim(
        "LE-B02",
        _CONTRACT,
        "§5 deduplication",
        "L2b",
        "Duplicate artifacts and repeated outcome rows do not multiply contribution sample size.",
        status="implemented",
    )
)
def test_contribution_aggregation_deduplicates_outcome_identity():
    from aletheia.learning.contribution import ContributionObservation, aggregate_observations

    rows = [
        ContributionObservation(
            path_id="operator_learning_bundle",
            overlap_group="operator",
            outcome_key="position:p1",
            horizon="realized_close",
            assignment="treatment",
            net_return_pct=1.0,
            hypothesis_id=hypothesis,
        )
        for hypothesis in ("lesson-a", "memo-a", "reflection-a")
    ]
    report = aggregate_observations(rows)
    assert report.raw_n == 3
    assert report.unique_n == 1
    assert report.duplicate_n == 2
    assert report.overlap_n == 1
    assert report.attribution_status == "not_identifiable"


@spec_claim(
    SpecClaim(
        "LE-C10",
        _CONTRACT,
        "§6.1 overlap credit",
        "L2c",
        "Overlapping Learning channels cannot count one outcome as independent credit for each channel.",
        status="partial",
    )
)
def test_distinct_outcomes_remain_distinct_but_overlap_is_one_sample():
    from aletheia.learning.contribution import ContributionObservation, aggregate_observations

    rows = [
        ContributionObservation("ranker_memo_score", "ranker", "candidate:a", "4h", "treatment", 1.0, "memo"),
        ContributionObservation("ranker_case_score", "ranker", "candidate:a", "4h", "treatment", 1.0, "case"),
        ContributionObservation("ranker_case_score", "ranker", "candidate:b", "4h", "treatment", -1.0, "case"),
    ]
    report = aggregate_observations(rows)
    assert report.unique_n == 2
    assert report.overlap_n == 1
    assert sorted(report.unique_values) == [-1.0, 1.0]


@spec_claim(
    SpecClaim(
        "LE-C11",
        _CONTRACT,
        "§9.9 data-quality loss",
        "L2c",
        "Contribution becomes unavailable and requests refreeze when eligible outcome coverage is lost.",
        status="partial",
    )
)
def test_ineligible_contribution_evidence_requests_refreeze():
    from aletheia.learning.contribution import ContributionObservation, aggregate_observations
    from aletheia.learning.evidence import EvidenceClass

    report = aggregate_observations([
        ContributionObservation(
            "operator_learning_bundle", "operator", "review:r1", "narrative",
            "treatment", 99.0, "review", EvidenceClass.NARRATIVE,
        )
    ])
    assert report.unique_n == 0
    assert report.excluded_n == 1
    assert report.data_quality_status == "invalid"
    assert report.authority_action == "refreeze"


@spec_claim(
    SpecClaim(
        "LE-B16",
        _CONTRACT,
        "§9.9 outcome truth",
        "L2b",
        "The shared outcome adapter rejects synthetic, dirty, unresolved, gross-only, and economically incomplete evidence.",
        status="partial",
    )
)
def test_shared_outcome_adapter_fails_closed_on_ineligible_rows():
    from aletheia.learning.outcomes import assess_candidate_outcome, assess_realized_position

    now = datetime.now(timezone.utc)
    clean = now.isoformat()
    dirty = "2026-06-24T00:00:00+00:00"

    assert not assess_realized_position({
        "id": "p-synthetic", "closed_at": clean, "close_reason": "reconciled_gone",
        "realized_pnl": 1.0, "entry_price": 100.0, "quantity": 1.0,
    }).eligible
    assert not assess_realized_position({
        "id": "p-dirty", "closed_at": dirty, "close_reason": "stop_loss",
        "realized_pnl": -1.0, "entry_price": 100.0, "quantity": 1.0,
    }).eligible
    assert not assess_candidate_outcome({
        "outcome_candidate_key": "c-pending", "horizon": "4h", "status": "pending",
        "entry_ts": clean, "direction": "long", "net_return_pct": None,
    }).eligible
    assert not assess_candidate_outcome({
        "outcome_candidate_key": "c-gross", "horizon": "4h", "status": "settled",
        "entry_ts": clean, "direction": "long", "gross_return_pct": 2.0,
        "net_return_pct": None,
    }).eligible
    assert not assess_candidate_outcome({
        "outcome_candidate_key": "c-nan", "horizon": "4h", "status": "settled",
        "entry_ts": clean, "direction": "long", "net_return_pct": float("nan"),
        "cost_model_version": "v1",
    }).eligible
    eligible = assess_candidate_outcome({
        "outcome_candidate_key": "c-net", "horizon": "4h", "status": "settled",
        "entry_ts": clean, "direction": "long", "net_return_pct": 0.5,
        "cost_model_version": "v1",
    })
    assert eligible.eligible
    assert eligible.net_return_pct == 0.5


@spec_claim(
    SpecClaim(
        "LE-B09",
        _CONTRACT,
        "§9.5 guard effectiveness",
        "L2b",
        "Guard effectiveness consumes cost-aware outcomes for successfully blocked candidates, not only executed positions.",
        status="implemented",
    )
)
def test_guard_effectiveness_includes_successfully_blocked_candidate_outcomes(monkeypatch):
    import json

    from aletheia.analytics.guard_effectiveness import compute_guard_stats
    from aletheia.knowledge.guards import GuardAppliesTo, GuardPredicate, TradingGuard
    from aletheia.storage.sqlite import get_db

    guard = TradingGuard(
        guard_id="guard-phase0",
        title="Phase 0 guard",
        status="active",
        block_candidate=True,
        predicates=[GuardPredicate(field="direction", op="eq", value="long")],
        applies_to=GuardAppliesTo(asset_class=["crypto"]),
    )
    monkeypatch.setattr(
        "aletheia.analytics.guard_effectiveness.load_trading_guards", lambda **_: [guard]
    )
    monkeypatch.setattr(
        "aletheia.analytics.guard_effectiveness.build_trade_attribution_rows", lambda **_: []
    )

    db = get_db()
    entry = _iso(1)
    snapshot = json.dumps({"guard_ids": [guard.guard_id]})
    for idx, net in enumerate((-1.0, -2.0, 0.5)):
        key = f"guard-outcome-{idx}"
        db.execute(
            "INSERT INTO filter_decisions "
            "(id, dedup_key, candidate_key, candidate_kind, symbol, market_type, direction, filter_name, stage, sequence_no, decision, reason_code, input_snapshot, parameter_snapshot, filter_version, outcome_candidate_key, evaluated_at) "
            "VALUES (?, ?, ?, 'opportunity', 'BTC/USDT', 'spot', 'long', 'guard_candidate_block', 'ranker', 1, 'rejected', 'guard_blocked', ?, '{}', 'v1', ?, ?)",
            (f"fd-{idx}", f"fd-dedup-{idx}", f"candidate-{idx}", snapshot, key, entry),
        )
        db.execute(
            "INSERT INTO filter_candidate_outcomes "
            "(id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, cost_model_version, status) "
            "VALUES (?, ?, 'test', 'BTC/USDT', 'spot', 'long', '4h', 100, ?, ?, ?, ?, 'v1', 'settled')",
            (f"fo-{idx}", key, entry, entry, entry, net),
        )
    db.commit()

    stats = compute_guard_stats()
    assert len(stats) == 1
    assert stats[0]["fire_count"] == 0
    assert stats[0]["blocked_candidate_count"] == 3
    assert stats[0]["blocked_settled_n"] == 3
    assert stats[0]["blocked_loss_rate"] == pytest.approx(2 / 3, abs=0.0001)
    assert stats[0]["blocked_avg_net_pct"] == -0.8333


def _iso(days_ago: int) -> str:
    anchor = datetime.fromisoformat("2026-07-12T07:10:01+00:00")
    return (anchor + timedelta(seconds=(30 - days_ago) * 10)).isoformat()


@spec_claim(
    SpecClaim(
        "LE-C06",
        _CONTRACT,
        "§11 UI/API honesty",
        "L2c",
        "Learning alpha reports raw events separately from unique cohorts and bases confidence on unique outcomes.",
        status="partial",
    )
)
def test_learning_alpha_deduplicates_duplicate_lessons_and_reports_raw_counts(monkeypatch):
    import aletheia.analytics.alpha_attribution as alpha_attribution

    from aletheia.storage.sqlite import get_db

    audit_now = datetime.fromisoformat("2026-07-13T07:10:01+00:00")
    monkeypatch.setattr(alpha_attribution, "_utcnow", lambda: audit_now)

    db = get_db()
    event_at = _iso(10)
    for idx in range(5):
        _insert_trade(db, idx, -2.0, _iso(15))
        _insert_trade(db, idx + 100, 2.0, _iso(5))
    evidence = '{"scanner_family":"momentum","direction":"long","symbol":"BTC/USDT"}'
    for idx in range(6):
        db.execute(
            "INSERT INTO ps_lessons "
            "(lesson_id, source_type, source_id, category, severity, summary, evidence_json, recommendation_json, status, created_at, activated_at) "
            "VALUES (?, 'trade_case_study', 'same-case', 'entry_timing', 'warning', ?, ?, ?, 'applied', ?, ?)",
            (
                f"duplicate-{idx}", f"duplicate lesson {idx}", evidence,
                '{"action":"block_direction","direction":"short"}', event_at, event_at,
            ),
        )
    db.commit()

    learning = alpha_attribution.build_alpha_attribution_audit(days=30)["verdicts"]["learning"]
    assert learning["sample_size"] == 5
    assert learning["details"]["raw_event_count"] == 6
    assert learning["details"]["unique_event_count"] == 1
    assert learning["details"]["duplicate_event_count"] == 5
    assert learning["details"]["unique_after_sample_size"] == 5
    assert learning["contribution_verdict"] == "insufficient_data"
    assert learning["measurement_status"] == "directional_evidence_only"


def _insert_trade(db, idx: int, pnl_pct: float, closed_at: str) -> None:
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position
    from tests.test_outcome_closure import _insert_entry_chain

    opp = f"le-opp-{idx}"
    proposal = f"le-prop-{idx}"
    position_id = f"le-pos-{idx}"
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id=position_id,
        opportunity_id=opp,
        proposal_id=proposal,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )
    db.execute(
        "UPDATE opportunities SET scanner_family='momentum' WHERE id=?",
        (opportunity_id,),
    )
    insert_position(Position(
        id=position_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0 + pnl_pct,
        realized_pnl=pnl_pct,
        execution_mode="paper_internal",
        status="closed",
        close_reason="signal_exit",
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=0.0,
        funding_source="local_simulation",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        close_category="strategy",
        exit_price_source="local_fill",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        outcome_semantics_version="oi-v1",
        opened_at=_iso(20),
        closed_at=closed_at,
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        order_id=order_id,
    ))


@spec_claim(
    SpecClaim(
        "LE-B24", _CONTRACT, "§shadow vs treatment", "L2b",
        "A shadow-stage cohort (no treatment actually applied) is measurable but NOT "
        "causally eligible; it yields shadow_trial_only, never a causal promote signal.",
        status="implemented",
    ),
    SpecClaim(
        "LE-B25", _CONTRACT, "§shadow vs treatment", "L2b",
        "A cohort where the treatment arm was actually applied is causally eligible.",
        status="implemented",
    ),
)
def test_shadow_cohort_is_not_causally_eligible():
    from aletheia.learning.contribution import ContributionObservation, aggregate_observations

    # Shadow: both arms ran baseline (authority_applied False everywhere). Even with a
    # clean treatment/control return spread, this must NOT be read as causal.
    shadow = aggregate_observations([
        ContributionObservation("universe_family_calibration", "universe", "u:a", "4h",
                                 "treatment", 0.5, "h", authority_applied=False),
        ContributionObservation("universe_family_calibration", "universe", "u:b", "4h",
                                 "control", -0.2, "h", authority_applied=False),
    ])
    assert shadow.data_quality_status == "green"      # measurable
    assert shadow.intervention_applied_n == 0
    assert shadow.causal_eligible is False
    assert shadow.authority_action == "shadow_trial_only"
    assert shadow.authority_action != "hold"          # cannot feed a promote verdict

    # Real intervention: the treatment arm actually had the treatment applied.
    real = aggregate_observations([
        ContributionObservation("universe_family_calibration", "universe", "u:a", "4h",
                                 "treatment", 0.5, "h", authority_applied=True),
        ContributionObservation("universe_family_calibration", "universe", "u:b", "4h",
                                 "control", -0.2, "h", authority_applied=False),
    ])
    assert real.intervention_applied_n == 1
    assert real.causal_eligible is True
    assert real.authority_action == "hold"


@spec_claim(SpecClaim(
    "LE-B29", _CONTRACT, "§measurement-support gate: paths need exact cohort adapters (#324)", "L2b",
    "Operator and Observer bundles plus ranker paths without exact adapters stay out of "
    "SUPPORTED_PATH_IDS. ranker_case_score is admitted only because its candidate-level "
    "filter_candidate_outcomes adapter does not require Universe allocation_selected.",
    status="implemented",
))
def test_paths_without_cohort_adapters_excluded_from_supported_path_ids():
    """Paths without exact cohort adapters must remain out of SUPPORTED_PATH_IDS."""
    from aletheia.learning.cohorts import SUPPORTED_PATH_IDS

    _PATHS_WITHOUT_ADAPTERS = {
        "operator_learning_bundle",
        "observer_advisory_bundle",
        "ranker_memo_score",
        "ranker_factor_filter",
    }
    leaked = _PATHS_WITHOUT_ADAPTERS & SUPPORTED_PATH_IDS
    assert not leaked, f"paths without adapters leaked into support: {leaked}"
    assert "ranker_case_score" in SUPPORTED_PATH_IDS


def test_ranker_case_outcome_adapter_uses_exact_base_key_and_horizon():
    from datetime import datetime, timezone

    from aletheia.learning.cohorts import _load_ranker_case_outcomes
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO filter_candidate_outcomes ("
        "id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
        "estimated_fee_pct, estimated_spread_slippage_pct, cost_model_version, status"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ranker-outcome-1", "member-1", "bitget", "BTC/USDT", "spot", "long",
            "4h", 100.0, now, now, now, 0.4, 0.05, 0.05, "cost-v1", "settled",
        ),
    )
    db.commit()

    resolved = _load_ranker_case_outcomes({"member-1:4h"}, "4h")
    outcome, reason, duplicate_n, wrong_horizon_n, *_ = resolved["member-1:4h"]
    assert reason is None
    assert duplicate_n == 0
    assert wrong_horizon_n == 0
    assert outcome["outcome_candidate_key"] == "member-1"
    assert outcome["_learning_outcome_source"] == "filter_candidate_outcomes"
    assert outcome["_allocation_selection_source"] == "ranker_candidate_identity"


def test_ranker_case_cohort_is_candidate_level_and_shadow_only(monkeypatch):
    from datetime import datetime, timedelta, timezone
    import json

    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.learning import experiments
    from aletheia.learning.cohorts import build_experiment_cohorts
    from aletheia.learning.experiments import stable_assignment
    from aletheia.learning.models import LearningExperiment
    from aletheia.learning.verdicts import evaluate_cohort
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    common = {
        "path_id": "ranker_case_score",
        "proposed_delta": 0.0,
        "direction": "long",
        "scope": {"symbol": "BTC/USDT", "direction": "long"},
        "source_cohort": "trade_case_study:v1",
        "learning_trace": None,
    }

    def ranker_key(candidate_key: str) -> str:
        return f"ranker_member:snapshot-1:{candidate_key}:spot"

    def expose(candidate_key: str) -> None:
        ranker._authorized_ranker_delta(
            **common,
            opp={
                "id": f"carrier-{candidate_key}",
                "symbol": "BTC/USDT",
                "market": "spot",
                "universe_snapshot_id": "snapshot-1",
                "universe_member_id": candidate_key,
                "metadata": json.dumps({
                    "learning_candidate_key": f"unrelated:{candidate_key}",
                }),
            },
        )

    expose("member-1")
    db = get_db()
    first = db.execute(
        "SELECT * FROM learning_exposures WHERE path_id=? ORDER BY exposed_at",
        ("ranker_case_score",),
    ).fetchone()
    assert first is not None
    experiment = LearningExperiment(**dict(db.execute(
        "SELECT * FROM learning_experiments WHERE experiment_id=?",
        (first["experiment_id"],),
    ).fetchone()))
    opposite = next(
        f"member-{index}"
        for index in range(2, 1000)
        if stable_assignment(
            experiment, ranker_key(f"member-{index}")
        ) != first["assignment"]
    )
    expose(opposite)

    outcome_time = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    for candidate_key, outcome in (("member-1", 0.4), (opposite, -0.2)):
        outcome_key = ranker_key(candidate_key)
        db.execute(
            "INSERT INTO filter_candidate_outcomes ("
            "id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
            "entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
            "estimated_fee_pct, estimated_spread_slippage_pct, mae_pct, "
            "cost_model_version, status"
            ") VALUES (?, ?, 'bitget', 'BTC/USDT', 'spot', 'long', '4h', "
            "100, ?, ?, ?, ?, 0.05, 0.05, -0.5, 'cost-v1', 'settled')",
            (
                f"outcome-{candidate_key}",
                outcome_key,
                outcome_time,
                outcome_time,
                outcome_time,
                outcome,
            ),
        )
    db.commit()

    cohorts = build_experiment_cohorts(experiment.experiment_id)
    assert len(cohorts) == 1
    cohort = cohorts[0]
    assert {sample.assignment_unit_key for sample in cohort.samples} == {
        ranker_key("member-1"), ranker_key(opposite),
    }, cohort.inspection()
    assert {sample.outcome_source for sample in cohort.samples} == {
        "filter_candidate_outcomes",
    }
    assert len(cohort.treatment_samples) == 1
    assert len(cohort.control_samples) == 1

    evaluation = evaluate_cohort(cohort)
    assert evaluation.harm_metrics["metric_version"] == (
        "ranker_case_candidate_net_utility_v1"
    )
    assert evaluation.harm_metrics["statistical_unit"] == "ranker_candidate"
    assert "trial_integrity_readiness" not in evaluation.harm_metrics
    assert evaluation.harm_metrics["allocation_selection_source_member_counts"] == {
        "ranker_candidate_identity": 2,
    }
    assert evaluation.verdict == "insufficient_data"

def test_ranker_case_excludes_wrong_horizon_and_incomplete_cost(monkeypatch):
    from datetime import datetime, timezone

    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.learning import experiments
    from aletheia.learning.cohorts import build_experiment_cohorts
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    common = {
        "proposed_delta": 0.0,
        "path_id": "ranker_case_score",
        "direction": "long",
        "scope": {"symbol": "BTC/USDT", "direction": "long"},
        "source_cohort": "trade_case_study:v1",
        "learning_trace": None,
    }

    def ranker_key(candidate_key: str) -> str:
        return f"ranker_member:snapshot-1:{candidate_key}:spot"

    for candidate_key in ("wrong-horizon", "missing-cost"):
        ranker._authorized_ranker_delta(
            **common,
            opp={
                "id": f"carrier-{candidate_key}",
                "symbol": "BTC/USDT",
                "market": "spot",
                "universe_snapshot_id": "snapshot-1",
                "universe_member_id": candidate_key,
                "metadata": json.dumps({"learning_candidate_key": "unrelated"}),
            },
        )

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    for candidate_key, horizon, fee in (
        ("wrong-horizon", "1h", 0.05),
        ("missing-cost", "4h", None),
    ):
        outcome_key = ranker_key(candidate_key)
        db.execute(
            "INSERT INTO filter_candidate_outcomes ("
            "id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
            "entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
            "estimated_fee_pct, estimated_spread_slippage_pct, cost_model_version, status"
            ") VALUES (?, ?, 'bitget', 'BTC/USDT', 'spot', 'long', ?, "
            "100, ?, ?, ?, 0.4, ?, 0.05, 'cost-v1', 'settled')",
            (f"outcome-{candidate_key}", outcome_key, horizon, now, now, now, fee),
        )
    db.commit()
    experiment_id = db.execute(
        "SELECT experiment_id FROM learning_exposures "
        "WHERE path_id='ranker_case_score' LIMIT 1",
    ).fetchone()["experiment_id"]

    cohort = build_experiment_cohorts(experiment_id)[0]
    assert cohort.samples == []
    assert cohort.exclusion_reasons["wrong_horizon"] == 1
    assert cohort.invalid_reasons["incomplete_cost_model"] == 1


@spec_claim(SpecClaim(
    "LE-B28", _CONTRACT, "§5 conflicting outcomes", "L2b",
    "Two settled values for one outcome identity that disagree beyond tolerance make "
    "the cohort invalid and refreeze — never silently take the first row.",
    status="implemented",
))
def test_conflicting_outcome_values_fail_closed():
    from aletheia.learning.contribution import ContributionObservation, aggregate_observations

    conflict = aggregate_observations([
        ContributionObservation("ranker_case_score", "ranker", "candidate:x", "4h",
                                 "treatment", 1.0, "case", authority_applied=True),
        ContributionObservation("ranker_case_score", "ranker", "candidate:x", "4h",
                                 "treatment", -2.0, "case", authority_applied=True),
    ])
    assert conflict.conflict_n == 1
    assert conflict.data_quality_status == "conflicting_outcome_values"
    assert conflict.authority_action == "refreeze"
    assert conflict.causal_eligible is False
    assert conflict.unique_values == ()  # a conflicting identity contributes nothing

    # Agreeing duplicates within tolerance still dedup to one clean sample.
    agree = aggregate_observations([
        ContributionObservation("ranker_case_score", "ranker", "candidate:y", "4h",
                                 "treatment", 1.0, "case", authority_applied=True),
        ContributionObservation("ranker_case_score", "ranker", "candidate:y", "4h",
                                 "treatment", 1.0, "case", authority_applied=True),
    ])
    assert agree.conflict_n == 0
    assert agree.unique_values == (1.0,)
    assert agree.authority_action == "hold"
