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

from ops.backup.restore import RestoreService
from ops.backup.service import BackupService
from aletheia.config.loader import get_data_dir, load_config
from aletheia.update.process_env import safe_env
from aletheia.update.packaged import (
    HOST_ROOT_ENV, PACKAGED_ENV,
    PackagedUpdateError, is_packaged_mode, host_root, _paths, platform_id,
    _read_json, current_selection, read_update_state, _current_release_git_sha,
)

FEED_URL_ENV = "ALETHEIA_UPDATE_FEED_URL"
PUBLIC_KEY_PATH_ENV = "ALETHEIA_UPDATE_PUBLIC_KEY_PATH"
BEARER_TOKEN_ENV = "ALETHEIA_UPDATE_BEARER_TOKEN"
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
_DOWNLOAD_TIMEOUT_SEC = 120.0
_stage_thread_lock = threading.Lock()
_stage_thread: threading.Thread | None = None
_LOCAL_RELEASE_TIMESTAMP = re.compile(r"\.local-(\d{14})$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _extract_archive(archive, staging):
    from aletheia.package_integrity import extract_archive
    try:
        return extract_archive(archive, staging)
    except PackagedUpdateError as exc:
        if "symlink" in str(getattr(exc, "reason", "")):
            raise PackagedUpdateError(exc.reason, f"unsupported symbolic link: {exc}") from exc
        raise


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
    deadline = time.monotonic() + _DOWNLOAD_TIMEOUT_SEC

    def ensure_deadline() -> None:
        if time.monotonic() >= deadline:
            raise PackagedUpdateError(
                "update_download_timeout",
                f"update artifact download exceeded {_DOWNLOAD_TIMEOUT_SEC:g}s",
            )

    try:
        ensure_deadline()
        if parsed.scheme == "file":
            source = Path(parsed.path)
            total = source.stat().st_size
            copied = 0
            with source.open("rb") as src, partial.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    ensure_deadline()
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
                timeout=30.0,
                headers=_request_headers(),
            ) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or expected_size or 0)
                copied = 0
                with partial.open("wb") as dst:
                    for chunk in response.iter_bytes(1024 * 1024):
                        ensure_deadline()
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
    except PackagedUpdateError:
        partial.unlink(missing_ok=True)
        raise
    except (OSError, httpx.HTTPError) as exc:
        partial.unlink(missing_ok=True)
        raise PackagedUpdateError("update_download_failed", str(exc)) from exc
    try:
        ensure_deadline()
    except PackagedUpdateError:
        partial.unlink(missing_ok=True)
        raise
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
    expected_sha: str = "",
) -> Path:
    """Validate and preflight the candidate release directory.

    ``expected_sha`` must come from the *signed feed* (the external authority),
    not from the candidate's own manifest — reading the SHA from the file under
    test would be a circular self-certification that allows a tampered manifest
    to pass its own SHA check.  Callers must supply the feed-pinned value.
    """
    preflight_root = _paths()["preflight"] / operation_id
    shutil.rmtree(preflight_root, ignore_errors=True)
    data_copy = preflight_root / "data-copy"
    restore = RestoreService().restore(backup_path, data_copy)
    if not restore.success:
        raise PackagedUpdateError("update_preflight_restore_failed", "; ".join(restore.errors))
    manifest = _validate_release_dir(
        candidate_root,
        expected_release_id=target_release,
        expected_sha=expected_sha,
    )
    executable = Path(str(manifest["entrypoint_path"]))
    env = safe_env()
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
            backup = BackupService(get_data_dir()).create_backup(
                label=f"pre_update_{operation_id[:12]}", profile="online"
            )
            # Pass the feed-pinned git_sha so preflight validates against the
            # signed external authority, not the candidate's self-reported value.
            feed_sha = str(feed.get("git_sha") or "")
            preflight_data = _preflight_candidate(
                destination,
                backup_path=backup,
                operation_id=operation_id,
                target_release=target_release,
                expected_sha=feed_sha,
            )
            request = {
                "schema_version": 1,
                "operation_id": operation_id,
                "action": "activate",
                "candidate_release": target_release,
                "previous_release": selection["current"],
                "backup_path": str(backup),
                "backup_profile": "online",
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


def request_update():
    from ops.update.packaged_commands import request_update as command
    return command()


def request_rollback():
    from ops.update.packaged_commands import request_rollback as command
    return command()


def validate_current_data() -> dict[str, Any]:
    """Run candidate migration/config/SQLite/audit checks for preflight or activation."""

    from aletheia.files.jsonl import verify_audit_chain
    from aletheia.storage.migrations import apply_migrations
    import sqlite3

    apply_migrations()
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
    from extensions.observation_lab.kernel import invalidate_active_for_restore
    from extensions.observation_lab.worker_lock import worker_cycle_exclusion

    data_dir = get_data_dir()
    with worker_cycle_exclusion():
        for database in (data_dir / "app.db", data_dir / "observation-lab" / "lab.db"):
            for suffix in ("-wal", "-shm"):
                # Both stores are part of the authoritative cut.  Stale
                # sidecars beside either restored database can replay frames
                # from the newer runtime and invalidate the rollback proof.
                (database.parent / f"{database.name}{suffix}").unlink(missing_ok=True)
        result = RestoreService().restore(
            archive_path,
            data_dir,
            True,
        )
        if not result.success:
            raise PackagedUpdateError("update_backup_restore_failed", "; ".join(result.errors))
        validate_current_data()
        invalidated = invalidate_active_for_restore(joint_cut_proven=False)
    return {**result.to_dict(), "observation_windows_invalidated": invalidated}

from aletheia.package_integrity import validate_release_dir as _validate_release_dir
