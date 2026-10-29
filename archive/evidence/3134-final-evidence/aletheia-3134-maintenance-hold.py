"""One-time trusted old-6749 maintenance hold; PREPARED ONLY, NOT EXECUTED.

Caller must first stop/prove absence of owned Gateway, deploy and Observer
writers and disable their triggers. This attestation is not checked here.
Invoke with old repo interpreter and --quiescence-verified. Keep stdin open;
only exact RELEASE authorizes normal lock release. EOF/signals fail-stop:
locks unwind, but this script never starts services or resumes timers.
The private receipt/archive remain on disk on every exit for recovery.
"""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile

OLD_HEAD = "6749c31b62a36a0497659cf25608025f8ab4218f"
REPO = Path("/home/admin/aletheia")
DATA = Path("/home/admin/.aletheia")


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, sort_keys=True),
          file=sys.__stdout__, flush=True)


def interrupted(signum, _frame):
    emit("ABORT", reason="signal", signal=signum)
    raise SystemExit(128 + signum)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiescence-verified", action="store_true", required=True)
    parser.parse_args()
    os.umask(0o077)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    require(REPO.is_dir() and DATA.is_dir(), "required_roots_missing")
    require(not os.environ.get("ALETHEIA_PREVIEW_MODE"), "preview_env_forbidden")
    require(Path(tempfile.gettempdir()).resolve() == Path("/var/tmp"), "disk_tmpdir_required")
    # Read-only Git calls; never print status output or mutate refs.
    head = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    require(head == OLD_HEAD, "old_head_mismatch")
    status = subprocess.check_output(["git", "-C", str(REPO), "status", "--porcelain"], text=True)
    require(not status, "old_checkout_not_clean")
    sys.path.insert(0, str(REPO))
    import aletheia
    from aletheia.config import loader
    require(Path(aletheia.__file__).resolve() == REPO / "aletheia/__init__.py", "old_package_mismatch")
    require(Path(loader.__file__).resolve() == REPO / "aletheia/config/loader.py", "old_loader_mismatch")
    require(loader._PROJECT_ROOT.resolve() == REPO, "old_project_root_mismatch")
    require(loader.get_data_dir().resolve() == DATA, "effective_data_root_mismatch")
    from aletheia.update import auto, continuity
    from aletheia.observation_lab.runner import observation_settlement_barrier
    from aletheia.observation_lab.checkpoint import verify_joint_checkpoint
    from aletheia.backup.service import BackupService, DEFAULT_KEEP_RECENT
    from aletheia.backup.verify import verify_backup
    import yaml

    private = Path(tempfile.mkdtemp(prefix="aletheia-3134-maintenance-", dir="/var/tmp"))
    emit("PREPARING", receipt_directory=str(private), old_head=OLD_HEAD)
    with auto._deployment_lock() as acquired:
        require(acquired, "deployment_lock_busy")
        before = continuity.capture_update_continuity()
        preflight = continuity.evaluate_preflight(before)
        require(preflight["ok"] is True, "continuity_preflight_failed")
        with observation_settlement_barrier() as settlement:
            require(settlement.get("ok") is True, "settlement_failed")
            checkpoint = verify_joint_checkpoint(create_lab=False, allow_main_outbox_progress=False)
            require(checkpoint.ok and checkpoint.last is not None
                    and checkpoint.reason == "fingerprint_match"
                    and checkpoint.last.joint_fingerprint == checkpoint.current_fingerprint,
                    "strict_joint_checkpoint_unproven")
            joint = checkpoint.current_fingerprint
            after = continuity.capture_update_continuity()
            verdict = continuity.evaluate_preflight(after)
            require(verdict["ok"] is True, "settled_preflight_failed")
            require(after["control"]["available"] is True
                    and after["control"]["disk_sha"] == OLD_HEAD
                    and after["observation"]["identities"].get("available") is True,
                    "continuity_identity_unavailable")
            # Preserve archives the existing owner's next rotation can retire.
            prior = [path for path in (DATA / "backups").glob("backup_*.tar.gz") if path.is_file()]
            prior.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            preserved = []
            recovery = private / "recovery-backups"
            recovery.mkdir(mode=0o700)
            for source in prior[max(0, DEFAULT_KEEP_RECENT - 1):]:
                require(not source.is_symlink(), "rotation_source_symlink")
                destination = recovery / source.name
                require(not destination.exists(), "rotation_copy_already_exists")
                source_hash = digest(source)
                shutil.copy2(source, destination)
                require(digest(destination) == source_hash == digest(source), "rotation_copy_mismatch")
                with destination.open("rb") as handle:
                    os.fsync(handle.fileno())
                destination.chmod(0o400)
                preserved.append({"source": str(source), "recovery_copy": str(destination),
                                  "sha256": source_hash, "size_bytes": destination.stat().st_size})
            recovery_receipt = private / "preserved-backups.json"
            with recovery_receipt.open("x", encoding="utf-8") as handle:
                json.dump(preserved, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            emit("ROTATION_PRESERVED", receipt=str(recovery_receipt), backups=preserved)
            archive = BackupService(DATA).create_backup(label="3134-pre-scheme-a", profile="authoritative")
            snapshot = private / "authoritative.tar.gz"
            with archive.open("rb") as source, snapshot.open("xb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            snapshot_hash = digest(snapshot)
            require(snapshot_hash == digest(archive), "archive_copy_mismatch")
            verified = verify_backup(snapshot)
            require(verified.valid and verified.recovery_eligible
                    and verified.profile == "authoritative", "backup_verification_failed")
            with tarfile.open(snapshot, "r:gz") as bundle:
                entries = [item for item in bundle.getmembers()
                           if item.name == archive.name.removesuffix(".tar.gz") + "/manifest.yaml"]
                require(len(entries) == 1 and entries[0].isfile(), "manifest_member_invalid")
                with bundle.extractfile(entries[0]) as handle:
                    manifest = yaml.safe_load(handle)
            identities = {key: manifest.get(key) for key in (
                "checkpoint_identity", "sqlite_identity", "lab_sqlite_identity", "lab_checkpoint_identity")}
            require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                        for value in identities.values()), "backup_identity_invalid")
            require(identities["checkpoint_identity"] == verified.checkpoint_identity
                    and identities["lab_checkpoint_identity"] == joint,
                    "backup_joint_cut_mismatch")
            require(digest(snapshot) == snapshot_hash, "verified_snapshot_changed")
            snapshot.chmod(0o400)
            receipt = private / "receipt.json"
            with receipt.open("x", encoding="utf-8") as handle:
                json.dump({"old_head": OLD_HEAD, "data_root": str(DATA),
                           "archive": str(snapshot), "archive_sha256": snapshot_hash,
                           "preserved_backups": preserved,
                           "backup_identities": identities, "settled_joint_fingerprint": joint,
                           "before": before, "settled": after,
                           "preflight": preflight, "settled_preflight": verdict}, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            emit("HELD", receipt=str(receipt), archive=str(snapshot), archive_sha256=snapshot_hash,
                 backup_identities=identities, checked_files=verified.checked_count,
                 preserved_backups=preserved,
                 proof_gaps=verdict["proof_gaps"], warnings=verdict["warnings"])
            # No timeout/autonomous release and no background service. Caller must
            # monitor this process throughout activation; any death means STOP.
            require(sys.stdin.readline().rstrip("\r\n") == "RELEASE", "release_not_authorized")
    emit("RELEASED", services_started=False, timers_resumed=False)


if __name__ == "__main__":
    try:
        # Existing trusted owners may log details. Only explicit receipts above
        # reach the caller; never forward arbitrary exception/config messages.
        with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            main()
    except Exception as exc:
        emit("ABORT", reason=type(exc).__name__)
        raise SystemExit(1) from None
