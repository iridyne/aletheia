"""Regression guard for CI worker runtime-state isolation."""

import os
from pathlib import Path

import pytest


def test_xdist_worker_has_private_runtime_dir():
    """Worker-owned scheduler/config state cannot leak across xdist processes."""
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is None:
        pytest.skip("only meaningful when pytest-xdist starts a worker")

    runtime_dir = Path(os.environ["ALETHEIA_DATA_DIR"])
    assert runtime_dir.name == worker_id
    assert runtime_dir.parent.name == "pytest-xdist"
