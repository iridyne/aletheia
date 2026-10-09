"""#1232 / #1074: a config writer must not revert a concurrent writer's field.

Writer A reads config, and before A persists, an independent writer B commits a
different field. Every targeted writer reads its base under the cross-process
config permit, so B waits for A and then applies on top: both fields survive.
"""

from __future__ import annotations

import threading

import pytest

import aletheia.config.loader as loader
from aletheia.config.schema import AletheiaConfig


@pytest.fixture()
def config_home(tmp_path, monkeypatch):
    import aletheia.files.jsonl as jsonl

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(loader, "_config_cache", None)
    monkeypatch.setattr(loader, "_dirs_ensured_for", None)
    monkeypatch.setattr(jsonl, "_last_audit_hash", None)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader.save_config(AletheiaConfig())
    monkeypatch.setattr("aletheia.net.apply_proxy_env", lambda: None)
    return tmp_path


def _race(monkeypatch, modules, writer_a, writer_b):
    """Run writer B while writer A sits between its config read and its save."""
    real_load = loader.load_config
    writer = threading.Thread(target=writer_b)

    def load_then_let_b_run(*args, **kwargs):
        cfg = real_load(*args, **kwargs)
        if writer.ident is None:
            writer.start()
            # A writer that does not hold the permit finishes inside this window.
            writer.join(timeout=0.5)
        return cfg

    monkeypatch.setattr(loader, "load_config", load_then_let_b_run)
    for module in modules:
        monkeypatch.setattr(module, "load_config", load_then_let_b_run)
    writer_a()
    writer.join(timeout=15)
    assert not writer.is_alive()
    monkeypatch.setattr(loader, "load_config", real_load)
    loader._invalidate_config_cache()
    return loader.load_config()


def test_settings_routes_preserve_a_concurrent_writer(config_home, monkeypatch):
    from aletheia.server.routes import settings

    final = _race(
        monkeypatch, [settings],
        lambda: settings.set_permission_mode(settings.PermissionModeRequest(mode="Full")),
        lambda: settings.update_network_config(settings.NetworkConfigUpdate(enforce=True)),
    )
    assert final.agent.permission_mode == "Full"
    assert final.network.enforce is True


def test_settings_section_routes_preserve_a_concurrent_writer(config_home, monkeypatch):
    from aletheia.server.routes import settings

    final = _race(
        monkeypatch, [settings],
        lambda: settings.update_runtime_config(settings.RuntimeConfigUpdate(git_enabled=False)),
        lambda: settings.set_autonomous_learning(settings.AutonomousLearningRequest(enabled=False)),
    )
    assert final.runtime.git_enabled is False
    assert final.agent.autonomous_learning is False


def test_pause_lease_preserves_a_concurrent_writer(config_home, monkeypatch):
    from aletheia.agent_runtime import pause_ownership
    from aletheia.server.routes import settings

    monkeypatch.setattr("aletheia.agent_runtime.scheduler.pause_scheduler", lambda **_kw: None)
    final = _race(
        monkeypatch, [],
        lambda: pause_ownership.acquire_pause("startup_behavior_gate", reason="gate"),
        lambda: settings.set_permission_mode(settings.PermissionModeRequest(mode="Observe")),
    )
    assert final.runtime.pause_owners["startup_behavior_gate"].active
    assert final.agent.permission_mode == "Observe"


# Whole-snapshot save_config() callers that still read their base outside the
# permit. Each is on a Live-authority or credential path and is tracked in #1074
# for an owner decision; anything else must use edit_config()/mutate_config() or
# pass expected_hash.
_UNCONVERTED_SNAPSHOT_WRITERS = {
    "core/aletheia/server/routes/live.py": 2,
    "core/aletheia/trust/live_unlock.py": 1,
    "extensions/model_endpoints/credential_cli.py": 1,
    "extensions/model_endpoints/credential_routes.py": 2,
}


def test_no_new_unconditional_snapshot_writers():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    found: dict[str, int] = {}
    for top in ("core", "extensions", "ops"):
        for path in (root / top).rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel == "core/aletheia/config/loader.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == "save_config" and not any(k.arg == "expected_hash" for k in node.keywords):
                    found[rel] = found.get(rel, 0) + 1
    assert found == _UNCONVERTED_SNAPSHOT_WRITERS
