from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia.config import loader


def _imported_data_dir(env: dict[str, str]) -> Path:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from aletheia.config.loader import DATA_DIR; print(DATA_DIR)",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip()).resolve()


def test_runtime_data_dir_defaults_to_repository_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALETHEIA_DATA_DIR", raising=False)
    assert loader.runtime_data_dir() == (loader._PROJECT_ROOT / "data").resolve()


def test_runtime_data_dir_accepts_absolute_external_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durable = (tmp_path / "shared" / "data").resolve()
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(durable))
    assert loader.runtime_data_dir() == durable


def test_runtime_data_dir_rejects_relative_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALETHEIA_DATA_DIR", "shared/data")
    with pytest.raises(RuntimeError, match="must be an absolute path"):
        loader.runtime_data_dir()


def test_primary_runtime_selects_external_data_dir_on_import(tmp_path: Path) -> None:
    durable = (tmp_path / "shared" / "data").resolve()
    env = os.environ.copy()
    env.pop("ALETHEIA_PREVIEW_MODE", None)
    env.pop("ALETHEIA_PREVIEW_DATA_DIR", None)
    env["ALETHEIA_DATA_DIR"] = str(durable)
    assert _imported_data_dir(env) == durable


def test_preview_mode_keeps_preview_isolation_over_runtime_override(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    preview = (repo_root / "data" / "dev-preview" / "release-test").resolve()
    durable = (tmp_path / "shared" / "data").resolve()
    env = os.environ.copy()
    env["ALETHEIA_PREVIEW_MODE"] = "1"
    env["ALETHEIA_PREVIEW_DATA_DIR"] = str(preview)
    env["ALETHEIA_DATA_DIR"] = str(durable)
    assert _imported_data_dir(env) == preview
