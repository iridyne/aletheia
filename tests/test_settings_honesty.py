"""Regression tests for Settings honesty — dead config fields must not
pretend to be active controls.

Issue #399: Strategy Factory subsystem was removed with Position C (2026-07-02).
The UI/API must not accept mutations for dead fields or return a successful
"updated" response implying runtime behavior changed.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared app + config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def settings_app():
    from aletheia.server.routes import settings as settings_mod
    from aletheia.config.schema import StrategyFactoryConfig, EventTriggerConfig, AletheiaConfig
    from fastapi import FastAPI

    cfg = MagicMock(spec=AletheiaConfig)
    cfg.strategy_factory = StrategyFactoryConfig()
    cfg.event_triggers = EventTriggerConfig()

    app = FastAPI()
    app.include_router(settings_mod.router)

    with (
        patch.object(settings_mod, "load_config", return_value=cfg),
        patch.object(settings_mod, "save_config") as mock_save,
        patch("aletheia.files.jsonl.write_audit") as mock_audit,
    ):
        yield TestClient(app, raise_server_exceptions=True), cfg, mock_save, mock_audit


# ---------------------------------------------------------------------------
# Strategy Factory POST — must return deprecated, never mutate config
# ---------------------------------------------------------------------------

def test_strategy_factory_post_returns_deprecated_flag(settings_app):
    client, cfg, mock_save, mock_audit = settings_app
    resp = client.post("/api/settings/strategy_factory", json={"backtest_gate_enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deprecated"] is True
    assert body["supported"] is False


def test_strategy_factory_post_does_not_mutate_config(settings_app):
    client, cfg, mock_save, mock_audit = settings_app
    original_value = cfg.strategy_factory.backtest_gate_enabled
    client.post("/api/settings/strategy_factory", json={"backtest_gate_enabled": False})
    mock_save.assert_not_called()
    assert cfg.strategy_factory.backtest_gate_enabled == original_value


def test_strategy_factory_post_does_not_write_audit(settings_app):
    client, cfg, mock_save, mock_audit = settings_app
    client.post("/api/settings/strategy_factory", json={"sweep_enabled": True})
    mock_audit.assert_not_called()


def test_strategy_factory_post_returns_empty_updated(settings_app):
    client, cfg, _, __ = settings_app
    resp = client.post("/api/settings/strategy_factory", json={"walk_forward_enabled": True})
    assert resp.json()["updated"] == {}


def test_strategy_factory_post_lists_deprecated_fields(settings_app):
    client, cfg, _, __ = settings_app
    resp = client.post("/api/settings/strategy_factory", json={})
    body = resp.json()
    assert "fields" in body
    assert "backtest_gate_enabled" in body["fields"]
    assert "sweep_enabled" in body["fields"]


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
# Schema — StrategyFactoryConfig docstring signals deprecation
# ---------------------------------------------------------------------------

def test_strategy_factory_config_docstring_marks_deprecated():
    from aletheia.config.schema import StrategyFactoryConfig
    doc = (StrategyFactoryConfig.__doc__ or "").upper()
    assert "DEPRECATED" in doc, (
        "StrategyFactoryConfig docstring must mark it DEPRECATED so code readers "
        "and AI agents do not infer it as an active subsystem."
    )


def test_strategy_factory_fields_have_no_production_consumer():
    """Every field on StrategyFactoryConfig is compatibility-only.

    If someone adds a real consumer for one of these fields they should first
    remove the field from StrategyFactoryConfig, create a properly-named config
    class for the new subsystem, and delete this assertion for that field.
    """
    import ast
    import pathlib

    repo_root = pathlib.Path(__file__).parent.parent
    schema_file = repo_root / "aletheia" / "config" / "schema.py"
    source = schema_file.read_text()

    sf_fields: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "StrategyFactoryConfig":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    sf_fields.append(item.target.id)

    assert sf_fields, "StrategyFactoryConfig must still exist for config-file backward compat"

    # Only these files are allowed to reference StrategyFactoryConfig field names.
    allowed_consumer_files = {
        "aletheia/config/schema.py",
        "aletheia/server/routes/settings.py",
        "tests/test_settings_honesty.py",
    }

    violations: list[str] = []
    for field in sf_fields:
        for py_file in sorted(repo_root.rglob("*.py")):
            rel = str(py_file.relative_to(repo_root))
            if rel in allowed_consumer_files:
                continue
            if "__pycache__" in rel:
                continue
            text = py_file.read_text(errors="replace")
            if f".{field}" in text or f'"{field}"' in text or f"'{field}'" in text:
                violations.append(f"{field} found in {rel}")

    if violations:
        pytest.fail(
            "StrategyFactoryConfig fields appear to have consumers outside the "
            "allowed set. If this is a new legitimate consumer, update "
            "allowed_consumer_files and remove the DEPRECATED marker.\n"
            + "\n".join(violations)
        )
