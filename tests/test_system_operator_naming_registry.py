"""Focused regression for #97 — System Operator naming registry.

The Product Direction renamed "PI Agent" → "System Operator". The underlying
config field, REST path, and audit event names are LEGACY (`pi_daemon`) and
preserved for backwards compat, but this test locks in:

  1. The compat layer's shape is stable — the fields the ledger enumerated
     as legacy still exist and still route to the same subsystem.
  2. New user-facing UI copy uses "System Operator daemon"; the legacy
     `pi_daemon` label cannot silently re-appear in the visible label.
  3. The single-source-of-truth naming registry lives in the PiDaemonConfig
     docstring — if that comment moves, this test catches it.

Fixing this fix is fine as long as the naming registry stays in one place
and the test is updated to point at it.
"""

from __future__ import annotations

import inspect

import pytest


def test_pi_daemon_config_has_naming_registry_docstring():
    """#97: the compat layer must document that `pi_daemon` is the legacy
    name for the System Operator daemon. Losing that docstring would leave
    future readers guessing whether the two are the same subsystem."""
    from aletheia.config.schema import PiDaemonConfig

    doc = inspect.getdoc(PiDaemonConfig) or ""
    assert "System Operator daemon" in doc, (
        "PiDaemonConfig docstring must name the current product term "
        "'System Operator daemon'; got:\n" + doc
    )
    assert "legacy" in doc.lower(), (
        "PiDaemonConfig docstring must flag `pi_daemon` as legacy — got:\n" + doc
    )
    # Naming registry: the doc names each contract carrying the legacy label.
    for key in ("cfg.pi_daemon", "pi_daemon_config_changed", "/api/settings/pi_daemon"):
        assert key in doc, (
            f"Naming registry in PiDaemonConfig docstring must list {key!r} "
            f"as a legacy contract; got:\n{doc}"
        )


def test_pi_daemon_config_field_shape_unchanged():
    """#97: the compat layer must NOT silently drop any pi_daemon field —
    that would break existing configs. Enumerate the expected fields."""
    from aletheia.config.schema import PiDaemonConfig

    inst = PiDaemonConfig()
    for field in (
        "enabled",
        "debounce_sec",
        "cooldown_sec",
        "heartbeat_sec",
        "decision_interval_minutes",
    ):
        assert hasattr(inst, field), (
            f"PiDaemonConfig lost expected legacy field {field!r} — "
            "removing fields silently breaks existing config.yaml files. "
            "If the field is truly deprecated, add an explicit migration."
        )


def test_settings_ui_label_uses_system_operator():
    """#97: the visible Settings label must use the current product name
    'System Operator', not the legacy 'PI daemon' / 'PI Agent' text. The
    legacy `pi_daemon` search alias may stay (users muscle-memory), but the
    primary label / heading must not regress."""
    from pathlib import Path

    agent_tab = Path(__file__).resolve().parents[1] / "frontend" / "src" / "features" / "settings" / "tabs" / "AgentModelsTab.tsx"
    text = agent_tab.read_text(encoding="utf-8")

    # Primary label — must appear.
    assert "System Operator daemon" in text, (
        "AgentModelsTab.tsx must contain the label "
        "'System Operator daemon' — the visible UI heading for the "
        "pi_daemon config section."
    )

    # The old visible label 'PI Agent' / 'PI daemon (event-driven)' must NOT
    # appear as visible copy. It's OK for the search alias `pi daemon` (lower
    # case, no capital) to remain to preserve muscle memory.
    # Assert no VISIBLE-copy occurrence of the old capitalised names.
    for banned in ("PI Agent daemon", "PI daemon (event-driven)"):
        assert banned not in text, (
            f"AgentModelsTab.tsx must not re-introduce visible copy {banned!r}; "
            "System Operator rename (#97) explicitly forbids it. If a legacy "
            "label needs to reappear for compat, add it as an ARIA description "
            "or a search alias, not as visible heading text."
        )
