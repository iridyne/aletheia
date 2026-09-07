"""Regression tests for Settings honesty — dead config fields must not
pretend to be active controls.

Issue #399: the Strategy Factory subsystem was removed with Position C
(2026-07-02) but its config class, its `POST /api/settings/strategy_factory`
route and its GET projection survived as a compatibility shell. The shell is now
DELETED, so the tests that asserted the shell's honest-but-inert behavior are
gone with it; two of them actively guarded the dead code's survival
(`assert sf_fields, "must still exist for config-file backward compat"`).

Config files that still carry a `strategy_factory:` key load cleanly — Pydantic
ignores unknown top-level sections — so no migration is needed.

What remains here is the Event Triggers half of the same honesty rule: a removed
field must be REJECTED, not silently accepted.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Event Triggers POST — dead fields must be rejected (extra="forbid")
# ---------------------------------------------------------------------------

@pytest.fixture()
def event_trigger_app():
    from aletheia.server.routes import settings as settings_mod
    from aletheia.config.schema import EventTriggerConfig, AletheiaConfig
    from fastapi import FastAPI

    cfg = MagicMock(spec=AletheiaConfig)
    cfg.event_triggers = EventTriggerConfig()

    app = FastAPI()
    app.include_router(settings_mod.router)

    with (
        patch.object(settings_mod, "load_config", return_value=cfg),
        patch.object(settings_mod, "save_config"),
        patch("aletheia.files.jsonl.write_audit"),
    ):
        yield TestClient(app, raise_server_exceptions=False), cfg


def test_event_trigger_rejects_trigger_strategy_design(event_trigger_app):
    client, _ = event_trigger_app
    resp = client.post(
        "/api/settings/event_triggers",
        json={"trigger_strategy_design": True},
    )
    # extra="forbid" on SettingsRequest — unknown field must fail validation
    assert resp.status_code == 422


def test_event_trigger_rejects_strategy_design_min_move_pct(event_trigger_app):
    client, _ = event_trigger_app
    resp = client.post(
        "/api/settings/event_triggers",
        json={"strategy_design_min_move_pct": 7.5},
    )
    assert resp.status_code == 422


def test_event_trigger_live_fields_still_accepted(event_trigger_app):
    client, _ = event_trigger_app
    resp = client.post(
        "/api/settings/event_triggers",
        json={"enabled": True, "price_move_pct_threshold": 4.0},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"].get("price_move_pct_threshold") == 4.0


# ---------------------------------------------------------------------------
# The removed shell must stay removed
# ---------------------------------------------------------------------------

def test_strategy_factory_shell_is_gone():
    """No config class, no route, no GET projection.

    A compatibility shell that returns `{"deprecated": true, "updated": {}}` is a
    second, inert API surface. Keeping it taught readers the subsystem still
    existed and kept 13 dead fields alive in the schema.
    """
    import ast
    import pathlib

    from aletheia.config.schema import AletheiaConfig

    assert not hasattr(AletheiaConfig(), "strategy_factory"), (
        "AletheiaConfig grew a strategy_factory section again"
    )

    root = pathlib.Path(__file__).parent.parent
    for relative in ("aletheia/config/schema.py", "aletheia/server/routes/settings.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "StrategyFactory" not in source, (
            f"{relative} references the removed Strategy Factory shell"
        )
        assert "strategy_factory" not in source, (
            f"{relative} references the removed strategy_factory config section"
        )

    # And the route itself must not come back under any decorator form.
    settings_src = (root / "aletheia/server/routes/settings.py").read_text(encoding="utf-8")
    tree = ast.parse(settings_src)
    routes = {
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }
    assert "/strategy_factory" not in routes


def test_unimplemented_system_tools_are_not_registered():
    """A registered tool with no handler advertises a capability that cannot run.

    `create_playbook_draft` / `create_prompt_draft` /
    `create_code_change_proposal` had zero handler implementations, yet were
    registered under the `system` class, which `TOOL_CLASSES` permitted for
    `Full`. `is_tool_permitted` already fails closed for unknown names
    (`TOOL_REGISTRY.get(name, "restricted_tool")` → empty allow-set), so removing
    them is strictly more restrictive, not less.
    """
    from aletheia.agent_runtime.permissions import (
        TOOL_CLASSES,
        TOOL_REGISTRY,
        is_tool_permitted,
    )
    from aletheia.agent_runtime.tool_registry import init_tool_registry

    init_tool_registry()
    for phantom in (
        "create_playbook_draft",
        "create_prompt_draft",
        "create_code_change_proposal",
    ):
        assert phantom not in TOOL_REGISTRY
        for mode in ("Observe", "Paper", "Full"):
            assert is_tool_permitted(phantom, mode) is False

    assert "system" not in TOOL_CLASSES, (
        "the system tool class has no members; an empty permission class invites "
        "a future tool to be added without deciding its real class"
    )
