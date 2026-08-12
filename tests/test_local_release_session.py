from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from urllib.request import urlopen

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aletheia.update import packaged

ROOT = Path(__file__).resolve().parents[1]
SESSION_PATH = ROOT / "scripts" / "release" / "local_release_session.py"
BUILDER_PATH = ROOT / "scripts" / "release" / "build_packaged_runtime.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _session_module():
    return _load("test_local_release_session_module", SESSION_PATH)


def _builder_module():
    return _load("test_local_release_session_builder", BUILDER_PATH)


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
    return payload


def _private_key(path: Path) -> tuple[Path, bytes]:
    key = Ed25519PrivateKey.generate()
    private_path = path / "private.pem"
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return private_path, public


def _archive(tmp_path: Path):
    builder = _builder_module()
    return builder.package_existing_payload(
        _fake_payload(tmp_path / "payload"),
        tmp_path / "runtime",
        git_sha="a" * 40,
        version="0.5.0",
        target=packaged.platform_id(),
    ).archive


def test_local_site_is_signed_and_served_only_for_the_session(tmp_path):
    session = _session_module()
    archive = _archive(tmp_path)
    private_key, expected_public = _private_key(tmp_path)
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    site_dir = session_dir / "site"
    site_dir.mkdir()

    server = session.LocalFeedServer(site_dir)
    with server:
        artifacts = session.materialize_local_site(
            session_dir=session_dir,
            archive=archive,
            private_key=private_key,
            origin=server.origin,
            release_id="0.5.0+local-test",
        )
        assert server.running is True
        with urlopen(artifacts.feed_url, timeout=3) as response:  # noqa: S310 - localhost test server
            envelope = json.loads(response.read())
        with urlopen(f"{server.origin}/{archive.name}", timeout=3) as response:  # noqa: S310
            assert response.read() == archive.read_bytes()

    assert server.running is False
    assert artifacts.public_key.read_bytes() == expected_public
    payload = envelope["signed_payload"].encode("utf-8")
    signature = base64.b64decode(envelope["signature"])
    public = serialization.load_pem_public_key(expected_public)
    public.verify(signature, payload)
    assert json.loads(envelope["signed_payload"])["artifacts"][packaged.platform_id()]["url"] == (
        f"{server.origin}/{archive.name}"
    )


def test_local_session_refuses_a_signer_not_trusted_by_installed_app(tmp_path):
    session = _session_module()
    public = tmp_path / "local-public.pem"
    public.write_bytes(b"local")
    host = tmp_path / "host"
    host.mkdir()
    (host / "update-public-key.pem").write_bytes(b"different")

    with pytest.raises(session.LocalReleaseSessionError, match="does not match"):
        session.assert_installed_trust_root(public, installed_host_root=host)


def test_local_session_uses_temporary_feed_for_check_update_and_terminal_status(monkeypatch, tmp_path):
    session = _session_module()
    launcher = tmp_path / "aletheia"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    calls: list[tuple[list[str], dict[str, str]]] = []
    responses = iter(["available", json.dumps({"status": "success", "phase": "committed"})])

    def fake_cli(command, *, env):
        calls.append((command, env))
        return next(responses)

    trigger_calls: list[dict] = []

    def fake_trigger(lnch, *, env):
        trigger_calls.append({"launcher": lnch, "env": env})

    monkeypatch.setattr(session, "_cli_output", fake_cli)
    monkeypatch.setattr(session, "_cli_trigger_update", fake_trigger)

    state = session.apply_via_installed_cli(
        cli=launcher,
        feed_url="http://127.0.0.1:9911/update-feed.json",
        timeout_sec=3,
    )

    assert state == {"status": "success", "phase": "committed"}
    # _cli_output handles check and status polling; _cli_trigger_update handles apply.
    assert [call[0][1:] for call in calls] == [
        ["update", "check"],
        ["update", "status", "--json"],
    ]
    assert len(trigger_calls) == 1
    assert trigger_calls[0]["env"]["ALETHEIA_UPDATE_FEED_URL"].endswith("update-feed.json")


def test_local_session_version_boundary_falls_back_to_apply_on_old_cli(monkeypatch, tmp_path):
    """First upgrade from a pre-#909 installed runtime: bare 'update' is rejected
    with the old hint; the driver must fall back to 'update apply --yes'.
    """
    session = _session_module()
    launcher = tmp_path / "aletheia"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)

    apply_calls: list[list[str]] = []

    def fake_subprocess_run(command, **_kwargs):
        # Old packaged CLI rejects bare `update` with the legacy hint.
        if command[-1] == "update":
            return subprocess.CompletedProcess(
                command, returncode=1,
                stdout="",
                stderr="Direct Git HEAD updates are unavailable in packaged mode. "
                       "Run `aletheia update apply` for the signed-release workflow.\n",
            )
        pytest.fail(f"unexpected subprocess call in version_boundary test: {command}")

    def fake_cli_output(command, *, env):
        apply_calls.append(list(command))
        return "staged"

    monkeypatch.setattr(session.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(session, "_cli_output", fake_cli_output)

    session._cli_trigger_update(launcher, env={})  # noqa: SLF001 - testing internal boundary

    assert apply_calls == [[str(launcher), "update", "apply", "--yes"]]


def test_local_session_version_boundary_detects_old_typer_help(monkeypatch, tmp_path):
    session = _session_module()
    launcher = tmp_path / "aletheia"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    apply_calls: list[list[str]] = []

    monkeypatch.setattr(
        session.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            returncode=2,
            stdout="Commands:\n  apply  Apply a signed update\n  status  Show status\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        session,
        "_cli_output",
        lambda command, *, env: apply_calls.append(list(command)) or "staged",
    )

    session._cli_trigger_update(launcher, env={})

    assert apply_calls == [[str(launcher), "update", "apply", "--yes"]]


def test_local_release_id_rejects_path_escape(tmp_path):
    session = _session_module()
    archive = _archive(tmp_path)

    with pytest.raises(session.LocalReleaseSessionError, match="path-safe"):
        session._local_release_id(archive, "../outside")  # noqa: SLF001 - focused contract


def test_local_session_rejects_a_relative_installed_cli_path():
    session = _session_module()

    with pytest.raises(session.LocalReleaseSessionError, match="must be absolute"):
        session.apply_via_installed_cli(
            cli=Path("aletheia"),
            feed_url="http://127.0.0.1:9911/update-feed.json",
            timeout_sec=3,
        )


def test_local_session_treats_an_equivalent_payload_as_a_successful_noop(monkeypatch, tmp_path):
    session = _session_module()
    launcher = tmp_path / "aletheia"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    responses = iter(["already current", json.dumps({"status": "up_to_date"})])
    monkeypatch.setattr(session, "_cli_output", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(session, "_cli_trigger_update", lambda *_args, **_kwargs: None)

    state = session.apply_via_installed_cli(
        cli=launcher,
        feed_url="http://127.0.0.1:9911/update-feed.json",
        timeout_sec=3,
    )

    assert state["status"] == "up_to_date"


def test_local_session_refuses_to_publish_a_dirty_worktree(monkeypatch):
    session = _session_module()
    monkeypatch.setattr(
        session.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"], returncode=0, stdout=" M aletheia/foo.py\n"
        ),
    )

    with pytest.raises(session.LocalReleaseSessionError, match="clean worktree"):
        session._require_clean_worktree()
