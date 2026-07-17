"""Packaged-runtime update staging and verification.

The installed product never mutates a Git checkout.  The currently running packaged
runtime downloads and verifies a platform-specific immutable payload while the existing
gateway remains available, preflights migrations against a restored database snapshot,
and then writes one atomic ``update-request.json`` for the stable tray host.  The tray
owns the short stop/switch/health/rollback transaction.

Release feed envelope (schema v1)::

    {
      "schema_version": 1,
      "signed_payload": "{...exact JSON string...}",
      "signature": "<base64 Ed25519 signature over signed_payload UTF-8 bytes>"
    }

Keeping the signed payload as an exact string avoids ambiguous JSON canonicalisation.
The public key is never downloaded from the feed; it is installed out-of-band as
``update-public-key.pem`` under the host root or selected explicitly with
``ALETHEIA_UPDATE_PUBLIC_KEY_PATH``.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from typing import Any, Iterator
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from aletheia.backup.restore import RestoreService
from aletheia.backup.service import BackupService
from aletheia.config.loader import get_data_dir, load_config

HOST_ROOT_ENV = "ALETHEIA_HOST_ROOT"
FEED_URL_ENV = "ALETHEIA_UPDATE_FEED_URL"
PUBLIC_KEY_PATH_ENV = "ALETHEIA_UPDATE_PUBLIC_KEY_PATH"
BEARER_TOKEN_ENV = "ALETHEIA_UPDATE_BEARER_TOKEN"
PACKAGED_ENV = "ALETHEIA_PACKAGED"
_ACTIVE_PHASES = {
    "checking",
    "downloading",
    "verifying",
    "installing",
    "preflight",
    "ready_to_switch",
    "quiescing",
    "migrating",
    "starting",
    "soaking",
    "rolling_back",
}
_STAGE_LOCK_TTL_SEC = 60 * 60
_stage_thread_lock = threading.Lock()
_stage_thread: threading.Thread | None = None
_LOCAL_RELEASE_TIMESTAMP = re.compile(r"\.local-(\d{14})$")


class PackagedUpdateError(RuntimeError):
    """Fail-closed packaged update error with a stable reason code."""

    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_packaged_mode() -> bool:
    return os.environ.get(PACKAGED_ENV, "").strip() == "1" or bool(getattr(sys, "frozen", False))


def host_root() -> Path:
    explicit = os.environ.get(HOST_ROOT_ENV, "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise PackagedUpdateError("host_root_not_absolute")
        return path
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Aletheia"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if not base:
            raise PackagedUpdateError("local_app_data_unavailable")
        return Path(base) / "Aletheia"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg) if xdg else home / ".local" / "share") / "Aletheia"


def _paths() -> dict[str, Path]:
    root = host_root()
    return {
        "root": root,
        "releases": root / "releases",
        "downloads": root / "downloads",
        "preflight": root / "preflight",
        "current": root / "current.json",
        "state": root / "update-state.json",
        "request": root / "update-request.json",
        "lock": root / "update-stage.lock",
        "feed_url": root / "update-feed-url.txt",
        "public_key": root / "update-public-key.pem",
    }


def platform_id() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    system = {"darwin": "darwin", "windows": "windows", "linux": "linux"}.get(system, system)
    machine = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }.get(machine, machine)
    return f"{system}-{machine}"


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid4().hex[:8]}")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _read_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not required:
            return {}
        raise PackagedUpdateError("state_file_missing", str(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagedUpdateError("state_file_invalid", f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackagedUpdateError("state_file_invalid", f"{path}: expected object")
    return value


def current_selection() -> dict[str, Any]:
    selection = _read_json(_paths()["current"])
    if selection.get("schema_version") != 1 or not isinstance(selection.get("current"), str):
        raise PackagedUpdateError("current_selection_invalid")
    return selection


def read_update_state() -> dict[str, Any]:
    path = _paths()["state"]
    state = _read_json(path, required=False)
    if not state:
        selection = _read_json(_paths()["current"], required=False)
        return {
            "schema_version": 1,
            "mode": "packaged",
            "phase": "idle",
            "status": "idle",
            "current_release": selection.get("current"),
            "updated_at": None,
        }
    return state


def _write_state(
    *,
    operation_id: str,
    phase: str,
    status: str,
    target_release: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selection = _read_json(_paths()["current"], required=False)
    value: dict[str, Any] = {
        "schema_version": 1,
        "mode": "packaged",
        "operation_id": operation_id,
        "phase": phase,
        "status": status,
        "current_release": selection.get("current"),
        "previous_release": selection.get("previous"),
        "target_release": target_release,
        "progress": progress,
        "message": message,
        "error": error,
        "updated_at": _utc_now(),
    }
    if extra:
        value.update(extra)
    _atomic_json(_paths()["state"], value)
    return value


def _feed_url() -> str:
    value = os.environ.get(FEED_URL_ENV, "").strip()
    if value:
        return value
    path = _paths()["feed_url"]
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = ""
    if not value:
        raise PackagedUpdateError(
            "update_feed_not_configured",
            f"Set {FEED_URL_ENV} or write {path}.",
        )
    return value


def _public_key_path() -> Path:
    explicit = os.environ.get(PUBLIC_KEY_PATH_ENV, "").strip()
    path = Path(explicit).expanduser() if explicit else _paths()["public_key"]
    if not path.is_absolute():
        raise PackagedUpdateError("update_public_key_path_not_absolute")
    if not path.is_file():
        raise PackagedUpdateError("update_public_key_missing", str(path))
    return path


def _request_headers() -> dict[str, str]:
    token = os.environ.get(BEARER_TOKEN_ENV, "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _read_url(url: str, *, timeout: float = 30.0) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(parsed.path)
        return path.read_bytes()
    if parsed.scheme not in {"https", "http"}:
        raise PackagedUpdateError("update_url_scheme_unsupported", parsed.scheme)
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=timeout,
            headers=_request_headers(),
        )
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        raise PackagedUpdateError("update_download_failed", str(exc)) from exc


def _verified_feed(*, feed_url: str | None = None, public_key_path: Path | None = None) -> dict[str, Any]:
    raw = _read_url(feed_url or _feed_url())
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PackagedUpdateError("update_feed_invalid_json") from exc
    if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
        raise PackagedUpdateError("update_feed_schema_unsupported")
    signed_payload = envelope.get("signed_payload")
    signature_value = envelope.get("signature")
    if not isinstance(signed_payload, str) or not isinstance(signature_value, str):
        raise PackagedUpdateError("update_feed_signature_missing")
    try:
        signature = base64.b64decode(signature_value, validate=True)
        key_bytes = (public_key_path or _public_key_path()).read_bytes()
        public_key = serialization.load_pem_public_key(key_bytes)
    except Exception as exc:
        raise PackagedUpdateError("update_public_key_invalid", str(exc)) from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise PackagedUpdateError("update_public_key_not_ed25519")
    try:
        public_key.verify(signature, signed_payload.encode("utf-8"))
    except InvalidSignature as exc:
        raise PackagedUpdateError("update_feed_signature_invalid") from exc
    try:
        payload = json.loads(signed_payload)
    except json.JSONDecodeError as exc:
        raise PackagedUpdateError("update_signed_payload_invalid_json") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise PackagedUpdateError("update_signed_payload_schema_unsupported")
    return payload


def _artifact_for_host(feed: dict[str, Any]) -> dict[str, Any]:
    artifacts = feed.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PackagedUpdateError("update_artifacts_missing")
    artifact = artifacts.get(platform_id())
    if not isinstance(artifact, dict):
        raise PackagedUpdateError("update_platform_unavailable", platform_id())
    required = {"url", "sha256", "size_bytes"}
    if not required.issubset(artifact):
        raise PackagedUpdateError("update_artifact_metadata_incomplete")
    if artifact.get("archive_format", "tar.gz") != "tar.gz":
        raise PackagedUpdateError("update_archive_format_unsupported")
    return artifact


def _current_release_git_sha(selection: dict[str, Any]) -> str | None:
    """Return the current immutable payload SHA when its manifest is safe to inspect.

    Release IDs are normally validated by the stable host before it launches a payload.
    Update checks still treat an absent, malformed, or escaped local manifest as unknown
    rather than trusting it to suppress a signed update.
    """

    current = selection.get("current")
    if not isinstance(current, str) or not current or Path(current).name != current:
        return None
    release = _paths()["releases"] / current
    if release.is_symlink() or not release.is_dir():
        return None
    try:
        manifest = _read_json(release / "release-manifest.json")
    except PackagedUpdateError:
        return None
    git_sha = manifest.get("git_sha")
    return git_sha if isinstance(git_sha, str) and git_sha else None


def _feed_targets_current_source(selection: dict[str, Any], feed: dict[str, Any]) -> bool:
    """Treat distinct local release labels for the same immutable source as equivalent."""

    feed_sha = feed.get("git_sha")
    current_sha = _current_release_git_sha(selection)
    return isinstance(feed_sha, str) and bool(feed_sha) and feed_sha == current_sha


def _feed_is_not_newer_than_current_local_release(
    selection: dict[str, Any], feed: dict[str, Any]
) -> bool:
    """Prevent an older public feed from replacing an explicit local dogfood build.

    Git SHA values are opaque identifiers on an installed machine; they cannot establish
    ancestry without shipping a Git graph. Local release IDs deliberately contain an UTC
    timestamp, while signed feeds contain their publication time. Only a Feed published
    after that local build can be considered newer. If either value is malformed, leave
    the normal update path available rather than trusting ambiguous metadata.
    """

    current = selection.get("current")
    if not isinstance(current, str):
        return False
    matched = _LOCAL_RELEASE_TIMESTAMP.search(current)
    if matched is None:
        return False
    published_at = feed.get("published_at")
    if not isinstance(published_at, str):
        return False
    try:
        local_time = datetime.strptime(matched.group(1), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
        feed_time = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if feed_time.tzinfo is None:
            return False
    except ValueError:
        return False
    return feed_time <= local_time


def check_for_update() -> dict[str, Any]:
    try:
        selection = current_selection()
        feed = _verified_feed()
        artifact = _artifact_for_host(feed)
        target = str(feed.get("release_id") or "").strip()
        if not target:
            raise PackagedUpdateError("update_release_id_missing")
        current = str(selection["current"])
        same_source = _feed_targets_current_source(selection, feed)
        stale_for_local = _feed_is_not_newer_than_current_local_release(selection, feed)
        return {
            "ok": True,
            "mode": "packaged",
            "has_update": target != current and not same_source and not stale_for_local,
            "current_release": current,
            "available_release": target,
            "version": feed.get("version"),
            "git_sha": feed.get("git_sha"),
            "current_git_sha": _current_release_git_sha(selection),
            "same_source": same_source,
            "feed_stale_for_local_current": stale_for_local,
            "published_at": feed.get("published_at"),
            "platform": platform_id(),
            "size_bytes": artifact.get("size_bytes"),
            "state": read_update_state(),
        }
    except PackagedUpdateError as exc:
        return {
            "ok": False,
            "mode": "packaged",
            "reason": exc.reason,
            "message": str(exc),
            "state": read_update_state(),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_path(root: Path, member_name: str) -> Path:
    member = Path(member_name)
    if member.is_absolute() or any(part in {"", ".", ".."} for part in member.parts):
        raise PackagedUpdateError("update_archive_path_unsafe", member_name)
    destination = (root / member).resolve()
    root_resolved = root.resolve()
    if destination != root_resolved and root_resolved not in destination.parents:
        raise PackagedUpdateError("update_archive_path_unsafe", member_name)
    return destination


def _extract_archive(archive: Path, staging: Path) -> Path:
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            for member in members:
                _safe_member_path(staging, member.name)
                if not (member.isfile() or member.isdir()):
                    raise PackagedUpdateError("update_archive_member_unsupported", member.name)
            bundle.extractall(staging, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise PackagedUpdateError("update_archive_extract_failed", str(exc)) from exc
    roots = [path for path in staging.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise PackagedUpdateError("update_archive_root_invalid")
    return roots[0]


def _validate_release_dir(release_root: Path, *, expected_release_id: str, expected_sha: str) -> dict[str, Any]:
    manifest_path = release_root / "release-manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("target") != "packaged-runtime":
        raise PackagedUpdateError("update_manifest_target_invalid")
    if manifest.get("platform") != platform_id():
        raise PackagedUpdateError("update_manifest_platform_mismatch")
    if expected_sha and manifest.get("git_sha") != expected_sha:
        raise PackagedUpdateError("update_manifest_git_sha_mismatch")
    entrypoint = manifest.get("runtime_entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise PackagedUpdateError("update_manifest_entrypoint_missing")
    entrypoint_path = (release_root / entrypoint).resolve()
    root_resolved = release_root.resolve()
    if root_resolved not in entrypoint_path.parents or not entrypoint_path.is_file():
        raise PackagedUpdateError("update_manifest_entrypoint_unsafe")

    listed: set[str] = set()
    files = manifest.get("files")
    if not isinstance(files, list):
        raise PackagedUpdateError("update_manifest_files_missing")
    for item in files:
        if not isinstance(item, dict):
            raise PackagedUpdateError("update_manifest_file_invalid")
        relative = item.get("path")
        if not isinstance(relative, str):
            raise PackagedUpdateError("update_manifest_file_invalid")
        path = _safe_member_path(release_root, relative)
        if not path.is_file():
            raise PackagedUpdateError("update_manifest_file_missing", relative)
        if path.stat().st_size != int(item.get("size", -1)):
            raise PackagedUpdateError("update_manifest_file_size_mismatch", relative)
        if _sha256(path) != item.get("sha256"):
            raise PackagedUpdateError("update_manifest_file_hash_mismatch", relative)
        listed.add(relative)
    actual = {
        path.relative_to(release_root).as_posix()
        for path in release_root.rglob("*")
        if path.is_file() and path.name != "release-manifest.json"
    }
    if actual != listed:
        raise PackagedUpdateError(
            "update_manifest_file_set_mismatch",
            f"extra={sorted(actual - listed)[:5]} missing={sorted(listed - actual)[:5]}",
        )
    manifest["release_id"] = expected_release_id
    manifest["entrypoint_path"] = str(entrypoint_path)
    return manifest


def _download_artifact(
    artifact: dict[str, Any],
    destination: Path,
    *,
    operation_id: str,
    target_release: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.unlink(missing_ok=True)
    expected_size = int(artifact["size_bytes"])
    expected_hash = str(artifact["sha256"])
    url = str(artifact["url"])
    parsed = urlparse(url)
    try:
        if parsed.scheme == "file":
            source = Path(parsed.path)
            total = source.stat().st_size
            copied = 0
            with source.open("rb") as src, partial.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                    copied += len(chunk)
                    _write_state(
                        operation_id=operation_id,
                        phase="downloading",
                        status="running",
                        target_release=target_release,
                        progress=(copied / total) if total else None,
                        message="Downloading update payload",
                    )
        else:
            if parsed.scheme not in {"https", "http"}:
                raise PackagedUpdateError("update_url_scheme_unsupported", parsed.scheme)
            with httpx.stream(
                "GET",
                url,
                follow_redirects=True,
                timeout=120.0,
                headers=_request_headers(),
            ) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or expected_size or 0)
                copied = 0
                with partial.open("wb") as dst:
                    for chunk in response.iter_bytes(1024 * 1024):
                        dst.write(chunk)
                        copied += len(chunk)
                        _write_state(
                            operation_id=operation_id,
                            phase="downloading",
                            status="running",
                            target_release=target_release,
                            progress=(copied / total) if total else None,
                            message="Downloading update payload",
                        )
    except (OSError, httpx.HTTPError) as exc:
        partial.unlink(missing_ok=True)
        raise PackagedUpdateError("update_download_failed", str(exc)) from exc
    if partial.stat().st_size != expected_size:
        partial.unlink(missing_ok=True)
        raise PackagedUpdateError("update_artifact_size_mismatch")
    if _sha256(partial) != expected_hash:
        partial.unlink(missing_ok=True)
        raise PackagedUpdateError("update_artifact_hash_mismatch")
    os.replace(partial, destination)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


@contextmanager
def _stage_lock(operation_id: str) -> Iterator[None]:
    path = _paths()["lock"]
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"operation_id": operation_id, "pid": os.getpid(), "created_at": _utc_now()}, handle)
            break
        except FileExistsError:
            stale = False
            try:
                lock = _read_json(path)
                pid = int(lock.get("pid", 0))
                stale = (time.time() - path.stat().st_mtime > _STAGE_LOCK_TTL_SEC) or not _process_alive(pid)
            except Exception:
                stale = time.time() - path.stat().st_mtime > _STAGE_LOCK_TTL_SEC
            if not stale:
                raise PackagedUpdateError("update_already_in_progress")
            path.unlink(missing_ok=True)
    else:
        raise PackagedUpdateError("update_lock_unavailable")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _preflight_candidate(
    candidate_root: Path,
    *,
    backup_path: Path,
    operation_id: str,
    target_release: str,
) -> Path:
    preflight_root = _paths()["preflight"] / operation_id
    shutil.rmtree(preflight_root, ignore_errors=True)
    data_copy = preflight_root / "data-copy"
    restore = RestoreService().restore(backup_path, data_copy)
    if not restore.success:
        raise PackagedUpdateError("update_preflight_restore_failed", "; ".join(restore.errors))
    manifest = _validate_release_dir(
        candidate_root,
        expected_release_id=target_release,
        expected_sha=_read_json(candidate_root / "release-manifest.json").get("git_sha", ""),
    )
    executable = Path(str(manifest["entrypoint_path"]))
    env = dict(os.environ)
    env[PACKAGED_ENV] = "1"
    env[HOST_ROOT_ENV] = str(host_root())
    env["ALETHEIA_DATA_DIR"] = str(data_copy)
    result = subprocess.run(
        [str(executable), "__packaged_update_preflight"],
        cwd=candidate_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "candidate validation failed").strip()
        raise PackagedUpdateError("update_candidate_preflight_failed", detail[-2000:])
    return data_copy


def _stage_update(operation_id: str) -> None:
    paths = _paths()
    target_release: str | None = None
    try:
        with _stage_lock(operation_id):
            _write_state(
                operation_id=operation_id,
                phase="checking",
                status="running",
                message="Checking signed release feed",
            )
            selection = current_selection()
            feed = _verified_feed()
            artifact = _artifact_for_host(feed)
            target_release = str(feed.get("release_id") or "").strip()
            if not target_release:
                raise PackagedUpdateError("update_release_id_missing")
            if (
                target_release == selection["current"]
                or _feed_targets_current_source(selection, feed)
                or _feed_is_not_newer_than_current_local_release(selection, feed)
            ):
                _write_state(
                    operation_id=operation_id,
                    phase="idle",
                    status="up_to_date",
                    target_release=target_release,
                    progress=1.0,
                    message="Already running the selected release source",
                )
                return

            paths["downloads"].mkdir(parents=True, exist_ok=True)
            archive = paths["downloads"] / f"{target_release}.tar.gz"
            _download_artifact(
                artifact,
                archive,
                operation_id=operation_id,
                target_release=target_release,
            )
            _write_state(
                operation_id=operation_id,
                phase="verifying",
                status="running",
                target_release=target_release,
                progress=1.0,
                message="Verifying release manifest",
            )

            paths["releases"].mkdir(parents=True, exist_ok=True)
            destination = paths["releases"] / target_release
            if destination.exists():
                manifest = _validate_release_dir(
                    destination,
                    expected_release_id=target_release,
                    expected_sha=str(feed.get("git_sha") or ""),
                )
            else:
                staging_container = paths["releases"] / f".staging-{operation_id}"
                extracted = _extract_archive(archive, staging_container)
                manifest = _validate_release_dir(
                    extracted,
                    expected_release_id=target_release,
                    expected_sha=str(feed.get("git_sha") or ""),
                )
                os.replace(extracted, destination)
                shutil.rmtree(staging_container, ignore_errors=True)

            _write_state(
                operation_id=operation_id,
                phase="preflight",
                status="running",
                target_release=target_release,
                message="Creating consistent backup and preflighting migrations",
            )
            backup = BackupService(get_data_dir()).create_backup(label=f"pre_update_{operation_id[:12]}")
            preflight_data = _preflight_candidate(
                destination,
                backup_path=backup,
                operation_id=operation_id,
                target_release=target_release,
            )
            request = {
                "schema_version": 1,
                "operation_id": operation_id,
                "action": "activate",
                "candidate_release": target_release,
                "previous_release": selection["current"],
                "backup_path": str(backup),
                "preflight_data_path": str(preflight_data),
                "was_paused": bool(load_config().runtime.paused),
                "health_soak_sec": int(feed.get("health_soak_sec") or 30),
                "requested_at": _utc_now(),
                "manifest": {
                    "version": manifest.get("version"),
                    "git_sha": manifest.get("git_sha"),
                    "platform": manifest.get("platform"),
                },
            }
            _atomic_json(paths["request"], request)
            _write_state(
                operation_id=operation_id,
                phase="ready_to_switch",
                status="ready",
                target_release=target_release,
                progress=1.0,
                message="Update staged; waiting for tray-controlled activation",
                extra={"backup_path": str(backup)},
            )
    except Exception as exc:  # stage worker must always surface a durable terminal state
        reason = exc.reason if isinstance(exc, PackagedUpdateError) else "update_stage_failed"
        _write_state(
            operation_id=operation_id,
            phase="failed",
            status="failed",
            target_release=target_release,
            message="Update staging failed",
            error=f"{reason}: {exc}",
        )


def request_update() -> dict[str, Any]:
    global _stage_thread
    if not is_packaged_mode():
        raise PackagedUpdateError("not_packaged_mode")
    with _stage_thread_lock:
        state = read_update_state()
        if state.get("phase") in _ACTIVE_PHASES:
            return {
                "accepted": False,
                "reason": "update_already_in_progress",
                "operation_id": state.get("operation_id"),
                "state": state,
            }
        operation_id = f"update-{uuid4().hex}"
        _stage_thread = threading.Thread(
            target=_stage_update,
            args=(operation_id,),
            name=f"aletheia-update-stage-{operation_id[-8:]}",
            daemon=True,
        )
        _stage_thread.start()
    return {
        "accepted": True,
        "applied": False,
        "operation_id": operation_id,
        "message": "Update download and validation started in the background.",
        "state": read_update_state(),
    }


def request_rollback() -> dict[str, Any]:
    paths = _paths()
    selection = current_selection()
    previous = selection.get("previous")
    if not isinstance(previous, str) or not previous:
        return {"accepted": False, "reason": "previous_release_unavailable"}
    operation_id = f"rollback-{uuid4().hex}"
    request = {
        "schema_version": 1,
        "operation_id": operation_id,
        "action": "rollback",
        "candidate_release": previous,
        "previous_release": selection["current"],
        "backup_path": None,
        "was_paused": bool(load_config().runtime.paused),
        "health_soak_sec": 10,
        "requested_at": _utc_now(),
    }
    _atomic_json(paths["request"], request)
    _write_state(
        operation_id=operation_id,
        phase="ready_to_switch",
        status="ready",
        target_release=previous,
        message="Rollback requested; waiting for tray-controlled activation",
    )
    return {"accepted": True, "operation_id": operation_id, "state": read_update_state()}


def validate_current_data() -> dict[str, Any]:
    """Run candidate migration/config/SQLite/audit checks for preflight or activation."""

    from aletheia.files.jsonl import verify_audit_chain
    from aletheia.storage.migrations import run_migrations
    import sqlite3

    run_migrations()
    load_config()
    db_path = get_data_dir() / "app.db"
    if db_path.exists():
        connection = sqlite3.connect(str(db_path))
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
        if not result or result[0] != "ok":
            raise PackagedUpdateError("update_sqlite_integrity_failed", str(result))
    audit = verify_audit_chain()
    if not audit.get("valid"):
        raise PackagedUpdateError(
            "update_audit_integrity_failed",
            "; ".join(audit.get("errors") or [])[:2000],
        )
    return {
        "ok": True,
        "data_dir": str(get_data_dir()),
        "sqlite": "ok",
        "audit_records": audit.get("records", 0),
    }


def restore_update_backup(archive_path: Path) -> dict[str, Any]:
    """Restore a verified update backup while the gateway is stopped."""

    data_dir = get_data_dir()
    for suffix in ("-wal", "-shm"):
        (data_dir / f"app.db{suffix}").unlink(missing_ok=True)
    result = RestoreService().restore(archive_path, data_dir)
    if not result.success:
        raise PackagedUpdateError("update_backup_restore_failed", "; ".join(result.errors))
    validate_current_data()
    return result.to_dict()
