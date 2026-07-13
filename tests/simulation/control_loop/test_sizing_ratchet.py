"""Fixed regression for the one-way sizing ratchet incident."""

from datetime import datetime
from pathlib import Path

import yaml

from aletheia.simulation.control_loop import ControlLoopHarness
from aletheia.simulation.control_loop.invariants import assert_invariants


SCENARIO = Path(__file__).resolve().parents[2] / "fixtures/control_loop/sizing_ratchet_20260713.yaml"


def test_sizing_ratchet_recovers_with_real_dispatch_and_persistence(tmp_path):
    scenario = yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))
    state = scenario["initial_state"]
    with ControlLoopHarness(
        tmp_path,
        now=datetime.fromisoformat(state["now"]),
        initial_sizing=state["max_demo_position_notional_usdt"],
        kill_switch=state["kill_switch_active"],
        run_id=scenario["incident_id"],
        reproduce=(
            "uv run pytest "
            "tests/simulation/control_loop/test_sizing_ratchet.py -q"
        ),
    ) as harness:
        harness.change_signal_quality(
            win_rate=state["signal_win_rate_24h"], status="healthy"
        )
        harness.change_trade_performance(
            win_rate=state["win_rate_7d"], total=10,
            hours_since_last_fill=state["hours_since_last_fill"],
        )
        first = harness.run_operator_cycle(trigger=scenario["incident_id"])
        assert_invariants(harness)
        increases = [
            result for result in first["results"]
            if result.get("action") == "increase_sizing"
        ]
        assert increases and increases[0]["status"] == "executed"
        assert 100.0 < harness.load_config().risk.max_demo_position_notional_usdt <= 200.0

        harness.restart()
        persisted = harness.load_config().risk.max_demo_position_notional_usdt
        second = harness.run_operator_cycle(trigger=scenario["incident_id"])
        assert harness.load_config().risk.max_demo_position_notional_usdt == persisted
        assert any(
            result.get("action") == "increase_sizing"
            and result.get("status") == "cooldown"
            for result in second["results"]
        )
        assert_invariants(harness)
