"""DCLS can model diagnosis without letting it mutate a trading control."""

from pathlib import Path

from aletheia.analytics.skip_diagnosis import diagnose_skip_halt
from aletheia.simulation.control_loop import ControlLoopHarness
from aletheia.simulation.control_loop.invariants import assert_invariants


def test_degraded_aggregate_signal_creates_no_trading_side_effect(tmp_path):
    with ControlLoopHarness(Path(tmp_path)) as harness:
        harness.change_signal_quality(win_rate=33.0, status="degraded")

        summary = harness.run_operator_cycle(trigger="dcls-skip-diagnosis")
        diagnosis = diagnose_skip_halt()

        assert diagnosis.finding == "not_applicable"
        assert not any(
            row.get("action") == "tighten_confidence"
            for row in summary["results"]
        )
        assert harness.load_config().trading.min_confidence_to_trade == "medium"
        assert harness.db.execute("SELECT COUNT(*) FROM trade_proposals").fetchone()[0] == 0
        assert harness.db.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0] == 0
        assert_invariants(harness)
