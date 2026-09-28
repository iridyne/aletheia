from __future__ import annotations

from importlib.metadata import version as installed_version
import json
from pathlib import Path
import tomllib

from aletheia import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_product_version_has_one_source() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "core/aletheia/__init__.py"
    assert installed_version("aletheia-trading-os") == __version__


def test_release_automation_tracks_the_product_without_component_versions() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))

    assert config["release-type"] == "python"
    assert config["include-v-in-tag"] is True
    assert config["include-component-in-tag"] is False
    assert config["bump-minor-pre-major"] is True
    assert config["bump-patch-for-minor-pre-major"] is True
    assert config["draft-pull-request"] is False
    assert config["label"] == "autorelease: pending"
    assert config["packages"]["."]["extra-files"] == [
        {"type": "generic", "path": "core/aletheia/__init__.py"}
    ]
    assert manifest == {".": __version__}

    assert pyproject["tool"]["uv"]["cache-keys"] == [
        {"file": "pyproject.toml"},
        {"file": "core/aletheia/__init__.py"},
    ]

    configured_paths = json.dumps(config)
    assert "frontend/package.json" not in configured_paths
    assert "operator-shell/package.json" not in configured_paths
