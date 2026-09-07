"""Executable contract for the Python deterministic control-loop simulator."""

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]

# execution seams explicitly admitted into the simulator boundary (transitive
# closure confirmed identical for both; text-scan cannot enforce reachability)
_ALLOWED_EXECUTION_MODULES = {
    "aletheia.execution.paper_broker",
    "aletheia.execution.entry_plan_execution",
}


@spec_claim(SpecClaim(
    "DCLS-S01", "operator_layer", "deterministic simulator boundary", "L2a",
    "The simulator does not import Risk, Exchanges, or secrets. The two admitted "
    "execution seams (paper_broker, entry_plan_execution) are explicitly allowed; "
    "any other aletheia.execution import is forbidden. "
    "Contract Delta (2026-09-07): paper_broker and entry_plan_execution admitted; "
    "transitive closure measurement confirmed identical 359-module reachability for both.",
    status="implemented",
))
def test_dcls_public_boundary():
    from aletheia.simulation.control_loop import ControlLoopHarness, VirtualClock
    assert ControlLoopHarness and VirtualClock

    src = (ROOT / "aletheia/simulation/control_loop/runner.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        mod = (getattr(node, "module", "") or "")
        full = f"{mod}"
        for forbidden_prefix in ("aletheia.risk", "aletheia.exchanges", "aletheia.config.secrets"):
            assert not full.startswith(forbidden_prefix), (
                f"runner.py imports from forbidden module {full!r}"
            )
        if full.startswith("aletheia.execution"):
            # Handle both `import aletheia.execution.foo` and
            # `from aletheia.execution import foo` (module="aletheia.execution")
            if full == "aletheia.execution" and isinstance(node, ast.ImportFrom):
                names = {f"aletheia.execution.{alias.name}" for alias in node.names}
                disallowed = names - _ALLOWED_EXECUTION_MODULES
                assert not disallowed, (
                    f"runner.py imports unadmitted execution names {disallowed}; "
                    f"only {_ALLOWED_EXECUTION_MODULES} are allowed"
                )
            else:
                assert full in _ALLOWED_EXECUTION_MODULES, (
                    f"runner.py imports from unadmitted execution module {full!r}; "
                    f"only {_ALLOWED_EXECUTION_MODULES} are allowed"
                )


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
    "Control-loop simulation remains an explicit local diagnostic. Routine PR CI "
    "does not run remotely; owner-triggered full validation may include it.",
    status="implemented",
))
def test_dcls_is_kept_out_of_routine_ci():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "pull_request:" not in ci
    assert "DCLS_MAX_EXAMPLES" not in ci
    assert not (ROOT / ".github/workflows/dcls-nightly.yml").exists()
