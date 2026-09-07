"""Executable contract for the Python deterministic control-loop simulator."""

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]


# The local paper fill model and its receipt type. #2945 routed the harness's
# synthetic entry through these so the real broker owns depth walking and fill
# evidence, which the previous `portfolio.paper_account` seam did not produce.
_DCLS_ALLOWED_EXECUTION_MODULES = (
    "aletheia.execution.paper_broker",
    "aletheia.execution.entry_plan_execution",
)


@spec_claim(SpecClaim(
    "DCLS-S01", "operator_layer", "deterministic simulator boundary", "L2a",
    "The simulator's own source names no Risk, Exchange, Live or secrets module, and "
    "reaches Execution only through the local paper fill model.",
    status="implemented",
))
def test_dcls_public_boundary():
    """Direct-import hygiene in runner.py. NOT a reachability guarantee.

    Measured on this tree: the transitive aletheia-import closure of
    `portfolio.paper_account` (the pre-#2945 seam) and of
    `execution.paper_broker` (the current one) are BOTH 359 modules and BOTH
    reach 5 `aletheia.exchanges` modules, `aletheia.config.secrets`, 5
    `aletheia.risk` modules and 13 `aletheia.gateway` modules. The runner's own
    closure is 366 and has always reached the same set.

    So this scan never enforced the contract's intent, and reverting the harness
    to the old seam would buy textual hygiene and nothing else -- the reach is
    byte-identical either way. What it does enforce is worth keeping: the
    simulator's own source must not name Risk, Exchange, Live or secrets, so a
    future author cannot casually add a venue broker, a credential read or a Risk
    evaluation to the loop. `aletheia.execution` is admitted only for the two
    local paper-fill modules named above.

    Determinism and offline-ness are guaranteed by the harness's virtual clock
    and by DCLS-S02..S0n, not by this assertion.
    """
    from aletheia.simulation.control_loop import ControlLoopHarness, VirtualClock

    source = (ROOT / "aletheia/simulation/control_loop/runner.py").read_text()
    assert ControlLoopHarness and VirtualClock

    for forbidden in ("aletheia.risk", "aletheia.exchanges", "aletheia.config.secrets"):
        assert forbidden not in source, (
            f"the deterministic simulator must not name {forbidden} in its own source"
        )

    # AST, not substring matching: `from aletheia.execution import paper_broker`
    # and `from aletheia.execution.entry_plan_execution import X` name different
    # modules and a text split cannot tell them apart.
    import ast

    reached = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aletheia.execution"):
            if node.module == "aletheia.execution":
                reached.update(f"aletheia.execution.{alias.name}" for alias in node.names)
            else:
                reached.add(node.module)
        elif isinstance(node, ast.Import):
            reached.update(
                alias.name for alias in node.names
                if alias.name.startswith("aletheia.execution")
            )
    unexpected = sorted(reached - set(_DCLS_ALLOWED_EXECUTION_MODULES))
    assert unexpected == [], (
        f"simulator reaches Execution beyond the local paper fill model: {unexpected}"
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
