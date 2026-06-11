from aletheia.config.loader import load_config, save_config
from aletheia.config.schema import AletheiaConfig


def test_operator_defaults_present(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")

    cfg = load_config()

    assert cfg.operator.enabled is True
    assert cfg.operator.permission_mode == "ReadOnly"
    assert cfg.operator.port == 8791
    assert cfg.operator.main_session_id == "operator-main"


def test_operator_mode_is_independent_from_agent_mode(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")

    cfg = AletheiaConfig()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "Auto"
    save_config(cfg)

    loaded = load_config()
    assert loaded.agent.permission_mode == "Observe"
    assert loaded.operator.permission_mode == "Auto"
