"""Spec: Live Unlock Contract (live_unlock_contract.md)

Live trading must never be enabled without explicit human confirmation.
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-S01", "live_unlock_contract", "§1 default state", "L2a",
        "LiveConfig default is live_disabled — never live-enabled by default",
    ),
)
def test_live_default_disabled():
    import inspect
    from aletheia.config.schema import LiveConfig

    source = inspect.getsource(LiveConfig)
    assert "live_disabled" in source


@spec_claim(
    SpecClaim(
        "LU-S02", "live_unlock_contract", "§6 forbidden intents", "L2a",
        "FORBIDDEN_INTENTS is a frozenset with 7 items including enable_live",
    ),
)
def test_forbidden_intents_structure():
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    assert isinstance(FORBIDDEN_INTENTS, frozenset)
    assert len(FORBIDDEN_INTENTS) == 7
    assert "enable_live" in FORBIDDEN_INTENTS
    assert "bypass_risk" in FORBIDDEN_INTENTS


# ---------------------------------------------------------------------------
# L2b — Behavioral claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-B01", "live_unlock_contract", "§6 enforcement", "L2b",
        "FORBIDDEN_INTENTS are blocked by permissions.check_permission()",
    ),
)
def test_forbidden_intents_blocked_by_permissions():
    from aletheia.agent_shell.permissions import check_permission
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    for intent in FORBIDDEN_INTENTS:
        allowed, reason = check_permission(intent, "Full")
        assert allowed is False, f"{intent} should be blocked even in Full mode"
        assert "forbidden" in reason.lower()


@spec_claim(
    SpecClaim(
        "LU-B02", "live_unlock_contract", "§3 restricted tools", "L2b",
        "RESTRICTED_TOOLS are registered with 'restricted' permission class",
    ),
)
def test_restricted_tools_permission_class():
    from aletheia.agent_runtime.tool_registry import RESTRICTED_TOOLS, init_tool_registry
    from aletheia.agent_runtime.permissions import TOOL_REGISTRY

    init_tool_registry()
    for tool in RESTRICTED_TOOLS:
        assert tool in TOOL_REGISTRY, f"{tool} not registered"
        assert TOOL_REGISTRY[tool] == "restricted", \
            f"{tool} has class '{TOOL_REGISTRY[tool]}' instead of 'restricted'"


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-C01", "live_unlock_contract", "§6 no escalation", "L2c",
        "No permission_mode grants access to FORBIDDEN_INTENTS",
    ),
)
def test_no_mode_permits_forbidden_intents():
    from aletheia.agent_shell.permissions import check_permission
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    for mode in ("Observe", "Paper", "Full"):
        for intent in FORBIDDEN_INTENTS:
            allowed, _ = check_permission(intent, mode)
            assert allowed is False, (
                f"INVARIANT VIOLATION: {intent} permitted in {mode} mode"
            )
