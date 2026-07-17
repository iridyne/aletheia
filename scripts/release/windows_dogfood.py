"""Build and apply one private Windows dogfood release on the local machine.

Agent-callable. No GitHub Actions, no self-hosted runners, no manual steps.
The entire build runs in the local checkout and deploys to the user's own machine.

Two modes (auto-detected):

  Bootstrap  — first-time install; builds Rust host binaries, assembles a host
               directory under %LOCALAPPDATA%\\Aletheia, and launches the tray.

  Update     — existing tray running; builds a new Python payload, signs a
               localhost feed, and applies it through the installed CLI exactly
               as the signed update channel would.

Quick start
-----------
  # Agent command — works for both first install and subsequent updates:
  uv run python scripts/release/windows_dogfood.py

  # Force a Rust host rebuild (e.g. after changing native/ sources):
  uv run python scripts/release/windows_dogfood.py --rebuild-host

  # Pull latest master before building:
  uv run python scripts/release/windows_dogfood.py --pull

Prerequisites (Windows)
-----------------------
  - Git, uv, Node.js >= 18, npm in PATH
  - Rust stable toolchain (rustup) — only needed on first install or --rebuild-host
  - Repo checked out locally; script must run from the repo root

Output
------
  JSON report on stdout with keys: mode, release_id, host_root, tray_pid (bootstrap)
  or update_state (update).  Non-zero exit on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SIGNING_KEY = Path.home() / ".aletheia" / "release-signing" / "update-signing-private-key.pem"
DEFAULT_OUTPUT_ROOT = ROOT / "dist" / "windows-dogfood-sessions"
TRAY_READINESS_TIMEOUT_SEC = 60
UPDATE_TIMEOUT_SEC = 180
TERMINAL_SUCCESS = {"success", "up_to_date"}
TERMINAL_FAILURE = {"failed"}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class WindowsDogfoodError(RuntimeError):
    """Raised when the dogfood session cannot complete."""


# ---------------------------------------------------------------------------
# Platform guard
# ---------------------------------------------------------------------------

def _require_windows() -> None:
    if os.name != "nt":
        raise WindowsDogfoodError(
            "windows_dogfood.py must run on Windows; "
            "use local_release_session.py for macOS/Linux"
        )


# ---------------------------------------------------------------------------
# Host root (mirrors aletheia/update/packaged.py host_root())
# ---------------------------------------------------------------------------

def _host_root() -> Path:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if not base:
        raise WindowsDogfoodError(
            "LOCALAPPDATA is not set; cannot locate the Aletheia host root"
        )
    return Path(base) / "Aletheia"


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------

def _run(args: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(args, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        raise WindowsDogfoodError(
            f"command failed (exit {result.returncode}): {' '.join(str(a) for a in args)}"
        )


def _require_clean_worktree() -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise WindowsDogfoodError(
            "dogfood session requires a clean worktree; commit or stash changes first"
        )


def _pull_latest() -> None:
    _run(["git", "fetch", "origin", "master"])
    _run(["git", "merge", "--ff-only", "origin/master"])


def _build_frontend() -> None:
    frontend = ROOT / "frontend"
    _run(["npm", "ci"], cwd=frontend)
    _run(["npm", "run", "lint"], cwd=frontend)
    _run(["npm", "run", "build"], cwd=frontend)


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ModuleNotFoundError:
        _run([sys.executable, "-m", "pip", "install", "pyinstaller==6.16.0"])


def _build_python_payload(session_dir: Path) -> "BuildResult":
    from scripts.release import build_packaged_runtime as rb

    if rb.platform_id().split("-")[0] != "windows":
        raise WindowsDogfoodError(
            f"windows_dogfood.py must run on Windows, detected platform: {rb.platform_id()}"
        )

    payload_dir = rb.run_pyinstaller(build_root=session_dir / "pyinstaller")
    return rb.package_existing_payload(
        payload_dir,
        session_dir / "runtime",
        git_sha=rb._git_sha(),  # noqa: SLF001
        version=rb._version(),  # noqa: SLF001
        target=rb.platform_id(),
    )


def _rust_bin_dir() -> Path:
    return ROOT / "native" / "aletheia-host" / "target" / "release"


def _rust_binaries_exist() -> bool:
    d = _rust_bin_dir()
    return (d / "aletheia.exe").is_file() and (d / "aletheia-tray.exe").is_file()


def _build_rust_host() -> None:
    _run(
        ["cargo", "build", "--locked", "--release", "--bins"],
        cwd=ROOT / "native" / "aletheia-host",
    )
    if not _rust_binaries_exist():
        raise WindowsDogfoodError(
            "cargo build succeeded but aletheia.exe / aletheia-tray.exe are missing"
        )


# ---------------------------------------------------------------------------
# Signing-key management
# ---------------------------------------------------------------------------

def _generate_signing_keypair(private_key_path: Path) -> Path:
    """Generate an Ed25519 keypair; write PEMs; return the public key path."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private_key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_key_path = private_key_path.with_name(
        private_key_path.stem.replace("private", "public") + ".pem"
    )
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return public_key_path


def _ensure_signing_key(private_key_path: Path) -> tuple[Path, Path]:
    """Return (private_pem, public_pem) paths, generating the pair if absent."""
    if private_key_path.is_file():
        public_key_path = private_key_path.with_name(
            private_key_path.stem.replace("private", "public") + ".pem"
        )
        if not public_key_path.is_file():
            raise WindowsDogfoodError(
                f"private key exists but public key is missing: {public_key_path}"
            )
        return private_key_path, public_key_path

    public_key_path = _generate_signing_keypair(private_key_path)
    print(f"[dogfood] generated new Ed25519 signing keypair at {private_key_path.parent}")
    return private_key_path, public_key_path


# ---------------------------------------------------------------------------
# Local feed server (identical to local_release_session.py)
# ---------------------------------------------------------------------------

class _SilentHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def list_directory(self, _path: str):  # type: ignore[override]
        self.send_error(404, "Not found")
        return None


class LocalFeedServer(AbstractContextManager["LocalFeedServer"]):
    def __init__(self, directory: Path, *, port: int = 0) -> None:
        handler = partial(_SilentHandler, directory=str(directory))
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._thread = Thread(
            target=self._server.serve_forever,
            name="aletheia-windows-local-feed",
            daemon=True,
        )

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "LocalFeedServer":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


# ---------------------------------------------------------------------------
# Bootstrap mode — first-time install
# ---------------------------------------------------------------------------

def _release_id_from_archive(archive: Path) -> str:
    from scripts.release import sign_update_feed
    from scripts.release import build_packaged_runtime as rb

    manifest = sign_update_feed._load_manifest(archive)  # noqa: SLF001
    version = str(manifest["version"])
    git_sha = str(manifest["git_sha"])[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{version}+{git_sha}.local-{stamp}"


def _bootstrap_host(
    *,
    payload_dir: Path,
    archive: Path,
    host_root: Path,
    release_id: str,
    public_key_path: Path,
) -> None:
    """Install the first release directly into the host root without using the feed."""
    releases_dir = host_root / "releases"
    release_root = releases_dir / release_id
    if release_root.exists():
        shutil.rmtree(release_root)
    shutil.copytree(payload_dir, release_root, symlinks=False)

    current = {
        "schema_version": 1,
        "current": release_id,
    }
    host_root.mkdir(parents=True, exist_ok=True)
    (releases_dir).mkdir(parents=True, exist_ok=True)
    (host_root / "downloads").mkdir(parents=True, exist_ok=True)
    (host_root / "current.json").write_text(
        json.dumps(current, indent=2) + "\n", encoding="utf-8"
    )

    # Seed the trust root so the update feed will be accepted later
    dest_public_key = host_root / "update-public-key.pem"
    if not dest_public_key.exists():
        shutil.copy2(public_key_path, dest_public_key)

    print(f"[dogfood] bootstrapped host root at {host_root}")
    print(f"[dogfood] installed release {release_id}")


def _is_tray_running() -> bool:
    """Check whether aletheia-tray.exe has an HTTP gateway ready."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", 8787), timeout=1):
            return True
    except OSError:
        return False


def _launch_tray(tray_exe: Path, host_root: Path) -> int:
    """Launch aletheia-tray.exe detached. Returns the new PID."""
    env = dict(os.environ)
    env["ALETHEIA_HOST_ROOT"] = str(host_root)
    proc = subprocess.Popen(
        [str(tray_exe)],
        env=env,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return proc.pid


def _wait_for_tray_ready(timeout_sec: int) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _is_tray_running():
            return
        time.sleep(1)
    raise WindowsDogfoodError(
        f"tray gateway did not become ready within {timeout_sec}s"
    )


# ---------------------------------------------------------------------------
# Update mode — apply through installed CLI
# ---------------------------------------------------------------------------

def _materialize_site(
    *,
    archive: Path,
    session_dir: Path,
    private_key_path: Path,
    origin: str,
    release_id: str,
) -> tuple[Path, str]:
    """Sign the archive and write a localhost feed. Returns (site_dir, feed_url)."""
    from scripts.release import sign_update_feed

    site_dir = session_dir / "site"
    site_dir.mkdir(mode=0o700, exist_ok=True)
    archive_copy = site_dir / archive.name
    shutil.copy2(archive, archive_copy)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    if checksum.is_file():
        shutil.copy2(checksum, site_dir / checksum.name)

    public_key_out = site_dir / "update-public-key.pem"
    feed = site_dir / "update-feed.json"
    envelope = sign_update_feed.signed_feed(
        archive=archive_copy,
        artifact_url=f"{origin}/{archive_copy.name}",
        private_key_path=private_key_path,
        release_id=release_id,
        public_key_output=public_key_out,
    )
    feed.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    return site_dir, f"{origin}/{feed.name}"


def _assert_trust_root_matches(public_key_path: Path, host_root: Path) -> None:
    trusted = host_root / "update-public-key.pem"
    if not trusted.is_file():
        raise WindowsDogfoodError(
            f"installed trust root is missing: {trusted}\n"
            "Run with --bootstrap to perform a first-time install first."
        )
    if trusted.read_bytes() != public_key_path.read_bytes():
        raise WindowsDogfoodError(
            "local signing key does not match the installed trust root.\n"
            "Use the same signing key that was used during --bootstrap, "
            f"or replace {trusted} with the matching public key."
        )


def _cli_run(cli: Path, args: list[str], *, env: dict[str, str]) -> str:
    result = subprocess.run(
        [str(cli)] + args,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WindowsDogfoodError(f"aletheia CLI failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def _read_update_state(cli: Path, *, env: dict[str, str]) -> dict[str, Any]:
    output = _cli_run(cli, ["update", "status", "--json"], env=env)
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise WindowsDogfoodError(f"invalid update state JSON: {output!r}") from exc
    if not isinstance(value, dict):
        raise WindowsDogfoodError("update status returned a non-object")
    return value


def _apply_update(cli: Path, feed_url: str, timeout_sec: int) -> dict[str, Any]:
    env = dict(os.environ, ALETHEIA_UPDATE_FEED_URL=feed_url)
    _cli_run(cli, ["update", "check"], env=env)
    _cli_run(cli, ["update", "apply", "--yes"], env=env)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        state = _read_update_state(cli, env=env)
        status = str(state.get("status", ""))
        if status in TERMINAL_SUCCESS:
            return state
        if status in TERMINAL_FAILURE:
            raise WindowsDogfoodError(f"tray rejected update: {state.get('error')}")
        time.sleep(1)
    raise WindowsDogfoodError(f"update did not complete within {timeout_sec}s")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _new_session_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_dir = output_root / f"session-{stamp}-{uuid4().hex[:8]}"
    session_dir.mkdir()
    return session_dir


def run_dogfood_session(
    *,
    output_root: Path,
    private_key_path: Path,
    pull: bool,
    rebuild_host: bool,
    port: int = 0,
) -> dict[str, Any]:
    _require_windows()

    if pull:
        print("[dogfood] pulling latest master...")
        _pull_latest()

    _require_clean_worktree()

    host_root = _host_root()
    is_first_install = not (host_root / "current.json").exists()
    mode = "bootstrap" if is_first_install else "update"
    print(f"[dogfood] mode: {mode}  host_root: {host_root}")

    # Build Rust host if needed
    if is_first_install or rebuild_host or not _rust_binaries_exist():
        print("[dogfood] building Rust host binaries (this may take a few minutes)...")
        _build_rust_host()
    tray_exe = _rust_bin_dir() / "aletheia-tray.exe"
    cli_exe = _rust_bin_dir() / "aletheia.exe"

    # Build Python payload
    session_dir = _new_session_dir(output_root)
    print("[dogfood] building frontend...")
    _build_frontend()
    print("[dogfood] building PyInstaller payload...")
    _ensure_pyinstaller()
    build_result = _build_python_payload(session_dir)
    release_id = _release_id_from_archive(build_result.archive)
    print(f"[dogfood] payload built  release_id={release_id}")

    # Ensure signing key
    private_key_path, public_key_path = _ensure_signing_key(private_key_path)

    if mode == "bootstrap":
        _bootstrap_host(
            payload_dir=build_result.payload_dir,
            archive=build_result.archive,
            host_root=host_root,
            release_id=release_id,
            public_key_path=public_key_path,
        )
        if _is_tray_running():
            print("[dogfood] tray gateway already running, skipping launch")
            tray_pid = None
        else:
            print("[dogfood] launching aletheia-tray.exe...")
            tray_pid = _launch_tray(tray_exe, host_root)
            print(f"[dogfood] tray launched (pid={tray_pid}), waiting for gateway...")
            _wait_for_tray_ready(TRAY_READINESS_TIMEOUT_SEC)
            print(f"[dogfood] gateway ready at http://127.0.0.1:8787")
        return {
            "mode": "bootstrap",
            "release_id": release_id,
            "host_root": str(host_root),
            "session_dir": str(session_dir),
            "tray_pid": tray_pid,
            "dashboard_url": "http://127.0.0.1:8787",
        }

    # Update mode: sign feed, start server, apply through CLI
    _assert_trust_root_matches(public_key_path, host_root)
    site_dir = session_dir / "site"
    site_dir.mkdir()
    with LocalFeedServer(site_dir, port=port) as server:
        _, feed_url = _materialize_site(
            archive=build_result.archive,
            session_dir=session_dir,
            private_key_path=private_key_path,
            origin=server.origin,
            release_id=release_id,
        )
        print(f"[dogfood] local feed at {feed_url}")
        print("[dogfood] applying update through installed CLI...")
        update_state = _apply_update(cli_exe, feed_url, UPDATE_TIMEOUT_SEC)
    print(f"[dogfood] update complete  status={update_state.get('status')}")
    return {
        "mode": "update",
        "release_id": release_id,
        "host_root": str(host_root),
        "session_dir": str(session_dir),
        "feed_url": feed_url,
        "update_state": update_state,
        "server_stopped": True,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="Run `git pull --ff-only origin master` before building",
    )
    parser.add_argument(
        "--rebuild-host",
        action="store_true",
        help="Force a Rust host rebuild even if aletheia.exe already exists",
    )
    parser.add_argument(
        "--private-key",
        type=Path,
        default=DEFAULT_SIGNING_KEY,
        help=f"Ed25519 private key PEM for signing the local feed (default: {DEFAULT_SIGNING_KEY})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Directory for session artifacts (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Localhost feed server port; 0 picks a free port (default: 0)",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run_dogfood_session(
            output_root=args.output_dir,
            private_key_path=args.private_key,
            pull=args.pull,
            rebuild_host=args.rebuild_host,
            port=args.port,
        )
    except WindowsDogfoodError as exc:
        print(f"[dogfood] ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
