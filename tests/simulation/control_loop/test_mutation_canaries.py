"""Each canary must make an independent invariant reject a real-path mutation."""

from contextlib import ExitStack, contextmanager

import pytest

from aletheia.simulation.control_loop import ControlLoopHarness
from aletheia.simulation.control_loop.invariants import (
    ControlLoopInvariantError,
    assert_invariants,
)


@contextmanager
def _mutation(name, operator_loop, harness):
    with ExitStack() as stack:
        if name == "classifier":
            original = operator_loop.classify_action
            stack.enter_context(pytest.MonkeyPatch.context())
            operator_loop.classify_action = lambda action: 2 if action == "loosen_confidence" else original(action)
        elif name == "decision":
            original = operator_loop.decide_actions
            operator_loop.decide_actions = lambda assessment: [
                action for action in original(assessment)
                if action.action_type != "loosen_confidence"
            ]
        elif name == "dispatcher":
            original = operator_loop._execute_level1_action
            operator_loop._execute_level1_action = lambda action: (
                {"action": action.action_type, "status": "proposed"}
                if action.action_type == "loosen_confidence" else original(action)
            )
        elif name == "handler":
            operator_loop._action_loosen_confidence = lambda action: {
                "action": "loosen_confidence", "status": "executed",
                "param": "min_confidence_to_trade", "old": "high", "new": "medium",
            }
        elif name == "bounds":
            def escape_bounds(action):
                cfg = harness.load_config()
                cfg.risk.max_demo_position_margin_usdt = 600.0
                harness._loader.save_config(cfg)
                return {"action": "loosen_confidence", "status": "executed", "param": "max_demo_position_margin_usdt"}
            operator_loop._action_loosen_confidence = escape_bounds
        elif name == "audit":
            operator_loop._persist_action = lambda *args, **kwargs: None
        try:
            yield
        finally:
            # Reloading only the mutated module restores functions without changing DB state.
            import importlib
            importlib.reload(operator_loop)


@pytest.mark.parametrize("mutation", ["classifier", "decision", "dispatcher", "handler", "bounds", "audit"])
def test_mutation_is_caught_by_independent_invariant(tmp_path, mutation):
    import aletheia.agent_shell.operator_loop as operator_loop
    with ControlLoopHarness(tmp_path, initial_confidence="high") as harness:
        harness.change_signal_quality(win_rate=37.2, status="degraded")
        harness.change_trade_performance(win_rate=10.0, total=20, hours_since_last_fill=5.0)
        with _mutation(mutation, operator_loop, harness):
            harness.run_operator_cycle(trigger=f"dcls-mutation-{mutation}")
            with pytest.raises(ControlLoopInvariantError):
                assert_invariants(harness)
