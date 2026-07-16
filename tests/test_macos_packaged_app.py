from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "release" / "build_macos_app.py"


def _builder_module():
    spec = importlib.util.spec_from_file_location("build_macos_app", BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_executable(path: Path, body: bytes = b"host") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _payload(root: Path) -> Path:
    root.mkdir(parents=True)
    executable = root / "aletheia-core"
    _write_executable(executable, b"runtime")
    dashboard = root / "aletheia" / "dashboard_static"
    dashboard.mkdir(parents=True)
    (dashboard / "index.html").write_text("<html>Aletheia</html>", encoding="utf-8")
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "packaged-runtime",
                "platform": "darwin-arm64",
                "version": "0.5.0",
                "git_sha": "b" * 40,
                "runtime_entrypoint": "aletheia-core",
                "runtime_data_contract": "per-user-dot-aletheia",
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    return root


def _host(root: Path) -> Path:
    _write_executable(root / "aletheia")
    _write_executable(root / "aletheia-tray")
    return root


def test_macos_app_is_menu_bar_only_and_contains_bootstrap_runtime(tmp_path):
    builder = _builder_module()
    payload = _payload(tmp_path / "payload")
    host = _host(tmp_path / "host")
    public_key = tmp_path / "public.pem"
    public_key.write_text("public", encoding="utf-8")
    feed = tmp_path / "feed.txt"
    feed.write_text("https://updates.example.test/feed.json\n", encoding="utf-8")

    result = builder.build_macos_app(
        payload_dir=payload,
        host_bin_dir=host,
        output_dir=tmp_path / "dist",
        public_key=public_key,
        feed_url_file=feed,
    )

    app = result["app"]
    contents = app / "Contents"
    with (contents / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    assert info["CFBundleExecutable"] == "aletheia-tray"
    assert info["LSUIElement"] is True
    assert info["CFBundlePackageType"] == "APPL"

    for name in ("aletheia", "ato", "aletheia-tray", "aletheia-tray-bin"):
        path = contents / "MacOS" / name
        assert path.is_file()
        assert path.stat().st_mode & stat.S_IXUSR
    launcher = (contents / "MacOS" / "aletheia-tray").read_text(encoding="utf-8")
    assert "__packaged_bootstrap" in launcher
    assert "Application Support/Aletheia" in launcher
    assert "exec \"$CONTENTS/MacOS/aletheia-tray-bin\"" in launcher

    runtime = contents / "Resources" / "runtime" / "aletheia-core"
    assert (runtime / "aletheia-core").is_file()
    assert (runtime / "aletheia" / "dashboard_static" / "index.html").is_file()
    assert (contents / "Resources" / "update-public-key.pem").is_file()
    assert (contents / "Resources" / "update-feed-url.txt").is_file()

    cli_installer = (contents / "Resources" / "install-cli.sh").read_text(encoding="utf-8")
    assert "$HOME/.local/bin" in cli_installer
    assert "uv run" not in cli_installer
    assert "python" not in cli_installer.lower()

    with zipfile.ZipFile(result["archive"]) as archive:
        names = set(archive.namelist())
    assert "Aletheia.app/Contents/Info.plist" in names
    assert "Aletheia.app/Contents/MacOS/aletheia-tray" in names
    assert "Aletheia.app/Contents/MacOS/aletheia-tray-bin" in names
    assert any(name.endswith("/Resources/runtime/aletheia-core/release-manifest.json") for name in names)


def test_macos_app_archive_is_deterministic(tmp_path):
    builder = _builder_module()
    payload = _payload(tmp_path / "payload")
    host = _host(tmp_path / "host")
    first = builder.build_macos_app(
        payload_dir=payload,
        host_bin_dir=host,
        output_dir=tmp_path / "first",
    )
    second = builder.build_macos_app(
        payload_dir=payload,
        host_bin_dir=host,
        output_dir=tmp_path / "second",
    )
    assert first["archive"].read_bytes() == second["archive"].read_bytes()
