"""Post-pull verification for self-upgrade.

Three cheap gates that catch the common ways a pulled update can brick the box:
  1. import gate — a fresh subprocess imports the critical modules, so it reflects
     the NEW code on disk (not modules already loaded in the running process).
  2. fast test gate — a tiny pytest subset (config, risk engine, secrets
     isolation, and the proposal→risk→execution workflow routes) with a timeout.
  3. data gate — the migrated data root: config loads, SQLite passes an integrity
     check, and the audit hash chain verifies. Unattended updates point
     ``ALETHEIA_DATA_DIR`` at a restored snapshot to gate on it before the switch.

Every gate returns ``{ok, stage, detail}`` and never raises. The full test suite is
deliberately NOT run here — too slow for an unattended update path.
"""

import logging
import subprocess
import tempfile

from aletheia.config.loader import _PROJECT_ROOT, get_data_dir, load_config
from aletheia.gateway.pidfile import _no_window_kwargs
from aletheia.update.process_env import safe_env

logger = logging.getLogger("aletheia.update.smoke")

_IMPORT_TARGETS = "import aletheia.server.app, aletheia.execution.gateway, aletheia.risk.engine, aletheia.config.loader"
_FAST_TESTS = [
    "tests/test_config.py",
    "tests/test_risk_engine.py",
    "tests/test_secrets.py",
    "tests/test_workflow_routes.py",
]

def _safe_env() -> dict:
    """Return os.environ with secret-bearing keys stripped."""
    return safe_env()


def validate_current_data() -> dict:
    """Gate 3: the data root named by ``ALETHEIA_DATA_DIR`` is usable after migration.

    Returns ``{ok, stage, detail}`` and never raises, so a caller can decide
    fail-closed on the returned facts instead of parsing an exception.
    """
    import sqlite3

    try:
        # get_data_dir() reads the environment on every call, so the temporary
        # root an unattended preflight restores into is what this validates.
        from aletheia.files.jsonl import verify_audit_chain
        from aletheia.storage.migrations import apply_migrations

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
                return {"ok": False, "stage": "sqlite", "detail": str(result)}
        audit = verify_audit_chain()
        if not audit.get("valid"):
            return {
                "ok": False,
                "stage": "audit",
                "detail": "; ".join(audit.get("errors") or [])[:2000],
            }
    except Exception as e:
        return {"ok": False, "stage": "data", "detail": str(e)}
    return {
        "ok": True,
        "stage": "done",
        "detail": "",
        "data_dir": str(get_data_dir()),
        "audit_records": audit.get("records", 0),
    }


def run_smoke_check() -> dict:
    """Run the import gate then the fast-test gate. Returns {ok, stage, detail}."""
    import sys
    env = _safe_env()

    # Gate 1: import the critical modules in a fresh interpreter.
    try:
        r = subprocess.run(
            [sys.executable, "-c", _IMPORT_TARGETS],
            cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=60,
            env=env, **_no_window_kwargs(),
        )
        if r.returncode != 0:
            return {"ok": False, "stage": "import", "detail": (r.stderr or r.stdout).strip()[:500]}
    except Exception as e:
        return {"ok": False, "stage": "import", "detail": str(e)}

    # Gate 2: fast pytest subset.
    try:
        with tempfile.TemporaryDirectory(prefix="aletheia-update-tests-") as test_root:
            env["ALETHEIA_DATA_DIR"] = test_root
            r = subprocess.run(
                [sys.executable, "-m", "pytest", *_FAST_TESTS, "-q", "-x"],
                cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=180,
                env=env, **_no_window_kwargs(),
            )
        if r.returncode != 0:
            return {"ok": False, "stage": "fast_tests", "detail": (r.stdout or r.stderr).strip()[-500:]}
    except Exception as e:
        return {"ok": False, "stage": "fast_tests", "detail": str(e)}

    return {"ok": True, "stage": "done", "detail": ""}
