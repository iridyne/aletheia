from __future__ import annotations

import pytest
import yaml
from fastapi import HTTPException

from aletheia.config import loader
from aletheia.config.schema import AletheiaConfig


def _isolate_loader(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(loader, "is_preview_mode", lambda: True)
    monkeypatch.setattr(loader, "_dirs_ensured_for", None)
    monkeypatch.setattr(loader, "_config_cache", None)
    monkeypatch.setattr(loader, "_emit_consistency_events", lambda _cfg: None)
    monkeypatch.setattr(loader, "operator_shell_process_active", lambda: False)


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


def test_load_config_persists_and_audits_single_operator_authority(
    monkeypatch, tmp_path,
) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    audits: list[dict] = []
    monkeypatch.setattr(
        loader,
        "_audit_operator_authority_migration",
        lambda **payload: audits.append(payload),
    )
    raw = AletheiaConfig().model_dump(mode="json")
    raw["operator"]["enabled"] = True
    raw["operator"]["loop_enabled"] = True
    loader.CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    cfg = loader.load_config()

    assert cfg.operator.enabled is True
    assert cfg.operator.loop_enabled is False
    persisted = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    assert persisted["operator"]["loop_enabled"] is False
    assert len(audits) == 1
    assert audits[0]["before"]["loop_enabled"] is True
    assert audits[0]["after"]["loop_enabled"] is False
    assert "dual System Operator" in audits[0]["reason"]


def test_load_config_preserves_explicit_legacy_fallback(monkeypatch, tmp_path) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    raw = AletheiaConfig().model_dump(mode="json")
    raw["operator"]["enabled"] = False
    raw["operator"]["loop_enabled"] = True
    loader.CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    cfg = loader.load_config()

    assert cfg.operator.enabled is False
    assert cfg.operator.loop_enabled is True


def test_running_shell_suppresses_staged_legacy_fallback_without_rewriting_disk(
    monkeypatch, tmp_path,
) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    raw = AletheiaConfig().model_dump(mode="json")
    raw["operator"]["enabled"] = False
    raw["operator"]["loop_enabled"] = True
    loader.CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(loader, "operator_shell_process_active", lambda: True)

    cfg = loader.load_config()

    assert cfg.operator.enabled is False
    assert cfg.operator.loop_enabled is False
    persisted = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    assert persisted["operator"]["enabled"] is False
    assert persisted["operator"]["loop_enabled"] is True


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


def test_new_default_config_save_normalizes_to_operator_shell_only(
    monkeypatch, tmp_path,
) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    cfg = AletheiaConfig()
    assert cfg.operator.enabled is True
    assert cfg.operator.loop_enabled is True

    loader.save_config(cfg)

    assert cfg.operator.loop_enabled is False
    persisted = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    assert persisted["operator"]["enabled"] is True
    assert persisted["operator"]["loop_enabled"] is False


def test_existing_shell_owner_rejects_reenabling_legacy_loop(monkeypatch, tmp_path) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    cfg = AletheiaConfig()
    cfg.operator.enabled = True
    cfg.operator.loop_enabled = False
    loader.save_config(cfg)

    cfg.operator.loop_enabled = True
    with pytest.raises(HTTPException) as exc_info:
        loader.save_config(cfg)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "operator_authority_conflict"
    persisted = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    assert persisted["operator"]["enabled"] is True
    assert persisted["operator"]["loop_enabled"] is False


def test_save_config_rejects_hot_legacy_handoff_while_shell_process_is_active(
    monkeypatch, tmp_path,
) -> None:
    _isolate_loader(monkeypatch, tmp_path)
    monkeypatch.setattr(loader, "operator_shell_process_active", lambda: True)
    cfg = AletheiaConfig()
    cfg.operator.enabled = False
    cfg.operator.loop_enabled = True

    with pytest.raises(HTTPException) as exc_info:
        loader.save_config(cfg)

    assert exc_info.value.status_code == 409
    assert "still running" in exc_info.value.detail["message"]
    assert not loader.CONFIG_PATH.exists()
