"""Regression scenario for the 2026-07-13 confidence control-loop deadlock."""

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from aletheia.simulation.control_loop import ControlLoopHarness
from aletheia.simulation.control_loop.invariants import assert_invariants


SCENARIO_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures/control_loop/confidence_deadlock_20260713.yaml"
)


def test_confidence_deadlock_recovers_or_persists_visible_blocker(tmp_path):
    scenario = yaml.safe_load(SCENARIO_PATH.read_text(encoding="utf-8"))
    state = scenario["initial_state"]
    with ControlLoopHarness(
        tmp_path,
        now=datetime.fromisoformat(state["now"]),
        initial_confidence=state["min_confidence_to_trade"],
        kill_switch=state["kill_switch_active"],
        run_id=scenario["incident_id"],
        reproduce=(
            "uv run pytest "
            "tests/simulation/control_loop/test_confidence_deadlock.py -q"
        ),
    ) as harness:
        harness.change_signal_quality(
            win_rate=state["signal_win_rate_24h"], status="degraded"
        )
        harness.change_trade_performance(
            win_rate=state["win_rate_7d"], total=21,
            hours_since_last_fill=state["hours_since_last_fill"],
        )
        harness.seed_candidates(**state["candidates"])

        first = harness.run_operator_cycle(trigger=scenario["incident_id"])
        assert_invariants(harness)
        assert first["assessment"]["signal_win_rate"] == pytest.approx(37.2)
        assert first["assessment"]["win_rate_7d"] == pytest.approx(9.5, abs=0.1)
        assert first["assessment"]["hours_since_last_trade"] == pytest.approx(34.5)

        current_confidence = harness.load_config().trading.min_confidence_to_trade
        blockers = harness.db.execute(
            "SELECT * FROM audit_events "
            "WHERE event_type='operator_control_loop_blocked' "
            "AND entity_id='min_confidence_to_trade'"
        ).fetchall()
        recovered = current_confidence == "medium"
        assert recovered or blockers, (
            "liveness invariant failed: confidence stayed tightened with no durable, "
            "visible blocker"
        )

        loosen_results = [
            result for result in first["results"]
            if result.get("action") == "loosen_confidence"
        ]
        assert not any(
            result.get("status") == "proposed" for result in loosen_results
        ), "loosen_confidence was orphaned in an unconsumed proposal"

        if recovered:
            assert any(result.get("status") == "executed" for result in loosen_results)
            audit = harness.db.execute(
                "SELECT * FROM audit_events WHERE event_type='config_patch' "
                "AND entity_id='min_confidence_to_trade' AND after_state='medium'"
            ).fetchall()
            assert audit, "confidence recovery was not persisted to the audit log"
            assert "confidence drought: no fills in 34.5h" in audit[0]["message"]
            assert "high→medium" in audit[0]["message"]

            candidates = harness.run_candidate_filter()
            medium = [c for c in candidates if c.get("confidence") == "medium"]
            assert len(medium) == state["candidates"]["medium"]
            assert all(
                any(
                    step["filter_name"] == "ranker_confidence_gate"
                    and step["decision"] == "passed"
                    for step in candidate["filter_trace"]
                )
                for candidate in medium
            )

            second = harness.run_operator_cycle(trigger=scenario["incident_id"])
            assert_invariants(harness)
            assert not any(
                result.get("action") == "tighten_confidence"
                and result.get("status") in {"executed", "proposed"}
                for result in second["results"]
            ), "unchanged facts immediately re-tightened confidence after recovery"
