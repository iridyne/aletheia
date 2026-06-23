"""Spec layer conftest — auto-inject markers + collection summary."""

import pytest

from tests.specs.spec_meta import SPEC_REGISTRY


def pytest_collection_modifyitems(items):
    """Auto-add L2a/L2b/L2c/spec markers to tests decorated with @spec_claim."""
    for item in items:
        fn = getattr(item, "function", None)
        if fn and hasattr(fn, "_spec_claims"):
            item.add_marker(pytest.mark.spec)
            for claim in fn._spec_claims:
                item.add_marker(getattr(pytest.mark, claim.level))


def pytest_collection_finish(session):
    """Print SDD claim summary after collection."""
    spec_tests = [
        item
        for item in session.items
        if hasattr(getattr(item, "function", None), "_spec_claims")
    ]
    if spec_tests:
        claims_count = sum(
            len(item.function._spec_claims) for item in spec_tests
        )
        session.config.stash.setdefault("sdd_summary", None)
        print(
            f"\n[SDD] {len(spec_tests)} spec tests covering "
            f"{claims_count} claims loaded"
        )
