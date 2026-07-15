"""B1.1 regression: Ranker supply drought cannot permanently seal PI input."""

from pathlib import Path
from datetime import timedelta

from aletheia.simulation.control_loop import ControlLoopHarness
from aletheia.simulation.control_loop.invariants import assert_invariants


def _record_confidence_drought(harness: ControlLoopHarness) -> None:
    """Use the production event store to model two ranker telemetry cycles."""
    from aletheia.agent_shell.operator_store import create_event

    payload = {
        "executable_count": 0,
        "floor": 3,
        "below_floor": True,
        "required_consecutive_cycles": 2,
        "main_reason": "confidence_below",
        "excluded_candidates_summary": [
            {"reason": "confidence_below", "count": 12, "examples": []},
        ],
    }
    for index in range(2):
        create_event(
            event_type="candidate_supply_snapshot",
            severity="warning",
            source="trading_context",
            related_entity_type="candidate_supply",
            related_entity_id=f"b11-cycle-{index}",
            payload=payload,
        )


def test_auto_tightened_confidence_recovers_when_ranker_starves_pi(tmp_path):
    """No-fill cold start is recoverable without weakening guards or risk."""
    with ControlLoopHarness(Path(tmp_path), initial_confidence="high") as harness:
        from aletheia.agent_shell import operator_loop

        # This is the durable provenance of the legacy 36.7% aggregate-health
        # action.  A new B1.1 deployment must repair this state even though there
        # has never been a fill, so hours_since_last_trade is None.
        operator_loop._persist_action(
            "config_patch",
            param="min_confidence_to_trade",
            before="medium",
            after="high",
            direction="tighten",
            reason="legacy aggregate signal health tightening",
        )
        # The real legacy patch predates B1.1 by hours.  Keep the regular
        # per-parameter cooldown intact; this test is about recovery after that
        # bounded delay, not bypassing it.
        harness.db.execute(
            "UPDATE audit_events SET timestamp=? WHERE actor='operator_loop' "
            "AND event_type='config_patch' AND entity_id='min_confidence_to_trade'",
            ((harness.clock.now() - timedelta(hours=3)).isoformat(),),
        )
        harness.db.commit()
        _record_confidence_drought(harness)

        summary = harness.run_operator_cycle(trigger="b11-confidence-supply")

        assert summary["assessment"]["hours_since_last_trade"] is None
        assert summary["assessment"]["candidate_supply_drought"]["main_reason"] == "confidence_below"
        assert harness.load_config().trading.min_confidence_to_trade == "medium"
        loosen = [r for r in summary["results"] if r.get("action") == "loosen_confidence"]
        assert len(loosen) == 1 and loosen[0]["status"] == "executed"
        assert loosen[0]["old"] == "high"
        audit = harness.db.execute(
            "SELECT message FROM audit_events WHERE actor='operator_loop' "
            "AND event_type='config_patch' AND entity_id='min_confidence_to_trade' "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()[0]
        assert "candidate supply drought" in audit
        epoch = harness.db.execute(
            "SELECT effective_values_json FROM learning_policy_epochs "
            "WHERE epoch_kind='system_operator_skip_remediation' "
            "AND ended_at IS NULL"
        ).fetchone()
        assert epoch is not None
        assert '"version": "b1.1"' in epoch[0]
        assert_invariants(harness)


def test_manual_high_is_not_overridden_by_candidate_supply_drought(tmp_path):
    """B1.1 restores only a legacy automatic patch, never an owner control."""
    with ControlLoopHarness(Path(tmp_path), initial_confidence="high") as harness:
        _record_confidence_drought(harness)

        summary = harness.run_operator_cycle(trigger="b11-manual-high")

        assert summary["assessment"]["candidate_supply_drought"]["main_reason"] == "confidence_below"
        assert harness.load_config().trading.min_confidence_to_trade == "high"
        assert not any(r.get("action") == "loosen_confidence" for r in summary["results"])
        assert_invariants(harness)
