"""Executable claims for docs/design/contracts/filter_evidence_contract.md."""

import asyncio
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("observation_readers")

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "filter_evidence_contract"


@spec_claim(
    SpecClaim(
        "FE-S01", _CONTRACT, "§2 candidate identity", "L2a",
        "FilterDecision persists causal candidate, instrument, direction, filter version and decision fields.",
        status="implemented",
    )
)
def test_filter_decision_model_has_causal_identity():
    from aletheia.storage.models import FilterDecision

    required = {
        "candidate_key", "candidate_kind", "parent_candidate_key", "run_id",
        "opportunity_id", "universe_snapshot_id", "universe_member_id",
        "proposal_id", "venue", "symbol", "market_type", "direction",
        "scanner_family", "filter_name", "stage", "decision", "reason_code",
        "input_snapshot", "parameter_snapshot", "filter_version", "code_commit",
        "outcome_candidate_key", "evaluated_at",
    }
    assert required <= set(FilterDecision.model_fields)


@spec_claim(
    SpecClaim(
        "FE-S02", _CONTRACT, "§4 outcome identity", "L2a",
        "FilterCandidateOutcome carries exact instrument, fixed horizon and Stage-D net economics.",
        status="implemented",
    )
)
def test_filter_candidate_outcome_has_net_economics():
    from aletheia.storage.models import FilterCandidateOutcome

    required = {
        "outcome_candidate_key", "venue", "symbol", "market_type", "direction",
        "scanner_family", "horizon", "allocation_selected", "entry_price_usd", "entry_spread_bps",
        "funding_rate_at_entry", "entry_ts", "due_at", "gross_return_pct",
        "directional_return_pct", "estimated_fee_pct",
        "estimated_spread_slippage_pct", "estimated_funding_pct",
        "net_return_pct", "min_tradable_edge_pct", "mfe_pct", "mae_pct",
        "settlement_price_source", "settlement_lag_sec", "cost_model_version", "status",
    }
    assert required <= set(FilterCandidateOutcome.model_fields)


@spec_claim(
    SpecClaim(
        "FE-B01", _CONTRACT, "§3 append-only record", "L2b",
        "Recording the same candidate/filter/decision/version twice is idempotent and never updates the first fact.",
        status="implemented",
    )
)
def test_filter_decision_recording_is_append_only_and_idempotent():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    kwargs = dict(
        candidate_key="opportunity:fe-idempotent",
        candidate_kind="opportunity",
        filter_name="ranker_confidence_gate",
        stage="ranker",
        decision="rejected",
        reason_code="confidence_below",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        parameters={"minimum": "medium"},
        inputs={"confidence": "speculative"},
    )
    first = record_filter_decision(**kwargs)
    second = record_filter_decision(**kwargs, reason_detail="retry must not overwrite")
    rows = get_db().execute(
        "SELECT * FROM filter_decisions WHERE candidate_key=?",
        (kwargs["candidate_key"],),
    ).fetchall()

    assert first == second
    assert len(rows) == 1
    assert rows[0]["reason_detail"] in (None, "")


@spec_claim(
    SpecClaim(
        "FE-B10", _CONTRACT, "§3 stage identity", "L2b",
        "Same-named gates at different pipeline stages persist as distinct evidence facts.",
        status="implemented",
    )
)
def test_filter_decision_stage_is_part_of_dedup_identity():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    common = dict(
        candidate_key="pi_decision:fe-stage-identity",
        candidate_kind="pi_decision",
        filter_name="direction_bias_brake",
        decision="passed",
        reason_code="direction_not_overheated",
        symbol="BTC/USDT",
        parameters={"max_share": 0.95},
    )
    first = record_filter_decision(stage="system_operator", **common)
    second = record_filter_decision(stage="proposal_submission", **common)

    assert first != second
    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE candidate_key=?",
        (common["candidate_key"],),
    ).fetchone()[0] == 2


@spec_claim(
    SpecClaim(
        "FE-B02", _CONTRACT, "§4 ex-ante direction", "L2b",
        "A directionless filter candidate records gross-only outcomes without inventing directional alpha.",
        status="implemented",
    )
)
def test_directionless_filter_candidate_creates_gross_only_outcomes():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    key = "observer:run-directionless:okx:BTC/USDT:spot"
    record_filter_decision(
        candidate_key=key,
        candidate_kind="observer_evaluation",
        filter_name="observer_pre_filter",
        stage="observer_pre_filter",
        decision="rejected",
        reason_code="no_sufficient_signal",
        symbol="BTC/USDT",
        venue="okx",
        market_type="spot",
        direction=None,
        entry_price_usd=100.0,
    )

    rows = get_db().execute(
        "SELECT direction, horizon FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=?",
        (key,),
    ).fetchall()
    assert {row["horizon"] for row in rows} == {"15m", "1h", "4h", "24h"}
    assert {row["direction"] for row in rows} == {"unknown"}


@spec_claim(
    SpecClaim(
        "FE-B04", _CONTRACT, "§4 fixed-horizon settlement", "L2b",
        "Due canonical filter outcomes settle through the shared exact-instrument Stage-D "
        "economics path and receive bounded capacity before the larger Universe mirror queue.",
        status="implemented",
    )
)
def test_filter_outcome_settler_has_capacity_before_universe_mirror_backlog(
    monkeypatch,
):
    from aletheia.universe import filter_outcome_recorder, outcome_recorder

    calls = []

    fetch_ceilings = []

    async def settle_filters(*, max_rows, time_budget_sec, max_fetches=None):
        calls.append(("filter", max_rows, time_budget_sec))
        fetch_ceilings.append(max_fetches)
        return 1

    monkeypatch.setattr(
        filter_outcome_recorder,
        "settle_due_filter_outcomes",
        settle_filters,
    )
    monkeypatch.setattr(
        outcome_recorder.repo,
        "list_pending_forward_outcomes",
        lambda **kwargs: calls.append(("forward", kwargs["limit"], None)) or [],
    )

    assert asyncio.run(
        outcome_recorder.settle_due(max_rows=20, time_budget_sec=8.0)
    ) == 1
    assert [call[0] for call in calls[:2]] == ["filter", "forward"]
    assert calls[0][1] > 0
    assert 0 < calls[0][2] < 8.0
    # The canonical queue is given a bounded exchange-request ceiling, not just a
    # row cap. Without it, raising row throughput would raise external load by the
    # same factor, because settlement used one live request per row.
    assert fetch_ceilings and isinstance(fetch_ceilings[0], int)
    assert fetch_ceilings[0] > 0


@spec_claim(
    SpecClaim(
        "FE-B04a", _CONTRACT, "§4 fixed-horizon settlement", "L2b",
        "Within the canonical settlement quota, due outcomes belonging to the "
        "frozen behavior contract of an active Observation Lab window are processed "
        "before older-contract rows and unrelated historical backlog.",
        status="implemented",
    )
)
def test_filter_outcome_settler_prioritizes_active_lab_contract(
    monkeypatch,
):
    from aletheia.universe import filter_outcome_recorder

    now = "2026-07-27T03:00:00+00:00"
    base = {
        "symbol": "BTC/USDT",
        "venue": "okx",
        "market_type": "perp",
        "direction": "long",
        "horizon": "4h",
        "entry_price_usd": 100.0,
        "entry_ts": "2026-07-26T22:00:00+00:00",
        "due_at": "2026-07-27T02:00:00+00:00",
        "entry_spread_bps": 5.0,
        "funding_rate_at_entry": 0.0,
    }
    historical = {
        **base,
        "id": "historical-backlog",
        "outcome_candidate_key": "historical-candidate",
    }
    current_ufc_1h = {
        **base,
        "id": "current-ufc-1h",
        "outcome_candidate_key": "current-ufc-1h-candidate",
        "horizon": "1h",
    }
    current_ufc_4h = {
        **base,
        "id": "current-ufc-4h",
        "outcome_candidate_key": "current-ufc-4h-candidate",
    }
    current_ranker = {
        **base,
        "id": "current-ranker-4h",
        "outcome_candidate_key": "current-ranker-candidate",
    }
    previous_build = {
        **base,
        "id": "previous-build-ufc",
        "outcome_candidate_key": "previous-build-ufc-candidate",
    }
    settled = []

    async def observations(rows, now):
        return {
            row["id"]: {
                "exit_price": 101.0,
                "price_quality_ok": True,
                "price_source": "test",
                "settlement_lag_sec": 0.0,
            }
            for row in rows
        }

    monkeypatch.setattr(
        filter_outcome_recorder.repo,
        "list_observation_filter_outcome_priority_keys",
        lambda **kwargs: ({
            "current-ufc-1h-candidate:1h", "current-ufc-4h-candidate:4h",
            "current-ranker-candidate:4h",
        }, {"previous-build-ufc-candidate:4h"}),
    )

    def pending_for_keys(before_ts, outcome_candidate_keys, limit, deadline=None):
        rows = []
        if "current-ufc-1h-candidate:1h" in outcome_candidate_keys:
            rows.append(current_ufc_1h)
        if "current-ufc-4h-candidate:4h" in outcome_candidate_keys:
            rows.append(current_ufc_4h)
        if "current-ranker-candidate:4h" in outcome_candidate_keys:
            rows.append(current_ranker)
        if "previous-build-ufc-candidate:4h" in outcome_candidate_keys:
            rows.append(previous_build)
        return rows[:limit], False

    monkeypatch.setattr(
        filter_outcome_recorder.repo,
        "list_pending_filter_candidate_outcomes_for_keys",
        pending_for_keys,
        raising=False,
    )
    monkeypatch.setattr(
        filter_outcome_recorder.repo,
        "list_pending_filter_candidate_outcomes",
        lambda before_ts, limit: [historical],
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_economics.estimate_outcome_economics",
        lambda **kwargs: {
            "gross_return_pct": 1.0,
            "directional_return_pct": 1.0,
            "estimated_fee_pct": 0.0,
            "estimated_spread_slippage_pct": 0.0,
            "estimated_funding_pct": 0.0,
            "net_return_pct": 1.0,
            "min_tradable_edge_pct": 0.0,
            "mfe_pct": 1.0,
            "mae_pct": 0.0,
            "cost_model_version": "test",
        },
    )
    monkeypatch.setattr(
        filter_outcome_recorder.repo,
        "settle_filter_candidate_outcome",
        lambda outcome_id, **kwargs: settled.append(outcome_id),
    )

    assert asyncio.run(
        filter_outcome_recorder.settle_due_filter_outcomes(
            max_rows=3,
            time_budget_sec=5.0,
        )
    ) == 3
    assert settled == [
        "current-ufc-1h",
        "current-ufc-4h",
        "current-ranker-4h",
    ]



def test_lab_priority_uses_frozen_contract_and_proven_retention():
    import time
    from extensions.observation_lab.canonical import _hash, record_generation_contract, upsert_retention_claim
    from extensions.observation_lab.db import get_lab_db
    from aletheia.storage import repositories as repo

    lab = get_lab_db()
    now = "2026-01-01T00:00:00+00:00"
    for name, status, behavior, retained in (
        ("current", "active", "frozen", False),
        ("older", "finalized", "old", True),
        ("mismatch", "active", "old", True),
        ("unretained", "finalized", "old", False),
    ):
        lab.execute(
            "INSERT INTO observation_windows "
            "(observation_id,kind,handler_version,generation,slot_key,authority_mode,status,"
            "spec_json,spec_hash,started_at,collect_until,finalize_not_before) "
            "VALUES (?, 'test','v1',1,?,'observe_only',?,'{}','spec',?,?,?)",
            (name, name, status, now, now, now),
        )
        record_generation_contract(
            observation_id=name, generation=1, stage="shadow",
            assignment_policy="stable_assignment_by_key", assignment_namespace_key=name,
            assignment_salt="salt", assignment_unit_key_template="{unit}", allocation_pct=0.5,
            target_horizon="4h", outcome_contract_version="outcome/v1", metric_version="metric/v1",
            behavior_contract_hash="frozen", started_at=now,
        )
        key = name + ":4h"
        lab.execute(
            "INSERT INTO observation_exposures "
            "(exposure_id,source_receipt_id,observation_id,generation,assignment_unit_key,"
            "candidate_key,assignment,applied,behavior_changed,baseline_payload_hash,applied_payload_hash,"
            "outcome_candidate_key,outcome_source,resolution_reason,behavior_contract_hash,exposed_at,imported_at) "
            "VALUES (?,?,?,1,?,?,'control',0,0,'base','base',?,'fixed_horizon_candidate','control',?,?,?)",
            (name, name, name, key, name, key, behavior, now, now),
        )
        if retained:
            upsert_retention_claim(
                observation_id=name, generation=1, source_kind="outcome_candidate_key", source_ref=key,
                source_hash=_hash({"receipt_id": name, "sequence": None, "outcome_candidate_key": key}),
                claim_domain="filter_candidate_outcomes", derived_from_fingerprint="evidence",
            )
    lab.commit()
    assert repo.list_observation_filter_outcome_priority_keys(deadline=time.monotonic()+5) == (
        {"current:4h"}, {"older:4h", "mismatch:4h"},
    )
    with pytest.raises((TimeoutError, repo.sqlite3.OperationalError)):
        repo.list_observation_filter_outcome_priority_keys(deadline=time.monotonic()-1)


@spec_claim(
    SpecClaim(
        "FE-B05", _CONTRACT, "§4 shadow projection", "L2b",
        "New shadow rows link to shared candidate outcomes and persist net economics separately from legacy gross PnL.",
        status="implemented",
    )
)
def test_shadow_trade_has_shared_net_outcome_fields():
    from aletheia.storage.models import ShadowTrade

    required = {
        "venue", "market_type", "outcome_candidate_key", "gross_return_pct",
        "net_pnl_pct", "estimated_fee_pct", "estimated_spread_slippage_pct",
        "estimated_funding_pct", "min_tradable_edge_pct", "cost_model_version",
    }
    assert required <= set(ShadowTrade.model_fields)


@spec_claim(
    SpecClaim(
        "FE-C04", _CONTRACT, "§6 net alpha consumers", "L2c",
        "Ranker and System Operator alpha baselines consume exact settled 24h "
        "filter_candidate_outcomes net economics rather than variable-hold Position "
        "or legacy Shadow PnL.",
        status="implemented",
    )
)
def test_alpha_attribution_uses_canonical_fixed_horizon_net_outcomes():
    from pathlib import Path

    source = (
        Path(__file__).parents[2] / "core" / "aletheia" / "analytics" / "alpha_attribution.py"
    ).read_text(encoding="utf-8")
    assert "JOIN filter_candidate_outcomes fo" in source
    assert "fo.horizon='24h'" in source
    assert "assess_candidate_outcome" in source
    assert "as_of_at=as_of_at" in source
    assert "eligible_strategy_outcomes(closed_after=cutoff)" not in source


@spec_claim(
    SpecClaim(
        "FE-C05", _CONTRACT, "§6 prospective controls", "L2c",
        "Prospective alpha controls link exact-instrument shared outcomes and legacy gross-only controls are excluded.",
        status="implemented",
    )
)
def test_alpha_controls_require_shared_net_outcomes():
    from pathlib import Path

    causal_source = (
        Path(__file__).parents[2] / "core" / "aletheia" / "analytics" / "causal_controls.py"
    ).read_text(encoding="utf-8")
    alpha_source = (
        Path(__file__).parents[2] / "core" / "aletheia" / "analytics" / "alpha_attribution.py"
    ).read_text(encoding="utf-8")
    assert "outcome_candidate_key" in causal_source
    assert "SELECT * FROM filter_candidate_outcomes" in alpha_source
    assert "assess_candidate_outcome" in alpha_source


@spec_claim(
    SpecClaim(
        "FE-B03", _CONTRACT, "§2 direction continuity", "L2b",
        "UniverseMember persists scanner_direction so an Observer pre-filter result is judged by an ex-ante side.",
        status="implemented",
    )
)
def test_universe_member_persists_scanner_direction():
    from aletheia.storage.models import UniverseMember

    assert "scanner_direction" in UniverseMember.model_fields




@spec_claim(
    SpecClaim(
        "FE-B2", _CONTRACT, "§3.1 B2 System Operator candidate evidence", "L2b",
        "B2 provides one same-epoch, Ranker-selected candidate evidence summary to assessment and both debate roles; insufficient evidence is neutral.",
        status="implemented",
    )
)
def test_b2_candidate_evidence_contract_is_explicit(tmp_path, monkeypatch):
    from aletheia.analytics.pi_evidence import build_b2_evidence_summary, render_b2_evidence_summary
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    summary = build_b2_evidence_summary(
        {"scanner_family": "momentum", "direction": "long", "market_type": "perp", "trade_score": 51},
        policy_epoch_id="spec-b2",
    )
    assert summary["selection_tier"] == "ranker_enqueued"
    assert summary["status"] == "insufficient_evidence"
    assert "neutral, not a directional veto" in render_b2_evidence_summary(summary)








@spec_claim(
    SpecClaim(
        "FE-S15", _CONTRACT, "§3 terminal System Operator evidence", "L2b",
        "Doctor distinguishes unscoped heartbeat skips and candidate skips missing "
        "trace/outcome evidence, without changing a decision or configuration.",
    )
)
def test_doctor_surfaces_skip_halt_evidence_gaps_read_only(tmp_path, monkeypatch):
    import json

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.diagnostics import doctor
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

    insert_pi_decision(PiDecision(
        id="candidate-skip-without-evidence",
        action="skip",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        opportunity_id="candidate-skip-source",
        signal_source="market_observer",
        reasoning="legacy direct skip missing trace",
        evidence_manifest_json=json.dumps({
            "operator_rationale": {
                "validated_adverse_claims": [{"fact_refs": ["decision.stop_price"]}],
                "adverse_evidence": ["decision.stop_price=null"],
            }
        }),
    ))
    insert_pi_decision(PiDecision(
        id="unscoped-heartbeat-skip",
        action="skip",
        reasoning="heartbeat had no target",
    ))

    report = doctor.Report()
    doctor._probe_skip_halt(report)

    checks = [check for check in report.checks if check.name == "loop.skip_halt"]
    assert len(checks) == 1
    check = checks[0]
    assert check.severity == "info"
    assert "historical entry evidence; not current planning health" in check.detail
    assert "raw_24h_candidate_skips=1" in check.detail
    assert "raw_24h_missing_trace=1" in check.detail
    assert "raw_24h_missing_outcome_seed=1" in check.detail
    assert "raw_24h_direct_model_skips=0" in check.detail
    assert "raw_24h_trade_intent_gated_skips=0" in check.detail
    assert "raw_24h_confirmed_adverse_skips=0" in check.detail
    assert "unscoped_heartbeat_skips=1" in check.detail
    assert "no_evaluable_candidate_events=0" in check.detail
    assert "deferred_unchanged_context_events=0" in check.detail
    assert "current_epoch_unreconstructable_adverse_evidence=1" in check.detail
    assert "finding=recording_incident" in check.detail
    assert "does not change PI, config, Risk, or execution" in check.fix_hint


def test_doctor_separates_confirmed_direct_and_trade_gated_skip_paths(
    tmp_path, monkeypatch
):
    import json

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.diagnostics import doctor
    from aletheia.storage.models import FilterDecision, PiDecision
    from aletheia.storage.repositories import insert_filter_decision, insert_pi_decision

    for decision_id, reason_code, attempts in (
        ("confirmed-direct-skip", "operator_model_skip", 1),
        ("trade-gated-skip", "operator_model_trade", 0),
    ):
        insert_pi_decision(PiDecision(
            id=decision_id,
            action="skip",
            symbol="BTC/USDT",
            direction="long",
            market_type="spot",
            opportunity_id=f"opp-{decision_id}",
            signal_source="market_observer",
        ))
        insert_filter_decision(FilterDecision(
            dedup_key=f"trace-{decision_id}",
            candidate_key=f"pi_decision:{decision_id}",
            candidate_kind="pi_decision",
            opportunity_id=f"opp-{decision_id}",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            filter_name="operator_action_assessment",
            stage="system_operator",
            decision="rejected",
            reason_code=reason_code,
            input_snapshot=json.dumps({"confirmation_attempts": attempts}),
            filter_version="test",
        ))

    report = doctor.Report()
    doctor._probe_skip_halt(report)
    detail = next(
        check.detail for check in report.checks if check.name == "loop.skip_halt"
    )

    assert "raw_24h_direct_model_skips=1" in detail
    assert "raw_24h_trade_intent_gated_skips=1" in detail
    assert "raw_24h_confirmed_adverse_skips=1" in detail


@spec_claim(
    SpecClaim(
        "FE-S20", _CONTRACT, "§3 terminal System Operator evidence manifest", "L2b",
        "Doctor keeps pre-remediation evidence gaps in raw counts but evaluates the "
        "active Skip-remediation epoch independently, without changing a decision.",
    )
)
def test_doctor_excludes_pre_remediation_skip_gaps_from_current_finding(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    # This record represents the reload-era incomplete evidence.  It remains
    # visible in the raw 24h count, but must not diagnose the newly started B1
    # policy epoch as broken.
    #
    # Issue #467: on Windows (clock resolution 15.625 ms), the insert and the
    # epoch-open can land on the same tick → identical timestamps → the decision
    # is epoch-scoped and the test fails.  Fix: stamp the legacy decision with an
    # explicit past time so "pre-epoch" is guaranteed regardless of clock resolution.
    from datetime import timedelta, timezone

    from extensions.learning.skip_policy import ensure_skip_remediation_policy_epoch
    from aletheia.diagnostics import doctor
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision
    from aletheia.storage.sqlite import get_db
    _past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    insert_pi_decision(PiDecision(
        id="legacy-skip-without-evidence",
        action="skip",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        opportunity_id="legacy-source",
        signal_source="market_observer",
        reasoning="legacy skip",
    ))
    # Back-date the row so it is unambiguously before the epoch open.
    get_db().execute(
        "UPDATE pi_decisions SET created_at=? WHERE id='legacy-skip-without-evidence'",
        (_past,),
    )
    get_db().commit()
    epoch_id = ensure_skip_remediation_policy_epoch()

    report = doctor.Report()
    doctor._probe_skip_halt(report)

    check = next(check for check in report.checks if check.name == "loop.skip_halt")
    assert check.severity == "info"
    assert "raw_24h_candidate_skips=1" in check.detail
    assert "raw_24h_missing_trace=1" in check.detail
    assert f"diagnosis_epoch={epoch_id}" in check.detail
    assert "current_epoch_candidate_skips=0" in check.detail
    assert "finding=not_applicable" in check.detail


@spec_claim(
    SpecClaim(
        "FE-S17", _CONTRACT, "§3 skip-halt mature cohort", "L2b",
        "Recent candidate evidence is checked immediately, while effectiveness uses "
        "the preceding fixed-horizon-mature cohort so new skips cannot block a finding.",
    )
)
def test_skip_halt_judges_mature_cohort_while_recent_skips_settle(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env
    from tests.test_skip_diagnosis import _add_candidate

    setup_env(tmp_path, monkeypatch)
    from aletheia.analytics.skip_diagnosis import diagnose_skip_halt

    now = datetime.now(timezone.utc)
    for index in range(5):
        _add_candidate(
            key=f"pi_decision:spec-mature-protective-{index}",
            action="rejected",
            net=-1.0,
            now=now - timedelta(hours=25, minutes=index),
        )
    _add_candidate(
        key="pi_decision:spec-recent-unsettled",
        action="rejected",
        net=None,
        now=now - timedelta(minutes=5),
        pending=True,
    )

    report = diagnose_skip_halt(now=now)

    assert report.finding == "protective_skip_observed"
    assert report.counts["candidate_skips"] == 1
    assert report.counts["mature_candidate_skips"] == 5


@spec_claim(
    SpecClaim(
        "FE-S18", _CONTRACT, "§3 skip-halt administrative exclusion", "L2b",
        "Lifecycle cascade cleanup skips are reported separately and never treated "
        "as Operator candidate decisions or evidence gaps.",
    )
)
def test_doctor_excludes_administrative_cascade_from_candidate_skips(
    tmp_path, monkeypatch,
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.diagnostics import doctor
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

    insert_pi_decision(PiDecision(
        id="administrative-cascade",
        action="skip",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        opportunity_id="expired-source",
        signal_source="system_cascade",
        reasoning="cascade_expired: administrative cleanup",
    ))

    report = doctor.Report()
    doctor._probe_skip_halt(report)

    check = next(check for check in report.checks if check.name == "loop.skip_halt")
    assert check.severity == "info"
    assert "raw_24h_candidate_skips=0" in check.detail
    assert "raw_24h_missing_trace=0" in check.detail
    assert "administrative_cascade_skips_excluded=1" in check.detail


@spec_claim(
    SpecClaim(
        "FE-B07", _CONTRACT, "§5 risk authority", "L2b",
        "Authoritative RiskDecision and its final filter/outcome evidence commit atomically; evidence failure cannot authorize execution.",
        status="implemented",
    )
)
def test_risk_evidence_is_atomic_after_authoritative_decision():
    from pathlib import Path

    from extensions.analytics.filter_registry import filter_by_name

    source = (
        Path(__file__).parents[2] / "core" / "aletheia" / "risk" / "engine.py"
    ).read_text(encoding="utf-8")
    assert source.index("insert_risk_decision(decision)") < source.index(
        "record_risk_filter_decision"
    )
    assert "raise_on_error=True" in source
    spec = filter_by_name("risk_engine")
    assert spec is not None
    assert spec.auto_tunable is False


@spec_claim(
    SpecClaim(
        "FE-C01", _CONTRACT, "§6 feedback isolation", "L2c",
        "Filter feedback groups complete net outcomes by filter version, family, direction, market and horizon.",
        status="implemented",
    )
)
def test_filter_feedback_uses_stratified_net_outcomes():
    from aletheia.analytics.filter_evidence import filter_feedback_stats

    assert callable(filter_feedback_stats)
    source_names = set(filter_feedback_stats.__code__.co_names)
    assert "forward_return_pct" not in source_names




@spec_claim(
    SpecClaim(
        "FE-C03", _CONTRACT, "§3 secret exclusion", "L2c",
        "Filter evidence schema has no credential, authorization, raw prompt or raw context columns.",
        status="implemented",
    )
)
def test_filter_evidence_schema_excludes_sensitive_payloads():
    from aletheia.storage.models import FilterDecision

    forbidden = {"api_key", "secret", "authorization", "prompt", "raw_prompt", "raw_context"}
    assert not (forbidden & set(FilterDecision.model_fields))


@spec_claim(
    SpecClaim(
        "FE-S03", _CONTRACT, "§3 high-volume batches", "L2a",
        "Directionless high-volume filters persist complete per-run candidate facts in immutable compressed batches.",
        status="implemented",
    )
)
def test_filter_decision_batch_has_complete_counted_payload():
    from aletheia.storage.models import FilterDecisionBatch

    required = {
        "run_id", "candidate_kind", "filter_name", "candidate_count",
        "passed_count", "rejected_count", "not_applicable_count", "error_count",
        "candidate_facts", "parameter_snapshot", "filter_version", "code_commit",
    }
    assert required <= set(FilterDecisionBatch.model_fields)


@spec_claim(
    SpecClaim(
        "FE-B08", _CONTRACT, "§5 directionless tradability gates", "L2b",
        "Ticker and tradability gates persist every evaluated candidate without inventing a post-hoc direction.",
        status="implemented",
    )
)
def test_universe_directionless_filters_accept_scanner_run_identity():
    import inspect

    from aletheia.universe.features import collect_features
    from aletheia.universe.filters import apply_tradability_filters

    assert "scanner_run_id" in inspect.signature(collect_features).parameters
    assert "scanner_run_id" in inspect.signature(apply_tradability_filters).parameters


@spec_claim(
    SpecClaim(
        "FE-B09", _CONTRACT, "§5 asset classification", "L2b",
        "Gold-backed RWA tokens remain eligible for ordinary tradability checks while USD stablecoins are excluded.",
        status="implemented",
    )
)
def test_gold_backed_assets_are_not_in_stablecoin_exclusion_set():
    from aletheia.universe.filters import _STABLECOIN_BASES

    assert "USDC" in _STABLECOIN_BASES
    assert not ({"XAUT", "PAXG"} & _STABLECOIN_BASES)


@spec_claim(
    SpecClaim(
        "FE-C06", _CONTRACT, "§5 capability gates", "L2c",
        "Directionless exchange and tradability gates are registered as report-only and never auto-tunable.",
        status="implemented",
    )
)
def test_directionless_universe_filters_are_report_only():
    from extensions.analytics.filter_registry import filter_by_name

    names = {
        "ticker_availability",
        "ticker_volume_prefilter",
        "tradability_stablecoin",
        "tradability_blacklist",
        "tradability_volume",
        "tradability_spread",
        "tradability_freshness",
    }
    for name in names:
        spec = filter_by_name(name)
        assert spec is not None
        assert spec.feedback_source == "none"
        assert spec.auto_tunable is False


@spec_claim(
    SpecClaim(
        "FE-C07", _CONTRACT, "§8 bounded retention", "L2c",
        "High-volume analytical filter evidence has bounded cleanup while trading causal records remain permanent.",
        status="implemented",
    )
)
def test_filter_evidence_cleanup_is_wired_and_analytics_only():
    import inspect

    from aletheia.agent_runtime import scheduler
    from aletheia.storage import repositories as repo

    assert {
        "filter_decision_batches",
        "filter_decisions",
        "filter_candidate_outcomes",
    } <= set(repo._PRUNABLE)
    source = inspect.getsource(scheduler._run_cleanup)
    assert "prune_filter_decision_batches(r.filter_decision_batches_days)" in source
    assert "prune_filter_decisions(" in source
    assert "r.filter_decisions_days" in source
    assert "prune_filter_candidate_outcomes(" in source
    assert "r.filter_candidate_outcomes_days" in source


@spec_claim(
    SpecClaim(
        "FE-C08", _CONTRACT, "§5 registry completeness", "L2c",
        "Every persisted ranker and selection hard gate is present in the unified registry as feedback-ready or report-only.",
        status="implemented",
    )
)
def test_persisted_ranker_and_selection_gates_are_registered():
    from extensions.analytics.filter_registry import filter_by_name

    names = {
        "execution_capability",
        "execution_eligibility",
        "symbol_filter",
        "direction_present",
        "demo_entry_quality",
        "committee_direction_conflict",
        "ranker_confidence_gate",
        "universe_execution_eligibility",
        "family_ttl",
        "venue_symbol_capability",
        "factor_hard_block",
        "guard_candidate_block",
        "duplicate_symbol",
        "open_position_duplicate",
        "signal_cooldown",
        "signal_batch_limit",
        "active_operator_carrier",
    }
    specs = {name: filter_by_name(name) for name in names}

    assert all(specs.values())
    assert specs["family_ttl"].feedback_source == "forward_return"
    assert specs["family_ttl"].feedback_ready is True
    assert specs["ranker_confidence_gate"].authority_class == "strategy_judgment"
    assert specs["direction_present"].authority_class == "strategy_judgment"
    assert specs["execution_eligibility"].authority_class == "fact_or_execution"
    assert specs["signal_batch_limit"].authority_class == "attention_ranking"
    for name in names - {
        "family_ttl", "ranker_confidence_gate", "signal_cooldown", "signal_batch_limit"
    }:
        assert specs[name].auto_tunable is False




@spec_claim(
    SpecClaim(
        "FE-C09", _CONTRACT, "§6 first-exposure cohorts and bounded replay", "L2c",
        "Filter feedback assigns each candidate to its first persisted filter exposure, "
        "and supported threshold replays are fixed-version, read-only and bounded.",
        status="implemented",
    )
)
def test_filter_feedback_first_exposure_and_replay_bounds_are_explicit():
    import inspect

    from aletheia.analytics import filter_evidence
    from extensions.counterfactual import filter_replay

    cte = filter_evidence.FIRST_EXPOSURE_CTE_SQL
    assert "ROW_NUMBER() OVER" in cte
    assert "fd.filter_name, fd.stage, fd.filter_version, fd.outcome_candidate_key" in cte
    assert "exposure_rank=1" in inspect.getsource(filter_evidence.filter_feedback_stats).replace(" ", "")
    replay_source = inspect.getsource(filter_replay)
    assert filter_replay._MAX_ALTERNATIVES <= 4
    assert filter_replay._MAX_CANDIDATES <= 5000
    assert "filter_version is required" in replay_source
    assert '"read_only": True' in replay_source
    assert "save_config" not in replay_source


@spec_claim(
    SpecClaim(
        "FE-C10", _CONTRACT, "§6 prospective shadow experiments", "L2c",
        "Replay-ready filter changes are preregistered prospectively, evaluated on future first exposures, and can never mutate production config.",
        status="implemented",
    )
)
def test_filter_shadow_experiments_are_prospective_and_observe_only():
    import inspect

    from extensions.counterfactual import filter_experiments as filter_shadow_experiments

    source = inspect.getsource(filter_shadow_experiments)
    assert "prospective_first_exposure" in source
    assert "production_mutation_allowed" in source
    assert "save_config" not in source
    assert "ready_for_review" in source
    from aletheia.storage import migrations

    assert "idx_filter_shadow_experiments_fixed_scope" in migrations._FILTER_SHADOW_EXPERIMENTS_DDL


@spec_claim(
    SpecClaim(
        "FE-C12", _CONTRACT, "§6 Observer rule attribution and replay", "L2c",
        "Observer diagnostics use persisted first-exposure rule facts, and the bounded "
        "RSI/ATR replay cannot mutate production or cross a filter-version boundary.",
        status="implemented",
    )
)
def test_observer_diagnostics_and_replay_are_versioned_read_only_surfaces():
    import inspect

    from extensions.analytics import filter_controller
    from aletheia.analytics import filter_evidence
    from extensions.analytics import filter_registry
    from extensions.counterfactual import filter_replay

    diagnostics = inspect.getsource(filter_evidence.observer_pre_filter_diagnostics)
    replay = inspect.getsource(filter_replay.build_observer_pre_filter_replay)
    controller = inspect.getsource(filter_controller.build_filter_controller_report)

    assert "FIRST_EXPOSURE_CTE_SQL" in diagnostics
    assert "_observer_rule_cohort" in diagnostics
    assert filter_replay._OBSERVER_RSI_GRID == (12.0, 10.0, 8.0, 6.0)
    assert filter_replay._OBSERVER_ATR_GRID == (0.8, 0.7, 0.6, 0.5)
    assert filter_replay._OBSERVER_MIN_SAMPLES == 200
    assert filter_replay._OBSERVER_MIN_COVERAGE == 0.80
    assert filter_replay._MAX_OBSERVER_REPLAYS == 3
    assert filter_registry.filter_by_name("observer_pre_filter").family_keyed is True
    assert "save_config" not in replay
    assert "production_filter_version_changed" in controller


@spec_claim(
    SpecClaim(
        "FE-C11", _CONTRACT, "§8 operations", "L2c",
        "Doctor distinguishes outcomes waiting inside the 15-minute settler cadence from genuinely stale settlement backlog.",
        status="implemented",
    )
)
def test_filter_evidence_health_respects_settler_cadence():
    import inspect

    from aletheia.diagnostics.doctor import _probe_filter_evidence

    source = inspect.getsource(_probe_filter_evidence)
    assert "awaiting_cadence" in source
    assert "stale_overdue" in source








@spec_claim(
    SpecClaim(
        "FE-S23", _CONTRACT, "§2 allocation_selected upsert", "L2b",
        "A filter_candidate_outcomes row first written with allocation_selected=NULL "
        "by the filter-evidence path is updated to the authoritative True/False when "
        "the experiment path re-inserts the same row with a definitive value. A second "
        "insert with NULL never overwrites a resolved value.",
        status="implemented",
    )
)
def test_fco_allocation_selected_upsert_updates_null_row(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.analytics.filter_evidence import build_candidate_outcomes
    from aletheia.storage.repositories import insert_filter_candidate_outcomes_batch
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    key = "spec-fco-upsert-test"

    # Step 1: filter-evidence path writes rows with allocation_selected=None (NULL).
    null_outcomes = build_candidate_outcomes(
        outcome_candidate_key=key,
        venue="okx",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        scanner_family="momentum",
        regime_tag=None,
        entry_price_usd=60000.0,
        entry_spread_bps=None,
        funding_rate_at_entry=None,
        entry_ts=now,
        allocation_selected=None,
    )
    insert_filter_candidate_outcomes_batch(null_outcomes)

    db = get_db()
    rows = db.execute(
        "SELECT allocation_selected FROM filter_candidate_outcomes WHERE outcome_candidate_key = ?",
        (key,),
    ).fetchall()
    assert rows, "Expected outcome rows to be written"
    assert all(r["allocation_selected"] is None for r in rows), (
        "Initial insert should write NULL allocation_selected"
    )

    # Step 2: experiment path re-inserts with allocation_selected=False (not selected).
    false_outcomes = build_candidate_outcomes(
        outcome_candidate_key=key,
        venue="okx",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        scanner_family="momentum",
        regime_tag=None,
        entry_price_usd=60000.0,
        entry_spread_bps=None,
        funding_rate_at_entry=None,
        entry_ts=now,
        allocation_selected=False,
    )
    insert_filter_candidate_outcomes_batch(false_outcomes)

    rows = db.execute(
        "SELECT allocation_selected FROM filter_candidate_outcomes WHERE outcome_candidate_key = ?",
        (key,),
    ).fetchall()
    assert all(r["allocation_selected"] == 0 for r in rows), (
        "Upsert must update NULL → False=0 when experiment path provides definitive value"
    )

    # Step 3: a redundant re-insert with NULL must NOT overwrite the resolved 0.
    insert_filter_candidate_outcomes_batch(null_outcomes)
    rows = db.execute(
        "SELECT allocation_selected FROM filter_candidate_outcomes WHERE outcome_candidate_key = ?",
        (key,),
    ).fetchall()
    assert all(r["allocation_selected"] == 0 for r in rows), (
        "Inserting NULL again must not overwrite a resolved allocation_selected=0"
    )


@spec_claim(
    SpecClaim(
        "FE-S24", _CONTRACT, "§2 allocation_selected backfill", "L2b",
        "The run_migrations() backfill sets allocation_selected=0 for settled and "
        "failed filter_candidate_outcomes rows whose allocation_selected is NULL. "
        "Pending rows and rows with explicit True/False are left untouched.",
        status="implemented",
    )
)
def test_fco_backfill_sets_zero_for_settled_failed_null_rows(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import _backfill_fco_allocation_selected, run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    db = sq.get_db()
    # Insert raw rows directly (bypassing model layer) to simulate legacy NULL state.
    for row_id, status, alloc in [
        ("settled-null", "settled", None),
        ("failed-null",  "failed",  None),
        ("pending-null", "pending", None),
        ("settled-true", "settled", 1),
        ("settled-false","settled", 0),
    ]:
        db.execute(
            "INSERT OR IGNORE INTO filter_candidate_outcomes "
            "(id, outcome_candidate_key, venue, symbol, market_type, direction, "
            " horizon, entry_price_usd, entry_ts, due_at, status, allocation_selected) "
            "VALUES (?, ?, 'okx', 'BTC/USDT', 'spot', 'long', '1h', 60000.0, "
            " '2024-01-01T00:00:00+00:00', '2024-01-01T01:00:00+00:00', ?, ?)",
            (row_id, f"key-{row_id}", status, alloc),
        )
    db.commit()

    _backfill_fco_allocation_selected(db)

    def _alloc(row_id):
        return db.execute(
            "SELECT allocation_selected FROM filter_candidate_outcomes WHERE id = ?",
            (row_id,),
        ).fetchone()["allocation_selected"]

    assert _alloc("settled-null") == 0, "settled+NULL must be backfilled to 0"
    assert _alloc("failed-null") == 0, "failed+NULL must be backfilled to 0"
    assert _alloc("pending-null") is None, "pending+NULL must remain NULL"
    assert _alloc("settled-true") == 1, "settled+1 must be preserved"
    assert _alloc("settled-false") == 0, "settled+0 must be preserved (was already 0)"
