"""Small deterministic PR state machine; nightly CI raises its bounds."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import tempfile

from hypothesis import seed as hypothesis_seed
from hypothesis import settings, strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

from aletheia.simulation.control_loop import ControlLoopHarness
from aletheia.simulation.control_loop.invariants import assert_invariants


SEED = int(os.getenv("DCLS_SEED", "20260713"))
EXAMPLES = int(os.getenv("DCLS_MAX_EXAMPLES", "8"))
STEPS = int(os.getenv("DCLS_STATEFUL_STEPS", "8"))


def _reproduce_command(seed: int, examples: int, steps: int) -> str:
    return (
        f"DCLS_SEED={seed} DCLS_MAX_EXAMPLES={examples} "
        f"DCLS_STATEFUL_STEPS={steps} "
        "uv run pytest tests/simulation/control_loop/test_stateful_control_loop.py -q"
    )


@hypothesis_seed(SEED)
@settings(max_examples=EXAMPLES, stateful_step_count=STEPS, deadline=None)
class ControlLoopStateMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self._temp = tempfile.TemporaryDirectory(prefix="dcls-")
        self.harness = ControlLoopHarness(Path(self._temp.name))

    @rule(hours=st.sampled_from([0.0, 1.0, 2.0, 4.0, 8.0]))
    def advance_time(self, hours):
        self.harness.advance_time(hours)

    @rule(win_rate=st.sampled_from([20.0, 37.2, 40.0, 60.0]), status=st.sampled_from(["healthy", "degraded"]))
    def change_signal_quality(self, win_rate, status):
        self.harness.change_signal_quality(win_rate=win_rate, status=status)

    @rule(win_rate=st.sampled_from([10.0, 34.0, 50.0, 80.0]), total=st.sampled_from([3, 10, 21]), hours=st.sampled_from([1.0, 4.0, 8.0]))
    def change_trade_performance(self, win_rate, total, hours):
        self.harness.change_trade_performance(
            win_rate=win_rate, total=total, hours_since_last_fill=hours
        )

    @rule(high=st.integers(min_value=0, max_value=2), medium=st.integers(min_value=0, max_value=5))
    def seed_candidates(self, high, medium):
        self.harness.seed_candidates(high=high, medium=medium)

    @rule()
    def run_operator_cycle(self):
        self.harness.run_operator_cycle(trigger="dcls-stateful")

    @rule(won=st.booleans())
    def record_outcome(self, won):
        self.harness.record_outcome(won=won)

    @rule()
    def restart(self):
        self.harness.restart()

    @rule()
    def record_result(self):
        self.harness.record_result()

    @invariant()
    def control_invariants_hold(self):
        assert_invariants(
            self.harness,
            artifact_root=Path(os.getenv("DCLS_ARTIFACT_ROOT", "test-results/control-loop")),
            run_id=f"stateful-{SEED}", seed=SEED,
            reproduce=_reproduce_command(SEED, EXAMPLES, STEPS),
        )

    def teardown(self):
        self.harness.close()
        self._temp.cleanup()


TestControlLoopStateMachine = ControlLoopStateMachine.TestCase


def _run_seeded_campaign(seed: int, *, examples: int, steps: int) -> list[dict]:
    """Run the real state machine and retain each generated sequence and digest."""
    completed: list[dict] = []

    class RecordingControlLoopStateMachine(ControlLoopStateMachine):
        def teardown(self):
            completed.append({
                "operations": deepcopy(self.harness.operations),
                "digest": self.harness.digest(),
            })
            super().teardown()

    hypothesis_seed(seed)(RecordingControlLoopStateMachine)
    run_state_machine_as_test(
        RecordingControlLoopStateMachine,
        settings=settings(
            max_examples=examples,
            stateful_step_count=steps,
            deadline=None,
            database=None,
        ),
    )
    return completed


def test_dcls_seed_controls_generated_sequences_and_final_digests():
    first = _run_seeded_campaign(20260713, examples=4, steps=8)
    repeated = _run_seeded_campaign(20260713, examples=4, steps=8)
    different = _run_seeded_campaign(20260714, examples=4, steps=8)

    assert first == repeated
    assert [run["operations"] for run in first] != [
        run["operations"] for run in different
    ]


def test_failure_replay_command_preserves_every_generation_input():
    assert _reproduce_command(17, 50, 30) == (
        "DCLS_SEED=17 DCLS_MAX_EXAMPLES=50 DCLS_STATEFUL_STEPS=30 "
        "uv run pytest tests/simulation/control_loop/test_stateful_control_loop.py -q"
    )


def _replay(root: Path) -> str:
    with ControlLoopHarness(root, initial_confidence="high", initial_sizing=100.0) as harness:
        harness.change_signal_quality(win_rate=37.2, status="degraded")
        harness.change_trade_performance(win_rate=80.0, total=10, hours_since_last_fill=5.0)
        harness.seed_candidates(high=1, medium=4)
        harness.run_operator_cycle(trigger="dcls-replay")
        harness.restart()
        harness.record_result()
        assert_invariants(harness)
        return harness.digest()


def test_same_seed_and_operations_produce_same_state(tmp_path):
    assert _replay(tmp_path / "first") == _replay(tmp_path / "second")
