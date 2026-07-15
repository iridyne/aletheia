"""Read-only System Operator skip-halt diagnosis scenarios."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _setup(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)


def _add_candidate(
    *,
    key: str,
    action: str,
    net: float | None,
    now: datetime,
    complete: bool = True,
    pending: bool = False,
) -> None:
    from aletheia.storage.models import FilterCandidateOutcome, FilterDecision, PiDecision
    from aletheia.storage.repositories import (
        insert_filter_candidate_outcomes_batch,
        insert_filter_decision,
        insert_pi_decision,
    )

    opportunity_id = f"opp-{key}"
    if action == "rejected":
        insert_pi_decision(PiDecision(
            id=key.removeprefix("pi_decision:"),
            action="skip",
            symbol="BTC/USDT",
            direction="long",
            market_type="spot",
            opportunity_id=opportunity_id,
            signal_source="market_observer",
            created_at=now.isoformat(),
        ))
    insert_filter_decision(FilterDecision(
        id=f"trace-{key}",
        dedup_key=f"dedup-{key}",
        candidate_key=key,
        candidate_kind="pi_decision",
        opportunity_id=opportunity_id,
        venue="binance",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        filter_name="operator_action_assessment",
        stage="system_operator",
        decision=action,
        reason_code=f"operator_model_{'skip' if action == 'rejected' else 'trade'}",
        filter_version="skip-diagnosis-test-v1",
        outcome_candidate_key=key,
        evaluated_at=now.isoformat(),
    ))
    horizons = ("15m", "1h", "4h", "24h") if complete else ("15m",)
    rows = []
    for horizon in horizons:
        is_pending = pending or net is None
        rows.append(FilterCandidateOutcome(
            id=f"outcome-{key}-{horizon}",
            outcome_candidate_key=key,
            venue="binance",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            horizon=horizon,
            entry_price_usd=100.0,
            entry_ts=now.isoformat(),
            due_at=(now - timedelta(minutes=1)).isoformat(),
            settled_at=None if is_pending else now.isoformat(),
            net_return_pct=None if is_pending else net,
            status="pending" if is_pending else "settled",
        ))
    insert_filter_candidate_outcomes_batch(rows)


def test_no_candidate_flow_is_not_applicable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.analytics.skip_diagnosis import diagnose_skip_halt

    report = diagnose_skip_halt(now=datetime.now(timezone.utc))

    assert report.finding == "not_applicable"
    assert report.side_effects == ()


def test_missing_trace_or_four_horizon_seed_is_recording_incident(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.analytics.skip_diagnosis import diagnose_skip_halt

    now = datetime.now(timezone.utc)
    _add_candidate(
        key="pi_decision:missing-seed", action="rejected", net=-1.0,
        now=now, complete=False,
    )

    report = diagnose_skip_halt(now=now)

    assert report.finding == "recording_incident"
    assert report.counts["candidate_skips"] == 1
    assert report.counts["missing_outcome_seed"] == 1


def test_complete_but_unsettled_skip_cohort_awaits_outcomes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.analytics.skip_diagnosis import diagnose_skip_halt

    now = datetime.now(timezone.utc)
    _add_candidate(
        key="pi_decision:pending-outcome", action="rejected", net=None,
        now=now, pending=True,
    )

    report = diagnose_skip_halt(now=now)

    assert report.finding == "awaiting_outcomes"
    assert report.counts["unsettled_candidate_skips"] == 1


def test_negative_complete_skip_cohort_is_protective_not_a_bypass(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.analytics.skip_diagnosis import diagnose_skip_halt

    now = datetime.now(timezone.utc)
    for index in range(5):
        _add_candidate(
            key=f"pi_decision:protective-{index}", action="rejected", net=-1.0,
            now=now - timedelta(minutes=index),
        )

    report = diagnose_skip_halt(now=now)

    assert report.finding == "protective_skip_observed"
    assert report.side_effects == ()


def test_positive_skip_cohort_with_comparable_pass_is_overrestriction_suspect(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.analytics.skip_diagnosis import diagnose_skip_halt

    now = datetime.now(timezone.utc)
    for index in range(5):
        _add_candidate(
            key=f"pi_decision:skip-positive-{index}", action="rejected", net=1.0,
            now=now - timedelta(minutes=index),
        )
        _add_candidate(
            key=f"pi_decision:pass-flat-{index}", action="passed", net=0.0,
            now=now - timedelta(minutes=index),
        )

    report = diagnose_skip_halt(now=now)

    assert report.finding == "overrestriction_suspect"
    assert report.recommended_action == "create_non_executable_replay_draft"
    assert report.side_effects == ()


def test_repeated_complete_skips_without_comparable_pass_are_liveness_incident(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.analytics.skip_diagnosis import diagnose_skip_halt

    now = datetime.now(timezone.utc)
    for index in range(5):
        _add_candidate(
            key=f"pi_decision:liveness-{index}", action="rejected", net=1.0,
            now=now - timedelta(minutes=index),
        )

    report = diagnose_skip_halt(now=now)

    assert report.finding == "operator_liveness_incident"
    assert report.recommended_action == "create_non_executable_replay_draft"


def test_query_failure_is_honest_diagnosis_unavailable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    import aletheia.analytics.skip_diagnosis as diagnosis

    monkeypatch.setattr(diagnosis, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("db unavailable")))

    report = diagnosis.diagnose_skip_halt(now=datetime.now(timezone.utc))

    assert report.finding == "diagnosis_unavailable"
    assert report.recommended_action == "repair_diagnosis_read_path"


def test_explicit_policy_epoch_excludes_pre_epoch_candidates(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from aletheia.agent_shell.skip_policy import ensure_skip_remediation_policy_epoch
    from aletheia.analytics.skip_diagnosis import diagnose_skip_halt

    now = datetime.now(timezone.utc)
    _add_candidate(
        key="pi_decision:legacy", action="rejected", net=-1.0,
        now=now - timedelta(hours=1),
    )
    epoch_id = ensure_skip_remediation_policy_epoch()
    after_epoch = datetime.now(timezone.utc)
    _add_candidate(
        key="pi_decision:b1", action="rejected", net=None,
        now=after_epoch,
        pending=True,
    )

    report = diagnose_skip_halt(now=after_epoch, policy_epoch_id=epoch_id)

    assert report.policy_epoch_id == epoch_id
    assert report.counts["candidate_skips"] == 1
    assert report.finding == "awaiting_outcomes"
