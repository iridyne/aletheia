from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.async_test_utils import closing_coro_runner


async def _sample() -> str:
    return "unused"


def test_closing_coro_runner_returns_values_and_closes_each_coroutine():
    runner = closing_coro_runner("first", "second")
    first = _sample()
    second = _sample()

    assert runner(first) == "first"
    assert runner(second) == "second"
    assert first.cr_frame is None
    assert second.cr_frame is None


def test_closing_coro_runner_closes_before_raising():
    runner = closing_coro_runner(ConnectionError("exchange unavailable"))
    coro = _sample()

    with pytest.raises(ConnectionError, match="exchange unavailable"):
        runner(coro)

    assert coro.cr_frame is None


def test_closing_coro_runner_rejects_unexpected_extra_call():
    runner = closing_coro_runner()
    coro = _sample()

    with pytest.raises(AssertionError, match="more times than expected"):
        runner(coro)

    assert coro.cr_frame is None


def test_pytest_policy_rejects_an_unawaited_coroutine(tmp_path):
    """The repository warning policy must turn a leaked coroutine into failure."""

    root = Path(__file__).resolve().parents[1]
    probe = tmp_path / "test_unawaited_probe.py"
    probe.write_text(
        """
async def leaked():
    return None


def test_probe():
    leaked()
""".lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(root / "pyproject.toml"),
            str(probe),
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "was never awaited" in combined
