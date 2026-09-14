"""Spec: Universe candidate distribution ownership.

Originally (2026-07-03) this locked the legacy refresh path as one behavior:
six-family quota, same-symbol dedupe with `preferred_market_type` winning outright,
regime propagation, and derivatives context.

Contract Delta (2026-09-07, #2945 + candidate_selection_utility_contract.md):
Candidate Selection Utility now owns cross-market selection. Two of the three
original mechanisms are gone, and one is INVERTED:

* the six-family quota allocator and pre-allocation dedupe are retired; the current
  refresh path selects through the Candidate Selection Utility and retains its
  typed physical-instrument population for that authority;
* `preferred_market_type` is now a TIE-BREAK ONLY. Contract §6: "Cross-market
  deduplication uses the same order, with preferred market only resolving equal
  economic/signal rank." The old claim asserted the opposite — that `auto` keeps
  perp *even when spot scores higher*. Satisfying that claim today would require
  breaking the contract, so it is replaced rather than repaired.

Known coverage gap (deliberate, recorded rather than faked): there is no
refresh-level end-to-end assertion under the new path. Reaching a non-empty member
pool now requires a Startup-admissible fixture (eligible actionable instrument,
explicit direction, available capabilities, finite positive
`pessimistic_strength`), which this spec does not build; per-mechanism coverage
lives in `spec_candidate_selection_utility.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


_ROOT = Path(__file__).resolve().parents[2]


@spec_claim(
    SpecClaim(
        "UP-DIST-01",
        "universe_pipeline",
        "§candidate distribution ownership",
        "L2a",
        "Candidate Selection Utility is the sole owner of cross-market selection: "
        "refresh retains every physical instrument instead of pre-allocating a "
        "six-family quota, and `preferred_market_type` resolves only an equal "
        "economic/signal rank — it can never promote a lower-ranked market.",
        status="implemented",
    ),
)
def test_utility_owns_cross_market_selection():
    service = (_ROOT / "core/aletheia/universe/service.py").read_text(encoding="utf-8")

    # The current refresh path has one selection authority and no legacy fallback.
    assert "target_candidates_from_bundle" in service
    assert "candidate_selection_legacy_runtime_removed" in service
    tree = ast.parse(service)
    called = {
        node.func.id if isinstance(node.func, ast.Name) else
        node.func.attr if isinstance(node.func, ast.Attribute) else ""
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "allocate_universe" not in called, (
        "service.py calls the retired family-quota allocator again — a second "
        "selection authority alongside Candidate Selection Utility"
    )


@spec_claim(
    SpecClaim(
        "UP-DIST-02",
        "universe_pipeline",
        "§preferred market is a tie-break only",
        "L2a",
        "Cross-market deduplication orders by economic/signal rank first and consults "
        "`preferred_market_type` only to resolve an exact tie, so a higher-ranked spot "
        "instrument always beats a preferred perp (contract §6).",
        status="implemented",
    ),
)
def test_preferred_market_only_breaks_an_equal_rank():
    source = (
        _ROOT / "core/aletheia/universe/candidate_selection_shadow.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find the cross-market comparison and prove its key order: economic/signal
    # rank must be strictly ahead of the market preference, otherwise the
    # preference could promote a worse instrument.
    ordered_comparisons = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Tuple):
            continue
        left = ast.unparse(node.left)
        if "market_rank" not in left:
            continue
        ordered_comparisons.append(left)

    assert ordered_comparisons, (
        "no cross-market comparison found — if dedup moved, re-derive this claim "
        "from candidate_selection_utility_contract.md §6 instead of deleting it"
    )
    for left in ordered_comparisons:
        rank_at = left.find("decision_rank")
        market_at = left.find("market_rank")
        assert rank_at != -1, (
            f"cross-market key does not order by economic/signal rank at all: {left}"
        )
        assert rank_at < market_at, (
            "preferred market is ordered BEFORE economic/signal rank, so it can "
            f"promote a lower-ranked instrument — contract §6 violation: {left}"
        )
