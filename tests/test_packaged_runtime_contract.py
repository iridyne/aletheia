from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

from aletheia.packaged_process import translate_frozen_command

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "release" / "build_packaged_runtime.py"


def _builder_module():
    spec = importlib.util.spec_from_file_location("build_packaged_runtime", BUILDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_payload(root: Path) -> Path:
    payload = root / "aletheia-core"
    payload.mkdir(parents=True)
    executable = payload / ("aletheia-core.exe" if os.name == "nt" else "aletheia-core")
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    if os.name != "nt":
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    static = payload / "aletheia" / "dashboard_static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<html>Aletheia</html>", encoding="utf-8")
    (payload / "_internal").mkdir()
    (payload / "_internal" / "runtime.dat").write_bytes(b"runtime")
    return payload


def test_packaged_runtime_manifest_and_archive_are_deterministic(tmp_path):
    builder = _builder_module()
    payload = _fake_payload(tmp_path / "input")
    first = builder.package_existing_payload(
        payload,
        tmp_path / "first",
        git_sha="a" * 40,
        version="0.5.0",
        target=builder.platform_id(),
    )
    first_bytes = first.archive.read_bytes()
    first_manifest = json.loads(first.manifest.read_text(encoding="utf-8"))

    second = builder.package_existing_payload(
        payload,
        tmp_path / "second",
        git_sha="a" * 40,
        version="0.5.0",
        target=builder.platform_id(),
    )

    assert second.archive.read_bytes() == first_bytes
    assert first_manifest["target"] == "packaged-runtime"
    assert first_manifest["runtime_entrypoint"].startswith("aletheia-core")
    assert first_manifest["runtime_data_contract"] == "per-user-dot-aletheia"
    assert {entry["path"] for entry in first_manifest["files"]} >= {
        "aletheia/dashboard_static/index.html",
        "_internal/runtime.dat",
    }

    with tarfile.open(first.archive, "r:gz") as archive:
        names = archive.getnames()
    assert any(name.endswith("/release-manifest.json") for name in names)
    assert not any("/.git/" in name or "/tests/" in name or "/.aletheia/" in name for name in names)


def test_packaged_runtime_preserves_safe_internal_symlinks(tmp_path):
    if os.name == "nt" or not hasattr(os, "symlink"):
        pytest.skip("POSIX symlink contract")
    builder = _builder_module()
    payload = _fake_payload(tmp_path / "input")
    link = payload / "_internal" / "runtime-link.dat"
    try:
        link.symlink_to("runtime.dat")
    except OSError:
        pytest.skip("symlink creation is not permitted")

    result = builder.package_existing_payload(
        payload,
        tmp_path / "output",
        git_sha="c" * 40,
        version="0.5.0",
        target=builder.platform_id(),
    )
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    link_entry = next(item for item in manifest["files"] if item["path"] == "_internal/runtime-link.dat")
    assert link_entry["type"] == "symlink"
    assert link_entry["target"] == "runtime.dat"

    with tarfile.open(result.archive, "r:gz") as archive:
        member = archive.getmember(f"{payload.name}/_internal/runtime-link.dat")
        assert member.issym()
        assert member.linkname == "runtime.dat"


def test_packaged_runtime_rejects_escaping_symlink(tmp_path):
    if os.name == "nt" or not hasattr(os, "symlink"):
        pytest.skip("POSIX symlink contract")
    builder = _builder_module()
    payload = _fake_payload(tmp_path / "input")
    outside = tmp_path / "outside.dat"
    outside.write_bytes(b"outside")
    try:
        (payload / "_internal" / "outside-link.dat").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(RuntimeError, match="outside-link"):
        builder.package_existing_payload(
            payload,
            tmp_path / "output",
            git_sha="d" * 40,
            version="0.5.0",
            target=builder.platform_id(),
        )


def test_packaged_runtime_size_limit_fails_closed(tmp_path):
    builder = _builder_module()
    payload = _fake_payload(tmp_path / "input")
    (payload / "large.bin").write_bytes(b"x" * 1024)
    with pytest.raises(RuntimeError, match="hard limit"):
        builder.package_existing_payload(
            payload,
            tmp_path / "output",
            git_sha="b" * 40,
            version="0.5.0",
            target=builder.platform_id(),
            max_mib=0,
        )


def test_pyinstaller_contract_is_one_folder_and_includes_dashboard():
    source = (ROOT / "packaging" / "pyinstaller" / "aletheia-core.spec").read_text(encoding="utf-8")
    assert "COLLECT(" in source
    assert 'name="aletheia-core"' in source
    assert "dashboard_static" in source
    assert "console=True" in source
    assert "onefile" not in source.lower()


def test_frozen_child_commands_reenter_the_payload_instead_of_treating_it_as_python(tmp_path):
    executable = tmp_path / "aletheia-core"
    executable.write_text("payload", encoding="utf-8")
    runner = tmp_path / "aletheia" / "_gateway_runner.py"

    assert translate_frozen_command(
        [str(executable), str(runner), "--no-browser"], executable=str(executable)
    ) == [str(executable), "__packaged_gateway_runner", "--no-browser"]
    assert translate_frozen_command(
        [
            str(executable),
            "-m",
            "uvicorn",
            "aletheia.server.app:app",
            "--port",
            "8788",
        ],
        executable=str(executable),
    ) == [
        str(executable),
        "__packaged_uvicorn",
        "aletheia.server.app:app",
        "--port",
        "8788",
    ]
    assert translate_frozen_command(
        ["git", "status"], executable=str(executable)
    ) == ["git", "status"]


def test_rust_host_contract_keeps_tray_cli_and_payload_separate():
    cargo = (ROOT / "native" / "aletheia-host" / "Cargo.toml").read_text(encoding="utf-8")
    library = (ROOT / "native" / "aletheia-host" / "src" / "lib.rs").read_text(encoding="utf-8")
    cli = (ROOT / "native" / "aletheia-host" / "src" / "bin" / "aletheia.rs").read_text(encoding="utf-8")
    tray = (ROOT / "native" / "aletheia-host" / "src" / "bin" / "aletheia-tray.rs").read_text(encoding="utf-8")

    assert 'name = "aletheia"' in cargo
    assert 'name = "aletheia-tray"' in cargo
    assert "current.json" in library
    assert "release-manifest.json" in library
    assert "packaged-runtime" in library
    assert "command.exec()" in cli
    assert "__host" in cli
    assert "Open Aletheia" in tray
    assert "http://127.0.0.1:8787" in library


def test_plan_preserves_browser_only_ui_and_plain_cli():
    plan = (ROOT / "docs" / "plans" / "packaged_tray_cli_runtime.md").read_text(encoding="utf-8")
    operations = (ROOT / "docs" / "operations" / "PACKAGED_RUNTIME.md").read_text(encoding="utf-8")

    assert "the only product UI is the dashboard" in plan
    assert "must not require a system Python, `uv`, Node.js" in plan
    assert "aletheia doctor" in plan
    assert "No installed command requires" in plan
    assert "Installed product:" in operations
    installed_section = operations.split("Installed product:", 1)[1].split("The installed launchers", 1)[0]
    assert "aletheia doctor" in installed_section
    assert "ato gateway status" in installed_section
    assert "uv run" not in installed_section
