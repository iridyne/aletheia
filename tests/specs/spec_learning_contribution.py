"""Behavioral contribution-integrity claims for Learning Phase 0."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "learning_evolution"


def _fresh_ranker_mark(_opp: dict) -> dict:
    return {
        "price": 100.0,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "venue": "bitget",
    }


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
    from extensions.learning.contribution import ContributionObservation, aggregate_observations

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
    from extensions.learning.contribution import ContributionObservation, aggregate_observations

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
    from extensions.learning.contribution import ContributionObservation, aggregate_observations
    from extensions.learning.evidence import EvidenceClass

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
    from aletheia.analytics.outcome_semantics import assess_candidate_outcome, assess_realized_position

    now = datetime.now(timezone.utc)
    clean = now.isoformat()
    dirty = "2026-06-24T00:00:00+00:00"
    complete_economics = {
        "venue": "bitget",
        "market_type": "perp",
        "entry_price_usd": 100.0,
        "exit_price_usd": 100.8,
        "directional_return_pct": 0.8,
        "estimated_fee_pct": 0.1,
        "estimated_spread_slippage_pct": 0.1,
        "estimated_funding_pct": 0.1,
        "net_return_pct": 0.5,
        "min_tradable_edge_pct": 0.05,
        "settlement_price_source": "horizon_ohlcv",
        "settlement_lag_sec": 0,
        "cost_model_version": "v1",
    }

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
    for invalid_net in ("0.5", b"0.5", True):
        assert not assess_candidate_outcome({
            "outcome_candidate_key": "c-invalid-net", "horizon": "4h",
            "status": "settled", "entry_ts": clean, "direction": "long",
            **complete_economics, "net_return_pct": invalid_net,
        }).eligible
    for invalid_cost_model in (None, "", " ", b"v1", True):
        assert not assess_candidate_outcome({
            "outcome_candidate_key": "c-invalid-cost-model", "horizon": "4h",
            "status": "settled", "entry_ts": clean, "direction": "long",
            **complete_economics, "cost_model_version": invalid_cost_model,
        }).eligible
    for invalid_edge in (
        None, float("nan"), float("inf"), -0.01, "malformed", "0.05", b"0.05", True,
    ):
        assert not assess_candidate_outcome({
            "outcome_candidate_key": "c-invalid-edge", "horizon": "4h",
            "status": "settled", "entry_ts": clean, "direction": "long",
            **complete_economics, "min_tradable_edge_pct": invalid_edge,
        }).eligible
    for field, invalid_value in (
        ("directional_return_pct", None),
        ("estimated_fee_pct", None),
        ("estimated_spread_slippage_pct", None),
        ("estimated_funding_pct", None),
        ("estimated_fee_pct", -0.1),
        ("estimated_spread_slippage_pct", -0.1),
        ("estimated_funding_pct", float("inf")),
        ("estimated_fee_pct", "0.1"),
        ("estimated_spread_slippage_pct", b"0.1"),
        ("net_return_pct", 5.0),
    ):
        assert not assess_candidate_outcome({
            "outcome_candidate_key": "c-invalid-cost-tuple", "horizon": "4h",
            "status": "settled", "entry_ts": clean, "direction": "long",
            **complete_economics, field: invalid_value,
        }).eligible
    for invalid_market in (None, "", "crypto", b"perp"):
        assert not assess_candidate_outcome({
            "outcome_candidate_key": "c-invalid-market", "horizon": "4h",
            "status": "settled", "entry_ts": clean, "direction": "long",
            **complete_economics, "market_type": invalid_market,
        }).eligible
    missing_venue = assess_candidate_outcome({
        "outcome_candidate_key": "c-missing-venue", "horizon": "4h",
        "status": "settled", "entry_ts": clean, "direction": "long",
        **complete_economics, "venue": " \t",
    })
    assert not missing_venue.eligible
    assert missing_venue.reason == "missing_venue"
    assert not assess_candidate_outcome({
        "outcome_candidate_key": "c-spot-funding", "horizon": "4h",
        "status": "settled", "entry_ts": clean, "direction": "long",
        **complete_economics, "market_type": "spot",
    }).eligible
    eligible = assess_candidate_outcome({
        "outcome_candidate_key": "c-net", "horizon": "4h", "status": "settled",
        "entry_ts": clean, "direction": "long", **complete_economics,
    })
    assert eligible.eligible
    assert eligible.net_return_pct == 0.5
    funding_credit = assess_candidate_outcome({
        "outcome_candidate_key": "c-funding-credit", "horizon": "4h",
        "status": "settled", "entry_ts": clean, "direction": "long",
        **complete_economics, "estimated_funding_pct": -0.1,
        "net_return_pct": 0.7,
    })
    assert funding_credit.eligible
    assert funding_credit.net_return_pct == 0.7


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
    from extensions.analytics.guard_effectiveness import compute_guard_stats
    from extensions.knowledge.guards import GuardAppliesTo, GuardPredicate, TradingGuard
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
        "extensions.analytics.guard_effectiveness.load_trading_guards", lambda **_: [guard]
    )
    monkeypatch.setattr(
        "extensions.analytics.guard_effectiveness.build_trade_attribution_rows", lambda **_: []
    )

    db = get_db()
    entry = _iso(1)
    due_at = (datetime.fromisoformat(entry) + timedelta(hours=4)).isoformat()
    snapshot = json.dumps({"guard_ids": [guard.guard_id]})
    import aletheia.analytics.outcome_semantics as outcomes

    original = outcomes.assess_candidate_outcome
    as_of_values = []

    def _record(row, *, as_of_at=None):
        as_of_values.append(as_of_at)
        return original(row, as_of_at=as_of_at)

    monkeypatch.setattr(outcomes, "assess_candidate_outcome", _record)
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
            "(id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
            "entry_price_usd, exit_price_usd, entry_ts, due_at, settled_at, "
            "settlement_price_source, settlement_lag_sec, directional_return_pct, "
            "estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, "
            "net_return_pct, "
            "min_tradable_edge_pct, cost_model_version, status) "
            "VALUES (?, ?, 'test', 'BTC/USDT', 'spot', 'long', '4h', 100, ?, ?, ?, ?, "
            "'horizon_ohlcv', 0, ?, 0.1, 0.1, 0, ?, 0.05, 'v1', 'settled')",
            (f"fo-{idx}", key, 100.0 * (1.0 + (net + 0.2) / 100.0),
             entry, due_at, due_at, net + 0.2, net),
        )
    db.commit()

    stats = compute_guard_stats()
    assert len(stats) == 1
    assert stats[0]["fire_count"] == 0
    assert stats[0]["blocked_candidate_count"] == 3
    assert stats[0]["blocked_settled_n"] == 3
    assert stats[0]["blocked_loss_rate"] == pytest.approx(2 / 3, abs=0.0001)
    assert stats[0]["blocked_avg_net_pct"] == -0.8333
    assert len({value for value in as_of_values}) == 1
    assert next(iter(as_of_values)).tzinfo is timezone.utc


def _iso(days_ago: int) -> str:
    anchor = datetime.fromisoformat("2026-07-12T07:10:01+00:00")
    return (anchor + timedelta(seconds=(30 - days_ago) * 10)).isoformat()


@spec_claim(
    SpecClaim(
        "LE-C06",
        _CONTRACT,
        "§11 UI/API honesty",
        "L2c",
        "Learning alpha derives sample confidence ONLY from persisted canonical Lab "
        "measurements. Duplicate lessons and closed trades — however many — cannot "
        "manufacture a sample size, and the absence of a Lab measurement is reported as "
        "insufficient_data with an explicit reason rather than a computed verdict. "
        "Contract Delta (2026-09-07, learning_contract.md preamble): the Phase 0 "
        "lesson/trade-derived alpha with its raw/unique/duplicate event counts is "
        "retired — Observation Lab is the only online Experiment authority and legacy "
        "ledgers are never an executable fallback.",
        status="implemented",
    )
)
def test_learning_alpha_cannot_manufacture_confidence_from_duplicate_lessons(monkeypatch):
    import aletheia.analytics.alpha_attribution as alpha_attribution
    from aletheia.storage.sqlite import get_db

    audit_now = datetime.fromisoformat("2026-07-13T07:10:01+00:00")
    monkeypatch.setattr(alpha_attribution, "_utcnow", lambda: audit_now)

    db = get_db()
    event_at = _iso(10)
    for idx in range(5):
        _insert_trade(idx, -2.0, _iso(15))
        _insert_trade(idx + 100, 2.0, _iso(5))
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

    # 6 duplicate applied lessons over one shared source_id, plus 10 closed
    # positions, are deliberately adversarial input. None of it may become sample
    # confidence: the only admissible evidence is a persisted Lab measurement.
    assert learning["sample_size"] == 0
    assert learning["contribution_verdict"] == "insufficient_data"
    assert learning["evidence_type"] == "canonical_lab_measurements"
    assert learning["details"]["measurements"] == []
    # The reason must name the real blocker. Missing Lab authority is reported as
    # unavailable (learning_contract.md preamble: "Missing authority is
    # unavailable."), never as a computed neutral/negative verdict.
    assert learning["details"]["unavailable_reason"] in {
        "experiment_authority_unavailable",
        "no_persisted_lab_measurement",
        "current_consumed_settled_proof_unavailable",
    }, learning["details"]["unavailable_reason"]
    assert learning["reason"] == learning["details"]["unavailable_reason"]

    # The retired Phase 0 derivation must not come back: these keys existed only
    # while alpha was computed from the ps_lessons/trade join.
    for retired_key in (
        "raw_event_count",
        "unique_event_count",
        "duplicate_event_count",
        "unique_after_sample_size",
    ):
        assert retired_key not in learning["details"], (
            f"{retired_key} is back — learning alpha is deriving confidence from "
            "legacy lesson/trade rows instead of persisted Lab measurements"
        )


def _insert_trade(idx: int, pnl_pct: float, closed_at: str) -> None:
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    _insert_closed_position_via_producer(
        position_id=f"le-pos-{idx}",
        opportunity_id=f"le-opp-{idx}",
        proposal_id=f"le-prop-{idx}",
        scanner_family="momentum",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        realized_pnl=pnl_pct,
        execution_mode="bitget_demo",
        close_reason="signal_exit",
        opened_at=_iso(20),
        closed_at=closed_at,
    )


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
    from extensions.learning.contribution import ContributionObservation, aggregate_observations

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
    from extensions.learning.cohorts import SUPPORTED_PATH_IDS

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

    from extensions.learning.cohorts import _load_ranker_case_outcomes
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO filter_candidate_outcomes ("
        "id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "entry_price_usd, exit_price_usd, entry_ts, due_at, settled_at, "
        "settlement_price_source, settlement_lag_sec, directional_return_pct, "
        "net_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, "
        "estimated_funding_pct, min_tradable_edge_pct, cost_model_version, status"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 100.5, ?, ?, ?, 'horizon_ohlcv', 0, 0.5, ?, ?, ?, 0, 0.05, ?, ?)",
        (
            "ranker-outcome-1", "member-1", "bitget", "BTC/USDT", "spot", "long",
            "4h", 100.0, now, now, now, 0.4, 0.05, 0.05, "v1", "settled",
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





@spec_claim(SpecClaim(
    "LE-B28", _CONTRACT, "§5 conflicting outcomes", "L2b",
    "Two settled values for one outcome identity that disagree beyond tolerance make "
    "the cohort invalid and refreeze — never silently take the first row.",
    status="implemented",
))
def test_conflicting_outcome_values_fail_closed():
    from extensions.learning.contribution import ContributionObservation, aggregate_observations

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
