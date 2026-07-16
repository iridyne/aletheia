"""Safe public command facade for packaged-runtime updates.

The low-level staging module exposes primitives used by tests and the private frozen
entrypoint. Installed API/CLI/Tray callers use this facade so a signed feed is pinned
for one operation, release identifiers cannot become filesystem paths, existing release
directories cannot be symlink escapes, and manual rollback is allowed only while the
pre-update backup still represents the current release transition.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import threading
import time
from typing import Any, Iterator
from uuid import uuid4

from aletheia.update import packaged

_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SAFE_STAGE_LOCK = threading.Lock()
_MANUAL_ROLLBACK_WINDOW_SEC = 10 * 60

PackagedUpdateError = packaged.PackagedUpdateError
is_packaged_mode = packaged.is_packaged_mode
read_update_state = packaged.read_update_state
restore_update_backup = packaged.restore_update_backup


def _validate_release_id(value: object) -> str:
    release_id = value.strip() if isinstance(value, str) else ""
    if not _RELEASE_ID.fullmatch(release_id) or release_id in {".", ".."}:
        raise PackagedUpdateError("update_release_id_invalid", repr(value))
    return release_id


def _validate_managed_destination(release_id: str) -> None:
    releases = packaged._paths()["releases"]  # noqa: SLF001 - facade owns this private boundary
    releases.mkdir(parents=True, exist_ok=True)
    managed = releases.resolve()
    destination = releases / release_id
    if destination.is_symlink():
        raise PackagedUpdateError("update_release_destination_symlink", str(destination))
    if destination.exists():
        resolved = destination.resolve()
        if managed not in resolved.parents:
            raise PackagedUpdateError("update_release_destination_escape", str(resolved))
        if not destination.is_dir():
            raise PackagedUpdateError("update_release_destination_not_directory")


def _validated_feed() -> dict[str, Any]:
    feed = packaged._verified_feed()  # noqa: SLF001 - exact verified payload is pinned below
    release_id = _validate_release_id(feed.get("release_id"))
    packaged._artifact_for_host(feed)  # noqa: SLF001 - fail before starting a worker
    _validate_managed_destination(release_id)
    return feed


@contextmanager
def _pin_verified_feed(feed: dict[str, Any]) -> Iterator[None]:
    """Pin one verified feed object for the entire staging operation.

    Without this, a mutable file/HTTP feed could change between the pre-validation and
    low-level staging fetch. The process-local replacement is serialized and restored
    in ``finally`` so unrelated update checks keep normal behavior.
    """

    with _SAFE_STAGE_LOCK:
        original = packaged._verified_feed  # noqa: SLF001
        packaged._verified_feed = lambda **_kwargs: feed  # type: ignore[assignment]  # noqa: SLF001
        try:
            yield
        finally:
            packaged._verified_feed = original  # type: ignore[assignment]  # noqa: SLF001


def check_for_update() -> dict[str, Any]:
    result = packaged.check_for_update()
    if not result.get("ok"):
        return result
    try:
        _validate_release_id(result.get("available_release"))
    except PackagedUpdateError as exc:
        return {
            "ok": False,
            "mode": "packaged",
            "reason": exc.reason,
            "message": str(exc),
            "state": packaged.read_update_state(),
        }
    return result


def _run_safe_stage(operation_id: str) -> dict[str, Any]:
    try:
        feed = _validated_feed()
        with _pin_verified_feed(feed):
            packaged._stage_update(operation_id)  # noqa: SLF001 - facade is the sole caller
    except Exception as exc:
        reason = exc.reason if isinstance(exc, PackagedUpdateError) else "update_stage_failed"
        packaged._write_state(  # noqa: SLF001
            operation_id=operation_id,
            phase="failed",
            status="failed",
            message="Update staging failed",
            error=f"{reason}: {exc}",
        )
    return packaged.read_update_state()


def stage_update_blocking() -> dict[str, Any]:
    state = packaged.read_update_state()
    if state.get("phase") in packaged._ACTIVE_PHASES:  # noqa: SLF001
        return {
            "accepted": False,
            "reason": "update_already_in_progress",
            "operation_id": state.get("operation_id"),
            "state": state,
        }
    operation_id = f"update-{uuid4().hex}"
    final_state = _run_safe_stage(operation_id)
    return {
        "accepted": True,
        "operation_id": operation_id,
        "state": final_state,
        "ok": final_state.get("status") in {"ready", "up_to_date"},
    }


def request_update() -> dict[str, Any]:
    global _BACKGROUND_THREAD
    state = packaged.read_update_state()
    if state.get("phase") in packaged._ACTIVE_PHASES:  # noqa: SLF001
        return {
            "accepted": False,
            "reason": "update_already_in_progress",
            "operation_id": state.get("operation_id"),
            "state": state,
        }
    operation_id = f"update-{uuid4().hex}"
    with _BACKGROUND_LOCK:
        if _BACKGROUND_THREAD is not None and _BACKGROUND_THREAD.is_alive():
            return {
                "accepted": False,
                "reason": "update_already_in_progress",
                "operation_id": state.get("operation_id"),
                "state": state,
            }
        _BACKGROUND_THREAD = threading.Thread(
            target=_run_safe_stage,
            args=(operation_id,),
            name=f"aletheia-update-stage-{operation_id[-8:]}",
            daemon=True,
        )
        _BACKGROUND_THREAD.start()
    return {
        "accepted": True,
        "applied": False,
        "operation_id": operation_id,
        "message": "Update download and validation started in the background.",
        "state": packaged.read_update_state(),
    }


def prepare_activation_data() -> dict[str, Any]:
    """Prepare durable data for the selected release after the old runtime stopped.

    For a normal update this runs the already-preflighted migration and integrity gates
    on the real runtime home. For a manual rollback it first restores the exact
    pre-update backup recorded by the committed update, then validates the old release
    against the restored data. The operation is safe to repeat after a tray crash because
    restoring the same verified backup is idempotent.
    """

    request_path = packaged._paths()["request"]  # noqa: SLF001
    request = packaged._read_json(request_path)  # noqa: SLF001
    action = request.get("action")
    if action == "rollback":
        backup_value = request.get("backup_path")
        backup = Path(backup_value) if isinstance(backup_value, str) else None
        if backup is None or not backup.is_absolute() or not backup.is_file():
            raise PackagedUpdateError("rollback_checkpoint_unavailable")
        restored = packaged.restore_update_backup(backup)
        return {"ok": True, "action": "rollback", "restored": restored}
    if action != "activate":
        raise PackagedUpdateError("update_action_invalid", repr(action))
    return {"ok": True, "action": "activate", "validation": packaged.validate_current_data()}


def _committed_timestamp(state: dict[str, Any]) -> float | None:
    value = state.get("updated_at")
    if not isinstance(value, str):
        return None
    if value.startswith("unix:"):
        try:
            return float(value.split(":", 1)[1])
        except ValueError:
            return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def request_rollback() -> dict[str, Any]:
    paths = packaged._paths()  # noqa: SLF001
    selection = packaged.current_selection()
    state = packaged.read_update_state()
    previous = selection.get("previous")
    if not isinstance(previous, str) or not previous:
        return {"accepted": False, "reason": "previous_release_unavailable"}
    _validate_release_id(previous)
    backup_value = state.get("backup_path")
    backup = Path(backup_value) if isinstance(backup_value, str) else None
    committed_at = _committed_timestamp(state)
    if (
        state.get("phase") != "committed"
        or state.get("current_release") != selection.get("current")
        or state.get("previous_release") != previous
        or backup is None
        or not backup.is_absolute()
        or not backup.is_file()
    ):
        return {"accepted": False, "reason": "rollback_checkpoint_unavailable"}
    if committed_at is None or time.time() - committed_at > _MANUAL_ROLLBACK_WINDOW_SEC:
        return {"accepted": False, "reason": "rollback_window_expired"}

    operation_id = f"rollback-{uuid4().hex}"
    request = {
        "schema_version": 1,
        "operation_id": operation_id,
        "action": "rollback",
        "candidate_release": previous,
        "previous_release": selection["current"],
        "backup_path": str(backup),
        "was_paused": bool(packaged.load_config().runtime.paused),
        "health_soak_sec": 10,
        "requested_at": packaged._utc_now(),  # noqa: SLF001
    }
    packaged._atomic_json(paths["request"], request)  # noqa: SLF001
    packaged._write_state(  # noqa: SLF001
        operation_id=operation_id,
        phase="ready_to_switch",
        status="ready",
        target_release=previous,
        message="Rollback requested; waiting for tray-controlled activation",
        extra={"backup_path": str(backup)},
    )
    return {"accepted": True, "operation_id": operation_id, "state": read_update_state()}


_BACKGROUND_LOCK = threading.Lock()
_BACKGROUND_THREAD: threading.Thread | None = None
