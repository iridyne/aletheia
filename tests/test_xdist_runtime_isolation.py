"""Regression guard for CI worker runtime-state isolation."""

import os
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import pytest


def test_xdist_worker_has_private_runtime_dir():
    """Worker-owned scheduler/config state cannot leak across xdist processes."""
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is None:
        pytest.skip("only meaningful when pytest-xdist starts a worker")

    runtime_dir = Path(os.environ["ALETHEIA_DATA_DIR"])
    assert runtime_dir.name == worker_id
    assert runtime_dir.parent.name == "pytest-xdist"


def test_xdist_worker_updates_loaded_config_paths(monkeypatch, tmp_path):
    """A preloaded config loader must follow the worker-specific data root."""
    import aletheia.config.loader as loader

    configure_xdist_runtime_dir = run_path(
        str(Path(__file__).with_name("conftest.py"))
    )["_configure_xdist_runtime_dir"]

    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path / "stale")
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "stale" / "config.yaml")
    monkeypatch.setattr(loader, "_dirs_ensured_for", tmp_path / "stale")
    monkeypatch.setattr(loader, "_config_cache", None)

    configure_xdist_runtime_dir(
        SimpleNamespace(workerinput={"workerid": "gw3"})
    )

    expected = tmp_path / "pytest-xdist" / "gw3"
    assert Path(os.environ["ALETHEIA_DATA_DIR"]) == expected
    assert loader.DATA_DIR == expected
    assert loader.CONFIG_PATH == expected / "config.yaml"
