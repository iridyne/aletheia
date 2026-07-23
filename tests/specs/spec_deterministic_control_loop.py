"""Executable contract for the Python deterministic control-loop simulator."""

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]


@spec_claim(SpecClaim(
    "DCLS-S01", "operator_layer", "deterministic simulator boundary", "L2a",
    "The simulator exposes a virtual clock and isolated real-path harness without importing Risk, Execution, Exchange, Live, or secrets.",
    status="implemented",
))
def test_dcls_public_boundary():
    from aletheia.simulation.control_loop import ControlLoopHarness, VirtualClock
    source = (ROOT / "aletheia/simulation/control_loop/runner.py").read_text()
    assert ControlLoopHarness and VirtualClock
    for forbidden in ("aletheia.risk", "aletheia.execution", "aletheia.exchanges", "aletheia.config.secrets"):
        assert forbidden not in source


@spec_claim(SpecClaim(
    "DCLS-S02", "operator_layer", "failure replay evidence", "L2a",
    "Every invariant failure pack contains seed, config, operations, timeline, SQLite, failure, and reproduce artifacts.",
    status="implemented",
))
def test_dcls_failure_artifact_contract():
    from aletheia.simulation.control_loop.artifacts import REQUIRED_ARTIFACTS
    assert {"seed.txt", "initial_config.yaml", "final_config.yaml", "operation_sequence.json", "minimized_sequence.json", "timeline.jsonl", "app.db", "invariant_failure.json", "reproduce.txt"} <= REQUIRED_ARTIFACTS


@spec_claim(SpecClaim(
    "DCLS-S03", "operator_layer", "seed-controlled stateful search", "L2a",
    "DCLS_SEED controls Hypothesis generation; equal seeds replay equal operation sequences and final digests, while different seeds explore different sequences.",
    status="implemented",
))
def test_dcls_seed_is_wired_into_hypothesis_generation():
    source = (ROOT / "tests/simulation/control_loop/test_stateful_control_loop.py").read_text()
    assert "@hypothesis_seed(SEED)" in source
    assert "derandomize=True" not in source
    assert "DCLS_STATEFUL_STEPS={steps}" in source


@spec_claim(SpecClaim(
    "DCLS-B01", "operator_layer", "manual control-loop diagnostic", "L2b",
    "Control-loop simulation remains an explicit local diagnostic and does not consume routine PR or scheduled CI capacity.",
    status="implemented",
))
def test_dcls_is_kept_out_of_routine_ci():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "--ignore=tests/simulation" in ci
    assert "DCLS_MAX_EXAMPLES" not in ci
    assert not (ROOT / ".github/workflows/dcls-nightly.yml").exists()
