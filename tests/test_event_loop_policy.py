"""Behavioural tests for the Windows SelectorEventLoop policy fix (§17/§23/§24).

These run real subprocesses that import each process entry-point module and
assert the actual installed event-loop policy — no mocks. This verifies the fix
mechanism itself: that importing an entry point flips the process onto
SelectorEventLoop before any loop is created.

Why subprocesses: the policy is process-global and set at import time. The
pytest process already imported these modules (and already has its own policy),
so the only honest way to observe "what an import does to a fresh process" is to
spawn one.
"""

import subprocess
import sys

import pytest

# Each entry point must install the selector policy at import time.
_ENTRY_POINTS = [
    "aletheia.server.app",
    "aletheia.gateway.frontdoor",
    "aletheia.main",
]


@pytest.mark.skipif(sys.platform != "win32", reason="policy only changes on Windows")
@pytest.mark.parametrize("module", _ENTRY_POINTS)
def test_entry_point_import_installs_selector_policy(module: str) -> None:
    """Importing a process entry point must leave the process on SelectorEventLoop."""
    code = (
        f"import {module}; "
        "import asyncio; "
        "p = asyncio.get_event_loop_policy(); "
        "assert isinstance(p, asyncio.WindowsSelectorEventLoopPolicy), type(p).__name__; "
        # And a freshly created loop is genuinely a selector loop, not proactor.
        "loop = asyncio.new_event_loop(); "
        "name = type(loop).__name__; "
        "loop.close(); "
        "assert 'Selector' in name and 'Proactor' not in name, name; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{module} import did not install selector policy:\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "OK" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="policy only changes on Windows")
def test_apply_is_idempotent_and_effective() -> None:
    """apply_windows_selector_policy flips Proactor→Selector and is idempotent."""
    from aletheia.async_utils import apply_windows_selector_policy

    import asyncio

    original = asyncio.get_event_loop_policy()
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        apply_windows_selector_policy()
        assert isinstance(
            asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy
        )
        # Second call is a no-op, policy stays selector.
        apply_windows_selector_policy()
        assert isinstance(
            asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy
        )
    finally:
        asyncio.set_event_loop_policy(original)


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows no-op path")
def test_apply_is_noop_off_windows() -> None:
    """On non-Windows the helper must not touch the policy at all."""
    from aletheia.async_utils import apply_windows_selector_policy

    import asyncio

    before = asyncio.get_event_loop_policy()
    apply_windows_selector_policy()
    assert asyncio.get_event_loop_policy() is before


def test_uvicorn_loop_none_defers_to_process_policy() -> None:
    """`--loop none` must make uvicorn return loop_factory=None.

    This is the contract the §23/§24 fix relies on: with loop_factory=None,
    uvicorn falls back to the standard asyncio.run() path, which honours the
    process WindowsSelectorEventLoopPolicy instead of uvicorn's own hardcoded
    ProactorEventLoop factory on win32. Regression guard against a uvicorn
    upgrade dropping the "none" key (which would silently re-break §23).
    """
    uvicorn_config = pytest.importorskip("uvicorn.config")
    cfg = uvicorn_config.Config("asyncio:SelectorEventLoop", loop="none")
    assert cfg.get_loop_factory() is None
