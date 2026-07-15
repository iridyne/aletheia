"""Contracts for the explicitly opt-in, isolated development API."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from typer.testing import CliRunner


def test_dev_api_refuses_the_stable_gateway_ports():
    from aletheia.cli.dev import dev_app

    result = CliRunner().invoke(dev_app, ["api", "--port", "8787"])

    assert result.exit_code == 2
    assert "reserved" in result.output


def test_dev_api_launches_a_shadow_process_with_isolated_data(monkeypatch, tmp_path):
    import aletheia.cli.dev as dev

    captured: dict = {}

    monkeypatch.setattr(dev, "project_root", lambda: tmp_path)
    monkeypatch.setattr(dev.subprocess, "run", lambda command, **kwargs: captured.update(command=command, **kwargs))

    result = CliRunner().invoke(dev.dev_app, ["api", "--port", "8791"])

    assert result.exit_code == 0
    assert captured["command"][captured["command"].index("--port") + 1] == "8791"
    assert captured["env"]["ALETHEIA_PREVIEW_MODE"] == "1"
    assert captured["env"]["ALETHEIA_RUNTIME_STATE"] == "shadow"
    assert Path(captured["env"]["ALETHEIA_PREVIEW_DATA_DIR"]) == tmp_path / "data" / "dev-preview" / "8791"


def test_preview_data_dir_requires_the_worktree_preview_root(monkeypatch, tmp_path):
    import aletheia.config.loader as loader

    preview_root = tmp_path / "data" / "dev-preview"
    allowed = preview_root / "8791"
    monkeypatch.setattr(loader, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("ALETHEIA_PREVIEW_MODE", "1")
    monkeypatch.setenv("ALETHEIA_PREVIEW_DATA_DIR", str(allowed))

    assert loader.preview_data_dir() == allowed

    monkeypatch.setenv("ALETHEIA_PREVIEW_DATA_DIR", str(tmp_path / "outside"))
    with pytest.raises(RuntimeError, match="dev-preview"):
        loader.preview_data_dir()


@pytest.mark.anyio
async def test_preview_blocks_machine_level_gateway_and_update_controls(monkeypatch):
    import aletheia.server.routes.gateway as gateway_routes
    import aletheia.server.routes.update as update_routes

    monkeypatch.setattr(gateway_routes, "is_preview_mode", lambda: True)
    monkeypatch.setattr(update_routes, "is_preview_mode", lambda: True)

    with pytest.raises(HTTPException) as autostart:
        await gateway_routes.set_autostart(gateway_routes.AutostartRequest(enabled=True))
    with pytest.raises(HTTPException) as update:
        await update_routes.update_apply(update_routes.ApplyBody(confirm=True))

    assert autostart.value.status_code == 403
    assert autostart.value.detail["reason_code"] == "preview_gateway_control_blocked"
    assert update.value.status_code == 403
    assert update.value.detail["reason_code"] == "preview_update_blocked"
