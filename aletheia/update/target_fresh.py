"""Target-loaded helpers for source self-update verification and metadata writes.

The source updater itself necessarily starts under the pre-update interpreter.
After ``git pull`` that process may still hold old modules and old Pydantic model
objects in ``sys.modules``.  Any operation whose semantics belong to the target
checkout therefore runs in a fresh child interpreter rooted at the on-disk
project.  The helpers here deliberately pass only primitive values across that
boundary; no pre-pull config/model object is ever serialized into target state.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

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
    loading the latest committed config.  This prevents the pre-pull updater
    from overwriting target-side schema migrations or concurrent settings with a
    stale whole-config snapshot.
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
    return _run_json_script(
        script,
        project_root=project_root,
        env=child_env,
        python_executable=python_executable,
        timeout=30,
    )


__all__ = [
    "persist_current_commit",
    "run_smoke_check",
    "validate_dashboard_artifacts",
]
