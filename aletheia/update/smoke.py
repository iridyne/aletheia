"""Post-pull smoke verification for self-upgrade.

Two cheap gates that catch the common ways a pulled update can brick the box:
  1. import gate — a fresh subprocess imports the critical modules, so it reflects
     the NEW code on disk (not modules already loaded in the running process).
  2. fast test gate — a tiny pytest subset (config + risk engine) with a timeout.

Returns {ok, stage, detail}. Never raises. The full test suite is deliberately
NOT run here — too slow for an unattended update path.
"""

import logging
import subprocess

from aletheia.config.loader import _PROJECT_ROOT

logger = logging.getLogger("aletheia.update.smoke")

_IMPORT_TARGETS = "import aletheia.server.app, aletheia.execution.gateway, aletheia.risk.engine, aletheia.config.loader"
_FAST_TESTS = ["tests/test_config.py", "tests/test_risk_engine.py"]


def run_smoke_check() -> dict:
    """Run the import gate then the fast-test gate. Returns {ok, stage, detail}."""
    import sys

    # Gate 1: import the critical modules in a fresh interpreter.
    try:
        r = subprocess.run(
            [sys.executable, "-c", _IMPORT_TARGETS],
            cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return {"ok": False, "stage": "import", "detail": (r.stderr or r.stdout).strip()[:500]}
    except Exception as e:
        return {"ok": False, "stage": "import", "detail": str(e)}

    # Gate 2: fast pytest subset.
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", *_FAST_TESTS, "-q", "-x"],
            cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0:
            return {"ok": False, "stage": "fast_tests", "detail": (r.stdout or r.stderr).strip()[-500:]}
    except Exception as e:
        return {"ok": False, "stage": "fast_tests", "detail": str(e)}

    return {"ok": True, "stage": "done", "detail": ""}
