"""Build and apply one private, localhost-only packaged-runtime release.

This is a developer/owner tool, not part of the installed product.  It deliberately
reuses the normal signed update transaction instead of copying files into an installed
release directory.  A session is short lived:

build payload -> sign localhost feed -> stage through installed CLI -> wait for Tray
-> stop the localhost server.

The server binds only to ``127.0.0.1``.  It is therefore useful for local dogfooding
without GitHub Actions, GitHub Pages, or a permanently running release service.
"""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

from aletheia.update.packaged import host_root
from scripts.release import build_packaged_runtime as runtime_builder
from scripts.release import sign_update_feed

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = ROOT / "dist" / "local-release-sessions"
DEFAULT_PRIVATE_KEY = (
    Path.home() / ".aletheia" / "release-signing" / "update-signing-private-key.pem"
)
TERMINAL_SUCCESS = {"success"}
TERMINAL_FAILURE = {"failed"}


class LocalReleaseSessionError(RuntimeError):
    """A local release session failed before its Tray transaction completed."""


class _LocalOnlyHandler(SimpleHTTPRequestHandler):
    """Static handler with no directory listings and no request log noise."""

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def list_directory(self, _path: str):  # type: ignore[override]
        self.send_error(404, "Not found")
        return None


class LocalFeedServer(AbstractContextManager["LocalFeedServer"]):
    """A localhost-only static server that is always shut down by its context."""

    def __init__(self, directory: Path, *, port: int = 0) -> None:
        if not 0 <= port <= 65535:
            raise LocalReleaseSessionError("local feed port must be between 0 and 65535")
        self.directory = directory
        handler = partial(_LocalOnlyHandler, directory=str(directory))
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="aletheia-local-release-feed",
            daemon=True,
        )

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def __enter__(self) -> "LocalFeedServer":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


@dataclass(frozen=True)
class SessionArtifacts:
    session_dir: Path
    site_dir: Path
    archive: Path
    feed: Path
    public_key: Path
    feed_url: str
    release_id: str


def _require_regular_file(path: Path, *, label: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        raise LocalReleaseSessionError(f"{label} must be an absolute path")
    if candidate.is_symlink() or not candidate.is_file():
        raise LocalReleaseSessionError(f"{label} must be an existing regular file: {candidate}")
    return candidate


def _new_session_dir(output_root: Path) -> Path:
    root = output_root.expanduser()
    if not root.is_absolute():
        raise LocalReleaseSessionError("output directory must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise LocalReleaseSessionError(f"output directory is not a normal directory: {root}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_dir = root / f"session-{stamp}-{uuid4().hex[:8]}"
    session_dir.mkdir(mode=0o700)
    return session_dir


def _run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ModuleNotFoundError:
        _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "pyinstaller==6.16.0",
            ]
        )


def _build_frontend() -> None:
    frontend = ROOT / "frontend"
    _run(["npm", "ci"], cwd=frontend)
    _run(["npm", "run", "lint"], cwd=frontend)
    _run(["npm", "run", "build"], cwd=frontend)


def build_local_payload(session_dir: Path, *, max_mib: int = runtime_builder.DEFAULT_MAX_MIB):
    """Build a fresh immutable runtime under this session directory only."""

    _build_frontend()
    _ensure_pyinstaller()
    payload_dir = runtime_builder.run_pyinstaller(build_root=session_dir / "pyinstaller")
    return runtime_builder.package_existing_payload(
        payload_dir,
        session_dir / "runtime",
        git_sha=runtime_builder._git_sha(),  # noqa: SLF001 - release builder owns this source metadata
        version=runtime_builder._version(),  # noqa: SLF001 - release builder owns this source metadata
        target=runtime_builder.platform_id(),
        max_mib=max_mib,
    )


def _local_release_id(archive: Path, requested: str | None) -> str:
    if requested:
        if any(char in requested for char in "/\\") or requested in {".", ".."}:
            raise LocalReleaseSessionError("release id must be a path-safe value")
        return requested
    manifest = sign_update_feed._load_manifest(archive)  # noqa: SLF001 - validates exactly what will be signed
    version = str(manifest["version"])
    git_sha = str(manifest["git_sha"])[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{version}+{git_sha}.local-{stamp}"


def materialize_local_site(
    *,
    session_dir: Path,
    archive: Path,
    private_key: Path,
    origin: str,
    release_id: str,
) -> SessionArtifacts:
    """Create a signed, localhost-addressed update site from one archive."""

    site_dir = session_dir / "site"
    site_dir.mkdir(mode=0o700, exist_ok=True)
    if site_dir.is_symlink() or not site_dir.is_dir():
        raise LocalReleaseSessionError(f"local release site is not a normal directory: {site_dir}")
    archive_copy = site_dir / archive.name
    shutil.copy2(archive, archive_copy)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    if checksum.is_file() and not checksum.is_symlink():
        shutil.copy2(checksum, site_dir / checksum.name)
    public_key = site_dir / "update-public-key.pem"
    feed = site_dir / "update-feed.json"
    envelope = sign_update_feed.signed_feed(
        archive=archive_copy,
        artifact_url=f"{origin}/{archive_copy.name}",
        private_key_path=private_key,
        release_id=release_id,
        public_key_output=public_key,
    )
    feed.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SessionArtifacts(
        session_dir=session_dir,
        site_dir=site_dir,
        archive=archive_copy,
        feed=feed,
        public_key=public_key,
        feed_url=f"{origin}/{feed.name}",
        release_id=release_id,
    )


def assert_installed_trust_root(public_key: Path, *, installed_host_root: Path | None = None) -> None:
    """Fail before staging if this local signer cannot be trusted by the installed app."""

    root = installed_host_root or host_root()
    trusted = root / "update-public-key.pem"
    if trusted.is_symlink() or not trusted.is_file():
        raise LocalReleaseSessionError(
            "installed Aletheia update public key is unavailable; install a feed-enabled app first"
        )
    if trusted.read_bytes() != public_key.read_bytes():
        raise LocalReleaseSessionError(
            "local signing key does not match the installed Aletheia update trust root"
        )


def _cli_output(command: list[str], *, env: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise LocalReleaseSessionError(f"installed CLI failed ({completed.returncode}): {detail}")
    return completed.stdout.strip()


def _read_update_state(cli: Path, *, env: dict[str, str]) -> dict[str, Any]:
    output = _cli_output([str(cli), "update", "status", "--json"], env=env)
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise LocalReleaseSessionError(f"installed CLI returned invalid update state: {output!r}") from exc
    if not isinstance(value, dict):
        raise LocalReleaseSessionError("installed CLI returned a non-object update state")
    return value


def apply_via_installed_cli(
    *,
    cli: Path,
    feed_url: str,
    timeout_sec: int,
) -> dict[str, Any]:
    if timeout_sec < 1:
        raise LocalReleaseSessionError("timeout must be at least one second")
    if not cli.expanduser().is_absolute():
        raise LocalReleaseSessionError("installed CLI path must be absolute")
    try:
        launcher = cli.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LocalReleaseSessionError(f"installed CLI cannot be resolved: {cli}") from exc
    if not launcher.is_file():
        raise LocalReleaseSessionError(f"installed CLI is not a file: {launcher}")
    env = dict(os.environ, ALETHEIA_UPDATE_FEED_URL=feed_url)
    _cli_output([str(launcher), "update", "check"], env=env)
    _cli_output([str(launcher), "update", "apply", "--yes"], env=env)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        state = _read_update_state(launcher, env=env)
        status = str(state.get("status", ""))
        if status in TERMINAL_SUCCESS:
            return state
        if status in TERMINAL_FAILURE:
            raise LocalReleaseSessionError(f"Tray rejected local update: {state.get('error')}")
        time.sleep(1)
    raise LocalReleaseSessionError("Tray did not finish the local update before timeout")


def run_local_release_session(
    *,
    output_root: Path,
    private_key: Path,
    cli: Path,
    port: int = 0,
    timeout_sec: int = 180,
    release_id: str | None = None,
) -> tuple[SessionArtifacts, dict[str, Any]]:
    """Build, serve, stage, and observe one local update; server exits on every path."""

    private_key = _require_regular_file(private_key, label="private signing key")
    session_dir = _new_session_dir(output_root)
    result = build_local_payload(session_dir)
    resolved_release_id = _local_release_id(result.archive, release_id)
    site_dir = session_dir / "site"
    site_dir.mkdir(mode=0o700)
    with LocalFeedServer(site_dir, port=port) as server:
        artifacts = materialize_local_site(
            session_dir=session_dir,
            archive=result.archive,
            private_key=private_key,
            origin=server.origin,
            release_id=resolved_release_id,
        )
        assert_installed_trust_root(artifacts.public_key)
        state = apply_via_installed_cli(
            cli=cli,
            feed_url=artifacts.feed_url,
            timeout_sec=timeout_sec,
        )
    return artifacts, state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--private-key", type=Path, default=DEFAULT_PRIVATE_KEY)
    parser.add_argument("--cli", type=Path, required=True, help="Absolute installed `aletheia` launcher path")
    parser.add_argument("--port", type=int, default=0, help="Localhost feed port; 0 chooses a free port")
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--release-id", help="Optional path-safe release ID for this local session")
    return parser


def main() -> int:
    args = _parser().parse_args()
    artifacts, state = run_local_release_session(
        output_root=args.output_dir,
        private_key=args.private_key,
        cli=args.cli,
        port=args.port,
        timeout_sec=args.timeout_sec,
        release_id=args.release_id,
    )
    print(
        json.dumps(
            {
                "status": "success",
                "release_id": artifacts.release_id,
                "session_dir": str(artifacts.session_dir),
                "feed_url": artifacts.feed_url,
                "archive": str(artifacts.archive),
                "update_state": state,
                "server_stopped": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
