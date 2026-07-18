"""Executable claims for docs/design/filter_evidence_contract.md."""

import pytest

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
        "scanner_family", "horizon", "entry_price_usd", "entry_spread_bps",
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
        "A directionless filter candidate records the decision but creates no learnable outcome rows.",
        status="implemented",
    )
)
def test_directionless_filter_candidate_does_not_create_outcomes():
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

    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_candidate_outcomes WHERE outcome_candidate_key=?",
        (key,),
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "FE-B04", _CONTRACT, "§4 fixed-horizon settlement", "L2b",
        "Due filter candidate outcomes settle through the shared exact-instrument Stage-D economics path.",
        status="implemented",
    )
)
def test_filter_outcome_settler_is_wired_into_universe_settlement():
    from pathlib import Path
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    source = (
        Path(__file__).parents[2] / "aletheia" / "universe" / "outcome_recorder.py"
    ).read_text(encoding="utf-8")
    assert callable(settle_due_filter_outcomes)
    assert "settle_due_filter_outcomes" in source


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
        "Ranker and System Operator alpha baselines consume net_pnl_pct rather than legacy shadow gross PnL.",
        status="implemented",
    )
)
def test_alpha_attribution_uses_shadow_net_pnl():
    from pathlib import Path

    source = (
        Path(__file__).parents[2] / "aletheia" / "analytics" / "alpha_attribution.py"
    ).read_text(encoding="utf-8")
    assert "SELECT batch_id, rank, net_pnl_pct FROM shadow_trades" in source
    assert "SELECT net_pnl_pct FROM shadow_trades" in source


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
        Path(__file__).parents[2] / "aletheia" / "analytics" / "causal_controls.py"
    ).read_text(encoding="utf-8")
    alpha_source = (
        Path(__file__).parents[2] / "aletheia" / "analytics" / "alpha_attribution.py"
    ).read_text(encoding="utf-8")
    assert "outcome_candidate_key" in causal_source
    assert "SELECT net_return_pct FROM filter_candidate_outcomes" in alpha_source


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
        "FE-B06", _CONTRACT, "§5 System Operator gates", "L2b",
        "System Operator decisions carry debate, conviction and direction-brake filter traces to persistence.",
        status="implemented",
    )
)
def test_system_operator_carries_filter_trace_to_reflection():
    from pathlib import Path
    from aletheia.agent_shell.trading_decision import TradingDecision

    assert "filter_trace" in TradingDecision.__dataclass_fields__
    command_source = (
        Path(__file__).parents[2] / "aletheia" / "agent_shell" / "commands.py"
    ).read_text(encoding="utf-8")
    reflection_source = (
        Path(__file__).parents[2] / "aletheia" / "agent_shell" / "reflection.py"
    ).read_text(encoding="utf-8")
    assert '"filter_trace": decision.filter_trace' in command_source
    assert "record_operator_filter_trace" in reflection_source


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
        "FE-S14", _CONTRACT, "§3 terminal System Operator evidence", "L2b",
        "A candidate-scoped terminal PI skip atomically persists its operator trace and "
        "one shared fixed-horizon outcome cohort; evidence failure leaves the pending "
        "carrier unconsumed.",
    )
)
def test_candidate_skip_claim_is_atomic_with_operator_evidence(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import claim_decision
    from aletheia.analytics import filter_evidence
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-atomic-skip-evidence",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="pending-atomic-skip-evidence",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))

    def fail_evidence(*_args, **_kwargs):
        raise RuntimeError("forced operator evidence failure")

    monkeypatch.setattr(filter_evidence, "record_operator_filter_trace", fail_evidence)

    with pytest.raises(RuntimeError, match="forced operator evidence failure"):
        claim_decision({
            "action": "skip",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 31,
            "reasoning": "direct model skip",
            "opportunity_id": opportunity.id,
            "signal_source": "market_observer",
            "filter_trace": [{
                "filter_name": "operator_action_assessment",
                "stage": "system_operator",
                "decision": "rejected",
                "reason_code": "operator_model_skip",
                "inputs": {"model_action": "skip", "initial_conviction": 31},
                "parameters": {"schema_actions": ["trade", "skip"]},
            }],
        })

    db = get_db()
    assert db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? AND action='skip'",
        (opportunity.id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,)
    ).fetchone()[0] == "queued"
    assert db.execute(
        "SELECT COUNT(*) FROM filter_decisions WHERE opportunity_id=?", (opportunity.id,)
    ).fetchone()[0] == 0


def test_candidate_skip_claim_creates_one_shared_fixed_outcome_cohort(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import claim_decision
    from aletheia.storage.models import MarketSnapshot, Opportunity, PiDecision
    from aletheia.storage.repositories import (
        insert_market_snapshot,
        insert_opportunity,
        insert_pi_decision,
    )
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-skip-outcome-cohort",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="pending-skip-outcome-cohort",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))
    insert_market_snapshot(MarketSnapshot(
        symbol=opportunity.symbol,
        market_type="spot",
        exchange="test",
        price=100.0,
    ))

    claimed, created = claim_decision({
        "action": "skip",
        "symbol": opportunity.symbol,
        "direction": "long",
        "market_type": "spot",
        "conviction": 31,
        "reasoning": "direct model skip",
        "opportunity_id": opportunity.id,
        "signal_source": "market_observer",
        "filter_trace": [{
            "filter_name": "operator_action_assessment",
            "stage": "system_operator",
            "decision": "rejected",
            "reason_code": "operator_model_skip",
            "inputs": {"model_action": "skip", "initial_conviction": 31},
            "parameters": {"schema_actions": ["trade", "skip"]},
        }],
    })

    assert created is True
    db = get_db()
    trace_rows = db.execute(
        "SELECT candidate_key, outcome_candidate_key, reason_code FROM filter_decisions "
        "WHERE candidate_key=?",
        (f"pi_decision:{claimed['id']}",),
    ).fetchall()
    outcomes = db.execute(
        "SELECT horizon, entry_price_usd, direction, market_type FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? ORDER BY horizon",
        (f"pi_decision:{claimed['id']}",),
    ).fetchall()

    assert [dict(row) for row in trace_rows] == [{
        "candidate_key": f"pi_decision:{claimed['id']}",
        "outcome_candidate_key": f"pi_decision:{claimed['id']}",
        "reason_code": "operator_model_skip",
    }]
    assert {row["horizon"] for row in outcomes} == {"15m", "1h", "4h", "24h"}
    assert {row["entry_price_usd"] for row in outcomes} == {100.0}
    assert {(row["direction"], row["market_type"]) for row in outcomes} == {("long", "spot")}


def test_candidate_skip_without_exact_entry_snapshot_rolls_back(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.reflection import claim_decision
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
    from aletheia.storage.sqlite import get_db

    opportunity = Opportunity(
        id="opp-skip-without-entry-snapshot",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
        scanner_family="momentum",
    )
    insert_opportunity(opportunity)
    insert_pi_decision(PiDecision(
        id="pending-skip-without-entry-snapshot",
        action="signal_pending",
        opportunity_id=opportunity.id,
        signal_source="market_observer",
        symbol=opportunity.symbol,
        direction="long",
        market_type="spot",
    ))

    with pytest.raises(ValueError, match="terminal_operator_outcome_unavailable"):
        claim_decision({
            "action": "skip",
            "symbol": opportunity.symbol,
            "direction": "long",
            "market_type": "spot",
            "conviction": 31,
            "reasoning": "direct model skip without exact price",
            "opportunity_id": opportunity.id,
            "signal_source": "market_observer",
            "filter_trace": [{
                "filter_name": "operator_action_assessment",
                "stage": "system_operator",
                "decision": "rejected",
                "reason_code": "operator_model_skip",
                "inputs": {"model_action": "skip", "initial_conviction": 31},
                "parameters": {"schema_actions": ["trade", "skip"]},
            }],
        })

    db = get_db()
    assert db.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE opportunity_id=? AND action='skip'",
        (opportunity.id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM opportunities WHERE id=?", (opportunity.id,)
    ).fetchone()[0] == "queued"


@spec_claim(
    SpecClaim(
        "FE-S15", _CONTRACT, "§3 terminal System Operator evidence", "L2b",
        "Doctor distinguishes unscoped heartbeat skips and candidate skips missing "
        "trace/outcome evidence, without changing a decision or configuration.",
    )
)
def test_doctor_surfaces_skip_halt_evidence_gaps_read_only(tmp_path, monkeypatch):
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
    assert check.severity == "warn"
    assert "raw_24h_candidate_skips=1" in check.detail
    assert "raw_24h_missing_trace=1" in check.detail
    assert "raw_24h_missing_outcome_seed=1" in check.detail
    assert "raw_24h_direct_model_skips=0" in check.detail
    assert "raw_24h_trade_intent_gated_skips=0" in check.detail
    assert "raw_24h_confirmed_adverse_skips=0" in check.detail
    assert "unscoped_heartbeat_skips=1" in check.detail
    assert "no_evaluable_candidate_events=0" in check.detail
    assert "deferred_unchanged_context_events=0" in check.detail
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
        "FE-S16", _CONTRACT, "§3 terminal System Operator evidence", "L2b",
        "Doctor keeps pre-remediation evidence gaps in raw counts but evaluates the "
        "active Skip-remediation epoch independently, without changing a decision.",
    )
)
def test_doctor_excludes_pre_remediation_skip_gaps_from_current_finding(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.skip_policy import ensure_skip_remediation_policy_epoch
    from aletheia.diagnostics import doctor
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision

    # This record represents the reload-era incomplete evidence.  It remains
    # visible in the raw 24h count, but must not diagnose the newly started B1
    # policy epoch as broken.
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
        "RiskDecision remains authoritative while a report-only risk filter fact is appended after persistence.",
        status="implemented",
    )
)
def test_risk_evidence_is_appended_after_authoritative_decision():
    from pathlib import Path
    from aletheia.analytics.filter_registry import filter_by_name

    source = (
        Path(__file__).parents[2] / "aletheia" / "risk" / "engine.py"
    ).read_text(encoding="utf-8")
    assert source.index("insert_risk_decision(decision)") < source.index(
        "record_risk_filter_decision"
    )
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
        "FE-C02", _CONTRACT, "§7 selection order", "L2c",
        "Signal selection backfills lower-ranked candidates when a higher-ranked candidate is already open or cooling down.",
        status="implemented",
    )
)
def test_signal_selection_backfills_after_early_rejection():
    from aletheia.agents.paper_trader import _plan_signal_batch

    candidates = [
        {"opportunity_id": "a", "symbol": "AAA/USDT", "direction": "long"},
        {"opportunity_id": "b", "symbol": "BBB/USDT", "direction": "long"},
        {"opportunity_id": "c", "symbol": "CCC/USDT", "direction": "long"},
        {"opportunity_id": "d", "symbol": "DDD/USDT", "direction": "long"},
    ]
    plan = _plan_signal_batch(
        candidates,
        open_symbols={"AAA/USDT"},
        recent_signal_pairs={("BBB/USDT", "long")},
        limit=2,
    )

    assert [row["opportunity_id"] for row in plan.selected] == ["c", "d"]
    assert plan.decisions["a"] == ("open_position_duplicate", "rejected")
    assert plan.decisions["b"] == ("signal_cooldown", "rejected")


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
    from aletheia.analytics.filter_registry import filter_by_name

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
    from aletheia.analytics.filter_registry import filter_by_name

    names = {
        "execution_capability",
        "execution_eligibility",
        "direction_present",
        "universe_execution_eligibility",
        "family_ttl",
        "venue_symbol_capability",
        "factor_hard_block",
        "guard_candidate_block",
        "duplicate_symbol",
        "open_position_duplicate",
        "signal_cooldown",
        "signal_batch_limit",
    }
    specs = {name: filter_by_name(name) for name in names}

    assert all(specs.values())
    assert specs["family_ttl"].feedback_source == "forward_return"
    assert specs["family_ttl"].feedback_ready is True
    for name in names - {"family_ttl", "signal_cooldown", "signal_batch_limit"}:
        assert specs[name].auto_tunable is False


@spec_claim(
    SpecClaim(
        "FE-B11",
        _CONTRACT,
        "§3 Guard evidence truthfulness",
        "L2a",
        "guard_candidate_block follows Guard configuration independently of Learning, records "
        "not_applicable when Guard enforcement is disabled and error when evaluation fails; "
        "only a completed non-blocking evaluation may be passed.",
        status="implemented",
    )
)
def test_guard_filter_has_explicit_not_evaluated_states():
    import inspect

    from aletheia.agents import paper_trader_ranker as ranker

    applicability = inspect.getsource(ranker._guard_evaluation_applicability)
    candidate = inspect.getsource(ranker._evaluate_candidate)
    assert "learning_influence_disabled" not in applicability
    assert '"not_applicable", "guard_disabled"' in applicability
    assert '"not_applicable", "guard_ranker_enforcement_disabled"' in applicability
    assert '"error", "guard_applicability_error"' in applicability
    assert 'guard_reason = "guard_evaluation_failed"' in candidate
    assert "if guard_eval is None" in candidate


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

    from aletheia.analytics import filter_evidence, filter_replay

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

    from aletheia.analytics import filter_shadow_experiments

    source = inspect.getsource(filter_shadow_experiments)
    assert "prospective_first_exposure" in source
    assert "production_mutation_allowed" in source
    assert "save_config" not in source
    assert "ready_for_review" in source
    from aletheia.storage import migrations

    assert "idx_filter_shadow_experiments_fixed_scope" in migrations._FILTER_SHADOW_EXPERIMENTS_DDL


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
        "FE-S16", _CONTRACT, "§3 terminal System Operator evidence", "L2b",
        "A terminal skip preserves its complete rationale and structured supporting, "
        "opposing, risk, and adverse evidence in the durable PiDecision evidence manifest.",
        status="implemented",
    ),
)
def test_terminal_skip_preserves_complete_structured_rationale():
    import json

    from aletheia.agent_shell.reflection import _build_pi_decision

    reasoning = "candidate-specific adverse thesis " + ("x" * 900)
    result = {
        "action": "skip",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "conviction": 30,
        "reasoning": reasoning,
        "skip_kind": "adverse_candidate_evidence",
        "adverse_reason_codes": ["regime_mismatch"],
        "adverse_evidence": ["1h range rejection remained intact"],
        "supporting_signals": ["15m momentum turned positive"],
        "opposing_signals": ["1h structure rejected the breakout"],
        "risk_factors": ["range whipsaw"],
        "evidence_manifest": {
            "operator_rationale": {
                "skip_kind": "adverse_candidate_evidence",
                "adverse_reason_codes": ["regime_mismatch"],
                "adverse_evidence": ["1h range rejection remained intact"],
                "supporting_signals": ["15m momentum turned positive"],
                "opposing_signals": ["1h structure rejected the breakout"],
                "risk_factors": ["range whipsaw"],
            }
        },
    }

    decision = _build_pi_decision(result)
    manifest = json.loads(decision.evidence_manifest_json)

    assert decision.reasoning == reasoning
    assert manifest["operator_rationale"]["skip_kind"] == "adverse_candidate_evidence"
    assert manifest["operator_rationale"]["supporting_signals"] == [
        "15m momentum turned positive"
    ]


def test_decision_evidence_builder_carries_operator_rationale(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import build_decision_evidence_manifest
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity

    insert_opportunity(Opportunity(
        id="opp-manifest-v2", symbol="BTC/USDT", market="spot",
        direction_bias="long", status="queued", scanner_family="momentum",
        universe_snapshot_id="universe-snapshot-1",
        universe_member_id="universe-member-1",
    ))

    decision = TradingDecision(
        action="skip",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        conviction=30,
        reasoning="confirmed candidate-specific conflict",
        skip_kind="adverse_candidate_evidence",
        adverse_reason_codes=["signal_conflict"],
        adverse_evidence=["15m and 1h directions conflict"],
        supporting_signals=["15m momentum"],
        opposing_signals=["1h rejection"],
        risk_factors=["whipsaw"],
        opportunity_id="opp-manifest-v2",
        scanner_family="momentum",
        validated_adverse_claims=[{
            "reason_code": "signal_conflict",
            "fact_refs": ["technical.return_1h", "technical.return_4h"],
        }],
    )

    manifest = build_decision_evidence_manifest({
        "skip_policy_epoch_id": "skip-b1.3",
        "layer1": {
            "regime": {"state": "bearish", "confidence": 0.8, "long_bias": 0.2, "short_bias": 0.8},
            "feature_index": {"spot:BTC/USDT": {
                "feature_snapshot_id": "feature-1", "feature_ts": "2026-07-18T01:00:00Z",
                "data_freshness_sec": 42, "symbol": "BTC/USDT", "market_type": "spot",
            }},
        },
        "layer2": {
            "outcome_stats": {"total_trades": 1, "wins": 0, "losses": 1, "total_pnl": -2.06},
            "pending_signals": [{
                "opportunity_id": "opp-manifest-v2", "symbol": "BTC/USDT",
                "direction": "long", "market_type": "spot", "trade_score": 51,
                "scanner_family": "momentum", "b2_evidence": {
                    "status": "insufficient_evidence", "n_total": 8, "n_net": 0,
                    "outcome_coverage": 0.0, "avg_net_pct": None,
                    "shrunk_avg_net_pct": None, "directional_verdict": None,
                },
            }],
        },
    }, decision)
    rationale = manifest["operator_rationale"]

    assert rationale == {
        "model_reasoning": None,
        "skip_kind": "adverse_candidate_evidence",
        "adverse_reason_codes": ["signal_conflict"],
        "adverse_evidence": ["15m and 1h directions conflict"],
        "supporting_signals": ["15m momentum"],
        "opposing_signals": ["1h rejection"],
        "risk_factors": ["whipsaw"],
        "validated_adverse_claims": [{
            "reason_code": "signal_conflict",
            "fact_refs": ["technical.return_1h", "technical.return_4h"],
        }],
    }
    assert manifest["decision_evidence_version"] == "operator-candidate-v2"
    assert manifest["source_chain"]["opportunity_id"] == "opp-manifest-v2"
    assert manifest["source_chain"]["universe_snapshot_id"] == "universe-snapshot-1"
    assert manifest["source_chain"]["universe_member_id"] == "universe-member-1"
    assert manifest["technical_snapshot"]["data_freshness_sec"] == 42
    assert manifest["candidate_selection"]["rank_priority_semantics"] == "ordering_only"
    assert manifest["b2_candidate_evidence"]["n_total"] == 8
    assert manifest["market_regime"]["state"] == "bearish"
    assert manifest["recent_performance"]["total_trades"] == 1


@spec_claim(
    SpecClaim(
        "FE-S19", _CONTRACT, "§3 current progress projection", "L2a",
        "Overview and CLI derive current Ranker progress from durable facts without re-running Ranker.",
        status="implemented",
    )
)
def test_opportunity_progress_read_model_does_not_import_ranker_preview():
    from pathlib import Path

    source = (
        Path(__file__).parent.parent.parent
        / "aletheia/analytics/opportunity_progress.py"
    ).read_text(encoding="utf-8")
    assert "paper_trader_ranker" not in source
    assert "build_paper_candidates" not in source
    assert "filter_decisions" in source
    assert "FROM agent_runs" in source
