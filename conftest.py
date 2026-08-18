"""Repository-level pytest compatibility fixtures.

Keep this file intentionally tiny. The main shared fixtures remain under
``tests/conftest.py``.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _legacy_python_operator_suite_uses_explicit_fallback(request, monkeypatch):
    """Run legacy PI Operator tests under the explicit fallback-owner contract.

    Production now selects ``operator-shell`` as the sole System Operator by
    default. Tests whose subject is specifically the retired Python governance
    loop must therefore opt into its fallback ownership mode instead of
    inheriting production defaults.
    """
    path = str(getattr(request.node, "path", "")).replace("\\", "/")
    node_name = str(getattr(request.node, "name", ""))
    tests_legacy_module = path.endswith("tests/test_operator_loop.py")
    tests_legacy_kill_switch_e2e = (
        path.endswith("tests/e2e/test_pipeline_failure_paths.py")
        and node_name == "test_kill_switch_suppresses_config_patches_emits_notify"
    )
    if not (tests_legacy_module or tests_legacy_kill_switch_e2e):
        return

    import aletheia.config.loader as loader

    original_load_config = loader.load_config

    def load_legacy_operator_config(*args, **kwargs):
        cfg = original_load_config(*args, **kwargs)
        cfg.operator.enabled = False
        cfg.operator.loop_enabled = True
        return cfg

    monkeypatch.setattr(loader, "load_config", load_legacy_operator_config)

    # The legacy module imports load_config directly, so patch an already-loaded
    # module too. Most tests import it inside the test body, in which case the
    # patched loader binding is picked up automatically.
    operator_loop = sys.modules.get("aletheia.agent_shell.operator_loop")
    if operator_loop is not None:
        monkeypatch.setattr(operator_loop, "load_config", load_legacy_operator_config)
