"""Canonical Universe entry to realized Outcome, with isolated venue/Risk doubles.

The shared ancestry/closure/read-cohort and consumer code is never mocked.
Account/fill/funding transport qualification remains owned by its existing suites.
"""
import pytest

from aletheia.storage import repositories as repo
from tests.specs.spec_meta import SpecClaim, spec_claim


@pytest.fixture
def universe_position(monkeypatch, request):
    from aletheia.execution.exchange_materialization import materialize_bitget_entry
    from tests.e2e.test_pipeline_exchange_entry_to_position import (
        _approving_risk, _drive_entry, _private_entry_fill, _wire_venue,
    )
    from tests.test_entry_plan_execution import _persist_waiting_plan

    monkeypatch.setattr("aletheia.execution.entry_plan_execution._require_ai_entry_authority", lambda p: None)
    _approving_risk(monkeypatch)
    _wire_venue(monkeypatch)
    updates = getattr(request, "param", None)
    if updates and updates.get("source_kind") == "serenity":
        from tests.test_candidate_opportunity_persistence import _serenity_publication_source
        _, opportunity = _serenity_publication_source()
        updates = {**updates, "source_id": opportunity.id}
    plan = _persist_waiting_plan(
        mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT",
        plan_updates=updates,
    )
    result = _drive_entry(plan)
    assert result["status"] == "submitted", result
    order = repo.get_order(result["order_id"])
    materialized = materialize_bitget_entry(order["id"], [_private_entry_fill(order)])
    assert materialized.state == "filled"
    position = repo.get_position(materialized.position_id)
    assert position["opportunity_id"] == (plan.source_id if plan.source_kind == "serenity" else None)
    return plan, position


def _close(monkeypatch, position):
    from aletheia.execution.coordinator import close_position
    from tests.e2e.test_pipeline_exchange_position_to_close import _prepare_venue, _settle_pending_close
    from tests.position_fixture import force_update_position

    # Isolate this ancestry regression from broker funding transport. Unknown
    # funding still has a negative case below; the real ledger has its own suite.
    force_update_position(position["id"], funding_cost=0.0, funding_source="broker_ledger")
    _, submitted, private_fill, _ = _prepare_venue(monkeypatch, fill_price=110)
    close_position(position["id"], reason="take_profit")
    assert len(submitted) == 1
    _settle_pending_close(position["id"], private_fill)
    return repo.get_position(position["id"])


@spec_claim(SpecClaim(
    "PL-OF-20", "pipeline_outcome_feedback", "§19 canonical Universe ancestry", "L2c",
    "A canonical Universe Candidate/Generation/EntryPlan chain reaches realized Outcome "
    "and Attribution/Learning without a synthetic Opportunity; existing economics gates still apply.",
    status="implemented",
))
def test_universe_close_reaches_shared_outcome_and_consumers(universe_position, monkeypatch):
    from aletheia.analytics.outcome_integrity import attribution_chain_complete, read_complete_outcome_cohort
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows
    from aletheia.learning.outcomes import assess_realized_position
    from aletheia.portfolio import outcome_hooks

    plan, position = universe_position
    assert repo.get_entry_plan(plan.plan_id)["order_id"] is None
    assert repo.get_order(position["order_id"])["entry_plan_id"] == plan.plan_id
    assert attribution_chain_complete(position)
    closed = _close(monkeypatch, position)
    assert closed["outcome_eligible"] == 1
    assert assess_realized_position(closed).eligible
    cohort = read_complete_outcome_cohort()
    assert cohort.cohort_complete and len(cohort.rows) == 1
    assert cohort.assessments[0].eligible
    [attribution] = build_trade_attribution_rows()
    assert attribution["entry_plan_id"] == plan.plan_id
    assert attribution["planning_generation_id"] == plan.planning_generation_id
    assert attribution["universe_member_id"] == plan.candidate_id
    assert attribution["opportunity_id"] is None
    jobs = repo.list_outcome_hook_jobs(position_id=position["id"])
    assert jobs and all(job["status"] == "completed" for job in jobs)
    assert outcome_hooks.process_outcome_hook_jobs(position_id=position["id"])["claimed"] == 0


@pytest.mark.parametrize("broken_link", ["order", "order_plan", "proposal", "generation", "candidate", "source"])
def test_broken_universe_chain_is_excluded_by_writer_and_snapshot(
    universe_position, monkeypatch, e2e_db, broken_link,
):
    from aletheia.analytics.outcome_integrity import (
        attribution_chain_complete, outcome_write_eligibility, raw_account_events,
    )
    from tests.position_fixture import force_update_position

    plan, position = universe_position
    closed = _close(monkeypatch, position)
    assert closed["outcome_eligible"] == 1
    if broken_link == "order":
        repo.update_order_metadata(position["order_id"], position_id=None)
    elif broken_link == "order_plan":
        # An obsolete reverse cache cannot authorize a wrong current Order link.
        e2e_db.execute("UPDATE entry_plans SET order_id=? WHERE id=?", (position["order_id"], plan.plan_id))
        e2e_db.execute("UPDATE orders SET entry_plan_id='unrelated-plan' WHERE id=?", (position["order_id"],))
    elif broken_link == "proposal":
        force_update_position(position["id"], proposal_id="missing-proposal")
    elif broken_link == "generation":
        e2e_db.execute("UPDATE candidate_planning_generations SET entry_plan_id=NULL WHERE id=?", (plan.planning_generation_id,))
    elif broken_link == "candidate":
        e2e_db.execute("DELETE FROM universe_members WHERE id=?", (plan.candidate_id,))
    else:
        e2e_db.execute("UPDATE universe_snapshots SET source_kind='serenity'")
    row = repo.get_position(position["id"])
    assert not attribution_chain_complete(row)
    assert outcome_write_eligibility(row, exit_fill_verified=True) == (False, "missing_attribution")
    [snapshot] = raw_account_events()
    assert snapshot["_attribution_complete"] == 0


@pytest.mark.parametrize("changes,reason", [
    ({"entry_fill_verified": False}, "unverified_entry_fill"),
    ({"exit_fill_verified": False}, "unverified_exit_fill"),
    ({"exit_fee": None}, "incomplete_realized_economics"),
    ({"funding_cost": None}, "incomplete_funding_economics"),
    ({"funding_source": "local_simulation"}, "incomplete_funding_economics"),
    ({"close_reason": "reconciled_gone"}, "administrative_close"),
    ({"outcome_eligible": False, "outcome_exclusion_reason": "missing_attribution"}, "missing_attribution"),
    ({"closed_at": "2020-01-01T00:00:00+00:00"}, "pre_clean_window"),
])
def test_universe_ancestry_does_not_relax_evidence_or_promote_history(
    universe_position, monkeypatch, changes, reason,
):
    from aletheia.analytics.outcome_integrity import assess_reportable_outcome, raw_account_events
    from tests.position_fixture import force_update_position

    _, position = universe_position
    closed = _close(monkeypatch, position)
    assessment = assess_reportable_outcome({**closed, **changes})
    assert not assessment.eligible
    assert assessment.reason == reason
    force_update_position(position["id"], **changes)
    [persisted] = raw_account_events()
    assert assess_reportable_outcome(persisted).reason == reason


@pytest.mark.parametrize("universe_position", [{"source_id": "unrelated-candidate"}], indirect=True)
def test_plan_source_cannot_name_an_unrelated_candidate(universe_position):
    from aletheia.analytics.outcome_integrity import attribution_chain_complete

    _, position = universe_position
    assert not attribution_chain_complete(position)


@pytest.mark.parametrize("universe_position", [
    {"source_kind": "serenity", "trade_school": "serenity"},
], indirect=True)
def test_canonical_serenity_retains_its_exact_opportunity(universe_position):
    from aletheia.analytics.outcome_integrity import attribution_chain_complete

    plan, position = universe_position
    assert position["opportunity_id"] == plan.source_id
    assert attribution_chain_complete(position)
    assert not attribution_chain_complete({**position, "opportunity_id": None})
    assert not attribution_chain_complete({**position, "opportunity_id": "unrelated"})


def test_executed_ancestry_survives_candidate_expiry_and_generation_supersession(
    universe_position, e2e_db,
):
    from aletheia.analytics.outcome_integrity import attribution_chain_complete

    plan, position = universe_position
    e2e_db.execute(
        "UPDATE universe_members SET execution_eligible=0, expires_at='2020-01-01' WHERE id=?",
        (plan.candidate_id,),
    )
    e2e_db.execute(
        "UPDATE candidate_planning_generations SET is_current=0, status='superseded' WHERE id=?",
        (plan.planning_generation_id,),
    )
    assert attribution_chain_complete(position)
