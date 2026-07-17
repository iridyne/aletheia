from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import threading
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aletheia.update import applier, packaged, packaged_commands

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _builder():
    return _load_script(
        "test_build_packaged_runtime",
        ROOT / "scripts" / "release" / "build_packaged_runtime.py",
    )


def _signer():
    return _load_script(
        "test_sign_update_feed",
        ROOT / "scripts" / "release" / "sign_update_feed.py",
    )


def _key_pair(root: Path) -> tuple[Path, Path]:
    private = Ed25519PrivateKey.generate()
    private_path = root / "private.pem"
    public_path = root / "public.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


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


def _archive_and_feed(tmp_path: Path, *, release_id: str = "0.5.1+abc123"):
    builder = _builder()
    signer = _signer()
    payload = _fake_payload(tmp_path / "payload")
    result = builder.package_existing_payload(
        payload,
        tmp_path / "dist",
        git_sha="abc123" + "0" * 34,
        version="0.5.1",
        target=packaged.platform_id(),
    )
    private_path, public_path = _key_pair(tmp_path)
    envelope = signer.signed_feed(
        archive=result.archive,
        artifact_url=result.archive.as_uri(),
        private_key_path=private_path,
        release_id=release_id,
        published_at="2026-07-16T00:00:00Z",
    )
    feed_path = tmp_path / "feed.json"
    feed_path.write_text(json.dumps(envelope), encoding="utf-8")
    return result, feed_path, public_path


@contextmanager
def _http_origin(root: Path):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: QuietHandler(*args, directory=str(root), **kwargs),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _configure_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, feed: Path, public_key: Path):
    host = tmp_path / "host"
    data = tmp_path / "data"
    host.mkdir()
    data.mkdir()
    (host / "current.json").write_text(
        json.dumps({"schema_version": 1, "current": "0.5.0+old", "previous": None}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALETHEIA_PACKAGED", "1")
    monkeypatch.setenv("ALETHEIA_HOST_ROOT", str(host))
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(data))
    monkeypatch.setenv("ALETHEIA_UPDATE_FEED_URL", feed.as_uri())
    monkeypatch.setenv("ALETHEIA_UPDATE_PUBLIC_KEY_PATH", str(public_key))
    return host, data


def test_signed_feed_accepts_exact_payload_and_rejects_tampering(monkeypatch, tmp_path):
    _, feed, public_key = _archive_and_feed(tmp_path)
    _configure_host(monkeypatch, tmp_path, feed, public_key)

    result = packaged_commands.check_for_update()
    assert result["ok"] is True
    assert result["has_update"] is True
    assert result["available_release"] == "0.5.1+abc123"

    envelope = json.loads(feed.read_text(encoding="utf-8"))
    envelope["signed_payload"] = envelope["signed_payload"].replace(
        "0.5.1+abc123", "0.5.1+tampered"
    )
    feed.write_text(json.dumps(envelope), encoding="utf-8")
    tampered = packaged_commands.check_for_update()
    assert tampered["ok"] is False
    assert tampered["reason"] == "update_feed_signature_invalid"


def test_signer_exports_the_public_key_matching_the_feed(tmp_path):
    builder = _builder()
    signer = _signer()
    payload = _fake_payload(tmp_path / "payload")
    archive = builder.package_existing_payload(
        payload,
        tmp_path / "dist",
        git_sha="abc123" + "0" * 34,
        version="0.5.1",
        target=packaged.platform_id(),
    ).archive
    private_path, expected_public = _key_pair(tmp_path)
    exported_public = tmp_path / "published-public.pem"

    envelope = signer.signed_feed(
        archive=archive,
        artifact_url="https://updates.example.test/runtime.tar.gz",
        private_key_path=private_path,
        public_key_output=exported_public,
        published_at="2026-07-16T00:00:00Z",
    )

    assert exported_public.read_bytes() == expected_public.read_bytes()
    signed_payload = envelope["signed_payload"].encode("utf-8")
    signature = base64.b64decode(envelope["signature"])
    public_key = serialization.load_pem_public_key(exported_public.read_bytes())
    public_key.verify(signature, signed_payload)


def test_http_feed_downloads_and_installs_verified_runtime(monkeypatch, tmp_path):
    builder = _builder()
    signer = _signer()
    payload = _fake_payload(tmp_path / "payload")
    result = builder.package_existing_payload(
        payload,
        tmp_path / "site",
        git_sha="abc123" + "0" * 34,
        version="0.5.1",
        target=packaged.platform_id(),
    )
    private_path, public_path = _key_pair(tmp_path)

    with _http_origin(tmp_path / "site") as origin:
        envelope = signer.signed_feed(
            archive=result.archive,
            artifact_url=f"{origin}/{result.archive.name}",
            private_key_path=private_path,
            release_id="0.5.1+httpfeed",
            published_at="2026-07-16T00:00:00Z",
        )
        feed = tmp_path / "site" / "update-feed.json"
        feed.write_text(json.dumps(envelope), encoding="utf-8")
        host, _ = _configure_host(monkeypatch, tmp_path, feed, public_path)
        monkeypatch.setenv("ALETHEIA_UPDATE_FEED_URL", f"{origin}/update-feed.json")

        backup = tmp_path / "pre-update.tar.gz"
        backup.write_bytes(b"verified-backup")

        class FakeBackupService:
            def __init__(self, *_args, **_kwargs):
                pass

            def create_backup(self, *, label: str):
                assert label.startswith("pre_update_")
                return backup

        monkeypatch.setattr(packaged, "BackupService", FakeBackupService)
        monkeypatch.setattr(
            packaged,
            "_preflight_candidate",
            lambda candidate_root, **_kwargs: candidate_root,
        )

        staged = packaged_commands.stage_update_blocking()

    assert staged["ok"] is True
    assert staged["state"]["phase"] == "ready_to_switch"
    installed = host / "releases" / "0.5.1+httpfeed"
    assert (installed / "release-manifest.json").is_file()
    request = json.loads((host / "update-request.json").read_text(encoding="utf-8"))
    assert request["candidate_release"] == "0.5.1+httpfeed"


def test_safe_facade_rejects_validly_signed_path_like_release_id(monkeypatch, tmp_path):
    builder = _builder()
    payload = _fake_payload(tmp_path / "payload")
    artifact = builder.package_existing_payload(
        payload,
        tmp_path / "dist",
        git_sha="abc123" + "0" * 34,
        version="0.5.1",
        target=packaged.platform_id(),
    ).archive
    private = Ed25519PrivateKey.generate()
    public_key = tmp_path / "public.pem"
    public_key.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    malicious_payload = json.dumps(
        {
            "schema_version": 1,
            "release_id": "../outside",
            "version": "0.5.1",
            "git_sha": "abc123" + "0" * 34,
            "published_at": "2026-07-16T00:00:00Z",
            "artifacts": {
                packaged.platform_id(): {
                    "url": artifact.as_uri(),
                    "sha256": digest,
                    "size_bytes": artifact.stat().st_size,
                    "archive_format": "tar.gz",
                }
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    feed = tmp_path / "feed.json"
    feed.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "signed_payload": malicious_payload,
                "signature": base64.b64encode(
                    private.sign(malicious_payload.encode("utf-8"))
                ).decode("ascii"),
            }
        ),
        encoding="utf-8",
    )
    host, _ = _configure_host(monkeypatch, tmp_path, feed, public_key)

    result = packaged_commands.stage_update_blocking()
    assert result["state"]["status"] == "failed"
    assert "update_release_id_invalid" in result["state"]["error"]
    assert not (tmp_path / "outside").exists()
    assert not (host / "update-request.json").exists()


def test_archive_extraction_rejects_traversal_and_links(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        traversal = tarfile.TarInfo("../outside")
        traversal.size = 1
        import io

        bundle.addfile(traversal, io.BytesIO(b"x"))
    with pytest.raises(packaged.PackagedUpdateError) as exc_info:
        packaged._extract_archive(archive, tmp_path / "stage")
    assert exc_info.value.reason == "update_archive_path_unsafe"
    assert not (tmp_path / "outside").exists()

    link_archive = tmp_path / "link.tar.gz"
    with tarfile.open(link_archive, "w:gz") as bundle:
        link = tarfile.TarInfo("runtime/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp/escape"
        bundle.addfile(link)
    with pytest.raises(packaged.PackagedUpdateError) as exc_info:
        packaged._extract_archive(link_archive, tmp_path / "link-stage")
    assert exc_info.value.reason == "update_symlink_target_unsafe"


def test_stage_writes_switch_request_only_after_backup_and_preflight(monkeypatch, tmp_path):
    result, feed, public_key = _archive_and_feed(tmp_path)
    host, _ = _configure_host(monkeypatch, tmp_path, feed, public_key)
    backup = tmp_path / "pre-update.tar.gz"
    backup.write_bytes(b"verified-backup")
    preflight = tmp_path / "preflight-data"

    class FakeBackupService:
        def __init__(self, *_args, **_kwargs):
            pass

        def create_backup(self, *, label: str):
            assert label.startswith("pre_update_")
            return backup

    def successful_preflight(candidate_root: Path, **kwargs):
        assert candidate_root.name == "0.5.1+abc123"
        assert kwargs["backup_path"] == backup
        preflight.mkdir()
        return preflight

    monkeypatch.setattr(packaged, "BackupService", FakeBackupService)
    monkeypatch.setattr(packaged, "_preflight_candidate", successful_preflight)

    staged = packaged_commands.stage_update_blocking()
    assert staged["ok"] is True
    assert staged["state"]["phase"] == "ready_to_switch"
    request = json.loads((host / "update-request.json").read_text(encoding="utf-8"))
    assert request["candidate_release"] == "0.5.1+abc123"
    assert request["previous_release"] == "0.5.0+old"
    assert request["backup_path"] == str(backup)
    assert (host / "releases" / "0.5.1+abc123" / "release-manifest.json").is_file()
    assert result.archive.is_file()


def test_preflight_failure_never_creates_switch_request(monkeypatch, tmp_path):
    _, feed, public_key = _archive_and_feed(tmp_path)
    host, _ = _configure_host(monkeypatch, tmp_path, feed, public_key)
    backup = tmp_path / "pre-update.tar.gz"
    backup.write_bytes(b"verified-backup")

    class FakeBackupService:
        def __init__(self, *_args, **_kwargs):
            pass

        def create_backup(self, *, label: str):
            return backup

    def failed_preflight(*_args, **_kwargs):
        raise packaged.PackagedUpdateError("update_candidate_preflight_failed")

    monkeypatch.setattr(packaged, "BackupService", FakeBackupService)
    monkeypatch.setattr(packaged, "_preflight_candidate", failed_preflight)

    staged = packaged_commands.stage_update_blocking()
    assert staged["state"]["status"] == "failed"
    assert not (host / "update-request.json").exists()


def test_manual_rollback_requires_recent_committed_backup(monkeypatch, tmp_path):
    _, feed, public_key = _archive_and_feed(tmp_path)
    host, _ = _configure_host(monkeypatch, tmp_path, feed, public_key)
    backup = tmp_path / "pre-update.tar.gz"
    backup.write_bytes(b"backup")
    (host / "current.json").write_text(
        json.dumps(
            {"schema_version": 1, "current": "0.5.1+abc123", "previous": "0.5.0+old"}
        ),
        encoding="utf-8",
    )
    committed_state = {
        "schema_version": 1,
        "phase": "committed",
        "status": "success",
        "current_release": "0.5.1+abc123",
        "previous_release": "0.5.0+old",
        "backup_path": str(backup),
        "updated_at": f"unix:{time.time()}",
    }
    (host / "update-state.json").write_text(json.dumps(committed_state), encoding="utf-8")

    accepted = packaged_commands.request_rollback()
    assert accepted["accepted"] is True
    request = json.loads((host / "update-request.json").read_text(encoding="utf-8"))
    assert request["action"] == "rollback"
    assert request["backup_path"] == str(backup)

    (host / "update-request.json").unlink()
    committed_state["updated_at"] = f"unix:{time.time() - 3600}"
    (host / "update-state.json").write_text(json.dumps(committed_state), encoding="utf-8")
    expired = packaged_commands.request_rollback()
    assert expired == {"accepted": False, "reason": "rollback_window_expired"}


def test_installed_applier_never_calls_git(monkeypatch):
    class FakePackaged:
        @staticmethod
        def request_update():
            return {"accepted": True, "operation_id": "update-test", "message": "started"}

    monkeypatch.setattr(applier, "_is_packaged", lambda: True)
    monkeypatch.setattr(applier, "_packaged_module", lambda: FakePackaged)
    monkeypatch.setattr(
        applier.git_ops,
        "pull",
        lambda *_args, **_kwargs: pytest.fail("packaged updater must not invoke Git"),
    )
    monkeypatch.setattr(applier, "write_audit", lambda *_args, **_kwargs: None)

    result = applier.apply_update()
    assert result["mode"] == "packaged"
    assert result["accepted"] is True
