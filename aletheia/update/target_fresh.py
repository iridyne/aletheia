"""Target-loaded helpers for source self-update verification and metadata writes.

The source updater itself necessarily starts under the pre-update interpreter.
After ``git pull`` that process may still hold old modules and old Pydantic model
objects in ``sys.modules``. Any operation whose semantics belong to the target
checkout therefore runs in a fresh child interpreter rooted at the on-disk
project. The helpers here deliberately pass only primitive values across that
boundary; no pre-pull config/model object is ever serialized into target state.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from aletheia.gateway.pidfile import _no_window_kwargs

_RESULT_PREFIX = "ALETHEIA_TARGET_FRESH_RESULT="


def _run_json_script(
    script: str,
    *,
    project_root: Path,
    env: dict[str, str],
    python_executable: str,
    timeout: int,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [python_executable, "-c", script],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            **_no_window_kwargs(),
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "target_fresh_process_failed",
            "detail": type(exc).__name__,
        }
    if result.returncode != 0:
        return {
            "ok": False,
            "reason": "target_fresh_process_failed",
            "detail": (result.stderr or result.stdout).strip()[-1000:],
        }
    for line in reversed(result.stdout.splitlines()):
        if not line.startswith(_RESULT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(_RESULT_PREFIX) :])
        except json.JSONDecodeError:
            break
        if isinstance(payload, dict):
            return payload
    return {
        "ok": False,
        "reason": "target_fresh_payload_missing",
        "detail": result.stdout.strip()[-1000:],
    }


def run_smoke_check(
    *,
    project_root: Path,
    env: dict[str, str],
    python_executable: str,
) -> dict[str, Any]:
    """Run the target checkout's smoke policy, not the updater process's copy."""
    script = (
        "import json\n"
        "from aletheia.update.smoke import run_smoke_check\n"
        "payload = run_smoke_check()\n"
        f"print({_RESULT_PREFIX!r} + json.dumps(payload, sort_keys=True))\n"
    )
    result = _run_json_script(
        script,
        project_root=project_root,
        env=env,
        python_executable=python_executable,
        timeout=300,
    )
    if result.get("ok") is False and result.get("stage") is None:
        return {
            "ok": False,
            "stage": "target_fresh_smoke_process",
            "detail": result.get("detail") or result.get("reason"),
        }
    return result


def validate_dashboard_artifacts(
    static_dir: Path,
    *,
    project_root: Path,
    env: dict[str, str],
    python_executable: str,
) -> dict[str, Any]:
    """Validate built dashboard output using the target checkout's validator."""
    script = (
        "import json\n"
        "from pathlib import Path\n"
        "from aletheia.gateway.preflight import dashboard_artifact_errors\n"
        f"errors = dashboard_artifact_errors(Path({str(static_dir)!r}))\n"
        f"print({_RESULT_PREFIX!r} + json.dumps({{'ok': not errors, 'errors': errors}}, sort_keys=True))\n"
    )
    return _run_json_script(
        script,
        project_root=project_root,
        env=env,
        python_executable=python_executable,
        timeout=60,
    )


def persist_current_commit(
    commit: str,
    *,
    project_root: Path,
    data_dir: Path,
    env: dict[str, str],
    python_executable: str,
) -> dict[str, Any]:
    """Persist only ``cfg.update.current_commit`` using target-loaded config code.

    ``mutate_config`` acquires the cross-process config writer permit before
    loading the latest committed config. This prevents the pre-pull updater
    from overwriting target-side schema migrations or concurrent settings with a
    stale whole-config snapshot. A successful child write also invalidates the
    updater process's local config cache so any later diagnostic read cannot
    return the pre-write snapshot.
    """
    child_env = dict(env)
    child_env["ALETHEIA_DATA_DIR"] = str(data_dir)
    script = (
        "import json\n"
        "from aletheia.config.loader import mutate_config\n"
        f"commit = {commit!r}\n"
        "def _apply(cfg):\n"
        "    changed = cfg.update.current_commit != commit\n"
        "    cfg.update.current_commit = commit\n"
        "    return changed\n"
        "changed = mutate_config(_apply)\n"
        f"print({_RESULT_PREFIX!r} + json.dumps({{'ok': True, 'changed': bool(changed)}}, sort_keys=True))\n"
    )
    result = _run_json_script(
        script,
        project_root=project_root,
        env=child_env,
        python_executable=python_executable,
        timeout=30,
    )
    if result.get("ok") is True:
        try:
            from aletheia.config import loader as config_loader

            config_loader._invalidate_config_cache()  # noqa: SLF001
        except Exception:
            # The disk write already succeeded. Cache invalidation is a local
            # coherence aid and must not relabel durable persistence as failed.
            pass
    return result


def checkpoint_delta_allowed(verdict: Any, expected_fingerprint: str) -> bool:
    """Allow only the target's migration/schema delta from the locked checkpoint."""
    last = getattr(verdict, "last", None)
    if not expected_fingerprint or last is None:
        return False
    if getattr(last, "joint_fingerprint", None) != expected_fingerprint:
        return False
    if bool(getattr(verdict, "ok", False)):
        return True
    components = set(getattr(verdict, "mismatch_components", ()))
    allowed = {"app_migration", "lab_schema", "lab_state", "checkpoint_ledger"}
    return bool(components) and components <= allowed and (
        not ({"lab_state", "checkpoint_ledger"} & components)
        or "lab_schema" in components
    )


def reanchor_joint_checkpoint(
    *,
    project_root: Path,
    data_dir: Path,
    env: dict[str, str],
    python_executable: str,
    expected_checkpoint_fingerprint: str,
) -> dict[str, Any]:
    """Re-anchor the cross-store checkpoint with target-loaded code.

    A source update process may still have pre-pull checkpoint modules in
    ``sys.modules`` after switching the checkout.  Run the write in a fresh
    interpreter so the persisted checkpoint schema and digest belong to the
    target source, not the updater's old module graph.
    """
    child_env = dict(env)
    child_env["ALETHEIA_DATA_DIR"] = str(data_dir)
    expected = repr(str(expected_checkpoint_fingerprint))
    script = (
        "import json\n"
        "from aletheia.observation_lab.checkpoint import (\n"
        "    compute_joint_checkpoint, save_joint_checkpoint, verify_joint_checkpoint,\n"
        ")\n"
        "from aletheia.update.target_fresh import checkpoint_delta_allowed\n"
        "before = verify_joint_checkpoint(create_lab=False)\n"
        f"expected = {expected}\n"
        "components = set(before.mismatch_components)\n"
        "if not checkpoint_delta_allowed(before, expected):\n"
        "    payload = {'ok': False, 'reason': 'target_checkpoint_delta_unproven', "
        "'mismatch_components': sorted(components)}\n"
        f"    print({_RESULT_PREFIX!r} + json.dumps(payload, sort_keys=True))\n"
        "    raise SystemExit(0)\n"
        "checkpoint = compute_joint_checkpoint()\n"
        "save_joint_checkpoint(checkpoint)\n"
        "verdict = verify_joint_checkpoint(create_lab=False)\n"
        "payload = {\n"
        "    'ok': bool(verdict.ok),\n"
        "    'reason': verdict.reason,\n"
        "    'mismatch_components': list(verdict.mismatch_components),\n"
        "    'joint_fingerprint': checkpoint.joint_fingerprint,\n"
        "}\n"
        f"print({_RESULT_PREFIX!r} + json.dumps(payload, sort_keys=True))\n"
    )
    return _run_json_script(
        script,
        project_root=project_root,
        env=child_env,
        python_executable=python_executable,
        timeout=60,
    )


__all__ = [
    "checkpoint_delta_allowed",
    "persist_current_commit",
    "reanchor_joint_checkpoint",
    "run_smoke_check",
    "validate_dashboard_artifacts",
]
