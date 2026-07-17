"""Contract tests for scripts/release/windows_dogfood.py.

Platform-agnostic contracts run on all OSes (signing, feed, host layout,
error paths).  Windows-only integration tests are gated with
``@pytest.mark.skipif(os.name != "nt", ...)``.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
DOGFOOD_PATH = ROOT / "scripts" / "release" / "windows_dogfood.py"
SIGN_PATH = ROOT / "scripts" / "release" / "sign_update_feed.py"
BUILDER_PATH = ROOT / "scripts" / "release" / "build_packaged_runtime.py"


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _dogfood():
    return _load("windows_dogfood", DOGFOOD_PATH)


def _builder():
    return _load("build_packaged_runtime", BUILDER_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_payload(root: Path) -> Path:
    """Minimal valid PyInstaller one-folder layout for contract tests."""
    payload = root / "aletheia-core"
    payload.mkdir(parents=True)
    exe_name = "aletheia-core.exe" if os.name == "nt" else "aletheia-core"
    exe = payload / exe_name
    exe.write_bytes(b"fake-binary")
    if os.name != "nt":
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    static = payload / "aletheia" / "dashboard_static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<html/>", encoding="utf-8")
    return payload


def _gen_keypair(tmp: Path) -> tuple[Path, Path]:
    key = Ed25519PrivateKey.generate()
    tmp.mkdir(parents=True, exist_ok=True)
    priv = tmp / "update-signing-private-key.pem"
    pub = tmp / "update-signing-public-key.pem"
    priv.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return priv, pub


def _fake_archive(tmp: Path, payload: Path, platform_id: str = "windows-x86_64") -> Path:
    """Build a real .tar.gz from a fake payload so the signing functions work."""
    rb = _builder()
    manifest = {
        "schema_version": 1,
        "target": "packaged-runtime",
        "platform": platform_id,
        "version": "0.5.0-test",
        "git_sha": "a" * 40,
        "runtime_entrypoint": "aletheia-core.exe" if os.name == "nt" else "aletheia-core",
        "dashboard_url": "http://127.0.0.1:8787",
        "runtime_data_contract": "per-user-dot-aletheia",
    }
    (payload / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    out = tmp / "runtime"
    out.mkdir()
    rb.create_deterministic_archive(payload, out / "aletheia-runtime-0.5.0-test-aaaaaaaaaaaaa-windows-x86_64.tar.gz")
    archive = next(out.glob("*.tar.gz"))
    rb._sha256  # just import-check
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    import hashlib
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


# ---------------------------------------------------------------------------
# Signing-key management
# ---------------------------------------------------------------------------

def test_ensure_signing_key_generates_pair_when_absent(tmp_path):
    m = _dogfood()
    priv = tmp_path / "keys" / "update-signing-private-key.pem"
    returned_priv, returned_pub = m._ensure_signing_key(priv)
    assert returned_priv == priv
    assert returned_priv.is_file()
    assert returned_pub.is_file()
    # Key is a valid PEM
    Ed25519PrivateKey.from_private_bytes(
        serialization.load_pem_private_key(
            returned_priv.read_bytes(), password=None
        ).private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    )


def test_ensure_signing_key_returns_existing_pair(tmp_path):
    m = _dogfood()
    priv, pub = _gen_keypair(tmp_path)
    returned_priv, returned_pub = m._ensure_signing_key(priv)
    assert returned_priv == priv
    assert returned_pub == pub
    assert returned_pub.read_bytes() == pub.read_bytes()


def test_ensure_signing_key_raises_when_public_key_missing(tmp_path):
    m = _dogfood()
    priv, pub = _gen_keypair(tmp_path)
    pub.unlink()
    with pytest.raises(m.WindowsDogfoodError, match="public key is missing"):
        m._ensure_signing_key(priv)


# ---------------------------------------------------------------------------
# Bootstrap host layout
# ---------------------------------------------------------------------------

def test_bootstrap_host_writes_current_json_and_release_dir(tmp_path):
    m = _dogfood()
    payload = _fake_payload(tmp_path / "payload")
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"fake")
    priv, pub = _gen_keypair(tmp_path / "keys")
    host_root = tmp_path / "host"
    release_id = "0.5.0+abc123.local-20260717120000"

    m._bootstrap_host(
        payload_dir=payload,
        archive=archive,
        host_root=host_root,
        release_id=release_id,
        public_key_path=pub,
    )

    current = json.loads((host_root / "current.json").read_text(encoding="utf-8"))
    assert current["schema_version"] == 1
    assert current["current"] == release_id
    assert (host_root / "releases" / release_id).is_dir()
    assert (host_root / "update-public-key.pem").is_file()
    assert (host_root / "update-public-key.pem").read_bytes() == pub.read_bytes()


def test_bootstrap_host_does_not_overwrite_existing_trust_root(tmp_path):
    m = _dogfood()
    payload = _fake_payload(tmp_path / "payload")
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"fake")
    priv1, pub1 = _gen_keypair(tmp_path / "keys1")
    priv2, pub2 = _gen_keypair(tmp_path / "keys2")
    host_root = tmp_path / "host"
    release_id = "0.5.0+first"

    m._bootstrap_host(
        payload_dir=payload, archive=archive, host_root=host_root,
        release_id=release_id, public_key_path=pub1,
    )
    first_trust = (host_root / "update-public-key.pem").read_bytes()

    # Second bootstrap with a different key must not replace the trust root
    m._bootstrap_host(
        payload_dir=payload, archive=archive, host_root=host_root,
        release_id="0.5.0+second", public_key_path=pub2,
    )
    assert (host_root / "update-public-key.pem").read_bytes() == first_trust


# ---------------------------------------------------------------------------
# Trust-root assertion
# ---------------------------------------------------------------------------

def test_assert_trust_root_matches_passes_when_keys_equal(tmp_path):
    m = _dogfood()
    priv, pub = _gen_keypair(tmp_path / "keys")
    host_root = tmp_path / "host"
    host_root.mkdir()
    (host_root / "update-public-key.pem").write_bytes(pub.read_bytes())
    m._assert_trust_root_matches(pub, host_root)  # must not raise


def test_assert_trust_root_matches_raises_on_mismatch(tmp_path):
    m = _dogfood()
    priv1, pub1 = _gen_keypair(tmp_path / "keys1")
    priv2, pub2 = _gen_keypair(tmp_path / "keys2")
    host_root = tmp_path / "host"
    host_root.mkdir()
    (host_root / "update-public-key.pem").write_bytes(pub1.read_bytes())
    with pytest.raises(m.WindowsDogfoodError, match="does not match"):
        m._assert_trust_root_matches(pub2, host_root)


def test_assert_trust_root_raises_when_missing(tmp_path):
    m = _dogfood()
    priv, pub = _gen_keypair(tmp_path / "keys")
    host_root = tmp_path / "host"
    host_root.mkdir()
    with pytest.raises(m.WindowsDogfoodError, match="trust root is missing"):
        m._assert_trust_root_matches(pub, host_root)


# ---------------------------------------------------------------------------
# Local feed server
# ---------------------------------------------------------------------------

def test_local_feed_server_serves_files_only_for_session(tmp_path):
    from urllib.request import urlopen

    m = _dogfood()
    site = tmp_path / "site"
    site.mkdir()
    (site / "update-feed.json").write_text('{"ok": true}', encoding="utf-8")

    with m.LocalFeedServer(site) as server:
        assert server.origin.startswith("http://127.0.0.1:")
        with urlopen(f"{server.origin}/update-feed.json", timeout=5) as resp:
            body = json.loads(resp.read().decode())
        assert body == {"ok": True}

    # Server is stopped after __exit__
    import socket
    port = int(server.origin.split(":")[-1])
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)


# ---------------------------------------------------------------------------
# Platform guard
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="guard only fires on non-Windows")
def test_require_windows_raises_on_non_windows():
    m = _dogfood()
    with pytest.raises(m.WindowsDogfoodError, match="must run on Windows"):
        m._require_windows()


@pytest.mark.skipif(os.name != "nt", reason="Windows-only")
def test_require_windows_passes_on_windows():
    m = _dogfood()
    m._require_windows()  # must not raise


# ---------------------------------------------------------------------------
# Release ID format
# ---------------------------------------------------------------------------

def test_release_id_from_archive_contains_version_and_sha(tmp_path):
    m = _dogfood()
    payload = _fake_payload(tmp_path / "payload")
    archive = _fake_archive(tmp_path, payload)
    release_id = m._release_id_from_archive(archive)
    assert release_id.startswith("0.5.0-test+")
    assert ".local-" in release_id
    # path-safe: no slashes, not . or ..
    assert "/" not in release_id and "\\" not in release_id
    assert release_id not in {".", ".."}
