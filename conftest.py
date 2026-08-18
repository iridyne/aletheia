"""Repository-level pytest compatibility fixtures.

Keep this file intentionally tiny. The main shared fixtures remain under
``tests/conftest.py``.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _legacy_python_operator_suite_uses_explicit_fallback(request, monkeypatch):
    """Run the legacy PI Operator test module under the legacy-owner contract.

    ``tests/test_operator_loop.py`` validates the retired Python governance loop
    itself. Production now selects ``operator-shell`` as the sole System Operator
    by default, so that legacy test module must explicitly exercise the fallback
    ownership mode instead of inheriting production defaults.
    """
    path = str(getattr(request.node, "path", "")).replace("\\", "/")
    if not path.endswith("tests/test_operator_loop.py"):
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
