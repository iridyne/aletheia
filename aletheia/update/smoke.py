"""Post-pull smoke verification for self-upgrade.

Two cheap gates that catch the common ways a pulled update can brick the box:
  1. import gate — a fresh subprocess imports the critical modules, so it reflects
     the NEW code on disk (not modules already loaded in the running process).
  2. fast test gate — a tiny pytest subset (config, risk engine, secrets
     isolation, and the proposal→risk→execution workflow routes) with a timeout.

Returns {ok, stage, detail}. Never raises. The full test suite is deliberately
NOT run here — too slow for an unattended update path.
"""

import logging
import os
import subprocess

from aletheia.config.loader import _PROJECT_ROOT
from aletheia.gateway.pidfile import _no_window_kwargs

logger = logging.getLogger("aletheia.update.smoke")

_IMPORT_TARGETS = "import aletheia.server.app, aletheia.execution.gateway, aletheia.risk.engine, aletheia.config.loader"
_FAST_TESTS = [
    "tests/test_config.py",
    "tests/test_risk_engine.py",
    "tests/test_secrets.py",
    "tests/test_workflow_routes.py",
]

# #136: strip every exchange/API credential prefix the app reads from os.environ
# (COINALYZE_/FRED_ are read directly; OKX_/BINANCE_ for cross-broker parity).
_SECRET_PREFIXES = ("BITGET_", "OKX_", "BINANCE_", "OPENAI_", "ANTHROPIC_", "LLM_",
                    "TELEGRAM_BOT_TOKEN", "COINALYZE_", "FRED_")


def _safe_env() -> dict:
    """Return os.environ with secret-bearing keys stripped."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(_SECRET_PREFIXES)}


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
