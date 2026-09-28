"""Contracts for the explicitly opt-in, isolated development API."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from typer.testing import CliRunner


def test_dev_api_refuses_the_stable_gateway_ports():
    from aletheia.cli.dev import dev_app

    result = CliRunner().invoke(dev_app, ["api", "--port", "8787"])

    assert result.exit_code == 2
    assert "reserved" in result.output


@pytest.mark.parametrize("extensions_present", [False, True])
def test_dev_api_launches_a_shadow_process_with_isolated_data(monkeypatch, tmp_path, extensions_present):
    import aletheia.cli.dev as dev
    import aletheia.runtime_paths as runtime_paths

    captured: dict = {}
    if extensions_present:
        (tmp_path / "extensions").mkdir()

    monkeypatch.setattr(dev, "project_root", lambda: tmp_path)
    monkeypatch.setattr(runtime_paths, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(dev.subprocess, "run", lambda command, **kwargs: captured.update(command=command, **kwargs))

    result = CliRunner().invoke(dev.dev_app, ["api", "--port", "8791"])

    assert result.exit_code == 0
    assert captured["command"][captured["command"].index("--port") + 1] == "8791"
    assert captured["env"]["ALETHEIA_PREVIEW_MODE"] == "1"
    assert captured["env"]["ALETHEIA_RUNTIME_STATE"] == "shadow"
    expected = runtime_paths.preview_instance_dir(tmp_path, 8791)
    assert Path(captured["env"]["ALETHEIA_PREVIEW_DATA_DIR"]) == expected
    command = captured["command"]
    watched = [command[index + 1] for index, value in enumerate(command) if value == "--reload-dir"]
    assert watched == [str(tmp_path / "core" / "aletheia")] + ([str(tmp_path / "extensions")] if extensions_present else [])
    assert captured["cwd"] == tmp_path
    assert captured["env"]["PYTHONPATH"].split(os.pathsep) == [str(tmp_path / "core"), str(tmp_path)]


def test_preview_data_dir_requires_the_worktree_preview_root(monkeypatch, tmp_path):
    import aletheia.config.loader as loader
    import aletheia.runtime_paths as runtime_paths

    monkeypatch.setattr(runtime_paths, "_user_home", lambda: tmp_path)
    preview_root = tmp_path / ".aletheia" / "instances" / "dev-preview"
    allowed = preview_root / "8791"
    monkeypatch.setattr(loader, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("ALETHEIA_PREVIEW_MODE", "1")
    monkeypatch.setenv("ALETHEIA_PREVIEW_DATA_DIR", str(allowed))

    assert loader.preview_data_dir() == allowed

    monkeypatch.setenv("ALETHEIA_PREVIEW_DATA_DIR", str(tmp_path / "outside"))
    with pytest.raises(RuntimeError, match="dev-preview"):
        loader.preview_data_dir()


@pytest.mark.parametrize("extensions_present", [False, True])
def test_opt_in_watcher_tracks_present_source_roots_without_starting_runtime(monkeypatch, tmp_path, extensions_present):
    import watchfiles
    from scripts import watch_backend

    (tmp_path / "aletheia").mkdir()
    if extensions_present:
        (tmp_path / "extensions").mkdir()
    captured = []
    monkeypatch.setattr(watch_backend, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watch_backend, "ALETHEIA_DIR", tmp_path / "aletheia")
    monkeypatch.setenv("ALETHEIA_DEV_RELOAD", "1")
    monkeypatch.setattr(watch_backend, "check_live_guard", lambda: True)
    monkeypatch.setattr(watchfiles, "watch", lambda *paths, **kwargs: captured.extend(paths) or iter(()))
    watch_backend.main()
    assert captured == [tmp_path / "aletheia"] + ([tmp_path / "extensions"] if extensions_present else [])


@pytest.mark.anyio
async def test_preview_blocks_machine_level_gateway_and_update_controls(monkeypatch):
    import aletheia.server.routes.gateway as gateway_routes
    import aletheia.server.routes.update as update_routes

    monkeypatch.setattr(gateway_routes, "is_preview_mode", lambda: True)
    monkeypatch.setattr(update_routes, "is_preview_mode", lambda: True)

    with pytest.raises(HTTPException) as update:
        update_routes.update_apply(update_routes.ApplyBody(confirm=True))

    with pytest.raises(HTTPException) as restart:
        await gateway_routes.restart_gateway(type("Request", (), {"app": type("App", (), {"state": type("State", (), {})()})()})())
    assert restart.value.status_code == 403
    assert restart.value.detail["reason_code"] == "preview_gateway_control_blocked"
    assert update.value.status_code == 403
    assert update.value.detail["reason_code"] == "preview_update_blocked"


@pytest.mark.parametrize("preview_mode", ["0", "1"])
def test_seed_cli_isolates_first_import_and_preserves_primary(tmp_path, preview_mode):
    from aletheia.runtime_paths import checkout_id

    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "sentinel").write_text("untouched")
    root = Path(__file__).resolve().parents[1]
    expected = tmp_path / ".aletheia/instances/dev-preview" / checkout_id(root) / "8791"
    env = dict(os.environ, HOME=str(tmp_path), ALETHEIA_DATA_DIR=str(primary),
               ALETHEIA_PREVIEW_MODE=preview_mode)
    env.pop("ALETHEIA_PREVIEW_DATA_DIR", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    if preview_mode == "1":
        env["ALETHEIA_PREVIEW_DATA_DIR"] = str(expected)
    result = subprocess.run(
        [sys.executable, "-c", "from aletheia.main import app; app()",
         "dev", "seed-markets", "--bars", "50"],
        env=env, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in primary.iterdir()) == ["sentinel"]
    assert (primary / "sentinel").read_text() == "untouched"
    assert str(expected) in result.stdout
    with sqlite3.connect(expected / "app.db") as conn:
        persisted = conn.execute("SELECT COUNT(*) FROM market_ohlcv").fetchone()[0]
        assert f"ohlcv={persisted} bars" in result.stdout
        assert conn.execute("SELECT DISTINCT provider FROM market_ohlcv").fetchall() == [("preview_seed",)]


@pytest.mark.parametrize("target", ["outside", "other_port", "other_checkout", "symlink"])
def test_seed_rejects_wrong_identity_before_database_access(tmp_path, target):
    from aletheia.runtime_paths import checkout_id

    root = Path(__file__).resolve().parents[1]
    base = tmp_path / ".aletheia/instances/dev-preview"
    expected = base / checkout_id(root) / "8791"
    outside = tmp_path / "primary"
    outside.mkdir()
    (outside / "sentinel").write_text("untouched")
    targets = {"outside": outside, "other_port": expected.parent / "8792",
               "other_checkout": base / "another-checkout" / "8791"}
    if target == "symlink":
        expected.parent.mkdir(parents=True)
        expected.symlink_to(outside, target_is_directory=True)
    chosen = expected if target == "symlink" else targets[target]
    env = dict(os.environ, HOME=str(tmp_path), ALETHEIA_DATA_DIR=str(outside),
               ALETHEIA_PREVIEW_MODE="0", ALETHEIA_PREVIEW_DATA_DIR=str(chosen))
    result = subprocess.run(
        [sys.executable, "-c", "from aletheia.main import app; app()", "dev", "seed-markets"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "must match this checkout" in result.stderr
    assert not list(tmp_path.rglob("app.db"))
    assert (outside / "sentinel").read_text() == "untouched"


def test_seed_internal_entry_rejects_loaded_primary_and_does_not_read_secrets(monkeypatch, tmp_path):
    from aletheia.cli import dev_seed
    from aletheia.config import loader, secrets
    from aletheia import runtime_paths
    from aletheia.storage import sqlite

    monkeypatch.setattr(runtime_paths, "_user_home", lambda: tmp_path)
    expected = runtime_paths.preview_instance_dir(dev_seed._project_root(), 8791)
    monkeypatch.setenv("ALETHEIA_PREVIEW_MODE", "1")
    monkeypatch.setenv("ALETHEIA_PREVIEW_DATA_DIR", str(expected))
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path / "primary")
    def forbidden():
        pytest.fail("database/keyring access before isolation")
    monkeypatch.setattr(sqlite, "get_db", forbidden)
    monkeypatch.setattr(secrets, "_get_keyring", forbidden)
    assert secrets.get_secret("synthetic") is None
    with pytest.raises(RuntimeError, match="selected isolated"):
        dev_seed._seed_preview(8791, "binance", 50)
    assert not expected.exists()


def test_seed_propagates_child_failure(monkeypatch, tmp_path):
    from aletheia.cli import dev_seed
    from aletheia import runtime_paths

    monkeypatch.setattr(runtime_paths, "_user_home", lambda: tmp_path)
    monkeypatch.delenv("ALETHEIA_PREVIEW_DATA_DIR", raising=False)
    monkeypatch.setattr(dev_seed.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 7))
    result = CliRunner().invoke(dev_seed.dev_seed_app, [])
    assert result.exit_code == 7
    assert not list(tmp_path.rglob("app.db"))
