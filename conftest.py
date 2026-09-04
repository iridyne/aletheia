"""Repository-level pytest compatibility fixtures.

Keep this file narrow: fixtures here affect the whole suite. The update
continuity modules below predate the dedicated activation-settlement barrier
and stub the old full-maintenance seam. Route only those modules through their
existing trusted test cut without adding any production compatibility path.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

_UPDATE_CONTINUITY_MODULES = frozenset({
    "test_update_observer_continuity",
    "test_update_observer_continuity_followup",
    "test_update_target_freshness",
    "tests.test_update_observer_continuity",
    "tests.test_update_observer_continuity_followup",
    "tests.test_update_target_freshness",
})


@pytest.fixture(autouse=True)
def _settled_update_activation_boundary(request, monkeypatch):
    """Preserve the established isolated cut for legacy update unit tests."""
    if request.module.__name__ not in _UPDATE_CONTINUITY_MODULES:
        return

    from aletheia.observation_lab import runner

    @contextmanager
    def settled_barrier():
        yield {
            "ok": True,
            "checkpoint": {"current_fingerprint": "trusted"},
        }

    monkeypatch.setattr(
        runner,
        "observation_settlement_barrier",
        settled_barrier,
    )
