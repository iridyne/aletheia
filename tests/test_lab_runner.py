"""Multi-window Lab runner contract tests (Issue #1072 Cluster 2 / §5).

The runner's job is one bounded, fair, exception-isolated pass over every
registered Observation Window. These tests pin that contract directly on
the primitive, independent of which windows ship by default.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def clean_registry():
    """Isolate each test from the module-level default window registry."""
    from aletheia.observation_lab.runner import (
        _WINDOWS,
        registered_windows,
    )

    snapshot = list(registered_windows())
    _WINDOWS.clear()
    yield
    _WINDOWS.clear()
    _WINDOWS.extend(snapshot)


def test_run_lab_windows_runs_every_registered_window_in_alphabetical_order(clean_registry):
    from aletheia.observation_lab.runner import (
        LabWindow,
        register_window,
        run_lab_windows,
    )

    call_order: list[str] = []

    def _make(name: str):
        def _fn():
            call_order.append(name)
            return {"status": "ok", "name": name}
        return _fn

    # Register out of order — the runner must still iterate alphabetically.
    register_window(LabWindow(name="charlie", kind="test", fn=_make("charlie")))
    register_window(LabWindow(name="alpha", kind="test", fn=_make("alpha")))
    register_window(LabWindow(name="bravo", kind="test", fn=_make("bravo")))

    summary = run_lab_windows()

    assert call_order == ["alpha", "bravo", "charlie"]
    assert summary["windows_run"] == 3
    assert summary["windows_errored"] == 0
    assert set(summary["results"]) == {"alpha", "bravo", "charlie"}


def test_run_lab_windows_isolates_per_window_exceptions(clean_registry):
    from aletheia.observation_lab.runner import (
        LabWindow,
        register_window,
        run_lab_windows,
    )

    good_calls = 0

    def _bad():
        raise RuntimeError("simulated crash")

    def _good():
        nonlocal good_calls
        good_calls += 1
        return {"status": "ok"}

    register_window(LabWindow(name="bad", kind="test", fn=_bad))
    register_window(LabWindow(name="good", kind="test", fn=_good))

    summary = run_lab_windows()

    # The crashing window is isolated to its own result — the good window
    # still ran and produced output. This is the load-bearing invariant per
    # new #1072 §"Failure isolation".
    assert good_calls == 1
    assert summary["windows_run"] == 2
    assert summary["windows_errored"] == 1
    assert summary["results"]["bad"]["status"] == "error"
    assert "simulated crash" in summary["results"]["bad"]["error"]
    assert summary["results"]["good"] == {"status": "ok"}


def test_run_lab_windows_bounds_pass_to_max_windows(clean_registry):
    from aletheia.observation_lab.runner import (
        LabWindow,
        register_window,
        run_lab_windows,
    )

    call_order: list[str] = []

    def _make(name: str):
        def _fn():
            call_order.append(name)
            return {"status": "ok"}
        return _fn

    for name in ("delta", "alpha", "charlie", "bravo"):
        register_window(LabWindow(name=name, kind="test", fn=_make(name)))

    summary = run_lab_windows(max_windows=2)

    # Deterministic fair ordering + bound = alphabetically first two windows.
    assert call_order == ["alpha", "bravo"]
    assert summary["windows_run"] == 2
    assert "charlie" not in summary["results"]
    assert "delta" not in summary["results"]


def test_register_window_rejects_duplicate_names(clean_registry):
    from aletheia.observation_lab.runner import (
        LabWindow,
        register_window,
    )

    register_window(LabWindow(name="dup", kind="test", fn=lambda: {}))
    with pytest.raises(ValueError, match="already registered"):
        register_window(LabWindow(name="dup", kind="test", fn=lambda: {}))


def test_default_registry_contains_two_shipped_windows():
    """The module-level defaults must land the two Cluster 2 windows."""
    from aletheia.observation_lab.runner import registered_windows

    names = {w.name for w in registered_windows()}
    assert "filter_shadow_evaluate" in names
    assert "ufc_shadow_equivalence" in names


def test_run_lab_windows_normalises_non_dict_return(clean_registry):
    from aletheia.observation_lab.runner import (
        LabWindow,
        register_window,
        run_lab_windows,
    )

    register_window(LabWindow(name="scalar", kind="test", fn=lambda: 7))

    summary = run_lab_windows()

    assert summary["results"]["scalar"] == {"status": "ok", "value": 7}
