"""Spec conformance matrix — claim-status report and structural validation.

Run with: uv run pytest tests/test_spec_conformance.py -v -s
Prints the full conformance matrix showing claim_id -> test -> status.

This is not a product-support coverage report. Product capability status lives
in docs/product/CAPABILITY_GROUND_TRUTH.md.
"""

import importlib
import pkgutil

import pytest

import tests.specs as specs_package
from tests.specs.spec_meta import SPEC_REGISTRY


def _discover_spec_module_names() -> tuple[str, ...]:
    """Return every executable spec module in the package.

    Discovery, rather than a hand-maintained import list, keeps the conformance
    matrix aligned with pytest's spec_*.py collection rule.
    """
    return tuple(sorted(
        module.name
        for module in pkgutil.iter_modules(specs_package.__path__)
        if module.name.startswith("spec_") and module.name != "spec_meta"
    ))


DISCOVERED_SPEC_MODULE_NAMES = _discover_spec_module_names()
IMPORTED_SPEC_MODULES = {
    name: importlib.import_module(f"tests.specs.{name}")
    for name in DISCOVERED_SPEC_MODULE_NAMES
}

REQUIRED_CONTRACTS = [
    "risk_engine_contract",
    "execution_contract",
    "live_unlock_contract",
    "data_model",
    "agent_contracts",
    "api_reference",
    "data_layer_contract",
    "operator_layer",
    "audit_chain_contract",
    "decision_lifecycle_contract",
    "learning_contract",
    "learning_evolution",
    "learning_observability",
    "llm_inference_harness_contract",
    "trading_mandate_contract",
    # subsystem observer claims register under "agent_contracts" by design —
    # equity_observer / prediction_observer are agents, not a separate contract.
]


class TestConformanceStructure:
    """Validate the spec registry itself is well-formed."""

    def test_every_discovered_spec_module_registers_claims(self):
        registered_modules = {
            str(getattr(test_fn, "__module__", "")).rsplit(".", 1)[-1]
            for entries in SPEC_REGISTRY.values()
            for _, test_fn in entries
        }
        missing = sorted(
            set(DISCOVERED_SPEC_MODULE_NAMES) - registered_modules
        )
        assert not missing, (
            "CONFORMANCE GAP: discovered spec module(s) registered no claims: "
            + ", ".join(missing)
        )

    def test_every_required_contract_has_claims(self):
        for contract in REQUIRED_CONTRACTS:
            claims = SPEC_REGISTRY.get(contract, [])
            assert claims, f"CONFORMANCE GAP: no claims for '{contract}'"

    def test_every_claim_has_callable_test(self):
        for contract, entries in SPEC_REGISTRY.items():
            for claim, test_fn in entries:
                assert callable(test_fn), (
                    f"ORPHAN: {claim.claim_id} in {contract} has no callable test"
                )

    def test_claim_ids_unique_within_contract(self):
        for contract, entries in SPEC_REGISTRY.items():
            ids = [c.claim_id for c, _ in entries]
            seen = set()
            for cid in ids:
                assert cid not in seen, (
                    f"DUPLICATE claim_id '{cid}' in {contract}"
                )
                seen.add(cid)

    def test_all_levels_represented(self):
        """Each required contract should have at least one L2a claim."""
        for contract in REQUIRED_CONTRACTS:
            entries = SPEC_REGISTRY.get(contract, [])
            levels = {c.level for c, _ in entries}
            assert "L2a" in levels, (
                f"{contract} has no L2a (structural) claims"
            )

    def test_non_conforming_claim_budget(self):
        """#75 fix: `test_print_conformance_matrix` is informational-only
        and passes green even when the matrix contains missing/partial/
        superseded claims. An owner reading "green tests" then believes the
        contracts are all wired up. Anchor the current baseline as a budget
        — new missing/partial claims can only be added by consciously
        updating this test's expectation, matching what happened when
        contracts were paused or new claims added without wiring.

        If this test fails, either:
          - restore the claim and set its compatibility status to
            'implemented' (meaning locally conforming), OR
          - if the gap is intentional, update the counts below and note why.
        """
        counts: dict[str, int] = {}
        for entries in SPEC_REGISTRY.values():
            for claim, _ in entries:
                counts[claim.status] = counts.get(claim.status, 0) + 1

        # Baseline anchored 2026-07-08 from the review's own count:
        #   missing=5, partial=1, superseded=1.
        # Bumping any of these upward is a signal that a new gap opened;
        # bumping downward means a fix landed and someone should ratchet
        # this budget down accordingly.
        # TASK-2026-032 intentionally added twelve OI-* RED tripwires for the
        # reconciled-gone remediation.  They are split into later implementation
        # tasks; TASK-2026-033 makes OI-S01 GREEN by adding the persistence layer.
        # Strict xfail keeps the remaining eleven honest without representing them
        # as implemented.
        # TASK-2026-034 makes OI-S02 GREEN with the dependency-neutral central
        # classifier/read model, durable incident/Learning refreeze, and disappearance
        # confirmation, broker-neutral verified exit, and verified/admin accounting
        # split, behavior consumers, and authoritative Risk loss. Three OI
        # reporting/history claims remain missing.
        # 2026-07-24: the first bounded Learning Contribution Observability slice
        # implemented the first six claims. The 2026-07-31 reconciliation also
        # recognizes the shipped GET-only summary and the partial frontend
        # verdict/query-state proofs. Three LCO target claims remain missing;
        # two frontend claims are partial until the complete workbench exists.
        # #488 concerns the separate legacy master Learning toggle; the
        # trial-admission lifecycle claim remains implemented.
        # PF-03/#419 adds one explicit RED tripwire for the absent
        # cash-flow-adjusted performance consumer. The current UI is honest
        # about that absence; the future analytics join remains intentionally
        # visible instead of hiding behind capital-event persistence.
        # EX-04/#418 adds one RED tripwire for the missing join between the
        # experimental Live authority state and new-Proposal mode selection.
        # It keeps the gap visible without enabling autonomous live trading.
        # LE-C07 now has an explicit RED tripwire for the source-outcome to
        # learned-treatment producer link that precedes the proven Phase 4 E2E.
        MAX_MISSING = 12
        # #153: 5 self-heal specs moved to partial (OL-C08, OL-S13, SH-S01, SH-C02, SH-B03).
        # The Learning MVP reconciliation scopes conformance to currently
        # measurement-supported paths without treating adjacent/source-linked
        # tests as direct executable proof. LCO-C05 also remains partial until
        # the executable claim runs its existing component-level behavior proof.
        MAX_PARTIAL = 15
        MAX_SUPERSEDED = 1

        missing = counts.get("missing", 0)
        partial = counts.get("partial", 0)
        superseded = counts.get("superseded", 0)

        assert missing <= MAX_MISSING, (
            f"CONFORMANCE REGRESSION: {missing} missing claims exceed the "
            f"documented budget of {MAX_MISSING}. New missing claim(s) landed — "
            "either implement them or consciously bump the budget with a reason."
        )
        assert partial <= MAX_PARTIAL, (
            f"CONFORMANCE REGRESSION: {partial} partial claims exceed the "
            f"documented budget of {MAX_PARTIAL}."
        )
        assert superseded <= MAX_SUPERSEDED, (
            f"CONFORMANCE REGRESSION: {superseded} superseded claims exceed the "
            f"documented budget of {MAX_SUPERSEDED}."
        )


def test_print_conformance_matrix(capsys):
    """Print the full matrix (always passes — informational output)."""
    lines = ["\n{'='*60}", "  SPEC CONFORMANCE MATRIX", f"{'='*60}\n"]
    total = conforming = 0

    for contract in sorted(SPEC_REGISTRY.keys()):
        entries = SPEC_REGISTRY[contract]
        lines.append(f"\n  {contract} ({len(entries)} claims)")
        for claim, test_fn in sorted(entries, key=lambda x: x[0].claim_id):
            total += 1
            icon = {
                "implemented": "+",
                "partial": "~",
                "missing": "-",
                # #74 fix: "superseded" claims (older contract clauses replaced
                # by a newer one) get their own icon so the matrix doesn't render
                # them as "?" (unknown). Not a failure — a legitimate lifecycle
                # state next to implemented/partial/missing.
                "superseded": "s",
            }.get(claim.status, "?")
            if claim.status == "implemented":
                conforming += 1
            invariant_short = claim.invariant[:55]
            lines.append(
                f"    [{icon}] {claim.level} {claim.claim_id}: "
                f"{invariant_short}..."
                f"\n        -> {test_fn.__name__}"
            )

    conformance_ratio = (conforming / total * 100) if total else 0
    lines.append(f"\n  Total: {total} | Conforming: {conforming} | "
                 f"Claim conformance ratio: {conformance_ratio:.0f}%")
    lines.append(f"{'='*60}\n")

    with capsys.disabled():
        print("\n".join(lines))
