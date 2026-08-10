from __future__ import annotations

from importlib.metadata import version as installed_version
import json
from pathlib import Path
import runpy
import tomllib

from aletheia import __version__
from scripts.release.build_bundle import _project_metadata


ROOT = Path(__file__).resolve().parents[1]


def test_product_version_has_one_source_and_all_build_readers_agree() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packaged_runtime = runpy.run_path(str(ROOT / "scripts/release/build_packaged_runtime.py"))

    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "aletheia/__init__.py"
    assert installed_version("aletheia-trading-os") == __version__
    assert _project_metadata(ROOT)[0] == __version__
    assert packaged_runtime["_version"]() == __version__


def test_release_automation_tracks_the_product_without_component_versions() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))

    assert config["release-type"] == "python"
    assert config["include-v-in-tag"] is True
    assert config["bump-minor-pre-major"] is True
    assert config["draft-pull-request"] is False
    assert set(config["label"].split(",")) == {
        "autorelease: pending",
        "do-not-auto-merge",
    }
    assert config["packages"]["."]["extra-files"] == [
        {"type": "generic", "path": "aletheia/__init__.py"}
    ]
    assert manifest == {".": __version__}

    configured_paths = json.dumps(config)
    assert "frontend/package.json" not in configured_paths
    assert "operator-shell/package.json" not in configured_paths
    assert "native/aletheia-host/Cargo.toml" not in configured_paths
