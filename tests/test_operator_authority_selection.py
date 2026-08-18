from __future__ import annotations

import yaml

from aletheia.config import loader
from aletheia.config.schema import AletheiaConfig


def _isolate_loader(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(loader, "is_preview_mode", lambda: True)
    monkeypatch.setattr(loader, "_dirs_ensured_for", None)
    monkeypatch.setattr(loader, "_config_cache", None)
    monkeypatch.setattr(loader, "_emit_consistency_events", lambda _cfg: None)


def test_operator_shell_disables_legacy_python_loop() -> None:
    cfg = AletheiaConfig()
    assert cfg.operator.enabled is True
    assert cfg.operator.loop_enabled is True

    changed = loader._enforce_single_operator_authority(cfg)

    assert changed is True
    assert cfg.operator.loop_enabled is False


def test_legacy_python_loop_remains_explicit_fallback() -> None:
    cfg = AletheiaConfig()
    cfg.operator.enabled = False
    cfg.operator.loop_enabled = True

    changed = loader._enforce_single_operator_authority(cfg)

    assert changed is False
    assert cfg.operator.loop_enabled is True


def test_load_config_persists_single_operator_authority(monkeypatch, tmp_path) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    raw = AletheiaConfig().model_dump(mode="json")
    raw["operator"]["enabled"] = True
    raw["operator"]["loop_enabled"] = True
    loader.CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    cfg = loader.load_config()

    assert cfg.operator.enabled is True
    assert cfg.operator.loop_enabled is False
    persisted = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    assert persisted["operator"]["loop_enabled"] is False


def test_load_config_preserves_explicit_legacy_fallback(monkeypatch, tmp_path) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    raw = AletheiaConfig().model_dump(mode="json")
    raw["operator"]["enabled"] = False
    raw["operator"]["loop_enabled"] = True
    loader.CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    cfg = loader.load_config()

    assert cfg.operator.enabled is False
    assert cfg.operator.loop_enabled is True


def test_pre_shell_config_without_operator_section_keeps_legacy_owner(monkeypatch, tmp_path) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    raw = AletheiaConfig().model_dump(mode="json")
    raw.pop("operator")
    loader.CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    cfg = loader.load_config()

    assert cfg.operator.enabled is False
    assert cfg.operator.loop_enabled is True
    persisted = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    assert persisted["operator"]["enabled"] is False
    assert persisted["operator"]["loop_enabled"] is True


def test_default_runtime_selects_operator_shell_as_only_owner(monkeypatch, tmp_path) -> None:
    _isolate_loader(monkeypatch, tmp_path)

    cfg = loader.load_config()

    assert cfg.operator.enabled is True
    assert cfg.operator.loop_enabled is False


def test_save_config_cannot_persist_dual_operator_authority(monkeypatch, tmp_path) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    cfg = AletheiaConfig()
    cfg.operator.enabled = True
    cfg.operator.loop_enabled = True

    loader.save_config(cfg)

    assert cfg.operator.loop_enabled is False
    persisted = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    assert persisted["operator"]["enabled"] is True
    assert persisted["operator"]["loop_enabled"] is False
