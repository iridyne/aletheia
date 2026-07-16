"""Fail-closed migration from repository-local data into the user runtime home."""

from __future__ import annotations

import json
import hashlib
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_MARKER = ".runtime-home-migration.json"
_PRIMARY_STATE = ("app.db", "config.yaml", "secrets.enc", ".secret.key")
_TRANSIENT_NAMES = {
    "gateway.pid",
    "operator-shell.pid",
    ".operator-shell.pid.lock",
    "frontdoor-route.json",
    ".audit.lock",
}


class RuntimeHomeMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationPlan:
    source: Path
    target: Path
    file_count: int
    total_bytes: int
    source_has_primary_state: bool
    target_has_primary_state: bool
    target_has_marker: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source"] = str(self.source)
        payload["target"] = str(self.target)
        return payload


def legacy_data_dir(project_root: Path) -> Path:
    return (project_root / "data").resolve()


def has_primary_state(root: Path) -> bool:
    if any((root / name).exists() for name in _PRIMARY_STATE):
        return True
    return (root / "logs" / "audit.jsonl").exists()


def migration_complete(root: Path) -> bool:
    return (root / MIGRATION_MARKER).is_file() and has_primary_state(root)


def _is_transient(path: Path) -> bool:
    name = path.name
    return (
        name in _TRANSIENT_NAMES
        or name.endswith("-wal")
        or name.endswith("-shm")
        or name.endswith(".pid")
        or name.endswith(".lock")
    )


def _source_entries(source: Path) -> list[tuple[Path, Path]]:
    entries = [
        (path, path.relative_to(source))
        for path in source.rglob("*")
        if path.is_file() and not _is_transient(path)
    ]
    legacy_env = source.parent / ".env"
    if source.name == "data" and legacy_env.is_file() and not (source / ".env").exists():
        entries.append((legacy_env, Path(".env")))
    return sorted(entries, key=lambda item: item[1].as_posix())


def build_plan(source: Path, target: Path) -> MigrationPlan:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    entries = _source_entries(source) if source.is_dir() else []
    return MigrationPlan(
        source=source,
        target=target,
        file_count=len(entries),
        total_bytes=sum(path.stat().st_size for path, _relative in entries),
        source_has_primary_state=has_primary_state(source),
        target_has_primary_state=has_primary_state(target),
        target_has_marker=migration_complete(target),
    )


def assert_runtime_home_ready(*, project_root: Path, target: Path, override_active: bool) -> None:
    """Block silent empty-state startup when a legacy primary runtime exists."""
    if override_active:
        return
    source = legacy_data_dir(project_root)
    if not has_primary_state(source):
        return
    if migration_complete(target):
        return
    raise RuntimeHomeMigrationError(
        f"Legacy runtime state exists at {source}, but {target} has no completed "
        "migration marker. Run `aletheia runtime migrate-home --from "
        f"{source} --dry-run`, then repeat with --apply."
    )


def _pid_is_alive(path: Path, markers: tuple[str, ...]) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if platform.system() == "Windows":
        from aletheia.gateway._win32_proc import pid_alive_win32

        return pid_alive_win32(pid, markers)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _assert_source_stopped(source: Path) -> None:
    candidates = {
        "gateway.pid": ("aletheia._gateway_runner", "uvicorn"),
        "operator-shell.pid": ("operator-shell", "node"),
    }
    live = [name for name, markers in candidates.items() if _pid_is_alive(source / name, markers)]
    if live:
        raise RuntimeHomeMigrationError(
            "Runtime processes must be stopped before migration; live PID files: " + ", ".join(live)
        )


def _checkpoint_and_verify_database(path: Path) -> None:
    if not path.exists():
        return
    try:
        with sqlite3.connect(str(path), timeout=30) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            row = db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise RuntimeHomeMigrationError(f"SQLite validation failed for {path}: {exc}") from exc
    if not row or row[0] != "ok":
        raise RuntimeHomeMigrationError(f"SQLite integrity_check failed for {path}: {row}")


def _copy_to_staging(source: Path, staging: Path) -> None:
    for source_file, relative in _source_entries(source):
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_audit_chain(staging: Path) -> None:
    if not (staging / "logs" / "audit.jsonl").exists():
        return
    env = os.environ.copy()
    env["ALETHEIA_DATA_DIR"] = str(staging)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from aletheia.files.jsonl import verify_audit_chain; "
            "print(json.dumps(verify_audit_chain()))",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeHomeMigrationError(f"Audit-chain validation failed: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeHomeMigrationError("Audit-chain validation returned invalid output") from exc
    if not payload.get("valid"):
        raise RuntimeHomeMigrationError(
            f"Audit-chain validation failed with {len(payload.get('errors') or [])} error(s)"
        )


def _verify_copy(source: Path, staging: Path) -> None:
    for source_file, relative in _source_entries(source):
        copied = staging / relative
        if (
            not copied.is_file()
            or copied.stat().st_size != source_file.stat().st_size
            or _sha256(copied) != _sha256(source_file)
        ):
            raise RuntimeHomeMigrationError(f"Copied file verification failed: {relative}")
    _checkpoint_and_verify_database(staging / "app.db")
    config_path = staging / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            from aletheia.config.schema import AletheiaConfig

            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            AletheiaConfig.model_validate(payload)
        except Exception as exc:
            raise RuntimeHomeMigrationError(f"Config validation failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeHomeMigrationError("Config validation failed: config.yaml must be a mapping")
    _verify_audit_chain(staging)


def _write_marker(target: Path, plan: MigrationPlan) -> None:
    payload = {
        "schema_version": 1,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source": str(plan.source),
        "target": str(plan.target),
        "file_count": plan.file_count,
        "total_bytes": plan.total_bytes,
        "source_preserved": True,
    }
    fd, temp_name = tempfile.mkstemp(prefix=".runtime-home-migration.", suffix=".tmp", dir=target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target / MIGRATION_MARKER)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def apply_migration(source: Path, target: Path) -> MigrationPlan:
    plan = build_plan(source, target)
    if not plan.source_has_primary_state:
        raise RuntimeHomeMigrationError(f"No primary runtime state found at {plan.source}")
    if plan.source == plan.target or plan.target.is_relative_to(plan.source):
        raise RuntimeHomeMigrationError("Migration target must be outside the legacy source")
    if plan.target_has_marker:
        return plan
    if plan.target_has_primary_state:
        raise RuntimeHomeMigrationError(
            f"Target already contains primary state without {MIGRATION_MARKER}: {plan.target}"
        )
    _assert_source_stopped(plan.source)
    _checkpoint_and_verify_database(plan.source / "app.db")
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(plan.target.parent).free
    if free_bytes < plan.total_bytes:
        raise RuntimeHomeMigrationError(
            f"Insufficient free space: need {plan.total_bytes} bytes, have {free_bytes}"
        )
    staging = plan.target.parent / f".{plan.target.name}.migrating-{os.getpid()}"
    if staging.exists():
        raise RuntimeHomeMigrationError(f"Migration staging path already exists: {staging}")
    staging.mkdir(mode=0o700)
    moved: list[Path] = []
    try:
        _copy_to_staging(plan.source, staging)
        _verify_copy(plan.source, staging)
        plan.target.mkdir(mode=0o700, parents=True, exist_ok=True)
        for child in sorted(staging.iterdir(), key=lambda path: path.name):
            destination = plan.target / child.name
            if destination.exists():
                raise RuntimeHomeMigrationError(f"Migration target collision: {destination}")
            os.replace(child, destination)
            moved.append(destination)
        try:
            os.chmod(plan.target, 0o700)
            for name in ("secrets.enc", ".secret.key", "config.yaml"):
                path = plan.target / name
                if path.exists():
                    os.chmod(path, 0o600)
        except OSError:
            pass
        _write_marker(plan.target, plan)
    except Exception:
        for destination in reversed(moved):
            if destination.is_dir():
                shutil.rmtree(destination, ignore_errors=True)
            else:
                destination.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return build_plan(plan.source, plan.target)
